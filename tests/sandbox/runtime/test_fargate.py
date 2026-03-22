"""Tests for FargateRuntime.

Groups:
  Group 1 — Constructor validation (4 tests)
  Group 2 — provision() (8 tests)
  Group 3 — execute() (12 tests)
  Group 4 — destroy() (8 tests)
"""

from __future__ import annotations

import re
import stat as _stat_module
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sandboxshift.config import SandboxConfig
from sandboxshift.observability.audit import AuditLogger
from sandboxshift.sandbox.runtime.fargate import FargateRuntime

# ---------------------------------------------------------------------------
# Patch paths
# ---------------------------------------------------------------------------

_TO_THREAD = "sandboxshift.sandbox.runtime.fargate.asyncio.to_thread"
_SLEEP = "sandboxshift.sandbox.runtime.fargate.asyncio.sleep"
_BOTO3_SESSION = "sandboxshift.sandbox.runtime.fargate.boto3.Session"

_MOCK_TASK_DEF_ARN = "arn:aws:ecs:us-east-1:123:task-definition/sandboxshift-sandbox-ss-abc123:1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def aws_clients(monkeypatch):
    mock_session = MagicMock()
    mock_s3 = MagicMock()
    mock_ecs = MagicMock()
    mock_logs = MagicMock()
    mock_session.client.side_effect = lambda svc, **kw: {
        "s3": mock_s3, "ecs": mock_ecs, "logs": mock_logs
    }[svc]
    # register_task_definition returns the registered ARN
    mock_ecs.register_task_definition.return_value = {
        "taskDefinition": {"taskDefinitionArn": _MOCK_TASK_DEF_ARN}
    }
    monkeypatch.setattr(_BOTO3_SESSION, lambda: mock_session)
    return mock_session, mock_s3, mock_ecs, mock_logs


@pytest.fixture()
def mock_to_thread(monkeypatch):
    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(_TO_THREAD, fake_to_thread)


@pytest.fixture()
def mock_sleep(monkeypatch):
    async def fake_sleep(_: float) -> None:
        pass
    monkeypatch.setattr(_SLEEP, fake_sleep)


@pytest.fixture()
def runtime(aws_clients, mock_to_thread):
    return FargateRuntime(
        cluster_arn="arn:aws:ecs:us-east-1:123456789:cluster/test",
        execution_role_arn="arn:aws:iam::123456789:role/test-execution",
        task_role_arn="arn:aws:iam::123456789:role/test-task",
        subnet_ids=["subnet-abc123"],
        security_group_ids=["sg-abc123"],
        region="us-east-1",
        log_group="/sandboxshift/tasks",
        workspace_bucket="sandboxshift-ws-123456789-abc123",
        task_family="sandboxshift-sandbox",
        ecr_image="",  # empty = Docker Hub default
    )


@pytest.fixture()
def default_config():
    return SandboxConfig()


