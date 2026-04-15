"""SQLite database for SPORE - hypothesis and metrics storage."""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import aiosqlite

from config import get_settings
from models.hypothesis import Hypothesis, HumanFeedback, HypothesisStatus
from agents.reviewer import AutoFeedback, AutoFeedbackScores

# SQL Schema
SCHEMA = """
-- Hypotheses table
CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    genome_version TEXT NOT NULL,
    collision_json TEXT NOT NULL,
    bridge_json TEXT NOT NULL,
    scores_json TEXT,
    predictions_json TEXT,
    kill_condition TEXT NOT NULL,
    next_steps_json TEXT,
    relevant_labs_json TEXT,
    relevant_datasets_json TEXT,
    sources_used_json TEXT,
    critic_debate_log TEXT,
    gap_manifest_json TEXT,
    impact_analysis_json TEXT,
    status TEXT NOT NULL DEFAULT 'generated',
    human_feedback TEXT,
    human_feedback_comment TEXT,
    auto_feedback_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Metrics table for run tracking
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    metadata_json TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status);
CREATE INDEX IF NOT EXISTS idx_hypotheses_generated_at ON hypotheses(generated_at);
CREATE INDEX IF NOT EXISTS idx_metrics_run_id ON metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name);

-- Run history table
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    collisions_requested INTEGER NOT NULL,
    collisions_processed INTEGER DEFAULT 0,
    hypotheses_generated INTEGER DEFAULT 0,
    bridge_rate REAL,
    total_tokens_in INTEGER DEFAULT 0,
    total_tokens_out INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT
);

-- Research briefs table (post-fire pipeline)
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,              -- SPR-2026-XXXX
    hypothesis_id TEXT NOT NULL,      -- FK vers hypotheses
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending',    -- pending | grounding | sharpening | reviewing | complete | killed

    -- Literature Grounding
    novelty_score REAL,
    novelty_verdict TEXT,             -- novel | incremental | already_explored | already_proven
    evidence_count INTEGER,
    counter_evidence_count INTEGER,
    kill_reason TEXT,                  -- NULL si pas tué

    -- Sharpening
    formal_statement TEXT,
    prediction_count INTEGER,

    -- Protocol
    phase1_cost_estimate TEXT,
    phase1_duration TEXT,
    can_start_today BOOLEAN,

    -- Panel Review
    panel_consensus_score REAL,
    panel_verdict TEXT,               -- publish_brief | revise_and_resubmit | reject
    revision_count INTEGER DEFAULT 0,

    -- Brief
    brief_md_path TEXT,
    brief_pdf_path TEXT,
    brief_json_path TEXT,

    -- Full JSON blobs
    grounding_data JSON,
    sharpened_data JSON,
    protocol_data JSON,
    panel_data JSON,
    vulgarization_data JSON
);
CREATE INDEX IF NOT EXISTS idx_briefs_hypothesis ON briefs(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_briefs_status ON briefs(status);

-- ── API / monétisation tables ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stripe_customer_id TEXT,
    free_brief_used BOOLEAN DEFAULT FALSE,
    credits INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS purchases (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    brief_id TEXT,
    type TEXT NOT NULL,                 -- 'single' | 'pack_5' | 'custom' | 'free'
    amount_cents INTEGER NOT NULL,
    stripe_session_id TEXT,
    stripe_payment_intent TEXT,
    status TEXT DEFAULT 'pending',      -- 'pending' | 'paid' | 'failed' | 'refunded'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_brief ON purchases(brief_id);
CREATE INDEX IF NOT EXISTS idx_purchases_session ON purchases(stripe_session_id);
CREATE INDEX IF NOT EXISTS idx_purchases_user_brief_paid
    ON purchases(user_id, brief_id, status);

CREATE TABLE IF NOT EXISTS custom_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    domain_a TEXT NOT NULL,
    domain_b TEXT NOT NULL,
    purchase_id TEXT REFERENCES purchases(id),
    status TEXT DEFAULT 'pending',      -- 'pending' | 'paid' | 'running' | 'complete' | 'failed'
    hypothesis_id TEXT,
    brief_id TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_custom_requests_user ON custom_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_custom_requests_status ON custom_requests(status);

CREATE TABLE IF NOT EXISTS magic_links (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_magic_links_user ON magic_links(user_id);
"""


