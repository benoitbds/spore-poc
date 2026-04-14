"""One-shot: mark any 'running' row in runs as 'failed'.

Runs that never called update_run() — usually killed mid-flight or crashed —
stay in status='running' forever. This script cleans every such row
without a time cutoff.
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "spore.db"


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            UPDATE runs
            SET status = 'failed',
                completed_at = CURRENT_TIMESTAMP,
                error_message = 'Stale run cleaned up (one-shot)'
            WHERE status = 'running'
            """
        )
        conn.commit()
        print(f"Cleaned {cur.rowcount} stale run(s)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
