"""验证 develop/product 启动模式对工作流公开范围的影响。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from unilabos.app.main import normalize_startup_mode_argv, parse_args
from unilabos.app.runtime_topology import resolve_runtime_process_plan
from unilabos.app.startup_mode import (
    OSStartupMode,
    get_startup_mode,
    reset_startup_mode,
    set_startup_mode,
)
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowConflict, WorkflowService
from unilabos.workflow.store import WorkflowStore


def _client(tmp_path: Path) -> tuple[TestClient, WorkflowService, WorkflowStore]:
    """建立通过公开 HTTP 接口操作的隔离工作流服务。

    参数：``tmp_path`` 是 pytest 提供的临时目录。返回：FastAPI 公共客户端、
    工作流服务和底层存储。异常：装配失败时原样抛出；调用方负责关闭服务与存储。
    """

    store = WorkflowStore(tmp_path / "startup-mode.db")
    service = WorkflowService(store)
    return TestClient(create_workflow_app(service)), service, store


def _create_and_publish(
    client: TestClient,
    *,
    name: str,
    workflow_type: str,
) -> str:
    """通过工作流 HTTP 合同创建并发布一个最小定义。

    参数：``client`` 是公开 FastAPI 客户端；``name`` 是工作流显示名；
    ``workflow_type`` 是 ``normal`` 或 ``experiment_operation``。返回：已发布
    工作流 UUID。异常：任一步骤不是预期的创建、保存或发布结果时由断言暴露。
    状态不变量：发布请求使用保存图后返回的最新修订，不直接访问存储内部。
    """

    created = client.post(
        "/api/v1/workflows",
        json={
            "name": name,
            "workflow_type": workflow_type,
            "tags": [],
            "meta_data": {},
        },
    )
    assert created.status_code == 201
    workflow_uuid = created.json()["data"]["uuid"]
    graph = client.put(
        f"/api/v1/workflows/{workflow_uuid}/graph",
        json={
            "revision": 1,
            "nodes": [
                {
                    "uuid": str(uuid4()),
                    "name": f"节点-{name}",
                    "type": "compute",
                    "pose": {"x": 0, "y": 0},
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
    revision = graph.json()["data"]["workflow"]["revision"]
    published = client.post(
        f"/api/v1/workflows/{workflow_uuid}/publications",
        json={"revision": revision},
    )
    assert published.status_code == 201
    return workflow_uuid


def test_startup_mode_parser_and_global_state_are_explicit() -> None:
    """启动模式枚举和全局状态只接受 develop/product 两个值。

    参数：无。返回：无。异常：未知值未被拒绝或设置后的全局值不一致时由断言
    暴露。状态不变量：测试结束恢复 develop，避免影响其他公共接口测试。
    """

    try:
        assert normalize_startup_mode_argv(
            ["unilab", "product", "--graph", "graph.json"]
        ) == ["unilab", "--run_mode", "product", "--graph", "graph.json"]
        assert vars(parse_args().parse_args(["develop"]))["run_mode"] == "develop"
        assert vars(parse_args().parse_args(["product"]))["run_mode"] == "product"
        assert set_startup_mode(OSStartupMode.PRODUCT) is OSStartupMode.PRODUCT
        assert set_startup_mode("develop") is OSStartupMode.DEVELOP
    finally:
        reset_startup_mode()


def test_backend_control_plane_is_not_a_startup_option() -> None:
    """启动参数和运行计划都拒绝已经移除的 Backend 控制面。

    参数：无。返回：无。异常：解析或运行计划仍接受 ``backend`` 时由断言暴露。
    状态不变量：未指定模式时解析结果为 ``develop``，且不会创建远端控制面计划。
    """

    parser = parse_args()
    with pytest.raises(SystemExit):
        parser.parse_args(["--control_plane", "backend"])
    assert vars(parser.parse_args([]))["control_plane"] == "local"
    assert vars(parser.parse_args([]))["run_mode"] == "develop"
    with pytest.raises(ValueError, match="仅支持 local"):
        resolve_runtime_process_plan({"control_plane": "backend"})


def test_local_startup_does_not_enable_cloud_websocket() -> None:
    """默认本地启动不带云端 WebSocket，显式旧桥也会被拒绝。

    参数：无。返回：无；默认桥仅包含入站 FastAPI。异常：解析器或运行计划仍
    接受 ``websocket`` 时测试失败。状态不变量：启动阶段不会从默认配置派生云端
    WebSocket 地址。
    """

    parser = parse_args()
    assert vars(parser.parse_args([]))["app_bridges"] == ["fastapi"]
    with pytest.raises(SystemExit):
        parser.parse_args(["--app_bridges", "websocket"])
    with pytest.raises(ValueError, match="移除云端 websocket"):
        resolve_runtime_process_plan(
            {"control_plane": "local", "app_bridges": ["websocket"]}
        )


def test_idle_runtime_switches_startup_mode_without_restart(tmp_path: Path) -> None:
    """空闲 Runtime 可原地往返切换模式，并立即改变工作流可见范围。"""

    client, service, store = _client(tmp_path)
    try:
        source_workflow = client.post(
            "/api/v1/workflows",
            json={"name": "仅开发可见", "tags": [], "meta_data": {}},
        ).json()["data"]["uuid"]

        switched = client.put(
            "/api/v1/startup-mode",
            json={"mode": "product", "expected_mode": "develop"},
        )

        assert switched.status_code == 200
        assert switched.json() == {
            "code": 0,
            "data": {
                "previous_mode": "develop",
                "mode": "product",
                "changed": True,
                "scope": "runtime_session",
                "requires_restart": False,
            },
        }
        product_list = client.get("/api/v1/workflows").json()["data"]["items"]
        assert source_workflow not in {item["uuid"] for item in product_list}

        restored = client.put(
            "/api/v1/startup-mode",
            json={"mode": "develop", "expected_mode": "product"},
        )
        assert restored.json()["data"]["mode"] == "develop"
        develop_list = client.get("/api/v1/workflows").json()["data"]["items"]
        assert source_workflow in {item["uuid"] for item in develop_list}
    finally:
        reset_startup_mode()
        service.close()
        store.close()


@pytest.mark.parametrize(
    ("status", "cleanup_status"),
    [("running", "none"), ("failed", "requires_attention")],
)
def test_startup_mode_switch_reports_task_blockers(
    tmp_path: Path,
    status: str,
    cleanup_status: str,
) -> None:
    """活动 Task 或未结算清理会阻止切换，并返回前端可展示的权威原因。"""

    client, service, store = _client(tmp_path)
    task_uuid = str(uuid4())
    try:
        workflow_uuid = client.post(
            "/api/v1/workflows",
            json={"name": "模式切换占用者", "tags": [], "meta_data": {}},
        ).json()["data"]["uuid"]
        with store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO workflow_task(
                    uuid, create_time, update_time, meta_data, workflow_uuid,
                    status, workflow_snapshot, execution_plan, run_mode,
                    execution_mode, control_status, cleanup_status,
                    trace_context, input, output, error_info
                ) VALUES (?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                          '{}', ?, ?, '{}',
                          '{"version":1,"nodes":[],"edges":[],"handles":[]}',
                          'normal', 'normal', 'active', ?, '{}', '{}', '{}', '[]')
                """,
                (task_uuid, workflow_uuid, status, cleanup_status),
            )

        response = client.put(
            "/api/v1/startup-mode",
            json={"mode": "product", "expected_mode": "develop"},
        )

        assert response.status_code == 200
        assert response.json()["code"] == 3003
        error = response.json()["error"]
        assert error["code"] == "startup_mode_switch_blocked"
        assert error["details"]["blockers"] == [
            {
                "task_uuid": task_uuid,
                "status": status,
                "cleanup_status": cleanup_status,
                "workflow_uuid": workflow_uuid,
                "execution_kind": "workflow",
            }
        ]
        assert get_startup_mode().value == "develop"
    finally:
        reset_startup_mode()
        service.close()
        store.close()


