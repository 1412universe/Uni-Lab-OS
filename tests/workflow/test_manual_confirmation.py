"""人工确认（ManualConfirmation）的公共合同与调度继续行为。"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowConflict, WorkflowService
from unilabos.workflow.store import WorkflowStore
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection
from unilabos.workflow.task_scheduler_bridge import TaskSchedulerBridge

WORKFLOW_UUID = "10000000-0000-4000-8000-000000000201"
TASK_UUID = "20000000-0000-4000-8000-000000000201"
NODE_UUID = "30000000-0000-4000-8000-000000000201"
JOB_UUID = "40000000-0000-4000-8000-000000000201"
CREATED_AT = "2026-08-26T00:00:00Z"


class _DecisionBridge:
    """记录公共服务越过调度边界的人工决定。"""

    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []

    def submit(self, task: dict[str, Any]) -> dict[str, Any]:
        return task

    def close(self) -> None:
        return None

    def step(
        self,
        task_uuid: str,
        *,
        target_node_uuid: str | None = None,
    ) -> dict[str, Any]:
        return {"task_uuid": task_uuid, "target_node_uuid": target_node_uuid}

    def decide_manual_confirmation(
        self,
        job_uuid: str,
        *,
        approved: bool,
        param: dict[str, Any] | None,
    ) -> dict[str, Any]:
        decision = {"job_uuid": job_uuid, "approved": approved, "param": param}
        self.decisions.append(decision)
        return decision


def _seed_manual_job(store: WorkflowStore) -> None:
    """创建等待人工确认的标准任务与作业。"""

    store.create_workflow(
        workflow_uuid=WORKFLOW_UUID,
        name="人工确认测试",
        tags=[],
        description=None,
        meta_data={},
    )
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO workflow_task(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_uuid, status, workflow_snapshot,
                execution_plan, run_mode, target_node_uuid, control_status,
                cleanup_status, trace_context, input, output, error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, 'pending', '{}', '{}',
                      'normal', NULL, 'active', 'none', '{}', '{}', '{}', '[]')
            """,
            (TASK_UUID, CREATED_AT, CREATED_AT, WORKFLOW_UUID),
        )
        connection.execute(
            """
            INSERT INTO workflow_node_job(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_uuid,
                feedback_sequence, topological_index, executor_kind,
                execution_policy, execution_timeout_seconds, status,
                attempt, param, feedback_data, return_info, control_data,
                error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, 0, 0,
                      'manual_confirm', '{}', 0, 'pending', 1, ?, '{}',
                      '{}', '{}', '[]')
            """,
            (
                JOB_UUID,
                CREATED_AT,
                CREATED_AT,
                TASK_UUID,
                NODE_UUID,
                '{"prompt":"请检查容器","timeout_seconds":60}',
            ),
        )


def test_manual_confirmation_public_api_is_queryable_and_idempotent(tmp_path) -> None:
    """确认事实可查询；同键重放安全，另一决定冲突。"""

    store = WorkflowStore(tmp_path / "manual-confirmation.db")
    try:
        _seed_manual_job(store)
        TaskRuntimeProjection(store).project_pre_dispatch(
            task_uuid=TASK_UUID,
            job_uuid=JOB_UUID,
        )
        bridge = _DecisionBridge()
        client = TestClient(
            create_workflow_app(
                WorkflowService(store, task_scheduler_bridge=bridge)
            )
        )

        confirmations = client.get(
            f"/api/v1/workflow-tasks/{TASK_UUID}/manual-confirmations"
        ).json()["data"]
        assert len(confirmations) == 1
        confirmation = confirmations[0]
        assert confirmation["status"] == "pending"
        assert confirmation["param"]["prompt"] == "请检查容器"

        body = {
            "action": "approve",
            "confirmed_by": "operator-1",
            "comment": "检查通过",
            "idempotency_key": "decision-1",
            "param": {"prompt": "请检查容器", "timeout_seconds": 60},
        }
        first = client.post(
            "/api/v1/workflow-manual-confirmations/"
            f"{confirmation['uuid']}/decision",
            json=body,
        )
        replay = client.post(
            "/api/v1/workflow-manual-confirmations/"
            f"{confirmation['uuid']}/decision",
            json=body,
        )

        assert first.status_code == 200
        assert replay.status_code == 200
        assert replay.json()["data"]["status"] == "approved"
        assert bridge.decisions == [
            {"job_uuid": JOB_UUID, "approved": True, "param": body["param"]},
            {"job_uuid": JOB_UUID, "approved": True, "param": body["param"]},
        ]
        conflict = client.post(
            "/api/v1/workflow-manual-confirmations/"
            f"{confirmation['uuid']}/decision",
            json={
                "action": "reject",
                "confirmed_by": "operator-2",
                "idempotency_key": "decision-2",
            },
        )
        assert conflict.status_code == 200
        assert conflict.json()["code"] != 0
    finally:
        store.close()


