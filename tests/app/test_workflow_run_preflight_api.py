"""工作流候选运行预检（RunPreflight）的公共只读合同。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore


def _client(tmp_path):
    """创建隔离的工作流预检应用。"""

    store = WorkflowStore(tmp_path / "workflow_preflight.db")
    return TestClient(create_workflow_app(WorkflowService(store))), store


def test_empty_workflow_preflight_is_read_only_and_ready(tmp_path) -> None:
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
    assert report["status"] == "ready"
    assert report["can_run"] is True
    assert report["summary"]["execution_node_count"] == 0
    assert report["summary"]["blocking_check_count"] == 0
    assert len(report["checks"]) == 2

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
    assert report["status"] == "requires_confirmation"
    assert report["can_run"] is True
    confirmation = next(
        check for check in report["checks"] if check["type"] == "manual_confirmation"
    )
    assert confirmation["status"] == "confirmation_required"
    assert confirmation["node_uuid"] == node_uuid
    store.close()


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
