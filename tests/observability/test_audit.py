"""Tests for AuditLogger — V1 real implementation.

All tests use tmp_path so they never write to ~/.sandboxshift/audit.log.

Test groups:
  Group 1: Construction                  (4 tests)
  Group 2: record() basic behaviour      (5 tests)
  Group 3: JSONL format + fields         (4 tests)
  Group 4: Failure resilience            (3 tests)
  Group 5: Thread safety                 (2 tests)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from sandboxshift.observability.audit import AuditLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[dict]:
    """Read all JSONL lines from path and return as list of dicts."""
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Group 1: Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_custom_log_path_stored(self, tmp_path: Path) -> None:
        """log_path property must return the path passed to __init__."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        assert al.log_path == log

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        """Log directory must be created automatically on construction."""
        log = tmp_path / "deep" / "nested" / "audit.log"
        AuditLogger(log_path=log)
        assert log.parent.exists()

    def test_auto_session_id_generated(self, tmp_path: Path) -> None:
        """A session_id must be auto-generated when none is supplied."""
        al = AuditLogger(log_path=tmp_path / "a.log")
        assert isinstance(al.session_id, str)
        assert len(al.session_id) == 8

    def test_custom_session_id_stored(self, tmp_path: Path) -> None:
        """Explicit session_id must be preserved exactly."""
        al = AuditLogger(log_path=tmp_path / "a.log", session_id="deadbeef")
        assert al.session_id == "deadbeef"


# ---------------------------------------------------------------------------
# Group 2: record() basic behaviour
# ---------------------------------------------------------------------------


class TestRecordBasic:
    def test_record_creates_file(self, tmp_path: Path) -> None:
        """record() must create the log file if it does not yet exist."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "test"})
        assert log.exists()

    def test_record_writes_one_line(self, tmp_path: Path) -> None:
        """One record() call must produce exactly one line."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "test"})
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_record_appends_multiple_lines(self, tmp_path: Path) -> None:
        """Multiple record() calls must produce multiple lines (append, not overwrite)."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "first"})
        al.record({"event": "second"})
        al.record({"event": "third"})
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_record_preserves_event_fields(self, tmp_path: Path) -> None:
        """All caller-supplied fields must appear in the written entry."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "provision", "instance_id": "ss-abc123", "exit_code": 0})
        entry = _read_lines(log)[0]
        assert entry["event"] == "provision"
        assert entry["instance_id"] == "ss-abc123"
        assert entry["exit_code"] == 0

    def test_record_never_raises_on_bad_path(self) -> None:
        """record() must not raise even if the log path is unwritable."""
        al = AuditLogger(log_path=Path("/dev/null/cannot/exist/audit.log"))
        al.record({"event": "test"})  # must not raise


# ---------------------------------------------------------------------------
# Group 3: JSONL format + auto-added fields
# ---------------------------------------------------------------------------


class TestJsonlFormat:
    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        """Every line written must be valid JSON."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "run_start"})
        al.record({"event": "run_complete", "exit_code": 0})
        for line in log.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # raises if invalid

    def test_entry_has_ts_field(self, tmp_path: Path) -> None:
        """Every entry must include a 'ts' (timestamp) field."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "test"})
        entry = _read_lines(log)[0]
        assert "ts" in entry
        assert entry["ts"]  # non-empty string

    def test_entry_has_session_field(self, tmp_path: Path) -> None:
        """Every entry must include a 'session' field matching the logger's session_id."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log, session_id="cafebabe")
        al.record({"event": "test"})
        entry = _read_lines(log)[0]
        assert entry["session"] == "cafebabe"

    def test_session_consistent_across_records(self, tmp_path: Path) -> None:
        """All records from one AuditLogger instance must share the same session_id."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "a"})
        al.record({"event": "b"})
        entries = _read_lines(log)
        sessions = {e["session"] for e in entries}
        assert len(sessions) == 1  # all same
        assert list(sessions)[0] == al.session_id


# ---------------------------------------------------------------------------
# Group 4: Failure resilience
# ---------------------------------------------------------------------------


class TestFailureResilience:
    def test_construction_with_unwritable_dir_does_not_raise(self) -> None:
        """AuditLogger.__init__ must not raise even for an uncreateable directory."""
        # /dev/null is a file, so creating a child directory must fail silently.
        al = AuditLogger(log_path=Path("/dev/null/sandboxshift/audit.log"))
        assert al is not None

    def test_two_instances_have_different_session_ids(self, tmp_path: Path) -> None:
        """Auto-generated session IDs must be unique across instances."""
        a = AuditLogger(log_path=tmp_path / "a.log")
        b = AuditLogger(log_path=tmp_path / "b.log")
        assert a.session_id != b.session_id

    def test_non_serialisable_values_coerced_to_string(self, tmp_path: Path) -> None:
        """Non-JSON-serialisable values (e.g. Path objects) must be coerced, not raised."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        al.record({"event": "test", "path": Path("/some/path")})
        entry = _read_lines(log)[0]
        assert entry["path"] == "/some/path"


# ---------------------------------------------------------------------------
# Group 5: Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_writes_produce_correct_line_count(self, tmp_path: Path) -> None:
        """100 concurrent record() calls across 10 threads must produce exactly 100 lines."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)
        errors: list[Exception] = []

        def write_ten() -> None:
            try:
                for i in range(10):
                    al.record({"event": "concurrent", "i": i})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write_ten) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 100

    def test_concurrent_writes_all_valid_json(self, tmp_path: Path) -> None:
        """All lines written under concurrent load must be valid, complete JSON objects."""
        log = tmp_path / "audit.log"
        al = AuditLogger(log_path=log)

        def write_ten() -> None:
            for i in range(10):
                al.record({"event": "concurrent", "i": i})

        threads = [threading.Thread(target=write_ten) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for line in log.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # raises if any line is malformed
