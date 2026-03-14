"""AuditLogger — V1 real implementation for SandboxShift.

Writes audit events to a local JSONL file (one JSON object per line).
Each entry is automatically stamped with a UTC timestamp and a session ID
so events from a single SandboxManager.run() call can be correlated.

Design constraints (Security Layer 7):
  - record() is synchronous — callers never need to await it.
  - record() NEVER raises — a write failure must not crash the runtime.
  - Thread-safe — multiple asyncio tasks call record() concurrently.
  - Append-only — existing entries are never modified or deleted.
  - Log directory is created automatically on first use.

Default log path: ~/.sandboxshift/audit.log
Format:          JSONL — one JSON object per line, UTF-8 encoded.

Example entry::

    {"ts": "2026-03-14T09:00:00.123456Z", "session": "a1b2c3d4",
     "event": "run_start", "workspace": "/home/user/project", "task": "pytest"}
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Default log location — created automatically on first write.
_DEFAULT_LOG_PATH = Path.home() / ".sandboxshift" / "audit.log"


class AuditLogger:
    """Append-only JSONL audit logger.

    Args:
        log_path:   Path to the audit log file.  Directory is created if it
                    does not exist.  Defaults to ~/.sandboxshift/audit.log.
        session_id: 8-char hex string identifying this logger instance.
                    Auto-generated if not supplied.  All events from this
                    instance carry the same session_id for correlation.
    """

    def __init__(
        self,
        log_path: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self._log_path: Path = log_path if log_path is not None else _DEFAULT_LOG_PATH
        self._session_id: str = session_id if session_id is not None else uuid.uuid4().hex[:8]
        self._lock = threading.Lock()
        # Best-effort directory creation — failure is silently ignored;
        # record() will also silently ignore write errors.
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record(self, event: dict) -> None:  # type: ignore[type-arg]
        """Write one audit event to the log file.

        Automatically adds:
          ``ts``      — UTC ISO-8601 timestamp with microsecond precision.
          ``session`` — Session ID shared by all events from this instance.

        Event fields take precedence over auto-added fields if there is a
        name collision (caller-supplied ``ts`` or ``session`` are preserved).

        Args:
            event: Arbitrary dict describing the audit event.  Must be
                   JSON-serialisable; non-serialisable values are coerced
                   to strings by ``default=str``.

        Note:
            This method never raises.  If the write fails for any reason
            (disk full, permissions, etc.) the error is silently swallowed.
            The runtime must never be interrupted by an audit failure.
        """
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "session": self._session_id,
            **event,
        }
        line = json.dumps(entry, default=str)
        try:
            with self._lock:
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError:
            pass  # audit failures must never crash the runtime

    # ------------------------------------------------------------------
    # Accessors (useful for testing and introspection)
    # ------------------------------------------------------------------

    @property
    def log_path(self) -> Path:
        """Absolute path to the audit log file."""
        return self._log_path

    @property
    def session_id(self) -> str:
        """Session ID stamped on every event from this instance."""
        return self._session_id
