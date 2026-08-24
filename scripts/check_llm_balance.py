"""Alert by email when SPORE's LLM providers are running out of credit.

Two providers, two mechanisms, because they expose different information:

- **DeepSeek (primary)** — proactive. ``GET /user/balance`` returns the
  remaining balance, so we can warn *before* the money runs out. A rolling
  history of past readings gives an observed daily burn, and therefore a
  runway in days, which is what makes the alert actionable.
- **Anthropic (fallback)** — reactive. There is no balance endpoint, so the
  only honest signal is the ``credit balance is too low`` error the API
  returns once the key is already dead. We scan the run log for it rather
  than burning a live probe call that cannot distinguish "no credit" from
  "misconfigured key" or "network hiccup".

**An email is sent only when BOTH providers are in trouble.** One dead
provider is not an outage — the fallback covers it. Only losing both stops
the pipeline, which is the failure this alert exists to catch.

That gate needs care, because Anthropic is *only ever called when DeepSeek
has already failed* (see ``FallbackClient.complete``). A run in which
DeepSeek works produces no evidence about Anthropic at all. So Anthropic's
state is **sticky**: once an out-of-credit error is observed it persists
across runs, and is cleared only when a run shows the fallback actually
being used without a credit error (or by ``--reset anthropic``). Without
stickiness the "both down" condition could never fire in advance, which
would defeat the point of the proactive DeepSeek check.

Alerts fire on *state transitions*, not on every check, so a low balance does
not produce an identical email every morning until it is topped up.

Usage:
    python scripts/check_llm_balance.py
    python scripts/check_llm_balance.py --dry-run
    python scripts/check_llm_balance.py --log /var/log/spore.log
    python scripts/check_llm_balance.py --force            # ignore dedup state
    python scripts/check_llm_balance.py --reset anthropic  # clear sticky state
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notify import PROJECT_ROOT, iter_json_events, send_email  # noqa: E402

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
STATE_PATH = PROJECT_ROOT / "outputs" / "notify_state" / "llm_balance.json"
L0_LOG = Path("/var/log/spore.log")

#: Balance below which we alert regardless of measured burn (USD).
DEFAULT_MIN_USD = 2.00
#: Runway below which we alert once a burn rate has been measured (days).
DEFAULT_MIN_DAYS = 5.0
#: Re-send an unchanged alert only after this many days.
REPEAT_AFTER_DAYS = 3
#: How many balance readings to retain for the burn-rate estimate.
HISTORY_LIMIT = 21

#: Substrings identifying an out-of-credit error, per provider.
CREDIT_ERROR_MARKERS = {
    "deepseek": ("insufficient balance",),
    "anthropic": ("credit balance is too low",),
}

#: Log event proving the fallback provider was actually exercised.
FALLBACK_USED_EVENT = "falling_back_to_secondary"

State = Literal["ok", "low", "exhausted", "unknown"]

#: States that count as "this provider is in trouble" for the both-down gate.
BAD_STATES: tuple[State, ...] = ("low", "exhausted")


def _load_state() -> dict[str, Any]:
    """Read the persisted alert state, tolerating a missing or corrupt file.

    Returns:
        The state mapping, or a fresh empty one.
    """
    if not STATE_PATH.exists():
        return {"providers": {}, "history": []}
    try:
        data = json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"check_llm_balance: unreadable state ({e}), starting fresh", file=sys.stderr)
        return {"providers": {}, "history": []}
    data.setdefault("providers", {})
    data.setdefault("history", [])
    return data


def _save_state(state: dict[str, Any]) -> None:
    """Persist the alert state, never raising on failure.

    Args:
        state: The mapping to write.
    """
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))
    except OSError as e:
        print(f"check_llm_balance: could not save state: {e}", file=sys.stderr)


def fetch_deepseek_balance(api_key: str, timeout: int = 20) -> dict[str, Any] | None:
    """Query the DeepSeek balance endpoint.

    Args:
        api_key: DeepSeek API key.
        timeout: Socket timeout in seconds.

    Returns:
        A dict with ``available`` (bool) and ``balance`` (float USD), or None
        if the endpoint could not be reached or returned an unexpected shape.
    """
    req = urllib.request.Request(
        DEEPSEEK_BALANCE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"check_llm_balance: DeepSeek balance query failed: {e}", file=sys.stderr)
        return None

    infos = payload.get("balance_infos") or []
    usd = next((i for i in infos if i.get("currency") == "USD"), None)
    if usd is None:
        usd = infos[0] if infos else None
    if usd is None:
        print("check_llm_balance: no balance_infos in DeepSeek response", file=sys.stderr)
        return None

    try:
        balance = float(usd.get("total_balance", 0.0))
    except (TypeError, ValueError):
        print("check_llm_balance: unparseable total_balance", file=sys.stderr)
        return None

    return {"available": bool(payload.get("is_available")), "balance": balance}


def record_reading(state: dict[str, Any], balance: float, today: str) -> None:
    """Append today's balance reading to the rolling history.

    One reading per day is kept; a same-day re-run overwrites it.

    Args:
        state: The state mapping, mutated in place.
        balance: Current balance in USD.
        today: ISO date of the reading.
    """
    history = [h for h in state["history"] if h.get("date") != today]
    history.append({"date": today, "balance": round(balance, 4)})
    history.sort(key=lambda h: h["date"])
    state["history"] = history[-HISTORY_LIMIT:]


def estimate_daily_burn(history: list[dict[str, Any]]) -> float | None:
    """Estimate average daily spend from consecutive balance readings.

    Only decreases count: an increase means a top-up, which would otherwise
    show up as negative burn and inflate the runway.

    Args:
        history: Chronological readings, each with ``date`` and ``balance``.

    Returns:
        Average USD burned per day, or None if there is not enough data.
    """
    spent = 0.0
    days = 0.0
    for prev, cur in zip(history, history[1:]):
        try:
            d0 = date.fromisoformat(prev["date"])
            d1 = date.fromisoformat(cur["date"])
        except (KeyError, ValueError):
            continue
        elapsed = (d1 - d0).days
        if elapsed <= 0:
            continue
        delta = prev["balance"] - cur["balance"]
        if delta <= 0:  # top-up or flat: no spend signal
            continue
        spent += delta
        days += elapsed
    if days <= 0 or spent <= 0:
        return None
    return spent / days


def scan_log_for_credit_errors(log_path: Path) -> dict[str, str]:
    """Find out-of-credit errors per provider in the log's most recent run.

    The log accumulates across runs, so the scan is anchored to the latest
    date present: a provider that has since been topped up must stop being
    reported, and stale errors from a past outage must not alert forever.

    Args:
        log_path: Path to a JSON-lines SPORE run log.

    Returns:
        A dict with ``credit_errors`` (provider -> latest matching timestamp)
        and ``fallback_used`` (bool), both restricted to the latest day of
        activity in the log.
    """
    events = list(iter_json_events(log_path))
    dates = {ts[:10] for e in events if len(ts := e.get("timestamp", "")) >= 10}
    if not dates:
        return {"credit_errors": {}, "fallback_used": False}
    anchor = max(dates)

    hits: dict[str, str] = {}
    fallback_used = False
    for evt in events:
        ts = evt.get("timestamp", "")
        if ts[:10] != anchor:
            continue
        if evt.get("event") == FALLBACK_USED_EVENT:
            fallback_used = True
        blob = " ".join(
            str(evt.get(k, "")) for k in ("error", "last_error", "message", "event")
        ).lower()
        if not blob:
            continue
        for provider, markers in CREDIT_ERROR_MARKERS.items():
            if any(m in blob for m in markers):
                hits[provider] = ts or hits.get(provider, "")
    return {"credit_errors": hits, "fallback_used": fallback_used}


def resolve_anthropic_state(
    scan: dict[str, Any], previous: State
) -> tuple[State, str]:
    """Determine Anthropic's credit state, carrying it over when unobserved.

    Anthropic is only called after DeepSeek fails, so most runs yield no
    evidence about it. Rather than reading silence as health, the last
    observed state is carried forward until contradicted.

    Args:
        scan: Result of :func:`scan_log_for_credit_errors`.
        previous: State persisted from the last check.

    Returns:
        A ``(state, reason)`` pair, the reason explaining the evidence used.
    """
    if "anthropic" in scan["credit_errors"]:
        return "exhausted", "out-of-credit error in the latest run"
    if scan["fallback_used"]:
        return "ok", "fallback was exercised without a credit error"
    return previous, "no evidence in the latest run; carried over"


def classify_deepseek(
    reading: dict[str, Any] | None,
    burn: float | None,
    min_usd: float,
    min_days: float,
) -> tuple[State, str | None]:
    """Decide the DeepSeek alert state from its balance and burn rate.

    Args:
        reading: Result of :func:`fetch_deepseek_balance`, or None on failure.
        burn: Measured daily burn in USD, or None if unknown.
        min_usd: Hard floor below which we always alert.
        min_days: Runway floor, applied only when ``burn`` is known.

    Returns:
        A ``(state, runway_text)`` pair; ``runway_text`` is None when the
        runway cannot be computed.
    """
    if reading is None:
        return "unknown", None

    balance = reading["balance"]
    runway_text = None
    days_left = None
    if burn:
        days_left = balance / burn
        runway_text = f"~{days_left:.1f} days at ${burn:.3f}/day"

    if balance <= 0 or not reading["available"]:
        return "exhausted", runway_text
    if balance < min_usd:
        return "low", runway_text
    if days_left is not None and days_left < min_days:
        return "low", runway_text
    return "ok", runway_text


def should_alert(state: dict[str, Any], key: str, current: str, today: str) -> bool:
    """Decide whether the current situation warrants sending an email now.

    Alerts fire on entering a bad situation, on any change in it, and again
    only after ``REPEAT_AFTER_DAYS`` of no improvement.

    Args:
        state: The persisted state mapping.
        key: Dedup key (the combined-outage signature).
        current: Freshly computed situation signature.
        today: ISO date of this check.

    Returns:
        True if an email should be sent.
    """
    prior = state.get("alerts", {}).get(key) or {}
    if prior.get("signature") != current:
        return True

    last = prior.get("last_alert_date")
    if not last:
        return True
    try:
        elapsed = date.fromisoformat(today) - date.fromisoformat(last)
    except ValueError:
        return True
    return elapsed >= timedelta(days=REPEAT_AFTER_DAYS)


def format_alert(
    findings: list[dict[str, Any]], today: str, min_usd: float, min_days: float
) -> tuple[str, str]:
    """Render the alert email.

    Args:
        findings: Per-provider dicts with ``provider``, ``state`` and detail keys.
        today: ISO date of the check.
        min_usd: Configured hard floor.
        min_days: Configured runway floor.

    Returns:
        A ``(subject, body)`` pair.
    """
    states = [f["state"] for f in findings]
    if all(s == "exhausted" for s in states):
        icon, label = "🛑", "BOTH PROVIDERS OUT"
    elif "exhausted" in states:
        icon, label = "🛑", "no fallback left"
    else:
        icon, label = "⚠️", "both providers low"
    subject = f"{icon} SPORE LLM — {label} | {today}"

    lines = [
        f"SPORE LLM credit alert — {today}",
        "─" * 34,
        "Both providers are in trouble at once, so there is no fallback left.",
    ]

    for f in findings:
        lines.append("")
        lines.append(f"{f['provider'].upper()} — {f['state']}")
        if f.get("balance") is not None:
            lines.append(f"  Balance:  ${f['balance']:.2f}")
        if f.get("runway"):
            lines.append(f"  Runway:   {f['runway']}")
        if f.get("last_error_at"):
            lines.append(f"  Last credit error in log: {f['last_error_at']}")
        lines.append(f"  Detected via: {f['source']}")

    lines += [
        "",
        "─" * 34,
        "Impact: DeepSeek is the primary provider for every L0 and post-fire",
        "agent, Anthropic the only fallback. With both out, runs still",
        "'complete' but produce zero hypotheses — bridge_rate reads 0.0%",
        "because failed calls are counted as 'no bridge found'.",
        "",
        "Top up:",
        "  DeepSeek   https://platform.deepseek.com/top_up",
        "  Anthropic  https://console.anthropic.com/settings/billing",
        "",
        f"Thresholds: floor ${min_usd:.2f}, runway {min_days:.0f} days"
        f" (SPORE_BALANCE_MIN_USD / SPORE_BALANCE_MIN_DAYS).",
        f"Repeat alerts suppressed for {REPEAT_AFTER_DAYS} days unless state changes.",
        "A single provider in trouble does not trigger an email: the other",
        "one still covers it.",
    ]
    return subject, "\n".join(lines)


def main() -> int:
    """Run the balance check and send an alert if warranted.

    Returns:
        0 always, so a cron chain is never broken by this stage.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=L0_LOG,
        help="Run log to scan for out-of-credit errors (default: /var/log/spore.log)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the email instead of sending"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass repeat-alert suppression (the both-providers-down gate still applies)",
    )
    parser.add_argument(
        "--reset",
        choices=["anthropic", "all"],
        help="Clear sticky provider state (use after topping up Anthropic)",
    )
    args = parser.parse_args()

    min_usd = float(os.environ.get("SPORE_BALANCE_MIN_USD") or DEFAULT_MIN_USD)
    min_days = float(os.environ.get("SPORE_BALANCE_MIN_DAYS") or DEFAULT_MIN_DAYS)
    today = date.today().isoformat()

    state = _load_state()
    state.setdefault("alerts", {})
    if args.reset in ("anthropic", "all"):
        state["providers"].pop("anthropic", None)
    if args.reset == "all":
        # Keep `history`: it is the burn-rate record, not alert state.
        state["providers"] = {}
        state["alerts"] = {}

    # --- DeepSeek: proactive, from the balance endpoint -------------------
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    reading = fetch_deepseek_balance(api_key) if api_key else None
    if not api_key:
        print("check_llm_balance: DEEPSEEK_API_KEY not set", file=sys.stderr)

    burn = estimate_daily_burn(state["history"])
    if reading is not None:
        record_reading(state, reading["balance"], today)

    ds_state, runway = classify_deepseek(reading, burn, min_usd, min_days)

    # --- Anthropic: reactive and sticky, from the run log -----------------
    scan = scan_log_for_credit_errors(args.log)
    prev_anthropic: State = (state["providers"].get("anthropic") or {}).get(
        "state", "unknown"
    )
    an_state, an_reason = resolve_anthropic_state(scan, prev_anthropic)

    if args.reset in ("anthropic", "all") and an_state == "exhausted":
        print(
            "check_llm_balance: reset requested, but the latest run in "
            f"{args.log} still shows an Anthropic out-of-credit error "
            f"({scan['credit_errors'].get('anthropic')}), so the state stays "
            "'exhausted'. It will clear once a run logs no such error.",
            file=sys.stderr,
        )

    state["providers"]["deepseek"] = {"state": ds_state, "last_seen_date": today}
    state["providers"]["anthropic"] = {"state": an_state, "last_seen_date": today}

    # --- The gate: only alert when BOTH providers are in trouble ----------
    both_down = ds_state in BAD_STATES and an_state in BAD_STATES
    signature = f"deepseek={ds_state},anthropic={an_state}"
    fire = both_down and (args.force or should_alert(state, "combined", signature, today))

    if not fire:
        bal = f"${reading['balance']:.2f}" if reading else "unknown"
        burn_txt = f", burn ${burn:.3f}/day" if burn else ""
        why = "suppressed by dedup" if both_down else "fallback still available"
        print(
            f"check_llm_balance: no alert ({why}) — "
            f"deepseek={ds_state} {bal}{burn_txt}; anthropic={an_state} ({an_reason})"
        )
        if not args.dry_run:
            _save_state(state)
        return 0

    findings: list[dict[str, Any]] = [
        {
            "provider": "deepseek",
            "state": ds_state,
            "balance": reading["balance"] if reading else None,
            "runway": runway,
            "source": "balance endpoint",
        },
        {
            "provider": "anthropic",
            "state": an_state,
            "balance": None,
            "runway": None,
            "last_error_at": scan["credit_errors"].get("anthropic"),
            "source": an_reason,
        },
    ]
    state["alerts"]["combined"] = {"signature": signature, "last_alert_date": today}

    subject, body = format_alert(findings, today, min_usd, min_days)

    if args.dry_run:
        print(f"Subject: {subject}")
        print()
        print(body)
        return 0

    try:
        send_email(subject, body)
    except Exception as e:  # send_email already swallows SMTP errors
        print(f"check_llm_balance: unexpected error: {e}", file=sys.stderr)
    _save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
