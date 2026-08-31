# Edge Resource and Workflow Context

This context defines the domain language shared by the frontend, Backend, and
Edge microbackend. Backend is the canonical public contract; Edge provides a
local adapter to the same concepts.

## Language

**Shared Interface**:
The public resource and workflow contract presented identically by Backend and
Edge, including route meaning, field spelling, enums, envelopes, and errors.
_Avoid_: Similar API, Edge-shaped response, frontend fallback

**Authority**:
The single selected owner allowed to accept changes for an aggregate during a
given operation; replicas and projections do not become additional owners.
_Avoid_: Last writer wins, dual truth, nearest database

**Backend Authority**:
The configured laboratory service that owns shared domain state. It may be a
central Docker deployment and remains external to Workspace Host lifecycle;
selecting it never implies that Workbench starts, stops, or restarts it.
_Avoid_: Managed Local process, workspace child, embedded Backend

**Material UUID**:
The sole stable identity of a Material, referred to as `material_uuid` by
relationships and workflow fields.
_Avoid_: Edge UUID, cloud UUID, instance UUID

**Material Composition**:
The structural parent-child relationship expressed by a Material's
`parent_uuid`.
_Avoid_: Site occupancy, laboratory layout, scheduler claim

**Site**:
A stable named position owned by one Material; `Site.uuid` identifies the
position itself, never its owner or occupant.
_Avoid_: Material UUID, slot name, PLR index

**Site Occupancy**:
The optional relationship from a Site to the Material currently placed there,
expressed by `occupied_material_uuid`.
_Avoid_: Material Composition, Site identity, execution lock

**Soft Deletion**:
The lifecycle state in which an aggregate retains its UUID and history while
normal reads exclude it after `deleted_at` becomes non-null.
_Avoid_: Physical row deletion, empty status, hidden alias

**Workflow**:
A reusable graph definition owned by the selected Authority. Backend Authority
may persist it; Local Authority rebuilds it into a process-local catalog from an
authorized domain-package Python source. JSON and Python imports are normalized
to that Python source before they become Local definitions.
_Avoid_: One execution, runtime snapshot, Run

**实验操作（ExperimentOperation）**:
中文定义：具有明确实验语义、可分类，并可在发布后供其他工作流复用的一类工作流定义。
中文避免：子工作流、可复用节点。
English: A classified Workflow definition with explicit experimental meaning
that other Workflows may reuse after publication.
_Avoid_: Subworkflow, reusable node

**Workspace Workflow Source**:
Authorized domain-package Python source that is AST-scanned, compiled, and
validated into the Local Workflow Catalog on every OS start; it has no implicit
write relationship to a Backend-owned Workflow.
_Avoid_: Backend definition, remote replica, implicit publication

**Local Workflow Catalog**:
The process-local, non-durable set of validated Workflow definitions used by
Local Authority. Domain-package workflows, including JSON/Python imports, are
rebuilt at startup; API edits to an imported Workflow are written back to the
same registered Python source before the in-memory revision advances.
_Avoid_: SQLite definition authority, temporary upload, Backend replica

**Local Workflow Publication Catalog**:
The domain-package `workflow_publications.json` file that stores immutable
published Workflow contracts and their source hashes. Local Authority restores
these contracts into the process-local catalog after Python sources are
activated, so published status and ExperimentOperation reuse survive restart.
It is definition-side file authority, not runtime SQLite state.
_Avoid_: SQLite publication authority, derived status without restart recovery

**组合工作流调用（CompositeWorkflowInvocation）**:
中文定义：父工作流中静态展开一个已发布实验操作的稳定调用节点。实验操作产生兼容的新应用
定义或发布合同时，本地权威（Local Authority）自动刷新没有待应用编辑的父工作流，
并保持调用 UUID、布局、参数值和外部连线；不兼容时保留父工作流上一个有效定义并
返回诊断，已经创建的工作流任务（WorkflowTask）始终不变。
中文避免：运行时动态读取实验操作、要求前端点击重新编译、静默重映射参数。
English: A stable parent node that statically expands one published child and is
automatically refreshed only when the new child contract is compatible.
_Avoid_: Runtime child lookup, frontend recompile button, silent parameter remap

**Workflow Task**:
One durable execution created from a frozen Workflow graph. Its snapshot,
execution plan, jobs, results, reservations, and recovery facts survive Local
Workflow Catalog replacement or process restart.
_Avoid_: Workflow definition, Run alias

**Edge-only Inventory Interface**:
The operational lot, reservation, ledger, and diagnostic contract used inside
the local debug control plane; it is not started by the production Backend
control plane and is not part of the Shared Interface.
_Avoid_: Frontend fallback, Backend-compatible route

**Authoring Device Catalog**:
The Local Backend-only diagnostic projection that joins Driver registry and ROS
facts for installation, activation, and package acceptance. Normal product UI
uses the Shared Interface `DeviceOverview` plus `WorkflowNodeTemplate` instead.
_Avoid_: Public device fallback, Backend device list, production read model

**Control Plane**:
The single selected owner of workflow scheduling and material facts. `local`
starts the embedded debug Scheduler and Inventory modules; `backend` uses the
production HTTP/WebSocket Edge protocol and does not start those modules.
_Avoid_: Environment name, deployment label, simultaneous local and remote authority