@asynccontextmanager
async def get_connection() -> AsyncIterator[aiosqlite.Connection]:
    """Get an async database connection."""
    settings = get_settings()
    db_path = settings.db_path

    # Ensure directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def init_database() -> None:
    """Initialize database schema."""
    async with get_connection() as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()

        # Migration: add impact_analysis_json column if missing
        try:
            await conn.execute(
                "ALTER TABLE hypotheses ADD COLUMN impact_analysis_json TEXT"
            )
            await conn.commit()
        except Exception:
            pass  # Column already exists

        # Migration: add auto_feedback_json column if missing
        try:
            await conn.execute(
                "ALTER TABLE hypotheses ADD COLUMN auto_feedback_json TEXT"
            )
            await conn.commit()
        except Exception:
            pass  # Column already exists

        # Migration: add vulgarization_data column to briefs if missing
        try:
            await conn.execute(
                "ALTER TABLE briefs ADD COLUMN vulgarization_data JSON"
            )
            await conn.commit()
        except Exception:
            pass  # Column already exists


async def save_hypothesis(hypothesis: Hypothesis) -> None:
    """Save a hypothesis to the database."""
    async with get_connection() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO hypotheses (
                id, generated_at, genome_version, collision_json, bridge_json,
                scores_json, predictions_json, kill_condition, next_steps_json,
                relevant_labs_json, relevant_datasets_json, sources_used_json,
                critic_debate_log, gap_manifest_json, impact_analysis_json,
                status, human_feedback, human_feedback_comment, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hypothesis.id,
                hypothesis.generated_at.isoformat(),
                hypothesis.genome_version,
                hypothesis.collision.model_dump_json(),
                hypothesis.bridge.model_dump_json(),
                hypothesis.scores.model_dump_json() if hypothesis.scores else None,
                json.dumps([p.model_dump() for p in hypothesis.predictions]),
                hypothesis.kill_condition,
                json.dumps(hypothesis.next_steps),
                json.dumps(hypothesis.relevant_labs),
                json.dumps(hypothesis.relevant_datasets),
                json.dumps(hypothesis.sources_used),
                hypothesis.critic_debate_log,
                hypothesis.gap_manifest.model_dump_json(),
                hypothesis.impact_analysis.model_dump_json() if hypothesis.impact_analysis else None,
                hypothesis.status.value,
                hypothesis.human_feedback.value if hypothesis.human_feedback else None,
                hypothesis.human_feedback_comment,
                datetime.now().isoformat(),
            ),
        )
        await conn.commit()


async def get_hypothesis(hypothesis_id: str) -> Optional[Hypothesis]:
    """Retrieve a hypothesis by ID."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM hypotheses WHERE id = ?",
            (hypothesis_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return _row_to_hypothesis(row)


async def list_hypotheses(
    status: Optional[HypothesisStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Hypothesis]:
    """List hypotheses with optional filtering."""
    async with get_connection() as conn:
        if status:
            cursor = await conn.execute(
                """
                SELECT * FROM hypotheses
                WHERE status = ?
                ORDER BY generated_at DESC
                LIMIT ? OFFSET ?
                """,
                (status.value, limit, offset),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT * FROM hypotheses
                ORDER BY generated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )

        rows = await cursor.fetchall()
        return [_row_to_hypothesis(row) for row in rows]


async def update_hypothesis_feedback(
    hypothesis_id: str,
    feedback: Optional[HumanFeedback],
    comment: Optional[str] = None,
) -> bool:
    """Update human feedback for a hypothesis.

    Args:
        hypothesis_id: ID of the hypothesis to update
        feedback: The feedback value, or None to clear feedback
        comment: Optional comment
    """
    async with get_connection() as conn:
        # Determine new status
        if feedback is None:
            new_status = "curated"  # Reset to curated when clearing feedback
            feedback_value = None
        else:
            new_status = "human_reviewed"
            feedback_value = feedback.value

        result = await conn.execute(
            """
            UPDATE hypotheses
            SET human_feedback = ?,
                human_feedback_comment = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (feedback_value, comment, new_status, datetime.now().isoformat(), hypothesis_id),
        )
        await conn.commit()
        return result.rowcount > 0


async def update_hypothesis_auto_feedback(
    hypothesis_id: str,
    auto_feedback: AutoFeedback,
) -> bool:
    """Update auto-feedback for a hypothesis.

    Args:
        hypothesis_id: ID of the hypothesis to update
        auto_feedback: The AutoFeedback object from ReviewerAgent
    """
    async with get_connection() as conn:
        auto_feedback_json = auto_feedback.model_dump_json()

        result = await conn.execute(
            """
            UPDATE hypotheses
            SET auto_feedback_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (auto_feedback_json, datetime.now().isoformat(), hypothesis_id),
        )
        await conn.commit()
        return result.rowcount > 0


async def get_hypothesis_auto_feedback(hypothesis_id: str) -> Optional[AutoFeedback]:
    """Get auto-feedback for a hypothesis."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT auto_feedback_json FROM hypotheses WHERE id = ?",
            (hypothesis_id,),
        )
        row = await cursor.fetchone()

        if row is None or row["auto_feedback_json"] is None:
            return None

        return AutoFeedback.model_validate_json(row["auto_feedback_json"])


