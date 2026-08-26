"""工作流定义增量编辑公共合同。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore


def _client(tmp_path):
    """创建隔离的 Local 工作流定义服务。"""

    store = WorkflowStore(tmp_path / "workflow_definition_edit.db")
    return TestClient(create_workflow_app(WorkflowService(store))), store


def _workflow(client: TestClient, name: str = "增量编辑") -> dict:
    """创建一份空工作流并返回公共投影。"""

    return client.post(
        "/api/v1/workflows",
        json={"name": name, "tags": [], "meta_data": {}},
    ).json()["data"]


def _compute_node(name: str) -> dict:
    """返回不依赖设备模板的计算节点创建参数。"""

    return {
        "name": name,
        "type": "compute",
        "pose": {"x": 10, "y": 20},
        "param": {"expression": "1 + 1"},
        "execution_policy": {},
        "disabled": False,
        "minimized": False,
        "meta_data": {},
    }


def test_node_crud_duplicate_and_page_contract(tmp_path) -> None:
    """节点增删改查和复制必须共享整图修订并使用 Backend 分页形状。"""

    client, store = _client(tmp_path)
    workflow = _workflow(client)
    workflow_uuid = workflow["uuid"]

    created_response = client.post(
        f"/api/v1/workflows/{workflow_uuid}/nodes",
        json=_compute_node("计算 A"),
    )
    assert created_response.status_code == 201
    created = created_response.json()["data"]
    assert created["workflow_uuid"] == workflow_uuid
    assert created["name"] == "计算 A"

    page = client.get(
        f"/api/v1/workflows/{workflow_uuid}/nodes",
        params={"page": 1, "page_size": 1},
    ).json()["data"]
    assert set(page) == {"items", "total", "page", "page_size"}
    assert page["total"] == 1
    assert page["items"][0]["uuid"] == created["uuid"]

    detail = client.get(
        f"/api/v1/workflow-nodes/{created['uuid']}"
    ).json()["data"]
    assert detail["uuid"] == created["uuid"]

    patched = client.patch(
        f"/api/v1/workflow-nodes/{created['uuid']}",
        json={"name": "计算 A（已修改）", "description": "用于校验"},
    ).json()["data"]
    assert patched["name"] == "计算 A（已修改）"
    assert patched["description"] == "用于校验"

    duplicate_response = client.post(
        f"/api/v1/workflow-nodes/{created['uuid']}/duplicate",
        json={},
    )
    assert duplicate_response.status_code == 201
    duplicate = duplicate_response.json()["data"]
    assert duplicate["uuid"] != created["uuid"]
    assert duplicate["name"] == "计算 A（已修改） copy"

    deleted = client.post(
        f"/api/v1/workflows/{workflow_uuid}/batch-delete",
        json={"node_uuids": [duplicate["uuid"]], "edge_uuids": []},
    ).json()["data"]
    assert [node["uuid"] for node in deleted["nodes"]] == [created["uuid"]]

    response = client.delete(f"/api/v1/workflow-nodes/{created['uuid']}")
    assert response.status_code == 200
    assert response.json() == {"code": 0}
    assert client.get(
        f"/api/v1/workflows/{workflow_uuid}/graph"
    ).json()["data"]["nodes"] == []
    assert store.get_workflow(workflow_uuid)["revision"] == 6
    store.close()


def test_duplicate_workflow_remaps_parent_identity_and_is_independent(tmp_path) -> None:
    """复制工作流须重建节点身份和父子引用，后续编辑不能影响来源图。"""

    client, store = _client(tmp_path)
    source = _workflow(client, "来源流程")
    source_uuid = source["uuid"]
    parent = client.post(
        f"/api/v1/workflows/{source_uuid}/nodes",
        json=_compute_node("父节点"),
    ).json()["data"]
    child_body = _compute_node("子节点")
    child_body["parent_uuid"] = parent["uuid"]
    child = client.post(
        f"/api/v1/workflows/{source_uuid}/nodes",
        json=child_body,
    ).json()["data"]

    copied_response = client.post(
        f"/api/v1/workflows/{source_uuid}/duplicate",
        json={"name": "复制流程"},
    )
    assert copied_response.status_code == 201
    copied = copied_response.json()["data"]
    assert copied["workflow"]["uuid"] != source_uuid
    assert copied["workflow"]["name"] == "复制流程"
    assert len(copied["nodes"]) == 2
    copied_parent = next(node for node in copied["nodes"] if node["name"] == "父节点")
    copied_child = next(node for node in copied["nodes"] if node["name"] == "子节点")
    assert copied_parent["uuid"] != parent["uuid"]
    assert copied_child["uuid"] != child["uuid"]
    assert copied_child["parent_uuid"] == copied_parent["uuid"]

    client.patch(
        f"/api/v1/workflow-nodes/{copied_child['uuid']}",
        json={"name": "复制图内子节点"},
    )
    source_graph = client.get(
        f"/api/v1/workflows/{source_uuid}/graph"
    ).json()["data"]
    assert next(node for node in source_graph["nodes"] if node["uuid"] == child["uuid"])[
        "name"
    ] == "子节点"
    store.close()


def test_batch_delete_rejects_unknown_identity_without_partial_write(tmp_path) -> None:
    """批量删除含未知身份时必须关闭式失败并保留整图。"""

    client, store = _client(tmp_path)
    workflow = _workflow(client)
    workflow_uuid = workflow["uuid"]
    node = client.post(
        f"/api/v1/workflows/{workflow_uuid}/nodes",
        json=_compute_node("保留节点"),
    ).json()["data"]

    response = client.post(
        f"/api/v1/workflows/{workflow_uuid}/batch-delete",
        json={
            "node_uuids": ["ffffffff-ffff-4fff-8fff-ffffffffffff"],
            "edge_uuids": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["code"] == 1000
    graph = client.get(
        f"/api/v1/workflows/{workflow_uuid}/graph"
    ).json()["data"]
    assert [item["uuid"] for item in graph["nodes"]] == [node["uuid"]]
    store.close()


def test_legacy_import_accepts_wrapped_payload_and_rebuilds_identities(tmp_path) -> None:
    """旧版导入须原子创建首版图，并且不能沿用来源节点身份。"""

    client, store = _client(tmp_path)
    old_node_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    response = client.post(
        "/api/v1/workflows/import",
        json={
            "data": {
                "workflow_name": "旧版流程",
                "tags": ["legacy"],
                "meta_data": {},
                "nodes": [{"uuid": old_node_uuid, **_compute_node("旧计算节点")}],
                "edges": [],
                "inventory_requirements": [],
            }
        },
    )
    assert response.status_code == 201
    imported = response.json()["data"]
    assert imported["workflow"]["name"] == "旧版流程"
    assert imported["workflow"]["revision"] == 1
    assert imported["nodes"][0]["uuid"] != old_node_uuid
    assert imported["nodes"][0]["name"] == "旧计算节点"
    assert store.count_rows("workflow") == 1
    store.close()
