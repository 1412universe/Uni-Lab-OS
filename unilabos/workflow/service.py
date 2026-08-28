"""本地后端形态工作流权威（Backend-shaped Workflow Authority）的应用服务。"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple
from uuid import uuid4

from pydantic import ValidationError

from unilabos.workflow.authoring_candidate_hash import (
    AuthoringCandidateHashError,
    compute_authoring_candidate_hash,
)
from unilabos.workflow.authoring_identity import declared_workflow_uuid
from unilabos.workflow.candidate_validation import (
    CandidateBundleError,
    validate_candidate_bundle,
)
from unilabos.workflow.catalog_dependent_authoring_refresh import (
    CatalogAuthoringGenerationTracker,
    refresh_catalog_dependent_authoring,
)
from unilabos.workflow.composite_invocation import (
    CompositeInvocationInvalid,
    expand_composite_invocation,
)
from unilabos.workflow.definition_edit import (
    WorkflowDefinitionInvalid,
    duplicate_graph,
)
from unilabos.workflow.definition_edit import (
    create_edge as build_workflow_edge,
)
from unilabos.workflow.definition_edit import (
    create_node as build_workflow_node,
)
from unilabos.workflow.definition_edit import (
    duplicate_node as build_duplicated_node,
)
from unilabos.workflow.definition_edit import (
    patch_node as build_patched_node,
)
from unilabos.workflow.device_action_run import (
    DeviceActionRunConflict,
    DeviceActionRunInputError,
    DeviceActionRunService,
    DeviceActionRunUnavailable,
)
from unilabos.workflow.domain_source_target import (
    DomainWorkflowSourceError,
    DomainWorkflowSourceTarget,
)
from unilabos.workflow.event_reader import DurableEventReader
from unilabos.workflow.execution_plan import ExecutionPlanBuilder
from unilabos.workflow.graph_validation import GraphValidationError
from unilabos.workflow.intervention import WorkflowInterventionStore
from unilabos.workflow.job_evidence import JobEvidenceStore
from unilabos.workflow.manual_confirmation import ManualConfirmationStore
from unilabos.workflow.models import (
    CandidateChangeset,
    CandidateCompilation,
    CandidateDiagnostic,
    CandidateSourceMapEntry,
    WorkflowEdgeWrite,
    WorkflowNodeWrite,
    normalize_json_array,
    normalize_json_object,
    validate_uuid,
)
from unilabos.workflow.published_contract import (
    PublishedContractConflict,
    PublishedContractInvalid,
    PublishedWorkflowContractStore,
)
from unilabos.workflow.python_workflow_import import (
    PythonWorkflowImportError,
    validate_python_workflow_import,
)
from unilabos.workflow.run_preflight import build_run_preflight_report
from unilabos.workflow.source_coordinates import source_ranges_fit
from unilabos.workflow.source_discovery import (
    EditableSourceDiscoveryPlan,
    EditableSourceRegistration,
)
from unilabos.workflow.source_workspace import (
    NO_EXPECTED_HASH as _NO_EXPECTED_HASH,
)
from unilabos.workflow.source_workspace import (
    SourceWorkspaceConflict,
    SourceWorkspaceError,
    pin_package_roots,
    read_registered_source,
    registered_source_signature,
    validate_source_registration,
    write_registered_source,
)
from unilabos.workflow.store import (
    StoreAuthoringConflict,
    StoreConflict,
    StoreNotFound,
    StoreRevisionConflict,
    WorkflowStore,
    utc_now,
)
from unilabos.workflow.task_input import (
    PreparedTaskInput,
    TaskInputError,
    prepare_task_input,
)
from unilabos.workflow.task_scheduler_bridge import TaskSchedulerBridgeError

logger = logging.getLogger(__name__)

_ERRORS = {
    "invalid_input": (400, "提交内容格式不正确"),
    "not_found": (404, "请求的资源不存在"),
    "conflict": (409, "资源已发生冲突，请刷新后重试"),
    "workflow_not_found": (404, "工作流不存在或已被删除"),
    "draft_hash_conflict": (
        409,
        "草稿已被其他程序修改，请查看差异后再保存",
    ),
    "workflow_revision_conflict": (
        409,
        "工作流已在其他位置更新，请刷新并重新确认本次修改",
    ),
    "workflow_identity_mismatch": (
        409,
        "导入的 Python workflow_uuid 与当前工作流不一致",
    ),
    "candidate_hash_conflict": (
        409,
        "预览结果已变化，请重新检查 DAG 和源码差异",
    ),
    "template_catalog_conflict": (
        409,
        "设备动作模板已更新，请重新编译并检查工作流",
    ),
    "candidate_not_ready": (409, "当前草稿尚未生成可应用的工作流"),
    "draft_invalid": (422, "草稿存在错误，修复后才能应用"),
    "candidate_invalid": (422, "工作流校验失败，请检查节点、连线和输入输出"),
    "candidate_identity_conflict": (
        409,
        "节点或连线 UUID 已被其他工作流占用，请更新源码中的节点身份",
    ),
    "invalid_material_source": (400, "物料来源选择器不符合规范"),
    "material_flow_fan_out": (409, "同一个物料输出不能连接多个物理消费者"),
    "material_template_mismatch": (409, "物料资源模板与消费者约束不兼容"),
    "template_catalog_unavailable": (
        503,
        "设备动作模板暂不可用，请稍后重试",
    ),
    "source_target_unavailable": (
        503,
        "当前没有唯一可写的领域包，无法保存工作流源码",
    ),
    "source_publication_failed": (
        500,
        "工作流源码写入领域包失败，请检查目录权限后重试",
    ),
    "source_identity_conflict": (
        409,
        "工作流 UUID 或 Python 文件名已被领域包中的其他工作流占用",
    ),
    "internal_error": (500, "本地工作流服务出现错误，请重试或查看日志"),
}
_HASH_TOKEN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ISOLATED_WORKSPACE_ACTIVATION_ERRORS = frozenset(
    {"candidate_invalid", "draft_invalid"}
)
_WORKFLOW_READ_FIELDS = {
    "uuid",
    "create_time",
    "update_time",
    "meta_data",
    "name",
    "tags",
    "revision",
    "description",
}
_NODE_TEMPLATE_READ_FIELDS = {
    "uuid",
    "create_time",
    "update_time",
    "meta_data",
    "resource_template_uuid",
    "name",
    "display_name",
    "goal",
    "goal_default",
    "feedback",
    "result",
    "type",
    "node_type",
    "description",
    "class",
    "schema",
    "icon",
    "header",
    "footer",
}
_HANDLE_TEMPLATE_READ_FIELDS = {
    "uuid",
    "create_time",
    "update_time",
    "meta_data",
    "workflow_node_template_uuid",
    "handle_key",
    "io_type",
    "display_name",
    "type",
    "required",
    "description",
    "data_source",
    "data_key",
}
_WORKFLOW_REQUIRED_READ_FIELDS = _WORKFLOW_READ_FIELDS - {"description"}
_NODE_REQUIRED_READ_FIELDS = {
    "uuid",
    "create_time",
    "update_time",
    "meta_data",
    "workflow_uuid",
    "name",
    "status",
    "type",
    "pose",
    "param",
    "execution_policy",
    "disabled",
    "minimized",
}
_EDGE_REQUIRED_READ_FIELDS = {
    "uuid",
    "create_time",
    "update_time",
    "meta_data",
    "source_node_uuid",
    "target_node_uuid",
    "source_handle_uuid",
    "target_handle_uuid",
}
_NODE_TEMPLATE_REQUIRED_READ_FIELDS = {
    "uuid",
    "create_time",
    "update_time",
    "meta_data",
    "resource_template_uuid",
    "name",
    "display_name",
    "goal",
    "goal_default",
    "feedback",
    "result",
    "type",
    "node_type",
}
_HANDLE_TEMPLATE_REQUIRED_READ_FIELDS = {
    "uuid",
    "create_time",
    "update_time",
    "meta_data",
    "workflow_node_template_uuid",
    "handle_key",
    "io_type",
    "display_name",
    "type",
    "required",
}


class WorkflowError(RuntimeError):
    """面向前端的稳定 Workflow 错误。"""

    def __init__(self, code: str, *, message: str | None = None):
        """创建稳定业务错误并允许安全的可行动消息覆盖。

        参数：``code`` 是公共错误码，``message`` 可提供不含源码内容的具体提示。
        返回：无。异常：未知错误码抛出 ``KeyError``，保持开发期失败关闭。
        """

        status, default_message = _ERRORS[code]
        message = message or default_message
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class WorkflowConflict(WorkflowError):
    pass


class AuthoringCompiler(Protocol):
    compiler_version: str
    template_catalog_fingerprint: str

    def compile(
        self,
        *,
        workflow_uuid: str,
        workflow_revision: int,
        python_source: str,
        source_uri: str,
        applied_graph: Dict[str, Any],
    ) -> CandidateCompilation: ...


class WorkflowTaskSchedulerBridge(Protocol):
    """普通任务与设备单动作共享的工作流任务（WorkflowTask）调度端口。"""

    def submit(self, task: dict[str, Any]) -> dict[str, Any]:
        """提交已持久任务。

        参数：``task`` 是标准工作流任务（WorkflowTask）投影。返回：同步推进后的
        任务/作业聚合。异常：编译、准入或派发前投影失败时由实现抛稳定桥接错误。
        """

        ...

    def close(self) -> None:
        """幂等释放调度生命周期监听器；参数无，返回无。"""

        ...

    def step(
        self,
        task_uuid: str,
        *,
        target_node_uuid: str | None = None,
    ) -> dict[str, Any]:
        """让暂停的单步任务放行一个节点并返回调度摘要。"""

        ...

    def cancel(
        self,
        task_uuid: str,
        *,
        command_uuid: str | None = None,
    ) -> dict[str, Any]:
        """持久化取消命令并返回当前任务聚合；物理终态可随后异步收敛。"""

        ...

    def decide_manual_confirmation(
        self,
        job_uuid: str,
        *,
        approved: bool,
        param: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """恢复或拒绝一个已经持久开启的人工确认。"""

        ...

    def request_uncertain_resolution(
        self,
        job_uuid: str,
        *,
        reason: str,
        device_command_id: str,
    ) -> dict[str, Any]:
        """请求 Edge 证明未知设备作业已经取消。"""

        ...


class WorkflowInterventionDelivery(Protocol):
    """本地设备异常决定的投递端口。"""

    def add_error_decision_required_listener(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> None: ...

    def remove_error_decision_required_listener(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> None: ...

    def resolve_error_decision(
        self,
        decision_id: str,
        decision: dict[str, Any],
    ) -> bool: ...


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _mtime_rfc3339(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


class WorkflowService:
    """协调进程内工作流定义、持久运行事实与 package 源码。"""

    def __init__(
        self,
        store: WorkflowStore,
        *,
        definition_store: WorkflowStore | None = None,
        compiler: Optional[AuthoringCompiler] = None,
        compiler_rebuilder: Callable[[], AuthoringCompiler] | None = None,
        source_target: DomainWorkflowSourceTarget | None = None,
        material_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        task_scheduler_bridge: WorkflowTaskSchedulerBridge | None = None,
    ) -> None:
        """装配本地工作流应用服务。

        参数：``store`` 是持久 Task/Job 运行事实库；``definition_store`` 是进程内
        工作流定义目录，省略时仅为兼容隔离测试而复用 ``store``；``compiler``
        负责编译可信工作流源码；
        ``compiler_rebuilder`` 在成功应用后重建包含已发布工作流的完整目录代际；
        ``source_target`` 是用户 JSON/Python 导入默认写入的唯一领域包，导入后
        的图和元数据编辑也回写该目标；省略时仅保留无领域工作区的遗留内存行为；
        ``material_resolver`` 按物料 UUID 读取活动物料身份，供设备单动作运行
        （DeviceActionRun）关闭式校验；``task_scheduler_bridge`` 把普通工作流任务
        （WorkflowTask）与首次创建的设备单动作聚合交给同一本地调度器。返回无。
        异常：编译器重建器不可调用时抛出 ``TypeError``。
        """

        self._store = store
        self._definition_store = (
            definition_store if definition_store is not None else store
        )
        # 新增能力仓储按需初始化，避免“只创建任务”的调用方被迫实现发布、证据和
        # 人工处置所需的 SQLite 事务接口；第一次使用相应能力时才补齐其物理表。
        self._published_contracts: PublishedWorkflowContractStore | None = None
        self._job_evidence: JobEvidenceStore | None = None
        self._manual_confirmations: ManualConfirmationStore | None = None
        self._interventions: WorkflowInterventionStore | None = None
        self._intervention_delivery: WorkflowInterventionDelivery | None = None
        self._event_reader = DurableEventReader(store)
        self._definition_event_lock = threading.Lock()
        self._definition_event_cursor = 0
        self.compiler = compiler
        if compiler_rebuilder is not None and not callable(compiler_rebuilder):
            raise TypeError("compiler_rebuilder 必须是可调用对象")
        self._compiler_rebuilder = compiler_rebuilder
        self._source_target = source_target
        self._device_action_runs = DeviceActionRunService(
            store,
            material_resolver=material_resolver,
        )
        self._material_resolver = material_resolver
        # ``_task_scheduler_bridge`` 是普通任务与设备单动作共享的唯一监听器所有者；
        # 后端控制（Backend-controlled）配置保持空，避免第二个调度权威。
        self._task_scheduler_bridge = task_scheduler_bridge
        self._locks_guard = threading.Lock()
        self._capability_store_lock = threading.Lock()
        self._authoring_locks: Dict[str, threading.RLock] = {}
        # ``_source_authorization_replacement_lock`` 串行化完整授权集合替换，使“当前
        # 集合 ∪ 新集合”的锁快照在取得所有创作锁前不会被另一替换命令改变。
        self._source_authorization_replacement_lock = threading.RLock()
        # ``_active_source_workflow_uuids`` 只表达本次进程启动配置授权的工作流
        # 源码（Workflow Source）；注册行和创作状态都只存在于本次进程目录。
        self._active_sources_lock = threading.RLock()
        self._active_source_workflow_uuids: frozenset[str] = frozenset()
        # 依赖关系来自与 Registry Snapshot 同代的静态 Package Catalog，只用于
        # managed-local 冷启动按子到父分层激活；交互 Apply 仍保持原有单项语义。
        self._active_source_dependencies: dict[str, frozenset[str]] = {}
        # 冷启动单线程批次提交期间暂缓逐项目录重建；这是服务内部生命周期状态，
        # 不暴露到公共 Apply API，避免调用方绕开每次交互应用后的目录一致性刷新。
        self._workspace_activation_batch = False
        # 自动激活候选由当前线程刚刚编译并安装进内存目录；线程本地授权让
        # Apply 复用这个编译事实，同时保持交互 Apply 的独立重编译复核。
        self._workspace_activation_context = threading.local()
        # ``_catalog_generation_tracker`` 隐藏本进程目录编译基线、变化判定和源码
        # 观测签名组合；工作流服务只在编译事务接缝提交已验证指纹。
        self._catalog_generation_tracker = CatalogAuthoringGenerationTracker()

    def _published_contract_store(self) -> PublishedWorkflowContractStore:
        with self._capability_store_lock:
            if self._published_contracts is None:
                self._published_contracts = PublishedWorkflowContractStore(
                    self._definition_store
                )
        return self._published_contracts

    def _job_evidence_store(self) -> JobEvidenceStore:
        with self._capability_store_lock:
            if self._job_evidence is None:
                self._job_evidence = JobEvidenceStore(self._store)
        return self._job_evidence

    def _manual_confirmation_store(self) -> ManualConfirmationStore:
        with self._capability_store_lock:
            if self._manual_confirmations is None:
                self._manual_confirmations = ManualConfirmationStore(self._store)
        return self._manual_confirmations

    def _intervention_store(self) -> WorkflowInterventionStore:
        with self._capability_store_lock:
            if self._interventions is None:
                self._interventions = WorkflowInterventionStore(self._store)
        return self._interventions

    def bind_intervention_delivery(
        self,
        delivery: WorkflowInterventionDelivery,
    ) -> None:
        """把设备异常报告与决定投递接到持久工作流干预。

        参数：``delivery`` 是当前本地设备执行端口。返回无。绑定后立即重投此前
        已选但未明确接受的决定；投递仍不可达时保留 ``unknown``，不会阻止服务
        启动，也不会伪造设备已恢复执行。
        """

        self._intervention_delivery = delivery
        delivery.add_error_decision_required_listener(
            self.open_workflow_intervention_from_report
        )
        for intervention in self._intervention_store().list_replayable_selected():
            if not self._deliver_workflow_intervention(intervention):
                logger.warning(
                    "工作流干预恢复投递仍不可达 intervention_uuid=%s job_uuid=%s",
                    intervention["uuid"],
                    intervention["workflow_node_job_uuid"],
                )

    # 工作流（Workflow）与图（Graph） -------------------------------------

    def create_workflow(
        self,
        *,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
        workflow_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            name = name.strip()
            if not name:
                raise ValueError("workflow name must not be blank")
            identity = validate_uuid(workflow_uuid or str(uuid4()))
            tags = normalize_json_array(tags)
            meta_data = normalize_json_object(meta_data)
            public_meta_data = dict(meta_data)
            public_meta_data.pop("unilab", None)
            return self._definition_store.create_workflow(
                workflow_uuid=identity,
                name=name,
                tags=tags,
                description=self._optional_text(description),
                meta_data=public_meta_data,
            )
        except (ValueError, ValidationError):
            raise WorkflowError("invalid_input") from None
        except StoreConflict:
            raise WorkflowConflict("conflict") from None

    def get_workflow(self, workflow_uuid: str) -> Dict[str, Any]:
        try:
            identity = validate_uuid(workflow_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        try:
            return self._definition_store.get_workflow(identity)
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def list_workflows(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        name: str = "",
    ) -> Dict[str, Any]:
        page, page_size = self._normalize_page(page, page_size)
        return self._definition_store.list_workflows(
            page=page,
            page_size=page_size,
            name=name,
        )

    def update_workflow(
        self,
        workflow_uuid: str,
        *,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        current = self.get_workflow(workflow_uuid)
        identity = current["uuid"]
        with self._authoring_lock(identity):
            current = self.get_workflow(identity)
            try:
                name = name.strip()
                if not name:
                    raise ValueError("workflow name must not be blank")
                tags = normalize_json_array(tags)
                public_meta_data = dict(normalize_json_object(meta_data))
            except (AttributeError, TypeError, ValueError):
                raise WorkflowError("invalid_input") from None
            public_meta_data.pop("unilab", None)
            if "unilab" in current["meta_data"]:
                public_meta_data["unilab"] = current["meta_data"]["unilab"]
            if self._has_active_source(identity):
                unilab_meta = dict(public_meta_data.get("unilab") or {})
                root_fields = set(unilab_meta.get("authoring_root_fields") or [])
                root_fields.update({"tags", "meta_data"})
                unilab_meta["authoring_root_fields"] = sorted(root_fields)
                public_meta_data["unilab"] = unilab_meta
                graph = self.get_graph(identity)
                graph["workflow"] = {
                    **graph["workflow"],
                    "name": name,
                    "tags": tags,
                    "description": self._optional_text(description),
                    "meta_data": public_meta_data,
                }
                return self._commit_domain_graph_candidate(
                    identity,
                    revision=int(current["revision"]),
                    graph=graph,
                )["workflow"]
            return self._definition_store.update_workflow(
                identity,
                name=name,
                tags=tags,
                description=self._optional_text(description),
                meta_data=public_meta_data,
            )

    def delete_workflow(self, workflow_uuid: str) -> None:
        identity = self.get_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(identity):
            self.get_workflow(identity)
            if self._has_active_source(identity):
                registration = self._registered_domain_source(identity)
                self._unregister_domain_source(registration)
            self._definition_store.delete_workflow(identity)
            self._remove_active_source_authorization(identity)

    def get_graph(self, workflow_uuid: str) -> Dict[str, Any]:
        identity = self.get_workflow(workflow_uuid)["uuid"]
        return self._validated_applied_backend_graph(
            self._definition_store.get_graph(identity),
        )

    def publish_workflow_contract(
        self,
        workflow_uuid: str,
        *,
        revision: int,
    ) -> Dict[str, Any]:
        """把当前工作流修订冻结为不可变已发布工作流合同。

        参数：``workflow_uuid`` 是来源工作流稳定身份，``revision`` 是调用方确认
        的当前修订。返回 Backend 公共发布投影；修订变化或同修订内容漂移抛
        ``WorkflowConflict``，空图和非法边界抛 ``WorkflowError``。
        """

        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise WorkflowError("invalid_input")
        identity = self.get_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(identity):
            graph = self.get_graph(identity)
            try:
                contract = self._published_contract_store().publish(
                    graph=graph,
                    expected_revision=revision,
                )
            except PublishedContractInvalid as error:
                raise WorkflowError("invalid_input", message=str(error)) from None
            except PublishedContractConflict:
                raise WorkflowConflict("workflow_revision_conflict") from None
        return self._published_contract_store().public(contract)

    def list_published_workflow_contracts(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
    ) -> Dict[str, Any]:
        """分页返回每个来源工作流最新的已发布合同。

        参数：``page``/``page_size`` 是 Backend 页码，``keyword`` 按名称模糊
        过滤。返回空集合而不是 ``null``；非法页码按 Backend 默认值规范化。
        """

        page, page_size = self._normalize_page(page, page_size)
        return self._published_contract_store().list_latest(
            page=page,
            page_size=page_size,
            keyword=keyword,
        )

    def insert_composite_workflow(
        self,
        workflow_uuid: str,
        *,
        revision: int,
        contract_uuid: str,
        invocation_uuid: Optional[str],
        device_bindings: Mapping[str, str],
        pose: Dict[str, Any],
        param: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把一个不可变发布合同原子展开到父工作流图。

        参数：``workflow_uuid``/``revision`` 固定父图，``contract_uuid`` 固定子
        合同，``invocation_uuid`` 固定本次调用身份；设备绑定、位置和参数成为冻结
        调用事实。返回修订推进后的完整父图；递归、碰撞、合同损坏或设备不兼容时
        关闭失败，任何节点都不会部分写入。
        """

        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise WorkflowError("invalid_input")
        try:
            parent_uuid = validate_uuid(workflow_uuid)
            contract_identity = validate_uuid(contract_uuid)
            invocation_identity = validate_uuid(invocation_uuid or str(uuid4()))
            pose = normalize_json_object(pose)
            param = normalize_json_object(param)
            if not isinstance(device_bindings, Mapping):
                raise ValueError
            normalized_bindings = {
                str(key): validate_uuid(value)
                for key, value in device_bindings.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if len(normalized_bindings) != len(device_bindings):
                raise ValueError
        except (TypeError, ValueError):
            raise WorkflowError("invalid_input") from None

        parent_uuid = self.get_workflow(parent_uuid)["uuid"]
        with self._authoring_lock(parent_uuid):
            parent_graph = self.get_graph(parent_uuid)
            if parent_graph["workflow"]["revision"] != revision:
                raise WorkflowConflict("workflow_revision_conflict")
            try:
                contract = self._published_contract_store().get(contract_identity)
            except KeyError:
                raise WorkflowError("not_found") from None
            requirements = contract["executor_requirements"]
            required_keys = {str(item["key"]) for item in requirements}
            if set(normalized_bindings) != required_keys:
                raise WorkflowError("invalid_input")
            for requirement in requirements:
                material_uuid = normalized_bindings[str(requirement["key"])]
                material = (
                    self._material_resolver(material_uuid)
                    if self._material_resolver is not None
                    else None
                )
                if (
                    not isinstance(material, Mapping)
                    or material.get("resource_template_uuid")
                    != requirement["resource_template_uuid"]
                ):
                    raise WorkflowError("invalid_input")
            try:
                insertion_nodes, insertion_edges = expand_composite_invocation(
                    parent_graph=parent_graph,
                    contract=contract,
                    invocation_uuid=invocation_identity,
                    pose=pose,
                    param=param,
                    device_bindings=normalized_bindings,
                )
                node_values = [
                    WorkflowNodeWrite.model_validate(item)
                    for item in [*parent_graph["nodes"], *insertion_nodes]
                ]
                edge_values = [
                    WorkflowEdgeWrite.model_validate(item)
                    for item in [*parent_graph["edges"], *insertion_edges]
                ]
                return self.save_graph(
                    parent_uuid,
                    revision=revision,
                    nodes=node_values,
                    edges=edge_values,
                )
            except (CompositeInvocationInvalid, ValidationError):
                raise WorkflowError("invalid_input") from None
            except StoreRevisionConflict:
                raise WorkflowConflict("workflow_revision_conflict") from None
            except StoreNotFound:
                raise WorkflowError("not_found") from None
            except StoreAuthoringConflict as error:
                raise WorkflowError(error.code) from None
            except StoreConflict:
                raise WorkflowError("invalid_input") from None

    def get_workflow_run_preflight(
        self,
        workflow_uuid: str,
        *,
        run_mode: str = "normal",
        target_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """返回不产生 Task、预留或执行占用的候选运行报告。

        参数：``workflow_uuid`` 定位当前图，``run_mode`` 与可选目标节点固定执行
        范围。返回 Backend 形状的只读报告；动态设备和物料条件明确延迟到调度器
        原子准入，不把预检结果当成执行承诺。
        """

        normalized_mode = run_mode or "normal"
        if normalized_mode not in {"normal", "step", "single_node"}:
            raise WorkflowError("invalid_input")
        if normalized_mode == "single_node" and target_node_uuid is None:
            raise WorkflowError("invalid_input")
        if normalized_mode != "single_node" and target_node_uuid is not None:
            raise WorkflowError("invalid_input")
        if target_node_uuid is not None:
            try:
                target_node_uuid = validate_uuid(target_node_uuid)
            except ValueError:
                raise WorkflowError("invalid_input") from None
        graph = self.get_graph(workflow_uuid)
        return build_run_preflight_report(
            graph=graph,
            run_mode=normalized_mode,
            target_node_uuid=target_node_uuid,
            material_resolver=self._material_resolver,
        )

    def save_graph(
        self,
        workflow_uuid: str,
        *,
        revision: int,
        nodes: List[WorkflowNodeWrite | Dict[str, Any]],
        edges: List[WorkflowEdgeWrite | Dict[str, Any]],
    ) -> Dict[str, Any]:
        """以严格工作流输入/输出（Workflow I/O）合同保存完整图。

        参数说明：``workflow_uuid`` 是工作流（Workflow）稳定身份，``revision``
        是乐观并发预期版本，``nodes`` 与 ``edges`` 是完整替换集合。返回：提交后
        的后端（Backend）形状工作流图投影。异常：输入 DTO 或图语义非法抛出
        ``WorkflowError``；修订冲突抛出 ``WorkflowConflict``；任何失败都由存储
        适配器（Store Adapter）回滚，公共服务入口不会留下部分节点或修订写入。
        旧形状只在存储适配器内兼容，公共入口始终使用同一个严格校验深模块。
        """

        identity = self.get_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(identity):
            self.get_workflow(identity)
            try:
                node_values = [
                    item
                    if isinstance(item, WorkflowNodeWrite)
                    else WorkflowNodeWrite.model_validate(item)
                    for item in nodes
                ]
                edge_values = [
                    item
                    if isinstance(item, WorkflowEdgeWrite)
                    else WorkflowEdgeWrite.model_validate(item)
                    for item in edges
                ]
                if self._has_active_source(identity):
                    candidate = self._definition_store.preview_graph_replacement(
                        identity,
                        revision=revision,
                        nodes=node_values,
                        edges=edge_values,
                        protect_reserved_metadata=True,
                        validate_workflow_io_contract=True,
                    )
                    return self._commit_domain_graph_candidate(
                        identity,
                        revision=revision,
                        graph=candidate,
                    )
                return self._definition_store.save_graph(
                    identity,
                    revision=revision,
                    nodes=node_values,
                    edges=edge_values,
                    protect_reserved_metadata=True,
                    validate_workflow_io_contract=True,
                )
            except ValidationError:
                raise WorkflowError("invalid_input") from None
            except StoreRevisionConflict:
                raise WorkflowConflict("conflict") from None
            except StoreNotFound:
                raise WorkflowError("not_found") from None
            except StoreAuthoringConflict as error:
                raise WorkflowError(error.code) from None
            except StoreConflict:
                raise WorkflowError("invalid_input") from None

    def _has_active_source(self, workflow_uuid: str) -> bool:
        """返回当前定义是否由本次启动的领域包 Python 目标拥有。

        仅手工挂载创作文件、但没有配置领域包写入目标的遗留入口继续保持原有
        Graph API 语义；只有 managed Local 领域包启用双向同步。
        """

        if self._source_target is None:
            return False
        with self._active_sources_lock:
            return workflow_uuid in self._active_source_workflow_uuids

    def _commit_domain_graph_candidate(
        self,
        workflow_uuid: str,
        *,
        revision: int,
        graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把 API 图修改转换为 Python，并经现有创作事务写回同一领域文件。

        参数：``workflow_uuid``/``revision`` 固定当前定义代际，``graph`` 是已用
        Store 同合同预演通过的候选完整图。返回应用后的完整图。源码生成、AST
        固定点、目录指纹、文件 CAS 或候选应用任一步失败时，不直接改内存图。
        """

        if self.compiler is None:
            raise WorkflowError("template_catalog_unavailable")
        registration = self._registration(workflow_uuid)
        source = self._read_source(registration)
        if source is None:
            raise WorkflowConflict("draft_hash_conflict")
        try:
            compilation = CandidateCompilation.model_validate(
                self.compiler.generate_python(
                    workflow_uuid=workflow_uuid,
                    workflow_revision=revision,
                    graph=self._authoring_graph_projection(graph),
                    source_uri=str(registration["source_uri"]),
                )
            )
        except (KeyError, TypeError, ValidationError, ValueError):
            raise WorkflowError("candidate_invalid") from None
        except Exception:
            raise WorkflowError("internal_error") from None
        if not compilation.valid or compilation.normalized_python_source is None:
            diagnostic = next(
                (
                    item
                    for item in compilation.diagnostics
                    if str(item.get("severity", "")).lower() == "error"
                ),
                None,
            )
            message = (
                str(diagnostic.get("message"))
                if isinstance(diagnostic, Mapping) and diagnostic.get("message")
                else "工作流图不能转换为规范 Python 源码"
            )
            raise WorkflowError("candidate_invalid", message=message)
        authoring = self.save_draft(
            workflow_uuid,
            python_source=compilation.normalized_python_source,
            expected_draft_hash=source["draft_hash"],
            expected_workflow_revision=revision,
        )
        candidate = authoring.get("candidate")
        if candidate is None:
            return self.get_graph(workflow_uuid)
        candidate_hash = candidate.get("candidate_hash")
        if not isinstance(candidate_hash, str) or not candidate_hash:
            raise WorkflowError("candidate_invalid")
        applied = self.apply_authoring(
            workflow_uuid,
            candidate_hash=candidate_hash,
        )
        return self._validated_applied_backend_graph(
            applied["authoring"]["applied_graph"]
        )

    def create_workflow_node(
        self,
        workflow_uuid: str,
        *,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """通过完整图原子保存向工作流增加一个节点。"""

        identity = self.get_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(identity):
            graph = self.get_graph(identity)
            template = None
            template_uuid = payload.get("workflow_node_template_uuid")
            if template_uuid is not None:
                try:
                    template = self._definition_store.get_node_template(
                        validate_uuid(str(template_uuid))
                    )
                except ValueError:
                    raise WorkflowError("invalid_input") from None
                except StoreNotFound:
                    raise WorkflowError("not_found") from None
            try:
                node = build_workflow_node(payload=payload, template=template)
            except (TypeError, ValueError, WorkflowDefinitionInvalid):
                raise WorkflowError("invalid_input") from None
            updated = self.save_graph(
                identity,
                revision=graph["workflow"]["revision"],
                nodes=[*graph["nodes"], node],
                edges=graph["edges"],
            )
            return self._graph_entity(updated, "nodes", node["uuid"])

    def list_workflow_nodes(
        self,
        workflow_uuid: str,
        *,
        page: int,
        page_size: int,
        workflow_node_template_uuid: Optional[str] = None,
        material_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """分页返回指定工作流节点，并支持模板与物料身份筛选。"""

        graph = self.get_graph(workflow_uuid)
        page, page_size = self._normalize_page(page, page_size)
        try:
            template_uuid = (
                validate_uuid(workflow_node_template_uuid)
                if workflow_node_template_uuid
                else None
            )
            resolved_material_uuid = (
                validate_uuid(material_uuid) if material_uuid else None
            )
        except ValueError:
            raise WorkflowError("invalid_input") from None
        items = [
            node
            for node in graph["nodes"]
            if (
                template_uuid is None
                or node.get("workflow_node_template_uuid") == template_uuid
            )
            and (
                resolved_material_uuid is None
                or node.get("material_uuid") == resolved_material_uuid
            )
        ]
        offset = (page - 1) * page_size
        return {
            "items": items[offset : offset + page_size],
            "total": len(items),
            "page": page,
            "page_size": page_size,
        }

    def get_workflow_node(self, node_uuid: str) -> Dict[str, Any]:
        """按全局稳定身份读取一个活动工作流节点。"""

        _workflow_uuid, _graph, node = self._locate_graph_entity("nodes", node_uuid)
        return node

    def patch_workflow_node(
        self,
        node_uuid: str,
        *,
        patch: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """局部修改节点，但最终只通过完整图 CAS 写入一次。"""

        workflow_uuid, _graph, _node = self._locate_graph_entity("nodes", node_uuid)
        with self._authoring_lock(workflow_uuid):
            graph = self.get_graph(workflow_uuid)
            current = self._graph_entity(graph, "nodes", validate_uuid(node_uuid))
            try:
                updated_node = build_patched_node(current, patch)
            except (TypeError, ValueError, WorkflowDefinitionInvalid):
                raise WorkflowError("invalid_input") from None
            nodes = [
                updated_node if node["uuid"] == current["uuid"] else node
                for node in graph["nodes"]
            ]
            updated = self.save_graph(
                workflow_uuid,
                revision=graph["workflow"]["revision"],
                nodes=nodes,
                edges=graph["edges"],
            )
            return self._graph_entity(updated, "nodes", current["uuid"])

    def duplicate_workflow_node(
        self,
        node_uuid: str,
        *,
        name: Optional[str],
    ) -> Dict[str, Any]:
        """复制单个节点定义；原有连线保持不变。"""

        workflow_uuid, _graph, _node = self._locate_graph_entity("nodes", node_uuid)
        with self._authoring_lock(workflow_uuid):
            graph = self.get_graph(workflow_uuid)
            current = self._graph_entity(graph, "nodes", validate_uuid(node_uuid))
            try:
                duplicate = build_duplicated_node(current, name=name)
            except (TypeError, ValueError, WorkflowDefinitionInvalid):
                raise WorkflowError("invalid_input") from None
            updated = self.save_graph(
                workflow_uuid,
                revision=graph["workflow"]["revision"],
                nodes=[*graph["nodes"], duplicate],
                edges=graph["edges"],
            )
            return self._graph_entity(updated, "nodes", duplicate["uuid"])

    def delete_workflow_node(self, node_uuid: str) -> None:
        """删除节点及其关联连线；存在未删除子节点时拒绝。"""

        workflow_uuid, _graph, _node = self._locate_graph_entity("nodes", node_uuid)
        with self._authoring_lock(workflow_uuid):
            graph = self.get_graph(workflow_uuid)
            identity = validate_uuid(node_uuid)
            self._graph_entity(graph, "nodes", identity)
            if any(node.get("parent_uuid") == identity for node in graph["nodes"]):
                raise WorkflowConflict("conflict")
            self.save_graph(
                workflow_uuid,
                revision=graph["workflow"]["revision"],
                nodes=[node for node in graph["nodes"] if node["uuid"] != identity],
                edges=[
                    edge
                    for edge in graph["edges"]
                    if edge["source_node_uuid"] != identity
                    and edge["target_node_uuid"] != identity
                ],
            )

    def create_workflow_edge(
        self,
        workflow_uuid: str,
        *,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """通过完整图验证和 CAS 增加一条连线。"""

        identity = self.get_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(identity):
            graph = self.get_graph(identity)
            try:
                edge = build_workflow_edge(payload)
                edge_value = WorkflowEdgeWrite.model_validate(edge)
            except (TypeError, ValueError, ValidationError, WorkflowDefinitionInvalid):
                raise WorkflowError("invalid_input") from None
            updated = self.save_graph(
                identity,
                revision=graph["workflow"]["revision"],
                nodes=graph["nodes"],
                edges=[*graph["edges"], edge_value],
            )
            return self._graph_entity(updated, "edges", edge_value.uuid)

    def delete_workflow_edge(self, edge_uuid: str) -> None:
        """按稳定身份删除一条工作流连线。"""

        workflow_uuid, _graph, _edge = self._locate_graph_entity("edges", edge_uuid)
        with self._authoring_lock(workflow_uuid):
            graph = self.get_graph(workflow_uuid)
            identity = validate_uuid(edge_uuid)
            self._graph_entity(graph, "edges", identity)
            self.save_graph(
                workflow_uuid,
                revision=graph["workflow"]["revision"],
                nodes=graph["nodes"],
                edges=[edge for edge in graph["edges"] if edge["uuid"] != identity],
            )

    def batch_delete_workflow_graph(
        self,
        workflow_uuid: str,
        *,
        node_uuids: List[str],
        edge_uuids: List[str],
    ) -> Dict[str, Any]:
        """一次原子删除指定节点、关联连线和显式指定连线。"""

        identity = self.get_workflow(workflow_uuid)["uuid"]
        try:
            node_set = {validate_uuid(value) for value in node_uuids}
            edge_set = {validate_uuid(value) for value in edge_uuids}
        except (TypeError, ValueError):
            raise WorkflowError("invalid_input") from None
        if not node_set and not edge_set:
            raise WorkflowError("invalid_input")
        with self._authoring_lock(identity):
            graph = self.get_graph(identity)
            known_nodes = {node["uuid"] for node in graph["nodes"]}
            known_edges = {edge["uuid"] for edge in graph["edges"]}
            if not node_set <= known_nodes or not edge_set <= known_edges:
                raise WorkflowError("invalid_input")
            if any(
                node.get("parent_uuid") in node_set and node["uuid"] not in node_set
                for node in graph["nodes"]
            ):
                raise WorkflowConflict("conflict")
            retained_edges = [
                edge
                for edge in graph["edges"]
                if edge["uuid"] not in edge_set
                and edge["source_node_uuid"] not in node_set
                and edge["target_node_uuid"] not in node_set
            ]
            return self.save_graph(
                identity,
                revision=graph["workflow"]["revision"],
                nodes=[node for node in graph["nodes"] if node["uuid"] not in node_set],
                edges=retained_edges,
            )

    def duplicate_workflow(
        self,
        workflow_uuid: str,
        *,
        name: Optional[str],
    ) -> Dict[str, Any]:
        """在一个 SQLite 事务中复制工作流主记录和完整图。"""

        source = self.get_graph(workflow_uuid)
        if not source["nodes"]:
            raise WorkflowError("invalid_input")
        copied_name = (
            name.strip() if isinstance(name, str) else f"{source['workflow']['name']} copy"
        )
        if not copied_name:
            raise WorkflowError("invalid_input")
        nodes, edges = duplicate_graph(source)
        identity = str(uuid4())
        try:
            return self._definition_store.create_workflow_with_graph(
                workflow_uuid=identity,
                name=copied_name,
                tags=list(source["workflow"].get("tags", [])),
                description=source["workflow"].get("description"),
                meta_data=dict(source["workflow"].get("meta_data", {})),
                nodes=[WorkflowNodeWrite.model_validate(node) for node in nodes],
                edges=[WorkflowEdgeWrite.model_validate(edge) for edge in edges],
            )
        except ValidationError:
            raise WorkflowError("invalid_input") from None
        except StoreAuthoringConflict as error:
            raise WorkflowError(error.code) from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        except StoreConflict:
            raise WorkflowError("invalid_input") from None

    def import_legacy_workflow(
        self,
        *,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """原子导入旧版工作流定义，并重建节点与连线身份。"""

        definition = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
        name_value = definition.get("name") or definition.get("workflow_name")
        if not isinstance(name_value, str) or not name_value.strip():
            raise WorkflowError("invalid_input")
        source_nodes = definition.get("nodes")
        source_edges = definition.get("edges", [])
        if not isinstance(source_nodes, list) or not source_nodes:
            raise WorkflowError("invalid_input")
        if not isinstance(source_edges, list):
            raise WorkflowError("invalid_input")
        if definition.get("inventory_requirements") not in (None, []):
            # Local 的数量型库存需求必须走已对齐的任务准入合同，不能静默丢弃。
            raise WorkflowError("invalid_input")
        if self._source_target is None:
            raise WorkflowError("source_target_unavailable")
        if self.compiler is None:
            raise WorkflowError("template_catalog_unavailable")

        old_to_new: Dict[str, str] = {}
        nodes: List[Dict[str, Any]] = []
        try:
            for source in source_nodes:
                if not isinstance(source, Mapping):
                    raise WorkflowDefinitionInvalid("nodes 必须是对象数组")
                old_uuid = validate_uuid(str(source.get("uuid")))
                if old_uuid in old_to_new:
                    raise WorkflowDefinitionInvalid("旧版节点 UUID 重复")
                node_payload = dict(source)
                node_payload["workflow_node_template_uuid"] = source.get(
                    "workflow_node_template_uuid"
                ) or source.get("template_uuid")
                template = None
                template_uuid = node_payload.get("workflow_node_template_uuid")
                if template_uuid is not None:
                    template = self._definition_store.get_node_template(
                        validate_uuid(str(template_uuid))
                    )
                node = build_workflow_node(payload=node_payload, template=template)
                old_to_new[old_uuid] = node["uuid"]
                nodes.append(node)
            for index, source in enumerate(source_nodes):
                parent_uuid = source.get("parent_uuid")
                if parent_uuid is not None:
                    nodes[index]["parent_uuid"] = old_to_new[
                        validate_uuid(str(parent_uuid))
                    ]
            edges: List[Dict[str, Any]] = []
            for source in source_edges:
                if not isinstance(source, Mapping):
                    raise WorkflowDefinitionInvalid("edges 必须是对象数组")
                edge_payload = dict(source)
                edge_payload["source_node_uuid"] = old_to_new[
                    validate_uuid(str(source.get("source_node_uuid")))
                ]
                edge_payload["target_node_uuid"] = old_to_new[
                    validate_uuid(str(source.get("target_node_uuid")))
                ]
                edges.append(build_workflow_edge(edge_payload))
            tags = normalize_json_array(definition.get("tags"))
            meta_data = normalize_json_object(definition.get("meta_data"))
            public_meta_data = dict(meta_data)
            public_meta_data.pop("unilab", None)
        except (KeyError, TypeError, ValueError, ValidationError, WorkflowDefinitionInvalid):
            raise WorkflowError("invalid_input") from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        except StoreAuthoringConflict as error:
            raise WorkflowError(error.code) from None
        except StoreConflict:
            raise WorkflowError("invalid_input") from None
        identity = str(uuid4())
        registration = self._source_target.registration(
            workflow_uuid=identity,
            file_name=DomainWorkflowSourceTarget.default_file_name(identity),
        )
        return self._commit_legacy_domain_import(
            registration=registration,
            name=name_value.strip(),
            tags=tags,
            description=self._optional_text(definition.get("description")),
            meta_data=public_meta_data,
            nodes=nodes,
            edges=edges,
        )

    def _commit_legacy_domain_import(
        self,
        *,
        registration: EditableSourceRegistration,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Mapping[str, Any],
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """在工作流锁内把旧 JSON 规范化为首版领域 Python 定义。"""

        identity = registration.workflow_uuid
        source_registered = False
        workflow_created = False
        with self._authoring_lock(identity):
            try:
                created = self._definition_store.create_workflow_with_graph(
                    workflow_uuid=identity,
                    name=name,
                    tags=tags,
                    description=description,
                    meta_data=dict(meta_data),
                    nodes=[WorkflowNodeWrite.model_validate(node) for node in nodes],
                    edges=[WorkflowEdgeWrite.model_validate(edge) for edge in edges],
                )
                workflow_created = True
            except (KeyError, TypeError, ValueError, ValidationError):
                raise WorkflowError("invalid_input") from None
            except StoreNotFound:
                raise WorkflowError("not_found") from None
            except StoreAuthoringConflict as error:
                raise WorkflowError(error.code) from None
            except StoreConflict:
                raise WorkflowError("invalid_input") from None

            try:
                source_meta_data = dict(created["workflow"].get("meta_data") or {})
                source_meta_data["unilab"] = {
                    "source_bootstrap": self._source_bootstrap_metadata(registration),
                    "authoring_root_fields": ["meta_data", "tags"],
                }
                source_graph = self._authoring_graph_projection(created)
                source_graph["workflow"]["meta_data"] = source_meta_data
                try:
                    compilation = CandidateCompilation.model_validate(
                        self.compiler.generate_python(
                            workflow_uuid=identity,
                            workflow_revision=int(created["workflow"]["revision"]),
                            graph=source_graph,
                            source_uri=registration.source_uri,
                        )
                    )
                except Exception:
                    raise WorkflowError("internal_error") from None
                if (
                    not compilation.valid
                    or compilation.normalized_python_source is None
                ):
                    raise WorkflowError("candidate_invalid")

                # 旧 JSON 图先经过公共图校验，再以生成的规范 Python 重编译。
                # 重新创建首版图以带齐作者源码映射，同时保持 revision=1。
                try:
                    canonical = CandidateCompilation.model_validate(
                        self.compiler.compile(
                            workflow_uuid=identity,
                            workflow_revision=1,
                            python_source=compilation.normalized_python_source,
                            source_uri=registration.source_uri,
                            applied_graph=source_graph,
                        )
                    )
                except Exception:
                    raise WorkflowError("internal_error") from None
                if not canonical.valid or canonical.graph is None:
                    raise WorkflowError("candidate_invalid")
                canonical_workflow = canonical.graph["workflow"]
                self._definition_store.discard_uncommitted_workflow(identity)
                workflow_created = False
                created = self._definition_store.create_workflow_with_graph(
                    workflow_uuid=identity,
                    name=canonical_workflow["name"],
                    tags=list(canonical_workflow.get("tags") or []),
                    description=canonical_workflow.get("description"),
                    meta_data=dict(canonical_workflow.get("meta_data") or {}),
                    nodes=[
                        WorkflowNodeWrite.model_validate(node)
                        for node in canonical.graph["nodes"]
                    ],
                    edges=[
                        WorkflowEdgeWrite.model_validate(edge)
                        for edge in canonical.graph["edges"]
                    ],
                    node_templates=list(
                        canonical.graph.get("node_templates") or []
                    ),
                    handle_templates=list(
                        canonical.graph.get("handle_templates") or []
                    ),
                    template_catalog_fingerprint=(
                        canonical.template_catalog_fingerprint
                    ),
                    trusted_authoring_graph=True,
                )
                workflow_created = True
                self._provision_domain_source(
                    registration=registration,
                    python_source=compilation.normalized_python_source,
                )
                source_registered = True
                return self._publish_imported_domain_workflow(
                    registration=registration,
                    python_source=compilation.normalized_python_source,
                    created_graph=created,
                )
            except Exception as error:
                rollback_failed = self._rollback_domain_import(
                    registration,
                    workflow_uuid=identity,
                    source_was_registered=source_registered,
                    workflow_was_created=workflow_created,
                )
                if rollback_failed:
                    raise WorkflowError("source_publication_failed") from error
                raise

    def import_python_workflow(
        self,
        *,
        file_name: str,
        python_source: str,
    ) -> Dict[str, Any]:
        """通过现有 AST 编译器原子导入一个 Python 工作流文件。

        参数：``file_name`` 是上传时的单个 ``.py`` 文件名，``python_source`` 是
        已按 UTF-8 解码的完整文件内容。返回：新建工作流的 Backend 形状完整图。
        异常：文件边界、AST 声明、动作模板、节点或连线不合法时返回稳定工作流
        错误；工作流、节点或连线身份已存在时返回冲突。

        安全不变量：源码只进入静态 AST 编译器，绝不 import 或执行；只有完整候选
        通过现有目录、图和身份校验后，才在进程内定义事务中创建定义和完整图。
        """

        try:
            # ``imported`` 冻结上传文件身份、工作流稳定 UUID 与源码摘要；此阶段
            # 尚未创建任何工作流定义事实。
            encoded = python_source.encode("utf-8")
            imported = validate_python_workflow_import(
                file_name=file_name,
                python_source=python_source,
                source_hash=_sha256(encoded),
            )
        except (AttributeError, PythonWorkflowImportError, UnicodeEncodeError):
            raise WorkflowError("invalid_input") from None
        if self.compiler is None:
            raise WorkflowError("template_catalog_unavailable")
        if self._source_target is None:
            raise WorkflowError("source_target_unavailable")
        registration = self._source_target.registration(
            workflow_uuid=imported.workflow_uuid,
            file_name=imported.file_name,
        )

        # ``initial_graph`` 是修订 1 的空基线，``compilation`` 只代表同一内存模板
        # 代际产生的静态候选，二者都不是已提交的工作流定义。
        initial_graph = imported.initial_graph()
        # 跨重启只保留可由领域包清单重建的来源事实；upload:// 追踪信息不能
        # 成为内存图与冷启动图之间的隐藏差异。
        initial_graph["workflow"]["meta_data"] = {
            "unilab": {
                "source_bootstrap": self._source_bootstrap_metadata(registration)
            }
        }
        try:
            compilation = CandidateCompilation.model_validate(
                self.compiler.compile(
                    workflow_uuid=imported.workflow_uuid,
                    workflow_revision=1,
                    python_source=imported.python_source,
                    source_uri=registration.source_uri,
                    applied_graph=initial_graph,
                )
            )
        except Exception:
            raise WorkflowError("internal_error") from None
        # ``candidate`` 经过图、源码范围、模板目录和全局节点/连线身份复核；只有
        # 签发成功后才能进入下面唯一的进程内定义事务。
        candidate = self._issue_candidate(
            workflow_revision=1,
            draft_hash=imported.source_hash,
            compilation=compilation,
            applied_graph=initial_graph,
            draft_python_source=imported.python_source,
        )
        if candidate is None:
            diagnostic = next(
                (
                    item
                    for item in compilation.diagnostics
                    if str(item.get("severity", "")).lower() == "error"
                ),
                None,
            )
            message = (
                str(diagnostic.get("message"))
                if isinstance(diagnostic, Mapping) and diagnostic.get("message")
                else "Python 工作流未能生成可信候选图"
            )
            raise WorkflowError("draft_invalid", message=message)

        graph = candidate["graph"]
        workflow = graph["workflow"]
        # 领域包来源证据属于系统保留元数据；编译器只追加创作合同，两者在首次
        # 内存事务前合并，避免生成文件、当前投影和冷启动投影出现隐藏差异。
        graph_meta_data = dict(workflow.get("meta_data") or {})
        initial_unilab = dict(
            initial_graph["workflow"].get("meta_data", {}).get("unilab", {})
        )
        candidate_unilab = dict(graph_meta_data.get("unilab") or {})
        graph_meta_data["unilab"] = {**initial_unilab, **candidate_unilab}
        return self._commit_python_domain_import(
            registration=registration,
            workflow=workflow,
            graph=graph,
            graph_meta_data=graph_meta_data,
            candidate=candidate,
        )

    def _commit_python_domain_import(
        self,
        *,
        registration: EditableSourceRegistration,
        workflow: Mapping[str, Any],
        graph: Mapping[str, Any],
        graph_meta_data: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """在工作流锁内原子提交 Python 定义、领域来源及创作状态。"""

        source_registered = False
        workflow_created = False
        with self._authoring_lock(registration.workflow_uuid):
            try:
                try:
                    created = self._definition_store.create_workflow_with_graph(
                        workflow_uuid=registration.workflow_uuid,
                        name=str(workflow["name"]),
                        tags=list(workflow.get("tags") or []),
                        description=workflow.get("description"),
                        meta_data=dict(graph_meta_data),
                        nodes=[
                            WorkflowNodeWrite.model_validate(node)
                            for node in graph["nodes"]
                        ],
                        edges=[
                            WorkflowEdgeWrite.model_validate(edge)
                            for edge in graph["edges"]
                        ],
                        node_templates=list(graph.get("node_templates") or []),
                        handle_templates=list(graph.get("handle_templates") or []),
                        template_catalog_fingerprint=str(
                            candidate["template_catalog_fingerprint"]
                        ),
                        trusted_authoring_graph=True,
                    )
                    workflow_created = True
                except StoreAuthoringConflict as error:
                    raise WorkflowConflict(error.code) from None
                except StoreNotFound:
                    raise WorkflowConflict("template_catalog_conflict") from None
                except StoreConflict:
                    raise WorkflowConflict("conflict") from None
                except (KeyError, TypeError, ValidationError, ValueError):
                    raise WorkflowError("candidate_invalid") from None
                normalized_source = candidate.get("normalized_python_source")
                if not isinstance(normalized_source, str) or not normalized_source:
                    raise WorkflowError("candidate_invalid")
                self._provision_domain_source(
                    registration=registration,
                    python_source=normalized_source,
                )
                source_registered = True
                return self._publish_imported_domain_workflow(
                    registration=registration,
                    python_source=normalized_source,
                    created_graph=created,
                )
            except Exception as error:
                rollback_failed = self._rollback_domain_import(
                    registration,
                    workflow_uuid=registration.workflow_uuid,
                    source_was_registered=source_registered,
                    workflow_was_created=workflow_created,
                )
                if rollback_failed:
                    raise WorkflowError("source_publication_failed") from error
                raise

    @staticmethod
    def _source_bootstrap_metadata(
        registration: EditableSourceRegistration,
    ) -> Dict[str, str]:
        """生成与冷启动骨架完全相同的领域来源追踪元数据。"""

        return {
            "kind": "editable_package_manifest",
            "package_id": registration.package_id,
            "relative_path": registration.relative_path,
            "source_uri": registration.source_uri,
        }

    @staticmethod
    def _authoring_graph_projection(graph: Mapping[str, Any]) -> Dict[str, Any]:
        """把公共读图收敛为创作编译器严格要求的五集合。"""

        fields = (
            "workflow",
            "nodes",
            "edges",
            "node_templates",
            "handle_templates",
        )
        try:
            return {field: deepcopy(graph[field]) for field in fields}
        except (KeyError, TypeError):
            raise WorkflowError("candidate_invalid") from None

    def _provision_domain_source(
        self,
        *,
        registration: EditableSourceRegistration,
        python_source: str,
    ) -> None:
        """发布已验证 Python 与 manifest 登记，供导入事务随后激活。"""

        if self._source_target is None:
            raise WorkflowError("source_target_unavailable")
        try:
            self._source_target.provision(
                registration=registration,
                python_source=python_source,
            )
        except DomainWorkflowSourceError as error:
            raise WorkflowError(error.code) from None

    def _registered_domain_source(
        self,
        workflow_uuid: str,
    ) -> EditableSourceRegistration:
        """读取当前内存目录中的领域来源身份并恢复为强类型对象。"""

        if self._source_target is None:
            raise WorkflowError("source_target_unavailable")
        try:
            row = self._definition_store.get_source_registration(workflow_uuid)
            return EditableSourceRegistration(
                workflow_uuid=str(row["workflow_uuid"]),
                package_id=str(row["package_id"]),
                package_root=Path(str(row["package_root"])),
                relative_path=str(row["relative_path"]),
                source_uri=str(row["source_uri"]),
            )
        except (KeyError, StoreNotFound):
            raise WorkflowError("source_target_unavailable") from None

    def _unregister_domain_source(
        self,
        registration: EditableSourceRegistration,
    ) -> None:
        """从唯一领域包 manifest 注销来源，保留未登记 Python 文件。"""

        if self._source_target is None:
            raise WorkflowError("source_target_unavailable")
        try:
            self._source_target.unregister(registration=registration)
        except DomainWorkflowSourceError as error:
            raise WorkflowError(error.code) from None

    def _remove_active_source_authorization(self, workflow_uuid: str) -> None:
        """在源码注销或导入回滚后移除本进程的来源授权。"""

        with self._active_sources_lock:
            self._active_source_workflow_uuids = frozenset(
                identity
                for identity in self._active_source_workflow_uuids
                if identity != workflow_uuid
            )
            self._active_source_dependencies.pop(workflow_uuid, None)

    def _rollback_domain_import(
        self,
        registration: EditableSourceRegistration,
        *,
        workflow_uuid: str,
        source_was_registered: bool,
        workflow_was_created: bool,
    ) -> bool:
        """补偿失败导入；返回是否有任一步补偿失败。"""

        rollback_failed = False
        if workflow_was_created and self._store.has_workflow_tasks(workflow_uuid):
            logger.error("拒绝回滚已有运行 Task 的工作流定义 %s", workflow_uuid)
            return True
        if source_was_registered:
            try:
                self._unregister_domain_source(registration)
            except WorkflowError:
                logger.exception("回滚工作流领域来源失败")
                # manifest 仍是重启权威；保留本进程定义和授权，避免当前进程与
                # 下次启动观察到两套相反事实。调用方返回可重试的发布失败。
                return True
        if workflow_was_created:
            self._remove_active_source_authorization(workflow_uuid)
            try:
                self._definition_store.discard_uncommitted_workflow(workflow_uuid)
            except StoreNotFound:
                pass
            except Exception:
                logger.exception("回滚进程内工作流定义失败")
                rollback_failed = True
        return rollback_failed

    def _publish_imported_domain_workflow(
        self,
        *,
        registration: EditableSourceRegistration,
        python_source: str,
        created_graph: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """登记已验证领域源码并发布刚创建的内存图。

        参数：来源已经写入领域包；``python_source`` 与 ``created_graph`` 已在发布
        前完成 AST、图和固定点校验。返回当前完整图。首次导入仍通过统一创作协调
        器记录已应用源码；source-only 候选不会推进 HTTP ``revision=1`` 合同。
        """

        del python_source, created_graph
        self._add_active_source_authorization(registration)
        self.reconcile_registered_source(
            registration.workflow_uuid,
            force_compile=True,
            preserve_author_source=True,
        )
        record = self._definition_store.get_authoring_record(
            registration.workflow_uuid
        )
        candidate = record.get("candidate")
        if isinstance(candidate, Mapping):
            candidate_hash = candidate.get("candidate_hash")
            if not isinstance(candidate_hash, str) or not candidate_hash:
                raise WorkflowError("candidate_invalid")
            self.apply_authoring(
                registration.workflow_uuid,
                candidate_hash=candidate_hash,
                preserve_author_source=True,
            )
        return self.get_graph(registration.workflow_uuid)

    def _locate_graph_entity(
        self,
        collection: str,
        entity_uuid: str,
    ) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        """通过公共存储投影定位全局节点或连线身份。"""

        try:
            identity = validate_uuid(entity_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        page = 1
        while True:
            result = self._definition_store.list_workflows(page=page, page_size=100)
            for workflow in result["items"]:
                graph = self.get_graph(workflow["uuid"])
                for entity in graph[collection]:
                    if entity["uuid"] == identity:
                        return workflow["uuid"], graph, entity
            if page * result["page_size"] >= result["total"]:
                break
            page += 1
        raise WorkflowError("not_found")

    @staticmethod
    def _graph_entity(
        graph: Mapping[str, Any],
        collection: str,
        entity_uuid: str,
    ) -> Dict[str, Any]:
        """从同一图快照读取一个稳定身份实体。"""

        for entity in graph[collection]:
            if entity["uuid"] == entity_uuid:
                return entity
        raise WorkflowError("not_found")

    # 工作流任务（WorkflowTask）与工作流节点作业（WorkflowNodeJob） --------

    def create_workflow_task(
        self,
        *,
        workflow_uuid: str,
        run_mode: str,
        target_node_uuid: Optional[str],
        input_value: Dict[str, Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
        inventory_bindings: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """从已应用工作流图创建一次工作流任务（WorkflowTask）及其作业。

        参数：``workflow_uuid`` 是工作流定义身份；``run_mode`` 是普通、单步或
        单节点运行模式；``target_node_uuid`` 是单节点运行目标；``input_value``
        是任务输入；``description`` 与 ``meta_data`` 是用户说明和公开元数据；
        ``inventory_bindings`` 把本次执行的逻辑数量需求绑定到具体试剂或当前
        内容物库存。
        返回：同一事务创建的工作流任务及工作流节点作业（WorkflowNodeJob）投影。
        异常：身份、运行模式、输入或执行计划不合法时抛出稳定工作流错误；输入
        合同解析、默认值填充与计划绑定全部在同一创建事务的首次写入前完成。
        """

        workflow_uuid = self.get_workflow(workflow_uuid)["uuid"]
        run_mode = "normal" if run_mode == "" else run_mode
        if run_mode not in {"normal", "step", "single_node"}:
            raise WorkflowError("invalid_input")
        if target_node_uuid is not None:
            try:
                target_node_uuid = validate_uuid(target_node_uuid)
            except ValueError:
                raise WorkflowError("invalid_input") from None
        try:
            input_value = normalize_json_object(input_value)
            meta_data = normalize_json_object(meta_data)
            normalized_inventory_bindings = [
                normalize_json_object(binding)
                for binding in (inventory_bindings or [])
            ]
        except ValueError:
            raise WorkflowError("invalid_input") from None
        description = self._optional_text(description)
        task_uuid = str(uuid4())

        def plan_builder(graph: Dict[str, Any]) -> PreparedTaskInput:
            """在创建事务内冻结本次工作流任务（WorkflowTask）输入和计划。

            参数：``graph`` 是同一事务读取的已应用工作流图。返回：规范输入、
            工作流快照、执行计划（ExecutionPlan）和首次工作流节点作业
            （WorkflowNodeJob）的不可变创建载荷。异常：计划构建或输入绑定失败
            时保留原始领域错误，使外层映射为稳定公共错误且事务零写入。
            """

            return self._prepare_task_input(
                graph,
                input_value=input_value,
                run_mode=run_mode,
                target_node_uuid=target_node_uuid,
            )

        def inventory_allocation_builder(
            connection: Any,
            graph: Dict[str, Any],
            prepared: PreparedTaskInput,
        ) -> List[Dict[str, Any]]:
            """在 Task/Jobs 首次写入前校验并冻结数量型库存分配。

            参数：``connection`` 是工作流写事务；``graph/prepared`` 来自同一
            应用图。返回：现有分配表待插入行。异常：执行包含库存需求但没有
            可用调度桥/库存权威时关闭式失败，禁止创建无法执行的半任务。
            """

            prepare = getattr(
                self._task_scheduler_bridge,
                "prepare_inventory_allocations",
                None,
            )
            if callable(prepare):
                return prepare(
                    connection,
                    graph=graph,
                    prepared=prepared,
                    task_uuid=task_uuid,
                    bindings=normalized_inventory_bindings,
                )
            active_nodes = {
                str(job.get("workflow_node_uuid")) for job in prepared.jobs
            }
            has_active_requirements = any(
                isinstance(requirement, Mapping)
                and str(requirement.get("consume_node_uuid")) in active_nodes
                for requirement in graph.get("inventory_requirements", [])
            )
            if has_active_requirements or normalized_inventory_bindings:
                raise StoreConflict("工作流数量型库存未装配本地库存权威")
            return []

        task_created = False

        def discard_uncommitted_inventory() -> None:
            """补偿本次 Task 创建回滚前写入库存权威的数量预留。"""

            discard = getattr(
                self._task_scheduler_bridge,
                "discard_uncommitted_inventory",
                None,
            )
            if callable(discard):
                discard(task_uuid)

        try:
            with self._authoring_lock(workflow_uuid):
                applied_graph = self.get_graph(workflow_uuid)
                task = self._store.create_task_with_jobs(
                    workflow_uuid=workflow_uuid,
                    task_uuid=task_uuid,
                    run_mode=run_mode,
                    target_node_uuid=target_node_uuid,
                    description=description,
                    meta_data=meta_data,
                    plan_builder=plan_builder,
                    inventory_allocation_builder=inventory_allocation_builder,
                    applied_graph=applied_graph,
                )
            task_created = True
            if self._task_scheduler_bridge is None:
                return task
            # ``aggregate`` 来自调度同步推进后的标准持久投影，不返回创建事务中的
            # 过期 ``pending`` 快照。
            aggregate = self._task_scheduler_bridge.submit(task)
            return aggregate["task"]
        except (TaskSchedulerBridgeError, TaskInputError, StoreConflict) as error:
            if not task_created:
                try:
                    discard_uncommitted_inventory()
                except Exception:
                    raise WorkflowError("internal_error") from None
            if isinstance(error, TaskSchedulerBridgeError):
                raise WorkflowError("internal_error") from None
            raise WorkflowError("invalid_input") from None
        except Exception:
            if not task_created:
                try:
                    discard_uncommitted_inventory()
                except Exception:
                    raise WorkflowError("internal_error") from None
            raise

    def command_workflow_task(
        self,
        task_uuid: str,
        *,
        command_type: str,
        target_node_uuid: Optional[str],
        idempotency_key: str,
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """幂等执行本地工作流任务控制命令。

        暂停只阻止后续派发，不中断已在途设备动作；恢复、单步和取消都复用
        同一个 Task 身份。返回与 Backend 相同的 WorkflowTaskCommand 投影。
        """

        try:
            task_uuid = validate_uuid(task_uuid)
            if command_type not in {"step", "pause", "resume", "cancel"}:
                raise WorkflowError("invalid_input")
            if target_node_uuid is not None:
                target_node_uuid = validate_uuid(target_node_uuid)
            normalized_key = str(idempotency_key).strip()
            if not normalized_key:
                raise WorkflowError("invalid_input")
            meta_data = normalize_json_object(meta_data)
            description = self._optional_text(description)
            task = self._store.get_task(task_uuid)
            if task.get("status") in {
                "succeeded",
                "success",
                "failed",
                "canceled",
                "timeout",
            }:
                raise WorkflowError("invalid_input")
            if command_type == "step" and (
                task.get("run_mode") != "step"
                or task.get("control_status") != "paused"
            ):
                raise WorkflowError("invalid_input")
            command, created = self._store.create_task_command(
                task_uuid=task_uuid,
                command_uuid=str(uuid4()),
                command_type=command_type,
                target_node_uuid=target_node_uuid,
                idempotency_key=normalized_key,
                description=description,
                meta_data=meta_data,
            )
            if not created or command["status"] != "pending":
                return command
            if self._task_scheduler_bridge is None:
                return self._store.complete_task_command(
                    command["uuid"],
                    status="rejected",
                    result={"reason": "scheduler_unavailable"},
                )
            try:
                if command_type == "step":
                    result = self._task_scheduler_bridge.step(
                        task_uuid,
                        target_node_uuid=target_node_uuid,
                    )
                elif command_type == "pause":
                    result = self._task_scheduler_bridge.pause(task_uuid)
                elif command_type == "resume":
                    result = self._task_scheduler_bridge.resume(task_uuid)
                else:
                    result = self._task_scheduler_bridge.cancel(
                        task_uuid,
                        command_uuid=command["uuid"],
                    )
            except TaskSchedulerBridgeError as error:
                return self._store.complete_task_command(
                    command["uuid"],
                    status="rejected",
                    result={"reason": str(error)},
                )
            return self._store.complete_task_command(
                command["uuid"],
                status="succeeded",
                result=result,
            )
        except WorkflowError:
            raise
        except (StoreNotFound, StoreConflict, ValueError):
            raise WorkflowError("invalid_input") from None

    def create_debug_workflow_task(
        self,
        *,
        workflow_uuid: str,
        start_node_uuids: List[str],
        breakpoint_node_uuids: List[str],
        input_value: Dict[str, Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """创建带不可变起始点、断点和首个 Admission Hold 的调试任务。"""

        workflow_uuid = self.get_workflow(workflow_uuid)["uuid"]
        try:
            normalized_starts = [validate_uuid(value) for value in start_node_uuids]
            normalized_breakpoints = [
                validate_uuid(value) for value in breakpoint_node_uuids
            ]
            if len(normalized_starts) != 1 or len(set(normalized_starts)) != 1:
                raise ValueError
            if len(set(normalized_breakpoints)) != len(normalized_breakpoints):
                raise ValueError
            input_value = normalize_json_object(input_value)
            meta_data = normalize_json_object(meta_data)
        except (TypeError, ValueError):
            raise WorkflowError("invalid_input") from None
        description = self._optional_text(description)
        meta_data = {**meta_data, "debug": True}
        start_node_uuid = normalized_starts[0]

        def plan_builder(graph: Dict[str, Any]) -> PreparedTaskInput:
            prepared = self._prepare_task_input(
                graph,
                input_value=input_value,
                run_mode="step",
                target_node_uuid=None,
            )
            return self._scope_debug_task_input(
                prepared,
                start_node_uuid=start_node_uuid,
                breakpoint_node_uuids=normalized_breakpoints,
            )

        try:
            with self._authoring_lock(workflow_uuid):
                applied_graph = self.get_graph(workflow_uuid)
                task = self._store.create_task_with_jobs(
                    workflow_uuid=workflow_uuid,
                    task_uuid=str(uuid4()),
                    run_mode="step",
                    target_node_uuid=None,
                    description=description,
                    meta_data=meta_data,
                    plan_builder=plan_builder,
                    applied_graph=applied_graph,
                )
                self._store.create_debug_configuration(
                    task_uuid=task["uuid"],
                    start_node_uuids=normalized_starts,
                    breakpoint_node_uuids=normalized_breakpoints,
                )
            if self._task_scheduler_bridge is None:
                return task
            return self._task_scheduler_bridge.submit(task)["task"]
        except (TaskInputError, StoreConflict, ValueError):
            raise WorkflowError("invalid_input") from None
        except TaskSchedulerBridgeError:
            raise WorkflowError("internal_error") from None

    def get_debug_workflow_task(self, task_uuid: str) -> Dict[str, Any]:
        """返回标准 Task/Jobs 与调试配置、范围和 Hold 的三源一致投影。"""

        try:
            task_uuid = validate_uuid(task_uuid)
            task = self._store.get_task(task_uuid)
            jobs = self._store.list_jobs(task_uuid)
            debug = self._store.get_debug_projection(task_uuid)
        except (StoreNotFound, ValueError):
            raise WorkflowError("not_found") from None
        snapshot_nodes = task.get("workflow_snapshot", {}).get("nodes", [])
        disabled = [
            str(node.get("uuid"))
            for node in snapshot_nodes
            if isinstance(node, Mapping) and node.get("disabled") is True
        ]
        enabled = [
            str(node.get("uuid"))
            for node in snapshot_nodes
            if isinstance(node, Mapping)
            and node.get("disabled") is not True
            and node.get("uuid")
        ]
        active = [
            str(node.get("uuid"))
            for node in task.get("execution_plan", {}).get("nodes", [])
            if isinstance(node, Mapping) and node.get("uuid")
        ]
        active_set = set(active)
        return {
            "task": task,
            "jobs": jobs,
            **debug,
            "active_node_uuids": active,
            "out_of_scope_node_uuids": [
                node_uuid for node_uuid in enabled if node_uuid not in active_set
            ],
            "disabled_node_uuids": disabled,
        }

    def command_debug_workflow_task(
        self,
        task_uuid: str,
        *,
        command_type: str,
        scope_type: str,
        hold_uuid: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """按精确 Hold 范围幂等执行调试单步或继续。"""

        try:
            task_uuid = validate_uuid(task_uuid)
            hold_uuid = validate_uuid(hold_uuid)
            if command_type not in {"step", "continue"} or scope_type != "hold":
                raise ValueError
            normalized_key = str(idempotency_key).strip()
            if not normalized_key:
                raise ValueError
            command, created, node_uuid = self._store.begin_debug_command(
                task_uuid=task_uuid,
                command_uuid=str(uuid4()),
                command_type=command_type,
                hold_uuid=hold_uuid,
                idempotency_key=normalized_key,
            )
            if not created or command["status"] != "pending":
                return command
            if self._task_scheduler_bridge is None:
                return self._store.complete_debug_command(
                    command["uuid"],
                    status="rejected",
                    result={"reason": "scheduler_unavailable"},
                )
            try:
                result = self._task_scheduler_bridge.step(
                    task_uuid,
                    target_node_uuid=node_uuid,
                )
            except TaskSchedulerBridgeError as error:
                return self._store.complete_debug_command(
                    command["uuid"],
                    status="rejected",
                    result={"reason": str(error)},
                )
            return self._store.complete_debug_command(
                command["uuid"], status="succeeded", result=result
            )
        except WorkflowError:
            raise
        except (StoreNotFound, StoreConflict, TypeError, ValueError):
            raise WorkflowError("invalid_input") from None

    def create_device_action_run(
        self,
        *,
        material_uuid: str,
        workflow_node_template_uuid: str,
        param: Optional[Dict[str, Any]],
        execution_policy: Optional[Dict[str, Any]],
        idempotency_key: str,
        description: Optional[str],
        meta_data: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """创建或幂等复用设备单动作运行（DeviceActionRun）。

        参数与 Backend ``POST /device-action-runs`` DTO 同名；返回标准工作流任务
        （WorkflowTask）、唯一工作流节点作业（WorkflowNodeJob）和 ``created``。
        输入/引用错误、依赖未装配和幂等冲突分别映射为稳定 HTTP 业务错误。
        公共任务调度桥失败映射为 ``internal_error``，且不创建第二套执行身份。
        """

        try:
            # ``aggregate`` 是已原子持久化的标准工作流任务（WorkflowTask）和
            # 工作流节点作业（WorkflowNodeJob）；只有首次创建才允许物理派发。
            aggregate = self._device_action_runs.create(
                material_uuid=material_uuid,
                workflow_node_template_uuid=workflow_node_template_uuid,
                param=param,
                execution_policy=execution_policy,
                idempotency_key=idempotency_key,
                description=description,
                meta_data=meta_data,
            )
            if aggregate["created"] is True and self._task_scheduler_bridge is not None:
                scheduled = self._task_scheduler_bridge.submit(aggregate["task"])
                # ``scheduled_jobs`` 是公共桥返回的同一任务作业集合；设备单动作
                # 必须仍精确包含创建事务生成的唯一作业身份。
                scheduled_jobs = [
                    job
                    for job in scheduled["jobs"]
                    if job.get("uuid") == aggregate["job"]["uuid"]
                ]
                if len(scheduled_jobs) != 1:
                    raise TaskSchedulerBridgeError(
                        "设备单动作调度结果缺少唯一原始作业身份"
                    )
                # 公共桥可能同步推进首次派发，必须返回同一任务/作业身份的刷新状态，
                # 不能把创建事务中的 ``pending`` 快照误报给前端。
                aggregate = {
                    "task": scheduled["task"],
                    "job": scheduled_jobs[0],
                    "created": True,
                }
            return aggregate
        except DeviceActionRunInputError:
            raise WorkflowError("invalid_input") from None
        except DeviceActionRunUnavailable:
            raise WorkflowError("template_catalog_unavailable") from None
        except DeviceActionRunConflict:
            raise WorkflowConflict("conflict") from None
        except TaskSchedulerBridgeError:
            raise WorkflowError("internal_error") from None

    def list_task_inventory_consumptions(
        self, task_uuid: str
    ) -> List[Dict[str, Any]]:
        """读取一个工作流任务的数量型库存消费事实。

        参数：``task_uuid`` 是任务身份。返回：统一库存台账还原的消费列表；未
        装配库存权威时为空。异常：非法任务身份映射为公共参数错误。
        """

        try:
            task_uuid = validate_uuid(task_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        try:
            self._store.get_task(task_uuid)
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        reader = getattr(
            self._task_scheduler_bridge,
            "list_task_inventory_consumptions",
            None,
        )
        return reader(task_uuid) if callable(reader) else []

    def list_job_inventory_consumptions(
        self, job_uuid: str
    ) -> List[Dict[str, Any]]:
        """读取一个工作流节点作业的数量型库存消费事实。

        参数：``job_uuid`` 是作业身份。返回：统一库存台账还原的消费列表；未
        装配库存权威时为空。异常：非法作业身份映射为公共参数错误。
        """

        try:
            job_uuid = validate_uuid(job_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        try:
            self._store.get_job(job_uuid)
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        reader = getattr(
            self._task_scheduler_bridge,
            "list_job_inventory_consumptions",
            None,
        )
        return reader(job_uuid) if callable(reader) else []

    def list_reagent_inventory_consumptions(
        self, reagent_uuid: str
    ) -> List[Dict[str, Any]]:
        """读取一个试剂库存实例的工作流消费谱系。

        参数：``reagent_uuid`` 是库存实例身份。返回：统一库存台账还原的消费
        列表，允许已删除库存保留历史；未装配库存权威时为空。异常：非法身份映射
        为公共参数错误。
        """

        try:
            reagent_uuid = validate_uuid(reagent_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        reader = getattr(
            self._task_scheduler_bridge,
            "list_reagent_inventory_consumptions",
            None,
        )
        return reader(reagent_uuid) if callable(reader) else []

    def get_workflow_task(self, task_uuid: str) -> Dict[str, Any]:
        try:
            identity = validate_uuid(task_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        try:
            return self._store.get_task(identity)
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def list_workflow_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        workflow_uuid: Optional[str] = None,
        execution_kind: str = "",
        status: str = "",
        cleanup_status: str = "",
    ) -> Dict[str, Any]:
        """按后端（Backend）查询合同分页读取工作流任务（WorkflowTask）。

        参数：分页字段限定结果窗口；``workflow_uuid`` 限定工作流定义；
        ``execution_kind`` 区分工作流与直接设备动作来源；状态字段限定业务和清理
        生命周期。返回分页投影，非法枚举或 UUID 映射为稳定输入错误。
        """

        page, page_size = self._normalize_page(page, page_size)
        if workflow_uuid is not None:
            try:
                workflow_uuid = validate_uuid(workflow_uuid)
            except ValueError:
                raise WorkflowError("invalid_input") from None
        status = status.strip().lower()
        execution_kind = execution_kind.strip().lower()
        cleanup_status = cleanup_status.strip().lower()
        if execution_kind and execution_kind not in {
            "workflow",
            "ad_hoc_device_action",
        }:
            raise WorkflowError("invalid_input")
        if status and status not in {
            "pending",
            "running",
            "canceling",
            "succeeded",
            "failed",
            "canceled",
            "timeout",
        }:
            raise WorkflowError("invalid_input")
        if cleanup_status and cleanup_status not in {
            "none",
            "pending",
            "canceling",
            "settled",
            "requires_attention",
        }:
            raise WorkflowError("invalid_input")
        return self._store.list_tasks(
            page=page,
            page_size=page_size,
            workflow_uuid=workflow_uuid,
            execution_kind=execution_kind,
            status=status,
            cleanup_status=cleanup_status,
        )

    def list_workflow_node_jobs(self, task_uuid: str) -> List[Dict[str, Any]]:
        identity = self.get_workflow_task(task_uuid)["uuid"]
        return self._store.list_jobs(identity)

    def list_workflow_task_runtime_events(
        self,
        task_uuid: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """分页读取任务的持久运行事件与动作下发/结果载荷。"""

        try:
            identity = validate_uuid(task_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
            or after_sequence > (1 << 63) - 1
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
            raise WorkflowError("invalid_input")
        try:
            return self._store.list_task_runtime_events(
                identity,
                after_sequence=after_sequence,
                limit=limit,
            )
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def get_workflow_node_job(self, job_uuid: str) -> Dict[str, Any]:
        try:
            identity = validate_uuid(job_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        try:
            return self._store.get_job(identity)
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def list_workflow_node_job_feedback(
        self,
        job_uuid: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """分页读取单个作业已经提交的过程反馈证据。"""

        try:
            identity = validate_uuid(job_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        page, page_size = self._normalize_page(page, page_size)
        try:
            return self._job_evidence_store().list_feedback(
                job_uuid=identity,
                page=page,
                page_size=page_size,
            )
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def get_manual_confirmation(self, confirmation_uuid: str) -> Dict[str, Any]:
        """读取一条人工确认事实。"""

        try:
            identity = validate_uuid(confirmation_uuid)
            return self._manual_confirmation_store().get(identity)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def resolve_uncertain_job(
        self,
        job_uuid: str,
        *,
        resolution: str,
        reason: str,
        device_command_id: str | None,
    ) -> Dict[str, Any]:
        """安全请求取消 execution_unknown 设备作业，等待 Edge 提交证明。"""

        try:
            identity = validate_uuid(job_uuid)
            normalized_resolution = resolution.strip().lower()
            normalized_reason = reason.strip()
            if normalized_resolution != "canceled" or not normalized_reason:
                raise ValueError
            job = self._store.get_job(identity)
            control = job.get("control_data")
            manual = (
                control.get("manual_resolution")
                if isinstance(control, Mapping)
                else None
            )
            if job.get("status") == "canceled" and isinstance(manual, Mapping):
                if (
                    manual.get("resolution") != "canceled"
                    or manual.get("reason") != normalized_reason
                ):
                    raise StoreConflict("作业已经按另一人工结论终结")
                return {
                    "job": job,
                    "resolution_command_uuid": manual.get("command_uuid"),
                    "pending_edge_confirmation": False,
                    "created": False,
                }
            if job.get("status") != "execution_unknown":
                raise StoreConflict("作业不是 execution_unknown")
            if self._task_scheduler_bridge is None:
                raise StoreConflict("当前模式没有本地 UNKNOWN 处置端口")
            command_id = (
                str(device_command_id).strip()
                if device_command_id is not None
                else f"workflow-node-job:{identity}"
            )
            if not command_id:
                raise ValueError
            return self._task_scheduler_bridge.request_uncertain_resolution(
                identity,
                reason=normalized_reason,
                device_command_id=command_id,
            )
        except ValueError:
            raise WorkflowError("invalid_input") from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        except (StoreConflict, TaskSchedulerBridgeError):
            raise WorkflowConflict("conflict") from None

    def list_task_manual_confirmations(self, task_uuid: str) -> List[Dict[str, Any]]:
        """按开启时间倒序读取任务的全部人工确认。"""

        try:
            identity = validate_uuid(task_uuid)
            return self._manual_confirmation_store().list_by_task(identity)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def decide_manual_confirmation(
        self,
        confirmation_uuid: str,
        *,
        action: str,
        confirmed_by: str,
        comment: str | None,
        idempotency_key: str,
        param: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        """幂等批准或拒绝人工确认，并恢复同一工作流作业。"""

        try:
            identity = validate_uuid(confirmation_uuid)
            confirmation, _created = self._manual_confirmation_store().decide(
                identity,
                action=action,
                confirmed_by=confirmed_by,
                comment=comment,
                idempotency_key=idempotency_key,
                param=param,
            )
            if self._task_scheduler_bridge is not None:
                self._task_scheduler_bridge.decide_manual_confirmation(
                    confirmation["workflow_node_job_uuid"],
                    approved=confirmation["status"] == "approved",
                    param=(
                        confirmation["param"]
                        if confirmation["status"] == "approved"
                        else None
                    ),
                )
            return self._manual_confirmation_store().get(identity)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        except (StoreConflict, TaskSchedulerBridgeError):
            raise WorkflowConflict("conflict") from None

    def open_workflow_intervention_from_report(
        self,
        report: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """把本地设备异常报告持久化为公共工作流干预。"""

        try:
            return self._intervention_store().open_from_report(report)
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        except StoreConflict:
            raise WorkflowConflict("conflict") from None

    def list_workflow_interventions(
        self,
        *,
        status: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """按状态读取当前工作流干预。"""

        try:
            return self._intervention_store().list(status=status, limit=limit)
        except StoreConflict:
            raise WorkflowError("invalid_input") from None

    def get_workflow_intervention(
        self,
        intervention_uuid: str,
    ) -> Dict[str, Any]:
        """读取一条工作流干预事实。"""

        try:
            return self._intervention_store().get(validate_uuid(intervention_uuid))
        except ValueError:
            raise WorkflowError("invalid_input") from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None

    def select_workflow_intervention(
        self,
        intervention_uuid: str,
        *,
        revision: int,
        option_id: str,
        idempotency_key: str,
        result: Any = None,
    ) -> Dict[str, Any]:
        """幂等选择一个设备已提供的干预方案并投递给原设备动作。"""

        try:
            identity = validate_uuid(intervention_uuid)
            intervention, _created = self._intervention_store().select(
                identity,
                revision=revision,
                option_id=option_id,
                idempotency_key=idempotency_key,
                result=result,
            )
            if intervention["delivery_status"] == "accepted":
                return {
                    "intervention": intervention,
                    "command_uuid": intervention["edge_command_uuid"],
                    "created": False,
                }
            if not self._deliver_workflow_intervention(intervention):
                raise WorkflowConflict("conflict")
            delivered = self._intervention_store().get(identity)
            return {
                "intervention": delivered,
                "command_uuid": delivered["edge_command_uuid"],
                "created": _created,
            }
        except ValueError:
            raise WorkflowError("invalid_input") from None
        except StoreNotFound:
            raise WorkflowError("not_found") from None
        except StoreConflict:
            raise WorkflowConflict("conflict") from None

    def _deliver_workflow_intervention(
        self,
        intervention: Mapping[str, Any],
    ) -> bool:
        """用持久冻结载荷向本地设备幂等投递一次干预决定。

        参数：``intervention`` 是已选干预投影。返回：设备明确接受时为真；端口
        缺失或拒绝时为假并把状态记为 ``unknown``。异常：持久事实损坏或数据库
        写失败原样传播；同一 ``edge_command_uuid`` 始终作为稳定投递身份。
        """

        identity = str(intervention["uuid"])
        delivery = self._intervention_delivery
        if delivery is None:
            self._intervention_store().mark_delivery(identity, accepted=False)
            return False
        meta_data = intervention.get("meta_data")
        payload = (
            dict(meta_data["delivery_payload"])
            if isinstance(meta_data, Mapping)
            and isinstance(meta_data.get("delivery_payload"), Mapping)
            else {
                "option": dict(intervention["selected_option"]),
                "action": str(
                    intervention["selected_option"].get("action")
                    or intervention.get("selected_option_id")
                    or ""
                ),
                **(
                    {"result": intervention["selected_option"]["result"]}
                    if "result" in intervention["selected_option"]
                    else {}
                ),
            }
        )
        accepted = delivery.resolve_error_decision(
            str(intervention["edge_command_uuid"]),
            payload,
        )
        self._intervention_store().mark_delivery(identity, accepted=accepted)
        return accepted

    def _build_execution_plan(
        self,
        graph: Dict[str, Any],
        *,
        run_mode: str,
        target_node_uuid: Optional[str],
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """委托深模块构造执行计划（ExecutionPlan）与首次作业集合。

        参数：``graph`` 是冻结应用图，``run_mode`` 是运行模式，
        ``target_node_uuid`` 是单节点目标。返回：唯一版本化计划和待持久化作业。
        异常：图、物料来源（MaterialSource）或目标非法时由构建器失败关闭。
        """

        return ExecutionPlanBuilder().build(
            graph,
            run_mode=run_mode,
            target_node_uuid=target_node_uuid,
        )

    def _prepare_task_input(
        self,
        graph: Dict[str, Any],
        *,
        input_value: Dict[str, Any],
        run_mode: str,
        target_node_uuid: Optional[str],
    ) -> PreparedTaskInput:
        """从同一应用图构造计划并冻结工作流任务（WorkflowTask）输入。

        参数：``graph`` 是创建事务读取的应用图；``input_value`` 是规范 JSON
        请求对象；``run_mode`` 和 ``target_node_uuid`` 决定活动计划范围。返回：
        已解析输入、快照、执行计划（ExecutionPlan）和首次作业。异常：计划或
        输入绑定不合法时保留构建器/``TaskInputError`` 以映射稳定业务错误。
        """

        plan, jobs = self._build_execution_plan(
            graph,
            run_mode=run_mode,
            target_node_uuid=target_node_uuid,
        )
        return prepare_task_input(
            graph=graph,
            raw_input=input_value,
            execution_plan=plan,
            jobs=jobs,
            resource_resolver=self._material_resolver,
        )

    @staticmethod
    def _scope_debug_task_input(
        prepared: PreparedTaskInput,
        *,
        start_node_uuid: str,
        breakpoint_node_uuids: List[str],
    ) -> PreparedTaskInput:
        """把已验证计划裁成从起始点可达的活动子图，快照保持完整。"""

        plan = dict(prepared.execution_plan)
        nodes = [dict(node) for node in plan.get("nodes", [])]
        node_ids = {str(node.get("uuid") or "") for node in nodes}
        if start_node_uuid not in node_ids:
            raise StoreConflict("debug start node is not enabled and executable")
        snapshot_nodes = prepared.workflow_snapshot.get("nodes", [])
        enabled_snapshot_ids = {
            str(node.get("uuid") or "")
            for node in snapshot_nodes
            if isinstance(node, Mapping) and node.get("disabled") is not True
        }
        if any(node_uuid not in enabled_snapshot_ids for node_uuid in breakpoint_node_uuids):
            raise StoreConflict("debug breakpoint node is not enabled")
        outgoing: Dict[str, List[str]] = {}
        for edge in plan.get("edges", []):
            if not isinstance(edge, Mapping):
                continue
            source = str(edge.get("source_node_uuid") or "")
            target = str(edge.get("target_node_uuid") or "")
            outgoing.setdefault(source, []).append(target)
        reachable: set[str] = set()
        pending = [start_node_uuid]
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(outgoing.get(current, []))
        # 调试起点只裁掉此前的物理动作；MaterialSource 是任务级物料准入，
        # 不是可以跳过的设备动作。资源可能先经过被跳过动作的 ResourceSlot
        # 透传链，再进入活动子图。此时把来源的冻结绑定目标重接到活动子图的
        # 第一个消费者，既不补跑起点前的物理动作，也不会丢失稳定物料身份。
        resource_outgoing: Dict[str, List[Mapping[str, Any]]] = {}
        for edge in plan.get("edges", []):
            if not isinstance(edge, Mapping):
                continue
            if (
                edge.get("dependency_only") is True
                or edge.get("source_type") != "ResourceSlot"
                or edge.get("target_type") != "ResourceSlot"
            ):
                continue
            resource_outgoing.setdefault(
                str(edge.get("source_node_uuid") or ""),
                [],
            ).append(edge)
        supporting_material_sources: set[str] = set()
        for node in nodes:
            if str(node.get("kind") or "") != "material_source":
                continue
            source_uuid = str(node.get("uuid") or "")
            raw_targets = node.get("material_binding_targets", [])
            if not isinstance(raw_targets, list):
                continue
            rebound_targets: List[Dict[str, str]] = []
            seen_targets: set[tuple[str, str]] = set()

            def append_target(target_uuid: str, param_key: str) -> None:
                identity = (target_uuid, param_key)
                if not target_uuid or not param_key or identity in seen_targets:
                    return
                seen_targets.add(identity)
                rebound_targets.append(
                    {
                        "workflow_node_uuid": target_uuid,
                        "param_key": param_key,
                    }
                )

            for target in raw_targets:
                if not isinstance(target, Mapping):
                    continue
                target_uuid = str(target.get("workflow_node_uuid") or "")
                if target_uuid in reachable:
                    append_target(target_uuid, str(target.get("param_key") or ""))

            visited_resource_nodes = {source_uuid}
            pending_resource_nodes = [source_uuid]
            while pending_resource_nodes:
                current = pending_resource_nodes.pop()
                for edge in resource_outgoing.get(current, []):
                    target_uuid = str(edge.get("target_node_uuid") or "")
                    if target_uuid in reachable:
                        append_target(
                            target_uuid,
                            str(edge.get("target_data_key") or ""),
                        )
                        continue
                    if target_uuid and target_uuid not in visited_resource_nodes:
                        visited_resource_nodes.add(target_uuid)
                        pending_resource_nodes.append(target_uuid)
            if rebound_targets:
                supporting_material_sources.add(source_uuid)
                node["material_binding_targets"] = rebound_targets
        scoped_node_ids = reachable | supporting_material_sources
        plan["nodes"] = [
            node for node in nodes if str(node.get("uuid")) in scoped_node_ids
        ]
        plan["edges"] = [
            edge
            for edge in plan.get("edges", [])
            if str(edge.get("source_node_uuid")) in scoped_node_ids
            and str(edge.get("target_node_uuid")) in scoped_node_ids
        ]
        plan["handles"] = [
            handle
            for handle in plan.get("handles", [])
            if str(handle.get("node_uuid")) in scoped_node_ids
        ]
        jobs = [
            job
            for job in prepared.jobs
            if str(job.get("workflow_node_uuid")) in scoped_node_ids
        ]
        if not jobs:
            raise StoreConflict("debug task has no reachable jobs")
        return PreparedTaskInput(
            workflow_snapshot=prepared.workflow_snapshot,
            resolved_input=prepared.resolved_input,
            execution_plan=plan,
            jobs=jobs,
        )

    # 工作流创作（Authoring） ---------------------------------------------

    def replace_discovered_source_authorizations(
        self,
        plan: EditableSourceDiscoveryPlan,
    ) -> List[Dict[str, Any]]:
        """原子安装发现计划并替换当前进程活动源码授权集合。

        参数：``plan`` 是从全部显式授权目录完成预校验后生成的不可变计划。
        返回：按计划顺序排列的进程内来源记录；成功后活动授权恰好等于本计划。
        异常：软删除工作流、来源身份或目录安全冲突映射为稳定
        ``invalid_input``，且不提交任何部分定义、来源或创作事实。
        """

        if not isinstance(plan, EditableSourceDiscoveryPlan):
            raise WorkflowError("invalid_input")
        # ``root_paths`` 是计划声称已固定的全部包目录；每项注册必须且只能引用它们。
        root_paths = tuple(
            package_root for package_root, _identity in plan.root_identities
        )
        registered_roots = {
            registration.package_root for registration in plan.registrations
        }
        if (
            len(root_paths) != len(set(root_paths))
            or any(not package_root.is_absolute() for package_root in root_paths)
            # 一个新领域包可以先只有合法 package 身份、尚无工作流；它仍是本次
            # 启动明确授权的写入目标。已有注册必须来自这些根，但不再反向要求
            # 每个根至少声明一项工作流。
            or not registered_roots.issubset(set(root_paths))
            or any(
                registration.source_uri
                != (f"package://{registration.package_id}/{registration.relative_path}")
                for registration in plan.registrations
            )
        ):
            raise WorkflowError("invalid_input")
        # ``incoming_workflow_uuids`` 是计划将保留授权的完整新集合。
        incoming_workflow_uuids = frozenset(
            {registration.workflow_uuid for registration in plan.registrations}
        )
        source_dependencies: dict[str, frozenset[str]] = {}
        for registration in plan.registrations:
            dependencies = registration.dependency_workflow_uuids
            if (
                not isinstance(dependencies, tuple)
                or any(not isinstance(item, str) for item in dependencies)
                or not set(dependencies).issubset(incoming_workflow_uuids)
            ):
                raise WorkflowError("invalid_input")
            source_dependencies[registration.workflow_uuid] = frozenset(dependencies)
        # ``registration_rows`` 是交给进程内定义目录的完整、不可变批次。
        registration_rows = tuple(
            {
                "workflow_uuid": registration.workflow_uuid,
                "package_id": registration.package_id,
                "package_root": str(registration.package_root),
                "relative_path": registration.relative_path,
                "source_uri": registration.source_uri,
            }
            for registration in plan.registrations
        )
        with self._source_authorization_replacement_lock:
            with self._active_sources_lock:
                current_workflow_uuids = self._active_source_workflow_uuids
            # ``locked_workflow_uuids`` 同时覆盖将撤销和将激活的身份；稳定排序避免
            # 多工作流保存、读取与授权替换形成锁顺序反转。
            locked_workflow_uuids = sorted(
                current_workflow_uuids | incoming_workflow_uuids
            )
            with ExitStack() as locks:
                for workflow_uuid in locked_workflow_uuids:
                    locks.enter_context(self._authoring_lock(workflow_uuid))
                try:
                    with pin_package_roots(plan.root_identities) as pinned_roots:
                        registered = self._definition_store.install_discovered_sources(
                            registration_rows,
                            before_commit=pinned_roots.assert_current,
                        )
                except SourceWorkspaceError:
                    raise WorkflowError("invalid_input") from None
                except StoreConflict:
                    raise WorkflowConflict("invalid_input") from None
                # 定义目录事务与进程级文件访问授权不能共用一个锁，但必须
                # 在所有相关创作锁释放前一次发布，撤权返回后不得再有旧操作读写路径。
                with self._active_sources_lock:
                    self._active_source_workflow_uuids = incoming_workflow_uuids
                    self._active_source_dependencies = source_dependencies
            return registered

    def replace_active_editable_source_authorization(
        self,
        *,
        workflow_uuid: str,
        package_id: str,
        package_root: str | Path,
        relative_path: str,
    ) -> Dict[str, Any]:
        """用一项可编辑来源替换当前进程的完整活动源码授权集合。

        参数：工作流（Workflow）UUID 是已有定义身份；包身份、包目录和相对路径
        共同形成工作流源码（Workflow Source）的稳定来源身份。
        返回：安装后的进程内来源记录；此前活动的其他来源和对应定义同时退出本次
        进程授权集合，下次启动只从新选择的领域包重新建立。
        异常：身份不存在、路径不安全或唯一性冲突时返回稳定工作流错误。
        """

        workflow_uuid = self._get_authoring_workflow(workflow_uuid)["uuid"]
        try:
            root, normalized_relative_path = validate_source_registration(
                package_root=package_root,
                relative_path=relative_path,
            )
            root_metadata = root.lstat()
        except (OSError, SourceWorkspaceError):
            raise WorkflowError("invalid_input") from None
        if not isinstance(package_id, str) or not package_id.strip():
            raise WorkflowError("invalid_input")
        normalized_package_id = package_id.strip()
        source_uri = f"package://{normalized_package_id}/{normalized_relative_path}"
        # 单项替换命令构造成与启动发现完全相同的不可变计划，避免绕过物理路径、
        # 来源 URI 和“既有身份不可重绑定”等批量授权不变量。
        plan = EditableSourceDiscoveryPlan(
            registrations=(
                EditableSourceRegistration(
                    workflow_uuid=workflow_uuid,
                    package_id=normalized_package_id,
                    package_root=root,
                    relative_path=normalized_relative_path,
                    source_uri=source_uri,
                ),
            ),
            root_identities=(((root, (root_metadata.st_dev, root_metadata.st_ino))),),
        )
        return self.replace_discovered_source_authorizations(plan)[0]

    def _add_active_source_authorization(
        self,
        registration: EditableSourceRegistration,
    ) -> Dict[str, Any]:
        """把一项已发布领域源码增量加入当前进程授权集合。

        参数：``registration`` 来自当前唯一领域包源码目标。返回安装后的来源
        记录。异常：目录、身份或既有来源冲突时失败关闭；已有活动来源不会被
        替换或撤权。
        """

        try:
            root, relative_path = validate_source_registration(
                package_root=registration.package_root,
                relative_path=registration.relative_path,
            )
            metadata = root.lstat()
        except (OSError, SourceWorkspaceError):
            raise WorkflowError("source_target_unavailable") from None
        if (
            root != registration.package_root
            or registration.source_uri
            != f"package://{registration.package_id}/{relative_path}"
        ):
            raise WorkflowError("source_target_unavailable")
        row = {
            "workflow_uuid": registration.workflow_uuid,
            "package_id": registration.package_id,
            "package_root": str(root),
            "relative_path": relative_path,
            "source_uri": registration.source_uri,
        }
        with self._source_authorization_replacement_lock:
            with self._authoring_lock(registration.workflow_uuid):
                try:
                    with pin_package_roots(
                        ((root, (metadata.st_dev, metadata.st_ino)),)
                    ) as pinned:
                        installed = self._definition_store.install_discovered_sources(
                            (row,),
                            before_commit=pinned.assert_current,
                        )[0]
                except SourceWorkspaceError:
                    raise WorkflowError("source_target_unavailable") from None
                except StoreConflict:
                    raise WorkflowConflict("source_identity_conflict") from None
                with self._active_sources_lock:
                    self._active_source_workflow_uuids = frozenset(
                        (*self._active_source_workflow_uuids, registration.workflow_uuid)
                    )
                    self._active_source_dependencies[registration.workflow_uuid] = (
                        frozenset(registration.dependency_workflow_uuids)
                    )
                return installed

    def list_registered_sources(self) -> List[Dict[str, Any]]:
        """返回本次进程配置仍授权的工作流源码（Workflow Source）。

        参数：无。返回：按稳定工作流 UUID 排序的本进程活动注册；文件库中的旧
        定义或来源记录不会自动获得当前路径访问权。
        """

        with self._active_sources_lock:
            active_workflow_uuids = self._active_source_workflow_uuids
        return [
            registration
            for registration in self._definition_store.list_source_registrations()
            if registration["workflow_uuid"] in active_workflow_uuids
        ]

    def recover_registered_sources(
        self,
        *,
        preserve_author_source: bool = False,
    ) -> None:
        """启动时按当前模板目录逐一恢复全部授权源码。

        参数：``preserve_author_source`` 只供工作区自动激活使用，使成功候选绑定
        原始作者源码和对应映射。返回：无；只读取本轮活动注册并强制替换旧进程
        目录产生的候选或诊断，不把普通子源码编译误当成子合同发布。异常：文件、
        目录或持久化失败全部传播到组合根，禁止启动在不完整恢复后误报 ready。
        """

        for registration in self.list_registered_sources():
            self.reconcile_registered_source(
                registration["workflow_uuid"],
                force_compile=True,
                preserve_author_source=preserve_author_source,
            )

    def activate_registered_sources_to_fixed_point(
        self,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """恢复并应用工作区源码，直到组合工作流依赖达到固定点。

        参数：``progress_callback`` 在开始和每个源码完成真实编译后接收
        ``(loaded, total)``。返回：无；使用同代 Package Catalog 的静态组合依赖把来源按
        子到父分层，每层只编译一次、批量提交候选并只重建一次模板目录。循环层
        保留逐项刷新语义，让编译器发布稳定递归诊断。缺失或无效来源保留真实
        诊断；单个候选的稳定业务失败被隔离，未知基础设施异常继续失败关闭。对于
        旧包未显式标注的组合依赖，分层后再以有限轮次重试到固定点。
        """

        registrations = self.list_registered_sources()
        total = len(registrations)
        loaded = 0
        if progress_callback is not None:
            progress_callback(loaded, total)
        registrations_by_uuid = {
            str(registration["workflow_uuid"]): registration
            for registration in registrations
        }
        applied_during_layers = False
        for workflow_uuids, cyclic in self._workspace_activation_layers(
            tuple(registrations_by_uuid)
        ):
            for workflow_uuid in workflow_uuids:
                self.reconcile_registered_source(
                    workflow_uuid,
                    force_compile=True,
                    preserve_author_source=True,
                )
                loaded += 1
                if progress_callback is not None:
                    progress_callback(loaded, total)

            deferred_results: list[Mapping[str, Any]] = []
            previous_batch_state = self._workspace_activation_batch
            self._workspace_activation_batch = not cyclic
            try:
                for workflow_uuid in workflow_uuids:
                    record = self._definition_store.get_authoring_record(workflow_uuid)
                    candidate = record.get("candidate")
                    if not isinstance(candidate, dict):
                        continue
                    candidate_hash = candidate.get("candidate_hash")
                    if not isinstance(candidate_hash, str) or not candidate_hash:
                        raise WorkflowError("candidate_invalid")
                    try:
                        result = self._apply_workspace_activation_candidate(
                            workflow_uuid,
                            candidate_hash=candidate_hash,
                        )
                    except WorkflowError as error:
                        if error.code not in _ISOLATED_WORKSPACE_ACTIVATION_ERRORS:
                            raise
                        self._record_workspace_activation_failure(
                            workflow_uuid,
                            error=error,
                        )
                        continue
                    if cyclic:
                        self._require_workspace_activation_apply_complete(result)
                    else:
                        deferred_results.append(result)
                    applied_during_layers = True
            finally:
                self._workspace_activation_batch = previous_batch_state

            if not deferred_results:
                continue
            self._rebuild_workspace_activation_catalog()
            for result in deferred_results:
                self._require_workspace_activation_apply_complete(result)
        unresolved_sources = any(
            self.get_authoring(workflow_uuid).get("state") != "applied"
            for workflow_uuid in registrations_by_uuid
        )
        if applied_during_layers and unresolved_sources:
            self._retry_workspace_activation_to_fixed_point(
                tuple(registrations_by_uuid),
            )

    def _retry_workspace_activation_to_fixed_point(
        self,
        workflow_uuids: tuple[str, ...],
    ) -> None:
        """重试静态计划未标注出的组合依赖，直到不再产生新应用图。

        参数：``workflow_uuids`` 保留来源注册稳定顺序。返回无；每轮都使用上一轮
        已应用工作流生成的新目录重新编译尚未成功的来源，最多推进来源数量轮。
        稳定业务失败只隔离对应来源，基础设施或目录发布失败继续关闭式失败。

        领域包旧声明可能没有显式 ``dependency_workflow_uuids``，但 Python import
        已形成真实子工作流依赖。本补偿循环只解决这类缺失元数据，不替代有向依赖
        分层，也不会执行源码或猜测替代工作流。
        """

        blocked: set[str] = set()
        for _pass in range(len(workflow_uuids)):
            applied_any = False
            for workflow_uuid in workflow_uuids:
                if workflow_uuid in blocked:
                    continue
                self.reconcile_registered_source(
                    workflow_uuid,
                    force_compile=True,
                    preserve_author_source=True,
                )
                record = self._definition_store.get_authoring_record(workflow_uuid)
                candidate = record.get("candidate")
                if not isinstance(candidate, dict):
                    continue
                candidate_hash = candidate.get("candidate_hash")
                if not isinstance(candidate_hash, str) or not candidate_hash:
                    raise WorkflowError("candidate_invalid")
                try:
                    result = self._apply_workspace_activation_candidate(
                        workflow_uuid,
                        candidate_hash=candidate_hash,
                    )
                except WorkflowError as error:
                    if error.code not in _ISOLATED_WORKSPACE_ACTIVATION_ERRORS:
                        raise
                    self._record_workspace_activation_failure(
                        workflow_uuid,
                        error=error,
                    )
                    blocked.add(workflow_uuid)
                    continue
                self._require_workspace_activation_apply_complete(result)
                applied_any = True
            if not applied_any:
                return

    def _apply_workspace_activation_candidate(
        self,
        workflow_uuid: str,
        *,
        candidate_hash: str,
    ) -> Dict[str, Any]:
        """应用同一启动线程刚签发的候选并复用其编译结果。

        参数：``workflow_uuid`` 与 ``candidate_hash`` 精确标识待应用候选。
        返回：与公共 Apply 相同的结果。异常：公共 Apply 的所有权威冲突和
        定义目录写入错误均原样传播；授权只在当前线程的本次调用期间有效。
        """

        key = (workflow_uuid, candidate_hash)
        previous = getattr(
            self._workspace_activation_context,
            "prevalidated_candidate",
            None,
        )
        self._workspace_activation_context.prevalidated_candidate = key
        try:
            return self.apply_authoring(
                workflow_uuid,
                candidate_hash=candidate_hash,
                preserve_author_source=True,
            )
        finally:
            if previous is None:
                del self._workspace_activation_context.prevalidated_candidate
            else:
                self._workspace_activation_context.prevalidated_candidate = previous

    def _workspace_activation_layers(
        self,
        workflow_uuids: tuple[str, ...],
    ) -> tuple[tuple[tuple[str, ...], bool], ...]:
        """把活动来源稳定划分为子到父的启动层。

        参数：``workflow_uuids`` 保留注册表稳定顺序。返回：每项包含同层 UUID
        及是否为循环残量；只有循环残量允许退回逐项目录刷新。异常：活动依赖
        快照缺失时按无内部依赖处理，兼容不含工作流的旧计划。
        """

        with self._active_sources_lock:
            dependencies = dict(self._active_source_dependencies)
        remaining = set(workflow_uuids)
        layers: list[tuple[tuple[str, ...], bool]] = []
        while remaining:
            layer = tuple(
                workflow_uuid
                for workflow_uuid in workflow_uuids
                if workflow_uuid in remaining
                and not (dependencies.get(workflow_uuid, frozenset()) & remaining)
            )
            if not layer:
                layers.append(
                    (
                        tuple(
                            workflow_uuid
                            for workflow_uuid in workflow_uuids
                            if workflow_uuid in remaining
                        ),
                        True,
                    )
                )
                break
            layers.append((layer, False))
            remaining.difference_update(layer)
        return tuple(layers)

    def _rebuild_workspace_activation_catalog(self) -> None:
        """在一层候选全部提交后原子发布一次新模板目录。

        参数：无。返回：无；没有目录重建器时保留既有编译器。异常：重建失败时
        关闭陈旧编译入口并抛稳定目录不可用错误，禁止组合根误报 ready。
        """

        if self._compiler_rebuilder is None:
            return
        try:
            self.compiler = self._compiler_rebuilder()
        except Exception:
            self.compiler = None
            raise WorkflowError("template_catalog_unavailable") from None

    def _require_workspace_activation_apply_complete(
        self,
        result: Mapping[str, Any],
    ) -> None:
        """禁止工作区自动激活在提交后恢复未完成时发布 ready。

        参数：``result`` 是刚完成的 ``apply_authoring`` 结果。返回：没有提交后
        warning 且当前目录编译器仍可用时无返回值。异常：目录重建或依赖来源刷新
        未完成时抛 ``template_catalog_unavailable``；其他提交后恢复 warning 抛
        ``internal_error``。图事务可能已经提交，但组合根必须失败关闭并由下次冷
        启动从领域源码重新编译，绝不把部分固定点误报为 ready。
        """

        apply_result = result.get("apply_result")
        if not isinstance(apply_result, Mapping):
            raise WorkflowError("candidate_invalid")
        warnings = apply_result.get("warnings")
        if not isinstance(warnings, list):
            raise WorkflowError("candidate_invalid")
        warning_codes = {
            str(warning.get("code"))
            for warning in warnings
            if isinstance(warning, Mapping)
        }
        catalog_incomplete = {
            "template_catalog_rebuild_pending",
            "dependent_authoring_refresh_pending",
        }
        if self.compiler is None or warning_codes & catalog_incomplete:
            raise WorkflowError("template_catalog_unavailable")
        if warnings:
            raise WorkflowError("internal_error")

    def _record_workspace_activation_failure(
        self,
        workflow_uuid: str,
        *,
        error: WorkflowError,
    ) -> None:
        """把一个自动应用业务失败收敛为该来源自己的进程内诊断。

        参数：``workflow_uuid`` 是失败来源身份；``error`` 是已经稳定映射的工作流
        业务错误。返回：无；撤销不可再次应用的旧候选，保留当前源码代，并发布
        一次可观察创作事件。异常：读取来源或写入诊断失败时原样传播，避免把存储
        故障误当成普通草稿错误。
        """

        workflow_uuid = self._get_authoring_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(workflow_uuid):
            registration = self._registration(workflow_uuid)
            source = self._read_source(registration)
            record = self._definition_store.get_authoring_record(workflow_uuid)
            draft_hash = (
                source["draft_hash"]
                if source is not None
                else record["observed_draft_hash"]
            )
            draft_update_time = (
                source["update_time"]
                if source is not None
                else record["draft_update_time"]
            )
            self._definition_store.record_draft_compilation(
                workflow_uuid=workflow_uuid,
                draft_hash=draft_hash,
                draft_update_time=draft_update_time,
                diagnostics=[
                    {
                        "severity": "error",
                        "code": error.code,
                        "message": error.message,
                    }
                ],
                candidate_hash=None,
                candidate=None,
                event_data={
                    "workflow_uuid": workflow_uuid,
                    "cause": "workspace_activation_failed",
                    "workflow_revision": self._get_authoring_workflow(workflow_uuid)[
                        "revision"
                    ],
                    "draft_hash": draft_hash,
                    "candidate_hash": None,
                },
            )
        logger.warning(
            "工作区工作流自动激活失败 workflow_uuid=%s code=%s message=%s",
            workflow_uuid,
            error.code,
            error.message,
        )

    def close(self) -> None:
        """关闭共享本地调度桥、运行事实库和进程内定义目录。

        参数：无。返回：无；桥必须幂等注销监听器，随后关闭运行库与定义目录。异常：清理
        失败原样传播，调用方据此保留未完成资源所有权并可重试。
        """

        if self._task_scheduler_bridge is not None:
            self._task_scheduler_bridge.close()
        if self._intervention_delivery is not None:
            self._intervention_delivery.remove_error_decision_required_listener(
                self.open_workflow_intervention_from_report
            )
            self._intervention_delivery = None
        self._store.close()
        if self._definition_store is not self._store:
            self._definition_store.close()

    def get_authoring(self, workflow_uuid: str) -> Dict[str, Any]:
        workflow_uuid = self._get_authoring_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(workflow_uuid):
            workflow = self._get_authoring_workflow(workflow_uuid)
            registration = self._registration(workflow_uuid)
            source = self._read_source(registration)
            graph = self.get_graph(workflow_uuid)
            record = self._definition_store.get_authoring_record(workflow_uuid)
            return self._authoring_aggregate(
                workflow=workflow,
                graph=graph,
                registration=registration,
                source=source,
                record=record,
            )

    def save_draft(
        self,
        workflow_uuid: str,
        *,
        python_source: str,
        expected_draft_hash: Optional[str],
        expected_workflow_revision: int,
    ) -> Dict[str, Any]:
        self._validate_hash(expected_draft_hash, nullable=True)
        workflow_uuid = self._get_authoring_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(workflow_uuid):
            workflow = self._get_authoring_workflow(workflow_uuid)
            registration = self._registration(workflow_uuid)
            current = self._read_source(registration)
            current_hash = current["draft_hash"] if current is not None else None
            if current_hash != expected_draft_hash:
                raise WorkflowConflict("draft_hash_conflict")
            if workflow["revision"] != expected_workflow_revision:
                raise WorkflowConflict("workflow_revision_conflict")

            try:
                encoded = python_source.encode("utf-8")
            except UnicodeEncodeError:
                raise WorkflowError("invalid_input") from None
            self._reject_cross_workflow_source(
                workflow_uuid=workflow_uuid,
                python_source=python_source,
            )
            encoded_hash = _sha256(encoded)
            # IDE 保存事件发生时，文件系统已经发布了作者字节；随后对同一哈希
            # 发起的 CAS 只用于静态编译和签发候选。再次原子替换会无意义地改变
            # 文件世代，还可能触发工作区监视器的第二轮刷新。
            if encoded_hash != current_hash:
                try:
                    self._atomic_write(
                        registration,
                        encoded,
                        expected_hash=current_hash,
                    )
                except OSError:
                    raise WorkflowError("internal_error") from None
            source = self._read_source(registration)
            assert source is not None
            if source["draft_hash"] != encoded_hash:
                raise WorkflowConflict("draft_hash_conflict")
            applied_graph = self.get_graph(workflow_uuid)
            compilation = self._compile(
                workflow=workflow,
                graph=applied_graph,
                registration=registration,
                python_source=source["python_source"],
            )
            candidate = self._issue_candidate(
                workflow_revision=workflow["revision"],
                draft_hash=source["draft_hash"],
                compilation=compilation,
                applied_graph=applied_graph,
                draft_python_source=source["python_source"],
            )
            record = self._definition_store.get_authoring_record(workflow_uuid)
            applied_source = record.get("applied_source")
            if self._source_only_candidate_is_already_applied(
                candidate=candidate,
                applied_source=applied_source,
                workflow_revision=workflow["revision"],
                draft_hash=source["draft_hash"],
            ):
                # 恢复到已应用的精确作者字节且重新编译证明图未变时，没有待
                # Apply 的新事实；清空旧无效草稿派生状态即可回到 applied。
                candidate = None
            event_data = {
                "workflow_uuid": workflow_uuid,
                "cause": "draft_saved",
                "workflow_revision": workflow["revision"],
                "draft_hash": source["draft_hash"],
                "candidate_hash": (
                    candidate["candidate_hash"] if candidate is not None else None
                ),
            }
            self._definition_store.record_draft_compilation(
                workflow_uuid=workflow_uuid,
                draft_hash=source["draft_hash"],
                draft_update_time=source["update_time"],
                diagnostics=compilation.diagnostics,
                candidate_hash=(
                    candidate["candidate_hash"] if candidate is not None else None
                ),
                candidate=candidate,
                event_data=event_data,
            )
            self._catalog_generation_tracker.record_compilation(
                workflow_uuid,
                compilation.template_catalog_fingerprint,
            )
            return self.get_authoring(workflow_uuid)

    @staticmethod
    def _reject_cross_workflow_source(
        *, workflow_uuid: str, python_source: str
    ) -> None:
        """拒绝把明确属于其他工作流的源码写入当前登记路径。

        参数：``workflow_uuid`` 是当前登记路径的权威工作流 UUID；
        ``python_source`` 是待保存的完整 Python 文本。返回：身份一致或无法静态
        确认时无返回。异常：唯一声明另一个有效 UUID 时抛
        ``WorkflowConflict(workflow_identity_mismatch)``。

        安全不变量：语法错误、缺失/动态/歧义声明仍可作为无效草稿保存；这里只
        拦截能够静态证明属于另一工作流的源码，且拒绝发生在任何物理写入之前。
        """

        expected_uuid = validate_uuid(workflow_uuid)
        declared_uuid = declared_workflow_uuid(python_source)
        if declared_uuid is None or declared_uuid == expected_uuid:
            return
        raise WorkflowConflict(
            "workflow_identity_mismatch",
            message=(
                f"导入的 Python 声明工作流 {declared_uuid}，当前编辑的是 "
                f"{expected_uuid}；请选择匹配的工作流，或修改 "
                "@workflow.workflow_uuid 后再保存"
            ),
        )

    def reconcile_registered_source(
        self,
        workflow_uuid: str,
        *,
        force_compile: bool = False,
        preserve_author_source: bool = False,
    ) -> Dict[str, Any]:
        """协调一个已注册工作流源码及其可重建创作派生状态。

        参数：``workflow_uuid`` 是工作流（Workflow）稳定身份；
        ``force_compile`` 用于启动恢复或模板目录换代后强制重编译目录相关诊断和
        候选版本（Candidate）；``preserve_author_source`` 让自动激活候选保留
        原始作者源码与相应映射。返回：最新创作聚合。异常：来源、编译或持久化
        失败时传播稳定工作流错误；本函数不发布模板目录。
        """

        workflow_uuid = self._get_authoring_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(workflow_uuid):
            workflow = self._get_authoring_workflow(workflow_uuid)
            registration = self._registration(workflow_uuid)
            source = self._read_source(registration)
            record = self._definition_store.get_authoring_record(workflow_uuid)
            current_catalog_fingerprint = (
                self._catalog_fingerprint() if self.compiler is not None else None
            )
            catalog_changed = (
                current_catalog_fingerprint is not None
                and self._catalog_generation_tracker.changed_from_known_generation(
                    workflow_uuid,
                    current_catalog_fingerprint,
                )
            )
            applied_source = record.get("applied_source")
            writeback_marker_valid = (
                record.get("writeback_source") is not None
                and record.get("writeback_expected_hash") is not None
                and record.get("writeback_generation") is not None
            )
            if (
                record["writeback_status"] == "pending"
                and writeback_marker_valid
                and source is not None
                and applied_source is not None
                and source["draft_hash"] == applied_source["source_hash"]
            ):
                self._definition_store.settle_writeback(
                    workflow_uuid=workflow_uuid,
                    expected_writeback_source=record["writeback_source"],
                    expected_writeback_hash=record["writeback_expected_hash"],
                    expected_writeback_generation=record["writeback_generation"],
                    observed_draft_hash=source["draft_hash"],
                    draft_update_time=source["update_time"],
                    event_data={
                        "workflow_uuid": workflow_uuid,
                        "cause": "recovered",
                        "workflow_revision": workflow["revision"],
                        "draft_hash": source["draft_hash"],
                        "candidate_hash": None,
                    },
                )
                return self.get_authoring(workflow_uuid)
            if (
                record["writeback_status"] == "pending"
                and writeback_marker_valid
                and (
                    source is None
                    or source["draft_hash"] == record["writeback_expected_hash"]
                )
            ):
                recovery_source = record.get("writeback_source")
                if recovery_source is not None:
                    try:
                        recovery_bytes = recovery_source.encode("utf-8")
                        recovery_hash = _sha256(recovery_bytes)
                        self._atomic_write(
                            registration,
                            recovery_bytes,
                            expected_hash=(
                                source["draft_hash"] if source is not None else None
                            ),
                        )
                        source = self._read_source(registration)
                        if source is not None and source["draft_hash"] == recovery_hash:
                            self._definition_store.settle_writeback(
                                workflow_uuid=workflow_uuid,
                                expected_writeback_source=record["writeback_source"],
                                expected_writeback_hash=record[
                                    "writeback_expected_hash"
                                ],
                                expected_writeback_generation=record[
                                    "writeback_generation"
                                ],
                                observed_draft_hash=source["draft_hash"],
                                draft_update_time=source["update_time"],
                                event_data={
                                    "workflow_uuid": workflow_uuid,
                                    "cause": "recovered",
                                    "workflow_revision": workflow["revision"],
                                    "draft_hash": source["draft_hash"],
                                    "candidate_hash": None,
                                },
                            )
                            return self.get_authoring(workflow_uuid)
                    except (OSError, UnicodeError, WorkflowError):
                        return self.get_authoring(workflow_uuid)
            actual_hash = source["draft_hash"] if source is not None else None
            invalid_writeback_marker = (
                record["writeback_status"] == "pending" and not writeback_marker_valid
            )
            if (
                actual_hash == record["observed_draft_hash"]
                and not invalid_writeback_marker
                and not (actual_hash is None and record.get("candidate") is not None)
                and not force_compile
            ):
                return self.get_authoring(workflow_uuid)

            candidate: Optional[Dict[str, Any]] = None
            diagnostics: List[Dict[str, Any]] = []
            if source is not None:
                applied_graph = self.get_graph(workflow_uuid)
                compilation = self._compile(
                    workflow=workflow,
                    graph=applied_graph,
                    registration=registration,
                    python_source=source["python_source"],
                )
                if preserve_author_source:
                    compilation = self._preserve_author_source_compilation(
                        compilation=compilation,
                        workflow=workflow,
                        graph=applied_graph,
                        python_source=source["python_source"],
                    )
                diagnostics = compilation.diagnostics
                candidate = self._issue_candidate(
                    workflow_revision=workflow["revision"],
                    draft_hash=source["draft_hash"],
                    compilation=compilation,
                    applied_graph=applied_graph,
                    draft_python_source=source["python_source"],
                )
                if self._source_only_candidate_is_already_applied(
                    candidate=candidate,
                    applied_source=applied_source,
                    workflow_revision=workflow["revision"],
                    draft_hash=source["draft_hash"],
                ):
                    # 当前目录已再次证明同一作者源码不改变应用图；若继续签发
                    # 候选，每次重启都会无意义提升修订并触发全目录级联编译。
                    candidate = None
            if force_compile and actual_hash == record["observed_draft_hash"]:
                # 同一源码代际只在进程内已知目录指纹变化时标记为目录变化；
                # 冷启动没有旧代际证据，只能记录为恢复编译。
                cause = "catalog_changed" if catalog_changed else "recovered"
            elif (
                source is not None
                and record["observed_draft_hash"] is None
                and record["update_time"] is not None
            ):
                cause = "recovered"
            else:
                cause = "external_draft_changed"
            self._definition_store.record_draft_compilation(
                workflow_uuid=workflow_uuid,
                draft_hash=actual_hash,
                draft_update_time=(
                    source["update_time"] if source is not None else None
                ),
                diagnostics=diagnostics,
                candidate_hash=(
                    candidate["candidate_hash"] if candidate is not None else None
                ),
                candidate=candidate,
                event_data={
                    "workflow_uuid": workflow_uuid,
                    "cause": cause,
                    "workflow_revision": workflow["revision"],
                    "draft_hash": actual_hash,
                    "candidate_hash": (
                        candidate["candidate_hash"] if candidate is not None else None
                    ),
                },
            )
            if source is not None:
                self._catalog_generation_tracker.record_compilation(
                    workflow_uuid,
                    compilation.template_catalog_fingerprint,
                )
            else:
                self._catalog_generation_tracker.record_compilation(
                    workflow_uuid,
                    None,
                )
            return self.get_authoring(workflow_uuid)

    def submit_source_change(
        self,
        workflow_uuid: str,
        *,
        observed_signature: Tuple[Any, ...],
    ) -> bool:
        """提交一个稳定观测到的工作流源码（Workflow Source）变化命令。

        参数：``workflow_uuid`` 是已注册来源绑定的稳定工作流身份；
        ``observed_signature`` 是源码监视器（Source Monitor）去抖后的文件世代。
        返回：只有相同文件世代完成哈希去重、候选推进及待写回恢复时才为
        ``True``；文件并发变化或持久恢复仍待处理时返回 ``False``。读取、编译或
        持久化异常原样映射为稳定工作流错误，调用者不得把异常视为已确认。
        """

        workflow_uuid = self._get_authoring_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(workflow_uuid):
            # ``current_signature`` 是服务在取得创作锁后复核的文件世代，防止监视
            # 线程用过期观测授权编译更新中的文件。
            current_signature = self.source_signature(workflow_uuid)
            if current_signature != observed_signature:
                return False
            # ``catalog_fingerprint`` 是当前服务编译器的目录代际；它与文件签名
            # 独立变化，必须强制替换旧目录产生的候选或失败诊断。
            catalog_fingerprint = self._catalog_fingerprint()
            force_compile = self._catalog_generation_tracker.requires_compile(
                workflow_uuid,
                catalog_fingerprint,
            )
            self.reconcile_registered_source(
                workflow_uuid,
                force_compile=force_compile,
            )
            # ``latest_signature`` 证明整个状态推进期间规范源码没有再次变化。
            latest_signature = self.source_signature(workflow_uuid)
            if latest_signature != observed_signature:
                return False
            record = self._definition_store.get_authoring_record(workflow_uuid)
            return record["writeback_status"] != "pending"

    def apply_authoring(
        self,
        workflow_uuid: str,
        *,
        candidate_hash: str,
        preserve_author_source: bool = False,
    ) -> Dict[str, Any]:
        """按服务端候选哈希线性化应用可信工作流创作结果。

        参数：``workflow_uuid`` 是工作流（Workflow）稳定身份；``candidate_hash``
        是服务端持久并签发的候选哈希（Candidate Hash），客户端不得重述草稿、
        工作流修订或候选包；``preserve_author_source`` 仅供启动固定点激活，
        禁止把自动扫描变成未确认的规范化编辑。返回：应用结果与最新创作聚合。
        异常：候选、源码权威（Source Authority）、工作流修订（Workflow
        Revision）或目录指纹已变化时抛出稳定 ``WorkflowConflict``；候选无效时
        抛出 ``WorkflowError``。
        """

        self._validate_hash(candidate_hash, nullable=False)
        workflow_uuid = self._get_authoring_workflow(workflow_uuid)["uuid"]
        with self._authoring_lock(workflow_uuid):
            workflow = self._get_authoring_workflow(workflow_uuid)
            registration = self._registration(workflow_uuid)
            record = self._definition_store.get_authoring_record(workflow_uuid)
            candidate = record.get("candidate")
            if candidate is None:
                if any(
                    str(item.get("severity", "")).lower() == "error"
                    for item in record["diagnostics"]
                ):
                    raise WorkflowError("draft_invalid")
                raise WorkflowConflict("candidate_not_ready")
            if candidate.get("candidate_hash") != candidate_hash:
                raise WorkflowConflict("candidate_hash_conflict")
            try:
                # 这些前置事实只从持久候选推导，客户端无法混搭不同世代。
                expected_draft_hash = candidate["draft_hash"]
                expected_workflow_revision = candidate["base_workflow_revision"]
                expected_catalog_fingerprint = candidate["template_catalog_fingerprint"]
            except (KeyError, TypeError):
                raise WorkflowError("candidate_invalid") from None
            self._validate_hash(expected_draft_hash, nullable=False)
            if (
                type(expected_workflow_revision) is not int
                or expected_workflow_revision < 1
            ):
                raise WorkflowError("candidate_invalid")
            self._validate_hash(expected_catalog_fingerprint, nullable=False)

            source = self._read_source(registration)
            if source is None:
                raise WorkflowConflict("draft_hash_conflict")
            # D-079 的源码、修订、目录冲突顺序继续保持稳定。
            actual_hash = source["draft_hash"]
            if actual_hash != expected_draft_hash:
                raise WorkflowConflict("draft_hash_conflict")
            if workflow["revision"] != expected_workflow_revision:
                raise WorkflowConflict("workflow_revision_conflict")
            if self._catalog_fingerprint() != expected_catalog_fingerprint:
                raise WorkflowConflict("template_catalog_conflict")

            prevalidated_candidate = getattr(
                self._workspace_activation_context,
                "prevalidated_candidate",
                None,
            )
            if prevalidated_candidate != (workflow_uuid, candidate_hash):
                applied_graph = self.get_graph(workflow_uuid)
                compilation = self._compile(
                    workflow=workflow,
                    graph=applied_graph,
                    registration=registration,
                    python_source=source["python_source"],
                )
                if preserve_author_source:
                    compilation = self._preserve_author_source_compilation(
                        compilation=compilation,
                        workflow=workflow,
                        graph=applied_graph,
                        python_source=source["python_source"],
                    )
                if not self._normalize_candidate_diagnostics(
                    compilation,
                    python_source=source["python_source"],
                ):
                    raise WorkflowError("candidate_invalid")
                if not compilation.valid:
                    if any(
                        str(item.get("severity", "")).lower() == "error"
                        for item in compilation.diagnostics
                    ):
                        raise WorkflowError("draft_invalid")
                    raise WorkflowError("candidate_invalid")
                revalidated = self._issue_candidate(
                    workflow_revision=workflow["revision"],
                    draft_hash=source["draft_hash"],
                    compilation=compilation,
                    applied_graph=applied_graph,
                    draft_python_source=source["python_source"],
                )
                if revalidated is None:
                    raise WorkflowError("candidate_invalid")
                if (
                    revalidated["template_catalog_fingerprint"]
                    != expected_catalog_fingerprint
                ):
                    raise WorkflowConflict("template_catalog_conflict")
                if revalidated["candidate_hash"] != candidate_hash:
                    raise WorkflowConflict("candidate_hash_conflict")

            def validate_authoring_authorities(
                linearized_draft_hash: str,
                linearized_catalog_fingerprint: str,
            ) -> None:
                """在写事务内复核源码与目录两项创作权威。

                参数：``linearized_draft_hash`` 是存储从持久候选推导的草稿哈希。
                ``linearized_catalog_fingerprint`` 是同一候选的目录指纹
                （Catalog Fingerprint）。返回：无；源码或目录世代变化时抛出稳定
                冲突，使同一 SQLite 事务回滚。该回调不接受客户端事实。
                """

                latest_source = self._read_source(registration)
                if (
                    latest_source is None
                    or latest_source["draft_hash"] != linearized_draft_hash
                ):
                    raise WorkflowConflict("draft_hash_conflict")
                if self._catalog_fingerprint() != linearized_catalog_fingerprint:
                    raise WorkflowConflict("template_catalog_conflict")

            normalized_source = candidate["normalized_python_source"]
            normalized_bytes = normalized_source.encode("utf-8")
            normalized_hash = _sha256(normalized_bytes)
            applied_source = {
                "python_source": normalized_source,
                "source_hash": normalized_hash,
                "source_map": candidate["source_map"],
                "compiler_version": candidate["compiler_version"],
                "template_catalog_fingerprint": candidate[
                    "template_catalog_fingerprint"
                ],
            }
            # 在进入唯一写事务前最后复核目录权威（Catalog Authority）。
            if self._catalog_fingerprint() != expected_catalog_fingerprint:
                raise WorkflowConflict("template_catalog_conflict")
            previous_revision = expected_workflow_revision
            try:
                (
                    resulting_revision,
                    writeback_generation,
                ) = self._definition_store.apply_authoring_candidate(
                    workflow_uuid=workflow_uuid,
                    candidate_hash=candidate_hash,
                    authoring_authority_validator=validate_authoring_authorities,
                )
            except StoreAuthoringConflict as error:
                raise WorkflowConflict(error.code) from None
            except StoreRevisionConflict:
                raise WorkflowConflict("workflow_revision_conflict") from None
            except (StoreConflict, ValidationError):
                raise WorkflowError("candidate_invalid") from None

            warnings: List[Dict[str, str]] = []
            if (
                self._compiler_rebuilder is not None
                and not self._workspace_activation_batch
            ):
                try:
                    rebuilt_compiler = self._compiler_rebuilder()
                except Exception:  # noqa: BLE001 - 主事务已提交，只能关闭目录
                    # 应用图已经提交，目录刷新失败时撤销编译入口，禁止继续用陈旧
                    # 指纹签发父候选；下次进程启动会从持久图重建完整代际。
                    self.compiler = None
                    warnings.append(
                        {
                            "code": "template_catalog_rebuild_pending",
                            "message": (
                                "工作流已应用，但模板目录重建失败；"
                                "创作编译已关闭，重启后将自动恢复。"
                            ),
                        }
                    )
                else:
                    self.compiler = rebuilt_compiler
            response_source = source

            def warn_writeback() -> None:
                if warnings:
                    return
                warnings.append(
                    {
                        "code": "draft_writeback_pending",
                        "message": (
                            "工作流已应用，但本地源码同步失败；"
                            "OS 已保留可恢复的源码记录。"
                        ),
                    }
                )

            def mark_pending_best_effort() -> None:
                for _attempt in range(2):
                    try:
                        marker_owned = self._definition_store.mark_writeback_pending(
                            workflow_uuid=workflow_uuid,
                            expected_writeback_source=normalized_source,
                            expected_writeback_hash=actual_hash,
                            expected_writeback_generation=writeback_generation,
                        )
                        if not marker_owned:
                            # 新 Apply/Draft 已接管 marker，旧 generation 不再重试。
                            return
                        return
                    except Exception:  # noqa: BLE001 - 提交后只能尽力恢复
                        continue

            try:
                latest = self._read_source(registration)
                if latest is None or latest["draft_hash"] != actual_hash:
                    raise WorkflowError("draft_hash_conflict")
                if normalized_hash == actual_hash:
                    # 自动激活以及已经规范的交互 Apply 都不替换相同作者字节，
                    # 避免制造虚假的 IDE 保存事件和文件世代。
                    written = latest
                else:
                    self._atomic_write(
                        registration,
                        normalized_bytes,
                        expected_hash=actual_hash,
                    )
                    written = self._read_source(registration)
                    assert written is not None
                response_source = written
                if written["draft_hash"] != normalized_hash:
                    raise WorkflowConflict("draft_hash_conflict")
            except Exception:  # noqa: BLE001 - 主事务已提交
                # 主事务已经提交。之后任何文件系统、数据库或聚合错误
                # 都只能降级为可恢复警告，不能把成功伪装成失败。
                warn_writeback()
                mark_pending_best_effort()
            else:
                settled = False
                for _attempt in range(2):
                    try:
                        marker_owned = self._definition_store.settle_writeback(
                            workflow_uuid=workflow_uuid,
                            expected_writeback_source=normalized_source,
                            expected_writeback_hash=actual_hash,
                            expected_writeback_generation=writeback_generation,
                            observed_draft_hash=written["draft_hash"],
                            draft_update_time=written["update_time"],
                        )
                        if not marker_owned:
                            # 新 generation 已接管；陈旧 settle 无需恢复。
                            settled = True
                            break
                        settled = True
                        break
                    except Exception:  # noqa: BLE001 - 主事务已提交
                        warn_writeback()
                if not settled:
                    mark_pending_best_effort()

            fallback_meta_data = dict(workflow["meta_data"])
            candidate_workflow = candidate["graph"].get("workflow") or {}
            candidate_meta_data = candidate_workflow.get("meta_data") or {}
            if (
                isinstance(candidate_meta_data, dict)
                and "unilab" in candidate_meta_data
            ):
                fallback_meta_data["unilab"] = candidate_meta_data["unilab"]
            fallback_workflow = {
                **workflow,
                "revision": resulting_revision,
                "meta_data": fallback_meta_data,
                "update_time": utc_now(),
            }
            fallback_applied_source = {
                **applied_source,
                "workflow_revision": resulting_revision,
                "update_time": utc_now(),
            }
            fallback_record = {
                "observed_draft_hash": (
                    response_source["draft_hash"]
                    if response_source is not None
                    else None
                ),
                "diagnostics": [],
                "candidate": None,
                "applied_source": fallback_applied_source,
            }
            try:
                authoring = self.get_authoring(workflow_uuid)
            except Exception:  # noqa: BLE001 - 主事务已提交
                try:
                    authoring = self.get_authoring(workflow_uuid)
                except Exception:  # noqa: BLE001 - 使用已知事实降级
                    try:
                        fallback_graph = self.get_graph(workflow_uuid)
                        fallback_workflow = fallback_graph["workflow"]
                        fallback_record = self._definition_store.get_authoring_record(
                            workflow_uuid
                        )
                    except Exception:  # noqa: BLE001 - 使用提交时事实降级
                        fallback_graph = self._post_commit_candidate_graph(
                            candidate["graph"],
                            workflow=fallback_workflow,
                        )
                    authoring = self._authoring_aggregate(
                        workflow=fallback_workflow,
                        graph=fallback_graph,
                        registration=registration,
                        source=response_source,
                        record=fallback_record,
                    )

            result = {
                "apply_result": {
                    "kind": candidate["changeset"]["kind"],
                    "previous_workflow_revision": previous_revision,
                    "workflow_revision": resulting_revision,
                    "applied_candidate_hash": candidate["candidate_hash"],
                    "applied_source_hash": normalized_hash,
                    "warnings": warnings,
                },
                "authoring": authoring,
            }
        if not self._workspace_activation_batch:
            refresh_catalog_dependent_authoring(
                registrations=self.list_registered_sources(),
                load_authoring_record=self._definition_store.get_authoring_record,
                reconcile_source=partial(
                    self.reconcile_registered_source,
                    force_compile=True,
                    preserve_author_source=preserve_author_source,
                ),
                mutated_workflow_uuid=workflow_uuid,
                warnings=warnings,
            )
        return result

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 200,
        after_id: int | None = None,
    ) -> Dict[str, Any]:
        """读取服务器发送事件（SSE）使用的持久失效通知页。

        参数：``after_sequence`` 是规范排他游标，``limit`` 是公开页长；
        ``after_id`` 仅兼容现有进程内调用，不能与非零规范游标并用。返回：含事件、
        下一游标与是否还有后页的只读投影，并保留旧 ``after_id`` 回显。异常：
        参数非法时抛稳定 ``WorkflowError``；持久投影损坏时传播
        ``EventProjectionError`` 形成服务器失败，不误报为客户端输入错误。本方法
        不写任何运行状态。
        """

        if after_id is not None:
            if after_sequence != 0:
                raise WorkflowError("invalid_input")
            after_sequence = after_id
        try:
            self._forward_definition_events()
            page = self._event_reader.read(
                after_sequence=after_sequence,
                limit=limit,
            )
        except ValueError:
            raise WorkflowError("invalid_input")
        return {**page, "after_id": after_sequence}

    def _forward_definition_events(self) -> None:
        """把本进程定义变更投影到持久 SSE 失效通知流。

        参数：无。返回无；定义目录与运行库相同时无需转发。每批先完整写入运行
        事件事务，再推进局部游标；事件只用于提示客户端重读，不成为工作流定义。
        """

        if self._definition_store is self._store:
            return
        with self._definition_event_lock:
            while True:
                events = self._definition_store.list_events(
                    after_sequence=self._definition_event_cursor,
                    limit=200,
                )
                if not events:
                    return
                self._store.append_forwarded_events(events)
                self._definition_event_cursor = int(events[-1]["id"])
                if len(events) < 200:
                    return

    # 工作流创作（Authoring）内部实现 -------------------------------------

    def _get_authoring_workflow(
        self,
        workflow_uuid: str,
    ) -> Dict[str, Any]:
        try:
            identity = validate_uuid(workflow_uuid)
        except ValueError:
            raise WorkflowError("invalid_input") from None
        try:
            return self._definition_store.get_workflow(identity)
        except StoreNotFound:
            raise WorkflowError("workflow_not_found") from None

    def _registration(self, workflow_uuid: str) -> Dict[str, Any]:
        """读取当前进程仍授权的规范源码注册。

        参数：``workflow_uuid`` 是已校验的工作流稳定身份。返回：当前活动来源
        注册。异常：未在本次启动 allowlist 中授权或目录行缺失时统一抛出
        ``workflow_not_found``，且在拒绝前不触碰持久路径。
        """

        with self._active_sources_lock:
            if workflow_uuid not in self._active_source_workflow_uuids:
                raise WorkflowError("workflow_not_found")
        try:
            return self._definition_store.get_source_registration(workflow_uuid)
        except StoreNotFound:
            raise WorkflowError("workflow_not_found") from None

    def _read_source(
        self,
        registration: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """通过源码工作区（SourceWorkspace）读取一项已注册草稿。

        参数：``registration`` 是本进程已授权的来源身份。
        返回：缺失时为 ``None``，否则返回源码、草稿哈希和修改时间字典。
        异常：不安全或超限文件映射为 ``invalid_input``。
        """

        try:
            source = read_registered_source(registration)
        except SourceWorkspaceError:
            raise WorkflowError("invalid_input") from None
        if source is None:
            return None
        return {
            "python_source": source.python_source,
            "draft_hash": source.draft_hash,
            "update_time": source.update_time,
        }

    def source_signature(
        self,
        workflow_uuid: str,
    ) -> Tuple[Any, ...]:
        """按当前授权的工作流身份返回轻量源码签名。

        参数：``workflow_uuid`` 是工作流源码（Workflow Source）绑定的稳定身份；
        本方法不接受调用者缓存的注册路径。
        返回：缺失标记或普通文件的身份、大小和时间签名。
        异常：已撤权身份稳定映射为 ``workflow_not_found``；不安全路径或非普通
        文件映射为 ``invalid_input``。安全：每次读取都在工作流创作锁内重新取得
        当前注册，撤权返回后旧注册信息不能继续触碰文件系统；尚未装配编译器的
        来源管理用途只返回文件签名，不伪造模板目录代际。
        """

        with self._authoring_lock(workflow_uuid):
            registration = self._registration(workflow_uuid)
            try:
                file_signature = registered_source_signature(registration)
                if self.compiler is None:
                    return self._catalog_generation_tracker.source_signature(
                        file_signature,
                        None,
                    )
                # 保留既有文件签名首项，末尾追加模板目录代际；旧诊断调用者仍可
                # 识别 ``file``/``missing``，统一监视器则会在目录换代时得到新签名。
                return self._catalog_generation_tracker.source_signature(
                    file_signature,
                    self._catalog_fingerprint(),
                )
            except SourceWorkspaceError:
                raise WorkflowError("invalid_input") from None

    def _atomic_write(
        self,
        registration: Dict[str, Any],
        content: bytes,
        *,
        expected_hash: Any = _NO_EXPECTED_HASH,
    ) -> None:
        """通过源码工作区（SourceWorkspace）执行原子 CAS 草稿写入。

        参数：``registration`` 是来源身份；``content`` 是 UTF-8 源码字节；
        ``expected_hash`` 是可选原稿哈希条件。
        返回：无；成功时规范源码完整替换。
        异常：CAS 变化映射为 ``draft_hash_conflict``，其他失败映射为稳定错误。
        """

        try:
            write_registered_source(
                registration,
                content,
                expected_hash=expected_hash,
            )
        except SourceWorkspaceConflict:
            raise WorkflowConflict("draft_hash_conflict") from None
        except SourceWorkspaceError as error:
            error_code = (
                "invalid_input" if error.code == "invalid_input" else "internal_error"
            )
            raise WorkflowError(error_code) from None

    def _compile(
        self,
        *,
        workflow: Dict[str, Any],
        graph: Dict[str, Any],
        registration: Dict[str, Any],
        python_source: str,
    ) -> CandidateCompilation:
        if self.compiler is None:
            raise WorkflowError("template_catalog_unavailable")
        try:
            result = self.compiler.compile(
                workflow_uuid=workflow["uuid"],
                workflow_revision=workflow["revision"],
                python_source=python_source,
                source_uri=registration["source_uri"],
                applied_graph=graph,
            )
            return CandidateCompilation.model_validate(result)
        except WorkflowError:
            raise
        except Exception:
            raise WorkflowError("internal_error") from None

    def _preserve_author_source_compilation(
        self,
        *,
        compilation: CandidateCompilation,
        workflow: Dict[str, Any],
        graph: Dict[str, Any],
        python_source: str,
    ) -> CandidateCompilation:
        """让自动激活候选使用原始作者源码及其精确源码映射。

        参数：``compilation`` 是当前目录刚生成的结果；``workflow``、``graph``
        和 ``python_source`` 是同一编译事务的权威输入。返回可按普通候选合同
        签发的源码保留结果。异常：编译器不支持可信源码保留或投影失败时抛
        ``candidate_invalid``，固定点激活会隔离该来源且绝不回写规范化文本。
        """

        if (
            not compilation.valid
            or compilation.normalized_python_source == python_source
        ):
            return compilation
        if self.compiler is None:
            raise WorkflowError("template_catalog_unavailable")
        preserve = getattr(self.compiler, "preserve_author_source", None)
        if not callable(preserve):
            raise WorkflowError("candidate_invalid")
        try:
            result = preserve(
                compilation=compilation,
                workflow_uuid=workflow["uuid"],
                workflow_revision=workflow["revision"],
                python_source=python_source,
                applied_graph=graph,
            )
            preserved = CandidateCompilation.model_validate(result)
        except WorkflowError:
            raise
        except Exception:
            raise WorkflowError("candidate_invalid") from None
        if preserved.normalized_python_source != python_source:
            raise WorkflowError("candidate_invalid")
        return preserved

    def _catalog_fingerprint(self) -> str:
        if self.compiler is None:
            raise WorkflowError("template_catalog_unavailable")
        try:
            value = self.compiler.template_catalog_fingerprint
        except Exception:
            raise WorkflowError("template_catalog_unavailable") from None
        if not isinstance(value, str) or _HASH_TOKEN.fullmatch(value) is None:
            raise WorkflowError("template_catalog_unavailable")
        return value

    @staticmethod
    def _source_only_candidate_is_already_applied(
        *,
        candidate: Optional[Dict[str, Any]],
        applied_source: Any,
        workflow_revision: int,
        draft_hash: str,
    ) -> bool:
        """判断当前目录重编译是否只重新证明了既有应用事实。

        参数：候选版本（Candidate）、已应用源码、当前工作流修订和作者源码哈希。
        返回：候选不改变图，且同一作者字节已绑定当前修订时为 ``True``。
        异常：无；持久派生字段形状异常只按不匹配处理。
        """

        return (
            isinstance(candidate, dict)
            and candidate.get("changeset", {}).get("kind") == "source_only"
            and isinstance(applied_source, dict)
            and applied_source.get("workflow_revision") == workflow_revision
            and applied_source.get("source_hash") == draft_hash
        )

    def _issue_candidate(
        self,
        *,
        workflow_revision: int,
        draft_hash: str,
        compilation: CandidateCompilation,
        applied_graph: Dict[str, Any],
        draft_python_source: str,
    ) -> Optional[Dict[str, Any]]:
        """校验编译结果并签发一个可信候选版本（Candidate）。

        参数：``workflow_revision`` 与 ``draft_hash`` 固定候选基线；``compilation``
        是编译结果；``applied_graph`` 是当前应用图；``draft_python_source`` 用于
        诊断和源码映射校验。返回：包含规范八字段、候选哈希（Candidate Hash）
        和更新时间的候选字典；编译结果不能证明时返回 ``None``。异常：目录不可
        用等非候选错误原样传播，其他候选结构或编码错误转为稳定诊断。
        """

        applied_graph = self._validated_applied_backend_graph(applied_graph)
        if not self._normalize_candidate_diagnostics(
            compilation,
            python_source=draft_python_source,
        ):
            return None
        if not compilation.valid:
            return None
        assert compilation.graph is not None
        try:
            graph = self._backend_candidate_graph(
                compilation.graph,
                applied_graph=applied_graph,
            )
            if not isinstance(compilation.source_map, list):
                raise ValueError
            source_map = [
                CandidateSourceMapEntry.model_validate(item).model_dump()
                for item in compilation.source_map
            ]
            if not source_ranges_fit(
                compilation.normalized_python_source,
                source_map,
            ):
                raise ValueError
            changeset = CandidateChangeset.model_validate(
                compilation.changeset,
            ).model_dump()
            validate_candidate_bundle(
                graph=graph,
                base_graph=applied_graph,
                workflow_uuid=graph["workflow"]["uuid"],
                revision=workflow_revision,
                source_map=source_map,
                changeset=changeset,
            )
            self._definition_store.validate_candidate_identity_ownership(
                workflow_uuid=graph["workflow"]["uuid"],
                node_uuids=(item["uuid"] for item in graph["nodes"]),
                edge_uuids=(item["uuid"] for item in graph["edges"]),
            )
            compiler_version = compilation.compiler_version
            if not compiler_version.strip():
                raise ValueError
            template_catalog_fingerprint = compilation.template_catalog_fingerprint
            if _HASH_TOKEN.fullmatch(template_catalog_fingerprint) is None:
                raise ValueError
        except StoreAuthoringConflict as error:
            if error.code != "candidate_identity_conflict":
                raise
            self._set_candidate_identity_conflict_diagnostic(compilation)
            return None
        except (
            GraphValidationError,
            CandidateBundleError,
            KeyError,
            TypeError,
            ValidationError,
            ValueError,
            WorkflowError,
        ) as error:
            if isinstance(error, WorkflowError) and error.code != "candidate_invalid":
                raise
            logger.exception(
                "工作流候选签发失败 workflow_uuid=%s revision=%s draft_hash=%s "
                "error=%s",
                compilation.graph.get("workflow", {}).get("uuid")
                if isinstance(compilation.graph, dict)
                else None,
                workflow_revision,
                draft_hash,
                error,
            )
            self._set_candidate_invalid_diagnostic(compilation)
            return None
        bundle = {
            "base_workflow_revision": workflow_revision,
            "draft_hash": draft_hash,
            "graph": graph,
            "normalized_python_source": compilation.normalized_python_source,
            "source_map": source_map,
            "changeset": changeset,
            "compiler_version": compiler_version,
            "template_catalog_fingerprint": template_catalog_fingerprint,
        }
        try:
            # ``candidate_hash`` 是共享八字段规则对本次签发正文的唯一稳定摘要。
            candidate_hash = compute_authoring_candidate_hash(bundle)
        except AuthoringCandidateHashError:
            self._set_candidate_invalid_diagnostic(compilation)
            return None
        return {
            "candidate_hash": candidate_hash,
            **bundle,
            "update_time": utc_now(),
        }

    @staticmethod
    def _set_candidate_invalid_diagnostic(
        compilation: CandidateCompilation,
    ) -> None:
        compilation.diagnostics = [
            {
                "severity": "error",
                "code": "candidate_invalid",
                "message": _ERRORS["candidate_invalid"][1],
            }
        ]

    @staticmethod
    def _set_candidate_identity_conflict_diagnostic(
        compilation: CandidateCompilation,
    ) -> None:
        """把跨工作流节点/连线身份占用投影为可行动候选诊断。"""

        compilation.diagnostics = [
            {
                "severity": "error",
                "code": "candidate_identity_conflict",
                "message": _ERRORS["candidate_identity_conflict"][1],
            }
        ]

    @classmethod
    def _normalize_candidate_diagnostics(
        cls,
        compilation: CandidateCompilation,
        *,
        python_source: str,
    ) -> bool:
        try:
            if not isinstance(compilation.diagnostics, list):
                raise ValueError
            compilation.diagnostics = [
                CandidateDiagnostic.model_validate(item).model_dump(
                    exclude_none=True,
                )
                for item in compilation.diagnostics
            ]
            source_ranges = [
                item["source_range"]
                for item in compilation.diagnostics
                if item.get("source_range") is not None
            ]
            if not source_ranges_fit(python_source, source_ranges):
                raise ValueError
        except (TypeError, ValidationError, ValueError):
            cls._set_candidate_invalid_diagnostic(compilation)
            return False
        return True

    @staticmethod
    def _backend_graph_projection(
        graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        """按后端（Backend）JSON omitempty 语义投影候选版本（Candidate）。

        参数：``graph`` 是编译器产出的完整候选图。返回：删除可选 ``None`` 字段、
        保留后端读取容器形状的新字典；输入图不被修改。
        """

        def omit_none(value: Any) -> Any:
            """删除单个实体中的 ``None`` 字段；非字典值保持原样返回。"""

            if not isinstance(value, dict):
                return value
            return {key: item for key, item in value.items() if item is not None}

        return {
            "workflow": omit_none(graph.get("workflow") or {}),
            "nodes": [omit_none(item) for item in (graph.get("nodes") or [])],
            "edges": [omit_none(item) for item in (graph.get("edges") or [])],
            "node_templates": [
                omit_none(item) for item in (graph.get("node_templates") or [])
            ],
            "handle_templates": [
                omit_none(item) for item in (graph.get("handle_templates") or [])
            ],
        }

    @classmethod
    def _backend_candidate_graph(
        cls,
        graph: Dict[str, Any],
        *,
        applied_graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把编译器写实体补全为冻结的后端（Backend）读取形状。

        参数：``graph`` 是编译器写模型，``applied_graph`` 是当前已应用图。返回：
        补齐稳定身份、时间与读取字段的候选图；非法图抛出稳定工作流错误。
        """

        applied = cls._validated_applied_backend_graph(applied_graph)
        cls._require_candidate_graph_containers(graph)
        projected = cls._backend_graph_projection(graph)
        applied_workflow = applied["workflow"]
        workflow_uuid = applied_workflow["uuid"]
        timestamp = applied_workflow["update_time"]
        applied_nodes = {item["uuid"]: item for item in applied["nodes"]}
        applied_edges = {item["uuid"]: item for item in applied["edges"]}
        applied_node_templates = {
            item["uuid"]: item for item in applied["node_templates"]
        }
        applied_handle_templates = {
            item["uuid"]: item for item in applied["handle_templates"]
        }

        nodes = []
        for item in projected["nodes"]:
            value = WorkflowNodeWrite.model_validate(item).model_dump(
                exclude_none=True,
            )
            persisted = applied_nodes.get(value["uuid"], {})
            nodes.append(
                {
                    "uuid": value["uuid"],
                    "create_time": persisted.get("create_time", timestamp),
                    "update_time": persisted.get("update_time", timestamp),
                    "meta_data": value.get("meta_data", {}),
                    "workflow_uuid": workflow_uuid,
                    **value,
                }
            )
        cls._require_backend_read_fields(
            nodes,
            _NODE_REQUIRED_READ_FIELDS,
        )

        edges = []
        for item in projected["edges"]:
            value = WorkflowEdgeWrite.model_validate(item).model_dump(
                exclude_none=True,
            )
            persisted = applied_edges.get(value["uuid"], {})
            edges.append(
                {
                    "uuid": value["uuid"],
                    "create_time": persisted.get("create_time", timestamp),
                    "update_time": persisted.get("update_time", timestamp),
                    "meta_data": value.get("meta_data", {}),
                    **value,
                }
            )
        cls._require_backend_read_fields(
            edges,
            _EDGE_REQUIRED_READ_FIELDS,
        )

        projected["workflow"] = {
            key: value
            for key, value in {
                **applied_workflow,
                **projected["workflow"],
                "uuid": workflow_uuid,
                "create_time": applied_workflow["create_time"],
                "update_time": timestamp,
            }.items()
            if key in _WORKFLOW_READ_FIELDS
        }
        projected["nodes"] = nodes
        projected["edges"] = edges
        projected["node_templates"] = cls._hydrate_backend_catalog_entities(
            projected["node_templates"],
            persisted=applied_node_templates,
            timestamp=timestamp,
            uuid_fields={"uuid", "resource_template_uuid"},
            allowed_fields=_NODE_TEMPLATE_READ_FIELDS,
            required_fields=_NODE_TEMPLATE_REQUIRED_READ_FIELDS,
        )
        projected["handle_templates"] = cls._hydrate_backend_catalog_entities(
            projected["handle_templates"],
            persisted=applied_handle_templates,
            timestamp=timestamp,
            uuid_fields={"uuid", "workflow_node_template_uuid"},
            allowed_fields=_HANDLE_TEMPLATE_READ_FIELDS,
            required_fields=_HANDLE_TEMPLATE_REQUIRED_READ_FIELDS,
        )
        cls._require_backend_read_fields(
            [projected["workflow"]],
            _WORKFLOW_REQUIRED_READ_FIELDS,
        )
        try:
            cls._require_backend_entity_types(projected)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise WorkflowError("candidate_invalid") from None
        if projected["workflow"]["revision"] != applied_workflow["revision"]:
            raise WorkflowError("candidate_invalid")
        return projected

    @classmethod
    def _validated_applied_backend_graph(
        cls,
        graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        """检查候选版本（Candidate）前先校验权威（Authority）持有的工作流图。"""

        try:
            applied = cls._backend_graph_projection(graph)
            cls._require_backend_read_fields(
                [applied["workflow"]],
                _WORKFLOW_REQUIRED_READ_FIELDS,
                error_code="internal_error",
            )
            cls._require_backend_read_fields(
                applied["nodes"],
                _NODE_REQUIRED_READ_FIELDS,
                error_code="internal_error",
            )
            cls._require_backend_read_fields(
                applied["edges"],
                _EDGE_REQUIRED_READ_FIELDS,
                error_code="internal_error",
            )
            cls._require_backend_read_fields(
                applied["node_templates"],
                _NODE_TEMPLATE_REQUIRED_READ_FIELDS,
                error_code="internal_error",
            )
            cls._require_backend_read_fields(
                applied["handle_templates"],
                _HANDLE_TEMPLATE_REQUIRED_READ_FIELDS,
                error_code="internal_error",
            )
            cls._require_backend_entity_types(applied)
            return applied
        except WorkflowError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError):
            raise WorkflowError("internal_error") from None

    @staticmethod
    def _require_backend_entity_types(graph: Dict[str, Any]) -> None:
        """在完整工作流图上强制执行冻结的后端（Backend）JSON 类型。

        参数：``graph`` 是待验证的完整工作流（Workflow）图。返回：无；任一字段
        类型偏离冻结合同即抛出 ``ValueError``。
        """

        def exact(entity: Dict[str, Any], fields: set[str], expected: type) -> None:
            """要求 ``entity`` 指定字段严格等于 ``expected`` 类型。"""

            if any(type(entity[field]) is not expected for field in fields):
                raise ValueError

        def optional(
            entity: Dict[str, Any],
            fields: set[str],
            expected: type,
        ) -> None:
            """要求存在的可选字段严格等于 ``expected`` 类型。"""

            if any(
                field in entity and type(entity[field]) is not expected
                for field in fields
            ):
                raise ValueError

        def uuids(entity: Dict[str, Any], fields: set[str]) -> None:
            """要求指定字段均为合法 UUID 字符串；非法值抛出 ``ValueError``。"""

            exact(entity, fields, str)
            for field in fields:
                validate_uuid(entity[field])

        def optional_uuids(entity: Dict[str, Any], fields: set[str]) -> None:
            """验证存在的可选 UUID 字段；缺失字段保持合法。"""

            for field in fields:
                if field in entity:
                    uuids(entity, {field})

        workflow = graph["workflow"]
        uuids(workflow, {"uuid"})
        exact(workflow, {"create_time", "update_time", "name"}, str)
        exact(workflow, {"meta_data"}, dict)
        exact(workflow, {"tags"}, list)
        normalize_json_object(workflow["meta_data"])
        normalize_json_array(workflow["tags"])
        optional(workflow, {"description"}, str)
        revision = workflow["revision"]
        if type(revision) is not int or not 1 <= revision <= (1 << 63) - 1:
            raise ValueError

        for node in graph["nodes"]:
            uuids(node, {"uuid", "workflow_uuid"})
            optional_uuids(
                node,
                {
                    "workflow_node_template_uuid",
                    "parent_uuid",
                    "material_uuid",
                },
            )
            exact(
                node,
                {"create_time", "update_time", "name", "status", "type"},
                str,
            )
            exact(
                node,
                {"meta_data", "pose", "param", "execution_policy"},
                dict,
            )
            for field in ("meta_data", "pose", "param", "execution_policy"):
                normalize_json_object(node[field])
            exact(node, {"disabled", "minimized"}, bool)
            optional(
                node,
                {
                    "description",
                    "icon",
                    "footer",
                    "action_name",
                    "action_type",
                    "script",
                },
                str,
            )

        for edge in graph["edges"]:
            uuids(
                edge,
                {
                    "uuid",
                    "source_node_uuid",
                    "target_node_uuid",
                    "source_handle_uuid",
                    "target_handle_uuid",
                },
            )
            exact(edge, {"create_time", "update_time"}, str)
            exact(edge, {"meta_data"}, dict)
            normalize_json_object(edge["meta_data"])
            optional(edge, {"description"}, str)

        for template in graph["node_templates"]:
            uuids(template, {"uuid", "resource_template_uuid"})
            exact(
                template,
                {
                    "create_time",
                    "update_time",
                    "name",
                    "display_name",
                    "type",
                    "node_type",
                },
                str,
            )
            exact(
                template,
                {
                    "meta_data",
                    "goal",
                    "goal_default",
                    "feedback",
                    "result",
                },
                dict,
            )
            for field in (
                "meta_data",
                "goal",
                "goal_default",
                "feedback",
                "result",
            ):
                normalize_json_object(template[field])
            optional(
                template,
                {
                    "description",
                    "class",
                    "schema",
                    "icon",
                    "header",
                    "footer",
                },
                str,
            )

        for handle in graph["handle_templates"]:
            uuids(handle, {"uuid", "workflow_node_template_uuid"})
            exact(
                handle,
                {
                    "create_time",
                    "update_time",
                    "handle_key",
                    "io_type",
                    "display_name",
                    "type",
                },
                str,
            )
            exact(handle, {"meta_data"}, dict)
            normalize_json_object(handle["meta_data"])
            exact(handle, {"required"}, bool)
            optional(
                handle,
                {"description", "data_source", "data_key"},
                str,
            )

    @staticmethod
    def _require_candidate_graph_containers(graph: Dict[str, Any]) -> None:
        workflow = graph.get("workflow")
        if workflow is not None and not isinstance(workflow, dict):
            raise WorkflowError("candidate_invalid")
        for field in (
            "nodes",
            "edges",
            "node_templates",
            "handle_templates",
        ):
            entities = graph.get(field)
            if entities is None:
                continue
            if not isinstance(entities, list) or any(
                not isinstance(item, dict) for item in entities
            ):
                raise WorkflowError("candidate_invalid")

    @staticmethod
    def _hydrate_backend_catalog_entities(
        entities: List[Dict[str, Any]],
        *,
        persisted: Dict[str, Dict[str, Any]],
        timestamp: str,
        uuid_fields: set[str],
        allowed_fields: set[str],
        required_fields: set[str],
    ) -> List[Dict[str, Any]]:
        hydrated = []
        for item in entities:
            value = {
                key: child
                for key, child in item.items()
                if key in allowed_fields and child is not None
            }
            for field in uuid_fields:
                try:
                    value[field] = validate_uuid(value[field])
                except (KeyError, ValueError):
                    raise WorkflowError("candidate_invalid") from None
            previous = persisted.get(value["uuid"], {})
            hydrated.append(
                {
                    "uuid": value["uuid"],
                    "create_time": previous.get("create_time", timestamp),
                    "update_time": previous.get("update_time", timestamp),
                    "meta_data": value.get("meta_data", {}),
                    **value,
                }
            )
        WorkflowService._require_backend_read_fields(
            hydrated,
            required_fields,
        )
        return hydrated

    @staticmethod
    def _require_backend_read_fields(
        entities: List[Dict[str, Any]],
        required_fields: set[str],
        *,
        error_code: str = "candidate_invalid",
    ) -> None:
        if any(
            not isinstance(item, dict) or not required_fields.issubset(item)
            for item in entities
        ):
            raise WorkflowError(error_code)

    @classmethod
    def _post_commit_candidate_graph(
        cls,
        graph: Dict[str, Any],
        *,
        workflow: Dict[str, Any],
    ) -> Dict[str, Any]:
        projected = cls._backend_graph_projection(graph)
        projected["workflow"] = {
            **projected["workflow"],
            **workflow,
        }
        return projected

    def _authoring_aggregate(
        self,
        *,
        workflow: Dict[str, Any],
        graph: Dict[str, Any],
        registration: Dict[str, Any],
        source: Optional[Dict[str, Any]],
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        draft: Optional[Dict[str, Any]] = None
        diagnostics: List[Dict[str, Any]] = []
        if source is not None:
            if record["observed_draft_hash"] == source["draft_hash"]:
                diagnostics = record["diagnostics"]
            draft = {
                "source_uri": registration["source_uri"],
                **source,
                "diagnostics": diagnostics,
            }

        stored_candidate = record.get("candidate")
        candidate: Optional[Dict[str, Any]] = None
        candidate_stale = False
        if stored_candidate is not None and source is not None:
            catalog_matches = False
            try:
                catalog_matches = (
                    stored_candidate["template_catalog_fingerprint"]
                    == self._catalog_fingerprint()
                )
            except WorkflowError:
                catalog_matches = False
            candidate_current = (
                record["observed_draft_hash"] == source["draft_hash"]
                and stored_candidate["draft_hash"] == source["draft_hash"]
                and stored_candidate["base_workflow_revision"] == workflow["revision"]
                and catalog_matches
            )
            if candidate_current:
                candidate = stored_candidate
            else:
                candidate_stale = True

        applied_source = record.get("applied_source")
        if source is None:
            state = "draft_missing"
        elif candidate_stale:
            state = "candidate_stale"
        elif any(
            str(item.get("severity", "")).lower() == "error" for item in diagnostics
        ):
            state = "draft_invalid"
        elif candidate is not None:
            state = (
                "unapplied_source_only"
                if candidate["changeset"]["kind"] == "source_only"
                else "unapplied_graph"
            )
        elif (
            applied_source is not None
            and applied_source["workflow_revision"] == workflow["revision"]
            and applied_source["source_hash"] == source["draft_hash"]
        ):
            state = "applied"
        else:
            state = "applied_source_stale"

        return {
            "workflow_uuid": workflow["uuid"],
            "workflow_revision": workflow["revision"],
            "state": state,
            "applied_graph": graph,
            "draft": draft,
            "candidate": candidate,
            "applied_source": applied_source,
        }

    def _authoring_lock(self, workflow_uuid: str) -> threading.RLock:
        with self._locks_guard:
            return self._authoring_locks.setdefault(
                workflow_uuid,
                threading.RLock(),
            )

    @staticmethod
    def _normalize_page(page: int, page_size: int) -> Tuple[int, int]:
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20
        if page_size > 100:
            page_size = 100
        return page, page_size

    @staticmethod
    def _optional_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _validate_hash(value: Optional[str], *, nullable: bool) -> None:
        if value is None:
            if nullable:
                return
            raise WorkflowError("invalid_input")
        if _HASH_TOKEN.fullmatch(value) is None:
            raise WorkflowError("invalid_input")


__all__ = [
    "AuthoringCompiler",
    "WorkflowConflict",
    "WorkflowError",
    "WorkflowService",
]
