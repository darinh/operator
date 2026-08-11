"""The SQLite connection helper, extracted from the metrics module.

The extraction spike found this was the *only* thing tying the supervision
kernel to metrics ingestion: ``work_claims`` and ``operator_session`` each did
``from operator_ingest import connect``, and that one import dragged the whole
metrics subsystem into the kernel graph. Thirty lines, in the wrong module.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT = 15.0


@contextmanager
def connect(db_path):
    """Open a connection, commit on success, and always close.

    Deliberately a context manager rather than a bare connection: sqlite3's own
    ``with`` block manages the *transaction* but does **not** close the
    connection. Returning a raw connection therefore leaks a file handle on
    every call, which matters for a long-running loop-mode process that reports
    and ingests repeatedly.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT * 1000)}")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
