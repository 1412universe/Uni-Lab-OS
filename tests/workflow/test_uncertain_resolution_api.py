"""running 作业等待物理对账时的人工处置安全合同。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection
from unilabos.workflow.task_scheduler_bridge import TaskSchedulerBridge

WORKFLOW_UUID = "10000000-0000-4000-8000-000000000401"
TASK_UUID = "20000000-0000-4000-8000-000000000401"
NODE_UUID = "30000000-0000-4000-8000-000000000401"
JOB_UUID = "40000000-0000-4000-8000-000000000401"
COMMAND_UUID = "50000000-0000-4000-8000-000000000401"
CREATED_AT = "2026-08-26T00:00:00Z"


class _ResolutionStore:
    """模拟 Edge 本地命令日志的幂等 UNKNOWN 处置入口。"""

    def __init__(self) -> None:
        self.reason: str | None = None

    def create_unknown_resolution(
        self,
        job_uuid: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        if job_uuid != JOB_UUID:
            raise KeyError(job_uuid)
        if self.reason is not None and self.reason != reason:
            raise ValueError("another UNKNOWN resolution is already pending")
        created = self.reason is None
        self.reason = reason
        return {"command_uuid": COMMAND_UUID, "sequence": 1, "created": created}


class _ResolutionDispatcher:
    def __init__(self) -> None:
        self.store = _ResolutionStore()

    def dispatch(self, payload: dict[str, Any]) -> None:
        raise AssertionError("人工处置不得重新派发原设备动作")


def _seed_uncertain_job(store: WorkflowStore) -> TaskRuntimeProjection:
    """创建一个主状态 running、等待物理对账的工作流节点作业。

    参数：``store`` 是隔离工作流写权威。返回绑定同一存储的运行投影；数据库写入
    异常原样传播，用于验证人工处置不会重新派发原动作。
    """

    store.create_workflow(
        workflow_uuid=WORKFLOW_UUID,
        name="执行未知处置测试",
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
                      'device_action', '{}', 0, 'pending', 1, '{}', '{}',
                      '{}', '{}', '[]')
            """,
            (JOB_UUID, CREATED_AT, CREATED_AT, TASK_UUID, NODE_UUID),
        )
    projection = TaskRuntimeProjection(store)
    projection.project_pre_dispatch(
        task_uuid=TASK_UUID,
        job_uuid=JOB_UUID,
        execution_locks=[
            {"lock_key": "/devices/reactor-a", "scope": "device"},
        ],
    )
    projection.project_dispatch_accepted(JOB_UUID)
    projection.project_execution_attention(JOB_UUID, reason="edge_disconnected")
    return projection


def test_uncertain_resolution_waits_for_edge_proof_before_releasing_lock(tmp_path) -> None:
    """公共请求只创建取消证明命令；Edge 证明前状态与占用保持 uncertain。"""

    store = WorkflowStore(tmp_path / "uncertain-resolution.db")
    bridge = None
    try:
        projection = _seed_uncertain_job(store)
        scheduler = EdgeScheduler(dispatcher=_ResolutionDispatcher())
        bridge = TaskSchedulerBridge(store, scheduler=scheduler)
        client = TestClient(
            create_workflow_app(
                WorkflowService(store, task_scheduler_bridge=bridge)
            )
        )
        body = {
            "resolution": "canceled",
            "reason": "操作员确认设备没有继续执行",
            "device_command_id": f"workflow-node-job:{JOB_UUID}",
        }
        first = client.post(
            f"/api/v1/workflow-node-jobs/{JOB_UUID}/resolve-uncertain",
            json=body,
        )
        replay = client.post(
            f"/api/v1/workflow-node-jobs/{JOB_UUID}/resolve-uncertain",
            json=body,
        )

        assert first.status_code == 202
        assert first.json()["data"]["created"] is True
        assert replay.status_code == 202
        assert replay.json()["data"]["created"] is False
        assert store.get_job(JOB_UUID)["status"] == "running"
        assert store.get_job(JOB_UUID)["uncertainty_reason"] == "edge_disconnected"
        assert {item["state"] for item in projection.list_execution_locks(JOB_UUID)} == {
            "uncertain"
        }

        # 该调用模拟 Local Edge 已持久提交 job.unknown_resolution_committed 后的
        # 投影；只有此时才能终结作业并释放执行占用。
        projection.project_job_finished(
            job_uuid=JOB_UUID,
            scheduler_state="canceled",
            return_info={},
            error_info=[],
        )
        assert store.get_job(JOB_UUID)["status"] == "canceled"
        assert store.get_task(TASK_UUID)["status"] == "canceled"
        assert {item["state"] for item in projection.list_execution_locks(JOB_UUID)} == {
            "released"
        }
    finally:
        if bridge is not None:
            bridge.close()
        store.close()