@pytest.fixture()
def tmp_workspace(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.31")
    (tmp_path / "main.py").write_text("print('hello')")
    return tmp_path


def _ecs_stopped(exit_code: int = 0):
    """Helper: ECS describe_tasks response for a STOPPED task."""
    return {
        "tasks": [{"lastStatus": "STOPPED", "containers": [{"exitCode": exit_code}]}]
    }


def _setup_ecs_mocks(mock_ecs, exit_code: int = 0):
    """Set up standard ECS mock responses."""
    mock_ecs.run_task.return_value = {
        "tasks": [{"taskArn": "arn:aws:ecs:us-east-1:123:task/abc123"}],
        "failures": [],
    }
    mock_ecs.describe_tasks.return_value = _ecs_stopped(exit_code)


# ===========================================================================
# Group 1 — Constructor validation
# ===========================================================================


def test_init_stores_config(aws_clients):
    rt = FargateRuntime(
        cluster_arn="arn:cluster",
        execution_role_arn="arn:aws:iam::123:role/exec",
        task_role_arn="arn:aws:iam::123:role/task",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        region="eu-west-1",
        log_group="/lg",
        workspace_bucket="my-bucket",
    )
    assert rt._cluster_arn == "arn:cluster"
    assert rt._execution_role_arn == "arn:aws:iam::123:role/exec"
    assert rt._task_role_arn == "arn:aws:iam::123:role/task"
    assert rt._region == "eu-west-1"
    assert rt._log_group == "/lg"
    assert rt._task_family == "sandboxshift-sandbox"
    assert rt._ecr_image == "sandboxshift/runtime-multi:latest"


def test_init_raises_on_empty_cluster_arn(aws_clients):
    with pytest.raises(ValueError, match="cluster_arn"):
        FargateRuntime(
            cluster_arn="",
            execution_role_arn="arn:exec",
            task_role_arn="arn:task",
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            region="us-east-1",
            log_group="/lg",
            workspace_bucket="bucket",
        )


def test_init_raises_on_empty_region(aws_clients):
    with pytest.raises(ValueError, match="region"):
        FargateRuntime(
            cluster_arn="arn:cluster",
            execution_role_arn="arn:exec",
            task_role_arn="arn:task",
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            region="",
            log_group="/lg",
            workspace_bucket="bucket",
        )


def test_init_raises_on_empty_execution_role_arn(aws_clients):
    with pytest.raises(ValueError, match="execution_role_arn"):
        FargateRuntime(
            cluster_arn="arn:cluster",
            execution_role_arn="",
            task_role_arn="arn:task",
            subnet_ids=["subnet-1"],
            security_group_ids=["sg-1"],
            region="us-east-1",
            log_group="/lg",
            workspace_bucket="bucket",
        )


# ===========================================================================
# Group 2 — provision()
# ===========================================================================


async def test_provision_returns_instance_id_format(
    runtime, tmp_workspace, default_config, aws_clients
):
    _, mock_s3, _, _ = aws_clients
    mock_s3.get_paginator.return_value.paginate.return_value = iter([{"Contents": []}])
    result = await runtime.provision(tmp_workspace, default_config)
    assert isinstance(result, str)
    assert re.match(r"ss-[0-9a-f]{12}$", result)


async def test_provision_registers_task_definition(
    runtime, tmp_workspace, default_config, aws_clients
):
    """provision() must call ecs.register_task_definition with the correct
    CPU units, memory, family prefix, and role ARNs."""
    _, mock_s3, mock_ecs, _ = aws_clients
    config = SandboxConfig(cpu_limit=2.0, memory_limit_mb=4096)
    instance_id = await runtime.provision(tmp_workspace, config)
    mock_ecs.register_task_definition.assert_called_once()
    call_kw = mock_ecs.register_task_definition.call_args.kwargs
    assert call_kw["cpu"] == "2048"     # 2.0 vCPUs × 1024
    assert call_kw["memory"] == "4096"
    assert call_kw["executionRoleArn"] == "arn:aws:iam::123456789:role/test-execution"
    assert call_kw["taskRoleArn"] == "arn:aws:iam::123456789:role/test-task"
    assert call_kw["family"].startswith("sandboxshift-sandbox-")
    assert instance_id in call_kw["family"]


async def test_provision_uploads_workspace_files(
    runtime, tmp_workspace, default_config, aws_clients
):
    _, mock_s3, _, _ = aws_clients
    await runtime.provision(tmp_workspace, default_config)
    # tmp_workspace has requirements.txt + main.py = 2 files
    assert mock_s3.put_object.call_count == 2
    keys = [call.kwargs["Key"] for call in mock_s3.put_object.call_args_list]
    assert all(k.startswith("workspace/") for k in keys)


async def test_provision_raises_on_missing_workspace(
    runtime, default_config, aws_clients
):
    _, mock_s3, mock_ecs, _ = aws_clients
    with pytest.raises(FileNotFoundError):
        await runtime.provision(
            Path("/tmp/sandboxshift-does-not-exist-xyz"), default_config
        )
    mock_ecs.register_task_definition.assert_not_called()


async def test_provision_raises_on_workspace_too_large(
    runtime, tmp_workspace, default_config, aws_clients, monkeypatch
):
    _, mock_s3, mock_ecs, _ = aws_clients

    class _BigStat:
        st_size = 600 * 1024 * 1024  # 600 MB
        st_mode = _stat_module.S_IFREG | 0o644

    monkeypatch.setattr(Path, "stat", lambda self, **kwargs: _BigStat())
    with pytest.raises(ValueError, match="500 MB"):
        await runtime.provision(tmp_workspace, default_config)
    mock_ecs.register_task_definition.assert_not_called()


async def test_provision_stores_registered_task_def_arn(
    runtime, tmp_workspace, default_config, aws_clients
):
    """The registered ARN from ECS must be stored in state."""
    _, mock_s3, mock_ecs, _ = aws_clients
    instance_id = await runtime.provision(tmp_workspace, default_config)
    state = runtime._instances[instance_id]
    assert state.registered_task_def_arn == _MOCK_TASK_DEF_ARN


async def test_provision_records_audit_event(
    tmp_workspace, default_config, aws_clients, mock_to_thread
):
    mock_audit = MagicMock(spec=AuditLogger)
    rt = FargateRuntime(
        cluster_arn="arn:cluster",
        execution_role_arn="arn:exec",
        task_role_arn="arn:task",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        region="us-east-1",
        log_group="/lg",
        workspace_bucket="bucket",
        audit_logger=mock_audit,
    )
    await rt.provision(tmp_workspace, default_config)
    mock_audit.record.assert_called()
    event_dict = mock_audit.record.call_args.args[0]
    assert event_dict["event"] == "provision"
    assert "instance_id" in event_dict
    assert "task_def_arn" in event_dict


async def test_provision_registered_arn_in_audit_event(
    tmp_workspace, default_config, aws_clients, mock_to_thread
):
    """The registered task def ARN must appear in the provision audit event."""
    mock_audit = MagicMock(spec=AuditLogger)
    rt = FargateRuntime(
        cluster_arn="arn:cluster",
        execution_role_arn="arn:exec",
        task_role_arn="arn:task",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        region="us-east-1",
        log_group="/lg",
        workspace_bucket="bucket",
        audit_logger=mock_audit,
    )
    await rt.provision(tmp_workspace, default_config)
    event_dict = mock_audit.record.call_args.args[0]
    assert event_dict["task_def_arn"] == _MOCK_TASK_DEF_ARN


# ===========================================================================
# Group 3 — execute()
# ===========================================================================


async def test_execute_runs_ecs_task(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.execute(instance_id, "echo hi", default_config)
    mock_ecs.run_task.assert_called_once()


async def test_execute_uses_registered_task_def_arn(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    """_run_ecs_task must use state.registered_task_def_arn, not a static ARN."""
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.execute(instance_id, "echo hi", default_config)
    call_kw = mock_ecs.run_task.call_args.kwargs
    assert call_kw["taskDefinition"] == _MOCK_TASK_DEF_ARN


async def test_execute_no_cpu_memory_in_task_overrides(
    runtime, tmp_workspace, aws_clients, mock_sleep
):
    """Since CPU/memory are baked into the registered task def, run_task()
    must NOT include top-level 'cpu'/'memory' overrides (Fargate rejects them)."""
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    config = SandboxConfig(cpu_limit=2.0, memory_limit_mb=4096)
    instance_id = await runtime.provision(tmp_workspace, config)
    await runtime.execute(instance_id, "echo hi", config)
    overrides = mock_ecs.run_task.call_args.kwargs["overrides"]
    assert "cpu" not in overrides, "overrides.cpu must not be set (EC2-only field)"
    assert "memory" not in overrides, "overrides.memory must not be set (EC2-only field)"


async def test_execute_passes_command_override(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.execute(instance_id, "echo hi", default_config)
    call_kwargs = mock_ecs.run_task.call_args.kwargs
    overrides = call_kwargs["overrides"]["containerOverrides"][0]
    cmd = overrides["command"]
    assert cmd[0] == "-c"
    assert "echo hi" in cmd[1]


async def test_execute_includes_setup_command_in_task(
    runtime, tmp_workspace, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    config = SandboxConfig(setup_command="npm ci")
    instance_id = await runtime.provision(tmp_workspace, config)
    await runtime.execute(instance_id, "node index.js", config)
    overrides = mock_ecs.run_task.call_args.kwargs["overrides"]["containerOverrides"][0]
    cmd_str = overrides["command"][1]
    assert "npm ci" in cmd_str
    assert "node index.js" in cmd_str
    assert cmd_str.index("npm ci") < cmd_str.index("node index.js")


async def test_execute_passes_environment_vars(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.execute(instance_id, "echo hi", default_config)
    overrides = mock_ecs.run_task.call_args.kwargs["overrides"]["containerOverrides"][0]
    env_names = {e["name"] for e in overrides["environment"]}
    assert "SS_BUCKET" in env_names
    assert "SS_PREFIX" in env_names
    assert "SS_TASK_ID" in env_names
    env_dict = {e["name"]: e["value"] for e in overrides["environment"]}
    assert env_dict["SS_TASK_ID"] == instance_id


async def test_execute_returns_taskresult(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    from sandboxshift.sandbox.runtime.base import TaskResult

    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": [{"message": "hello"}]}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    result = await runtime.execute(instance_id, "echo hi", default_config)
    assert isinstance(result, TaskResult)
    assert result.exit_code == 0
    assert result.stdout == "hello"
    assert result.stderr == ""


async def test_execute_returns_exit_code_from_ecs(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs, exit_code=42)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    result = await runtime.execute(instance_id, "exit 42", default_config)
    assert result.exit_code == 42


async def test_execute_retrieves_cloudwatch_logs(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {
        "events": [{"message": "line1"}, {"message": "line2"}]
    }
    instance_id = await runtime.provision(tmp_workspace, default_config)
    result = await runtime.execute(instance_id, "echo hi", default_config)
    assert result.stdout == "line1\nline2"


async def test_execute_returns_empty_logs_on_cw_error(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.side_effect = Exception("CloudWatch unavailable")
    instance_id = await runtime.provision(tmp_workspace, default_config)
    result = await runtime.execute(instance_id, "echo hi", default_config)
    from sandboxshift.sandbox.runtime.base import TaskResult
    assert isinstance(result, TaskResult)
    assert result.stdout == ""
    assert result.stderr == ""


async def test_execute_raises_on_unknown_instance_id(runtime, default_config):
    with pytest.raises(RuntimeError, match="unknown instance_id"):
        await runtime.execute("ss-doesnotexist", "echo hi", default_config)


async def test_execute_duration_is_non_negative(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    result = await runtime.execute(instance_id, "echo hi", default_config)
    assert result.duration_seconds >= 0.0


async def test_execute_records_audit_event(
    tmp_workspace, default_config, aws_clients, mock_to_thread, mock_sleep
):
    mock_audit = MagicMock(spec=AuditLogger)
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    rt = FargateRuntime(
        cluster_arn="arn:cluster",
        execution_role_arn="arn:exec",
        task_role_arn="arn:task",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        region="us-east-1",
        log_group="/lg",
        workspace_bucket="bucket",
        audit_logger=mock_audit,
    )
    instance_id = await rt.provision(tmp_workspace, default_config)
    mock_audit.reset_mock()
    await rt.execute(instance_id, "echo hi", default_config)
    calls = [c.args[0] for c in mock_audit.record.call_args_list]
    execute_events = [c for c in calls if c.get("event") == "execute"]
    assert len(execute_events) >= 1
    assert "exit_code" in execute_events[0]


# ===========================================================================
# Group 4 — destroy()
# ===========================================================================


async def test_destroy_stops_ecs_task(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.execute(instance_id, "echo hi", default_config)
    await runtime.destroy(instance_id)
    mock_ecs.stop_task.assert_called_once()
    call = mock_ecs.stop_task.call_args
    assert "task/abc123" in call.kwargs.get("task", "")


async def test_destroy_deregisters_task_definition(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    """destroy() must call ecs.deregister_task_definition with the registered ARN."""
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.execute(instance_id, "echo hi", default_config)
    await runtime.destroy(instance_id)
    mock_ecs.deregister_task_definition.assert_called_once_with(
        taskDefinition=_MOCK_TASK_DEF_ARN
    )


async def test_destroy_deletes_s3_objects(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = iter(
        [{"Contents": [{"Key": "workspace/main.py"}]}]
    )
    mock_s3.get_paginator.return_value = mock_paginator
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.destroy(instance_id)
    mock_s3.delete_objects.assert_called()


async def test_destroy_removes_from_instances(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = iter([{"Contents": []}])
    mock_s3.get_paginator.return_value = mock_paginator
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.destroy(instance_id)
    assert instance_id not in runtime._instances


async def test_destroy_idempotent_on_unknown_id(runtime, aws_clients):
    _, mock_s3, mock_ecs, _ = aws_clients
    # Should not raise and should not call stop_task or deregister
    await runtime.destroy("ss-nonexistent")
    mock_ecs.stop_task.assert_not_called()
    mock_ecs.deregister_task_definition.assert_not_called()


async def test_destroy_never_raises_on_error(
    runtime, tmp_workspace, default_config, aws_clients, mock_sleep
):
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    mock_s3.delete_objects.side_effect = Exception("AWS exploded")
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = iter([{"Contents": []}])
    mock_s3.get_paginator.return_value = mock_paginator
    instance_id = await runtime.provision(tmp_workspace, default_config)
    # Must not raise
    await runtime.destroy(instance_id)


async def test_destroy_records_audit_event(
    tmp_workspace, default_config, aws_clients, mock_to_thread, mock_sleep
):
    mock_audit = MagicMock(spec=AuditLogger)
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    _setup_ecs_mocks(mock_ecs)
    mock_logs.get_log_events.return_value = {"events": []}
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = iter([{"Contents": []}])
    mock_s3.get_paginator.return_value = mock_paginator
    rt = FargateRuntime(
        cluster_arn="arn:cluster",
        execution_role_arn="arn:exec",
        task_role_arn="arn:task",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        region="us-east-1",
        log_group="/lg",
        workspace_bucket="bucket",
        audit_logger=mock_audit,
    )
    instance_id = await rt.provision(tmp_workspace, default_config)
    mock_audit.reset_mock()
    await rt.execute(instance_id, "echo hi", default_config)
    await rt.destroy(instance_id)
    calls = [c.args[0] for c in mock_audit.record.call_args_list]
    destroy_events = [c for c in calls if c.get("event") == "destroy"]
    assert len(destroy_events) >= 1


async def test_destroy_deregisters_even_without_execute(
    runtime, tmp_workspace, default_config, aws_clients
):
    """deregister must be called in destroy() even if execute() was never called."""
    _, mock_s3, mock_ecs, mock_logs = aws_clients
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = iter([{"Contents": []}])
    mock_s3.get_paginator.return_value = mock_paginator
    instance_id = await runtime.provision(tmp_workspace, default_config)
    await runtime.destroy(instance_id)
    mock_ecs.deregister_task_definition.assert_called_once_with(
        taskDefinition=_MOCK_TASK_DEF_ARN
    )
    mock_ecs.stop_task.assert_not_called()  # no execute → no ecs_task_arn