def test_scheduler_approval_dispatches_same_manual_job_once() -> None:
    """批准人工确认后，同一 Job 才越过设备执行边界且不会重复下发。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher)
    spec = WorkflowSpec(
        workflow_id="wf-manual-continuation",
        nodes=[
            WorkflowNode(
                id="manual-node",
                device_id="reactor-a",
                action_name="start",
                action_type="UniLabJsonCommand",
                param={"speed": 120},
                node_type="manual_confirm",
                manual_continues_device_action=True,
            )
        ],
    )
    submitted = scheduler.submit_workflow(spec)
    job_id = submitted["dispatched"][0]["job_id"]
    assert dispatcher.dispatched == []

    first = scheduler.resolve_manual_confirmation(job_id, approved=True)
    replay = scheduler.resolve_manual_confirmation(job_id, approved=True)

    assert first["dispatched"][0]["job_id"] == job_id
    assert replay["dispatched"] == []
    assert [payload["job_id"] for payload in dispatcher.dispatched] == [job_id]
    assert dispatcher.dispatched[0]["action_args"] == {"speed": 120}


def test_scheduler_rejects_manual_confirmation_without_physical_dispatch() -> None:
    """拒绝人工确认必须明确失败，且不得触发物理动作。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher)
    submitted = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-manual-reject",
            nodes=[
                WorkflowNode(
                    id="manual-node",
                    device_id="reactor-a",
                    action_name="start",
                    node_type="manual_confirm",
                )
            ],
        )
    )
    result = scheduler.resolve_manual_confirmation(
        submitted["dispatched"][0]["job_id"],
        approved=False,
    )

    assert dispatcher.dispatched == []
    assert result["workflow_state"] == "failed"


def test_reject_request_cannot_override_frozen_parameters(tmp_path) -> None:
    """拒绝决定不能携带新的执行参数。"""

    store = WorkflowStore(tmp_path / "manual-reject-param.db")
    try:
        _seed_manual_job(store)
        TaskRuntimeProjection(store).project_pre_dispatch(
            task_uuid=TASK_UUID,
            job_uuid=JOB_UUID,
        )
        service = WorkflowService(store)
        confirmation = service.list_task_manual_confirmations(TASK_UUID)[0]
        with pytest.raises(WorkflowConflict):
            service.decide_manual_confirmation(
                confirmation["uuid"],
                action="reject",
                confirmed_by="operator-1",
                comment=None,
                idempotency_key="reject-1",
                param={"unexpected": True},
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    ("scheduler_state", "confirmation_status"),
    [("canceled", "canceled"), ("failed", "timed_out")],
)
def test_terminal_job_closes_pending_manual_confirmation(
    tmp_path,
    scheduler_state: str,
    confirmation_status: str,
) -> None:
    """Job 终结与人工确认必须在同一事务收敛，不能遗留 pending 记录。"""

    store = WorkflowStore(tmp_path / f"manual-{confirmation_status}.db")
    try:
        _seed_manual_job(store)
        projection = TaskRuntimeProjection(store)
        projection.project_pre_dispatch(task_uuid=TASK_UUID, job_uuid=JOB_UUID)

        projection.project_job_finished(
            job_uuid=JOB_UUID,
            scheduler_state=scheduler_state,
            error_info=(
                [{"code": "manual_confirmation_timed_out"}]
                if confirmation_status == "timed_out"
                else []
            ),
            manual_confirmation_status=confirmation_status,
        )

        confirmation = WorkflowService(store).list_task_manual_confirmations(
            TASK_UUID
        )[0]
        assert confirmation["status"] == confirmation_status
        assert "decided_at" in confirmation
    finally:
        store.close()