def test_uncertain_resolution_rejects_unprovable_success(tmp_path) -> None:
    """缺少真实结果与库存结算时不得把未知执行人工伪造成成功。"""

    store = WorkflowStore(tmp_path / "uncertain-success.db")
    bridge = None
    try:
        _seed_uncertain_job(store)
        bridge = TaskSchedulerBridge(
            store,
            scheduler=EdgeScheduler(dispatcher=_ResolutionDispatcher()),
        )
        client = TestClient(
            create_workflow_app(
                WorkflowService(store, task_scheduler_bridge=bridge)
            )
        )
        response = client.post(
            f"/api/v1/workflow-node-jobs/{JOB_UUID}/resolve-uncertain",
            json={"resolution": "succeeded", "reason": "我认为已经成功"},
        ).json()
        assert response["code"] != 0
        assert store.get_job(JOB_UUID)["status"] == "running"
    finally:
        if bridge is not None:
            bridge.close()
        store.close()


def test_restart_failed_job_can_request_stop_proof(tmp_path) -> None:
    """执行进程重启把主状态置失败后，仍可请求 Edge 提交停止证明。"""

    store = WorkflowStore(tmp_path / "restart-failed-resolution.db")
    bridge = None
    try:
        projection = _seed_uncertain_job(store)
        projection.project_execution_process_restarted(TASK_UUID)
        bridge = TaskSchedulerBridge(
            store,
            scheduler=EdgeScheduler(dispatcher=_ResolutionDispatcher()),
        )
        client = TestClient(
            create_workflow_app(
                WorkflowService(store, task_scheduler_bridge=bridge)
            )
        )

        response = client.post(
            f"/api/v1/workflow-node-jobs/{JOB_UUID}/resolve-uncertain",
            json={
                "resolution": "canceled",
                "reason": "执行进程重启，确认设备已经停止",
            },
        )

        assert response.status_code == 202
        assert response.json()["data"]["pending_edge_confirmation"] is True
        assert store.get_job(JOB_UUID)["status"] == "failed"
        assert {item["state"] for item in projection.list_execution_locks(JOB_UUID)} == {
            "uncertain"
        }
    finally:
        if bridge is not None:
            bridge.close()
        store.close()


def test_failed_job_stop_proof_releases_no_inventory_change_claim(tmp_path) -> None:
    """无库存变化的失败作业拿到停止证明后释放占用，重放保持幂等。"""

    store = WorkflowStore(tmp_path / "failed-stop-proof.db")
    try:
        projection = _seed_uncertain_job(store)
        projection.project_execution_process_restarted(TASK_UUID)

        first = projection.project_failed_job_execution_stopped(
            JOB_UUID,
            outcome="canceled",
            return_info={"stopped": True},
            error_info=[],
        )
        replay = projection.project_failed_job_execution_stopped(
            JOB_UUID,
            outcome="canceled",
            return_info={"stopped": True},
            error_info=[],
        )

        assert first["task"]["status"] == "failed"
        assert replay["task"]["status"] == "failed"
        assert store.get_job(JOB_UUID).get("uncertainty_reason") is None
        assert {item["state"] for item in projection.list_execution_locks(JOB_UUID)} == {
            "released"
        }
    finally:
        store.close()


def test_failed_transfer_waits_for_inventory_reconciliation(tmp_path) -> None:
    """失败转运即使设备已停止，也要等实际物料位置入账后才释放占用。"""

    store = WorkflowStore(tmp_path / "failed-transfer-settlement.db")
    try:
        projection = _seed_uncertain_job(store)
        with store.transaction() as connection:
            connection.execute(
                """
                UPDATE workflow_node_job
                SET expected_change_set=?
                WHERE uuid=?
                """,
                (
                    '{"kind":"material_transfer","material_uuid":"material-a",'
                    '"source_site_uuid":"site-a","target_site_uuid":"site-b"}',
                    JOB_UUID,
                ),
            )
        projection.project_execution_process_restarted(TASK_UUID)

        projection.project_failed_job_execution_stopped(
            JOB_UUID,
            outcome="failed",
            return_info={"stopped": True},
            error_info=[{"code": "execution_process_restarted"}],
        )

        assert store.get_job(JOB_UUID)["uncertainty_reason"] == (
            "material_transfer_inventory_reconciliation_required"
        )
        assert {item["state"] for item in projection.list_execution_locks(JOB_UUID)} == {
            "uncertain"
        }

        projection.project_failed_job_inventory_reconciled(
            JOB_UUID,
            actual_change_set={
                "kind": "material_transfer",
                "material_uuid": "material-a",
                "target_owner_material_uuid": "device-b",
                "target_site_uuid": "site-b",
            },
            reason="现场确认物料已在目标库位",
        )

        assert store.get_job(JOB_UUID)["status"] == "failed"
        assert store.get_job(JOB_UUID).get("uncertainty_reason") is None
        assert {item["state"] for item in projection.list_execution_locks(JOB_UUID)} == {
            "released"
        }
    finally:
        store.close()
