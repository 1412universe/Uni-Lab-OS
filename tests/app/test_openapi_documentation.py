"""验证 UniLabOS Swagger 的接口和参数说明可以直接阅读。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI

from unilabos.app.edge_control.local_authority import (
    LocalEdgeAuthorityStore,
    LocalEdgeControlAuthority,
    create_local_edge_control_router,
)
from unilabos.app.operation_category_api import create_operation_category_router
from unilabos.app.scheduler.api import create_scheduler_router
from unilabos.app.scheduler.inventory.api import (
    create_legacy_material_router,
)
from unilabos.app.scheduler.inventory.api import (
    create_router as create_inventory_router,
)
from unilabos.app.scheduler.inventory.backend_api import (
    create_backend_resource_router,
)
from unilabos.app.scheduler.inventory.backend_contract import BackendResourceService
from unilabos.app.scheduler.inventory.layout import create_lab_router
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.app.web.openapi_docs import install_openapi_documentation
from unilabos.app.workflow_api import create_workflow_router
from unilabos.app.workflow_template_api import (
    WorkflowTemplateQueryService,
    create_workflow_template_router,
)
from unilabos.workflow.service import WorkflowService

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
FORBIDDEN_DESCRIPTION_TERMS = (
    "backend",
    "dto",
    "envelope",
    "authority",
    "cas 冲突",
    "sse",
    "幂等",
    "游标",
    "投影",
    "哈希",
    "修订",
    "信封",
)


def _has_chinese(value: object) -> bool:
    """参数是任意 Swagger 字段；返回其中是否包含中文。"""

    return any("\u4e00" <= character <= "\u9fff" for character in str(value))


def _description_texts(value: object) -> list[str]:
    """收集 Swagger 中实际展示给调用方的全部说明文字。"""

    descriptions: list[str] = []
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            description = current.get("description")
            if isinstance(description, str):
                descriptions.append(description)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return descriptions


def _assert_every_public_item_is_documented(schema: dict[str, object]) -> None:
    """检查接口、参数、请求体、返回值和模型字段都有可读说明。"""

    paths = cast(dict[str, dict[str, dict[str, object]]], schema["paths"])
    operations = [
        operation
        for path_item in paths.values()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    ]
    assert operations
    for operation in operations:
        assert _has_chinese(operation.get("summary"))
        assert _has_chinese(operation.get("description"))
        for parameter in cast(list[dict[str, object]], operation.get("parameters", [])):
            description = str(parameter.get("description", ""))
            assert _has_chinese(description)
            assert "是否必传：" in description
            assert "数据类型：" in description
            assert not description.startswith("名称为 ")

        request_body = cast(dict[str, object] | None, operation.get("requestBody"))
        if request_body is not None:
            description = str(request_body.get("description", ""))
            assert _has_chinese(description)
            assert "是否必传：" in description
            assert "数据类型：" in description

        for response in cast(
            dict[str, dict[str, object]], operation.get("responses", {})
        ).values():
            assert _has_chinese(response.get("description"))

    components = cast(dict[str, object], schema.get("components", {}))
    component_schemas = cast(
        dict[str, dict[str, object]], components.get("schemas", {})
    )
    for component in component_schemas.values():
        properties = cast(
            dict[str, dict[str, object]], component.get("properties", {})
        )
        for field in properties.values():
            description = str(field.get("description", ""))
            assert _has_chinese(description)
            assert any(
                label in description
                for label in (
                    "是否必传：",
                    "是否一定返回：",
                    "请求和返回中是否必须有：",
                )
            )
            assert "数据类型：" in description
            assert not description.startswith("名称为 ")

    for description in _description_texts(schema):
        normalized = description.casefold()
        assert not any(term in normalized for term in FORBIDDEN_DESCRIPTION_TERMS)


def test_workflow_and_scheduler_swagger_is_plain_chinese() -> None:
    """工作流和调度接口必须显示中文用途、必传状态和数据类型。"""

    application = FastAPI()
    install_openapi_documentation(application)
    workflow_service = cast(WorkflowService, object())
    application.include_router(create_workflow_router(workflow_service))
    application.include_router(create_operation_category_router(workflow_service))
    application.include_router(
        create_workflow_template_router(WorkflowTemplateQueryService(object()))
    )
    application.include_router(
        create_scheduler_router(
            lambda: None,
            include_execution_shaped_workflow_routes=False,
        )
    )

    schema = application.openapi()

    _assert_every_public_item_is_documented(schema)
    task_path = cast(
        dict[str, dict[str, object]],
        cast(dict[str, object], schema["paths"])["/api/v1/workflow-tasks"],
    )
    task_create = task_path["post"]
    assert task_create["summary"] == "创建工作流任务"
    assert "是否必传：是" in str(
        cast(dict[str, object], task_create["requestBody"])["description"]
    )
    models = cast(
        dict[str, dict[str, object]],
        cast(dict[str, object], schema["components"])["schemas"],
    )
    task_model = cast(
        dict[str, dict[str, object]], models["WorkflowTaskCreateRequest"]["properties"]
    )
    assert "数据类型：字符串" in str(task_model["workflow_uuid"]["description"])
    assert "是否必传：是" in str(task_model["workflow_uuid"]["description"])
    assert "数据类型：字符串" in str(task_model["priority"]["description"])
    assert "是否必传：否" in str(task_model["priority"]["description"])
    workflow_read_model = cast(
        dict[str, dict[str, object]], models["WorkflowReadModel"]["properties"]
    )
    assert "是否一定返回：是" in str(
        workflow_read_model["uuid"]["description"]
    )


def test_inventory_and_material_swagger_is_plain_chinese() -> None:
    """动态挂载的库存、物料和实验室布局接口也必须使用同一说明规则。"""

    store = InventoryStore(":memory:")
    inventory = InventoryService(store)
    resources = BackendResourceService(
        store,
        edge_id=inventory.edge_id,
        lab_id=inventory.lab_id,
    )
    application = FastAPI()
    install_openapi_documentation(application)
    application.include_router(create_backend_resource_router(resources))
    application.include_router(create_inventory_router(inventory))
    application.include_router(create_legacy_material_router(inventory))
    application.include_router(create_lab_router(inventory))

    schema = application.openapi()

    _assert_every_public_item_is_documented(schema)
    paths = cast(dict[str, dict[str, dict[str, object]]], schema["paths"])
    material_list = paths["/api/v1/materials"]["get"]
    assert material_list["summary"] == "查询物料列表"
    page_parameter = next(
        parameter
        for parameter in cast(list[dict[str, object]], material_list["parameters"])
        if parameter["name"] == "page"
    )
    assert "是否必传：否" in str(page_parameter["description"])
    assert "数据类型：整数" in str(page_parameter["description"])


def test_device_task_communication_swagger_is_plain_chinese(tmp_path: Path) -> None:
    """动态挂载的设备任务通信接口也必须显示同一套中文说明。"""

    authority = LocalEdgeControlAuthority(
        LocalEdgeAuthorityStore(tmp_path / "edge-control.db"),
        api_key="test-key",
    )
    application = FastAPI()
    install_openapi_documentation(application)
    application.include_router(create_local_edge_control_router(authority))

    schema = application.openapi()

    _assert_every_public_item_is_documented(schema)
    paths = cast(dict[str, dict[str, dict[str, object]]], schema["paths"])
    update_status = paths[
        "/api/v1/edge/sessions/{session_uuid}/devices/{local_device_id}/status"
    ]["put"]
    assert update_status["summary"] == "更新设备在线状态"
    session_parameter = next(
        parameter
        for parameter in cast(list[dict[str, object]], update_status["parameters"])
        if parameter["name"] == "session_uuid"
    )
    assert "设备运行进程会话" in str(session_parameter["description"])
    assert "是否必传：是" in str(session_parameter["description"])


def test_main_web_server_has_documented_openapi_installed() -> None:
    """主 Web 服务创建时必须安装 Swagger 中文说明。"""

    from unilabos.app.web import server

    assert server.app.state.plain_chinese_openapi_installed is True
