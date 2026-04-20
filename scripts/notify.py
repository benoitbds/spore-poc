"""Send a one-screen email summary after an L0 or L1 SPORE cron run.

Usage:
    python scripts/notify.py --type l0
    python scripts/notify.py --type l1
    python scripts/notify.py --type l0 --dry-run
"""

import argparse
import json
import os
import smtplib
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

L0_LOG = Path("/var/log/spore.log")
L1_LOG = Path("/var/log/spore_l1.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DIR = PROJECT_ROOT / "outputs"


def _load_env_file(path):
    """Best-effort .env loader: KEY=VALUE lines into os.environ (no override)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(PROJECT_ROOT / ".env")


def iter_json_events(log_path: Path):
    """Yield parsed JSON-line events from the log, skipping non-JSON lines."""
    if not log_path.exists():
        return
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _date_prefix(evt: dict) -> str:
    ts = evt.get("timestamp", "")
    return ts[:10] if len(ts) >= 10 else ""


def collect_l0():
    """Return (summary_dict_or_None, anchor_date_str, fallback_events)."""
    summary = None
    all_events = []
    for evt in iter_json_events(L0_LOG):
        all_events.append(evt)
        if evt.get("event") == "pipeline_complete" and "run_id" in evt:
            summary = evt

    if summary is None:
        return None, None, _recent_alert_events(all_events)

    anchor = _date_prefix(summary)
    overrides = []
    reviews = []
    briefs = []
    for evt in all_events:
        if _date_prefix(evt) != anchor:
            continue
        e = evt.get("event")
        if e == "reviewer_verdict_override":
            overrides.append(evt)
        elif e == "review_complete":
            reviews.append(evt)
        elif e == "brief_generated":
            briefs.append(evt)

    interesting_kept = sum(
        1 for r in reviews
        if r.get("verdict") == "intéressant" and not r.get("overridden")
    )

    return {
        "run_id": summary.get("run_id"),
        "gate_pass_rate": summary.get("gate_pass_rate"),
        "hypotheses_generated": summary.get("hypotheses_generated"),
        "curated": summary.get("curated"),
        "bridge_rate": summary.get("bridge_rate"),
        "total_cost": summary.get("total_cost"),
        "overrides_count": len(overrides),
        "overrides_breakdown": _override_breakdown(overrides),
        "interesting_kept": interesting_kept,
        "briefs_published": len(briefs),
    }, anchor, []


def _override_breakdown(overrides):
    if not overrides:
        return ""
    risks = [o.get("hallucination_risk") for o in overrides]
    if risks and all(r == 0.5 for r in risks):
        return "all hallucination_risk 0.50"
    reasons = {o.get("reason", "?") for o in overrides}
    return f"{len(reasons)} distinct reasons"


def collect_l1():
    """Return (summary_dict_or_None, anchor_date_str, fallback_events)."""
    cycle_complete = None
    all_events = []
    for evt in iter_json_events(L1_LOG):
        all_events.append(evt)
        if evt.get("event") == "l1_cycle_complete":
            cycle_complete = evt

    if cycle_complete is None:
        return None, None, _recent_alert_events(all_events)

    anchor = _date_prefix(cycle_complete)
    applied = []
    blocked = []
    in_target_cycle = False
    target_cycle_id = cycle_complete.get("cycle_id")
    for evt in all_events:
        e = evt.get("event")
        if e == "l1_cycle_starting" and evt.get("cycle_id") == target_cycle_id:
            in_target_cycle = True
            applied = []
            blocked = []
        elif e == "l1_cycle_complete" and evt.get("cycle_id") == target_cycle_id:
            break
        elif in_target_cycle:
            if e == "mutation_applied":
                applied.append(evt)
            elif e == "mutation_blocked_by_lock":
                blocked.append(evt)

    old_values = _load_old_values(target_cycle_id)
    enriched_applied = [
        {
            "path": m.get("path"),
            "old_value": old_values.get(m.get("path"), "?"),
            "new_value": m.get("new_value"),
        }
        for m in applied
    ]

    return {
        "cycle_id": target_cycle_id,
        "cost_usd": cycle_complete.get("cost_usd", 0.0),
        "mutations_applied": enriched_applied,
        "mutations_blocked": [
            {
                "path": b.get("path"),
                "until": b.get("locked_until"),
                "reason": b.get("reason"),
            }
            for b in blocked
        ],
    }, anchor, []


def _load_old_values(cycle_id):
    if not cycle_id:
        return {}
    suffix = cycle_id[3:] if cycle_id.startswith("L1-") else cycle_id
    candidate = STRATEGY_DIR / f"strategy_L1-{suffix}.json"
    if not candidate.exists():
        return {}
    try:
        data = json.loads(candidate.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        m.get("target_path"): m.get("old_value")
        for m in data.get("mutations", [])
        if m.get("target_path")
    }


def _recent_alert_events(all_events, limit=5):
    """Return the last N warning/error events for NO-DATA triage."""
    alerts = [
        e for e in all_events
        if e.get("level") in ("warning", "error", "critical")
    ]
    return alerts[-limit:]


def format_l0_email(s, anchor_date):
    curated = s["curated"] or 0
    gate = s["gate_pass_rate"] or "?"
    flag = "⚠️ " if curated == 0 else ""
    subject = f"{flag}SPORE L0 — {curated} curated, {gate} gate | {anchor_date}"

    lines = [
        f"SPORE L0 Run — {anchor_date}",
        "─" * 28,
        f"Run ID:          {s['run_id']}",
        f"Gate pass rate:  {s['gate_pass_rate']}",
        f"Hypotheses:      {s['hypotheses_generated']}",
        f"Curated:         {s['curated']}",
        f"Bridge rate:     {s['bridge_rate']}",
        f"Cost:            {s['total_cost']}",
        "",
        f"Overrides:       {s['overrides_count']}"
        + (f" ({s['overrides_breakdown']})" if s["overrides_breakdown"] else ""),
        f"Kept as intéressant: {s['interesting_kept']}",
        f"Briefs published:    {s['briefs_published']}",
    ]

    if curated == 0:
        lines += ["", "⚠️  Zero curated. Check L0 thresholds."]
    elif s["briefs_published"] == 0 and s["interesting_kept"] > 0:
        lines += ["", "⚠️  Kept hypotheses but no briefs. Check post-fire pipeline."]
    elif s["briefs_published"] == 0 and curated > 0:
        lines += ["", "⚠️  Curated but no briefs. Check reviewer thresholds."]

    return subject, "\n".join(lines)


def format_l1_email(s, anchor_date):
    n = len(s["mutations_applied"])
    n_blocked = len(s["mutations_blocked"])
    subject = f"SPORE L1 — {n} mutations | {anchor_date}"

    lines = [
        f"SPORE L1 Cycle — {anchor_date}",
        "─" * 28,
        f"Cycle ID:   {s['cycle_id']}",
        f"Cost:       ${s['cost_usd']:.4f}",
        "",
        f"Applied ({n}):",
    ]
    if n == 0:
        lines.append("  (none)")
    for m in s["mutations_applied"]:
        lines.append(f"  • {m['path']}")
        lines.append(f"      {_short(m['old_value'])} → {_short(m['new_value'])}")

    if n_blocked:
        lines += ["", f"Blocked by lock ({n_blocked}):"]
        for b in s["mutations_blocked"]:
            lines.append(f"  • {b['path']} (until {b['until']})")

    return subject, "\n".join(lines)


def _short(v):
    s = v if isinstance(v, str) else repr(v)
    return s if len(s) < 80 else s[:77] + "..."


def format_no_data_email(run_type, fallback_events):
    label = run_type.upper()
    today = date.today().isoformat()
    subject = f"⚠️  SPORE {label} — NO DATA | {today}"

    lines = [
        f"SPORE {label} — NO DATA for {today}",
        "─" * 28,
        "No run summary event found in the log.",
        "The cron job may have crashed, exited early, or produced no output.",
        "",
    ]
    if fallback_events:
        lines.append(f"Last {len(fallback_events)} warning/error event(s):")
        for e in fallback_events:
            ts = e.get("timestamp", "?")[:19]
            lvl = e.get("level", "?")
            ev = e.get("event", "?")
            extras = {
                k: v for k, v in e.items()
                if k not in ("timestamp", "level", "event")
            }
            lines.append(f"  [{ts}] {lvl} {ev}")
            if extras:
                lines.append(f"    {extras}")
    else:
        lines.append("No warning or error events in the log either.")
        lines.append("The log file may be empty, missing, or unreadable.")

    return subject, "\n".join(lines)


def send_email(subject, body):
    """Send via env-configured SMTP. Never raises; reports errors on stderr."""
    sender = os.environ.get("SPORE_NOTIFY_FROM")
    if not sender:
        print("notify: SPORE_NOTIFY_FROM not set, skipping send", file=sys.stderr)
        return
    recipient = os.environ.get("SPORE_NOTIFY_TO") or sender
    host = os.environ.get("SPORE_SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SPORE_SMTP_PORT") or "587")
    user = os.environ.get("SPORE_SMTP_USER")
    password = os.environ.get("SPORE_SMTP_PASSWORD")
    if not user or not password:
        print(
            "notify: SPORE_SMTP_USER/PASSWORD missing, skipping send",
            file=sys.stderr,
        )
        return

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
    except Exception as e:
        print(f"notify: SMTP send failed: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", choices=["l0", "l1"], required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print email to stdout instead of sending",
    )
    args = parser.parse_args()

    if args.type == "l0":
        summary, anchor, fallback = collect_l0()
    else:
        summary, anchor, fallback = collect_l1()

    if summary is None:
        subject, body = format_no_data_email(args.type, fallback)
    elif args.type == "l0":
        subject, body = format_l0_email(summary, anchor)
    else:
        subject, body = format_l1_email(summary, anchor)

    if args.dry_run:
        print(f"Subject: {subject}")
        print()
        print(body)
        return 0

    try:
        send_email(subject, body)
    except Exception as e:
        print(f"notify: unexpected error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
