"""工作流候选运行预检（RunPreflight）的公共只读合同。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore
from unilabos.workflow.run_preflight import build_run_preflight_report
from unilabos.workflow.execution_plan import ExecutionPlanBuilder


def _client(tmp_path):
    """创建隔离的工作流预检应用。"""

    store = WorkflowStore(tmp_path / "workflow_preflight.db")
    return TestClient(create_workflow_app(WorkflowService(store))), store


def test_empty_workflow_preflight_is_read_only_and_runnable_now(tmp_path) -> None:
    """空图预检须返回可展示报告、禁止缓存且不能创建工作流任务。"""

    client, store = _client(tmp_path)
    workflow = client.post(
        "/api/v1/workflows",
        json={"name": "空流程", "tags": [], "meta_data": {}},
    ).json()["data"]

    response = client.get(
        f"/api/v1/workflows/{workflow['uuid']}/run-preflight"
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()["data"]
    assert report["workflow_uuid"] == workflow["uuid"]
    assert report["workflow_revision"] == 1
    assert report["run_mode"] == "normal"
    assert report["status"] == "runnable_now"
    assert report["can_run"] is True
    assert report["summary"]["execution_node_count"] == 0
    assert report["summary"]["blocking_check_count"] == 0
    assert report["summary"]["deferred_check_count"] == 1
    assert len(report["checks"]) == 2
    resource_check = next(
        check for check in report["checks"] if check["type"] == "resource_lock"
    )
    assert resource_check["status"] == "deferred"
    assert resource_check["code"] == "resource_admission_at_dispatch"

    tasks = client.get(
        "/api/v1/workflow-tasks", params={"workflow_uuid": workflow["uuid"]}
    ).json()["data"]
    assert tasks["items"] == []
    store.close()


def test_manual_confirmation_preflight_requires_confirmation(tmp_path) -> None:
    """包含人工确认节点的可执行图须明确提示人工确认而不是误报阻塞。"""

    client, store = _client(tmp_path)
    workflow = client.post(
        "/api/v1/workflows",
        json={"name": "人工复核", "tags": [], "meta_data": {}},
    ).json()["data"]
    node_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    client.put(
        f"/api/v1/workflows/{workflow['uuid']}/graph",
        json={
            "revision": 1,
            "nodes": [
                {
                    "uuid": node_uuid,
                    "name": "确认样品",
                    "type": "manual_confirm",
                    "pose": {},
                    "param": {},
                    "execution_policy": {},
                    "disabled": False,
                    "minimized": False,
                    "meta_data": {},
                }
            ],
            "edges": [],
        },
    )

    report = client.get(
        f"/api/v1/workflows/{workflow['uuid']}/run-preflight"
    ).json()["data"]
    assert report["status"] == "runnable_now"
    assert report["can_run"] is True
    confirmation = next(
        check for check in report["checks"] if check["type"] == "manual_confirmation"
    )
    assert confirmation["status"] == "confirmation_required"
    assert confirmation["node_uuid"] == node_uuid
    store.close()


def test_manual_confirmation_device_continuation_uses_device_preflight(
    monkeypatch,
) -> None:
    """包装真实设备动作的人工确认节点不能跳过当前设备 Gate 4。"""

    node_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    monkeypatch.setattr(
        ExecutionPlanBuilder,
        "build",
        lambda self, graph, run_mode, target_node_uuid: (
            {
                "nodes": [
                    {
                        "uuid": node_uuid,
                        "kind": "manual_confirm",
                        "continues_device_action": True,
                        "device_id": "reactor-a",
                        "material_uuid": "device-material-a",
                        "action_name": "heat",
                    }
                ]
            },
            [],
        ),
    )
    observed: list[dict[str, object]] = []

    report = build_run_preflight_report(
        graph={
            "workflow": {"uuid": "workflow-a", "revision": 1},
            "nodes": [{"uuid": node_uuid, "name": "确认后加热"}],
        },
        run_mode="normal",
        target_node_uuid=None,
        material_resolver=None,
        device_preflight=lambda planned: observed.append(dict(planned))
        or {
            "local_device_id": "reactor-a",
            "material_uuid": "device-material-a",
        },
    )

    assert observed[0]["continues_device_action"] is True
    device_check = next(
        item for item in report["checks"] if item["type"] == "device_selection"
    )
    assert device_check["status"] == "passed"


def test_single_node_preflight_requires_explicit_target(tmp_path) -> None:
    """单节点预检缺少目标身份时须返回稳定输入错误。"""

    client, store = _client(tmp_path)
    workflow = client.post(
        "/api/v1/workflows",
        json={"name": "单节点流程", "tags": [], "meta_data": {}},
    ).json()["data"]
    response = client.get(
        f"/api/v1/workflows/{workflow['uuid']}/run-preflight",
        params={"run_mode": "single_node"},
    )
    assert response.status_code == 200
    assert response.json()["code"] == 1000
    store.close()


def test_preflight_reports_temporary_inventory_unavailability(tmp_path) -> None:
    """当前库存无法准入时必须返回 temporarily_unavailable 而不是 invalid。"""

    client, store = _client(tmp_path)
    workflow = client.post(
        "/api/v1/workflows",
        json={"name": "等待库存", "tags": [], "meta_data": {}},
    ).json()["data"]

    response = client.post(
        f"/api/v1/workflows/{workflow['uuid']}/run-preflight",
        json={
            "input": {},
            "inventory_bindings": [
                {
                    "requirement_key": "sample",
                    "inventory_type": "reagent",
                    "inventory_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "reserved_quantity": 1,
                    "quantity_unit": "mL",
                }
            ],
        },
    )

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["status"] == "temporarily_unavailable"
    assert report["can_run"] is False
    gap = next(
        check for check in report["checks"] if check["type"] == "quantity_inventory"
    )
    assert gap["status"] == "blocked"
    store.close()
