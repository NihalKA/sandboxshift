"""SandboxManager — central orchestrator for SandboxShift.

Coordinates the full sandbox lifecycle:
  1. SensitivityScanner  → SensitivityResult  (skipped if config.skip_sensitivity_check)
  2. BurstEngine         → BurstDecision
  3. Runtime selection   → local_runtime or cloud_runtime
  4. provision / execute / destroy (destroy in finally — always runs)
  5. Return RunResult

Design: runtimes are injected at construction time so SandboxManager never
knows how to build FargateRuntime (which needs cluster_arn, subnet_ids, etc.
that are not in SandboxConfig). This follows the same single-responsibility
pattern as Decision #12 (BurstEngine enforces FORCE_LOCAL, not SandboxManager).

Port-forwarded tasks in cloud mode are handled by FargateRuntime in server mode:
the task is launched with a public-IP security group, execute() returns as soon
as the task is RUNNING, and destroy() skips the ECS stop (task keeps running).
Use `sandboxshift stop <instance_id>` to terminate a cloud server task.

Audit events emitted:
  "run_start"                 — before scan; always emitted
  "sensitivity_check_skipped" — when config.skip_sensitivity_check is True
  "sensitivity_blocked"       — when findings caused the run to be blocked
  "cloud_runtime_unavailable" — when decision==cloud/preferred but cloud_runtime is None
  "run_complete"              — after successful execute + destroy
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import SandboxConfig
from ..observability.audit import AuditLogger
from .burst.engine import BurstEngine
from .detection.sensitivity import SensitivityResult, SensitivityScanner, Recommendation
from .runtime.base import Runtime, TaskResult


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SensitivityBlockedError(Exception):
    """Raised by SandboxManager.run() when the workspace contains sensitive data
    and the caller has not set config.skip_sensitivity_check = True.

    Attributes:
        findings: Human-readable list of sensitivity findings from
                  SensitivityResult.explain().
    """

    def __init__(self, findings: list[str]) -> None:
        self.findings = findings
        super().__init__(
            f"Workspace contains sensitive data ({len(findings)} finding(s)). "
            "To override: pass --skip-sensitivity-check on the CLI, or add "
            "'skip_sensitivity_check: true' under the 'sandbox' key in sandboxshift.yaml."
        )


class CloudRuntimeRequiredError(Exception):
    """Raised when BurstEngine makes a forced cloud decision but cloud_runtime is None.

    A forced decision means a hard resource requirement (min_cpu or min_memory)
    cannot be satisfied locally. The run is blocked entirely — it must not silently
    fall back to local, as that would violate the operator's explicit requirement.

    Attributes:
        reason: The BurstDecision.reason string explaining why cloud was required.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            f"Cloud execution is required ({reason}) but no cloud runtime is configured. "
            "Set FARGATE_* environment variables to enable cloud bursting, or remove "
            "min_cpu / min_memory requirements from sandboxshift.yaml."
        )


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    """Immutable result of a complete SandboxManager.run() invocation.

    Attributes:
        task_result:          Exit code, stdout, stderr, and execute() duration.
        runtime_mode:         Actual mode used — "local" or "cloud".
        sensitivity_reasons:  Human-readable findings from SensitivityResult.explain().
                              Always empty in V1 (findings block execution;
                              skip_sensitivity_check bypasses the scan entirely).
        burst_confidence:     "forced" or "preferred" from BurstDecision.
        duration_seconds:     Total wall-clock time for the entire run() call,
                              including provision, execute, and destroy.
    """

    task_result: TaskResult
    runtime_mode: str
    sensitivity_reasons: list[str]
    burst_confidence: str
    duration_seconds: float


# ---------------------------------------------------------------------------
# SandboxManager
# ---------------------------------------------------------------------------


