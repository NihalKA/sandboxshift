"""Tests for BurstEngine, BurstDecision, and get_available_ram_gb."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from src.config import SandboxConfig
from src.sandbox.burst.engine import BurstDecision, BurstEngine, get_available_ram_gb, get_cpu_count
from src.sandbox.detection import Recommendation, SensitivityResult

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

DUMMY_WORKSPACE = Path("/tmp/dummy_workspace")
RAM_PATCH = "src.sandbox.burst.engine.get_available_ram_gb"
CPU_PATCH = "src.sandbox.burst.engine.get_cpu_count"


def make_sensitive_result() -> SensitivityResult:
    """Return a SensitivityResult that mandates FORCE_LOCAL."""
    return SensitivityResult(
        is_sensitive=True,
        findings=[],
        recommendation=Recommendation.FORCE_LOCAL,
    )


def make_clean_result() -> SensitivityResult:
    """Return a SensitivityResult that allows cloud."""
    return SensitivityResult(
        is_sensitive=False,
        findings=[],
        recommendation=Recommendation.ALLOW_CLOUD,
    )


# ---------------------------------------------------------------------------
# FORCE_LOCAL enforcement (4 tests)
# ---------------------------------------------------------------------------


async def test_force_local_returns_local_mode() -> None:
    engine = BurstEngine()
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_sensitive_result(), DUMMY_WORKSPACE)
    assert decision.mode == "local"


async def test_force_local_confidence_is_forced() -> None:
    engine = BurstEngine()
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_sensitive_result(), DUMMY_WORKSPACE)
    assert decision.confidence == "forced"


async def test_force_local_reason_string() -> None:
    engine = BurstEngine()
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_sensitive_result(), DUMMY_WORKSPACE)
    assert decision.reason == "sensitive data detected"


async def test_force_local_ignores_ram() -> None:
    """When sensitivity is FORCE_LOCAL, get_available_ram_gb must never be called."""
    engine = BurstEngine()
    with patch(RAM_PATCH) as mock_ram:
        decision = await engine.decide(make_sensitive_result(), DUMMY_WORKSPACE)
    mock_ram.assert_not_called()
    assert decision.mode == "local"


# ---------------------------------------------------------------------------
# RAM-based local decision (4 tests)
# ---------------------------------------------------------------------------


async def test_high_ram_returns_local_mode() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.mode == "local"


async def test_high_ram_confidence_is_preferred() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.confidence == "preferred"


async def test_high_ram_reason_format() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.reason == "sufficient RAM (8.0GB >= 4.0GB)"


async def test_ram_exactly_at_threshold_is_local() -> None:
    """RAM == threshold must be treated as local (>= is inclusive)."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=4.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.mode == "local"


# ---------------------------------------------------------------------------
# RAM-based cloud decision (4 tests)
# ---------------------------------------------------------------------------


async def test_low_ram_returns_cloud_mode() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=2.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.mode == "cloud"


async def test_low_ram_confidence_is_preferred() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=2.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.confidence == "preferred"


async def test_low_ram_reason_format() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=2.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.reason == "insufficient RAM (2.0GB < 4.0GB)"


async def test_ram_just_below_threshold_is_cloud() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=3.9):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.mode == "cloud"


# ---------------------------------------------------------------------------
# Custom threshold (2 tests)
# ---------------------------------------------------------------------------


async def test_custom_threshold_high() -> None:
    """With a 16 GB threshold, 8 GB RAM should burst to cloud."""
    engine = BurstEngine(ram_threshold_gb=16.0)
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.mode == "cloud"


