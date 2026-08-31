"""工作流不可变发布（PublishedWorkflowContract）的公开接口合同。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.json_codec import decode_json_bytes
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore


def _client(tmp_path):
    """创建隔离的本地工作流公开接口测试客户端。"""

    store = WorkflowStore(tmp_path / "workflow_publication.db")
    service = WorkflowService(store)
    return TestClient(create_workflow_app(service)), store


def _create_workflow_with_one_node(client: TestClient) -> tuple[str, int]:
    """创建一张可发布的单节点工作流图并返回身份与修订。"""

    created = client.post(
        "/api/v1/workflows",
        json={"name": "样品审核", "tags": ["published"], "meta_data": {}},
    )
    assert created.status_code == 201
    workflow_uuid = created.json()["data"]["uuid"]
    graph = client.put(
        f"/api/v1/workflows/{workflow_uuid}/graph",
        json={
            "revision": 1,
            "nodes": [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "name": "人工确认",
                    "type": "manual_confirm",
                    "pose": {"x": 80, "y": 60},
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
    assert graph.status_code == 200
    return workflow_uuid, graph.json()["data"]["workflow"]["revision"]


def test_publication_is_idempotent_and_freezes_each_revision(tmp_path) -> None:
    """同一修订重复发布须复用合同，新修订须生成不影响旧版的新合同。

    参数：``tmp_path`` 隔离 SQLite 权威。返回：无。异常：发布身份、版本递增、
    图快照不可变或公开字段偏离 Backend 合同时由断言暴露。
    """

    client, store = _client(tmp_path)
    workflow_uuid, revision = _create_workflow_with_one_node(client)

    first = client.post(
        f"/api/v1/workflows/{workflow_uuid}/publications",
        json={"revision": revision},
    )
    assert first.status_code == 201
    first_contract = first.json()["data"]
    assert first_contract["workflow_uuid"] == workflow_uuid
    assert first_contract["workflow_revision"] == revision
    assert first_contract["version"] == 1
    assert first_contract["node_count"] == 1
    assert first_contract["edge_count"] == 0
    assert first_contract["source_hash"].startswith("sha256:")
    assert first_contract["revision_fingerprint"] == first_contract["source_hash"]
    assert first_contract["contract_digest"].startswith("sha256:")
    assert first_contract["input_contract"] == {"version": 1, "parameters": []}
    assert "inputs" not in first_contract["input_contract"]
    assert "graph_snapshot" not in first_contract

    repeated = client.post(
        f"/api/v1/workflows/{workflow_uuid}/publications",
        json={"revision": revision},
    )
    assert repeated.status_code == 201
    assert repeated.json()["data"]["uuid"] == first_contract["uuid"]

    updated = client.put(
        f"/api/v1/workflows/{workflow_uuid}/graph",
        json={
            "revision": revision,
            "nodes": [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "name": "人工确认（修订版）",
                    "type": "manual_confirm",
                    "pose": {"x": 120, "y": 60},
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
    assert updated.status_code == 200
    next_revision = updated.json()["data"]["workflow"]["revision"]
    second = client.post(
        f"/api/v1/workflows/{workflow_uuid}/publications",
        json={"revision": next_revision},
    )
    assert second.status_code == 201
    second_contract = second.json()["data"]
    assert second_contract["version"] == 2
    assert second_contract["uuid"] != first_contract["uuid"]
    assert second_contract["source_hash"] != first_contract["source_hash"]

    stored_first = store._conn.execute(
        "SELECT graph_snapshot FROM published_workflow_contract WHERE uuid = ?",
        (first_contract["uuid"],),
    ).fetchone()
    frozen_graph = decode_json_bytes(stored_first["graph_snapshot"].encode("utf-8"))
    assert frozen_graph["nodes"][0]["name"] == "人工确认"
    store.close()


def test_publication_list_returns_latest_contract_per_workflow(tmp_path) -> None:
    """发行目录只返回每个来源工作流的最新完整版本。"""

    client, store = _client(tmp_path)
    workflow_uuid, revision = _create_workflow_with_one_node(client)
    published = client.post(
        f"/api/v1/workflows/{workflow_uuid}/publications",
        json={"revision": revision},
    ).json()["data"]

    response = client.get(
        "/api/v1/published-workflow-contracts",
        params={"page": 1, "page_size": 20, "keyword": "样品"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "items": [published],
        "has_more": False,
        "page": 1,
        "page_size": 20,
    }
    store.close()


def test_workflow_read_models_expose_source_and_published_status(tmp_path) -> None:
    """工作流列表与详情按当前修订返回源码或已发布状态。

    参数：``tmp_path`` 隔离工作流和发布合同存储。返回：无。异常：公开列表、详情
    或完整图没有随修订在 ``source`` 与 ``published`` 间切换时由断言暴露。
    """

    client, store = _client(tmp_path)
    created = client.post(
        "/api/v1/workflows",
        json={"name": "状态展示", "tags": [], "meta_data": {}},
    )
    assert created.status_code == 201
    workflow_uuid = created.json()["data"]["uuid"]
    assert created.json()["data"]["status"] == "source"

    listed_source = client.get("/api/v1/workflows").json()["data"]["items"]
    assert listed_source[0]["status"] == "source"
    assert client.get(f"/api/v1/workflows/{workflow_uuid}").json()["data"][
        "status"
    ] == ("source")

    graph = client.put(
        f"/api/v1/workflows/{workflow_uuid}/graph",
        json={
            "revision": 1,
            "nodes": [
                {
                    "uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "name": "人工确认",
                    "type": "manual_confirm",
                    "pose": {"x": 80, "y": 60},
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
    assert graph.status_code == 200
    assert graph.json()["data"]["workflow"]["status"] == "source"
    revision = graph.json()["data"]["workflow"]["revision"]
    published = client.post(
        f"/api/v1/workflows/{workflow_uuid}/publications",
        json={"revision": revision},
    )
    assert published.status_code == 201
    assert client.get(f"/api/v1/workflows/{workflow_uuid}").json()["data"][
        "status"
    ] == ("published")
    assert (
        client.get(f"/api/v1/workflows/{workflow_uuid}/graph").json()["data"][
            "workflow"
        ]["status"]
        == "published"
    )

    changed = client.put(
        f"/api/v1/workflows/{workflow_uuid}/graph",
        json={
            "revision": revision,
            "nodes": [
                {
                    "uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "name": "人工确认（修改）",
                    "type": "manual_confirm",
                    "pose": {"x": 100, "y": 60},
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
    assert changed.status_code == 200
    assert changed.json()["data"]["workflow"]["status"] == "source"
    assert client.get(f"/api/v1/workflows/{workflow_uuid}").json()["data"][
        "status"
    ] == ("source")
    store.close()


def test_publication_rejects_stale_revision_and_empty_graph(tmp_path) -> None:
    """发布必须命中当前修订，并拒绝没有节点的空工作流。"""

    client, store = _client(tmp_path)
    workflow_uuid, revision = _create_workflow_with_one_node(client)
    stale = client.post(
        f"/api/v1/workflows/{workflow_uuid}/publications",
        json={"revision": revision + 1},
    )
    assert stale.status_code == 200
    assert stale.json()["code"] == 3003

    empty = client.post(
        "/api/v1/workflows",
        json={"name": "空工作流", "tags": [], "meta_data": {}},
    ).json()["data"]
    rejected = client.post(
        f"/api/v1/workflows/{empty['uuid']}/publications",
        json={"revision": empty["revision"]},
    )
    assert rejected.status_code == 200
    assert rejected.json()["code"] == 1000
    store.close()


def test_composite_invocation_expands_one_frozen_contract_into_parent(tmp_path) -> None:
    """组合调用须把不可变子合同确定性展开为父图的真实层级节点。

    参数：``tmp_path`` 隔离父子工作流。返回：无。异常：调用根身份、私有子树、
    修订推进或冻结合同 pin 偏离 Backend 公共接口时由断言暴露。
    """

    client, store = _client(tmp_path)
    child_uuid, child_revision = _create_workflow_with_one_node(client)
    contract = client.post(
        f"/api/v1/workflows/{child_uuid}/publications",
        json={"revision": child_revision},
    ).json()["data"]
    parent = client.post(
        "/api/v1/workflows",
        json={"name": "父工作流", "tags": [], "meta_data": {}},
    ).json()["data"]
    invocation_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    inserted = client.post(
        f"/api/v1/workflows/{parent['uuid']}/composite-invocations",
        json={
            "revision": parent["revision"],
            "contract_uuid": contract["uuid"],
            "invocation_uuid": invocation_uuid,
            "device_bindings": {},
            "pose": {"x": 320, "y": 100},
            "param": {},
        },
    )
    assert inserted.status_code == 200
    graph = inserted.json()["data"]
    assert graph["workflow"]["revision"] == 2
    assert len(graph["nodes"]) == 2
    root = next(node for node in graph["nodes"] if node["uuid"] == invocation_uuid)
    child = next(node for node in graph["nodes"] if node["uuid"] != invocation_uuid)
    assert root["type"] == "workflow"
    assert root["workflow_node_template_uuid"] == contract["node_template_uuid"]
    assert root["meta_data"]["unilab"]["composite"]["contract_uuid"] == contract["uuid"]
    compatibility = root["meta_data"]["unilab"]["composite"]["contract_compatibility"]
    assert compatibility["parameters"] == []
    assert "inputs" not in compatibility
    assert child["parent_uuid"] == invocation_uuid
    assert child["name"] == "人工确认"
    store.close()


def test_new_child_publication_refreshes_parent_and_nested_grandparent(
    tmp_path,
) -> None:
    """子工作流重新发布后须自动替换父图和上游父图中的兼容调用。

    参数：``tmp_path`` 隔离父子工作流及发布合同。返回：无。异常：父图没有自动
    推进修订、嵌套调用没有向上游传播、调用身份或画布位置变化、合同仍指向旧版
    或内部节点没有更新时由断言暴露；整个流程只使用公开 HTTP 接口，模拟前端
    真实调用方式。
    """

    client, store = _client(tmp_path)
    child_uuid, child_revision = _create_workflow_with_one_node(client)
    first_contract = client.post(
        f"/api/v1/workflows/{child_uuid}/publications",
        json={"revision": child_revision},
    ).json()["data"]
    parent = client.post(
        "/api/v1/workflows",
        json={"name": "自动更新父工作流", "tags": [], "meta_data": {}},
    ).json()["data"]
    # ``invocation_uuid`` 是父图中对子工作流第一次调用的稳定身份；刷新前后必须
    # 保持不变，才能证明外部连线和画布布局不会因子版本替换而漂移。
    invocation_uuid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    inserted = client.post(
        f"/api/v1/workflows/{parent['uuid']}/composite-invocations",
        json={
            "revision": parent["revision"],
            "contract_uuid": first_contract["uuid"],
            "invocation_uuid": invocation_uuid,
            "device_bindings": {},
            "pose": {"x": 320, "y": 100},
            "param": {},
        },
    ).json()["data"]
    assert inserted["workflow"]["revision"] == 2

    parent_contract = client.post(
        f"/api/v1/workflows/{parent['uuid']}/publications",
        json={"revision": inserted["workflow"]["revision"]},
    ).json()["data"]
    grandparent = client.post(
        "/api/v1/workflows",
        json={"name": "上游组合工作流", "tags": [], "meta_data": {}},
    ).json()["data"]
    # ``grandparent_invocation_uuid`` 是上游父图引用中间父工作流的稳定身份，用于
    # 证明刷新会向祖父依赖传播，而不会把两级调用误认成同一节点。
    grandparent_invocation_uuid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    grandparent_graph = client.post(
        f"/api/v1/workflows/{grandparent['uuid']}/composite-invocations",
        json={
            "revision": grandparent["revision"],
            "contract_uuid": parent_contract["uuid"],
            "invocation_uuid": grandparent_invocation_uuid,
            "device_bindings": {},
            "pose": {"x": 80, "y": 40},
            "param": {},
        },
    ).json()["data"]
    assert grandparent_graph["workflow"]["revision"] == 2

    changed = client.put(
        f"/api/v1/workflows/{child_uuid}/graph",
        json={
            "revision": child_revision,
            "nodes": [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "name": "人工确认（新版）",
                    "type": "manual_confirm",
                    "pose": {"x": 120, "y": 80},
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
    assert changed.status_code == 200
    second = client.post(
        f"/api/v1/workflows/{child_uuid}/publications",
        json={"revision": changed.json()["data"]["workflow"]["revision"]},
    )
    assert second.status_code == 201
    second_contract = second.json()["data"]
    assert set(second_contract["dependent_refresh"]["updated_workflow_uuids"]) == {
        parent["uuid"],
        grandparent["uuid"],
    }
    assert second_contract["dependent_refresh"]["pending"] == []

    refreshed = client.get(f"/api/v1/workflows/{parent['uuid']}/graph").json()["data"]
    assert refreshed["workflow"]["revision"] == 3
    root = next(node for node in refreshed["nodes"] if node["uuid"] == invocation_uuid)
    nested = next(
        node for node in refreshed["nodes"] if node["uuid"] != invocation_uuid
    )
    assert root["pose"] == {"x": 320.0, "y": 100.0}
    assert (
        root["meta_data"]["unilab"]["composite"]["contract_uuid"]
        == (second_contract["uuid"])
    )
    assert (
        root["meta_data"]["unilab"]["composite"]["child_workflow_revision"]
        == second_contract["workflow_revision"]
    )
    assert nested["name"] == "人工确认（新版）"

    refreshed_grandparent = client.get(
        f"/api/v1/workflows/{grandparent['uuid']}/graph"
    ).json()["data"]
    assert refreshed_grandparent["workflow"]["revision"] == 3
    nested_invocation = next(
        node
        for node in refreshed_grandparent["nodes"]
        if node.get("meta_data", {})
        .get("unilab", {})
        .get("composite", {})
        .get("child_workflow_uuid")
        == child_uuid
    )
    assert nested_invocation["parent_uuid"] == grandparent_invocation_uuid
    assert (
        nested_invocation["meta_data"]["unilab"]["composite"]["contract_uuid"]
        == second_contract["uuid"]
    )
    store.close()