class SandboxManager:
    """Central orchestrator for SandboxShift sandbox execution.

    Runtimes are injected at construction time.  SandboxManager never
    constructs a Runtime itself — this keeps AWS-specific parameters (cluster
    ARN, subnet IDs, …) out of the manager entirely.

    Args:
        local_runtime:  Required. PodmanRuntime (or any Runtime) for local execution.
        cloud_runtime:  Optional. FargateRuntime for cloud execution.  When None
                        and BurstEngine decides "cloud" with confidence="preferred",
                        falls back to local with a stderr warning (fail-closed).
                        When None and confidence="forced" (min_cpu or min_memory
                        requirement not met), raises CloudRuntimeRequiredError.
        burst_engine:   BurstEngine instance.  Decides local vs cloud.
        scanner:        SensitivityScanner instance.  Must run before burst_engine.
        audit_logger:   Optional AuditLogger.  Defaults to the V1 no-op stub.

    Raises:
        ValueError: If local_runtime is None.
    """

    def __init__(
        self,
        local_runtime: Runtime,
        cloud_runtime: Runtime | None,
        burst_engine: BurstEngine,
        scanner: SensitivityScanner,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        if local_runtime is None:
            raise ValueError("local_runtime is required and must not be None")
        self._local_runtime = local_runtime
        self._cloud_runtime = cloud_runtime
        self._burst_engine = burst_engine
        self._scanner = scanner
        self._audit = audit_logger if audit_logger is not None else AuditLogger()

    async def run(
        self,
        workspace: Path,
        task: str,
        config: SandboxConfig,
    ) -> RunResult:
        """Execute a task inside an automatically selected sandbox.

        Steps:
          1. Emit "run_start" audit event.
          2a. If config.skip_sensitivity_check is True: emit audit warning,
              produce an empty SensitivityResult (ALLOW_CLOUD).
          2b. Otherwise: SensitivityScanner.scan(workspace) → SensitivityResult.
              If findings exist → emit "sensitivity_blocked" audit event and
              raise SensitivityBlockedError(findings). No execution occurs.
          3. BurstEngine.decide(scan_result, workspace, config) → BurstDecision.
          4. If decision.mode == "cloud" but cloud_runtime is None:
             - confidence="preferred" → emit "cloud_runtime_unavailable" audit
               event; print a stderr warning; override mode to "local" (graceful
               fallback).
             - confidence="forced" → emit "cloud_runtime_unavailable" audit
               event; raise CloudRuntimeRequiredError. Run is blocked entirely.
          5. Select runtime: cloud_runtime if mode=="cloud", else local_runtime.
          6. runtime.provision(workspace, config) → instance_id.
          7. try: runtime.execute(instance_id, task, config) → TaskResult
             finally: runtime.destroy(instance_id)  [always runs]
          8. Build and return RunResult.
          9. Emit "run_complete" audit event.

        Port-forwarded tasks in cloud mode (config.ports non-empty, mode=="cloud"):
        FargateRuntime handles this as server mode — it launches the task with a
        public-IP security group, waits until RUNNING, prints the URL, and returns
        immediately. destroy() skips the ECS stop so the task keeps running.

        If provision() raises, destroy() is NOT called (no instance to destroy).
        If execute() raises, destroy() IS called (finally block), then the
        exception propagates — no RunResult is returned.

        Args:
            workspace: Directory to mount into the sandbox. Must exist.
            task:      Shell command string passed to the runtime.
            config:    Sandbox resource and network configuration.

        Returns:
            RunResult on success.

        Raises:
            SensitivityBlockedError: If the workspace contains sensitive data
                and config.skip_sensitivity_check is False.
            CloudRuntimeRequiredError: If BurstEngine forces cloud execution
                (min_cpu or min_memory requirement) but cloud_runtime is None.
            Any exception raised by scanner.scan(), burst_engine.decide(),
            runtime.provision(), or runtime.execute() propagates to the caller.
        """
        wall_start = time.perf_counter()

        # Step 1: Audit — run start
        self._audit.record(
            {
                "event": "run_start",
                "workspace": str(workspace),
                "task": task,
                "skip_sensitivity_check": config.skip_sensitivity_check,
            }
        )

        # Step 2: Sensitivity scan (or skip)
        if config.skip_sensitivity_check:
            self._audit.record(
                {
                    "event": "sensitivity_check_skipped",
                    "workspace": str(workspace),
                    "reason": "skip_sensitivity_check=True in config",
                }
            )
            scan_result = SensitivityResult(
                is_sensitive=False,
                findings=[],
                recommendation=Recommendation.ALLOW_CLOUD,
            )
        else:
            scan_result = await self._scanner.scan(workspace)
            if scan_result.is_sensitive:
                findings = scan_result.explain()
                self._audit.record(
                    {
                        "event": "sensitivity_blocked",
                        "workspace": str(workspace),
                        "findings_count": len(findings),
                    }
                )
                raise SensitivityBlockedError(findings)

        # Step 3: Burst decision
        decision = await self._burst_engine.decide(scan_result, workspace, config)

        runtime_mode = decision.mode

        # Step 4: Cloud fallback / block when cloud_runtime is not available.
        # - confidence="preferred": RAM threshold advisory → fall back to local
        #   with a visible stderr warning so the user knows why.
        # - confidence="forced": hard requirement (min_cpu / min_memory) → block.
        if runtime_mode == "cloud" and self._cloud_runtime is None:
            self._audit.record(
                {
                    "event": "cloud_runtime_unavailable",
                    "workspace": str(workspace),
                    "reason": decision.reason,
                    "original_confidence": decision.confidence,
                }
            )
            if decision.confidence == "forced":
                raise CloudRuntimeRequiredError(decision.reason)
            # confidence == "preferred" — advisory only, fall back gracefully.
            # Print a visible warning so the user understands why the run
            # went local instead of cloud (e.g. sandbox.mode: cloud in YAML
            # but sandboxshift-setup.sh cloud was never run).
            print(
                "[sandboxshift] Warning: cloud mode requested but Fargate is not "
                "configured (FARGATE_* env vars missing). "
                "Falling back to local (Podman). "
                "Run './sandboxshift-setup.sh cloud' to enable cloud burst.",
                file=sys.stderr,
                flush=True,
            )
            runtime_mode = "local"

        # Step 5: Runtime selection
        runtime: Runtime = (
            self._cloud_runtime  # type: ignore[assignment]  # guarded by mode check above
            if runtime_mode == "cloud"
            else self._local_runtime
        )

        # Step 6: Provision
        instance_id = await runtime.provision(workspace, config)

        # Step 7: Execute with guaranteed destroy
        try:
            task_result = await runtime.execute(instance_id, task, config)
        finally:
            await runtime.destroy(instance_id)

        # Steps 8-9: Build result + audit
        duration = time.perf_counter() - wall_start

        result = RunResult(
            task_result=task_result,
            runtime_mode=runtime_mode,
            sensitivity_reasons=scan_result.explain(),
            burst_confidence=decision.confidence,
            duration_seconds=duration,
        )

        self._audit.record(
            {
                "event": "run_complete",
                "runtime_mode": runtime_mode,
                "exit_code": task_result.exit_code,
                "duration_seconds": round(duration, 3),
                "burst_confidence": decision.confidence,
            }
        )

        return result
