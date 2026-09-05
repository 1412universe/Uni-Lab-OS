"""工作流资源占用区间与静态资源关系图。

这个模块是调度器资源语义的深模块：作者图只需要提供资源别名、节点边和作用域，
调用方即可取得不可变的区间/原子取得集合；资源实例绑定后再由同一个 Interface
完成整站关系合并与有向无环检查。模块不读取数据库、不调用设备、不依赖运行时
Scheduler，因此可以在发布、任务创建和恢复路径复用同一套安全规则。
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


RESOURCE_PLAN_VERSION = 1
RESOURCE_PLAN_CAPABILITY = "resource_intervals_v1"
STATIC_RESOURCE_DAG_CAPABILITY = "static_resource_dag_v1"
_PLAN_NAMESPACE = uuid5(NAMESPACE_URL, "unilabos:workflow:resource-lock-plan:v1")


class ResourcePlanError(ValueError):
    """资源计划无法安全生成、绑定或验证。"""

    def __init__(self, code: str, message: str, *, path: str = "/") -> None:
        self.code = code
        self.message = message
        self.path = path
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CanonicalResource:
    """一项资源的稳定计划身份。

    ``resource_id`` 是计划内部身份；``canonical_key`` 在 template 阶段是
    ``symbol:<alias>``，在 bound 阶段必须是 Inventory 证明过的实例键。
    """

    resource_id: str
    canonical_key: str
    kind: str
    alias: str
    instance_uuid: str = ""


@dataclass(frozen=True, slots=True)
class ResourceScope:
    """根或词法资源作用域的冻结边界。"""

    scope_id: str
    kind: str
    resource_ids: tuple[str, ...]
    parent_scope_id: str | None = None
    entry_node_uuid: str = ""
    exit_node_uuid: str = ""
    node_uuids: tuple[str, ...] = ()
    hard_boundary: bool = True
    branch_id: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class ResourceInterval:
    """一项资源从取得到安全释放的实际占用区间。"""

    interval_id: str
    resource_id: str
    acquire_node_uuid: str
    release_node_uuid: str
    node_uuids: tuple[str, ...]
    scope_id: str | None
    workflow_instance_id: str
    branch_id: str
    condition_ids: tuple[str, ...] = ()
    source: str = ""
    physical_state: str = ""
    safe_release: bool = False
    explicit_boundary: bool = False


@dataclass(frozen=True, slots=True)
class AcquireSet:
    """一个全有或全无的资源新增集合。"""

    acquire_set_id: str
    node_uuid: str
    resource_ids: tuple[str, ...]
    preheld_resource_ids: tuple[str, ...] = ()
    atomic: bool = True
    branch_id: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class ResourceRelation:
    """持有资源到后续新增资源的静态取得关系。"""

    relation_id: str
    from_resource_id: str
    to_resource_id: str
    source_interval_id: str = ""
    source_node_uuid: str = ""
    branch_id: str = ""
    possible_concurrency: bool = True
    reason: str = "hold_then_acquire"


@dataclass(frozen=True, slots=True)
class ResourcePlan:
    """可持久化的资源计划；所有集合均按稳定顺序冻结。"""

    plan_id: str
    version: int = RESOURCE_PLAN_VERSION
    binding_state: str = "template"
    capabilities: tuple[str, ...] = (RESOURCE_PLAN_CAPABILITY,)
    resources: tuple[CanonicalResource, ...] = ()
    scopes: tuple[ResourceScope, ...] = ()
    intervals: tuple[ResourceInterval, ...] = ()
    acquire_sets: tuple[AcquireSet, ...] = ()
    relations: tuple[ResourceRelation, ...] = ()
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


def compile_template_resource_plan(
    graph: Mapping[str, Any],
    *,
    root_scopes: Sequence[Mapping[str, Any]] | None = None,
) -> ResourcePlan:
    """从冻结工作流图计算符号资源区间和取得关系。

    图可通过顶层 ``resource_scopes``、``resources`` 提供声明，也可放在
    ``workflow.meta_data.unilab``。节点资源支持 ``resource_defaults``、
    ``resources`` 或 ``meta_data.unilab.resource_defaults``。资源仍是别名，
    不在此处猜测设备/Site UUID；实例绑定必须经过
    :func:`bind_station_resource_plan`。
    """

    if not isinstance(graph, Mapping):
        raise ResourcePlanError("invalid_graph", "资源计划输入图必须是对象")
    raw_nodes = graph.get("nodes", [])
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
        raise ResourcePlanError("invalid_graph", "资源计划 nodes 必须是数组", path="/nodes")
    nodes: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("uuid"), str):
            raise ResourcePlanError(
                "invalid_node", "资源计划节点缺少 uuid", path=f"/nodes/{index}"
            )
        node_uuid = str(raw["uuid"])
        if node_uuid in nodes:
            raise ResourcePlanError("duplicate_node", f"节点 UUID 重复：{node_uuid}")
        nodes[node_uuid] = raw
    edges = _normalize_edges(graph.get("edges", []), nodes)
    order = _topological_order(nodes, edges)
    workflow = graph.get("workflow")
    workflow_meta = workflow.get("meta_data") if isinstance(workflow, Mapping) else None
    unilab_meta = (
        workflow_meta.get("unilab") if isinstance(workflow_meta, Mapping) else None
    )
    if not isinstance(unilab_meta, Mapping):
        unilab_meta = {}
    declarations = root_scopes
    if declarations is None:
        declarations = graph.get("resource_scopes")
    if declarations is None:
        declarations = unilab_meta.get("resource_scopes")
    if declarations is None:
        declarations = []
    if not isinstance(declarations, Sequence) or isinstance(declarations, (str, bytes)):
        raise ResourcePlanError("invalid_scope", "resource_scopes 必须是数组", path="/resource_scopes")
    raw_root_resources = graph.get("resources")
    if raw_root_resources is None:
        raw_root_resources = unilab_meta.get("resources")
    if raw_root_resources is not None:
        raw_root_resources = _string_sequence(raw_root_resources, "/resources")
        # 根资源与词法 ``with resources(...)`` 是两类可叠加的声明：根作用域
        # 覆盖整个 Workflow，词法作用域在其内部建立更窄的硬边界。根声明统一
        # 放到列表首部，保证确定性的父级语义与序列化顺序。
        declarations = [
            {
                "scope_id": "root",
                "kind": "root",
                "resources": list(raw_root_resources),
                "node_uuids": list(order),
                "source": "workflow.root",
            },
            *list(declarations),
        ]

    resource_aliases: list[str] = []
    scope_inputs: list[dict[str, Any]] = []
    for index, raw_scope in enumerate(declarations):
        if not isinstance(raw_scope, Mapping):
            raise ResourcePlanError("invalid_scope", "资源作用域必须是对象", path=f"/resource_scopes/{index}")
        scope_kind = str(raw_scope.get("kind") or ("root" if index == 0 else "with"))
        if scope_kind not in {"root", "with", "action", "transfer"}:
            raise ResourcePlanError("invalid_scope_kind", f"不支持资源作用域类型：{scope_kind}")
        aliases = _string_sequence(
            raw_scope.get("resources", raw_scope.get("resource_aliases", [])),
            f"/resource_scopes/{index}/resources",
        )
        if not aliases:
            raise ResourcePlanError("empty_scope", "资源作用域不能为空", path=f"/resource_scopes/{index}/resources")
        resource_aliases.extend(aliases)
        node_uuids = tuple(
            _string_sequence(raw_scope.get("node_uuids", []), f"/resource_scopes/{index}/node_uuids")
        )
        unknown_nodes = set(node_uuids) - set(nodes)
        if unknown_nodes:
            raise ResourcePlanError("unknown_scope_node", f"作用域引用未知节点：{sorted(unknown_nodes)}")
        scope_inputs.append(
            {
                "scope_id": str(raw_scope.get("scope_id") or f"scope-{index + 1}"),
                "kind": scope_kind,
                "aliases": aliases,
                "node_uuids": node_uuids,
                "entry_node_uuid": str(raw_scope.get("entry_node_uuid") or ""),
                "exit_node_uuid": str(raw_scope.get("exit_node_uuid") or ""),
                "parent_scope_id": (
                    str(raw_scope["parent_scope_id"])
                    if raw_scope.get("parent_scope_id") is not None
                    else None
                ),
                "branch_id": str(raw_scope.get("branch_id") or ""),
                "source": str(raw_scope.get("source") or f"resource_scopes[{index}]"),
            }
        )
    for node in nodes.values():
        for alias in _node_resource_aliases(node):
            resource_aliases.append(alias)
    aliases = tuple(sorted(set(resource_aliases)))
    resource_by_alias = {
        alias: CanonicalResource(
            resource_id=str(uuid5(_PLAN_NAMESPACE, f"resource:{alias}")),
            canonical_key=f"symbol:{alias}",
            kind="symbolic",
            alias=alias,
        )
        for alias in aliases
    }
    scopes: list[ResourceScope] = []
    scope_membership: dict[str, list[tuple[str, bool, str | None]]] = defaultdict(list)
    for raw_scope in scope_inputs:
        members = _scope_node_members(raw_scope, order, nodes, edges)
        entry = raw_scope["entry_node_uuid"] or (members[0] if members else "")
        exit_node = raw_scope["exit_node_uuid"] or (members[-1] if members else "")
        if not entry or not exit_node:
            raise ResourcePlanError("empty_scope", f"作用域 {raw_scope['scope_id']} 没有可执行节点")
        scope = ResourceScope(
            scope_id=raw_scope["scope_id"],
            kind=raw_scope["kind"],
            resource_ids=tuple(sorted(resource_by_alias[a].resource_id for a in raw_scope["aliases"])),
            parent_scope_id=raw_scope["parent_scope_id"],
            entry_node_uuid=entry,
            exit_node_uuid=exit_node,
            node_uuids=tuple(members),
            hard_boundary=True,
            branch_id=raw_scope["branch_id"],
            source=raw_scope["source"],
        )
        scopes.append(scope)
        for node_uuid in members:
            for resource_id in scope.resource_ids:
                scope_membership[node_uuid].append((resource_id, True, scope.scope_id))

    effective_by_node: dict[str, tuple[str, ...]] = {}
    source_by_node: dict[str, list[str]] = defaultdict(list)
    for node_uuid in order:
        node = nodes[node_uuid]
        resources = {resource_by_alias[a].resource_id for a in _node_resource_aliases(node)}
        for resource_id, _explicit, scope_id in scope_membership.get(node_uuid, []):
            resources.add(resource_id)
            source_by_node[node_uuid].append(scope_id or "scope")
        effective_by_node[node_uuid] = tuple(sorted(resources))
        if _node_resource_aliases(node):
            source_by_node[node_uuid].append("action.default")

    branch_by_node = {node_uuid: _branch_id(nodes[node_uuid]) for node_uuid in order}
    intervals: list[ResourceInterval] = []
    interval_by_node_resource: dict[tuple[str, str], ResourceInterval] = {}
    for resource_id in sorted({r for values in effective_by_node.values() for r in values}):
        uses = [node_uuid for node_uuid in order if resource_id in effective_by_node[node_uuid]]
        chains: list[list[str]] = []
        for node_uuid in uses:
            if chains and _can_continue_interval(
                chains[-1][-1], node_uuid, branch_by_node, edges, scopes, resource_id
            ):
                chains[-1].append(node_uuid)
            else:
                chains.append([node_uuid])
        for chain in chains:
            first, last = chain[0], chain[-1]
            scope_id = _common_scope_id(chain, scopes, resource_id)
            explicit_boundary = bool(scope_id)
            interval_id = str(
                uuid5(
                    _PLAN_NAMESPACE,
                    f"interval:{resource_id}:{first}:{last}:{branch_by_node[first]}:{scope_id or ''}",
                )
            )
            interval_sources = sorted(
                {
                    source
                    for node_uuid in chain
                    for source in source_by_node[node_uuid]
                }
            )
            interval = ResourceInterval(
                interval_id=interval_id,
                resource_id=resource_id,
                acquire_node_uuid=first,
                release_node_uuid=last,
                node_uuids=tuple(chain),
                scope_id=scope_id,
                workflow_instance_id=_workflow_instance_id(graph),
                branch_id=branch_by_node[first],
                source=",".join(interval_sources),
                physical_state="unknown",
                safe_release=False,
                explicit_boundary=explicit_boundary,
            )
            intervals.append(interval)
            for node_uuid in chain:
                interval_by_node_resource[(node_uuid, resource_id)] = interval

    acquire_sets: list[AcquireSet] = []
    relations: list[ResourceRelation] = []
    active_by_branch: dict[str, tuple[str, ...]] = {}
    previous_node_by_branch: dict[str, str] = {}
    for node_uuid in order:
        branch_id = branch_by_node[node_uuid]
        effective = effective_by_node[node_uuid]
        previous = active_by_branch.get(branch_id, ())
        previous_node = previous_node_by_branch.get(branch_id)
        continuous = {
            resource_id
            for resource_id in set(previous) & set(effective)
            if previous_node is not None
            and _can_continue_interval(
                previous_node,
                node_uuid,
                branch_by_node,
                edges,
                scopes,
                resource_id,
            )
        }
        preheld = tuple(sorted(continuous))
        new_resources = tuple(sorted(set(effective) - continuous))
        if new_resources:
            acquire_set_id = str(uuid5(_PLAN_NAMESPACE, f"acquire:{node_uuid}:{branch_id}"))
            acquire_sets.append(
                AcquireSet(
                    acquire_set_id=acquire_set_id,
                    node_uuid=node_uuid,
                    resource_ids=new_resources,
                    preheld_resource_ids=preheld,
                    atomic=True,
                    branch_id=branch_id,
                    source=",".join(sorted(set(source_by_node[node_uuid]))),
                )
            )
            for from_resource_id in preheld:
                source_interval = interval_by_node_resource.get(
                    (previous_node or node_uuid, from_resource_id)
                )
                for to_resource_id in new_resources:
                    relation_id = str(
                        uuid5(
                            _PLAN_NAMESPACE,
                            f"relation:{from_resource_id}:{to_resource_id}:{node_uuid}:{branch_id}",
                        )
                    )
                    relations.append(
                        ResourceRelation(
                            relation_id=relation_id,
                            from_resource_id=from_resource_id,
                            to_resource_id=to_resource_id,
                            source_interval_id=source_interval.interval_id if source_interval else "",
                            source_node_uuid=node_uuid,
                            branch_id=branch_id,
                            reason="hold_then_acquire",
                        )
            )
        active_by_branch[branch_id] = effective
        previous_node_by_branch[branch_id] = node_uuid

    workflow_id = _workflow_instance_id(graph)
    plan_id = str(uuid5(_PLAN_NAMESPACE, f"plan:{workflow_id}:{','.join(order)}"))
    plan = ResourcePlan(
        plan_id=plan_id,
        binding_state="template",
        capabilities=(RESOURCE_PLAN_CAPABILITY,),
        resources=tuple(resource_by_alias[a] for a in aliases),
        scopes=tuple(sorted(scopes, key=lambda item: item.scope_id)),
        intervals=tuple(sorted(intervals, key=lambda item: item.interval_id)),
        acquire_sets=tuple(sorted(acquire_sets, key=lambda item: item.acquire_set_id)),
        relations=tuple(sorted(relations, key=lambda item: item.relation_id)),
        metadata={"workflow_instance_id": workflow_id},
    )
    validate_resource_plan(plan)
    return plan


def bind_station_resource_plan(
    template_plan: ResourcePlan,
    resource_bindings: Mapping[str, Any],
    *,
    concurrency: Sequence[ResourcePlan | Mapping[str, Any]] = (),
) -> ResourcePlan:
    """把符号资源绑定为 Inventory 已证明的具体实例并检查整站 DAG。

    ``resource_bindings`` 的键是作者别名，值可以是 UUID 字符串，或包含
    ``instance_uuid``、``canonical_key``、``kind`` 的映射。模块不负责发现这些
    值；调用者必须从 ``StationResourceInventory`` 的公开 Interface 提供它们。
    """

    if not isinstance(template_plan, ResourcePlan):
        raise ResourcePlanError("invalid_plan", "绑定输入必须是 ResourcePlan")
    if template_plan.binding_state not in {"template", "bound"}:
        raise ResourcePlanError("invalid_binding_state", "资源计划绑定状态无效")
    if not isinstance(resource_bindings, Mapping):
        raise ResourcePlanError("invalid_binding", "resource_bindings 必须是对象")
    bound_resources: list[CanonicalResource] = []
    for resource in template_plan.resources:
        raw = resource_bindings.get(resource.alias)
        if raw is None:
            raise ResourcePlanError(
                "resource_unbound", f"资源别名未绑定到具体实例：{resource.alias}",
                path=f"/resources/{resource.alias}",
            )
        if isinstance(raw, Mapping):
            instance_uuid = str(raw.get("instance_uuid") or raw.get("uuid") or "").strip()
            canonical_key = str(raw.get("canonical_key") or "").strip()
            kind = str(raw.get("kind") or "resource").strip()
        else:
            instance_uuid = str(raw).strip()
            canonical_key = ""
            kind = "resource"
        if not instance_uuid and not canonical_key:
            raise ResourcePlanError("invalid_binding", f"资源绑定缺少实例身份：{resource.alias}")
        if instance_uuid:
            try:
                instance_uuid = str(UUID(instance_uuid))
            except ValueError as error:
                raise ResourcePlanError("invalid_binding", f"资源 UUID 无效：{resource.alias}") from error
        if not canonical_key:
            canonical_key = f"{kind}:{instance_uuid}"
        bound_resources.append(
            CanonicalResource(
                resource_id=resource.resource_id,
                canonical_key=canonical_key,
                kind=kind,
                alias=resource.alias,
                instance_uuid=instance_uuid,
            )
        )
    plan = ResourcePlan(
        plan_id=template_plan.plan_id,
        binding_state="bound",
        capabilities=tuple(sorted(set(template_plan.capabilities) | {STATIC_RESOURCE_DAG_CAPABILITY})),
        resources=tuple(bound_resources),
        scopes=template_plan.scopes,
        intervals=template_plan.intervals,
        acquire_sets=template_plan.acquire_sets,
        relations=template_plan.relations,
        diagnostics=template_plan.diagnostics,
        metadata=dict(template_plan.metadata),
    )
    if concurrency:
        plan = _merge_concurrent_relations(plan, concurrency)
    validate_resource_plan(plan)
    return plan


def validate_resource_plan(plan: ResourcePlan) -> None:
    """验证资源计划引用完整、取得原子且关系图无环。"""

    if not isinstance(plan, ResourcePlan):
        raise ResourcePlanError("invalid_plan", "资源计划必须是 ResourcePlan")
    if plan.version != RESOURCE_PLAN_VERSION:
        raise ResourcePlanError("unsupported_plan_version", "资源计划版本不支持")
    if plan.binding_state not in {"template", "bound"}:
        raise ResourcePlanError("invalid_binding_state", "资源计划 binding_state 无效")
    if RESOURCE_PLAN_CAPABILITY not in plan.capabilities:
        raise ResourcePlanError(
            "unsupported_plan_capability", "资源计划缺少资源区间能力"
        )
    if (
        plan.binding_state == "bound"
        and STATIC_RESOURCE_DAG_CAPABILITY not in plan.capabilities
    ):
        raise ResourcePlanError(
            "unsupported_plan_capability", "bound 资源计划缺少静态无环证明能力"
        )
    resource_ids = [item.resource_id for item in plan.resources]
    if len(resource_ids) != len(set(resource_ids)):
        raise ResourcePlanError("duplicate_resource", "资源计划包含重复 resource_id")
    if plan.binding_state == "bound":
        canonical_keys = [item.canonical_key for item in plan.resources]
        if any(not key or key.startswith("symbol:") for key in canonical_keys):
            raise ResourcePlanError("resource_unbound", "bound 资源计划仍含符号资源")
    valid_resources = set(resource_ids)
    scope_ids = {scope.scope_id for scope in plan.scopes}
    if len(scope_ids) != len(plan.scopes):
        raise ResourcePlanError("duplicate_scope", "资源计划包含重复 scope_id")
    for scope in plan.scopes:
        if not set(scope.resource_ids) <= valid_resources:
            raise ResourcePlanError("unknown_scope_resource", f"作用域 {scope.scope_id} 引用未知资源")
        if scope.parent_scope_id and scope.parent_scope_id not in scope_ids:
            raise ResourcePlanError("unknown_parent_scope", f"作用域 {scope.scope_id} 的父作用域不存在")
    for scope_id in scope_ids:
        seen_scopes: set[str] = set()
        current = scope_id
        while current:
            if current in seen_scopes:
                raise ResourcePlanError(
                    "scope_cycle", f"资源作用域父级关系成环：{scope_id}"
                )
            seen_scopes.add(current)
            parent = next(
                (scope.parent_scope_id for scope in plan.scopes if scope.scope_id == current),
                None,
            )
            current = parent or ""
    interval_ids = {interval.interval_id for interval in plan.intervals}
    if len(interval_ids) != len(plan.intervals):
        raise ResourcePlanError("duplicate_interval", "资源计划包含重复 interval_id")
    for interval in plan.intervals:
        if interval.resource_id not in valid_resources:
            raise ResourcePlanError("unknown_interval_resource", f"区间 {interval.interval_id} 引用未知资源")
        if not interval.acquire_node_uuid or not interval.release_node_uuid or not interval.node_uuids:
            raise ResourcePlanError("invalid_interval", f"区间 {interval.interval_id} 缺少节点边界")
        if interval.scope_id and interval.scope_id not in scope_ids:
            raise ResourcePlanError("unknown_interval_scope", f"区间 {interval.interval_id} 引用未知作用域")
    acquire_ids = {item.acquire_set_id for item in plan.acquire_sets}
    if len(acquire_ids) != len(plan.acquire_sets):
        raise ResourcePlanError("duplicate_acquire_set", "资源计划包含重复 acquire_set_id")
    for acquire_set in plan.acquire_sets:
        if not acquire_set.atomic:
            raise ResourcePlanError("non_atomic_acquire", f"取得集合 {acquire_set.acquire_set_id} 不是原子集合")
        resources = set(acquire_set.resource_ids)
        preheld = set(acquire_set.preheld_resource_ids)
        if not resources <= valid_resources or not preheld <= valid_resources:
            raise ResourcePlanError("unknown_acquire_resource", f"取得集合 {acquire_set.acquire_set_id} 引用未知资源")
        if resources & preheld:
            raise ResourcePlanError("overlapping_acquire_set", f"取得集合 {acquire_set.acquire_set_id} 同时新增并预持有资源")
    relations_by_id = {item.relation_id: item for item in plan.relations}
    if len(relations_by_id) != len(plan.relations):
        raise ResourcePlanError("duplicate_relation", "资源计划包含重复 relation_id")
    adjacency: dict[str, list[str]] = defaultdict(list)
    for relation in plan.relations:
        if relation.from_resource_id not in valid_resources or relation.to_resource_id not in valid_resources:
            raise ResourcePlanError("unknown_relation_resource", f"关系 {relation.relation_id} 引用未知资源")
        if relation.from_resource_id == relation.to_resource_id:
            raise ResourcePlanError("resource_self_cycle", f"资源关系 {relation.relation_id} 形成自环")
        if relation.possible_concurrency:
            adjacency[relation.from_resource_id].append(relation.to_resource_id)
    cycle = _find_cycle(adjacency)
    if cycle:
        raise ResourcePlanError(
            "resource_cycle",
            "静态资源取得关系成环：" + " -> ".join(cycle),
            path="/relations",
        )


def serialize_resource_plan(plan: ResourcePlan) -> dict[str, Any]:
    """返回可嵌入 ``execution_plan`` JSON 的确定性字典。"""

    validate_resource_plan(plan)
    return {
        "version": plan.version,
        "plan_id": plan.plan_id,
        "binding_state": plan.binding_state,
        "capabilities": list(plan.capabilities),
        "resources": [_serialize_dataclass(item) for item in plan.resources],
        "scopes": [_serialize_dataclass(item) for item in plan.scopes],
        "intervals": [_serialize_dataclass(item) for item in plan.intervals],
        "acquire_sets": [_serialize_dataclass(item) for item in plan.acquire_sets],
        "relations": [_serialize_dataclass(item) for item in plan.relations],
        "diagnostics": [dict(item) for item in plan.diagnostics],
        "metadata": dict(plan.metadata),
    }


def deserialize_resource_plan(value: Mapping[str, Any]) -> ResourcePlan:
    """从持久化 JSON 恢复并验证资源计划。

    反序列化只负责恢复冻结事实，不解析实时 Inventory；因此 bound 计划的实例
    身份仍必须已经存在，任何缺字段或未知引用都会在 WorkflowSpec seam 失败。
    """

    if not isinstance(value, Mapping):
        raise ResourcePlanError("invalid_plan", "持久化资源计划必须是对象")
    try:
        resources = tuple(
            CanonicalResource(
                resource_id=str(item["resource_id"]),
                canonical_key=str(item["canonical_key"]),
                kind=str(item["kind"]),
                alias=str(item["alias"]),
                instance_uuid=str(item.get("instance_uuid") or ""),
            )
            for item in value.get("resources", [])
            if isinstance(item, Mapping)
        )
        scopes = tuple(
            ResourceScope(
                scope_id=str(item["scope_id"]),
                kind=str(item["kind"]),
                resource_ids=tuple(str(raw) for raw in item.get("resource_ids", [])),
                parent_scope_id=(
                    str(item["parent_scope_id"])
                    if item.get("parent_scope_id") is not None
                    else None
                ),
                entry_node_uuid=str(item.get("entry_node_uuid") or ""),
                exit_node_uuid=str(item.get("exit_node_uuid") or ""),
                node_uuids=tuple(str(raw) for raw in item.get("node_uuids", [])),
                hard_boundary=bool(item.get("hard_boundary", True)),
                branch_id=str(item.get("branch_id") or ""),
                source=str(item.get("source") or ""),
            )
            for item in value.get("scopes", [])
            if isinstance(item, Mapping)
        )
        intervals = tuple(
            ResourceInterval(
                interval_id=str(item["interval_id"]),
                resource_id=str(item["resource_id"]),
                acquire_node_uuid=str(item["acquire_node_uuid"]),
                release_node_uuid=str(item["release_node_uuid"]),
                node_uuids=tuple(str(raw) for raw in item.get("node_uuids", [])),
                scope_id=str(item["scope_id"]) if item.get("scope_id") else None,
                workflow_instance_id=str(item.get("workflow_instance_id") or ""),
                branch_id=str(item.get("branch_id") or ""),
                condition_ids=tuple(str(raw) for raw in item.get("condition_ids", [])),
                source=str(item.get("source") or ""),
                physical_state=str(item.get("physical_state") or ""),
                safe_release=bool(item.get("safe_release", False)),
                explicit_boundary=bool(item.get("explicit_boundary", False)),
            )
            for item in value.get("intervals", [])
            if isinstance(item, Mapping)
        )
        acquire_sets = tuple(
            AcquireSet(
                acquire_set_id=str(item["acquire_set_id"]),
                node_uuid=str(item["node_uuid"]),
                resource_ids=tuple(str(raw) for raw in item.get("resource_ids", [])),
                preheld_resource_ids=tuple(str(raw) for raw in item.get("preheld_resource_ids", [])),
                atomic=bool(item.get("atomic", True)),
                branch_id=str(item.get("branch_id") or ""),
                source=str(item.get("source") or ""),
            )
            for item in value.get("acquire_sets", [])
            if isinstance(item, Mapping)
        )
        relations = tuple(
            ResourceRelation(
                relation_id=str(item["relation_id"]),
                from_resource_id=str(item["from_resource_id"]),
                to_resource_id=str(item["to_resource_id"]),
                source_interval_id=str(item.get("source_interval_id") or ""),
                source_node_uuid=str(item.get("source_node_uuid") or ""),
                branch_id=str(item.get("branch_id") or ""),
                possible_concurrency=bool(item.get("possible_concurrency", True)),
                reason=str(item.get("reason") or "hold_then_acquire"),
            )
            for item in value.get("relations", [])
            if isinstance(item, Mapping)
        )
        plan = ResourcePlan(
            plan_id=str(value["plan_id"]),
            version=int(value.get("version", RESOURCE_PLAN_VERSION)),
            binding_state=str(value.get("binding_state") or "template"),
            capabilities=tuple(str(raw) for raw in value.get("capabilities", [])),
            resources=resources,
            scopes=scopes,
            intervals=intervals,
            acquire_sets=acquire_sets,
            relations=relations,
            diagnostics=tuple(
                dict(item) for item in value.get("diagnostics", []) if isinstance(item, Mapping)
            ),
            metadata=dict(value.get("metadata") or {}),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ResourcePlanError("invalid_plan", "持久化资源计划字段无效") from error
    validate_resource_plan(plan)
    return plan


def resource_plan_for_node(plan: ResourcePlan, node_uuid: str) -> dict[str, Any]:
    """取得一个节点的区间和新增集合只读投影。"""

    validate_resource_plan(plan)
    node_uuid = str(node_uuid)
    return {
        "plan_id": plan.plan_id,
        "node_uuid": node_uuid,
        "intervals": [
            _serialize_dataclass(item)
            for item in plan.intervals
            if node_uuid in item.node_uuids
        ],
        "acquire_sets": [
            _serialize_dataclass(item)
            for item in plan.acquire_sets
            if item.node_uuid == node_uuid
        ],
    }


def _serialize_dataclass(value: Any) -> dict[str, Any]:
    raw = asdict(value)
    return _sort_json(raw)


def _sort_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sort_json(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, tuple):
        return [_sort_json(item) for item in value]
    if isinstance(value, list):
        return [_sort_json(item) for item in value]
    return value


def _string_sequence(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ResourcePlanError("invalid_resource_list", "资源声明必须是字符串数组", path=path)
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ResourcePlanError("invalid_resource_alias", "资源别名必须是非空字符串", path=f"{path}/{index}")
        text = item.strip()
        if text in result:
            raise ResourcePlanError("duplicate_resource_alias", f"资源别名重复：{text}", path=path)
        result.append(text)
    return tuple(result)


def _normalize_edges(value: Any, nodes: Mapping[str, Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ResourcePlanError("invalid_edges", "资源计划 edges 必须是数组", path="/edges")
    result: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ResourcePlanError("invalid_edge", "资源计划边必须是对象", path=f"/edges/{index}")
        source = str(raw.get("source_node_uuid") or raw.get("source_node_id") or "")
        target = str(raw.get("target_node_uuid") or raw.get("target_node_id") or "")
        if source not in nodes or target not in nodes:
            raise ResourcePlanError("unknown_edge_node", "资源计划边引用未知节点", path=f"/edges/{index}")
        if source == target:
            raise ResourcePlanError("graph_cycle", "资源计划节点不能自依赖", path=f"/edges/{index}")
        result.add((source, target))
    return tuple(sorted(result))


def _topological_order(nodes: Mapping[str, Mapping[str, Any]], edges: Sequence[tuple[str, str]]) -> list[str]:
    incoming = {node_uuid: 0 for node_uuid in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        incoming[target] += 1
        outgoing[source].append(target)
    queue = deque(sorted(node_uuid for node_uuid, count in incoming.items() if count == 0))
    order: list[str] = []
    while queue:
        node_uuid = queue.popleft()
        order.append(node_uuid)
        for target in sorted(outgoing[node_uuid]):
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    if len(order) != len(nodes):
        raise ResourcePlanError("graph_cycle", "工作流依赖图本身成环")
    return order


def _node_resource_aliases(node: Mapping[str, Any]) -> tuple[str, ...]:
    raw: Any = node.get("resource_defaults")
    if raw is None:
        raw = node.get("resources")
    metadata = node.get("meta_data")
    unilab = metadata.get("unilab") if isinstance(metadata, Mapping) else None
    if raw is None and isinstance(unilab, Mapping):
        raw = unilab.get("resource_defaults")
    if raw is None:
        contract = node.get("action_resource_contract")
        if isinstance(contract, Mapping):
            raw = contract.get("resource_aliases")
            if raw is None:
                resource_params = contract.get("resource_params")
                if isinstance(resource_params, Sequence) and not isinstance(
                    resource_params, (str, bytes)
                ):
                    raw = [
                        item.get("param")
                        for item in resource_params
                        if isinstance(item, Mapping)
                    ]
    if raw is None:
        return ()
    return _string_sequence(raw, f"/nodes/{node.get('uuid')}/resources")


def _branch_id(node: Mapping[str, Any]) -> str:
    explicit = node.get("branch_id")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    metadata = node.get("meta_data")
    unilab = metadata.get("unilab") if isinstance(metadata, Mapping) else None
    if isinstance(unilab, Mapping):
        scope = unilab.get("parallel_scope")
        order = unilab.get("parallel_order")
        if scope is not None and order is not None:
            return f"parallel:{scope}:{order}"
    return "main"


def _scope_node_members(
    raw_scope: Mapping[str, Any],
    order: Sequence[str],
    nodes: Mapping[str, Mapping[str, Any]],
    edges: Sequence[tuple[str, str]],
) -> list[str]:
    explicit = list(raw_scope.get("node_uuids") or ())
    if explicit:
        return [node_uuid for node_uuid in order if node_uuid in explicit]
    entry = str(raw_scope.get("entry_node_uuid") or "")
    exit_node = str(raw_scope.get("exit_node_uuid") or "")
    if entry or exit_node:
        if not entry or not exit_node or entry not in nodes or exit_node not in nodes:
            raise ResourcePlanError("invalid_scope_boundary", "资源作用域入口/出口节点无效")
        start = order.index(entry)
        end = order.index(exit_node)
        if start > end:
            raise ResourcePlanError("invalid_scope_boundary", "资源作用域入口不能位于出口之后")
        return list(order[start : end + 1])
    # 根作用域没有显式边界时覆盖所有启用节点；with 作用域必须给边界或成员，
    # 否则扩大锁周期不可见。
    if raw_scope.get("kind") == "root":
        return list(order)
    raise ResourcePlanError("missing_scope_boundary", "词法资源作用域必须声明 node_uuids 或入口/出口")


def _can_continue_interval(
    previous: str,
    current: str,
    branch_by_node: Mapping[str, str],
    edges: Sequence[tuple[str, str]],
    scopes: Sequence[ResourceScope],
    resource_id: str,
) -> bool:
    if branch_by_node[previous] != branch_by_node[current]:
        return False
    if (previous, current) not in set(edges):
        return False
    # 显式 scope 仍可跨普通后继连续持有；不同硬边界之间不得静默合并。
    previous_scopes = {scope.scope_id for scope in scopes if previous in scope.node_uuids and resource_id in scope.resource_ids}
    current_scopes = {scope.scope_id for scope in scopes if current in scope.node_uuids and resource_id in scope.resource_ids}
    if not previous_scopes or not current_scopes:
        return not previous_scopes and not current_scopes
    # 进入/离开同一路径的嵌套作用域只改变声明记录，不改变实际所有权；
    # 只有从一个非嵌套硬边界切换到另一个边界时才必须断开区间。
    if previous_scopes.issubset(current_scopes) or current_scopes.issubset(
        previous_scopes
    ):
        return True
    return False
    return True


def _common_scope_id(chain: Sequence[str], scopes: Sequence[ResourceScope], resource_id: str) -> str | None:
    candidates = [
        scope
        for scope in scopes
        if resource_id in scope.resource_ids
        and all(node_uuid in scope.node_uuids for node_uuid in chain)
    ]
    if not candidates:
        return None
    scope_by_id = {scope.scope_id: scope for scope in scopes}

    def depth(scope: ResourceScope) -> int:
        current = scope
        visited: set[str] = set()
        result = 0
        while current.parent_scope_id and current.parent_scope_id not in visited:
            visited.add(current.scope_id)
            parent = scope_by_id.get(current.parent_scope_id)
            if parent is None:
                break
            result += 1
            current = parent
        return result

    return max(candidates, key=lambda scope: (depth(scope), scope.scope_id)).scope_id


def _workflow_instance_id(graph: Mapping[str, Any]) -> str:
    for key in ("workflow_instance_id", "workflow_uuid", "uuid"):
        value = graph.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    workflow = graph.get("workflow")
    if isinstance(workflow, Mapping):
        value = workflow.get("uuid")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "workflow:anonymous"


def _merge_concurrent_relations(
    plan: ResourcePlan,
    concurrency: Sequence[ResourcePlan | Mapping[str, Any]],
) -> ResourcePlan:
    resources_by_key = {resource.canonical_key: resource.resource_id for resource in plan.resources}
    relations = list(plan.relations)
    seen = {(item.from_resource_id, item.to_resource_id) for item in relations}
    for index, candidate in enumerate(concurrency):
        if isinstance(candidate, ResourcePlan):
            candidate_plan = candidate
        elif isinstance(candidate, Mapping):
            candidate_plan = deserialize_resource_plan(candidate)
        else:
            raise ResourcePlanError("invalid_concurrency", f"并发计划 {index} 必须是 ResourcePlan")
        key_to_id = {resource.canonical_key: resource.resource_id for resource in candidate_plan.resources}
        for relation in candidate_plan.relations:
            from_key = next((key for key, value in key_to_id.items() if value == relation.from_resource_id), None)
            to_key = next((key for key, value in key_to_id.items() if value == relation.to_resource_id), None)
            if from_key not in resources_by_key or to_key not in resources_by_key:
                continue
            edge = (resources_by_key[from_key], resources_by_key[to_key])
            if edge in seen:
                continue
            seen.add(edge)
            relations.append(
                ResourceRelation(
                    relation_id=str(uuid5(_PLAN_NAMESPACE, f"concurrent:{edge[0]}:{edge[1]}")),
                    from_resource_id=edge[0],
                    to_resource_id=edge[1],
                    source_interval_id=relation.source_interval_id,
                    source_node_uuid=relation.source_node_uuid,
                    branch_id=relation.branch_id,
                    possible_concurrency=True,
                    reason="possible_concurrent_workflow",
                )
            )
    return ResourcePlan(
        plan_id=plan.plan_id,
        version=plan.version,
        binding_state=plan.binding_state,
        capabilities=plan.capabilities,
        resources=plan.resources,
        scopes=plan.scopes,
        intervals=plan.intervals,
        acquire_sets=plan.acquire_sets,
        relations=tuple(sorted(relations, key=lambda item: item.relation_id)),
        diagnostics=plan.diagnostics,
        metadata=plan.metadata,
    )


def _find_cycle(adjacency: Mapping[str, Sequence[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visiting:
            try:
                return path[path.index(node) :] + [node]
            except ValueError:
                return [node, node]
        if node in visited:
            return []
        visiting.add(node)
        path.append(node)
        for target in sorted(adjacency.get(node, ())):
            cycle = visit(target)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(set(adjacency) | {target for values in adjacency.values() for target in values}):
        cycle = visit(node)
        if cycle:
            return cycle
    return []


__all__ = [
    "AcquireSet",
    "CanonicalResource",
    "RESOURCE_PLAN_CAPABILITY",
    "RESOURCE_PLAN_VERSION",
    "ResourceInterval",
    "ResourcePlan",
    "ResourcePlanError",
    "ResourceRelation",
    "ResourceScope",
    "STATIC_RESOURCE_DAG_CAPABILITY",
    "bind_station_resource_plan",
    "compile_template_resource_plan",
    "deserialize_resource_plan",
    "resource_plan_for_node",
    "serialize_resource_plan",
    "validate_resource_plan",
]
