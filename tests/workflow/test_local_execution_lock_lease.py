"""Edge 本地模式持久执行占用、等待与重启不确定性合同。"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.workflow.test_f05_task_scheduler_bridge import (
    JOB_UUID,
    MATERIAL_UUID,
    TASK_UUID,
    WORKFLOW_UUID,
    _seed_task,
)
from unilabos.app.scheduler.dispatch import CancelDispatchState, RecordingDispatcher
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.workflow.store import WorkflowStore
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection
from unilabos.workflow.task_scheduler_bridge import TaskSchedulerBridge

SECOND_TASK_UUID = "21000000-0000-4000-8000-000000000002"
SECOND_NODE_UUID = "31000000-0000-4000-8000-000000000002"
SECOND_JOB_UUID = "41000000-0000-4000-8000-000000000002"
SITE_UUID = "71000000-0000-4000-8000-000000000001"
_CREATED_AT = "2026-08-05T00:00:01Z"


class _AcceptingCancelDispatcher(RecordingDispatcher):
    """记录派发并同步确认本地执行器已接受取消。"""

    def cancel(self, job_id, on_accepted):
        """确认取消已受理，但不生成设备终态。"""

        del job_id
        on_accepted(True)
        return CancelDispatchState.REQUESTED


class _SilentCancelDispatcher(RecordingDispatcher):
    """记录取消请求，但模拟执行器永不返回受理结果。"""

    def cancel(self, job_id, on_accepted):
        """保持取消悬挂，用于验证本地 ACK 截止时间。"""

        del job_id, on_accepted
        return CancelDispatchState.REQUESTED


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[WorkflowStore]:
    """创建隔离本地工作流权威。"""

    opened = WorkflowStore(tmp_path / "workflow_history.db")
    try:
        yield opened
    finally:
        opened.close()


def _seed_second_task(
    store: WorkflowStore,
    *,
    device_id: str = "reactor-b",
) -> None:
    """追加一个竞争同一物料子库位的独立任务与作业。"""

    execution_plan = {
        "version": 1,
        "run_mode": "normal",
        "target_node_uuid": None,
        "nodes": [
            {
                "uuid": SECOND_NODE_UUID,
                "kind": "device_action",
                "device_id": device_id,
                "action_name": "place",
                "action_type": "UniLabJsonCommand",
                "param": {},
                "param_schema": {
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        }
                    },
                    "additionalProperties": False,
                },
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
            (
                SECOND_TASK_UUID,
                _CREATED_AT,
                _CREATED_AT,
                WORKFLOW_UUID,
                json.dumps(execution_plan),
            ),
        )
        connection.execute(
            """
            INSERT INTO workflow_node_job(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_uuid,
                feedback_sequence, topological_index, executor_kind,
                execution_policy, execution_timeout_seconds, status, attempt,
                param, feedback_data, return_info, control_data, error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, 0, 0,
                      'device_action', '{}', 0, 'pending', 1, '{}', '{}',
                      '{}', '{}', '[]')
            """,
            (
                SECOND_JOB_UUID,
                _CREATED_AT,
                _CREATED_AT,
                SECOND_TASK_UUID,
                SECOND_NODE_UUID,
            ),
        )


def test_material_parent_lock_blocks_child_site_until_explicit_result(
    store: WorkflowStore,
) -> None:
    """整物料占用跨任务阻止其子库位，明确结果后按原等待身份重试。"""

    _seed_task(store, with_material=False)
    _seed_second_task(store)
    projection = TaskRuntimeProjection(store)
    whole_material = f"material/{MATERIAL_UUID}/exclusive"
    child_site = f"material/{MATERIAL_UUID}/site/{SITE_UUID}/exclusive"

    first = projection.project_pre_dispatch(
        task_uuid=TASK_UUID,
        job_uuid=JOB_UUID,
        execution_locks=[
            {"lock_key": "/devices/reactor-a", "scope": "device"},
            {
                "lock_key": whole_material,
                "scope": "material",
                "material_uuid": MATERIAL_UUID,
            },
        ],
    )
    assert first["jobs"][0]["status"] == "dispatched"
    projection.project_dispatch_accepted(JOB_UUID)

    blocked = projection.project_pre_dispatch(
        task_uuid=SECOND_TASK_UUID,
        job_uuid=SECOND_JOB_UUID,
        execution_locks=[
            {"lock_key": "/devices/reactor-b", "scope": "device"},
            {
                "lock_key": child_site,
                "scope": "material_site",
                "material_uuid": MATERIAL_UUID,
                "site_uuid": SITE_UUID,
            },
        ],
    )
    blocked_job = blocked["jobs"][0]
    assert blocked_job["status"] == "pending"
    assert blocked_job["wait_reason"]["code"] == "operation_lease"
    assert blocked_job["wait_reason"]["blocking_job_uuid"] == JOB_UUID
    with store.transaction() as connection:
        waiting_count = connection.execute(
            "SELECT COUNT(*) FROM execution_lock_waiter WHERE state = 'waiting'"
        ).fetchone()[0]
    assert waiting_count == 2

    projection.project_job_finished(job_uuid=JOB_UUID, scheduler_state="success")
    released = projection.list_execution_locks(JOB_UUID)
    assert {lease["state"] for lease in released} == {"released"}

    admitted = projection.project_pre_dispatch(
        task_uuid=SECOND_TASK_UUID,
        job_uuid=SECOND_JOB_UUID,
        execution_locks=[
            {"lock_key": "/devices/reactor-b", "scope": "device"},
            {
                "lock_key": child_site,
                "scope": "material_site",
                "material_uuid": MATERIAL_UUID,
                "site_uuid": SITE_UUID,
            },
        ],
    )
    assert admitted["jobs"][0]["status"] == "dispatched"
    assert admitted["jobs"][0]["wait_reason"] == {}


def test_restart_marks_inflight_job_unknown_and_keeps_lease(
    store: WorkflowStore,
) -> None:
    """重启不能盲目重放已接受作业，必须保留锁并等待对账。"""

    _seed_task(store, with_material=False)
    projection = TaskRuntimeProjection(store)
    projection.project_pre_dispatch(
        task_uuid=TASK_UUID,
        job_uuid=JOB_UUID,
        execution_locks=[
            {"lock_key": "/devices/reactor-a", "scope": "device"},
        ],
    )
    projection.project_dispatch_accepted(JOB_UUID)

    bridge = TaskSchedulerBridge(
        store,
        scheduler=EdgeScheduler(dispatcher=RecordingDispatcher()),
    )
    try:
        recovered = bridge.recover_active_tasks()
    finally:
        bridge.close()

    assert [aggregate["task"]["uuid"] for aggregate in recovered] == [TASK_UUID]
    assert store.get_job(JOB_UUID)["status"] == "execution_unknown"
    task = store.get_task(TASK_UUID)
    assert task["control_status"] == "waiting_reconciliation"
    assert task["cleanup_status"] == "requires_attention"
    assert {lease["state"] for lease in projection.list_execution_locks(JOB_UUID)} == {
        "uncertain"
    }


def test_restart_projects_edge_committed_outcome_before_marking_job_unknown(
    store: WorkflowStore,
) -> None:
    """Edge 已落盘结果必须先重放，不能被重启扫描误判为 UNKNOWN。"""

    _seed_task(store, with_material=False)
    projection = TaskRuntimeProjection(store)
    projection.project_pre_dispatch(
        task_uuid=TASK_UUID,
        job_uuid=JOB_UUID,
        execution_locks=[{"lock_key": "/devices/reactor-a", "scope": "device"}],
    )
    projection.project_dispatch_accepted(JOB_UUID)
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())

    def replay(*, feedback_listener, finished_listener):
        del feedback_listener
        finished_listener(JOB_UUID, True, {"completed": True}, "normal")
        return {"feedback": 0, "outcomes": 1}

    scheduler.replay_persisted_edge_projections = replay  # type: ignore[method-assign]
    bridge = TaskSchedulerBridge(store, scheduler=scheduler)
    try:
        recovered = bridge.recover_active_tasks()
    finally:
        bridge.close()

    assert recovered == []
    assert store.get_job(JOB_UUID)["status"] == "succeeded"
    assert store.get_task(TASK_UUID)["status"] == "succeeded"
    assert {lease["state"] for lease in projection.list_execution_locks(JOB_UUID)} == {
        "released"
    }


def test_live_device_conflict_persists_waiter_before_retry(
    store: WorkflowStore,
) -> None:
    """同进程设备忙也必须写等待原因，不能只依赖易失内存锁。"""

    first_task = _seed_task(store, with_material=False)
    _seed_second_task(store, device_id="reactor-a")
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    bridge = TaskSchedulerBridge(store, scheduler=scheduler)
    try:
        bridge.submit(first_task)
        blocked = bridge.submit(store.get_task(SECOND_TASK_UUID))
    finally:
        bridge.close()

    blocked_job = next(job for job in blocked["jobs"] if job["uuid"] == SECOND_JOB_UUID)
    assert blocked_job["status"] == "pending"
    assert blocked_job["wait_reason"]["code"] == "operation_lease"
    assert blocked_job["wait_reason"]["blocking_job_uuid"] == JOB_UUID
    with store.transaction() as connection:
        waiting = connection.execute(
            "SELECT lock_key, state FROM execution_lock_waiter "
            "WHERE workflow_node_job_uuid = ?",
            (SECOND_JOB_UUID,),
        ).fetchall()
    assert [(row["lock_key"], row["state"]) for row in waiting] == [
        ("/devices/reactor-a", "waiting")
    ]


def test_local_cancel_keeps_execution_lock_until_device_terminal(
    store: WorkflowStore,
) -> None:
    """取消受理不能提前释放锁，设备取消终态到达后才完成结算。"""

    task = _seed_task(store, with_material=False)
    dispatcher = _AcceptingCancelDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher)
    bridge = TaskSchedulerBridge(store, scheduler=scheduler)
    try:
        bridge.submit(task)
        requested = bridge.cancel(
            TASK_UUID,
            command_uuid="51000000-0000-4000-8000-000000000001",
        )
        requested_job = next(
            job for job in requested["jobs"] if job["uuid"] == JOB_UUID
        )
        assert requested["task"]["status"] == "canceling"
        assert requested_job["status"] == "cancel_requested"
        assert requested_job["cancel_accepted_at"] is not None
        assert requested_job.get("cancel_ack_deadline_at") is None
        assert requested_job["cancel_complete_deadline_at"] is not None
        assert {
            lease["state"] for lease in bridge._projection.list_execution_locks(JOB_UUID)
        } == {"running"}

        scheduler.on_job_finished(
            JOB_UUID,
            False,
            {"stopped": True},
            "canceled",
        )
        settled = store.get_task(TASK_UUID)
        assert settled["status"] == "canceled"
        assert settled["cleanup_status"] == "settled"
        assert store.get_job(JOB_UUID)["status"] == "canceled"
        assert {
            lease["state"] for lease in bridge._projection.list_execution_locks(JOB_UUID)
        } == {"released"}
    finally:
        bridge.close()


def test_local_cancel_acceptance_timeout_becomes_execution_unknown(
    store: WorkflowStore,
) -> None:
    """执行器不确认取消时冻结为 execution_unknown 并保留不确定占用。"""

    task = _seed_task(store, with_material=False)
    scheduler = EdgeScheduler(dispatcher=_SilentCancelDispatcher())
    bridge = TaskSchedulerBridge(
        store,
        scheduler=scheduler,
        cancel_ack_timeout_seconds=0.02,
        cancel_complete_timeout_seconds=0.1,
    )
    try:
        bridge.submit(task)
        bridge.cancel(
            TASK_UUID,
            command_uuid="51000000-0000-4000-8000-000000000002",
        )
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if store.get_job(JOB_UUID)["status"] == "execution_unknown":
                break
            time.sleep(0.01)
        assert store.get_job(JOB_UUID)["status"] == "execution_unknown"
        assert store.get_task(TASK_UUID)["cleanup_status"] == "requires_attention"
        assert {
            lease["state"] for lease in bridge._projection.list_execution_locks(JOB_UUID)
        } == {"uncertain"}
    finally:
        bridge.close()


def test_local_cancel_completion_timeout_keeps_uncertain_lock(
    store: WorkflowStore,
) -> None:
    """取消已受理但设备无终态时进入 execution_unknown，不能提前复用设备。"""

    task = _seed_task(store, with_material=False)
    scheduler = EdgeScheduler(dispatcher=_AcceptingCancelDispatcher())
    bridge = TaskSchedulerBridge(
        store,
        scheduler=scheduler,
        cancel_ack_timeout_seconds=0.01,
        cancel_complete_timeout_seconds=0.03,
    )
    try:
        bridge.submit(task)
        bridge.cancel(
            TASK_UUID,
            command_uuid="51000000-0000-4000-8000-000000000003",
        )
        assert store.get_job(JOB_UUID).get("cancel_accepted_at") is not None
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if store.get_job(JOB_UUID)["status"] == "execution_unknown":
                break
            time.sleep(0.01)
        job = store.get_job(JOB_UUID)
        assert job["status"] == "execution_unknown"
        assert job["uncertainty_reason"] == "local_cancel_completion_timeout"
        assert {
            lease["state"] for lease in bridge._projection.list_execution_locks(JOB_UUID)
        } == {"uncertain"}
    finally:
        bridge.close()


def test_restart_during_local_cancel_freezes_execution_as_unknown(
    store: WorkflowStore,
) -> None:
    """取消等待设备终态时进程重启，不能重放作业或释放持久执行占用。"""

    task = _seed_task(store, with_material=False)
    first_bridge = TaskSchedulerBridge(
        store,
        scheduler=EdgeScheduler(dispatcher=_SilentCancelDispatcher()),
        cancel_ack_timeout_seconds=30.0,
        cancel_complete_timeout_seconds=60.0,
    )
    try:
        first_bridge.submit(task)
        first_bridge.cancel(
            TASK_UUID,
            command_uuid="51000000-0000-4000-8000-000000000004",
        )
        assert store.get_job(JOB_UUID)["status"] == "cancel_requested"
    finally:
        first_bridge.close()

    recovered_bridge = TaskSchedulerBridge(
        store,
        scheduler=EdgeScheduler(dispatcher=RecordingDispatcher()),
    )
    try:
        recovered = recovered_bridge.recover_active_tasks()
    finally:
        recovered_bridge.close()

    assert [aggregate["task"]["uuid"] for aggregate in recovered] == [TASK_UUID]
    job = store.get_job(JOB_UUID)
    assert job["status"] == "execution_unknown"
    assert job["uncertainty_reason"] == "local_cancel_restart_missing_terminal"
    assert store.get_task(TASK_UUID)["cleanup_status"] == "requires_attention"
    assert {
        lease["state"]
        for lease in recovered_bridge._projection.list_execution_locks(JOB_UUID)
    } == {"uncertain"}
