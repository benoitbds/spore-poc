"""Mesure par appel LLM : événement structuré et ligne en base.

La télémétrie ne bloque jamais le pipeline. Une écriture qui échoue — base
verrouillée, table absente dans un processus qui n'a pas initialisé le schéma,
disque plein — produit un ``WARNING`` et rien d'autre. Les décisions du
pipeline, elles, restent fail closed : elles sont prises dans
``llm.client``, après cet enregistrement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - import de typage seulement
    from llm.client import LLMResponse

logger = get_logger("llm_telemetry")

_INSERT = """
INSERT INTO llm_calls (
    run_id, hypothesis_id, node, provider, model, response_model,
    system_fingerprint, attempt, max_tokens, input_tokens, output_tokens,
    finish_reason, cache_hit, latency_ms
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _context() -> dict[str, Any]:
    """Lit ``run_id`` et ``hypothesis_id`` liés au contexte structlog.

    Returns:
        Dictionnaire à deux clés, valeurs ``None`` tant que B.2 n'a pas lié le
        contexte dans les nœuds.
    """
    bound: dict[str, Any] = {}
    try:
        import structlog

        bound = dict(structlog.contextvars.get_contextvars())
    except Exception:  # noqa: BLE001 — la mesure ne casse jamais l'appel
        bound = {}
    return {
        "run_id": bound.get("run_id"),
        "hypothesis_id": bound.get("hypothesis_id"),
    }


async def record_llm_call(
    response: "LLMResponse",
    *,
    node: str,
    attempt: int,
    max_tokens: int,
) -> None:
    """Journalise un appel LLM et l'enregistre dans ``llm_calls``.

    Args:
        response: Réponse renvoyée par le client, plafond de sortie compris.
        node: Nœud appelant, tel que passé à ``client.complete``.
        attempt: Numéro de tentative pour ce nœud, à partir de 1.
        max_tokens: Plafond demandé pour cet appel.
    """
    context = _context()
    logger.info(
        "llm_call",
        node=node,
        provider=response.provider,
        model=response.requested_model,
        response_model=response.model,
        max_tokens=max_tokens,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        finish_reason=response.finish_reason,
        attempt=attempt,
        latency_ms=response.latency_ms,
        cache_hit=response.cache_hit,
        **context,
    )

    values = (
        context["run_id"],
        context["hypothesis_id"],
        node,
        response.provider,
        response.requested_model,
        response.model,
        response.system_fingerprint,
        attempt,
        max_tokens,
        response.input_tokens,
        response.output_tokens,
        response.finish_reason,
        int(response.cache_hit),
        response.latency_ms,
    )

    try:
        await _insert(values)
    except Exception as exc:  # noqa: BLE001 — jamais bloquant, cf. docstring
        logger.warning(
            "llm_call_not_recorded",
            node=node,
            attempt=attempt,
            finish_reason=response.finish_reason,
            error=str(exc),
        )


async def _insert(values: tuple[Any, ...]) -> None:
    """Insère une ligne, en créant la table si le processus ne l'a pas fait.

    Les scripts hors pipeline (traductions, rejeu) n'appellent pas
    ``init_database``. Plutôt que de perdre leur mesure, la table est créée à
    la volée : le DDL est le même que celui du schéma, et il est idempotent.

    Args:
        values: Valeurs de la ligne, dans l'ordre de ``_INSERT``.

    Raises:
        Exception: Toute erreur d'écriture, traitée par l'appelant.
    """
    from storage.database import LLM_CALLS_SCHEMA, get_connection

    async with get_connection() as conn:
        try:
            await conn.execute(_INSERT, values)
        except Exception as exc:  # noqa: BLE001 — distingue table absente du reste
            if "no such table" not in str(exc).lower():
                raise
            await conn.executescript(LLM_CALLS_SCHEMA)
            await conn.execute(_INSERT, values)
        await conn.commit()