async def clear_auto_feedback(hypothesis_id: str) -> bool:
    """Clear auto-feedback for a hypothesis."""
    async with get_connection() as conn:
        result = await conn.execute(
            """
            UPDATE hypotheses
            SET auto_feedback_json = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), hypothesis_id),
        )
        await conn.commit()
        return result.rowcount > 0


async def clear_all_auto_feedback() -> int:
    """Clear all auto-feedback from all hypotheses.

    Returns:
        Number of hypotheses updated
    """
    async with get_connection() as conn:
        result = await conn.execute(
            """
            UPDATE hypotheses
            SET auto_feedback_json = NULL,
                updated_at = ?
            WHERE auto_feedback_json IS NOT NULL
            """,
            (datetime.now().isoformat(),),
        )
        await conn.commit()
        return result.rowcount


async def save_metric(
    run_id: str,
    metric_name: str,
    metric_value: float,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Save a metric datapoint."""
    async with get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO metrics (run_id, metric_name, metric_value, metadata_json, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                metric_name,
                metric_value,
                json.dumps(metadata) if metadata else None,
                datetime.now().isoformat(),
            ),
        )
        await conn.commit()


async def get_metrics(
    run_id: Optional[str] = None,
    metric_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get metrics with optional filtering."""
    async with get_connection() as conn:
        query = "SELECT * FROM metrics WHERE 1=1"
        params: list[Any] = []

        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)

        if metric_name:
            query += " AND metric_name = ?"
            params.append(metric_name)

        query += " ORDER BY recorded_at DESC"

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "metric_name": row["metric_name"],
                "metric_value": row["metric_value"],
                "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
                "recorded_at": row["recorded_at"],
            }
            for row in rows
        ]


async def save_run(
    run_id: str,
    collisions_requested: int,
) -> None:
    """Save a new run record."""
    async with get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO runs (id, started_at, collisions_requested, status)
            VALUES (?, ?, ?, 'running')
            """,
            (run_id, datetime.now().isoformat(), collisions_requested),
        )
        await conn.commit()


async def update_run(
    run_id: str,
    **kwargs: Any,
) -> None:
    """Update run record with new values."""
    if not kwargs:
        return

    async with get_connection() as conn:
        set_clauses = []
        params = []

        for key, value in kwargs.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)

        params.append(run_id)

        await conn.execute(
            f"UPDATE runs SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        await conn.commit()


async def cleanup_stale_runs(timeout_hours: int = 6) -> int:
    """Mark runs stuck in 'running' for more than ``timeout_hours`` as 'failed'.

    The runs schema uses ``completed_at`` and ``error_message``; the timeout
    is computed against ``started_at`` via SQLite's ``datetime('now', '-Nh')``.

    Args:
        timeout_hours: Threshold in hours before a 'running' row is considered stale.

    Returns:
        Number of rows updated.
    """
    reason = f"Stale run cleaned up (stuck > {timeout_hours}h)"
    async with get_connection() as conn:
        cursor = await conn.execute(
            """
            UPDATE runs
            SET status = 'failed',
                completed_at = CURRENT_TIMESTAMP,
                error_message = ?
            WHERE status = 'running'
              AND started_at < datetime('now', ?)
            """,
            (reason, f"-{int(timeout_hours)} hours"),
        )
        await conn.commit()
        return cursor.rowcount or 0


async def get_run(run_id: str) -> Optional[dict[str, Any]]:
    """Get a run by ID."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM runs WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return dict(row)


async def save_brief(
    brief_id: str,
    hypothesis_id: str,
    grounding_data: dict | None = None,
    sharpened_data: dict | None = None,
    protocol_data: dict | None = None,
    panel_data: dict | None = None,
    vulgarization_data: dict | None = None,
    **kwargs: Any,
) -> None:
    """Save or update a brief record."""
    async with get_connection() as conn:
        # Extract summary fields from data blobs
        novelty_score = None
        novelty_verdict = None
        evidence_count = None
        counter_evidence_count = None
        if grounding_data:
            na = grounding_data.get("novelty_assessment", {})
            novelty_score = na.get("score")
            novelty_verdict = na.get("verdict")
            evidence_count = len(grounding_data.get("evidence_base", []))
            counter_evidence_count = len(grounding_data.get("counter_evidence", []))

        formal_statement = None
        prediction_count = None
        if sharpened_data:
            formal_statement = sharpened_data.get("formal_statement")
            prediction_count = len(sharpened_data.get("falsifiable_predictions", []))

        phase1_cost = None
        phase1_dur = None
        can_start = None
        if protocol_data:
            phases = protocol_data.get("phases", [])
            if phases:
                res = phases[0].get("required_resources", {})
                phase1_cost = res.get("estimated_cost")
                phase1_dur = res.get("estimated_duration")
            qs = protocol_data.get("phase_1_quick_start", {})
            can_start = qs.get("can_start_today")

        consensus_score = None
        panel_verdict = None
        if panel_data:
            meta = panel_data.get("meta_review", {})
            consensus_score = meta.get("consensus_score")
            panel_verdict = meta.get("verdict")

        status = kwargs.get("status", "complete")
        kill_reason = kwargs.get("kill_reason")
        brief_md = kwargs.get("brief_md_path")
        brief_json = kwargs.get("brief_json_path")
        # revision_count comes from the caller (state of the post-fire graph).
        # Default 0 for legacy callers.
        revision_count = int(kwargs.get("revision_count", 0) or 0)

        await conn.execute(
            """
            INSERT OR REPLACE INTO briefs (
                id, hypothesis_id, status,
                novelty_score, novelty_verdict, evidence_count, counter_evidence_count,
                kill_reason, formal_statement, prediction_count,
                phase1_cost_estimate, phase1_duration, can_start_today,
                panel_consensus_score, panel_verdict, revision_count,
                brief_md_path, brief_pdf_path, brief_json_path,
                grounding_data, sharpened_data, protocol_data, panel_data,
                vulgarization_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief_id, hypothesis_id, status,
                novelty_score, novelty_verdict, evidence_count, counter_evidence_count,
                kill_reason, formal_statement, prediction_count,
                phase1_cost, phase1_dur, can_start,
                consensus_score, panel_verdict, revision_count,
                brief_md, brief_json,
                json.dumps(grounding_data) if grounding_data else None,
                json.dumps(sharpened_data) if sharpened_data else None,
                json.dumps(protocol_data) if protocol_data else None,
                json.dumps(panel_data) if panel_data else None,
                json.dumps(vulgarization_data) if vulgarization_data else None,
            ),
        )
        await conn.commit()