async def test_custom_threshold_low() -> None:
    """With a 1 GB threshold, 2 GB RAM should run locally."""
    engine = BurstEngine(ram_threshold_gb=1.0)
    with patch(RAM_PATCH, return_value=2.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.mode == "local"


# ---------------------------------------------------------------------------
# psutil failure handling (3 tests)
# ---------------------------------------------------------------------------


async def test_ram_read_failure_returns_local() -> None:
    engine = BurstEngine()
    with patch(RAM_PATCH, side_effect=RuntimeError("psutil failed to read available RAM: mock error")):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.mode == "local"


async def test_ram_read_failure_confidence_is_forced() -> None:
    engine = BurstEngine()
    with patch(RAM_PATCH, side_effect=RuntimeError("psutil failed to read available RAM: mock error")):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.confidence == "forced"


async def test_ram_read_failure_reason_string() -> None:
    engine = BurstEngine()
    with patch(RAM_PATCH, side_effect=RuntimeError("psutil failed to read available RAM: mock error")):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE)
    assert decision.reason == "RAM read failed \u2014 defaulting to local"


# ---------------------------------------------------------------------------
# BurstDecision structure (3 tests \u2014 sync)
# ---------------------------------------------------------------------------


def test_burst_decision_is_frozen() -> None:
    d = BurstDecision(mode="local", reason="x", confidence="preferred")
    with pytest.raises(FrozenInstanceError):
        d.mode = "cloud"  # type: ignore[misc]


def test_burst_decision_fields_exist() -> None:
    d = BurstDecision(mode="local", reason="r", confidence="forced")
    assert hasattr(d, "mode")
    assert hasattr(d, "reason")
    assert hasattr(d, "confidence")


def test_burst_decision_mode_stored_correctly() -> None:
    d = BurstDecision(mode="cloud", reason="r", confidence="preferred")
    assert d.mode == "cloud"


# ---------------------------------------------------------------------------
# get_available_ram_gb() unit tests (2 tests \u2014 sync)
# ---------------------------------------------------------------------------


def test_get_available_ram_gb_returns_float() -> None:
    """Integration test \u2014 calls real psutil. Result must be a positive float."""
    result = get_available_ram_gb()
    assert isinstance(result, float)
    assert result > 0


def test_get_available_ram_gb_psutil_error_raises_runtime_error() -> None:
    with patch.object(psutil, "virtual_memory", side_effect=psutil.Error("mock")):
        with pytest.raises(RuntimeError, match="psutil failed to read available RAM"):
            get_available_ram_gb()


# ---------------------------------------------------------------------------
# get_cpu_count() unit tests (3 tests \u2014 sync)
# ---------------------------------------------------------------------------


def test_get_cpu_count_returns_positive_int() -> None:
    """Integration test \u2014 calls real psutil. Result must be a positive int."""
    result = get_cpu_count()
    assert isinstance(result, int)
    assert result > 0


def test_get_cpu_count_psutil_none_raises_runtime_error() -> None:
    with patch.object(psutil, "cpu_count", return_value=None):
        with pytest.raises(RuntimeError, match="psutil returned None for cpu_count"):
            get_cpu_count()


def test_get_cpu_count_psutil_error_raises_runtime_error() -> None:
    with patch.object(psutil, "cpu_count", side_effect=psutil.Error("mock")):
        with pytest.raises(RuntimeError, match="psutil failed to read CPU count"):
            get_cpu_count()


# ---------------------------------------------------------------------------
# min_memory_mb_required (6 tests)
# ---------------------------------------------------------------------------


async def test_min_memory_met_falls_through_to_ram_threshold() -> None:
    """min_memory satisfied locally \u2192 normal RAM threshold applies."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_memory_mb_required=1024)  # need 1GB, have 8GB
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "local"
    assert decision.confidence == "preferred"


async def test_min_memory_not_met_forces_cloud() -> None:
    """min_memory exceeds available RAM \u2192 cloud, forced."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_memory_mb_required=16384)  # need 16GB, have 2GB
    with patch(RAM_PATCH, return_value=2.0):  # 2GB = 2048MB < 16384MB
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "cloud"
    assert decision.confidence == "forced"


