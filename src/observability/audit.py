"""AuditLogger stub — V1 placeholder.

The real AuditLogger (built in Prompt 6) replaces this file entirely.
This stub accepts all record() calls and discards them so that runtime
components can call self._audit.record() unconditionally without
needing to check whether an audit backend is available.
"""


class AuditLogger:
    """No-op audit logger stub. Replaced by the real implementation in Prompt 6."""

    def record(self, event: dict) -> None:  # type: ignore[type-arg]
        """Accept an audit event and discard it. The real implementation persists it."""
        pass
