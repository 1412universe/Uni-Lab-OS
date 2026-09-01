"""验证 develop/product 启动模式对工作流公开范围的影响。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from unilabos.app.main import normalize_startup_mode_argv, parse_args
from unilabos.app.runtime_topology import resolve_runtime_process_plan
from unilabos.app.startup_mode import (
    OSStartupMode,
    reset_startup_mode,
    set_startup_mode,
)
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
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
                    "type": "manual_confirm",
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
                        "type": "manual_confirm",
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
