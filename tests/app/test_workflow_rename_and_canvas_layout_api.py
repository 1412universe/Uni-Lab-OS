"""工作流局部更新与画布落点持久化的公开 HTTP 合同回归。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore


def _client(tmp_path) -> tuple[TestClient, WorkflowStore]:
    """创建隔离的工作流应用。

    参数：``tmp_path`` 是 pytest 提供的临时目录。返回：公共 HTTP 客户端与需要
    由调用方关闭的存储。异常：应用装配失败时原样抛出。
    """

    store = WorkflowStore(tmp_path / "workflow-history.db")
    service = WorkflowService(store)
    return TestClient(create_workflow_app(service)), store


def _create(client: TestClient, name: str = "待重命名") -> dict:
    """创建一个空工作流并返回其公开读模型。"""

    response = client.post(
        "/api/v1/workflows",
        json={"name": name, "tags": ["chemistry"], "description": "原始说明"},
    )
    assert response.status_code == 201
    return response.json()["data"]


# ── PATCH：局部更新 ──────────────────────────────────────────────────────


def test_patch_rename_keeps_untouched_root_fields(tmp_path) -> None:
    """只改名称时，描述、标签与元数据都必须原样保留。

    这曾是重命名的真实缺陷：前端用 `PUT` 拼载荷，而 `PUT` 是完整替换语义，
    列表页手里只有摘要字段，于是重命名会顺手清掉描述和标签。
    """

    client, store = _client(tmp_path)
    try:
        created = _create(client)
        before = client.get(f"/api/v1/workflows/{created['uuid']}").json()["data"]

        response = client.patch(
            f"/api/v1/workflows/{created['uuid']}",
            json={"name": "改名后"},
        )

        assert response.status_code == 200
        updated = response.json()["data"]
        assert updated["name"] == "改名后"
        # 未提交的字段保持原值
        assert updated["description"] == before["description"]
        assert updated["tags"] == before["tags"]
        assert updated["meta_data"] == before["meta_data"]
        assert updated["workflow_type"] == before["workflow_type"]
    finally:
        store.close()


def test_patch_can_clear_description_explicitly(tmp_path) -> None:
    """显式传 null 表示清空描述；省略才是「保持原值」。

    两者必须可区分，否则前端无法表达「把说明删掉」。
    """

    client, store = _client(tmp_path)
    try:
        created = _create(client)

        response = client.patch(
            f"/api/v1/workflows/{created['uuid']}",
            json={"description": None},
        )

        assert response.status_code == 200
        # 清空后后端按 omitempty 形状省略该字段，因此按「空值」断言。
        assert response.json()["data"].get("description") in (None, "")
        # 名称不受影响
        assert response.json()["data"]["name"] == created["name"]
    finally:
        store.close()


def test_patch_rejects_blank_name_and_leaves_definition_untouched(tmp_path) -> None:
    """空白名称关闭式失败，且不留下部分更新。"""

    client, store = _client(tmp_path)
    try:
        created = _create(client)

        response = client.patch(
            f"/api/v1/workflows/{created['uuid']}",
            json={"name": "   "},
        )

        assert response.status_code == 200
        assert response.json()["code"] != 0
        current = client.get(f"/api/v1/workflows/{created['uuid']}").json()["data"]
        assert current["name"] == created["name"]
    finally:
        store.close()


def test_patch_unknown_field_is_rejected(tmp_path) -> None:
    """PATCH 只接受已知字段，避免拼写错误被静默忽略。"""

    client, store = _client(tmp_path)
    try:
        created = _create(client)

        response = client.patch(
            f"/api/v1/workflows/{created['uuid']}",
            json={"name": "新名", "nope": 1},
        )

        assert response.status_code == 200
        assert response.json()["code"] != 0
        assert response.json()["error"]["msg"]
    finally:
        store.close()


# ── 画布落点持久化 ──────────────────────────────────────────────────────


def test_canvas_layout_round_trips_through_read_model(tmp_path) -> None:
    """写入落点后，编辑器读图能拿回同一坐标。

    落点必须与图分开保存：Local 模式的图每次启动都由源码重建，生成器不写
    `pose`，所以只有独立的持久表才能让坐标跨重启存活。
    """

    client, store = _client(tmp_path)
    try:
        created = _create(client)
        workflow_uuid = created["uuid"]

        saved = client.put(
            f"/api/v1/workflows/{workflow_uuid}/canvas-layout",
            json={"positions": {"n1": {"x": 120, "y": 64}}},
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["node_count"] == 1

        # 布局表独立可读，且不依赖图里是否存在这个节点
        assert store.read_canvas_layout(workflow_uuid) == {"n1": {"x": 120.0, "y": 64.0}}
    finally:
        store.close()


def test_canvas_layout_is_a_full_replacement(tmp_path) -> None:
    """整体替换语义：先前写入但本次未提交的节点会被清掉。

    删除节点后若还留着它的坐标，就成了永远不会被清理的孤儿行。
    """

    client, store = _client(tmp_path)
    try:
        created = _create(client)
        workflow_uuid = created["uuid"]

        client.put(
            f"/api/v1/workflows/{workflow_uuid}/canvas-layout",
            json={"positions": {"n1": {"x": 0, "y": 0}, "n2": {"x": 300, "y": 0}}},
        )
        client.put(
            f"/api/v1/workflows/{workflow_uuid}/canvas-layout",
            json={"positions": {"n2": {"x": 300, "y": 0}}},
        )

        layout = store.read_canvas_layout(workflow_uuid)
        assert set(layout) == {"n2"}
    finally:
        store.close()


def test_canvas_layout_rejects_non_finite_and_non_numeric(tmp_path) -> None:
    """坐标必须是有限数值：NaN/Infinity 会毁掉画布视口计算。"""

    client, store = _client(tmp_path)
    try:
        created = _create(client)
        workflow_uuid = created["uuid"]

        for bad in ({"n1": {"x": "abc", "y": 0}}, {"n1": {"x": 1, "y": None}}, {"n1": 5}):
            response = client.put(
                f"/api/v1/workflows/{workflow_uuid}/canvas-layout",
                json={"positions": bad},
            )
            assert response.status_code == 200
            assert response.json()["code"] != 0
        # 失败不应留下任何行
        assert store.read_canvas_layout(workflow_uuid) == {}
    finally:
        store.close()


def test_canvas_layout_does_not_advance_workflow_revision(tmp_path) -> None:
    """纯坐标调整不得推进修订。

    否则一次拖动节点就会让已发布合同、预检结果和源码固定点全部失配。
    """

    client, store = _client(tmp_path)
    try:
        created = _create(client)
        workflow_uuid = created["uuid"]

        client.put(
            f"/api/v1/workflows/{workflow_uuid}/canvas-layout",
            json={"positions": {"n1": {"x": 10, "y": 20}}},
        )

        current = client.get(f"/api/v1/workflows/{workflow_uuid}").json()["data"]
        assert current["revision"] == created["revision"]
    finally:
        store.close()


def test_canvas_layout_unknown_workflow_is_not_found(tmp_path) -> None:
    """未知工作流身份返回稳定业务错误，而不是写入孤儿行。"""

    client, store = _client(tmp_path)
    try:
        response = client.put(
            "/api/v1/workflows/11111111-1111-4111-8111-111111111111/canvas-layout",
            json={"positions": {"n1": {"x": 0, "y": 0}}},
        )

        assert response.status_code == 200
        assert response.json()["code"] != 0
    finally:
        store.close()


def test_editor_read_model_overlays_stored_layout_only_when_pose_is_missing(
    tmp_path,
) -> None:
    """读图时只给缺坐标的节点补落点，已有坐标的节点保持原样。

    保留原值很关键：同一次会话里刚拖过的位置不能被旧落点覆盖回去。
    """

    client, store = _client(tmp_path)
    try:
        created = _create(client)
        workflow_uuid = created["uuid"]
        store.write_canvas_layout(
            workflow_uuid,
            {"n1": {"x": 111.0, "y": 222.0}, "n2": {"x": 333.0, "y": 444.0}},
        )

        graph = store.get_graph(workflow_uuid)
        overlay = {
            "workflow": graph["workflow"],
            "nodes": [
                {"uuid": "n1", "pose": {}},
                {"uuid": "n2", "pose": {"position": {"x": 5, "y": 6}}},
            ],
        }
        service = WorkflowService(store)
        result = service._overlay_canvas_layout(overlay)

        by_uuid = {node["uuid"]: node["pose"] for node in result["nodes"]}
        # 缺坐标 → 补上持久落点
        assert by_uuid["n1"]["position"] == {"x": 111.0, "y": 222.0}
        # 已有坐标 → 不被覆盖
        assert by_uuid["n2"]["position"] == {"x": 5, "y": 6}
    finally:
        store.close()
