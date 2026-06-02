"""Chaos Queue Processor - Persistent queue for file processing.

Tracks what needs processing, what's in progress, and what's done.
Resumes automatically on restart.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FileStatus(Enum):
    """Processing status for a file."""
    PENDING = auto()      # Waiting to be processed
    PROCESSING = auto()   # Currently being processed
    COMPLETED = auto()    # Successfully processed
    FAILED = auto()       # Processing failed
    SKIPPED = auto()      # Intentionally skipped


@dataclass
class FileRecord:
    """Record of a file in the queue."""
    id: int
    source_path: str
    filename: str
    file_type: str  # pdf, video, image, etc.
    status: str
    created_at: str
    processed_at: Optional[str] = None
    error_message: Optional[str] = None
    output_path: Optional[str] = None
    vault_path: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> FileRecord:
        return cls(
            id=row["id"],
            source_path=row["source_path"],
            filename=row["filename"],
            file_type=row["file_type"],
            status=row["status"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
            error_message=row["error_message"],
            output_path=row["output_path"],
            vault_path=row["vault_path"],
        )


class ChaosQueue:
    """Persistent queue for Chaos file processing.

    Uses SQLite for durability and easy querying.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    status TEXT DEFAULT 'PENDING',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    error_message TEXT,
                    output_path TEXT,
                    vault_path TEXT
                )
            """)

            # Index for quick lookups
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON file_queue(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_path
                ON file_queue(source_path)
            """)
            conn.commit()

    def add_file(self, source_path: Path, file_type: str) -> bool:
        """Add a file to the queue. Returns False if already exists."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO file_queue (source_path, filename, file_type, status)
                    VALUES (?, ?, ?, ?)
                """, (
                    str(source_path),
                    source_path.name,
                    file_type,
                    FileStatus.PENDING.name
                ))
                conn.commit()
                logger.info(f"Added to queue: {source_path.name}")
                return True
        except sqlite3.IntegrityError:
            logger.debug(f"Already in queue: {source_path.name}")
            return False

    def claim_next(self) -> Optional[FileRecord]:
        """Claim the next pending file for processing.

        Uses atomic update to prevent race conditions.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Find next pending file (oldest first)
            cursor = conn.execute("""
                SELECT * FROM file_queue
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT 1
            """, (FileStatus.PENDING.name,))

            row = cursor.fetchone()
            if not row:
                return None

            # Mark as processing
            conn.execute("""
                UPDATE file_queue
                SET status = ?, processed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (FileStatus.PROCESSING.name, row["id"]))
            conn.commit()

            return FileRecord.from_row(row)

    def claim_specific(self, file_ids: list[int]) -> list[FileRecord]:
        """Claim specific pending files for processing."""
        if not file_ids:
            return []

        placeholders = ",".join("?" for _ in file_ids)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"""
                SELECT * FROM file_queue
                WHERE status = ? AND id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                [FileStatus.PENDING.name, *file_ids],
            )
            rows = cursor.fetchall()
            if not rows:
                return []

            claimed_ids = [row["id"] for row in rows]
            claim_placeholders = ",".join("?" for _ in claimed_ids)
            conn.execute(
                f"""
                UPDATE file_queue
                SET status = ?, processed_at = CURRENT_TIMESTAMP
                WHERE id IN ({claim_placeholders})
                """,
                [FileStatus.PROCESSING.name, *claimed_ids],
            )
            conn.commit()
            return [FileRecord.from_row(row) for row in rows]

    def mark_completed(
        self,
        file_id: int,
        output_path: Optional[str] = None,
        vault_path: Optional[str] = None
    ) -> None:
        """Mark a file as successfully completed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE file_queue
                SET status = ?, processed_at = CURRENT_TIMESTAMP,
                    output_path = ?, vault_path = ?
                WHERE id = ?
            """, (FileStatus.COMPLETED.name, output_path, vault_path, file_id))
            conn.commit()
            logger.info(f"Marked complete: file_id={file_id}")

    def mark_failed(self, file_id: int, error_message: str) -> None:
        """Mark a file as failed with error message."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE file_queue
                SET status = ?, processed_at = CURRENT_TIMESTAMP,
                    error_message = ?
                WHERE id = ?
            """, (FileStatus.FAILED.name, error_message, file_id))
            conn.commit()
            logger.warning(f"Marked failed: file_id={file_id}, error={error_message}")

    def reset_processing(self) -> int:
        """Reset any 'PROCESSING' files back to 'PENDING'.

        Call this on startup to recover from crashes.
        Returns count of reset files.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE file_queue
                SET status = ?
                WHERE status = ?
            """, (FileStatus.PENDING.name, FileStatus.PROCESSING.name))
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.warning(f"Reset {count} stuck processing files to pending")
            return count

    def get_stats(self) -> dict:
        """Get queue statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM file_queue
                GROUP BY status
            """)
            stats = {row[0]: row[1] for row in cursor.fetchall()}

            # Total
            cursor = conn.execute("SELECT COUNT(*) FROM file_queue")
            total = cursor.fetchone()[0]

            return {
                "total": total,
                "pending": stats.get(FileStatus.PENDING.name, 0),
                "processing": stats.get(FileStatus.PROCESSING.name, 0),
                "completed": stats.get(FileStatus.COMPLETED.name, 0),
                "failed": stats.get(FileStatus.FAILED.name, 0),
            }

    def get_pending_files(self) -> list[FileRecord]:
        """Get all pending files."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM file_queue
                WHERE status = ?
                ORDER BY created_at ASC
            """, (FileStatus.PENDING.name,))
            return [FileRecord.from_row(row) for row in cursor.fetchall()]

    def get_recent_completed(self, limit: int = 10) -> list[FileRecord]:
        """Get recently completed files."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM file_queue
                WHERE status = ?
                ORDER BY processed_at DESC
                LIMIT ?
            """, (FileStatus.COMPLETED.name, limit))
            return [FileRecord.from_row(row) for row in cursor.fetchall()]

    def is_file_processed(self, source_path: Path) -> bool:
        """Check if a file has already been processed (completed or failed)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT status FROM file_queue
                WHERE source_path = ?
            """, (str(source_path),))
            row = cursor.fetchone()
            if row:
                return row[0] in (FileStatus.COMPLETED.name, FileStatus.FAILED.name)
            return False