def test_scheduler_restart_fails_pending_manual_confirmation_task(
    tmp_path,
) -> None:
    """调度进程重启后运行中任务整体失败且不再恢复人工等待。

    参数：``tmp_path`` 提供隔离工作流库。返回：无；断言运行中的工作流任务
    （WorkflowTask）进入 ``failed``，未越过物理边界的人工确认作业进入
    ``skipped``，对应确认进入 ``canceled``。异常：任何恢复后继续推进都会使
    断言失败。
    """

    store = WorkflowStore(tmp_path / "manual-restart.db")
    plan = {
        "version": 1,
        "run_mode": "normal",
        "target_node_uuid": None,
        "nodes": [
            {
                "uuid": NODE_UUID,
                "kind": "manual_confirm",
                "device_id": "operator",
                "action_name": "confirm",
                "action_type": "manual_confirm",
                "param": {"prompt": "请确认", "timeout_seconds": 60},
                "param_schema": None,
                "material_requirements": [],
            }
        ],
        "handles": [],
        "edges": [],
    }
    try:
        _seed_manual_job(store)
        with store.transaction() as connection:
            connection.execute(
                "UPDATE workflow_task SET execution_plan = ? WHERE uuid = ?",
                (json.dumps(plan), TASK_UUID),
            )
        projection = TaskRuntimeProjection(store)
        projection.project_pre_dispatch(task_uuid=TASK_UUID, job_uuid=JOB_UUID)
        assert store.get_job(JOB_UUID)["status"] == "dispatched"

        bridge = TaskSchedulerBridge(
            store,
            scheduler=EdgeScheduler(dispatcher=RecordingDispatcher()),
        )
        try:
            recovered = bridge.recover_active_tasks()
            assert [item["task"]["uuid"] for item in recovered] == [TASK_UUID]
            assert store.get_job(JOB_UUID)["status"] == "skipped"
            confirmation = WorkflowService(store).list_task_manual_confirmations(
                TASK_UUID
            )[0]
            assert confirmation["status"] == "canceled"
            task = store.get_task(TASK_UUID)
            assert task["status"] == "failed"
            assert task["cleanup_status"] == "settled"
            assert task["control_status"] == "active"
        finally:
            bridge.close()
    finally:
        store.close()


def test_manual_confirmation_deadline_fails_job_without_operator_request(
    tmp_path,
) -> None:
    """人工确认截止时间到达后应由本地调度恢复链自动收敛。"""

    store = WorkflowStore(tmp_path / "manual-deadline.db")
    store.create_workflow(
        workflow_uuid=WORKFLOW_UUID,
        name="人工确认超时测试",
        tags=[],
        description=None,
        meta_data={},
    )
    plan = {
        "version": 1,
        "run_mode": "normal",
        "target_node_uuid": None,
        "nodes": [
            {
                "uuid": NODE_UUID,
                "kind": "manual_confirm",
                "device_id": "operator",
                "action_name": "confirm",
                "action_type": "manual_confirm",
                "param": {"prompt": "请确认", "timeout_seconds": 0.03},
                "param_schema": None,
                "material_requirements": [],
            }
        ],
        "handles": [],
        "edges": [],
    }
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO workflow_task(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_uuid, status, workflow_snapshot,
                execution_plan, run_mode, target_node_uuid, control_status,
                cleanup_status, trace_context, input, output, error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, 'pending', '{}', ?,
                      'normal', NULL, 'active', 'none', '{}', '{}', '{}', '[]')
            """,
            (TASK_UUID, CREATED_AT, CREATED_AT, WORKFLOW_UUID, json.dumps(plan)),
        )
        connection.execute(
            """
            INSERT INTO workflow_node_job(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_uuid,
                feedback_sequence, topological_index, executor_kind,
                execution_policy, execution_timeout_seconds, status,
                attempt, param, feedback_data, return_info, control_data,
                error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, 0, 0,
                      'manual_confirm', '{}', 0, 'pending', 1, ?, '{}',
                      '{}', '{}', '[]')
            """,
            (
                JOB_UUID,
                CREATED_AT,
                CREATED_AT,
                TASK_UUID,
                NODE_UUID,
                json.dumps(plan["nodes"][0]["param"]),
            ),
        )
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    bridge = TaskSchedulerBridge(store, scheduler=scheduler)
    try:
        bridge.submit(store.get_task(TASK_UUID))
        deadline = time.monotonic() + 1
        while store.get_job(JOB_UUID)["status"] != "failed":
            if time.monotonic() >= deadline:
                pytest.fail("人工确认截止后 Job 未自动失败")
            time.sleep(0.01)

        confirmation = WorkflowService(store).list_task_manual_confirmations(
            TASK_UUID
        )[0]
        assert confirmation["status"] == "timed_out"
        assert store.get_task(TASK_UUID)["status"] == "failed"
    finally:
        bridge.close()
        store.close()
