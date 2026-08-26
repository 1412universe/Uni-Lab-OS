"""工作流人工干预（Intervention）的持久合同测试。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore

WORKFLOW_UUID = "10000000-0000-4000-8000-000000000301"
TASK_UUID = "20000000-0000-4000-8000-000000000301"
NODE_UUID = "30000000-0000-4000-8000-000000000301"
JOB_UUID = "40000000-0000-4000-8000-000000000301"
CREATED_AT = "2026-08-26T00:00:00Z"


class _InterventionDelivery:
    """模拟本地设备异常决定端口。"""

    def __init__(self) -> None:
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.delivered: list[tuple[str, dict[str, Any]]] = []

    def add_error_decision_required_listener(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> None:
        self.listeners.append(listener)

    def publish(self, report: dict[str, Any]) -> None:
        for listener in tuple(self.listeners):
            listener(dict(report))

    def resolve_error_decision(
        self,
        decision_id: str,
        decision: dict[str, Any],
    ) -> bool:
        self.delivered.append((decision_id, dict(decision)))
        return True


def _seed_running_job(store: WorkflowStore) -> None:
    store.create_workflow(
        workflow_uuid=WORKFLOW_UUID,
        name="干预测试",
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
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, 'running', '{}', '{}',
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
                      'device_action', '{}', 0, 'dispatched', 1, '{}', '{}',
                      '{}', '{}', '[]')
            """,
            (JOB_UUID, CREATED_AT, CREATED_AT, TASK_UUID, NODE_UUID),
        )


def _report(decision_id: str) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "task_id": TASK_UUID,
        "job_id": JOB_UUID,
        "device_id": "reactor-a",
        "action_name": "start",
        "exception_type": "CommunicationError",
        "error_message": "device offline",
        "options": [
            {"action": "retry", "label": "重试"},
            {
                "action": "reset_connection",
                "label": "重置连接",
                "result": {"suc": True, "return_value": {"reset": True}},
            },
        ],
    }


def test_intervention_report_selection_and_replay_use_public_api(tmp_path) -> None:
    """设备报告先持久化，方案选择按修订和幂等键只投递一次。"""

    store = WorkflowStore(tmp_path / "intervention.db")
    try:
        _seed_running_job(store)
        delivery = _InterventionDelivery()
        service = WorkflowService(store)
        service.bind_intervention_delivery(delivery)
        client = TestClient(create_workflow_app(service))

        delivery.publish(_report("50000000-0000-4000-8000-000000000301"))
        opened = client.get(
            "/api/v1/workflow-interventions",
            params={"status": "open", "limit": 10},
        ).json()["data"]
        assert len(opened) == 1
        intervention = opened[0]
        assert intervention["revision"] == 1
        assert [item["id"] for item in intervention["options"]] == [
            "retry",
            "reset_connection",
        ]
        assert store.get_task(TASK_UUID)["control_status"] == "waiting_intervention"

        body = {"revision": 1, "option_id": "reset_connection"}
        first = client.post(
            f"/api/v1/workflow-interventions/{intervention['uuid']}/decisions",
            headers={"Idempotency-Key": "intervention-decision-1"},
            json=body,
        )
        replay = client.post(
            f"/api/v1/workflow-interventions/{intervention['uuid']}/decisions",
            headers={"Idempotency-Key": "intervention-decision-1"},
            json=body,
        )

        assert first.status_code == 201
        assert first.json()["data"]["intervention"]["delivery_status"] == "accepted"
        assert replay.status_code == 200
        assert len(delivery.delivered) == 1
        assert delivery.delivered[0][0] == intervention["edge_command_uuid"]
        assert delivery.delivered[0][1]["action"] == "reset_connection"
        assert store.get_task(TASK_UUID)["control_status"] == "active"
    finally:
        store.close()


def test_new_report_supersedes_previous_revision_and_stale_choice_conflicts(
    tmp_path,
) -> None:
    """同一作业的新异常报告形成新修订，旧修订不能再选择。"""

    store = WorkflowStore(tmp_path / "intervention-revision.db")
    try:
        _seed_running_job(store)
        delivery = _InterventionDelivery()
        service = WorkflowService(store)
        service.bind_intervention_delivery(delivery)
        client = TestClient(create_workflow_app(service))
        delivery.publish(_report("50000000-0000-4000-8000-000000000311"))
        first = service.list_workflow_interventions(status="open", limit=10)[0]
        delivery.publish(_report("50000000-0000-4000-8000-000000000312"))
        second = service.list_workflow_interventions(status="open", limit=10)[0]

        assert second["revision"] == 2
        assert service.get_workflow_intervention(first["uuid"])["status"] == "superseded"
        stale = client.post(
            f"/api/v1/workflow-interventions/{first['uuid']}/decisions",
            headers={"Idempotency-Key": "stale-choice"},
            json={"revision": 1, "option_id": "retry"},
        ).json()
        assert stale["code"] != 0
        assert delivery.delivered == []
    finally:
        store.close()