async def test_min_memory_not_met_reason_string() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_memory_mb_required=16384)
    with patch(RAM_PATCH, return_value=2.0):  # 2048MB available
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert "min_memory requirement (16384MB)" in decision.reason
    assert "2048MB" in decision.reason


async def test_min_memory_zero_is_ignored() -> None:
    """min_memory_mb_required=0 (default) \u2192 no cloud forcing."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_memory_mb_required=0)
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "local"


async def test_min_memory_exactly_met_is_local() -> None:
    """min_memory exactly equal to available \u2192 requirement satisfied, fall through."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_memory_mb_required=8192)  # need 8192MB
    with patch(RAM_PATCH, return_value=8.0):  # 8GB = 8192MB exactly
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "local"


async def test_min_memory_wins_over_ram_threshold() -> None:
    """Even if RAM > threshold, min_memory not met \u2192 cloud."""
    engine = BurstEngine(ram_threshold_gb=1.0)  # threshold low
    config = SandboxConfig(min_memory_mb_required=16384)  # need 16GB
    with patch(RAM_PATCH, return_value=2.0):  # 2GB: above 1GB threshold but below 16GB
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "cloud"
    assert decision.confidence == "forced"


# ---------------------------------------------------------------------------
# min_cpu_required (6 tests)
# ---------------------------------------------------------------------------


async def test_min_cpu_met_falls_through_to_ram_threshold() -> None:
    """min_cpu satisfied locally \u2192 normal RAM threshold applies."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_cpu_required=2.0)
    with patch(RAM_PATCH, return_value=8.0), patch(CPU_PATCH, return_value=8):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "local"
    assert decision.confidence == "preferred"


async def test_min_cpu_not_met_forces_cloud() -> None:
    """min_cpu exceeds local CPU count \u2192 cloud, forced."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_cpu_required=16.0)
    with patch(RAM_PATCH, return_value=8.0), patch(CPU_PATCH, return_value=4):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "cloud"
    assert decision.confidence == "forced"


async def test_min_cpu_not_met_reason_string() -> None:
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_cpu_required=16.0)
    with patch(RAM_PATCH, return_value=8.0), patch(CPU_PATCH, return_value=4):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert "min_cpu requirement (16.0)" in decision.reason
    assert "4" in decision.reason


async def test_min_cpu_zero_is_ignored() -> None:
    """min_cpu_required=0 (default) \u2192 CPU check skipped entirely."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_cpu_required=0.0)
    with patch(RAM_PATCH, return_value=8.0), patch(CPU_PATCH) as mock_cpu:
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    mock_cpu.assert_not_called()
    assert decision.mode == "local"


async def test_min_cpu_read_failure_forces_cloud() -> None:
    """If get_cpu_count raises and min_cpu > 0 \u2192 fail-closed \u2192 cloud, forced."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_cpu_required=4.0)
    with patch(RAM_PATCH, return_value=8.0), patch(CPU_PATCH, side_effect=RuntimeError("mock")):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "cloud"
    assert decision.confidence == "forced"
    assert "could not be verified" in decision.reason


async def test_min_cpu_exactly_met_is_local() -> None:
    """min_cpu exactly equal to CPU count \u2192 requirement satisfied, fall through."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig(min_cpu_required=4.0)
    with patch(RAM_PATCH, return_value=8.0), patch(CPU_PATCH, return_value=4):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "local"


# ---------------------------------------------------------------------------
# Workspace/config parameter passthrough (2 tests)
# ---------------------------------------------------------------------------


async def test_decide_accepts_none_config() -> None:
    """config=None must not raise \u2014 it is unused in V1."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=None)
    assert isinstance(decision, BurstDecision)


async def test_decide_default_config_no_min_requirements() -> None:
    """Default SandboxConfig has no min requirements \u2192 normal RAM threshold."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    config = SandboxConfig()  # all defaults
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=config)
    assert decision.mode == "local"
    assert decision.confidence == "preferred"
