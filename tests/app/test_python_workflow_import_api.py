"""Local 模式 Python 工作流文件导入 API 测试。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from tests.workflow.test_authoring_engine import (
    ANALYZE_NODE_UUID,
    PREPARE_NODE_UUID,
    WORKFLOW_UUID,
    _engine,
    _source,
)
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore


def _client(tmp_path: Any) -> tuple[TestClient, WorkflowService, WorkflowStore]:
    """创建模板目录、编译器和 SQLite 权威完全对齐的测试应用。

    参数：``tmp_path`` 是 pytest 提供的隔离目录。返回 HTTP 客户端、服务和存储，
    由调用方在断言后关闭服务。
    """

    engine = _engine()
    store = WorkflowStore(tmp_path / "workflow_history.db")
    service = WorkflowService(store, compiler=engine)
    return TestClient(create_workflow_app(service)), service, store


def _upload(
    client: TestClient,
    source: str,
    *,
    file_name: str = "sample_workflow.py",
):
    """以原始 Python 文件字节调用 Local 导入接口。

    参数：``client`` 是隔离应用的 HTTP 客户端；``source`` 是待导入源码；
    ``file_name`` 是不含路径的上传文件名。返回原始 HTTP 响应，供各测试核对
    Backend 形状 envelope 与持久事实。
    """

    return client.post(
        "/api/v1/local/workflows/import-python",
        content=source.encode("utf-8"),
        headers={
            "Content-Type": "text/x-python",
            "X-Workflow-Filename": file_name,
        },
    )


def test_python_file_import_compiles_and_creates_complete_graph_atomically(
    tmp_path: Any,
) -> None:
    """有效源码应只经 AST 编译并在一次事务中创建完整工作流图。

    参数：``tmp_path`` 是本用例独占的 SQLite 与文件证据目录。返回：无。
    """

    client, service, store = _client(tmp_path)
    try:
        response = _upload(client, _source())
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["code"] == 0, payload
        graph = payload["data"]
        assert graph["workflow"]["uuid"] == WORKFLOW_UUID
        assert graph["workflow"]["name"] == "Sample preparation"
        assert [node["uuid"] for node in graph["nodes"]] == [
            PREPARE_NODE_UUID,
            ANALYZE_NODE_UUID,
        ]
        assert store.count_rows("workflow") == 1
        assert store.count_rows("workflow_node") == 2
        provenance = graph["workflow"]["meta_data"]["unilab"]["python_import"]
        assert provenance["file_name"] == "sample_workflow.py"
        assert provenance["compiler_mode"] == "ast_only"
        assert provenance["source_hash"].startswith("sha256:")

        readback = client.get(f"/api/v1/workflows/{WORKFLOW_UUID}/graph")
        published = client.post(
            f"/api/v1/workflows/{WORKFLOW_UUID}/publications",
            json={"revision": 1},
        )
        assert readback.status_code == 200
        assert readback.json()["code"] == 0
        assert published.status_code == 201
        assert published.json()["data"]["node_count"] == 2
        assert published.json()["data"]["edge_count"] == 1
    finally:
        service.close()


def test_python_file_import_rejects_invalid_source_without_partial_workflow(
    tmp_path: Any,
) -> None:
    """缺少显式工作流身份的源码必须失败且不留下空壳定义。

    参数：``tmp_path`` 是本用例独占的 SQLite 目录。返回：无。
    """

    client, service, store = _client(tmp_path)
    try:
        response = _upload(client, "raise RuntimeError('must never execute')\n")
        assert response.status_code == 200
        assert response.json() == {
            "code": 1000,
            "error": {"msg": "提交内容格式不正确"},
        }
        assert store.count_rows("workflow") == 0
        assert store.count_rows("workflow_node") == 0
    finally:
        service.close()


def test_python_file_import_rejects_compile_error_without_partial_workflow(
    tmp_path: Any,
) -> None:
    """有合法身份但引用未知动作的源码也不能留下工作流或节点。

    参数：``tmp_path`` 是本用例独占的 SQLite 目录。返回：无。
    """

    client, service, store = _client(tmp_path)
    invalid_source = _source().replace(
        "reactor.prepare(sample=sample, cycles=cycles)",
        "reactor.unknown(sample=sample, cycles=cycles)",
    )
    try:
        response = _upload(client, invalid_source)
        assert response.status_code == 200
        assert response.json()["code"] == 3003
        assert store.count_rows("workflow") == 0
        assert store.count_rows("workflow_node") == 0
    finally:
        service.close()


def test_python_file_import_rejects_path_and_duplicate_identity(
    tmp_path: Any,
) -> None:
    """文件名不能携带路径，同一源码身份也不能创建第二份定义。

    参数：``tmp_path`` 是本用例独占的 SQLite 目录。返回：无。
    """

    client, service, store = _client(tmp_path)
    try:
        invalid_name = _upload(client, _source(), file_name="../sample.py")
        assert invalid_name.status_code == 200
        assert invalid_name.json()["code"] == 1000
        assert store.count_rows("workflow") == 0

        first = _upload(client, _source())
        repeated = _upload(client, _source())
        assert first.status_code == 201
        assert first.json()["code"] == 0
        assert repeated.status_code == 200
        assert repeated.json()["code"] == 3003
        assert store.count_rows("workflow") == 1
        assert store.count_rows("workflow_node") == 2
    finally:
        service.close()


def test_python_file_import_enforces_utf8_and_shared_body_budget(tmp_path: Any) -> None:
    """原始文件必须是 UTF-8，且继续受工作流公共 8 MiB 请求预算保护。

    参数：``tmp_path`` 是本用例独占的 SQLite 目录。返回：无。
    """

    client, service, store = _client(tmp_path)
    headers = {
        "Content-Type": "text/x-python",
        "X-Workflow-Filename": "sample.py",
    }
    try:
        invalid_utf8 = client.post(
            "/api/v1/local/workflows/import-python",
            content=b"\xff\xfe",
            headers=headers,
        )
        too_large = client.post(
            "/api/v1/local/workflows/import-python",
            content=b"#" * (8 * 1024 * 1024 + 1),
            headers=headers,
        )
        assert invalid_utf8.status_code == 200
        assert invalid_utf8.json()["code"] == 1000
        assert too_large.status_code == 200
        assert too_large.json()["code"] == 1000
        assert store.count_rows("workflow") == 0
    finally:
        service.close()


def test_python_file_import_never_executes_module_level_source(tmp_path: Any) -> None:
    """合法身份旁的模块级副作用必须作为非法静态语法拒绝且绝不执行。

    参数：``tmp_path`` 同时提供隔离数据库和副作用哨兵路径。返回：无；哨兵不存在
    证明上传源码未被 import、compile、eval 或 execute。
    """

    client, service, store = _client(tmp_path)
    side_effect_path = tmp_path / "must-not-exist.txt"
    dangerous_source = _source().replace(
        "@workflow(",
        f"__import__('pathlib').Path({str(side_effect_path)!r}).write_text('bad')\n\n"
        "@workflow(",
        1,
    )
    try:
        response = _upload(client, dangerous_source)
        assert response.status_code == 200
        assert response.json()["code"] == 3003
        assert not side_effect_path.exists()
        assert store.count_rows("workflow") == 0
        assert store.count_rows("workflow_node") == 0
    finally:
        service.close()


def test_python_file_import_rolls_back_cross_workflow_node_identity_conflict(
    tmp_path: Any,
) -> None:
    """不同工作流复用既有节点身份时必须拒绝并回滚第二个工作流主记录。

    参数：``tmp_path`` 是本用例独占的 SQLite 目录。返回：无；断言只保留首个
    工作流及其节点，证明候选身份检查和写事务没有留下第二个空壳定义。
    """

    client, service, store = _client(tmp_path)
    second_workflow_uuid = str(uuid4())
    conflicting_source = _source().replace(WORKFLOW_UUID, second_workflow_uuid)
    try:
        first = _upload(client, _source(), file_name="first.py")
        conflicting = _upload(client, conflicting_source, file_name="second.py")
        assert first.status_code == 201
        assert first.json()["code"] == 0
        assert conflicting.status_code == 200
        assert conflicting.json()["code"] == 3003
        assert store.count_rows("workflow") == 1
        assert store.count_rows("workflow_node") == 2
    finally:
        service.close()


def test_python_file_import_requires_compiler_and_filename_header(
    tmp_path: Any,
) -> None:
    """缺少模板编译器或上传文件名时必须在任何持久写入前关闭失败。

    参数：``tmp_path`` 是本用例独占的 SQLite 目录。返回：无；分别核对目录不可用
    与 HTTP 请求不完整的稳定错误 envelope。
    """

    store = WorkflowStore(tmp_path / "workflow_history.db")
    service = WorkflowService(store)
    client = TestClient(create_workflow_app(service))
    try:
        unavailable = _upload(client, _source())
        missing_name = client.post(
            "/api/v1/local/workflows/import-python",
            content=_source().encode("utf-8"),
            headers={"Content-Type": "text/x-python"},
        )
        assert unavailable.status_code == 200
        assert unavailable.json()["code"] == 5001
        assert missing_name.status_code == 200
        assert missing_name.json()["code"] == 1000
        assert store.count_rows("workflow") == 0
    finally:
        service.close()


def test_python_file_import_openapi_and_scheduler_cors_contract(tmp_path: Any) -> None:
    """OpenAPI 与独立调度入口必须共同公开浏览器可调用的上传合同。

    参数：``tmp_path`` 是本用例独占的 SQLite 目录。返回：无；OpenAPI 必须声明
    Python 原始请求体和 201，CORS 必须允许文件名请求头通过预检。
    """

    from unilabos.app.scheduler.api import create_app as create_scheduler_app

    client, service, _store = _client(tmp_path)
    try:
        operation = client.app.openapi()["paths"][
            "/api/v1/local/workflows/import-python"
        ]["post"]
        assert "201" in operation["responses"]
        assert "text/x-python" in operation["requestBody"]["content"]

        scheduler_app = create_scheduler_app()
        cors = next(
            middleware
            for middleware in scheduler_app.user_middleware
            if middleware.cls is CORSMiddleware
        )
        allowed_headers = {str(value).lower() for value in cors.kwargs["allow_headers"]}
        assert "x-workflow-filename" in allowed_headers
    finally:
        service.close()


def test_python_file_import_is_process_local_and_disappears_after_restart(
    tmp_path: Any,
) -> None:
    """临时导入只安装进程内定义，重启后消失且文件库不留定义。

    参数：``tmp_path`` 是跨两次服务生命周期复用的运行事实 SQLite 目录。返回：
    无；第一进程立即可读，第二进程返回不存在，且文件库始终没有定义行。
    """

    database_path = tmp_path / "workflow_history.db"
    engine = _engine()
    first_store = WorkflowStore(
        database_path,
        persist_workflow_definitions=False,
    )
    first_definitions = WorkflowStore(":memory:")
    first_service = WorkflowService(
        first_store,
        definition_store=first_definitions,
        compiler=engine,
    )
    try:
        imported = _upload(
            TestClient(create_workflow_app(first_service)),
            _source(),
            file_name="restart-safe.py",
        )
        assert imported.status_code == 201
        assert imported.json()["code"] == 0
        assert first_definitions.count_rows("workflow") == 1
        assert first_store.count_rows("workflow") == 0
        assert first_store.count_rows("workflow_node") == 0
    finally:
        first_service.close()

    reopened_store = WorkflowStore(
        database_path,
        persist_workflow_definitions=False,
    )
    reopened_definitions = WorkflowStore(":memory:")
    reopened_service = WorkflowService(
        reopened_store,
        definition_store=reopened_definitions,
        compiler=engine,
    )
    try:
        response = TestClient(create_workflow_app(reopened_service)).get(
            f"/api/v1/workflows/{WORKFLOW_UUID}/graph"
        )
        assert response.status_code == 200
        assert response.json() == {
            "code": 3002,
            "error": {"msg": "请求的资源不存在"},
        }
        assert reopened_store.count_rows("workflow") == 0
        assert reopened_definitions.count_rows("workflow") == 0
    finally:
        reopened_service.close()