async def list_briefs(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List briefs with optional filtering."""
    async with get_connection() as conn:
        if status:
            cursor = await conn.execute(
                """SELECT * FROM briefs WHERE status = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (status, limit, offset),
            )
        else:
            cursor = await conn.execute(
                """SELECT * FROM briefs
                   ORDER BY panel_consensus_score DESC NULLS LAST, created_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_brief(brief_id: str) -> dict[str, Any] | None:
    """Get a single brief by ID."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM briefs WHERE id = ?", (brief_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_brief(brief_id: str, **kwargs: Any) -> bool:
    """Update brief fields."""
    if not kwargs:
        return False
    async with get_connection() as conn:
        set_clauses = []
        params = []
        for key, value in kwargs.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)
        params.append(brief_id)
        result = await conn.execute(
            f"UPDATE briefs SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        await conn.commit()
        return result.rowcount > 0


def _row_to_hypothesis(row: aiosqlite.Row) -> Hypothesis:
    """Convert a database row to a Hypothesis object."""
    from models.collision import CollisionPair
    from models.gap_manifest import GapManifest
    from models.hypothesis import Bridge, Prediction, Scores, ImpactAnalysis

    # Handle impact_analysis_json (may not exist in older databases)
    impact_analysis = None
    try:
        impact_json = row["impact_analysis_json"]
        if impact_json:
            impact_analysis = ImpactAnalysis.model_validate_json(impact_json)
    except (KeyError, IndexError):
        pass  # Column doesn't exist in older schema

    return Hypothesis(
        id=row["id"],
        generated_at=datetime.fromisoformat(row["generated_at"]),
        genome_version=row["genome_version"],
        collision=CollisionPair.model_validate_json(row["collision_json"]),
        bridge=Bridge.model_validate_json(row["bridge_json"]),
        scores=Scores.model_validate_json(row["scores_json"]) if row["scores_json"] else None,
        predictions=[
            Prediction.model_validate(p) for p in json.loads(row["predictions_json"])
        ] if row["predictions_json"] else [],
        kill_condition=row["kill_condition"],
        next_steps=json.loads(row["next_steps_json"]) if row["next_steps_json"] else [],
        relevant_labs=json.loads(row["relevant_labs_json"]) if row["relevant_labs_json"] else [],
        relevant_datasets=json.loads(row["relevant_datasets_json"]) if row["relevant_datasets_json"] else [],
        sources_used=json.loads(row["sources_used_json"]) if row["sources_used_json"] else [],
        critic_debate_log=row["critic_debate_log"],
        gap_manifest=GapManifest.model_validate_json(row["gap_manifest_json"]) if row["gap_manifest_json"] else GapManifest(),
        impact_analysis=impact_analysis,
        status=HypothesisStatus(row["status"]),
        human_feedback=HumanFeedback(row["human_feedback"]) if row["human_feedback"] else None,
        human_feedback_comment=row["human_feedback_comment"],
    )
