"""Tests for BurstEngine, BurstDecision, and get_available_ram_gb."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from src.sandbox.burst.engine import BurstDecision, BurstEngine, get_available_ram_gb
from src.sandbox.detection import Recommendation, SensitivityResult

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

DUMMY_WORKSPACE = Path("/tmp/dummy_workspace")
RAM_PATCH = "src.sandbox.burst.engine.get_available_ram_gb"


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
# BurstDecision structure (3 tests — sync)
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
# get_available_ram_gb() unit tests (2 tests — sync)
# ---------------------------------------------------------------------------


def test_get_available_ram_gb_returns_float() -> None:
    """Integration test — calls real psutil. Result must be a positive float."""
    result = get_available_ram_gb()
    assert isinstance(result, float)
    assert result > 0


def test_get_available_ram_gb_psutil_error_raises_runtime_error() -> None:
    with patch.object(psutil, "virtual_memory", side_effect=psutil.Error("mock")):
        with pytest.raises(RuntimeError, match="psutil failed to read available RAM"):
            get_available_ram_gb()


# ---------------------------------------------------------------------------
# Workspace/config parameter passthrough (1 test)
# ---------------------------------------------------------------------------


async def test_decide_accepts_none_config() -> None:
    """config=None must not raise — it is unused in V1."""
    engine = BurstEngine(ram_threshold_gb=4.0)
    with patch(RAM_PATCH, return_value=8.0):
        decision = await engine.decide(make_clean_result(), DUMMY_WORKSPACE, config=None)
    assert isinstance(decision, BurstDecision)
