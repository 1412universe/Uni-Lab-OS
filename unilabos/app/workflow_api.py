"""Thin FastAPI adapter for the Backend-shaped local Workflow authority."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, FastAPI, Header, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from unilabos.app.workflow_template_api import (
    TemplateSnapshotProvider,
    WorkflowTemplateQueryService,
    create_workflow_template_router,
)
from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.models import (
    WorkflowEdgeWrite,
    WorkflowNodeWrite,
    normalize_json_array,
    normalize_json_object,
    validate_uuid,
)
from unilabos.workflow.service import WorkflowError, WorkflowService


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _BackendModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


HashToken = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
_SIGNED_DECIMAL = re.compile(r"[+-]?[0-9]+\Z")
_INT64_MAX = (1 << 63) - 1
_WORKFLOW_BODY_LIMIT = 8 * 1024 * 1024
_WORKFLOW_JSON_INTEGER_DIGITS = 4096
_GO_WHITE_SPACE = (
    "\t\n\v\f\r "
    "\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


async def _read_limited_body(request: Request) -> bytes:
    """增量读取工作流（Workflow）请求体并在首次超限时停止。

    参数说明：`request` 是当前 ASGI 请求。函数先校验声明长度，再逐块读取，
    最多保留 8 MiB；返回缓存后的原始字节，超限或非法长度抛出 `ValueError`。
    """

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length, 10)
        except ValueError:
            raise ValueError("Content-Length 无效") from None
        if declared_length < 0 or declared_length > _WORKFLOW_BODY_LIMIT:
            raise ValueError("工作流请求体超过公共预算")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _WORKFLOW_BODY_LIMIT:
            raise ValueError("工作流请求体超过公共预算")
        body.extend(chunk)
    payload = bytes(body)
    request._body = payload
    return payload


class _BackendJSONRoute(APIRoute):
    """限制有请求体的路由，并按后端（Backend）规则预载 JSON。"""

    def get_route_handler(self):
        """构造只对有请求体路由执行预算与 JSON 解码的处理器。"""

        route_handler = super().get_route_handler()
        expects_body = self.body_field is not None

        async def backend_json_route_handler(request: Request) -> Response:
            """在业务处理前校验请求体；`request` 是单次 HTTP 请求。"""

            if expects_body:
                content_type = request.headers.get("content-type", "")
                mime = content_type.split(";", 1)[0].strip().lower()
                try:
                    body = await _read_limited_body(request)
                    if mime == "application/json" or mime.endswith("+json"):
                        request._json = decode_json_bytes(
                            body,
                            max_integer_digits=_WORKFLOW_JSON_INTEGER_DIGITS,
                        )
                except (
                    OverflowError,
                    UnicodeError,
                    ValueError,
                ):
                    return _error(WorkflowError("invalid_input"))
            return await route_handler(request)

        return backend_json_route_handler


def _parse_non_negative_int64_decimal(value: str) -> int:
    """按后端（Backend）规则解析 SSE 游标。

    参数：``value`` 是已按 Go 空白规则裁剪的十进制文本。返回：非负 int64。
    异常：格式、负值或上溢时抛 ``ValueError``；不接受小数或指数形式。
    """

    if _SIGNED_DECIMAL.fullmatch(value) is None:
        raise ValueError
    negative = value.startswith("-")
    digits = value[1:] if value[:1] in {"+", "-"} else value
    significant = digits.lstrip("0") or "0"
    if negative and significant != "0":
        raise ValueError
    maximum = str(_INT64_MAX)
    if len(significant) > len(maximum) or (
        len(significant) == len(maximum) and significant > maximum
    ):
        raise ValueError
    return int(significant, 10)


def _parse_positive_decimal(value: str, *, maximum: int) -> int:
    """解析严格正十进制页长并限制公开上界。"""

    if _SIGNED_DECIMAL.fullmatch(value) is None or value.startswith("-"):
        raise ValueError
    digits = value[1:] if value.startswith("+") else value
    significant = digits.lstrip("0") or "0"
    if significant == "0":
        raise ValueError
    maximum_text = str(maximum)
    if len(significant) > len(maximum_text) or (
        len(significant) == len(maximum_text) and significant > maximum_text
    ):
        raise ValueError
    return int(significant, 10)


class WorkflowCreateRequest(_BackendModel):
    name: str
    tags: List[Any] = Field(default_factory=list)
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("tags", mode="before")
    @classmethod
    def _json_array(cls, value: Any) -> List[Any]:
        return normalize_json_array(value)

    @field_validator("meta_data", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        return normalize_json_object(value)


class WorkflowUpdateRequest(WorkflowCreateRequest):
    pass


class GraphWriteRequest(_BackendModel):
    revision: int = Field(ge=1, le=_INT64_MAX, strict=True)
    nodes: List[WorkflowNodeWrite] = Field(default_factory=list)
    edges: List[WorkflowEdgeWrite] = Field(default_factory=list)

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def _json_array(cls, value: Any) -> List[Any]:
        return [] if value is None else value


class WorkflowNodeCreateRequest(_StrictModel):
    """向现有工作流增加一个节点的公共 DTO。"""

    workflow_node_template_uuid: Optional[str] = None
    parent_uuid: Optional[str] = None
    material_uuid: Optional[str] = None
    name: str = ""
    type: str = ""
    pose: Dict[str, Any] = Field(default_factory=dict)
    param: Optional[Dict[str, Any]] = None
    execution_policy: Dict[str, Any] = Field(default_factory=dict)
    disabled: bool = False
    minimized: bool = False
    script: Optional[str] = None
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("pose", "execution_policy", "meta_data", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        return normalize_json_object(value)


class WorkflowNodePatchRequest(_StrictModel):
    """Backend 允许局部更新的节点字段。"""

    parent_uuid: Optional[str] = None
    material_uuid: Optional[str] = None
    name: Optional[str] = None
    pose: Optional[Dict[str, Any]] = None
    param: Optional[Dict[str, Any]] = None
    execution_policy: Optional[Dict[str, Any]] = None
    disabled: Optional[bool] = None
    minimized: Optional[bool] = None
    script: Optional[str] = None
    description: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None


class WorkflowEdgeCreateRequest(_BackendModel):
    """向完整图增加一条依赖或数据连线的公共 DTO。"""

    source_node_uuid: str
    target_node_uuid: str
    source_handle_uuid: str
    target_handle_uuid: str
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("meta_data", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        return normalize_json_object(value)


class WorkflowBatchDeleteRequest(_BackendModel):
    """原子删除一组节点和连线的公共 DTO。"""

    node_uuids: List[str] = Field(default_factory=list)
    edge_uuids: List[str] = Field(default_factory=list)


class WorkflowDuplicateRequest(_BackendModel):
    """工作流或节点复制时可选的新名称。"""

    name: Optional[str] = None


class LegacyWorkflowImportRequest(_BackendModel):
    """兼容直接对象和 ``data`` 包装的旧版工作流导入载荷。"""

    workflow_name: str = ""
    name: str = ""
    tags: List[Any] = Field(default_factory=list)
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    inventory_requirements: List[Dict[str, Any]] = Field(default_factory=list)
    data: Optional[Dict[str, Any]] = None


class PublishWorkflowContractRequest(_StrictModel):
    """冻结指定工作流修订的发布命令。"""

    revision: int = Field(ge=1, le=_INT64_MAX, strict=True)


class InsertCompositeWorkflowRequest(_StrictModel):
    """在父图中插入一个已发布工作流调用。"""

    revision: int = Field(ge=1, le=_INT64_MAX, strict=True)
    contract_uuid: str
    invocation_uuid: Optional[str] = None
    device_bindings: Dict[str, str] = Field(default_factory=dict)
    pose: Dict[str, Any] = Field(default_factory=dict)
    param: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("device_bindings", "pose", "param", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        """规范化组合调用的对象字段并拒绝数组或标量。"""

        return normalize_json_object(value)


class WorkflowInventoryBindingRequest(_StrictModel):
    """把一个逻辑库存需求绑定到本次任务实际使用的库存实例。"""

    requirement_key: str
    inventory_type: Literal["reagent", "current_substance"]
    inventory_uuid: str
    reserved_quantity: float = Field(gt=0, allow_inf_nan=False)
    quantity_unit: str

    @field_validator("inventory_uuid")
    @classmethod
    def _inventory_uuid(cls, value: str) -> str:
        """规范库存实例 UUID；nil 或非法值由 Pydantic 映射为请求错误。"""

        return validate_uuid(value)

    @field_validator("requirement_key", "quantity_unit")
    @classmethod
    def _required_text(cls, value: str) -> str:
        """去除两端空白并拒绝空逻辑键或单位。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class WorkflowTaskCreateRequest(_BackendModel):
    workflow_uuid: str
    run_mode: str = "normal"
    target_node_uuid: Optional[str] = None
    input: Dict[str, Any] = Field(default_factory=dict)
    inventory_bindings: List[WorkflowInventoryBindingRequest] = Field(
        default_factory=list
    )
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("input", "meta_data", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        """规范化任务输入和公开元数据对象。

        参数：``value`` 是 Pydantic 解码前的 JSON 值。返回：独立 JSON 对象，
        显式 ``null`` 按后端（Backend）零值对象处理。异常：非对象或非法 JSON
        值由 ``normalize_json_object`` 抛出并映射为请求错误。
        """

        return normalize_json_object(value)


class DebugWorkflowTaskCreateRequest(_BackendModel):
    workflow_uuid: str
    start_node_uuids: List[str]
    breakpoint_node_uuids: List[str] = Field(default_factory=list)
    input: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("start_node_uuids", "breakpoint_node_uuids", mode="before")
    @classmethod
    def _json_array(cls, value: Any) -> List[Any]:
        return normalize_json_array(value)

    @field_validator("input", "meta_data", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        return normalize_json_object(value)


class DebugCommandScope(_StrictModel):
    type: str
    hold_uuid: str


class DebugWorkflowTaskCommandRequest(_StrictModel):
    type: str
    scope: DebugCommandScope
    idempotency_key: str


class WorkflowTaskCommandRequest(_StrictModel):
    type: str
    target_node_uuid: Optional[str] = None
    idempotency_key: str
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("meta_data", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        return normalize_json_object(value)


class DeviceActionRunCreateRequest(_StrictModel):
    """Backend 规范的设备单动作运行（DeviceActionRun）创建 DTO。"""

    material_uuid: str
    workflow_node_template_uuid: str
    param: Optional[Dict[str, Any]] = None
    execution_policy: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("execution_policy", "meta_data", mode="before")
    @classmethod
    def _json_object(cls, value: Any) -> Dict[str, Any]:
        """规范化设备动作请求中的可选 JSON 对象。"""

        return normalize_json_object(value)


class ManualConfirmationDecisionRequest(_StrictModel):
    """人工确认批准或拒绝的公共 DTO。"""

    action: str
    confirmed_by: str
    comment: Optional[str] = None
    idempotency_key: str
    param: Optional[Dict[str, Any]] = None


class WorkflowInterventionDecisionRequest(_StrictModel):
    """按修订选择一个设备已经提供的干预方案。"""

    revision: int = Field(ge=1, le=_INT64_MAX, strict=True)
    option_id: str
    # Local 模式兼容需要服务端执行的兜底动作结果；普通 retry/skip/abort 不传。
    result: Any = None


class UncertainJobResolutionRequest(_StrictModel):
    """执行未知作业的安全人工处置请求。"""

    resolution: str
    reason: str
    device_command_id: Optional[str] = None


class DraftWriteRequest(_StrictModel):
    python_source: str
    expected_draft_hash: Optional[HashToken]
    expected_workflow_revision: int = Field(
        ge=1,
        le=_INT64_MAX,
        strict=True,
    )


class ApplyRequest(_StrictModel):
    """只携带服务端签发候选哈希（Candidate Hash）的应用命令。"""

    candidate_hash: HashToken


class _BackendJSONResponse(JSONResponse):
    """Render deeply nested Backend JSON without process-global recursion state."""

    def render(self, content: Any) -> bytes:
        return encode_json(content)


def _public_data(data: Any) -> Any:
    """递归移除后端（Backend）迁移已删除的公共字段。

    参数：``data`` 是服务层投影或嵌套集合。返回：不共享容器的公共投影；任务
    输入保留，尚未进入当前迁移合同的输出隐藏。异常：无。
    """

    if isinstance(data, list):
        return [_public_data(value) for value in data]
    if not isinstance(data, dict):
        return data
    result = {key: _public_data(value) for key, value in data.items()}
    if "workflow_snapshot" in result and "workflow_uuid" in result:
        result.pop("output", None)
    if "workflow_uuid" in result and "pose" in result and "param" in result:
        result.pop("status", None)
    return result


def _success(data: Any = None, *, status: int = 200) -> _BackendJSONResponse:
    content: Dict[str, Any] = {"code": 0}
    if data is not None:
        content["data"] = _public_data(data)
    return _BackendJSONResponse(status_code=status, content=content)


def _error(error: WorkflowError) -> _BackendJSONResponse:
    conflict_codes = {
        "conflict",
        "draft_hash_conflict",
        "workflow_revision_conflict",
        "candidate_hash_conflict",
        "template_catalog_conflict",
        "candidate_not_ready",
        "draft_invalid",
        "candidate_invalid",
        "workflow_identity_mismatch",
    }
    if error.code == "invalid_input":
        business_code = 1000
    elif error.code in {"not_found", "workflow_not_found"}:
        business_code = 3002
    elif error.code in conflict_codes:
        business_code = 3003
    elif error.code == "template_catalog_unavailable":
        business_code = 5001
    else:
        business_code = 1
    error_content = {"msg": error.message}
    if error.code == "workflow_identity_mismatch":
        # product Backend 包络保持 HTTP 200；该窄符号码让前端区分身份拒绝与
        # 需要重读远端版本的普通 3003 CAS 冲突。
        error_content["code"] = error.code
    return _BackendJSONResponse(
        status_code=200,
        content={
            "code": business_code,
            "error": error_content,
        },
    )


def format_sse_event(event: Dict[str, Any]) -> str:
    """把一个持久失效通知编码为服务器发送事件（SSE）帧。

    参数：``event`` 含全局序号、事件类型和小型身份载荷。返回：UTF-8 文本帧；
    客户端必须再用 REST 复原（Rehydrate）权威事实。异常：记录缺字段或载荷不能
    JSON 编码时传播，不从内存历史补值。
    """

    payload = json.dumps(
        event["data"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event['id']}\nevent: {event['event']}\ndata: {payload}\n\n"


def create_workflow_router(service: WorkflowService) -> APIRouter:
    """围绕唯一工作流权威创建 Backend-shaped HTTP Router。

    参数：``service`` 是注入的工作流应用服务。返回同时承载工作流、任务、作业、
    设备单动作运行（DeviceActionRun）及创作接口的 FastAPI Router。
    """

    router = APIRouter(
        prefix="/api/v1",
        tags=["workflow"],
        route_class=_BackendJSONRoute,
    )

    @router.post("/workflows")
    def create_workflow(body: WorkflowCreateRequest) -> JSONResponse:
        return _success(
            service.create_workflow(**body.model_dump()),
            status=201,
        )

    @router.post("/workflows/import")
    def import_legacy_workflow(
        body: LegacyWorkflowImportRequest,
    ) -> JSONResponse:
        """校验并在一个事务中导入旧版工作流图。"""

        return _success(
            service.import_legacy_workflow(payload=body.model_dump()),
            status=201,
        )

    @router.post("/local/workflows/import-python", status_code=201)
    def import_python_workflow(
        python_file: Annotated[bytes, Body(media_type="text/x-python")],
        file_name: Annotated[str, Header(alias="X-Workflow-Filename")],
    ) -> JSONResponse:
        """静态校验并原子导入一个 Local 模式 Python 工作流文件。

        参数：请求体是原始 ``.py`` 文件字节，``X-Workflow-Filename`` 是不含路径
        的文件名。返回：新建工作流完整图的统一响应。异常：非 UTF-8、非法文件
        名、AST/模板/图校验失败或身份冲突由公共错误适配器处理。
        """

        try:
            python_source = python_file.decode("utf-8")
        except UnicodeDecodeError:
            raise WorkflowError("invalid_input") from None
        return _success(
            service.import_python_workflow(
                file_name=file_name,
                python_source=python_source,
            ),
            status=201,
        )

    @router.get("/workflows")
    def list_workflows(
        page: int = Query(default=1),
        page_size: int = Query(default=20),
        keyword: str = Query(default=""),
    ) -> JSONResponse:
        result = service.list_workflows(
            page=page,
            page_size=page_size,
            name=keyword,
        )
        return _success(
            {
                "items": result["items"],
                "has_more": result["page"] * result["page_size"]
                < result["total"],
                "page": result["page"],
                "page_size": result["page_size"],
            }
        )

    @router.get("/published-workflow-contracts")
    def list_published_workflow_contracts(
        page: int = Query(default=1),
        page_size: int = Query(default=20),
        keyword: str = Query(default=""),
    ) -> JSONResponse:
        """返回每个来源工作流最新的不可变发布合同。"""

        return _success(
            service.list_published_workflow_contracts(
                page=page,
                page_size=page_size,
                keyword=keyword,
            )
        )

    @router.get("/workflows/{workflow_uuid}")
    def get_workflow(workflow_uuid: str) -> JSONResponse:
        return _success(service.get_workflow(workflow_uuid))

    @router.put("/workflows/{workflow_uuid}")
    def update_workflow(
        workflow_uuid: str,
        body: WorkflowUpdateRequest,
    ) -> JSONResponse:
        return _success(service.update_workflow(workflow_uuid, **body.model_dump()))

    @router.delete("/workflows/{workflow_uuid}")
    def delete_workflow(workflow_uuid: str) -> JSONResponse:
        service.delete_workflow(workflow_uuid)
        return _success()

    @router.get("/workflows/{workflow_uuid}/graph")
    def get_graph(workflow_uuid: str) -> JSONResponse:
        return _success(service.get_graph(workflow_uuid))

    @router.put("/workflows/{workflow_uuid}/graph")
    def save_graph(
        workflow_uuid: str,
        body: GraphWriteRequest,
    ) -> JSONResponse:
        return _success(
            service.save_graph(
                workflow_uuid,
                revision=body.revision,
                nodes=body.nodes,
                edges=body.edges,
            )
        )

    @router.post("/workflows/{workflow_uuid}/nodes")
    def create_workflow_node(
        workflow_uuid: str,
        body: WorkflowNodeCreateRequest,
    ) -> JSONResponse:
        """增加节点，并由完整图校验器原子推进修订。"""

        return _success(
            service.create_workflow_node(
                workflow_uuid,
                payload=body.model_dump(),
            ),
            status=201,
        )

    @router.get("/workflows/{workflow_uuid}/nodes")
    def list_workflow_nodes(
        workflow_uuid: str,
        page: int = Query(default=1),
        page_size: int = Query(default=20),
        workflow_node_template_uuid: Optional[str] = Query(default=None),
        material_uuid: Optional[str] = Query(default=None),
    ) -> JSONResponse:
        """分页查询指定工作流的节点。"""

        return _success(
            service.list_workflow_nodes(
                workflow_uuid,
                page=page,
                page_size=page_size,
                workflow_node_template_uuid=workflow_node_template_uuid,
                material_uuid=material_uuid,
            )
        )

    @router.post("/workflows/{workflow_uuid}/edges")
    def create_workflow_edge(
        workflow_uuid: str,
        body: WorkflowEdgeCreateRequest,
    ) -> JSONResponse:
        """增加连线，并复用完整图引用与环路校验。"""

        return _success(
            service.create_workflow_edge(
                workflow_uuid,
                payload=body.model_dump(),
            ),
            status=201,
        )

    @router.post("/workflows/{workflow_uuid}/batch-delete")
    def batch_delete_workflow_graph(
        workflow_uuid: str,
        body: WorkflowBatchDeleteRequest,
    ) -> JSONResponse:
        """一次删除多个节点与连线，失败时整图不变。"""

        return _success(
            service.batch_delete_workflow_graph(
                workflow_uuid,
                node_uuids=body.node_uuids,
                edge_uuids=body.edge_uuids,
            )
        )

    @router.post("/workflows/{workflow_uuid}/duplicate")
    def duplicate_workflow(
        workflow_uuid: str,
        body: WorkflowDuplicateRequest,
    ) -> JSONResponse:
        """在单个 SQLite 事务中复制工作流和完整图。"""

        return _success(
            service.duplicate_workflow(workflow_uuid, name=body.name),
            status=201,
        )

    @router.post("/workflows/{workflow_uuid}/publications")
    def publish_workflow_contract(
        workflow_uuid: str,
        body: PublishWorkflowContractRequest,
    ) -> JSONResponse:
        """幂等冻结与当前修订一致的工作流合同。"""

        return _success(
            service.publish_workflow_contract(
                workflow_uuid,
                revision=body.revision,
            ),
            status=201,
        )

    @router.post("/workflows/{workflow_uuid}/composite-invocations")
    def insert_composite_workflow(
        workflow_uuid: str,
        body: InsertCompositeWorkflowRequest,
    ) -> JSONResponse:
        """原子插入并确定性展开一个不可变组合工作流调用。"""

        return _success(
            service.insert_composite_workflow(
                workflow_uuid,
                revision=body.revision,
                contract_uuid=body.contract_uuid,
                invocation_uuid=body.invocation_uuid,
                device_bindings=body.device_bindings,
                pose=body.pose,
                param=body.param,
            )
        )

    @router.get("/workflows/{workflow_uuid}/run-preflight")
    def get_workflow_run_preflight(
        workflow_uuid: str,
        run_mode: str = Query(default="normal"),
        target_node_uuid: Optional[str] = Query(default=None),
    ) -> JSONResponse:
        """返回不创建任务、不占用资源的候选运行检查报告。"""

        response = _success(
            service.get_workflow_run_preflight(
                workflow_uuid,
                run_mode=run_mode,
                target_node_uuid=target_node_uuid,
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/workflow-nodes/{node_uuid}")
    def get_workflow_node(node_uuid: str) -> JSONResponse:
        return _success(service.get_workflow_node(node_uuid))

    @router.patch("/workflow-nodes/{node_uuid}")
    def patch_workflow_node(
        node_uuid: str,
        body: WorkflowNodePatchRequest,
    ) -> JSONResponse:
        return _success(
            service.patch_workflow_node(
                node_uuid,
                patch=body.model_dump(exclude_unset=True),
            )
        )

    @router.delete("/workflow-nodes/{node_uuid}")
    def delete_workflow_node(node_uuid: str) -> JSONResponse:
        service.delete_workflow_node(node_uuid)
        return _success()

    @router.post("/workflow-nodes/{node_uuid}/duplicate")
    def duplicate_workflow_node(
        node_uuid: str,
        body: WorkflowDuplicateRequest,
    ) -> JSONResponse:
        return _success(
            service.duplicate_workflow_node(node_uuid, name=body.name),
            status=201,
        )

    @router.delete("/workflow-edges/{edge_uuid}")
    def delete_workflow_edge(edge_uuid: str) -> JSONResponse:
        service.delete_workflow_edge(edge_uuid)
        return _success()

    @router.post("/workflow-tasks")
    def create_workflow_task(
        body: WorkflowTaskCreateRequest,
    ) -> JSONResponse:
        """通过公共接口创建一次工作流任务（WorkflowTask）。

        参数：``body`` 携带工作流身份、运行模式和任务输入。返回：HTTP 201 的
        标准任务投影，包含已规范化输入与冻结执行计划（ExecutionPlan）。异常：
        服务层稳定错误由应用异常处理器转换为后端业务响应。
        """

        return _success(
            service.create_workflow_task(
                workflow_uuid=body.workflow_uuid,
                run_mode=body.run_mode,
                target_node_uuid=body.target_node_uuid,
                input_value=body.input,
                description=body.description,
                meta_data=body.meta_data,
                inventory_bindings=[
                    binding.model_dump() for binding in body.inventory_bindings
                ],
            ),
            status=201,
        )

    @router.get("/workflow-tasks/{task_uuid}/inventory-consumptions")
    def list_task_inventory_consumptions(task_uuid: str) -> JSONResponse:
        """读取任务级数量型库存消费事实。"""

        return _success(service.list_task_inventory_consumptions(task_uuid))

    @router.get("/workflow-node-jobs/{job_uuid}/inventory-consumptions")
    def list_job_inventory_consumptions(job_uuid: str) -> JSONResponse:
        """读取作业级数量型库存消费事实。"""

        return _success(service.list_job_inventory_consumptions(job_uuid))

    @router.get("/reagents/{reagent_uuid}/inventory-consumptions")
    def list_reagent_inventory_consumptions(reagent_uuid: str) -> JSONResponse:
        """读取试剂库存实例的工作流消费谱系。"""

        return _success(service.list_reagent_inventory_consumptions(reagent_uuid))

    @router.post("/debug/workflow-tasks")
    def create_debug_workflow_task(
        body: DebugWorkflowTaskCreateRequest,
    ) -> JSONResponse:
        """以不可变起始点和断点配置创建标准工作流任务。"""

        return _success(
            service.create_debug_workflow_task(
                workflow_uuid=body.workflow_uuid,
                start_node_uuids=body.start_node_uuids,
                breakpoint_node_uuids=body.breakpoint_node_uuids,
                input_value=body.input,
                description=body.description,
                meta_data=body.meta_data,
            ),
            status=201,
        )

    @router.get("/debug/workflow-tasks/{task_uuid}")
    def get_debug_workflow_task(task_uuid: str) -> JSONResponse:
        return _success(service.get_debug_workflow_task(task_uuid))

    @router.post("/debug/workflow-tasks/{task_uuid}/commands")
    def command_debug_workflow_task(
        task_uuid: str,
        body: DebugWorkflowTaskCommandRequest,
    ) -> JSONResponse:
        return _success(
            service.command_debug_workflow_task(
                task_uuid,
                command_type=body.type,
                scope_type=body.scope.type,
                hold_uuid=body.scope.hold_uuid,
                idempotency_key=body.idempotency_key,
            ),
            status=201,
        )

    @router.post("/device-action-runs")
    def create_device_action_run(
        body: DeviceActionRunCreateRequest,
    ) -> JSONResponse:
        """创建或幂等复用一次设备单动作运行（DeviceActionRun）。

        参数：``body`` 完全采用 Backend DTO。返回标准工作流任务（WorkflowTask）
        与唯一工作流节点作业（WorkflowNodeJob）；首次创建为 HTTP 201，复用为 200。
        """

        result = service.create_device_action_run(**body.model_dump())
        return _success(result, status=201 if result["created"] else 200)

    @router.get("/workflow-tasks")
    def list_workflow_tasks(
        page: int = Query(default=1),
        page_size: int = Query(default=20),
        workflow_uuid: Optional[str] = Query(default=None),
        execution_kind: str = Query(default=""),
        status: str = Query(default=""),
        cleanup_status: str = Query(default=""),
    ) -> JSONResponse:
        """按 Backend 筛选合同分页返回工作流任务（WorkflowTask）。

        参数包括分页、可选工作流 UUID、执行来源、业务状态和清理状态；返回标准
        分页 envelope，其中直接设备动作可用 ``ad_hoc_device_action`` 单独查询。
        """

        return _success(
            service.list_workflow_tasks(
                page=page,
                page_size=page_size,
                workflow_uuid=workflow_uuid,
                execution_kind=execution_kind,
                status=status,
                cleanup_status=cleanup_status,
            )
        )

    @router.get("/workflow-tasks/{task_uuid}")
    def get_workflow_task(task_uuid: str) -> JSONResponse:
        return _success(service.get_workflow_task(task_uuid))

    @router.post("/workflow-tasks/{task_uuid}/commands")
    def command_workflow_task(
        task_uuid: str,
        body: WorkflowTaskCommandRequest,
    ) -> JSONResponse:
        """幂等提交一次工作流任务控制命令。"""

        return _success(
            service.command_workflow_task(
                task_uuid,
                command_type=body.type,
                target_node_uuid=body.target_node_uuid,
                idempotency_key=body.idempotency_key,
                description=body.description,
                meta_data=body.meta_data,
            ),
            status=201,
        )

    @router.get("/workflow-tasks/{task_uuid}/jobs")
    def list_workflow_node_jobs(task_uuid: str) -> JSONResponse:
        return _success(service.list_workflow_node_jobs(task_uuid))

    @router.get("/workflow-tasks/{task_uuid}/manual-confirmations")
    def list_task_manual_confirmations(task_uuid: str) -> JSONResponse:
        return _success(service.list_task_manual_confirmations(task_uuid))

    @router.get("/workflow-tasks/{task_uuid}/events")
    def list_workflow_task_runtime_events(
        task_uuid: str,
        after_sequence: str = Query(default=""),
        limit: str = Query(default=""),
    ) -> JSONResponse:
        """分页返回持久任务运行日志，包括动作下发与明确执行结果。"""

        try:
            after_text = after_sequence.strip(_GO_WHITE_SPACE)
            limit_text = limit.strip(_GO_WHITE_SPACE)
            parsed_after = (
                _parse_non_negative_int64_decimal(after_text) if after_text else 0
            )
            parsed_limit = (
                _parse_positive_decimal(limit_text, maximum=500) if limit_text else 100
            )
        except ValueError:
            raise WorkflowError("invalid_input") from None
        return _success(
            service.list_workflow_task_runtime_events(
                task_uuid,
                after_sequence=parsed_after,
                limit=parsed_limit,
            )
        )

    @router.get("/workflow-node-jobs/{job_uuid}")
    def get_workflow_node_job(job_uuid: str) -> JSONResponse:
        return _success(service.get_workflow_node_job(job_uuid))

    @router.get("/workflow-node-jobs/{job_uuid}/feedback")
    def list_workflow_node_job_feedback(
        job_uuid: str,
        page: int = Query(default=1),
        page_size: int = Query(default=20),
    ) -> JSONResponse:
        """返回作业已经提交的有序过程反馈。"""

        return _success(
            service.list_workflow_node_job_feedback(
                job_uuid,
                page=page,
                page_size=page_size,
            )
        )

    @router.post("/workflow-node-jobs/{job_uuid}/resolve-uncertain")
    def resolve_uncertain_workflow_node_job(
        job_uuid: str,
        body: UncertainJobResolutionRequest,
    ) -> JSONResponse:
        result = service.resolve_uncertain_job(
            job_uuid,
            resolution=body.resolution,
            reason=body.reason,
            device_command_id=body.device_command_id,
        )
        return _success(
            result,
            status=202 if result["pending_edge_confirmation"] else 200,
        )

    @router.get("/workflow-manual-confirmations/{confirmation_uuid}")
    def get_manual_confirmation(confirmation_uuid: str) -> JSONResponse:
        return _success(service.get_manual_confirmation(confirmation_uuid))

    @router.post("/workflow-manual-confirmations/{confirmation_uuid}/decision")
    def decide_manual_confirmation(
        confirmation_uuid: str,
        body: ManualConfirmationDecisionRequest,
    ) -> JSONResponse:
        return _success(
            service.decide_manual_confirmation(
                confirmation_uuid,
                action=body.action,
                confirmed_by=body.confirmed_by,
                comment=body.comment,
                idempotency_key=body.idempotency_key,
                param=body.param,
            )
        )

    @router.get("/workflow-interventions")
    def list_workflow_interventions(
        status: str = Query(default="open"),
        limit: int = Query(default=100),
    ) -> JSONResponse:
        """按状态查询等待处理或已经处理的工作流干预。"""

        return _success(
            service.list_workflow_interventions(status=status, limit=limit)
        )

    @router.get("/workflow-interventions/{intervention_uuid}")
    def get_workflow_intervention(intervention_uuid: str) -> JSONResponse:
        return _success(service.get_workflow_intervention(intervention_uuid))

    @router.post("/workflow-interventions/{intervention_uuid}/decisions")
    def select_workflow_intervention(
        intervention_uuid: str,
        body: WorkflowInterventionDecisionRequest,
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> JSONResponse:
        result = service.select_workflow_intervention(
            intervention_uuid,
            revision=body.revision,
            option_id=body.option_id,
            idempotency_key=idempotency_key,
            result=body.result,
        )
        return _success(result, status=201 if result["created"] else 200)

    @router.get("/workflows/{workflow_uuid}/authoring")
    def get_authoring(workflow_uuid: str) -> JSONResponse:
        return _success(service.get_authoring(workflow_uuid))

    @router.put("/workflows/{workflow_uuid}/authoring/draft")
    def save_draft(
        workflow_uuid: str,
        body: DraftWriteRequest,
    ) -> JSONResponse:
        return _success(
            service.save_draft(
                workflow_uuid,
                python_source=body.python_source,
                expected_draft_hash=body.expected_draft_hash,
                expected_workflow_revision=body.expected_workflow_revision,
            )
        )

    @router.post("/workflows/{workflow_uuid}/authoring/apply")
    def apply_authoring(
        workflow_uuid: str,
        body: ApplyRequest,
    ) -> JSONResponse:
        """应用服务端持久候选并返回后端形状响应。

        参数：``workflow_uuid`` 是工作流（Workflow）身份；``body`` 只允许包含
        候选哈希（Candidate Hash）。返回：统一后端响应外层。异常：请求字段或
        领域前置条件错误由公共异常处理器转换成稳定业务错误。
        """

        return _success(
            service.apply_authoring(
                workflow_uuid,
                candidate_hash=body.candidate_hash,
            )
        )

    @router.get("/events")
    async def events(
        request: Request,
        last_event_id: Optional[str] = Header(
            default=None,
            alias="Last-Event-ID",
        ),
    ) -> Response:
        """从持久全局游标建立只作失效通知的 SSE 流。

        参数：``request`` 提供断开状态，``last_event_id`` 是规范请求头；为避免
        框架合并重复头，原始 ASGI 头仍由适配器唯一解析。返回：非法游标的稳定
        错误或从排他游标续传的事件流。异常：持久读取/编码错误终止当前流；不从
        MonitorBus 环形缓冲恢复，也不从 SSE 重建业务状态。
        """

        try:
            raw_cursor = next(
                (
                    value
                    for name, value in request.scope["headers"]
                    if name.lower() == b"last-event-id"
                ),
                None,
            )
            cursor_text = (
                raw_cursor.decode("utf-8")
                if raw_cursor is not None
                else (last_event_id or "")
            ).strip(_GO_WHITE_SPACE)
            if not cursor_text:
                cursor = 0
            else:
                cursor = _parse_non_negative_int64_decimal(cursor_text)
        except (UnicodeError, ValueError):
            cursor = -1
        if cursor == -1:
            return _error(WorkflowError("invalid_input"))

        async def stream():
            """持续读取持久事件页并发送保活帧。

            参数：无，闭包持有请求与当前游标。返回：异步 SSE 文本迭代器。异常：
            存储或编码失败时终止连接，让客户端携带最后已收序号重连。
            """

            nonlocal cursor
            yield "retry: 3000\n: connected\n\n"
            while not await request.is_disconnected():
                events_page = service.list_events(
                    after_sequence=cursor,
                    limit=100,
                )["items"]
                for event in events_page:
                    cursor = event["id"]
                    yield format_sse_event(event)
                if not events_page:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def install_workflow_api(
    app: FastAPI,
    service: WorkflowService,
    *,
    template_snapshot_provider: Optional[TemplateSnapshotProvider] = None,
    authoring_transform: Any | None = None,
) -> None:
    """向 OS FastAPI 应用安装工作流及可选可信创作转换接口。

    参数说明：``app`` 是共享 HTTP 应用，``service`` 是工作流权威；本地调度模式
    传入 ``template_snapshot_provider`` 后，模板查询与 F02 编译器共享同一投影；
    ``authoring_transform`` 是同一目录代际的可信创作转换（Trusted Authoring
    Transform），缺失时不发布三条纯转换路由。返回：无。
    """

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(
        _request: Request,
        error: WorkflowError,
    ) -> JSONResponse:
        """把工作流领域错误映射成统一业务 envelope。

        参数：``_request`` 是当前 HTTP 请求但不参与裁决；``error`` 携带稳定错误
        分类。返回与 Backend 一致的 HTTP 200 业务错误响应。
        """

        return _error(error)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        """把工作流相关 DTO 校验错误映射为 Backend 业务码 1000。

        参数：``request`` 用于识别合同路由；``error`` 是 FastAPI 校验详情。返回
        工作流合同的统一错误 envelope，其他路由继续使用框架默认响应。
        """

        workflow_prefixes = (
            "/api/v1/local/workflows",
            "/api/v1/workflows",
            "/api/v1/workflow-tasks",
            "/api/v1/workflow-node-jobs",
            "/api/v1/workflow-node-templates",
            "/api/v1/device-action-runs",
            "/api/v1/events",
            "/api/v1/authoring",
        )
        if any(
            request.url.path == prefix or request.url.path.startswith(f"{prefix}/")
            for prefix in workflow_prefixes
        ):
            return _error(WorkflowError("invalid_input"))
        return await request_validation_exception_handler(request, error)

    app.include_router(create_workflow_router(service))
    if template_snapshot_provider is not None:
        app.include_router(
            create_workflow_template_router(
                WorkflowTemplateQueryService(template_snapshot_provider)
            )
        )
    if authoring_transform is not None:
        from unilabos.app.workflow_authoring_transform import (
            create_authoring_transform_router,
        )

        app.include_router(create_authoring_transform_router(authoring_transform))


def create_workflow_app(
    service: WorkflowService,
    *,
    template_snapshot_provider: Optional[TemplateSnapshotProvider] = None,
    authoring_transform: Any | None = None,
) -> FastAPI:
    """创建工作流合同测试应用。

    参数说明：``service`` 是唯一工作流权威；可选模板快照提供者用于本地完整应用
    合同测试；``authoring_transform`` 显式安装纯转换接缝。返回已安装统一错误映射
    的 FastAPI 应用。
    """

    app = FastAPI(title="Uni-Lab Workflow", version="0.1.0")
    install_workflow_api(
        app,
        service,
        template_snapshot_provider=template_snapshot_provider,
        authoring_transform=authoring_transform,
    )
    return app


# 以下别名是可信创作转换（Trusted Authoring Transform）适配器复用的公共 HTTP
# 接缝；保留旧私有名称，避免扩大现有工作流路由的机械修改范围。
BackendJSONRoute = _BackendJSONRoute
BackendJSONResponse = _BackendJSONResponse
workflow_success_response = _success
workflow_error_response = _error


__all__ = [
    "BackendJSONResponse",
    "BackendJSONRoute",
    "create_workflow_app",
    "create_workflow_router",
    "format_sse_event",
    "install_workflow_api",
    "workflow_error_response",
    "workflow_success_response",
]
