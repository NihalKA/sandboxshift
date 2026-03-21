"""BurstEngine — decides whether to run a sandbox task locally or burst to cloud.

BurstEngine is Step 2 in SandboxManager's pipeline. It consumes the SensitivityResult
produced by SensitivityScanner and available system RAM to return an immutable
BurstDecision. When SensitivityScanner sets recommendation=FORCE_LOCAL, BurstEngine
must honour that unconditionally (Decision #9 — never re-open).

get_available_ram_gb() and get_cpu_count() are module-level functions (not methods)
so tests can patch them with unittest.mock.patch without monkey-patching psutil internals.

All blocking I/O (psutil calls) runs via asyncio.to_thread to avoid blocking the event
loop.

Min resource requirements (Decision #55):
  If config.min_memory_mb_required > 0 and available RAM < that value, BurstEngine
  forces cloud (confidence=forced) regardless of the global RAM threshold.
  If config.min_cpu_required > 0 and local CPU count < that value, BurstEngine
  forces cloud (confidence=forced).
  Both checks run after the FORCE_LOCAL sensitivity check and before the global
  RAM threshold check.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from ..detection import Recommendation, SensitivityResult

if TYPE_CHECKING:
    from ...config import SandboxConfig


@dataclass(frozen=True)
class BurstDecision:
    """Immutable result of the burst-or-local decision.

    Attributes:
        mode:       "local" or "cloud"
        reason:     Human-readable explanation — logged verbatim in the audit trail.
        confidence: "forced" (cannot be overridden) or "preferred" (advisory, CLI can
                    override in V2).
    """

    mode: str
    reason: str
    confidence: str


def get_available_ram_gb() -> float:
    """Return available system RAM in binary gigabytes.

    Uses psutil.virtual_memory().available — the amount of RAM that can be given to
    processes immediately without swapping, not total installed RAM.

    Raises:
        RuntimeError: if psutil fails to read memory info.
    """
    try:
        mem = psutil.virtual_memory()
        return mem.available / (1024**3)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"psutil failed to read available RAM: {exc}") from exc


def get_cpu_count() -> int:
    """Return the number of logical CPUs on this machine.

    Uses psutil.cpu_count(logical=True).

    Raises:
        RuntimeError: if psutil returns None or raises.
    """
    try:
        count = psutil.cpu_count(logical=True)
        if count is None:
            raise RuntimeError("psutil returned None for cpu_count")
        return count
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"psutil failed to read CPU count: {exc}") from exc


class BurstEngine:
    """Decides whether to run a sandbox locally or burst to cloud.

    Args:
        ram_threshold_gb: Minimum available RAM (in binary GB) required to run locally.
                          Defaults to 1.0 GB. Configurable at construction time.
    """

    def __init__(self, ram_threshold_gb: float = 1.0) -> None:
        self._threshold = ram_threshold_gb

    async def decide(
        self,
        sensitivity_result: SensitivityResult,
        workspace: Path,  # noqa: ARG002 — reserved for V2 feature parity with ADR-001
        config: "SandboxConfig | None" = None,
    ) -> BurstDecision:
        """Return the burst decision for a sandbox task.

        Decision order (must not be changed):
          1. If sensitivity_result says FORCE_LOCAL → always local, confidence=forced.
          2. Read available RAM via asyncio.to_thread(get_available_ram_gb).
             If psutil fails → local, confidence=forced (fail-closed).
          3. If config.min_memory_mb_required > 0 and available RAM (MB) < requirement
             → cloud, confidence=forced.
          4. If config.min_cpu_required > 0 and local CPU count < requirement
             → cloud, confidence=forced.
             If CPU count read fails → cloud, confidence=forced (fail-closed).
          5. If RAM >= threshold → local, confidence=preferred.
          6. Otherwise → cloud, confidence=preferred.

        Args:
            sensitivity_result: Result from SensitivityScanner.scan().
            workspace:          Workspace path — unused in V1, accepted for ADR-001
                                interface compatibility.
            config:             Optional sandbox config. When provided, min_cpu_required
                                and min_memory_mb_required are checked (Decision #55).

        Returns:
            BurstDecision with mode, reason, and confidence.
        """
        # Step 1: sensitivity hard-stop — unconditional, no RAM read needed.
        if sensitivity_result.recommendation == Recommendation.FORCE_LOCAL:
            return BurstDecision(
                mode="local",
                reason="sensitive data detected",
                confidence="forced",
            )

        # Step 2: read available RAM (blocking call → offload to thread).
        try:
            available_gb = await asyncio.to_thread(get_available_ram_gb)
        except RuntimeError:
            # Fail-closed: unknown RAM state must never allow cloud execution.
            return BurstDecision(
                mode="local",
                reason="RAM read failed — defaulting to local",
                confidence="forced",
            )

        # Step 3: min_memory_mb_required check (Decision #55).
        if config is not None and config.min_memory_mb_required > 0:
            available_mb = int(available_gb * 1024)
            if available_mb < config.min_memory_mb_required:
                return BurstDecision(
                    mode="cloud",
                    reason=(
                        f"min_memory requirement ({config.min_memory_mb_required}MB) "
                        f"exceeds local available RAM ({available_mb}MB)"
                    ),
                    confidence="forced",
                )

        # Step 4: min_cpu_required check (Decision #55).
        if config is not None and config.min_cpu_required > 0:
            try:
                cpu_count = await asyncio.to_thread(get_cpu_count)
            except RuntimeError:
                # Fail-closed: unknown CPU count → cannot confirm requirement is met.
                return BurstDecision(
                    mode="cloud",
                    reason=(
                        f"min_cpu requirement ({config.min_cpu_required:.1f}) "
                        "could not be verified (CPU count read failed)"
                    ),
                    confidence="forced",
                )
            if cpu_count < config.min_cpu_required:
                return BurstDecision(
                    mode="cloud",
                    reason=(
                        f"min_cpu requirement ({config.min_cpu_required:.1f}) "
                        f"exceeds local CPU count ({cpu_count})"
                    ),
                    confidence="forced",
                )

        # Steps 5 & 6: RAM threshold check.
        if available_gb >= self._threshold:
            return BurstDecision(
                mode="local",
                reason=f"sufficient RAM ({available_gb:.1f}GB >= {self._threshold:.1f}GB)",
                confidence="preferred",
            )
        return BurstDecision(
            mode="cloud",
            reason=f"insufficient RAM ({available_gb:.1f}GB < {self._threshold:.1f}GB)",
            confidence="preferred",
        )
