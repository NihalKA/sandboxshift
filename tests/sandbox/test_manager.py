"""Tests for SandboxManager — central SandboxShift orchestrator.

Test groups:
  Group 1: Constructor validation                    (2 tests)
  Group 2: run() — local path                        (5 tests)
  Group 3: run() — cloud path                        (4 tests)
  Group 4: run() — FORCE_LOCAL override              (3 tests)
  Group 5: run() — cloud_runtime=None fallback       (3 tests)
  Group 6: run() — destroy always called             (3 tests)

asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandboxshift.config import SandboxConfig
from sandboxshift.observability.audit import AuditLogger
from sandboxshift.sandbox.burst.engine import BurstDecision, BurstEngine
from sandboxshift.sandbox.detection.sensitivity import (
    DetectionLayer,
    Finding,
    Recommendation,
    SensitivityResult,
    SensitivityScanner,
)
from sandboxshift.sandbox.manager import RunResult, SandboxManager
from sandboxshift.sandbox.runtime.base import Runtime, TaskResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_task_result(exit_code: int = 0) -> TaskResult:
    return TaskResult(
        exit_code=exit_code,
        stdout="output",
        stderr="",
        duration_seconds=0.1,
    )


def _make_runtime(instance_id: str = "ss-test000000", exit_code: int = 0) -> AsyncMock:
    """Return an AsyncMock that satisfies the Runtime ABC."""
    mock = AsyncMock(spec=Runtime)
    mock.provision.return_value = instance_id
    mock.execute.return_value = _make_task_result(exit_code)
    mock.destroy.return_value = None
    return mock


def _make_scanner(
    is_sensitive: bool = False,
    recommendation: Recommendation = Recommendation.ALLOW_CLOUD,
    findings: list[Finding] | None = None,
) -> MagicMock:
    result = SensitivityResult(
        is_sensitive=is_sensitive,
        findings=findings or [],
        recommendation=recommendation,
    )
    mock = MagicMock(spec=SensitivityScanner)
    mock.scan = AsyncMock(return_value=result)
    return mock


def _make_burst_engine(mode: str, confidence: str = "preferred") -> MagicMock:
    decision = BurstDecision(
        mode=mode,
        reason="test reason",
        confidence=confidence,
    )
    mock = MagicMock(spec=BurstEngine)
    mock.decide = AsyncMock(return_value=decision)
    return mock


def _make_finding(workspace: Path) -> Finding:
    return Finding(
        layer=DetectionLayer.FILE_PATTERN,
        file=workspace / ".env",
        pattern=".env",
        reason="Environment variable file may contain secrets",
        match_value="",
    )


def _make_manager(
    *,
    local_runtime: AsyncMock | None = None,
    cloud_runtime: AsyncMock | None = None,
    burst_engine: MagicMock | None = None,
    scanner: MagicMock | None = None,
    audit_logger: MagicMock | None = None,
    mode: str = "local",
) -> SandboxManager:
    return SandboxManager(
        local_runtime=local_runtime or _make_runtime(),
        cloud_runtime=cloud_runtime,
        burst_engine=burst_engine or _make_burst_engine(mode),
        scanner=scanner or _make_scanner(),
        audit_logger=audit_logger or MagicMock(spec=AuditLogger),
    )


# ---------------------------------------------------------------------------
# Group 1: Constructor validation
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    def test_raises_if_local_runtime_is_none(self) -> None:
        """local_runtime=None must raise ValueError immediately."""
        with pytest.raises(ValueError, match="local_runtime"):
            SandboxManager(
                local_runtime=None,  # type: ignore[arg-type]
                cloud_runtime=None,
                burst_engine=MagicMock(spec=BurstEngine),
                scanner=MagicMock(spec=SensitivityScanner),
            )

    def test_accepts_none_audit_logger(self) -> None:
        """audit_logger=None must not raise — falls back to no-op AuditLogger."""
        manager = SandboxManager(
            local_runtime=_make_runtime(),
            cloud_runtime=None,
            burst_engine=_make_burst_engine("local"),
            scanner=_make_scanner(),
            audit_logger=None,
        )
        assert manager is not None


# ---------------------------------------------------------------------------
# Group 2: run() — local path
# ---------------------------------------------------------------------------


class TestRunLocalPath:
    async def test_returns_run_result(self, tmp_path: Path) -> None:
        """run() on the local path must return a RunResult instance."""
        manager = _make_manager(mode="local")
        result = await manager.run(tmp_path, "echo hi", SandboxConfig())
        assert isinstance(result, RunResult)

    async def test_runtime_mode_is_local(self, tmp_path: Path) -> None:
        """RunResult.runtime_mode must be 'local' when BurstEngine decides local."""
        manager = _make_manager(mode="local")
        result = await manager.run(tmp_path, "echo hi", SandboxConfig())
        assert result.runtime_mode == "local"

    async def test_task_result_is_propagated(self, tmp_path: Path) -> None:
        """RunResult.task_result must match what local_runtime.execute() returned."""
        local = _make_runtime(exit_code=42)
        manager = _make_manager(local_runtime=local, mode="local")
        result = await manager.run(tmp_path, "exit 42", SandboxConfig())
        assert result.task_result.exit_code == 42
        assert result.task_result.stdout == "output"

    async def test_sensitivity_reasons_empty_for_clean_workspace(
        self, tmp_path: Path
    ) -> None:
        """sensitivity_reasons must be [] when the scan produces no findings."""
        manager = _make_manager(scanner=_make_scanner(is_sensitive=False), mode="local")
        result = await manager.run(tmp_path, "echo hi", SandboxConfig())
        assert result.sensitivity_reasons == []

    async def test_burst_confidence_propagated(self, tmp_path: Path) -> None:
        """RunResult.burst_confidence must match BurstDecision.confidence."""
        engine = _make_burst_engine(mode="local", confidence="preferred")
        manager = _make_manager(burst_engine=engine)
        result = await manager.run(tmp_path, "echo hi", SandboxConfig())
        assert result.burst_confidence == "preferred"


# ---------------------------------------------------------------------------
# Group 3: run() — cloud path
# ---------------------------------------------------------------------------


class TestRunCloudPath:
    async def test_runtime_mode_is_cloud(self, tmp_path: Path) -> None:
        """RunResult.runtime_mode must be 'cloud' when BurstEngine decides cloud."""
        cloud = _make_runtime("ss-cloud000000")
        manager = _make_manager(cloud_runtime=cloud, mode="cloud")
        result = await manager.run(tmp_path, "echo hi", SandboxConfig())
        assert result.runtime_mode == "cloud"

    async def test_cloud_runtime_provision_called(self, tmp_path: Path) -> None:
        """cloud_runtime.provision() must be called; local_runtime.provision() must NOT."""
        local = _make_runtime("ss-local000000")
        cloud = _make_runtime("ss-cloud000000")
        manager = _make_manager(local_runtime=local, cloud_runtime=cloud, mode="cloud")
        await manager.run(tmp_path, "echo hi", SandboxConfig())
        cloud.provision.assert_awaited_once()
        local.provision.assert_not_awaited()

    async def test_cloud_task_result_propagated(self, tmp_path: Path) -> None:
        """RunResult.task_result must come from cloud_runtime.execute()."""
        cloud = _make_runtime("ss-cloud000000", exit_code=7)
        manager = _make_manager(cloud_runtime=cloud, mode="cloud")
        result = await manager.run(tmp_path, "exit 7", SandboxConfig())
        assert result.task_result.exit_code == 7

    async def test_cloud_destroy_called(self, tmp_path: Path) -> None:
        """cloud_runtime.destroy() must be called with the instance_id from provision()."""
        cloud = _make_runtime("ss-cloud-abc123")
        manager = _make_manager(cloud_runtime=cloud, mode="cloud")
        await manager.run(tmp_path, "echo hi", SandboxConfig())
        cloud.destroy.assert_awaited_once_with("ss-cloud-abc123")


# ---------------------------------------------------------------------------
# Group 4: run() — FORCE_LOCAL override
# ---------------------------------------------------------------------------


class TestRunForceLocal:
    async def test_force_local_uses_local_runtime(self, tmp_path: Path) -> None:
        """When scan is sensitive + decision is forced local, local_runtime must run."""
        local = _make_runtime("ss-local-forced")
        cloud = _make_runtime("ss-cloud-never")
        scanner = _make_scanner(
            is_sensitive=True,
            recommendation=Recommendation.FORCE_LOCAL,
            findings=[_make_finding(tmp_path)],
        )
        engine = _make_burst_engine(mode="local", confidence="forced")
        manager = _make_manager(
            local_runtime=local,
            cloud_runtime=cloud,
            burst_engine=engine,
            scanner=scanner,
        )
        await manager.run(tmp_path, "echo hi", SandboxConfig())
        local.provision.assert_awaited_once()
        cloud.provision.assert_not_awaited()

    async def test_force_local_cloud_runtime_never_called(self, tmp_path: Path) -> None:
        """cloud_runtime must be completely silent when mode is forced local."""
        cloud = _make_runtime("ss-cloud-never")
        scanner = _make_scanner(
            is_sensitive=True,
            recommendation=Recommendation.FORCE_LOCAL,
            findings=[_make_finding(tmp_path)],
        )
        engine = _make_burst_engine(mode="local", confidence="forced")
        manager = _make_manager(
            cloud_runtime=cloud,
            burst_engine=engine,
            scanner=scanner,
        )
        await manager.run(tmp_path, "echo hi", SandboxConfig())
        cloud.provision.assert_not_awaited()
        cloud.execute.assert_not_awaited()
        cloud.destroy.assert_not_awaited()

    async def test_force_local_runtime_mode_in_result(self, tmp_path: Path) -> None:
        """RunResult.runtime_mode must be 'local' for a forced-local decision."""
        scanner = _make_scanner(
            is_sensitive=True,
            recommendation=Recommendation.FORCE_LOCAL,
            findings=[_make_finding(tmp_path)],
        )
        engine = _make_burst_engine(mode="local", confidence="forced")
        manager = _make_manager(burst_engine=engine, scanner=scanner)
        result = await manager.run(tmp_path, "echo hi", SandboxConfig())
        assert result.runtime_mode == "local"
        assert result.burst_confidence == "forced"


# ---------------------------------------------------------------------------
# Group 5: run() — cloud_runtime=None fallback
# ---------------------------------------------------------------------------


class TestCloudRuntimeNoneFallback:
    async def test_falls_back_to_local_when_cloud_runtime_none(
        self, tmp_path: Path
    ) -> None:
        """When cloud_runtime=None and decision==cloud, local_runtime must run."""
        local = _make_runtime("ss-local-fallback")
        engine = _make_burst_engine(mode="cloud", confidence="preferred")
        manager = _make_manager(
            local_runtime=local,
            cloud_runtime=None,
            burst_engine=engine,
        )
        await manager.run(tmp_path, "echo hi", SandboxConfig())
        local.provision.assert_awaited_once()

    async def test_runtime_mode_is_local_after_fallback(self, tmp_path: Path) -> None:
        """RunResult.runtime_mode must be 'local' when falling back from cloud."""
        engine = _make_burst_engine(mode="cloud", confidence="preferred")
        manager = _make_manager(cloud_runtime=None, burst_engine=engine)
        result = await manager.run(tmp_path, "echo hi", SandboxConfig())
        assert result.runtime_mode == "local"

    async def test_cloud_runtime_unavailable_audit_event_emitted(
        self, tmp_path: Path
    ) -> None:
        """A 'cloud_runtime_unavailable' audit event must be recorded on fallback."""
        audit = MagicMock(spec=AuditLogger)
        engine = _make_burst_engine(mode="cloud", confidence="preferred")
        manager = _make_manager(
            cloud_runtime=None,
            burst_engine=engine,
            audit_logger=audit,
        )
        await manager.run(tmp_path, "echo hi", SandboxConfig())
        recorded_events = [c.args[0]["event"] for c in audit.record.call_args_list]
        assert "cloud_runtime_unavailable" in recorded_events


# ---------------------------------------------------------------------------
# Group 6: destroy always called
# ---------------------------------------------------------------------------


class TestDestroyAlwaysCalled:
    async def test_destroy_called_after_success(self, tmp_path: Path) -> None:
        """destroy() must be called once after a successful execute()."""
        local = _make_runtime("ss-abc123456789")
        manager = _make_manager(local_runtime=local, mode="local")
        await manager.run(tmp_path, "echo hi", SandboxConfig())
        local.destroy.assert_awaited_once_with("ss-abc123456789")

    async def test_destroy_called_after_execute_raises(self, tmp_path: Path) -> None:
        """destroy() must be called even when execute() raises; exception propagates."""
        local = _make_runtime("ss-execfail0000")
        local.execute.side_effect = RuntimeError("container crashed")
        manager = _make_manager(local_runtime=local, mode="local")
        with pytest.raises(RuntimeError, match="container crashed"):
            await manager.run(tmp_path, "bad-cmd", SandboxConfig())
        local.destroy.assert_awaited_once_with("ss-execfail0000")

    async def test_destroy_not_called_if_provision_raises(
        self, tmp_path: Path
    ) -> None:
        """destroy() must NOT be called when provision() raises (no instance exists)."""
        local = _make_runtime("ss-provfail0000")
        local.provision.side_effect = RuntimeError("no disk space")
        manager = _make_manager(local_runtime=local, mode="local")
        with pytest.raises(RuntimeError, match="no disk space"):
            await manager.run(tmp_path, "echo hi", SandboxConfig())
        local.destroy.assert_not_awaited()