def test_startup_mode_switch_rejects_a_stale_observation(tmp_path: Path) -> None:
    """旧页面不能用过期 expected_mode 覆盖另一页面已完成的模式切换。"""

    client, service, store = _client(tmp_path)
    try:
        set_startup_mode("product")
        response = client.put(
            "/api/v1/startup-mode",
            json={"mode": "product", "expected_mode": "develop"},
        )

        assert response.json()["error"]["code"] == "startup_mode_conflict"
        assert response.json()["error"]["details"] == {
            "current_mode": "product",
            "expected_mode": "develop",
        }
    finally:
        reset_startup_mode()
        service.close()
        store.close()


def test_task_creation_and_mode_switch_share_one_admission_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """切换不能越过已进入首次写入的 Task，随后必须看到该阻塞事实。"""

    client, service, store = _client(tmp_path)
    task_entered_store = Event()
    allow_task_write = Event()
    switch_started = Event()
    original_create = store.create_task_with_jobs

    def paused_create(*args: object, **kwargs: object) -> dict[str, object]:
        task_entered_store.set()
        assert allow_task_write.wait(timeout=2)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(store, "create_task_with_jobs", paused_create)
    try:
        workflow_uuid = _create_and_publish(
            client,
            name="并发准入测试",
            workflow_type="normal",
        )

        def create_task() -> dict[str, object]:
            return service.create_workflow_task(
                workflow_uuid=workflow_uuid,
                run_mode="normal",
                target_node_uuid=None,
                input_value={},
                description=None,
                meta_data={},
            )

        def switch_mode() -> dict[str, object]:
            switch_started.set()
            return service.switch_startup_mode(
                mode="product",
                expected_mode="develop",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            task_future = executor.submit(create_task)
            assert task_entered_store.wait(timeout=2)
            switch_future = executor.submit(switch_mode)
            assert switch_started.wait(timeout=2)
            assert not switch_future.done()
            allow_task_write.set()
            assert task_future.result(timeout=2)["status"] == "pending"
            with pytest.raises(WorkflowConflict) as blocked:
                switch_future.result(timeout=2)

        assert blocked.value.code == "startup_mode_switch_blocked"
        assert get_startup_mode() is OSStartupMode.DEVELOP
    finally:
        allow_task_write.set()
        reset_startup_mode()
        service.close()
        store.close()


def test_develop_mode_rejects_a_second_nonterminal_task(tmp_path: Path) -> None:
    """develop 全局只允许一个非终态 Task，并返回占用者身份与状态。"""

    client, service, store = _client(tmp_path)
    occupied_uuid = str(uuid4())
    try:
        first_workflow = client.post(
            "/api/v1/workflows",
            json={"name": "占用调试槽", "tags": [], "meta_data": {}},
        ).json()["data"]["uuid"]
        second_workflow = client.post(
            "/api/v1/workflows",
            json={"name": "第二个任务", "tags": [], "meta_data": {}},
        ).json()["data"]["uuid"]
        with store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO workflow_task(
                    uuid, create_time, update_time, meta_data, workflow_uuid,
                    status, workflow_snapshot, execution_plan, run_mode,
                    execution_mode, control_status, cleanup_status,
                    trace_context, input, output, error_info
                ) VALUES (?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                          '{}', ?, 'pending', '{}',
                          '{"version":1,"nodes":[],"edges":[],"handles":[]}',
                          'normal', 'normal', 'active', 'none', '{}', '{}', '{}', '[]')
                """,
                (occupied_uuid, first_workflow),
            )

        response = client.post(
            "/api/v1/workflow-tasks",
            json={
                "workflow_uuid": second_workflow,
                "run_mode": "normal",
                "input": {},
            },
        )

        assert response.status_code == 200
        assert response.json()["code"] == 3003
        assert response.json()["error"]["code"] == "develop_task_conflict"
        assert occupied_uuid in response.json()["error"]["msg"]
        assert "pending" in response.json()["error"]["msg"]
    finally:
        reset_startup_mode()
        service.close()
        store.close()


def test_product_only_lists_published_normal_workflows(tmp_path: Path) -> None:
    """生产模式只公开已发布普通工作流，调试模式公开全部定义。

    参数：``tmp_path`` 隔离工作流定义与发布事实。返回：无。异常：生产模式仍
    返回源码、实验操作或其类别，或调试模式无法读取这些定义时由断言暴露。
    状态不变量：模式切换只影响公开读模型，不删除或修改工作流事实。
    """

    client, service, store = _client(tmp_path)
    try:
        normal_source = client.post(
            "/api/v1/workflows",
            json={"name": "普通草稿", "workflow_type": "normal", "tags": [], "meta_data": {}},
        ).json()["data"]["uuid"]
        operation_source = client.post(
            "/api/v1/workflows",
            json={
                "name": "实验草稿",
                "workflow_type": "experiment_operation",
                "tags": [],
                "meta_data": {},
            },
        ).json()["data"]["uuid"]
        hidden_node_uuid = str(uuid4())
        hidden_graph = client.put(
            f"/api/v1/workflows/{normal_source}/graph",
            json={
                "revision": 1,
                "nodes": [
                    {
                        "uuid": hidden_node_uuid,
                        "name": "隐藏节点",
                        "type": "compute",
                        "pose": {"x": 0, "y": 0},
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
        assert hidden_graph.status_code == 200

        set_startup_mode(OSStartupMode.DEVELOP)
        debug_items = client.get("/api/v1/workflows").json()["data"]["items"]
        assert {item["uuid"] for item in debug_items} >= {
            normal_source,
            operation_source,
        }
        assert client.get("/api/v1/experiment-operation-categories").json()["data"][
            "items"
        ]

        published_normal = _create_and_publish(
            client,
            name="普通已发布",
            workflow_type="normal",
        )
        published_operation = _create_and_publish(
            client,
            name="实验已发布",
            workflow_type="experiment_operation",
        )

        set_startup_mode(OSStartupMode.PRODUCT)
        product_items = client.get("/api/v1/workflows").json()["data"]["items"]
        assert [item["uuid"] for item in product_items] == [published_normal]
        assert client.get(f"/api/v1/workflows/{normal_source}").json()["code"] != 0
        assert client.get(f"/api/v1/workflows/{published_operation}").json()["code"] != 0
        assert client.get(f"/api/v1/workflow-nodes/{hidden_node_uuid}").json()["code"] != 0
        assert client.get("/api/v1/experiment-operation-categories").json()["data"][
            "items"
        ] == []
    finally:
        reset_startup_mode()
        service.close()
        store.close()


def test_product_mode_blocks_definition_writes_but_allows_visible_task_creation(
    tmp_path: Path,
) -> None:
    """生产模式拒绝定义写入，但允许已发布普通工作流创建任务。

    参数：``tmp_path`` 隔离工作流定义与运行事实。返回：无。异常：生产模式仍
    允许新增、修改、删除、导入、编辑图或发布，或不能创建已发布普通工作流任务
    时由断言暴露。状态不变量：所有定义写入在进入 DTO 校验和业务服务前失败，
    任务接口仍可运行，但源码工作流和实验操作不能绕过可见性门禁创建任务。
    """

    client, service, store = _client(tmp_path)
    try:
        published_normal = _create_and_publish(
            client,
            name="生产可执行工作流",
            workflow_type="normal",
        )
        source_normal = client.post(
            "/api/v1/workflows",
            json={
                "name": "生产不可见草稿",
                "workflow_type": "normal",
                "tags": [],
                "meta_data": {},
            },
        ).json()["data"]["uuid"]
        published_operation = _create_and_publish(
            client,
            name="生产不可见实验操作",
            workflow_type="experiment_operation",
        )

        set_startup_mode(OSStartupMode.PRODUCT)
        write_requests = [
            ("post", "/api/v1/workflows", {"name": "禁止创建"}),
            ("post", "/api/v1/workflows/import", {}),
            ("post", "/api/v1/local/workflows/import-python", None),
            (
                "put",
                f"/api/v1/workflows/{published_normal}",
                {"name": "禁止修改", "tags": [], "meta_data": {}},
            ),
            ("delete", f"/api/v1/workflows/{published_normal}", None),
            ("put", f"/api/v1/workflows/{published_normal}/graph", {}),
            ("post", f"/api/v1/workflows/{published_normal}/nodes", {}),
            ("post", f"/api/v1/workflows/{published_normal}/edges", {}),
            ("post", f"/api/v1/workflows/{published_normal}/batch-delete", {}),
            ("post", f"/api/v1/workflows/{published_normal}/duplicate", {}),
            ("post", f"/api/v1/workflows/{published_normal}/publications", {}),
            (
                "post",
                f"/api/v1/workflows/{published_normal}/composite-invocations",
                {},
            ),
            ("patch", f"/api/v1/workflow-nodes/{uuid4()}", {}),
            ("delete", f"/api/v1/workflow-nodes/{uuid4()}", None),
            ("post", f"/api/v1/workflow-nodes/{uuid4()}/duplicate", {}),
            ("delete", f"/api/v1/workflow-edges/{uuid4()}", None),
            ("post", "/api/v1/experiment-operation-categories", {}),
            ("put", f"/api/v1/experiment-operation-categories/{uuid4()}", {}),
            ("delete", f"/api/v1/experiment-operation-categories/{uuid4()}", None),
        ]
        for method, path, body in write_requests:
            kwargs = {"headers": {"content-type": "application/json"}}
            if path.endswith("import-python"):
                kwargs["content"] = b"# blocked"
                kwargs["headers"]["content-type"] = "text/x-python"
                kwargs["headers"]["X-Workflow-Filename"] = "blocked.py"
            elif body is not None:
                kwargs["json"] = body
            response = client.request(method, path, **kwargs)
            assert response.status_code == 200
            assert response.json()["code"] == 1001

        created_task = client.post(
            "/api/v1/workflow-tasks",
            json={"workflow_uuid": published_normal, "input": {}},
        )
        assert created_task.status_code == 201
        assert created_task.json()["code"] == 0

        preflight = client.post(
            f"/api/v1/workflows/{published_normal}/run-preflight",
            json={"run_mode": "normal", "input": {}},
        )
        assert preflight.status_code == 200
        assert preflight.json()["code"] == 0

        step_preflight = client.post(
            f"/api/v1/workflows/{published_normal}/run-preflight",
            json={"run_mode": "step", "input": {}},
        )
        assert step_preflight.json()["error"]["code"] == "develop_mode_required"
        step_task = client.post(
            "/api/v1/workflow-tasks",
            json={
                "workflow_uuid": published_normal,
                "run_mode": "step",
                "input": {},
            },
        )
        assert step_task.json()["error"]["code"] == "develop_mode_required"
        pause_task = client.post(
            f"/api/v1/workflow-tasks/{created_task.json()['data']['uuid']}/commands",
            json={"type": "pause", "idempotency_key": "product-pause"},
        )
        assert pause_task.json()["error"]["code"] == "develop_mode_required"
        step_state = client.get(
            f"/api/v1/workflow-tasks/{created_task.json()['data']['uuid']}/step-state"
        )
        assert step_state.json()["error"]["code"] == "develop_mode_required"

        hidden_source_task = client.post(
            "/api/v1/workflow-tasks",
            json={"workflow_uuid": source_normal, "input": {}},
        )
        assert hidden_source_task.json()["code"] != 0
        hidden_operation_task = client.post(
            "/api/v1/workflow-tasks",
            json={"workflow_uuid": published_operation, "input": {}},
        )
        assert hidden_operation_task.json()["code"] != 0
    finally:
        reset_startup_mode()
        service.close()
        store.close()


def test_legacy_debug_workflow_task_api_is_gone(tmp_path: Path) -> None:
    """旧 Debug 路由统一返回 410，并指向标准 WorkflowTask Step API。"""

    client, service, store = _client(tmp_path)
    try:
        create = client.post("/api/v1/debug/workflow-tasks", json={})
        detail = client.get(f"/api/v1/debug/workflow-tasks/{uuid4()}")
        command = client.post(
            f"/api/v1/debug/workflow-tasks/{uuid4()}/commands",
            json={},
        )

        assert [create.status_code, detail.status_code, command.status_code] == [
            410,
            410,
            410,
        ]
        assert create.json()["error"]["code"] == "debug_api_retired"
    finally:
        reset_startup_mode()
        service.close()
        store.close()
