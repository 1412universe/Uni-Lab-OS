"""Backend 形状的进程内工作流定义与 SQLite 运行事实存储。"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Tuple,
)
from uuid import uuid4

from unilabos.workflow import source_bootstrap
from unilabos.workflow.authoring_candidate_hash import (
    AuthoringCandidateHashError,
    compute_authoring_candidate_hash,
)
from unilabos.workflow.authoring_kernel import (
    AuthoringCatalogError,
    AuthoringCatalogSnapshot,
)
from unilabos.workflow.graph_validation import (
    CodedGraphValidationError,
    GraphValidationError,
    MissingTemplateError,
    validate_graph,
)
from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.models import (
    WorkflowEdgeWrite,
    WorkflowInventoryRequirementWrite,
    WorkflowNodeWrite,
)
from unilabos.workflow.store_migrations import (
    ensure_device_action_run_schema,
    ensure_ephemeral_workflow_reference_schema,
    ensure_execution_lock_schema,
    ensure_local_cancellation_schema,
    ensure_task_material_admission_schema,
    ensure_workflow_inventory_schema,
)

if TYPE_CHECKING:
    from unilabos.workflow.task_input import PreparedTaskInput

_STORE_INITIALIZATION_BUSY_TIMEOUT_SECONDS = 5.0
_STORE_INITIALIZATION_SQLITE_BUSY_TIMEOUT_MS = 100
_STORE_INITIALIZATION_RETRY_INTERVAL_SECONDS = 0.01
_STORE_SQLITE_BUSY_TIMEOUT_MS = 5000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return encode_json(value, sort_keys=True).decode("utf-8")


def _load(value: Optional[str], fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    return decode_json_bytes(value.encode("utf-8"))


class StoreNotFound(LookupError):
    pass


class StoreConflict(RuntimeError):
    pass


class StoreRevisionConflict(StoreConflict):
    pass


class StoreAuthoringConflict(StoreConflict):
    """Apply 事务提交前发生了 Authoring 前置条件冲突。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TemplateSnapshotProvider(Protocol):
    """提供最近一次完整设备与动作内存目录的窄接口。"""

    def snapshot(self) -> AuthoringCatalogSnapshot:
        """返回一个不可变且可供整次 Store 操作复用的目录快照。"""


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workflow (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    name TEXT NOT NULL,
    tags TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workflow_node_template (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    resource_template_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    class TEXT,
    goal TEXT NOT NULL,
    goal_default TEXT NOT NULL,
    feedback TEXT NOT NULL,
    result TEXT NOT NULL,
    schema TEXT,
    type TEXT NOT NULL,
    icon TEXT,
    header TEXT,
    footer TEXT,
    node_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_workflow_node_template_authority
    ON workflow_node_template(authority_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_node_template_active_business_key
    ON workflow_node_template(resource_template_uuid, name)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS workflow_handle_template (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    workflow_node_template_uuid TEXT NOT NULL,
    handle_key TEXT NOT NULL,
    io_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    type TEXT NOT NULL,
    required INTEGER NOT NULL,
    data_source TEXT,
    data_key TEXT
);
CREATE INDEX IF NOT EXISTS ix_workflow_handle_template_node
    ON workflow_handle_template(workflow_node_template_uuid);
CREATE INDEX IF NOT EXISTS ix_workflow_handle_template_authority
    ON workflow_handle_template(authority_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_handle_template_active_business_key
    ON workflow_handle_template(
        workflow_node_template_uuid,
        handle_key,
        io_type
    )
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS workflow_node (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_uuid TEXT NOT NULL,
    workflow_node_template_uuid TEXT,
    parent_uuid TEXT,
    material_uuid TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    type TEXT NOT NULL,
    icon TEXT,
    pose TEXT NOT NULL,
    param TEXT NOT NULL,
    footer TEXT,
    action_name TEXT,
    action_type TEXT,
    execution_policy TEXT NOT NULL,
    disabled INTEGER NOT NULL,
    minimized INTEGER NOT NULL,
    script TEXT,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_node_workflow
    ON workflow_node(workflow_uuid);

CREATE TABLE IF NOT EXISTS workflow_edge (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_uuid TEXT NOT NULL,
    source_node_uuid TEXT NOT NULL,
    target_node_uuid TEXT NOT NULL,
    source_handle_uuid TEXT NOT NULL,
    target_handle_uuid TEXT NOT NULL,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_edge_workflow
    ON workflow_edge(workflow_uuid);

CREATE TABLE IF NOT EXISTS workflow_task (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_uuid TEXT NOT NULL,
    status TEXT NOT NULL,
    workflow_snapshot TEXT NOT NULL,
    execution_plan TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    target_node_uuid TEXT,
    control_status TEXT NOT NULL,
    cleanup_status TEXT NOT NULL,
    trace_context TEXT NOT NULL,
    input TEXT NOT NULL,
    output TEXT NOT NULL,
    error_info TEXT NOT NULL,
    timeout_at TEXT,
    attention_reason TEXT,
    terminal_ghost_detected_at TEXT,
    reconciliation_resume_control_status TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_task_workflow
    ON workflow_task(workflow_uuid);
CREATE INDEX IF NOT EXISTS ix_workflow_task_status
    ON workflow_task(status);

CREATE TABLE IF NOT EXISTS workflow_task_command (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_task_uuid TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('step', 'pause', 'resume', 'cancel')),
    target_node_uuid TEXT,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'rejected')),
    result TEXT NOT NULL,
    trace_context TEXT NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_command_idempotency_active
    ON workflow_task_command(workflow_task_uuid, idempotency_key)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_workflow_task_command_pending
    ON workflow_task_command(workflow_task_uuid, create_time, uuid)
    WHERE deleted_at IS NULL AND status = 'pending';

CREATE TABLE IF NOT EXISTS workflow_task_debug_configuration (
    workflow_task_uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    start_node_uuids TEXT NOT NULL,
    breakpoint_node_uuids TEXT NOT NULL,
    execution_policy TEXT NOT NULL CHECK (execution_policy IN ('step', 'continue')),
    status TEXT NOT NULL CHECK (status IN ('paused', 'running', 'completed', 'stopped')),
    FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workflow_node_admission_hold (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    workflow_task_uuid TEXT NOT NULL,
    workflow_node_job_uuid TEXT NOT NULL,
    workflow_node_uuid TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('start', 'breakpoint', 'step')),
    status TEXT NOT NULL CHECK (status IN ('open', 'released', 'canceled')),
    released_at TEXT,
    FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid) ON DELETE CASCADE,
    FOREIGN KEY(workflow_node_job_uuid) REFERENCES workflow_node_job(uuid) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_node_admission_hold_open
    ON workflow_node_admission_hold(workflow_task_uuid)
    WHERE status = 'open';

CREATE TABLE IF NOT EXISTS workflow_task_debug_command (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    workflow_task_uuid TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('step', 'continue')),
    hold_uuid TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'rejected')),
    result TEXT NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid) ON DELETE CASCADE,
    FOREIGN KEY(hold_uuid) REFERENCES workflow_node_admission_hold(uuid)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_debug_command_idempotency
    ON workflow_task_debug_command(workflow_task_uuid, idempotency_key);

CREATE TABLE IF NOT EXISTS workflow_node_job (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_task_uuid TEXT NOT NULL,
    workflow_node_uuid TEXT NOT NULL,
    material_uuid TEXT,
    edge_agent_uuid TEXT,
    edge_command_uuid TEXT,
    job_access_token_hash TEXT NOT NULL DEFAULT '',
    feedback_sequence INTEGER NOT NULL,
    topological_index INTEGER NOT NULL,
    executor_kind TEXT NOT NULL,
    execution_policy TEXT NOT NULL,
    execution_timeout_seconds INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    param TEXT NOT NULL,
    feedback_data TEXT NOT NULL,
    return_info TEXT NOT NULL,
    control_data TEXT NOT NULL,
    error_info TEXT NOT NULL,
    dispatch_deadline_at TEXT,
    execution_deadline_at TEXT,
    cancel_command_uuid TEXT,
    cancel_ack_deadline_at TEXT,
    cancel_complete_deadline_at TEXT,
    cancel_accepted_at TEXT,
    uncertainty_reason TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_node_job_task
    ON workflow_node_job(workflow_task_uuid);
CREATE INDEX IF NOT EXISTS ix_workflow_node_job_node
    ON workflow_node_job(workflow_node_uuid);

CREATE TABLE IF NOT EXISTS workflow_source_registration (
    workflow_uuid TEXT PRIMARY KEY,
    package_id TEXT NOT NULL,
    package_root TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_source_registration_path
    ON workflow_source_registration(package_root, relative_path);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_source_registration_uri
    ON workflow_source_registration(source_uri);

CREATE TABLE IF NOT EXISTS workflow_authoring (
    workflow_uuid TEXT PRIMARY KEY,
    observed_draft_hash TEXT,
    draft_update_time TEXT,
    diagnostics TEXT NOT NULL,
    candidate_hash TEXT,
    candidate TEXT,
    applied_source TEXT,
    writeback_status TEXT NOT NULL DEFAULT 'settled',
    writeback_source TEXT,
    writeback_expected_hash TEXT,
    writeback_generation TEXT,
    update_time TEXT NOT NULL,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);

CREATE TABLE IF NOT EXISTS frontend_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    data TEXT NOT NULL,
    create_time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runtime_journal (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_task_uuid TEXT NOT NULL,
    workflow_node_job_uuid TEXT,
    workflow_task_command_uuid TEXT,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'task_transition',
            'job_transition',
            'command_consumed',
            'feedback_committed',
            'uncertainty_opened',
            'uncertainty_resolved',
            'startup_recovered'
        )
    ),
    from_status TEXT,
    to_status TEXT,
    data TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(data) AND json_type(data) = 'object'),
    create_time TEXT NOT NULL,
    FOREIGN KEY(workflow_task_uuid)
        REFERENCES workflow_task(uuid) ON DELETE CASCADE,
    FOREIGN KEY(workflow_node_job_uuid)
        REFERENCES workflow_node_job(uuid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_workflow_runtime_journal_task_sequence
    ON workflow_runtime_journal(workflow_task_uuid, sequence);
CREATE INDEX IF NOT EXISTS ix_workflow_runtime_journal_job_sequence
    ON workflow_runtime_journal(workflow_node_job_uuid, sequence);
"""


class WorkflowStore:
    """由单一连接持有的工作流定义目录或持久运行事实库。

    定义角色使用 ``:memory:``，运行角色使用文件 SQLite；两者复用相同事务与
    投影代码但不形成双权威。Store 方法用进程内可重入锁串行化事务，Workflow
    专属编排锁由 ``WorkflowService`` 持有。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        template_snapshot_provider: TemplateSnapshotProvider | None = None,
        persist_workflow_definitions: bool = True,
    ) -> None:
        """建立工作流持久事实存储并绑定可选内存模板目录。

        参数：``db_path`` 是工作流 SQLite 路径；提供
        ``template_snapshot_provider`` 时，所有模板读取只使用其不可变快照且不
        回退模板表；省略时保留隔离测试和遗留调用的 SQLite 模板适配器。
        ``persist_workflow_definitions=False`` 表示该文件连接只持有 Task/Job 等
        运行事实，并解除 Task 对可消失工作流定义的外键依赖。
        """

        initialization_deadline = (
            monotonic() + _STORE_INITIALIZATION_BUSY_TIMEOUT_SECONDS
        )
        self.path = str(db_path)
        self._template_snapshot_provider = template_snapshot_provider
        self._persist_workflow_definitions = persist_workflow_definitions
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            # SQLite 有界 busy 重试统一覆盖线程与进程间的 WAL/schema 竞争，
            # 不用进程全局锁阻塞无关数据库。
            with self._lock:
                initialization_busy_timeout_ms = (
                    _STORE_INITIALIZATION_SQLITE_BUSY_TIMEOUT_MS
                )
                self._conn.execute(
                    f"PRAGMA busy_timeout = {initialization_busy_timeout_ms}"
                )
                self._retry_initialization(
                    lambda: self._conn.execute("PRAGMA journal_mode = WAL"),
                    deadline=initialization_deadline,
                )
                self._retry_initialization(
                    lambda: self._conn.execute("PRAGMA synchronous = NORMAL"),
                    deadline=initialization_deadline,
                )
                self._retry_initialization(
                    lambda: self._conn.executescript(_SCHEMA),
                    deadline=initialization_deadline,
                )
                self._retry_initialization(
                    lambda: self._conn.execute("BEGIN IMMEDIATE"),
                    deadline=initialization_deadline,
                )
                try:
                    ensure_device_action_run_schema(self._conn)
                    if not self._persist_workflow_definitions:
                        ensure_ephemeral_workflow_reference_schema(self._conn)
                    ensure_task_material_admission_schema(self._conn)
                    ensure_execution_lock_schema(self._conn)
                    ensure_local_cancellation_schema(self._conn)
                    ensure_workflow_inventory_schema(self._conn)
                    if self._persist_workflow_definitions:
                        columns = {
                            row["name"]
                            for row in self._conn.execute(
                                "PRAGMA table_info(workflow_authoring)"
                            ).fetchall()
                        }
                        if "writeback_generation" not in columns:
                            self._conn.execute(
                                """
                                ALTER TABLE workflow_authoring
                                ADD COLUMN writeback_generation TEXT
                                """
                            )
                        legacy_markers = self._conn.execute(
                            """
                            SELECT workflow_uuid
                            FROM workflow_authoring
                            WHERE writeback_status = 'pending'
                              AND writeback_source IS NOT NULL
                              AND writeback_expected_hash IS NOT NULL
                              AND writeback_generation IS NULL
                            """,
                        ).fetchall()
                        for marker in legacy_markers:
                            self._conn.execute(
                                """
                                UPDATE workflow_authoring
                                SET writeback_generation = ?
                                WHERE workflow_uuid = ?
                                  AND writeback_status = 'pending'
                                  AND writeback_source IS NOT NULL
                                  AND writeback_expected_hash IS NOT NULL
                                  AND writeback_generation IS NULL
                                """,
                                (str(uuid4()), marker["workflow_uuid"]),
                            )
                except BaseException:
                    self._conn.rollback()
                    raise
                else:
                    self._conn.commit()
                    self._conn.execute(
                        f"PRAGMA busy_timeout = {_STORE_SQLITE_BUSY_TIMEOUT_MS}"
                    )
        except BaseException:
            self._conn.close()
            raise

    def _retry_initialization(
        self,
        operation: Callable[[], object],
        *,
        deadline: float,
    ) -> None:
        while True:
            try:
                operation()
                return
            except sqlite3.OperationalError as error:
                error_code = getattr(error, "sqlite_errorcode", None)
                base_error_code = (
                    error_code & 0xFF if isinstance(error_code, int) else None
                )
                busy_message = str(error).lower() in {
                    "database is locked",
                    "database table is locked",
                }
                if (
                    base_error_code
                    not in {
                        sqlite3.SQLITE_BUSY,
                        sqlite3.SQLITE_LOCKED,
                    }
                    and not busy_message
                ):
                    raise
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise
                self._conn.rollback()
                sleep(
                    min(
                        _STORE_INITIALIZATION_RETRY_INTERVAL_SECONDS,
                        remaining,
                    )
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    # Workflow 与 Graph --------------------------------------------------

    def create_workflow(
        self,
        *,
        workflow_uuid: str,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = utc_now()
        try:
            with self.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO workflow(
                        uuid, create_time, update_time, deleted_at,
                        description, meta_data, name, tags, revision
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 1)
                    """,
                    (
                        workflow_uuid,
                        now,
                        now,
                        description,
                        _json(meta_data),
                        name,
                        _json(tags),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreConflict(f"workflow {workflow_uuid} already exists") from exc
        return self.get_workflow(workflow_uuid)

    def create_workflow_with_graph(
        self,
        *,
        workflow_uuid: str,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
        nodes: List[WorkflowNodeWrite],
        edges: List[WorkflowEdgeWrite],
        node_templates: List[Dict[str, Any]] | None = None,
        handle_templates: List[Dict[str, Any]] | None = None,
        template_catalog_fingerprint: str | None = None,
        trusted_authoring_graph: bool = False,
    ) -> Dict[str, Any]:
        """在一个事务中创建工作流及其首版完整图。

        参数：工作流字段构成新定义，``nodes``/``edges`` 已使用新身份重建引用；
        ``node_templates``/``handle_templates`` 是 AST 编译候选实际引用的目录子集，
        与工作流图在同一事务内校验或投影；``trusted_authoring_graph`` 只允许 AST
        编译器生成的图保留系统创作元数据，普通复制和旧版导入仍禁止提交
        ``meta_data.unilab``。
        返回修订为 1 的完整图；任何身份、模板或图语义错误都会回滚工作流主记录，
        因此复制/导入不会留下空壳工作流。
        """

        now = utc_now()
        try:
            with self.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO workflow(
                        uuid, create_time, update_time, deleted_at,
                        description, meta_data, name, tags, revision
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 1)
                    """,
                    (
                        workflow_uuid,
                        now,
                        now,
                        description,
                        _json(meta_data),
                        name,
                        _json(tags),
                    ),
                )
                if node_templates is not None or handle_templates is not None:
                    if not template_catalog_fingerprint:
                        raise StoreConflict("Candidate Catalog 缺少目录指纹")
                    self._ensure_authoring_catalog_projection(
                        conn,
                        node_templates=node_templates or [],
                        handle_templates=handle_templates or [],
                        authority_id=template_catalog_fingerprint,
                        now=now,
                    )
                self._reconcile_graph(
                    conn,
                    workflow_uuid=workflow_uuid,
                    expected_revision=1,
                    nodes=nodes,
                    edges=edges,
                    advance_revision=False,
                    protect_reserved_metadata=not trusted_authoring_graph,
                    semantic_workflow_meta_data=(
                        meta_data if trusted_authoring_graph else None
                    ),
                    validate_workflow_io_contract=True,
                )
        except sqlite3.IntegrityError as exc:
            raise StoreConflict(f"workflow {workflow_uuid} already exists") from exc
        return self.get_graph(workflow_uuid)

    def get_workflow(
        self,
        workflow_uuid: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = conn or self._conn
        with self._lock:
            row = database.execute(
                "SELECT * FROM workflow WHERE uuid = ? AND deleted_at IS NULL",
                (workflow_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"workflow {workflow_uuid} not found")
        return self._workflow_row(row)

    def list_workflows(
        self,
        *,
        page: int,
        page_size: int,
        name: str = "",
    ) -> Dict[str, Any]:
        where = "deleted_at IS NULL"
        values: List[Any] = []
        if name:
            where += " AND name LIKE ?"
            values.append(f"%{name}%")
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM workflow WHERE {where}",
                values,
            ).fetchone()[0]
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow WHERE {where}
                ORDER BY create_time DESC, uuid
                LIMIT ? OFFSET ?
                """,
                (*values, page_size, offset),
            ).fetchall()
        return {
            "items": [self._workflow_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def update_workflow(
        self,
        workflow_uuid: str,
        *,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self.transaction() as conn:
            self.get_workflow(workflow_uuid, conn=conn)
            conn.execute(
                """
                UPDATE workflow
                SET name = ?, tags = ?, description = ?, meta_data = ?,
                    update_time = ?
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (
                    name,
                    _json(tags),
                    description,
                    _json(meta_data),
                    utc_now(),
                    workflow_uuid,
                ),
            )
        return self.get_workflow(workflow_uuid)

    def delete_workflow(self, workflow_uuid: str) -> None:
        now = utc_now()
        with self.transaction() as conn:
            self.get_workflow(workflow_uuid, conn=conn)
            conn.execute(
                "UPDATE workflow SET deleted_at = ?, update_time = ? WHERE uuid = ?",
                (now, now, workflow_uuid),
            )
            conn.execute(
                "UPDATE workflow_node SET deleted_at = ?, update_time = ? "
                "WHERE workflow_uuid = ? AND deleted_at IS NULL",
                (now, now, workflow_uuid),
            )
            conn.execute(
                "UPDATE workflow_edge SET deleted_at = ?, update_time = ? "
                "WHERE workflow_uuid = ? AND deleted_at IS NULL",
                (now, now, workflow_uuid),
            )

    def get_graph(
        self,
        workflow_uuid: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = conn or self._conn
        workflow = self.get_workflow(workflow_uuid, conn=database)
        with self._lock:
            node_rows = database.execute(
                """
                SELECT * FROM workflow_node
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                ORDER BY create_time, uuid
                """,
                (workflow_uuid,),
            ).fetchall()
            edge_rows = database.execute(
                """
                SELECT * FROM workflow_edge
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                ORDER BY create_time, uuid
                """,
                (workflow_uuid,),
            ).fetchall()
            inventory_requirement_rows = database.execute(
                """
                SELECT * FROM workflow_inventory_requirement
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                ORDER BY sort_order, uuid
                """,
                (workflow_uuid,),
            ).fetchall()
            template_uuids = [
                row["workflow_node_template_uuid"]
                for row in node_rows
                if row["workflow_node_template_uuid"]
            ]
            node_templates: List[Dict[str, Any]] = []
            handle_templates: List[Dict[str, Any]] = []
            catalog_snapshot = (
                self._template_snapshot_provider.snapshot()
                if template_uuids and self._template_snapshot_provider is not None
                else None
            )
            if template_uuids:
                if catalog_snapshot is not None:
                    node_templates, handle_templates = self._catalog_entities(
                        template_uuids,
                        snapshot=catalog_snapshot,
                    )
                else:
                    marks = ",".join("?" for _ in template_uuids)
                    template_rows = database.execute(
                        f"""
                        SELECT * FROM workflow_node_template
                        WHERE uuid IN ({marks}) AND deleted_at IS NULL
                        ORDER BY create_time, uuid
                        """,
                        template_uuids,
                    ).fetchall()
                    handle_rows = database.execute(
                        f"""
                        SELECT * FROM workflow_handle_template
                        WHERE workflow_node_template_uuid IN ({marks})
                          AND deleted_at IS NULL
                        ORDER BY create_time, uuid
                        """,
                        template_uuids,
                    ).fetchall()
                    node_templates = [
                        self._node_template_row(row) for row in template_rows
                    ]
                    handle_templates = [
                        self._handle_template_row(row) for row in handle_rows
                    ]
        return {
            "workflow": workflow,
            "nodes": [
                self._public_node_row(row, snapshot=catalog_snapshot)
                for row in node_rows
            ],
            "edges": [self._edge_row(row) for row in edge_rows],
            "inventory_requirements": [
                self._inventory_requirement_row(row)
                for row in inventory_requirement_rows
            ],
            "node_templates": node_templates,
            "handle_templates": handle_templates,
        }

    def _catalog_entities(
        self,
        template_uuids: Iterable[str],
        *,
        snapshot: AuthoringCatalogSnapshot | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """从一个不可变目录快照分离指定模板及全部 Handle。

        参数：``template_uuids`` 是工作流图实际引用的模板身份；调用方可传入已经
        冻结的 ``snapshot`` 保证一次事务不混用代际。返回：按 UUID 排序的节点和
        Handle 普通字典；目录未装配或任一身份缺失时关闭式失败。
        """

        provider = self._template_snapshot_provider
        if provider is None:
            raise RuntimeError("工作流存储没有装配内存模板目录")
        current_snapshot = snapshot or provider.snapshot()
        actions = []
        for template_reference in sorted(set(template_uuids)):
            try:
                actions.append(current_snapshot.require_template(template_reference))
            except AuthoringCatalogError:
                try:
                    actions.append(
                        current_snapshot.require_template_key(template_reference)
                    )
                except AuthoringCatalogError as error:
                    raise StoreNotFound(
                        f"workflow node template {template_reference} not found"
                    ) from error
        node_templates = [action.detached_template() for action in actions]
        handle_templates = [
            handle
            for action in actions
            for handle in action.detached_handles()
        ]
        node_templates.sort(key=lambda item: str(item["uuid"]))
        handle_templates.sort(key=lambda item: str(item["uuid"]))
        return node_templates, handle_templates

    def _public_node_row(
        self,
        row: sqlite3.Row,
        *,
        snapshot: AuthoringCatalogSnapshot | None = None,
    ) -> Dict[str, Any]:
        """把内部名称模板引用恢复为既有外部 UUID 字段。

        参数：``row`` 是工作流节点持久行。返回：原 Backend-shaped 节点投影；
        SQLite 遗留适配器保持原值，内存目录模式严格解析名称键。
        """

        result = self._node_row(row)
        reference = result.get("workflow_node_template_uuid")
        if reference is None or self._template_snapshot_provider is None:
            return result
        current_snapshot = snapshot or self._template_snapshot_provider.snapshot()
        try:
            # 启动迁移前创建的节点可能已经保存外部 UUID；仍按同一目录验证。
            external_uuid = str(
                current_snapshot.require_template(reference).template["uuid"]
            )
        except AuthoringCatalogError:
            try:
                external_uuid = current_snapshot.template_uuid_for_key(reference)
            except AuthoringCatalogError as error:
                raise StoreNotFound(
                    f"workflow node template {reference} not found"
                ) from error
        result["workflow_node_template_uuid"] = external_uuid
        return result

    def get_published_workflow_snapshot(
        self,
        workflow_uuid: str,
    ) -> Dict[str, Any]:
        """一次冻结工作流图与应用源码发布资格事实。

        参数：``workflow_uuid`` 是活动工作流（Workflow）稳定身份。返回：同一
        SQLite 锁视图中的完整图及 ``applied_source``；尚未应用时该字段为
        ``None``。异常：工作流缺失或软删除时抛出 ``StoreNotFound``，持久 JSON
        损坏等读取错误原样传播。
        """

        with self._lock:
            graph = self.get_graph(workflow_uuid, conn=self._conn)
            row = self._conn.execute(
                "SELECT applied_source FROM workflow_authoring "
                "WHERE workflow_uuid = ?",
                (workflow_uuid,),
            ).fetchone()
            applied_source = (
                _load(row["applied_source"], None) if row is not None else None
            )
            return {**graph, "applied_source": applied_source}

    def save_graph(
        self,
        workflow_uuid: str,
        *,
        revision: int,
        nodes: List[WorkflowNodeWrite],
        edges: List[WorkflowEdgeWrite],
        inventory_requirements: Optional[
            List[WorkflowInventoryRequirementWrite]
        ] = None,
        protect_reserved_metadata: bool = False,
        validate_workflow_io_contract: bool = False,
    ) -> Dict[str, Any]:
        """事务性保存完整工作流图并返回最新投影。

        参数说明：`revision` 是乐观并发版本；`nodes/edges` 是完整替换集合；
        `protect_reserved_metadata` 保护服务端元数据；
        `validate_workflow_io_contract` 决定是否启用严格公共输入/输出合同。
        """

        with self.transaction() as conn:
            self._reconcile_graph(
                conn,
                workflow_uuid=workflow_uuid,
                expected_revision=revision,
                nodes=nodes,
                edges=edges,
                inventory_requirements=inventory_requirements,
                advance_revision=True,
                protect_reserved_metadata=protect_reserved_metadata,
                validate_workflow_io_contract=validate_workflow_io_contract,
            )
        return self.get_graph(workflow_uuid)

    def preview_graph_replacement(
        self,
        workflow_uuid: str,
        *,
        revision: int,
        nodes: List[WorkflowNodeWrite],
        edges: List[WorkflowEdgeWrite],
        inventory_requirements: Optional[
            List[WorkflowInventoryRequirementWrite]
        ] = None,
        protect_reserved_metadata: bool = False,
        validate_workflow_io_contract: bool = False,
    ) -> Dict[str, Any]:
        """在回滚事务中构造完整图替换后的精确候选投影。

        参数与 :meth:`save_graph` 相同。返回：经过同一模板、身份、连线和工作流
        输入/输出合同校验，但仍保持当前修订号的完整图。异常：与真实保存完全
        一致。无论成功或失败，本方法都回滚全部节点、连线、事件和时间字段，供
        领域源码写回链在触碰内存权威前生成并验证规范 Python。
        """

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._reconcile_graph(
                    self._conn,
                    workflow_uuid=workflow_uuid,
                    expected_revision=revision,
                    nodes=nodes,
                    edges=edges,
                    inventory_requirements=inventory_requirements,
                    advance_revision=False,
                    protect_reserved_metadata=protect_reserved_metadata,
                    validate_workflow_io_contract=validate_workflow_io_contract,
                )
                candidate = self.get_graph(workflow_uuid, conn=self._conn)
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.rollback()
        return candidate

    def _reconcile_graph(
        self,
        conn: sqlite3.Connection,
        *,
        workflow_uuid: str,
        expected_revision: int,
        nodes: List[WorkflowNodeWrite],
        edges: List[WorkflowEdgeWrite],
        inventory_requirements: Optional[
            List[WorkflowInventoryRequirementWrite]
        ] = None,
        advance_revision: bool,
        protect_reserved_metadata: bool = False,
        semantic_workflow_meta_data: Optional[Dict[str, Any]] = None,
        validate_workflow_io_contract: bool = False,
    ) -> int:
        """在现有事务中核对并写入完整工作流图。

        参数说明：``conn`` 是调用方持有的唯一 SQLite 写事务；``workflow_uuid``
        是工作流（Workflow）稳定身份；``expected_revision`` 是乐观并发预期版本；
        ``nodes`` 与 ``edges`` 是完整替换集合；``advance_revision`` 控制成功后是否
        推进修订；``protect_reserved_metadata`` 保留服务端私有元数据；
        ``semantic_workflow_meta_data`` 可替换本轮语义校验使用的工作流元数据；
        ``validate_workflow_io_contract`` 控制是否启用严格工作流输入/输出
        （Workflow I/O）合同。返回：本事务采用的最终工作流修订。异常：工作流
        不存在抛出 ``StoreNotFound``，修订不匹配抛出 ``StoreRevisionConflict``，
        创作合同冲突抛出 ``StoreAuthoringConflict``，其余身份、模板、图或元数据
        冲突抛出 ``StoreConflict``；异常由调用事务统一回滚，不留下部分写入。
        """

        workflow = self.get_workflow(workflow_uuid, conn=conn)
        if workflow["revision"] != expected_revision:
            raise StoreRevisionConflict(
                f"workflow revision {workflow['revision']} does not match "
                f"expected {expected_revision}"
            )
        node_by_uuid = {node.uuid: node for node in nodes}
        edge_by_uuid = {edge.uuid: edge for edge in edges}
        if len(node_by_uuid) != len(nodes):
            raise StoreConflict("duplicate workflow node UUID")
        if len(edge_by_uuid) != len(edges):
            raise StoreConflict("duplicate workflow edge UUID")
        for edge in edges:
            if (
                edge.source_node_uuid not in node_by_uuid
                or edge.target_node_uuid not in node_by_uuid
            ):
                raise StoreConflict(
                    f"edge {edge.uuid} references a node outside the submitted graph"
                )
        if inventory_requirements is not None:
            requirement_keys: set[str] = set()
            requirement_uuids: set[str] = set()
            for requirement in inventory_requirements:
                identity = requirement.uuid or ""
                key = requirement.requirement_key or identity
                if requirement.consume_node_uuid not in node_by_uuid:
                    raise StoreConflict(
                        "inventory requirement references a node outside the graph"
                    )
                if not key:
                    # 无身份的新需求在当前事务生成一次，返回后由前端
                    # 带回同一 UUID / requirement_key。
                    identity = str(uuid4())
                    key = identity
                elif not identity:
                    identity = str(uuid4())
                if identity in requirement_uuids or key in requirement_keys:
                    raise StoreConflict("duplicate workflow inventory requirement")
                requirement_uuids.add(identity)
                requirement_keys.add(key)
        template_uuids = sorted(
            {
                node.workflow_node_template_uuid
                for node in nodes
                if node.workflow_node_template_uuid is not None
            }
        )
        templates: Dict[str, Dict[str, Any]] = {}
        handles: Dict[str, Dict[str, Any]] = {}
        catalog_snapshot = (
            self._template_snapshot_provider.snapshot()
            if template_uuids and self._template_snapshot_provider is not None
            else None
        )
        if template_uuids:
            if catalog_snapshot is not None:
                catalog_nodes, catalog_handles = self._catalog_entities(
                    template_uuids,
                    snapshot=catalog_snapshot,
                )
                templates = {item["uuid"]: item for item in catalog_nodes}
                handles = {item["uuid"]: item for item in catalog_handles}
            else:
                marks = ",".join("?" for _ in template_uuids)
                template_rows = conn.execute(
                    f"""
                    SELECT * FROM workflow_node_template
                    WHERE uuid IN ({marks}) AND deleted_at IS NULL
                    """,
                    template_uuids,
                ).fetchall()
                templates = {
                    row["uuid"]: self._node_template_row(row)
                    for row in template_rows
                }
                handle_rows = conn.execute(
                    f"""
                    SELECT * FROM workflow_handle_template
                    WHERE workflow_node_template_uuid IN ({marks})
                      AND deleted_at IS NULL
                    """,
                    template_uuids,
                ).fetchall()
                handles = {
                    row["uuid"]: self._handle_template_row(row)
                    for row in handle_rows
                }
        effective_params = {
            node.uuid: self._graph_node_param(
                conn,
                node,
                catalog_snapshot=catalog_snapshot,
            )
            for node in nodes
        }
        effective_node_meta_data: Dict[str, Dict[str, Any]] = {}
        for node in nodes:
            existing_node = conn.execute(
                "SELECT meta_data FROM workflow_node WHERE uuid = ?",
                (node.uuid,),
            ).fetchone()
            effective_node_meta_data[node.uuid] = self._protected_metadata(
                node.meta_data,
                (existing_node["meta_data"] if existing_node is not None else None),
                enabled=protect_reserved_metadata,
            )
        try:
            validate_graph(
                nodes=nodes,
                edges=edges,
                templates=templates,
                handles=handles,
                effective_params=effective_params,
                workflow_meta_data=(
                    semantic_workflow_meta_data
                    if semantic_workflow_meta_data is not None
                    else workflow["meta_data"]
                ),
                node_meta_data=effective_node_meta_data,
                validate_workflow_io_contract=validate_workflow_io_contract,
            )
        except MissingTemplateError as exc:
            raise StoreNotFound(str(exc)) from exc
        except CodedGraphValidationError as exc:
            raise StoreAuthoringConflict(exc.code) from exc
        except GraphValidationError as exc:
            raise StoreConflict(str(exc)) from exc
        now = utc_now()
        for node in nodes:
            self._upsert_node(
                conn,
                workflow_uuid,
                node,
                now,
                protect_reserved_metadata=protect_reserved_metadata,
                effective_param=effective_params[node.uuid],
                catalog_snapshot=catalog_snapshot,
            )
        for edge in edges:
            self._upsert_edge(
                conn,
                workflow_uuid,
                edge,
                now,
                protect_reserved_metadata=protect_reserved_metadata,
            )
        if inventory_requirements is not None:
            self._reconcile_inventory_requirements(
                conn,
                workflow_uuid=workflow_uuid,
                requirements=inventory_requirements,
                now=now,
            )
        self._soft_delete_omitted(
            conn,
            table="workflow_edge",
            workflow_uuid=workflow_uuid,
            retained=edge_by_uuid,
            now=now,
        )
        self._soft_delete_omitted(
            conn,
            table="workflow_node",
            workflow_uuid=workflow_uuid,
            retained=node_by_uuid,
            now=now,
        )
        next_revision = expected_revision + 1 if advance_revision else expected_revision
        conn.execute(
            "UPDATE workflow SET revision = ?, update_time = ? "
            "WHERE uuid = ? AND deleted_at IS NULL",
            (next_revision, now, workflow_uuid),
        )
        return next_revision

    def _reconcile_inventory_requirements(
        self,
        conn: sqlite3.Connection,
        *,
        workflow_uuid: str,
        requirements: List[WorkflowInventoryRequirementWrite],
        now: str,
    ) -> None:
        """在完整图 CAS 事务内替换逻辑数量库存需求。"""

        retained: list[str] = []
        seen_keys: set[str] = set()
        for sort_order, requirement in enumerate(requirements):
            identity = requirement.uuid or str(uuid4())
            requirement_key = requirement.requirement_key or identity
            if requirement_key in seen_keys:
                raise StoreConflict("duplicate workflow inventory requirement key")
            seen_keys.add(requirement_key)
            existing = conn.execute(
                "SELECT workflow_uuid,create_time FROM workflow_inventory_requirement "
                "WHERE uuid=?",
                (identity,),
            ).fetchone()
            if existing is not None and existing["workflow_uuid"] != workflow_uuid:
                raise StoreAuthoringConflict("candidate_identity_conflict")
            values = (
                now,
                None,
                requirement.description,
                _json(requirement.meta_data),
                workflow_uuid,
                requirement.consume_node_uuid,
                requirement_key,
                requirement.target_type,
                requirement.reagent_info_uuid,
                requirement.required_quantity,
                requirement.quantity_unit,
                int(requirement.allow_split),
                sort_order,
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO workflow_inventory_requirement(
                        uuid,create_time,update_time,deleted_at,description,meta_data,
                        workflow_uuid,consume_node_uuid,requirement_key,target_type,
                        reagent_info_uuid,required_quantity,quantity_unit,allow_split,
                        sort_order
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (identity, now, *values),
                )
            else:
                conn.execute(
                    """
                    UPDATE workflow_inventory_requirement
                    SET update_time=?,deleted_at=?,description=?,meta_data=?,
                        workflow_uuid=?,consume_node_uuid=?,requirement_key=?,
                        target_type=?,reagent_info_uuid=?,required_quantity=?,
                        quantity_unit=?,allow_split=?,sort_order=?
                    WHERE uuid=?
                    """,
                    (*values, identity),
                )
            retained.append(identity)
        self._soft_delete_omitted(
            conn,
            table="workflow_inventory_requirement",
            workflow_uuid=workflow_uuid,
            retained=retained,
            now=now,
        )

    def _upsert_node(
        self,
        conn: sqlite3.Connection,
        workflow_uuid: str,
        node: WorkflowNodeWrite,
        now: str,
        *,
        protect_reserved_metadata: bool,
        effective_param: Dict[str, Any],
        catalog_snapshot: AuthoringCatalogSnapshot | None = None,
    ) -> None:
        """按同一冻结模板代际写入一个工作流节点。"""

        existing = conn.execute(
            "SELECT workflow_uuid, create_time, meta_data "
            "FROM workflow_node WHERE uuid = ?",
            (node.uuid,),
        ).fetchone()
        if existing is not None and existing["workflow_uuid"] != workflow_uuid:
            raise StoreAuthoringConflict("candidate_identity_conflict")
        meta_data = self._protected_metadata(
            node.meta_data,
            existing["meta_data"] if existing is not None else None,
            enabled=protect_reserved_metadata,
        )
        template_reference = node.workflow_node_template_uuid
        if (
            template_reference is not None
            and self._template_snapshot_provider is not None
        ):
            try:
                snapshot = catalog_snapshot or self._template_snapshot_provider.snapshot()
                template_reference = snapshot.template_key_for_uuid(template_reference)
            except AuthoringCatalogError as error:
                raise StoreNotFound(
                    f"workflow node template {template_reference} not found"
                ) from error
        values = (
            node.description,
            _json(meta_data),
            workflow_uuid,
            template_reference,
            node.parent_uuid,
            node.material_uuid,
            node.name,
            node.status,
            node.type,
            node.icon,
            _json(node.pose),
            _json(effective_param),
            node.footer,
            node.action_name,
            node.action_type,
            _json(node.execution_policy),
            int(node.disabled),
            int(node.minimized),
            node.script,
        )
        if existing is None:
            conn.execute(
                """
                INSERT INTO workflow_node(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_uuid, workflow_node_template_uuid,
                    parent_uuid, material_uuid, name, status, type, icon, pose,
                    param, footer, action_name, action_type, execution_policy,
                    disabled, minimized, script
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
                """,
                (node.uuid, now, now, *values),
            )
            return
        conn.execute(
            """
            UPDATE workflow_node
            SET update_time = ?, deleted_at = NULL, description = ?,
                meta_data = ?, workflow_uuid = ?,
                workflow_node_template_uuid = ?, parent_uuid = ?,
                material_uuid = ?, name = ?, status = ?, type = ?, icon = ?,
                pose = ?, param = ?, footer = ?, action_name = ?,
                action_type = ?, execution_policy = ?, disabled = ?,
                minimized = ?, script = ?
            WHERE uuid = ?
            """,
            (now, *values, node.uuid),
        )

    def _graph_node_param(
        self,
        conn: sqlite3.Connection,
        node: WorkflowNodeWrite,
        *,
        catalog_snapshot: AuthoringCatalogSnapshot | None = None,
    ) -> Dict[str, Any]:
        """解析节点显式参数或所引用模板的默认参数。

        参数：``conn`` 仅供遗留 SQLite 模板适配器读取；``node`` 是候选节点；
        ``catalog_snapshot`` 是本事务已经冻结的内存目录代际。返回：普通参数字典。
        """

        if node.param is not None:
            return node.param
        if node.workflow_node_template_uuid is None:
            return {}
        if self._template_snapshot_provider is not None:
            snapshot = catalog_snapshot or self._template_snapshot_provider.snapshot()
            try:
                template = snapshot.require_template(
                    node.workflow_node_template_uuid
                ).template
            except AuthoringCatalogError:
                return {}
            for field in ("goal_default", "goal"):
                fallback = template.get(field)
                if isinstance(fallback, Mapping) and fallback:
                    return dict(fallback)
            return {}
        template = conn.execute(
            """
            SELECT goal_default, goal
            FROM workflow_node_template
            WHERE uuid = ? AND deleted_at IS NULL
            """,
            (node.workflow_node_template_uuid,),
        ).fetchone()
        if template is None:
            return {}
        for field in ("goal_default", "goal"):
            fallback = _load(template[field], {})
            if isinstance(fallback, dict) and fallback:
                return fallback
        return {}

    def _upsert_edge(
        self,
        conn: sqlite3.Connection,
        workflow_uuid: str,
        edge: WorkflowEdgeWrite,
        now: str,
        *,
        protect_reserved_metadata: bool,
    ) -> None:
        existing = conn.execute(
            "SELECT workflow_uuid, meta_data FROM workflow_edge WHERE uuid = ?",
            (edge.uuid,),
        ).fetchone()
        if existing is not None and existing["workflow_uuid"] != workflow_uuid:
            raise StoreAuthoringConflict("candidate_identity_conflict")
        meta_data = self._protected_metadata(
            edge.meta_data,
            existing["meta_data"] if existing is not None else None,
            enabled=protect_reserved_metadata,
        )
        values = (
            edge.description,
            _json(meta_data),
            workflow_uuid,
            edge.source_node_uuid,
            edge.target_node_uuid,
            edge.source_handle_uuid,
            edge.target_handle_uuid,
        )
        if existing is None:
            conn.execute(
                """
                INSERT INTO workflow_edge(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_uuid, source_node_uuid,
                    target_node_uuid, source_handle_uuid, target_handle_uuid
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (edge.uuid, now, now, *values),
            )
            return
        conn.execute(
            """
            UPDATE workflow_edge
            SET update_time = ?, deleted_at = NULL, description = ?,
                meta_data = ?, workflow_uuid = ?, source_node_uuid = ?,
                target_node_uuid = ?, source_handle_uuid = ?,
                target_handle_uuid = ?
            WHERE uuid = ?
            """,
            (now, *values, edge.uuid),
        )

    @staticmethod
    def _protected_metadata(
        submitted: Dict[str, Any],
        existing_json: Optional[str],
        *,
        enabled: bool,
    ) -> Dict[str, Any]:
        result = dict(submitted)
        if not enabled:
            return result
        result.pop("unilab", None)
        existing = _load(existing_json, {}) if existing_json is not None else {}
        if isinstance(existing, dict) and "unilab" in existing:
            result["unilab"] = existing["unilab"]
        return result

    @staticmethod
    def _soft_delete_omitted(
        conn: sqlite3.Connection,
        *,
        table: str,
        workflow_uuid: str,
        retained: Iterable[str],
        now: str,
    ) -> None:
        retained_values = list(retained)
        if retained_values:
            marks = ",".join("?" for _ in retained_values)
            conn.execute(
                f"""
                UPDATE {table}
                SET deleted_at = ?, update_time = ?
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                  AND uuid NOT IN ({marks})
                """,
                (now, now, workflow_uuid, *retained_values),
            )
        else:
            conn.execute(
                f"""
                UPDATE {table}
                SET deleted_at = ?, update_time = ?
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                """,
                (now, now, workflow_uuid),
            )

    # Task 与 Job --------------------------------------------------------

    def create_task_with_jobs(
        self,
        *,
        workflow_uuid: str,
        task_uuid: str,
        run_mode: str,
        target_node_uuid: Optional[str],
        description: Optional[str],
        meta_data: Dict[str, Any],
        plan_builder: Callable[[Dict[str, Any]], PreparedTaskInput],
        inventory_allocation_builder: Optional[
            Callable[
                [sqlite3.Connection, Dict[str, Any], PreparedTaskInput],
                List[Dict[str, Any]],
            ]
        ] = None,
        applied_graph: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """原子创建工作流任务（WorkflowTask）及首次节点作业。

        参数：工作流、任务、运行模式与目标标识创建意图；说明和元数据是公开
        请求事实；``applied_graph`` 是进程内定义目录一次读取的不可变应用图，
        省略时仅为兼容持久定义 Store 从当前事务读取；``plan_builder`` 必须从该
        同一应用图返回已解析输入、冻结快照、执行计划（ExecutionPlan）及作业；可选
        ``inventory_allocation_builder`` 在首次写入前用同一工作流事务锁校验
        数量型库存绑定并返回既有分配表行。返回：提交后的任务投影。
        异常：图不存在、计划或输入无效及数据库失败均回滚任务和全部作业写入。
        """

        now = utc_now()
        with self.transaction() as conn:
            if applied_graph is None:
                if not self._persist_workflow_definitions:
                    raise StoreConflict("运行事实库创建任务时缺少工作流图快照")
                graph = self.get_graph(workflow_uuid, conn=conn)
            else:
                graph = applied_graph
            prepared = plan_builder(graph)
            plan = prepared.execution_plan
            jobs = prepared.jobs
            inventory_allocations = (
                inventory_allocation_builder(conn, graph, prepared)
                if inventory_allocation_builder is not None
                else []
            )
            effective_run_mode = str(plan["run_mode"])
            effective_target = plan.get("target_node_uuid")
            control_status = "paused" if effective_run_mode == "step" else "active"
            conn.execute(
                """
                INSERT INTO workflow_task(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_uuid, status, workflow_snapshot,
                    execution_plan, run_mode, target_node_uuid, control_status,
                    cleanup_status, trace_context, input, output, error_info
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, 'pending', ?, ?, ?, ?, ?,
                          'none', '{}', ?, '{}', '[]')
                """,
                (
                    task_uuid,
                    now,
                    now,
                    description,
                    _json(meta_data),
                    workflow_uuid,
                    _json(prepared.workflow_snapshot),
                    _json(plan),
                    effective_run_mode,
                    effective_target,
                    control_status,
                    _json(prepared.resolved_input),
                ),
            )
            self._append_runtime_event(
                conn,
                task_uuid=task_uuid,
                kind="task_transition",
                from_status=None,
                to_status="pending",
                now=now,
            )
            for job in jobs:
                conn.execute(
                    """
                    INSERT INTO workflow_node_job(
                        uuid, create_time, update_time, deleted_at, description,
                        meta_data, workflow_task_uuid, workflow_node_uuid,
                        material_uuid, feedback_sequence, topological_index,
                        executor_kind, execution_policy,
                        execution_timeout_seconds, status, attempt, param,
                        feedback_data, return_info, control_data, error_info
                    ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, ?, 0, ?, ?, ?,
                              ?, 'pending', 1, ?, '{}', '{}', '{}', '[]')
                    """,
                    (
                        job["uuid"],
                        now,
                        now,
                        task_uuid,
                        job["workflow_node_uuid"],
                        job.get("material_uuid"),
                        job["topological_index"],
                        job["executor_kind"],
                        _json(job.get("execution_policy") or {}),
                        int(job.get("execution_timeout_seconds") or 0),
                        _json(job.get("param") or {}),
                    ),
                )
                self._append_runtime_event(
                    conn,
                    task_uuid=task_uuid,
                    job_uuid=job["uuid"],
                    kind="job_transition",
                    from_status=None,
                    to_status="pending",
                    now=now,
                )
            for allocation in inventory_allocations:
                conn.execute(
                    """
                    INSERT INTO workflow_inventory_allocation(
                        uuid,workflow_task_uuid,workflow_node_job_uuid,
                        requirement_key,inventory_type,inventory_uuid,
                        material_uuid,reserved_quantity,quantity_unit,status,
                        revision,reserved_at,consumed_at,released_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,'reserved',1,?,NULL,NULL)
                    """,
                    (
                        allocation["uuid"],
                        task_uuid,
                        allocation["workflow_node_job_uuid"],
                        allocation["requirement_key"],
                        allocation["inventory_type"],
                        allocation["inventory_uuid"],
                        allocation["material_uuid"],
                        allocation["reserved_quantity"],
                        allocation["quantity_unit"],
                        now,
                    ),
                )
            if inventory_allocations:
                conn.execute(
                    """
                    INSERT INTO workflow_inventory_saga(
                        workflow_task_uuid,status,operation_key,payload,last_error,
                        attempt,update_time
                    ) VALUES (?, 'reserved', ?, ?, NULL, 1, ?)
                    """,
                    (
                        task_uuid,
                        f"reserve:{task_uuid}",
                        _json(
                            {
                                "allocation_uuids": [
                                    allocation["uuid"]
                                    for allocation in inventory_allocations
                                ]
                            }
                        ),
                        now,
                    ),
                )
            self._append_event(
                conn,
                event="workflow.runtime.changed",
                data={"workflow_task_uuid": task_uuid},
                now=now,
            )
        return self.get_task(task_uuid)

    def get_task(self, task_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"workflow task {task_uuid} not found")
        return self._task_row(row)

    def list_tasks(
        self,
        *,
        page: int,
        page_size: int,
        workflow_uuid: Optional[str] = None,
        execution_kind: str = "",
        status: str = "",
        cleanup_status: str = "",
    ) -> Dict[str, Any]:
        """按 Backend 查询合同分页读取工作流任务（WorkflowTask）。

        参数：``page/page_size`` 控制分页；``workflow_uuid`` 限定工作流定义；
        ``execution_kind`` 区分工作流运行与设备单动作运行（DeviceActionRun）；
        ``status/cleanup_status`` 分别限定业务状态和清理状态。返回分页任务投影。
        """

        clauses = ["deleted_at IS NULL"]
        values: List[Any] = []
        for field, value in (
            ("workflow_uuid", workflow_uuid),
            ("execution_kind", execution_kind),
            ("status", status),
            ("cleanup_status", cleanup_status),
        ):
            if value:
                clauses.append(f"{field} = ?")
                values.append(value)
        where = " AND ".join(clauses)
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM workflow_task WHERE {where}",
                values,
            ).fetchone()[0]
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow_task WHERE {where}
                ORDER BY create_time DESC, uuid
                LIMIT ? OFFSET ?
                """,
                (*values, page_size, offset),
            ).fetchall()
        return {
            "items": [self._task_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_recoverable_tasks(
        self,
        *,
        statuses: Iterable[str] = ("pending", "running", "canceling"),
    ) -> List[Dict[str, Any]]:
        """按持久创建顺序返回启动扫描需要处理的 Task。

        参数：``statuses`` 是调用方明确允许恢复的非终态集合。返回：按
        ``create_time, uuid`` 升序排列的完整任务投影；该顺序同时是容量等待的
        稳定公平顺序，进程重启不会改变。异常：空状态集合返回空数组。
        """

        normalized = tuple(dict.fromkeys(str(status).strip() for status in statuses))
        normalized = tuple(status for status in normalized if status)
        if not normalized:
            return []
        placeholders = ", ".join("?" for _ in normalized)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow_task
                WHERE deleted_at IS NULL AND status IN ({placeholders})
                ORDER BY create_time ASC, uuid ASC
                """,
                normalized,
            ).fetchall()
        return [self._task_row(row) for row in rows]

    def list_jobs(self, task_uuid: str) -> List[Dict[str, Any]]:
        self.get_task(task_uuid)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM workflow_node_job
                WHERE workflow_task_uuid = ? AND deleted_at IS NULL
                ORDER BY topological_index, create_time, uuid
                """,
                (task_uuid,),
            ).fetchall()
        return [self._job_row(row) for row in rows]

    def get_job(self, job_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM workflow_node_job
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (job_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"workflow node job {job_uuid} not found")
        return self._job_row(row)

    def create_task_command(
        self,
        *,
        task_uuid: str,
        command_uuid: str,
        command_type: str,
        target_node_uuid: Optional[str],
        idempotency_key: str,
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> tuple[Dict[str, Any], bool]:
        """幂等创建工作流任务控制命令。"""

        now = utc_now()
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT uuid FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
            if task is None:
                raise StoreNotFound(f"workflow task {task_uuid} not found")
            existing = conn.execute(
                """
                SELECT * FROM workflow_task_command
                WHERE workflow_task_uuid = ? AND idempotency_key = ?
                  AND deleted_at IS NULL
                """,
                (task_uuid, idempotency_key),
            ).fetchone()
            if existing is not None:
                return self._task_command_row(existing), False
            conn.execute(
                """
                INSERT INTO workflow_task_command(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_task_uuid, type, target_node_uuid,
                    idempotency_key, status, result, trace_context, consumed_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 'pending', '{}', '{}', NULL)
                """,
                (
                    command_uuid,
                    now,
                    now,
                    description,
                    _json(meta_data),
                    task_uuid,
                    command_type,
                    target_node_uuid,
                    idempotency_key,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workflow_task_command WHERE uuid = ?",
                (command_uuid,),
            ).fetchone()
            return self._task_command_row(row), True

    def set_task_control_status(
        self,
        task_uuid: str,
        *,
        control_status: str,
    ) -> Dict[str, Any]:
        """Persist the Agent-visible pause/resume state on the standard Task."""

        if control_status not in {"active", "paused"}:
            raise StoreConflict("任务控制状态非法")
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(f"workflow task {task_uuid} not found")
            if row["status"] in {"succeeded", "failed", "canceled", "timeout"}:
                raise StoreConflict("终态任务不能修改控制状态")
            previous = str(row["control_status"])
            if previous != control_status:
                conn.execute(
                    """
                    UPDATE workflow_task
                    SET control_status = ?, update_time = ?
                    WHERE uuid = ? AND deleted_at IS NULL
                    """,
                    (control_status, now, task_uuid),
                )
                self._append_event(
                    conn,
                    event="workflow.runtime.changed",
                    data={"workflow_task_uuid": task_uuid},
                    now=now,
                )
            updated = conn.execute(
                "SELECT * FROM workflow_task WHERE uuid = ?", (task_uuid,)
            ).fetchone()
            return self._task_row(updated)

    def complete_task_command(
        self,
        command_uuid: str,
        *,
        status: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把已接收控制命令原子标记为成功或拒绝。"""

        if status not in {"succeeded", "rejected"}:
            raise StoreConflict("任务命令终态非法")
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM workflow_task_command
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (command_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(f"workflow task command {command_uuid} not found")
            if row["status"] != "pending":
                return self._task_command_row(row)
            conn.execute(
                """
                UPDATE workflow_task_command
                SET status = ?, result = ?, consumed_at = ?, update_time = ?
                WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                """,
                (status, _json(result), now, now, command_uuid),
            )
            self._append_runtime_event(
                conn,
                task_uuid=str(row["workflow_task_uuid"]),
                command_uuid=command_uuid,
                kind="command_consumed",
                now=now,
                data={"type": row["type"], "status": status, "result": result},
            )
            updated = conn.execute(
                "SELECT * FROM workflow_task_command WHERE uuid = ?",
                (command_uuid,),
            ).fetchone()
            return self._task_command_row(updated)

    # Workflow Debugger -------------------------------------------------

    def create_debug_configuration(
        self,
        *,
        task_uuid: str,
        start_node_uuids: list[str],
        breakpoint_node_uuids: list[str],
    ) -> Dict[str, Any]:
        """冻结调试配置并在首个活动作业前建立持久 Hold。"""

        now = utc_now()
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT uuid FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
            if task is None:
                raise StoreNotFound(f"workflow task {task_uuid} not found")
            conn.execute(
                """
                INSERT INTO workflow_task_debug_configuration(
                    workflow_task_uuid, create_time, update_time,
                    start_node_uuids, breakpoint_node_uuids,
                    execution_policy, status
                ) VALUES (?, ?, ?, ?, ?, 'step', 'paused')
                """,
                (
                    task_uuid,
                    now,
                    now,
                    _json(start_node_uuids),
                    _json(breakpoint_node_uuids),
                ),
            )
            # MaterialSource 作业会被保留以完成任务级物料准入，但调试首个 Hold
            # 必须落在用户选择的可执行起点，不能退化为“第一个拓扑作业”。
            first_job = conn.execute(
                """
                SELECT uuid, workflow_node_uuid, attempt
                FROM workflow_node_job
                WHERE workflow_task_uuid = ? AND deleted_at IS NULL
                  AND workflow_node_uuid = ?
                  AND status = 'pending'
                LIMIT 1
                """,
                (task_uuid, start_node_uuids[0]),
            ).fetchone()
            if first_job is None:
                raise StoreConflict("debug task has no active job")
            self._insert_debug_hold(
                conn,
                task_uuid=task_uuid,
                job_row=first_job,
                reason="start",
                now=now,
            )
            self._append_event(
                conn,
                event="workflow.runtime.changed",
                data={"workflow_task_uuid": task_uuid},
                now=now,
            )
        return self.get_debug_projection(task_uuid)

    def get_debug_projection(self, task_uuid: str) -> Dict[str, Any]:
        """读取不可变配置和完整 Hold 历史。"""

        with self._lock:
            configuration = self._conn.execute(
                """
                SELECT * FROM workflow_task_debug_configuration
                WHERE workflow_task_uuid = ?
                """,
                (task_uuid,),
            ).fetchone()
            if configuration is None:
                raise StoreNotFound(f"debug configuration {task_uuid} not found")
            holds = self._conn.execute(
                """
                SELECT * FROM workflow_node_admission_hold
                WHERE workflow_task_uuid = ?
                ORDER BY create_time, uuid
                """,
                (task_uuid,),
            ).fetchall()
        return {
            "configuration": {
                "start_node_uuids": _load(configuration["start_node_uuids"], []),
                "breakpoint_node_uuids": _load(
                    configuration["breakpoint_node_uuids"], []
                ),
            },
            "execution_policy": configuration["execution_policy"],
            "status": configuration["status"],
            "holds": [self._debug_hold_row(row) for row in holds],
        }

    def begin_debug_command(
        self,
        *,
        task_uuid: str,
        command_uuid: str,
        command_type: str,
        hold_uuid: str,
        idempotency_key: str,
    ) -> tuple[Dict[str, Any], bool, str]:
        """幂等接收命令、核对精确 Hold，并在同一事务放行。"""

        if command_type not in {"step", "continue"}:
            raise StoreConflict("invalid debug command")
        now = utc_now()
        with self.transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM workflow_task_debug_command
                WHERE workflow_task_uuid = ? AND idempotency_key = ?
                """,
                (task_uuid, idempotency_key),
            ).fetchone()
            if existing is not None:
                hold = conn.execute(
                    "SELECT workflow_node_uuid FROM workflow_node_admission_hold WHERE uuid = ?",
                    (existing["hold_uuid"],),
                ).fetchone()
                if hold is None:
                    raise StoreConflict("debug command hold missing")
                return self._debug_command_row(existing), False, str(
                    hold["workflow_node_uuid"]
                )
            hold = conn.execute(
                """
                SELECT * FROM workflow_node_admission_hold
                WHERE uuid = ? AND workflow_task_uuid = ? AND status = 'open'
                """,
                (hold_uuid, task_uuid),
            ).fetchone()
            if hold is None:
                raise StoreConflict("debug hold is not open")
            conn.execute(
                """
                INSERT INTO workflow_task_debug_command(
                    uuid, create_time, update_time, workflow_task_uuid,
                    type, hold_uuid, idempotency_key, status, result, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '{}', NULL)
                """,
                (
                    command_uuid,
                    now,
                    now,
                    task_uuid,
                    command_type,
                    hold_uuid,
                    idempotency_key,
                ),
            )
            conn.execute(
                """
                UPDATE workflow_node_admission_hold
                SET status = 'released', released_at = ?, update_time = ?
                WHERE uuid = ? AND status = 'open'
                """,
                (now, now, hold_uuid),
            )
            conn.execute(
                """
                UPDATE workflow_task_debug_configuration
                SET execution_policy = ?, status = 'running', update_time = ?
                WHERE workflow_task_uuid = ?
                """,
                (command_type, now, task_uuid),
            )
            row = conn.execute(
                "SELECT * FROM workflow_task_debug_command WHERE uuid = ?",
                (command_uuid,),
            ).fetchone()
            self._append_event(
                conn,
                event="workflow.runtime.changed",
                data={"workflow_task_uuid": task_uuid},
                now=now,
            )
            return (
                self._debug_command_row(row),
                True,
                str(hold["workflow_node_uuid"]),
            )

    def complete_debug_command(
        self,
        command_uuid: str,
        *,
        status: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """完成调试命令；重复调用返回既有终态。"""

        if status not in {"succeeded", "rejected"}:
            raise StoreConflict("invalid debug command status")
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_task_debug_command WHERE uuid = ?",
                (command_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(f"debug command {command_uuid} not found")
            if row["status"] == "pending":
                conn.execute(
                    """
                    UPDATE workflow_task_debug_command
                    SET status = ?, result = ?, consumed_at = ?, update_time = ?
                    WHERE uuid = ? AND status = 'pending'
                    """,
                    (status, _json(result), now, now, command_uuid),
                )
            updated = conn.execute(
                "SELECT * FROM workflow_task_debug_command WHERE uuid = ?",
                (command_uuid,),
            ).fetchone()
            return self._debug_command_row(updated)

    def advance_debug_after_job_finished(self, task_uuid: str) -> Dict[str, Any]:
        """按调试策略为下一个待处理作业建 Hold，或请求继续派发。"""

        now = utc_now()
        with self.transaction() as conn:
            configuration = conn.execute(
                """
                SELECT * FROM workflow_task_debug_configuration
                WHERE workflow_task_uuid = ?
                """,
                (task_uuid,),
            ).fetchone()
            if configuration is None:
                return {"type": "none"}
            task = conn.execute(
                "SELECT status FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
            if task is None:
                raise StoreNotFound(f"workflow task {task_uuid} not found")
            if task["status"] in {"succeeded", "failed", "canceled", "timeout"}:
                conn.execute(
                    """
                    UPDATE workflow_task_debug_configuration
                    SET status = 'completed', update_time = ?
                    WHERE workflow_task_uuid = ?
                    """,
                    (now, task_uuid),
                )
                return {"type": "complete"}
            pending = conn.execute(
                """
                SELECT uuid, workflow_node_uuid, attempt
                FROM workflow_node_job
                WHERE workflow_task_uuid = ? AND deleted_at IS NULL
                  AND status = 'pending'
                ORDER BY topological_index, create_time, uuid
                LIMIT 1
                """,
                (task_uuid,),
            ).fetchone()
            if pending is None:
                return {"type": "none"}
            breakpoints = set(_load(configuration["breakpoint_node_uuids"], []))
            node_uuid = str(pending["workflow_node_uuid"])
            should_hold = (
                configuration["execution_policy"] == "step"
                or node_uuid in breakpoints
            )
            if not should_hold:
                return {"type": "step", "workflow_node_uuid": node_uuid}
            open_hold = conn.execute(
                """
                SELECT uuid FROM workflow_node_admission_hold
                WHERE workflow_task_uuid = ? AND status = 'open'
                """,
                (task_uuid,),
            ).fetchone()
            if open_hold is None:
                self._insert_debug_hold(
                    conn,
                    task_uuid=task_uuid,
                    job_row=pending,
                    reason=("breakpoint" if node_uuid in breakpoints else "step"),
                    now=now,
                )
            conn.execute(
                """
                UPDATE workflow_task_debug_configuration
                SET status = 'paused', update_time = ?
                WHERE workflow_task_uuid = ?
                """,
                (now, task_uuid),
            )
            self._append_event(
                conn,
                event="workflow.runtime.changed",
                data={"workflow_task_uuid": task_uuid},
                now=now,
            )
            return {"type": "hold", "workflow_node_uuid": node_uuid}

    def stop_debug(self, task_uuid: str) -> None:
        """关闭调试配置并取消尚未放行的 Hold；普通任务调用是幂等空操作。"""

        now = utc_now()
        with self.transaction() as conn:
            configuration = conn.execute(
                """
                SELECT workflow_task_uuid FROM workflow_task_debug_configuration
                WHERE workflow_task_uuid = ?
                """,
                (task_uuid,),
            ).fetchone()
            if configuration is None:
                return
            conn.execute(
                """
                UPDATE workflow_task_debug_configuration
                SET status = 'stopped', update_time = ?
                WHERE workflow_task_uuid = ?
                """,
                (now, task_uuid),
            )
            conn.execute(
                """
                UPDATE workflow_node_admission_hold
                SET status = 'canceled', update_time = ?
                WHERE workflow_task_uuid = ? AND status = 'open'
                """,
                (now, task_uuid),
            )
            self._append_event(
                conn,
                event="workflow.runtime.changed",
                data={"workflow_task_uuid": task_uuid},
                now=now,
            )

    @staticmethod
    def _insert_debug_hold(
        conn: sqlite3.Connection,
        *,
        task_uuid: str,
        job_row: sqlite3.Row,
        reason: str,
        now: str,
    ) -> str:
        hold_uuid = str(uuid4())
        conn.execute(
            """
            INSERT INTO workflow_node_admission_hold(
                uuid, create_time, update_time, workflow_task_uuid,
                workflow_node_job_uuid, workflow_node_uuid, attempt,
                reason, status, released_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL)
            """,
            (
                hold_uuid,
                now,
                now,
                task_uuid,
                job_row["uuid"],
                job_row["workflow_node_uuid"],
                int(job_row["attempt"]),
                reason,
            ),
        )
        return hold_uuid

    @staticmethod
    def _debug_hold_row(row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            "uuid": row["uuid"],
            "workflow_task_uuid": row["workflow_task_uuid"],
            "workflow_node_job_uuid": row["workflow_node_job_uuid"],
            "workflow_node_uuid": row["workflow_node_uuid"],
            "attempt": row["attempt"],
            "reason": row["reason"],
            "status": row["status"],
            "create_time": row["create_time"],
            "update_time": row["update_time"],
        }
        if row["released_at"] is not None:
            result["released_at"] = row["released_at"]
        return result

    @staticmethod
    def _debug_command_row(row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            "uuid": row["uuid"],
            "workflow_task_uuid": row["workflow_task_uuid"],
            "type": row["type"],
            "scope": {"type": "hold", "hold_uuid": row["hold_uuid"]},
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "result": _load(row["result"], {}),
            "create_time": row["create_time"],
            "update_time": row["update_time"],
        }
        if row["consumed_at"] is not None:
            result["consumed_at"] = row["consumed_at"]
        return result

    def list_task_runtime_events(
        self,
        task_uuid: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> Dict[str, Any]:
        """读取一次工作流任务的持久运行时间线。

        参数：``task_uuid`` 是工作流任务身份；``after_sequence`` 是排他全局序号；
        ``limit`` 是公开页长。返回按序号递增的运行事件页，并把作业下发参数与
        明确执行结果补充到对应状态转换。异常：任务不存在时抛 ``StoreNotFound``。
        """

        self.get_task(task_uuid)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    journal.sequence AS event_sequence,
                    journal.workflow_task_uuid AS event_task_uuid,
                    journal.workflow_node_job_uuid AS event_job_uuid,
                    journal.workflow_task_command_uuid AS event_command_uuid,
                    journal.kind AS event_kind,
                    journal.from_status AS event_from_status,
                    journal.to_status AS event_to_status,
                    journal.data AS event_data,
                    journal.create_time AS event_create_time,
                    job.workflow_node_uuid AS job_workflow_node_uuid,
                    job.executor_kind AS job_executor_kind,
                    job.attempt AS job_attempt,
                    job.param AS job_param,
                    job.return_info AS job_return_info,
                    job.error_info AS job_error_info
                FROM workflow_runtime_journal AS journal
                LEFT JOIN workflow_node_job AS job
                  ON job.uuid = journal.workflow_node_job_uuid
                 AND job.deleted_at IS NULL
                WHERE journal.workflow_task_uuid = ?
                  AND journal.sequence > ?
                ORDER BY journal.sequence
                LIMIT ?
                """,
                (task_uuid, after_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        return {
            "items": [self._runtime_event_row(row) for row in selected],
            "next_cursor": (
                selected[-1]["event_sequence"] if selected else after_sequence
            ),
            "has_more": has_more,
        }

    def get_node_template(self, template_uuid: str) -> Dict[str, Any]:
        """读取一个活动工作流节点模板（WorkflowNodeTemplate）。

        参数：``template_uuid`` 是已发布模板的稳定 UUID。返回 Backend-shaped
        模板投影；模板不存在或已软删除时抛出 ``StoreNotFound``。
        """

        if self._template_snapshot_provider is not None:
            try:
                return (
                    self._template_snapshot_provider.snapshot()
                    .require_template(template_uuid)
                    .detached_template()
                )
            except AuthoringCatalogError as error:
                raise StoreNotFound(
                    f"workflow node template {template_uuid} not found"
                ) from error
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM workflow_node_template
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (template_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"workflow node template {template_uuid} not found")
        return self._node_template_row(row)

    # Authoring ----------------------------------------------------------

    def install_discovered_sources(
        self,
        registrations: Iterable[Mapping[str, str]],
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> list[dict[str, Any]]:
        """以单事务安装定义、来源和空工作流创作（Authoring）事实。

        参数：``registrations`` 是已完成文件系统校验的完整来源集合；
        ``before_commit`` 在所有 SQL 写入后、事务提交前复核外部目录身份。
        返回：按输入顺序排列的持久注册记录。
        异常：任一工作流生命周期、物理路径、来源 URI 或包身份冲突抛出
        ``StoreConflict``；提交前复核异常原样传播，整个事务不提交。
        """

        return self._commit_source_registrations(
            registrations,
            before_commit=before_commit,
            allow_create_missing=True,
        )

    def register_sources(
        self,
        registrations: Iterable[Mapping[str, str]],
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> list[dict[str, Any]]:
        """兼容旧调用并委托工作流源码定义安装深模块（Deep Module）。

        参数：``registrations`` 与 ``before_commit`` 保持旧接口含义。返回：完整安装
        后的来源注册行；异常语义与 ``install_discovered_sources`` 相同。
        """

        return self._commit_source_registrations(
            registrations,
            before_commit=before_commit,
            allow_create_missing=False,
        )

    def _commit_source_registrations(
        self,
        registrations: Iterable[Mapping[str, str]],
        *,
        before_commit: Callable[[], None] | None,
        allow_create_missing: bool,
    ) -> list[dict[str, Any]]:
        """在唯一 ``BEGIN IMMEDIATE`` 接缝（Seam）调用源码启动深模块（Deep Module）。

        参数：``registrations`` 是完整来源批次；``before_commit`` 是固定包根复核；
        ``allow_create_missing`` 区分显式安装与旧兼容入口。返回：提交后的注册行。
        异常：深模块冲突统一映射为 ``StoreConflict``，SQLite 唯一冲突整体回滚。
        """

        try:
            with self.transaction() as conn:
                return source_bootstrap.install_discovered_sources(
                    conn,
                    registrations,
                    now=utc_now(),
                    before_commit=before_commit,
                    allow_create_missing=allow_create_missing,
                )
        except source_bootstrap.SourceBootstrapConflict as exc:
            raise StoreConflict(str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise StoreConflict("工作流源码身份已被占用") from exc

    def get_source_registration(self, workflow_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM workflow_source_registration
                WHERE workflow_uuid = ?
                """,
                (workflow_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(
                f"authoring source for workflow {workflow_uuid} is not registered"
            )
        return dict(row)

    def list_source_registrations(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT registration.*
                FROM workflow_source_registration AS registration
                JOIN workflow
                  ON workflow.uuid = registration.workflow_uuid
                WHERE workflow.deleted_at IS NULL
                ORDER BY registration.workflow_uuid
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_authoring_record(self, workflow_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_authoring WHERE workflow_uuid = ?",
                (workflow_uuid,),
            ).fetchone()
        if row is None:
            return {
                "workflow_uuid": workflow_uuid,
                "observed_draft_hash": None,
                "draft_update_time": None,
                "diagnostics": [],
                "candidate_hash": None,
                "candidate": None,
                "applied_source": None,
                "writeback_status": "settled",
                "writeback_source": None,
                "writeback_expected_hash": None,
                "writeback_generation": None,
                "update_time": None,
            }
        result = dict(row)
        result["diagnostics"] = _load(result["diagnostics"], [])
        result["candidate"] = _load(result["candidate"], None)
        result["applied_source"] = _load(result["applied_source"], None)
        return result

    def validate_candidate_identity_ownership(
        self,
        *,
        workflow_uuid: str,
        node_uuids: Iterable[str],
        edge_uuids: Iterable[str],
    ) -> None:
        """验证候选节点和连线身份未被其他工作流占用。

        参数：``workflow_uuid`` 是候选所属工作流；``node_uuids``、``edge_uuids``
        是已完成候选结构校验的稳定身份。返回：无。异常：任一身份已属于其他
        工作流时抛 ``candidate_identity_conflict``，让服务在签发候选前暴露明确
        诊断，而不是把冲突延迟到 Apply 事务。
        """

        with self._lock:
            for table, identities in (
                ("workflow_node", node_uuids),
                ("workflow_edge", edge_uuids),
            ):
                for identity in identities:
                    owner = self._conn.execute(
                        f"SELECT workflow_uuid FROM {table} WHERE uuid = ?",
                        (identity,),
                    ).fetchone()
                    if owner is not None and owner["workflow_uuid"] != workflow_uuid:
                        raise StoreAuthoringConflict(
                            "candidate_identity_conflict"
                        )

    def record_draft_compilation(
        self,
        *,
        workflow_uuid: str,
        draft_hash: Optional[str],
        draft_update_time: Optional[str],
        diagnostics: List[Dict[str, Any]],
        candidate_hash: Optional[str],
        candidate: Optional[Dict[str, Any]],
        event_data: Dict[str, Any],
    ) -> int:
        now = utc_now()
        with self.transaction() as conn:
            self.get_workflow(workflow_uuid, conn=conn)
            conn.execute(
                """
                INSERT INTO workflow_authoring(
                    workflow_uuid, observed_draft_hash, draft_update_time,
                    diagnostics, candidate_hash, candidate, update_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_uuid) DO UPDATE SET
                    observed_draft_hash = excluded.observed_draft_hash,
                    draft_update_time = excluded.draft_update_time,
                    diagnostics = excluded.diagnostics,
                    candidate_hash = excluded.candidate_hash,
                    candidate = excluded.candidate,
                    writeback_status = 'settled',
                    writeback_source = NULL,
                    writeback_expected_hash = NULL,
                    writeback_generation = NULL,
                    update_time = excluded.update_time
                """,
                (
                    workflow_uuid,
                    draft_hash,
                    draft_update_time,
                    _json(diagnostics),
                    candidate_hash,
                    _json(candidate) if candidate is not None else None,
                    now,
                ),
            )
            return self._append_event(
                conn,
                event="workflow.authoring.changed",
                data=event_data,
                now=now,
            )

    def apply_authoring_candidate(
        self,
        *,
        workflow_uuid: str,
        candidate_hash: str,
        authoring_authority_validator: Callable[[str, str], None],
    ) -> Tuple[int, str]:
        """在线性化写事务内应用服务端持久候选版本（Candidate）。

        参数：``workflow_uuid`` 是工作流（Workflow）身份；``candidate_hash``
        是调用者持有的服务端签发候选哈希（Candidate Hash）；
        ``authoring_authority_validator`` 在同一 ``BEGIN IMMEDIATE`` 内复核存储
        候选推导出的源码权威（Source Authority）草稿哈希与目录指纹（Catalog
        Fingerprint）。返回：结果工作流修订（Workflow Revision）与提交后写回
        世代。异常：任何候选、草稿、目录或修订冲突都在图、事件和写回标记写入
        前失败，并由事务整体回滚。
        """

        now = utc_now()
        with self.transaction() as conn:
            writeback_generation = str(uuid4())
            authoring = conn.execute(
                """
                SELECT observed_draft_hash, candidate_hash, candidate
                FROM workflow_authoring
                WHERE workflow_uuid = ?
                """,
                (workflow_uuid,),
            ).fetchone()
            if authoring is None:
                raise StoreAuthoringConflict("candidate_not_ready")
            stored_candidate = _load(authoring["candidate"], None)
            if not isinstance(stored_candidate, dict):
                raise StoreAuthoringConflict("candidate_not_ready")
            try:
                # ``recomputed_candidate_hash`` 绑定事务内刚重读的完整八字段正文。
                recomputed_candidate_hash = compute_authoring_candidate_hash(
                    stored_candidate
                )
            except AuthoringCandidateHashError:
                raise StoreAuthoringConflict("candidate_hash_conflict") from None
            if (
                recomputed_candidate_hash != candidate_hash
                or authoring["candidate_hash"] != candidate_hash
                or stored_candidate.get("candidate_hash") != candidate_hash
            ):
                raise StoreAuthoringConflict("candidate_hash_conflict")
            try:
                # 事务前置条件只从同一持久候选推导，禁止客户端混搭世代。
                expected_draft_hash = stored_candidate["draft_hash"]
                expected_revision = stored_candidate["base_workflow_revision"]
                expected_catalog_fingerprint = stored_candidate[
                    "template_catalog_fingerprint"
                ]
                changeset = stored_candidate["changeset"]
                kind = changeset["kind"]
                graph = stored_candidate["graph"]
                normalized_source = stored_candidate["normalized_python_source"]
            except (KeyError, TypeError):
                raise StoreConflict("候选版本（Candidate）持久包缺少应用事实") from None
            if (
                not isinstance(expected_draft_hash, str)
                or type(expected_revision) is not int
                or expected_revision < 1
                or not isinstance(expected_catalog_fingerprint, str)
                or not isinstance(normalized_source, str)
            ):
                raise StoreConflict("候选版本（Candidate）持久包应用事实类型无效")
            if authoring["observed_draft_hash"] != expected_draft_hash:
                raise StoreAuthoringConflict("draft_hash_conflict")
            workflow = self.get_workflow(workflow_uuid, conn=conn)
            if workflow["revision"] != expected_revision:
                raise StoreRevisionConflict("workflow revision changed before apply")

            # 文件系统不能与 SQLite 共用锁；在首个领域写入前完成线性化复核。
            authoring_authority_validator(
                expected_draft_hash,
                expected_catalog_fingerprint,
            )
            candidate = stored_candidate
            if kind == "graph":
                graph_workflow = graph.get("workflow")
                if not isinstance(graph_workflow, dict):
                    raise StoreConflict("Candidate 缺少 Workflow 根对象")
                if (
                    graph_workflow.get("uuid") != workflow_uuid
                    or graph_workflow.get("revision") != expected_revision
                ):
                    raise StoreConflict("Candidate Workflow 身份或版本不匹配")
                candidate_meta = graph_workflow.get("meta_data")
                if not isinstance(candidate_meta, dict):
                    raise StoreConflict("Candidate Workflow meta_data 必须是对象")
                nodes = [
                    WorkflowNodeWrite.model_validate(
                        {
                            field: item[field]
                            for field in WorkflowNodeWrite.model_fields
                            if field in item
                        }
                    )
                    for item in graph.get("nodes", [])
                ]
                edges = [
                    WorkflowEdgeWrite.model_validate(
                        {
                            field: item[field]
                            for field in WorkflowEdgeWrite.model_fields
                            if field in item
                        }
                    )
                    for item in graph.get("edges", [])
                ]
                self._ensure_authoring_catalog_projection(
                    conn,
                    node_templates=graph.get("node_templates", []),
                    handle_templates=graph.get("handle_templates", []),
                    authority_id=(
                        "authoring/" + str(candidate["template_catalog_fingerprint"])
                    ),
                    now=now,
                )
                resulting_revision = self._reconcile_graph(
                    conn,
                    workflow_uuid=workflow_uuid,
                    expected_revision=expected_revision,
                    nodes=nodes,
                    edges=edges,
                    advance_revision=True,
                    protect_reserved_metadata=False,
                    semantic_workflow_meta_data=candidate_meta,
                    validate_workflow_io_contract=True,
                )
                # 领域 Python 是工作流定义权威；候选已经由 AST 与目录固定点
                # 验证，因此公开元数据和系统生成的 ``unilab`` 元数据都以候选
                # 根对象为准，不能继续保留内存投影中的陈旧公开字段。
                workflow_meta = dict(candidate_meta)
                conn.execute(
                    """
                    UPDATE workflow
                    SET meta_data = ?, name = ?, tags = ?, description = ?,
                        update_time = ?
                    WHERE uuid = ? AND deleted_at IS NULL
                    """,
                    (
                        _json(workflow_meta),
                        graph_workflow["name"],
                        _json(graph_workflow.get("tags") or []),
                        graph_workflow.get("description"),
                        now,
                        workflow_uuid,
                    ),
                )
            elif kind == "source_only":
                resulting_revision = expected_revision
            else:
                raise StoreConflict(f"unsupported Authoring changeset kind {kind!r}")
            normalized_hash = (
                "sha256:"
                + hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
            )
            applied_source = {
                "python_source": normalized_source,
                "source_hash": normalized_hash,
                "source_map": candidate["source_map"],
                "compiler_version": candidate["compiler_version"],
                "template_catalog_fingerprint": expected_catalog_fingerprint,
                "workflow_revision": resulting_revision,
                "update_time": now,
            }
            conn.execute(
                """
                UPDATE workflow_authoring
                SET diagnostics = '[]', candidate_hash = NULL,
                    candidate = NULL, applied_source = ?,
                    writeback_status = 'pending',
                    writeback_source = ?,
                    writeback_expected_hash = observed_draft_hash,
                    writeback_generation = ?,
                    update_time = ?
                WHERE workflow_uuid = ?
                """,
                (
                    _json(applied_source),
                    applied_source["python_source"],
                    writeback_generation,
                    now,
                    workflow_uuid,
                ),
            )
            self._append_event(
                conn,
                event="workflow.authoring.changed",
                data={
                    "workflow_uuid": workflow_uuid,
                    "cause": "applied",
                    "draft_hash": normalized_hash,
                    "candidate_hash": None,
                    "workflow_revision": resulting_revision,
                },
                now=now,
            )
        return resulting_revision, writeback_generation

    def _ensure_authoring_catalog_projection(
        self,
        conn: sqlite3.Connection,
        *,
        node_templates: List[Dict[str, Any]],
        handle_templates: List[Dict[str, Any]],
        authority_id: str,
        now: str,
    ) -> None:
        """在应用事务内验证候选目录，或为遗留模式持久化最小投影。

        参数说明：``conn`` 是当前唯一写事务；两个模板数组已经过服务层候选校验；
        ``authority_id`` 绑定本次编译目录指纹，``now`` 是事务时间。内存目录模式
        只核对候选与当前代际完全一致，绝不写模板表；未装配目录的遗留模式才原子
        插入最小投影。
        """

        if not isinstance(node_templates, list) or not isinstance(
            handle_templates, list
        ):
            raise StoreConflict("Candidate Catalog 投影必须是数组")
        if self._template_snapshot_provider is not None:
            self._validate_in_memory_authoring_catalog(
                node_templates=node_templates,
                handle_templates=handle_templates,
            )
            return
        for template in node_templates:
            if not isinstance(template, dict):
                raise StoreConflict("Candidate NodeTemplate 必须是对象")
            template_uuid = str(template["uuid"])
            existing = conn.execute(
                "SELECT * FROM workflow_node_template WHERE uuid = ?",
                (template_uuid,),
            ).fetchone()
            if existing is not None:
                if not self._catalog_entity_matches(
                    self._node_template_row(existing),
                    template,
                ):
                    raise StoreConflict("Candidate NodeTemplate UUID 发生语义冲突")
                conn.execute(
                    """
                    UPDATE workflow_node_template
                    SET deleted_at = NULL, update_time = ?
                    WHERE uuid = ?
                    """,
                    (now, template_uuid),
                )
                continue
            conn.execute(
                """
                INSERT INTO workflow_node_template(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, authority_id, resource_template_uuid, name,
                    display_name, class, goal, goal_default, feedback, result,
                    schema, type, icon, header, footer, node_type
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?)
                """,
                (
                    template_uuid,
                    now,
                    now,
                    template.get("description"),
                    _json(template.get("meta_data") or {}),
                    authority_id,
                    template["resource_template_uuid"],
                    template["name"],
                    template["display_name"],
                    template.get("class"),
                    _json(template.get("goal") or {}),
                    _json(template.get("goal_default") or {}),
                    _json(template.get("feedback") or {}),
                    _json(template.get("result") or {}),
                    self._catalog_schema_value(template.get("schema")),
                    template["type"],
                    template.get("icon"),
                    template.get("header"),
                    template.get("footer"),
                    template["node_type"],
                ),
            )
        for handle in handle_templates:
            if not isinstance(handle, dict):
                raise StoreConflict("Candidate HandleTemplate 必须是对象")
            handle_uuid = str(handle["uuid"])
            existing = conn.execute(
                "SELECT * FROM workflow_handle_template WHERE uuid = ?",
                (handle_uuid,),
            ).fetchone()
            if existing is not None:
                if not self._catalog_entity_matches(
                    self._handle_template_row(existing),
                    handle,
                ):
                    raise StoreConflict("Candidate HandleTemplate UUID 发生语义冲突")
                conn.execute(
                    """
                    UPDATE workflow_handle_template
                    SET deleted_at = NULL, update_time = ?
                    WHERE uuid = ?
                    """,
                    (now, handle_uuid),
                )
                continue
            conn.execute(
                """
                INSERT INTO workflow_handle_template(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, authority_id, workflow_node_template_uuid,
                    handle_key, io_type, display_name, type, required,
                    data_source, data_key
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handle_uuid,
                    now,
                    now,
                    handle.get("description"),
                    _json(handle.get("meta_data") or {}),
                    authority_id,
                    handle["workflow_node_template_uuid"],
                    handle["handle_key"],
                    handle["io_type"],
                    handle["display_name"],
                    handle["type"],
                    int(bool(handle["required"])),
                    handle.get("data_source"),
                    handle.get("data_key"),
                ),
            )

    def _validate_in_memory_authoring_catalog(
        self,
        *,
        node_templates: List[Dict[str, Any]],
        handle_templates: List[Dict[str, Any]],
    ) -> None:
        """证明候选模板是当前内存代际的只读子集。

        参数：两个数组来自候选工作流快照。返回：无；未知 UUID、重复实体或同一
        UUID 语义漂移时拒绝应用，且不访问 ``workflow_*_template`` SQLite 表。
        """

        snapshot = self._template_snapshot_provider.snapshot()
        candidate_node_by_uuid = self._unique_catalog_entities(
            node_templates,
            entity_name="NodeTemplate",
        )
        candidate_handle_by_uuid = self._unique_catalog_entities(
            handle_templates,
            entity_name="HandleTemplate",
        )
        expected_nodes, expected_handles = self._catalog_entities(
            candidate_node_by_uuid,
            snapshot=snapshot,
        )
        expected_node_by_uuid = {str(item["uuid"]): item for item in expected_nodes}
        expected_handle_by_uuid = {
            str(item["uuid"]): item for item in expected_handles
        }
        for template_uuid, candidate in candidate_node_by_uuid.items():
            expected = expected_node_by_uuid.get(template_uuid)
            if expected is None or not self._catalog_entity_matches(
                expected,
                candidate,
            ):
                raise StoreConflict("Candidate NodeTemplate 与当前内存目录不一致")
        if set(candidate_handle_by_uuid) != set(expected_handle_by_uuid):
            raise StoreConflict("Candidate HandleTemplate 与当前内存目录不一致")
        for handle_uuid, candidate in candidate_handle_by_uuid.items():
            if not self._catalog_entity_matches(
                expected_handle_by_uuid[handle_uuid],
                candidate,
            ):
                raise StoreConflict("Candidate HandleTemplate 与当前内存目录不一致")

    @staticmethod
    def _unique_catalog_entities(
        candidates: List[Dict[str, Any]],
        *,
        entity_name: str,
    ) -> Dict[str, Dict[str, Any]]:
        """按 UUID 索引候选目录实体并拒绝重复或非法元素。"""

        result: Dict[str, Dict[str, Any]] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise StoreConflict(f"Candidate {entity_name} 必须是对象")
            template_uuid = candidate.get("uuid")
            if not isinstance(template_uuid, str) or not template_uuid:
                raise StoreConflict(f"Candidate {entity_name} 缺少 UUID")
            if template_uuid in result:
                raise StoreConflict(f"Candidate {entity_name} UUID 重复")
            result[template_uuid] = candidate
        return result

    @staticmethod
    def _catalog_entity_matches(
        persisted: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> bool:
        """比较持久目录实体与候选投影的业务语义。

        参数说明：两个字典分别来自 SQLite 行和已校验候选；忽略投影时间，返回
        规范 JSON 是否相同，使相同 UUID 不可被静默改义。
        """

        ignored = {"create_time", "update_time", "deleted_at"}
        persisted_semantic = {
            key: value
            for key, value in persisted.items()
            if key not in ignored and value is not None
        }
        candidate_semantic = {
            key: value
            for key, value in candidate.items()
            if key not in ignored and value is not None
        }
        return _json(persisted_semantic) == _json(candidate_semantic)

    @staticmethod
    def _catalog_schema_value(value: Any) -> Optional[str]:
        """把候选模板 Schema 适配为当前 SQLite 文本列。

        参数说明：``value`` 可以是 ``None``、字符串或 JSON 对象；返回可写文本，
        其他类型抛出 ``StoreConflict``。F03 将负责正式目录 Schema 的版本策略。
        """

        if value is None or isinstance(value, str):
            return value
        if isinstance(value, dict):
            return _json(value)
        raise StoreConflict("Candidate NodeTemplate schema 类型无效")

    def settle_writeback(
        self,
        *,
        workflow_uuid: str,
        expected_writeback_source: str,
        expected_writeback_hash: str,
        expected_writeback_generation: str,
        observed_draft_hash: str,
        draft_update_time: str,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        with self.transaction() as conn:
            now = utc_now()
            updated = conn.execute(
                """
                UPDATE workflow_authoring
                SET observed_draft_hash = ?, draft_update_time = ?,
                    writeback_status = 'settled', writeback_source = NULL,
                    writeback_expected_hash = NULL,
                    writeback_generation = NULL, update_time = ?
                WHERE workflow_uuid = ?
                  AND writeback_status = 'pending'
                  AND writeback_source = ?
                  AND writeback_expected_hash = ?
                  AND writeback_generation = ?
                """,
                (
                    observed_draft_hash,
                    draft_update_time,
                    now,
                    workflow_uuid,
                    expected_writeback_source,
                    expected_writeback_hash,
                    expected_writeback_generation,
                ),
            )
            if updated.rowcount != 1:
                return False
            if event_data is not None:
                self._append_event(
                    conn,
                    event="workflow.authoring.changed",
                    data=event_data,
                    now=now,
                )
            return True

    def mark_writeback_pending(
        self,
        *,
        workflow_uuid: str,
        expected_writeback_source: str,
        expected_writeback_hash: str,
        expected_writeback_generation: str,
    ) -> bool:
        with self.transaction() as conn:
            updated = conn.execute(
                """
                UPDATE workflow_authoring
                SET writeback_status = 'pending', update_time = ?
                WHERE workflow_uuid = ?
                  AND writeback_source = ?
                  AND writeback_expected_hash = ?
                  AND writeback_generation = ?
                """,
                (
                    utc_now(),
                    workflow_uuid,
                    expected_writeback_source,
                    expected_writeback_hash,
                    expected_writeback_generation,
                ),
            )
            return updated.rowcount == 1

    # 事件与诊断 --------------------------------------------------------

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """读取严格晚于全局持久游标的失效通知。

        参数：``after_sequence`` 是排他事件序号，``limit`` 是物理读取上限。
        返回：按 SQLite 自增主键严格递增的事件副本。异常：查询失败时传播 SQLite
        异常；参数边界由 ``DurableEventReader`` 统一校验。本方法只读。
        """

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM frontend_event
                WHERE id > ?
                ORDER BY id
                LIMIT ?
                """,
                (after_sequence, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "event": row["event"],
                "data": _load(row["data"], {}),
                "create_time": row["create_time"],
            }
            for row in rows
        ]

    def append_forwarded_events(
        self,
        events: Iterable[Mapping[str, Any]],
    ) -> None:
        """把进程内定义目录的失效通知追加到持久全局事件流。

        参数：``events`` 是按定义目录局部序号递增的已提交事件；这里只复制事件
        类型、载荷和发生时间，并由运行事实库分配新的全局游标。返回无。异常：
        事件形状非法或 SQLite 写入失败时整批回滚，不发布部分通知。

        该投影只保存“小型失效通知”，不保存工作流定义或图；客户端收到通知后
        仍须重新读取当前进程目录，不能从事件恢复工作流权威。
        """

        normalized: list[tuple[str, Dict[str, Any], str]] = []
        for raw_event in events:
            event = str(raw_event.get("event") or "").strip()
            data = raw_event.get("data")
            create_time = str(raw_event.get("create_time") or "").strip()
            if not event or not isinstance(data, Mapping) or not create_time:
                raise ValueError("工作流定义事件形状无效")
            normalized.append((event, dict(data), create_time))
        if not normalized:
            return
        with self.transaction() as conn:
            for event, data, create_time in normalized:
                self._append_event(
                    conn,
                    event=event,
                    data=data,
                    now=create_time,
                )

    @staticmethod
    def _append_event(
        conn: sqlite3.Connection,
        *,
        event: str,
        data: Dict[str, Any],
        now: str,
    ) -> int:
        cursor = conn.execute(
            "INSERT INTO frontend_event(event, data, create_time) VALUES (?, ?, ?)",
            (event, _json(data), now),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _append_runtime_event(
        conn: sqlite3.Connection,
        *,
        task_uuid: str,
        kind: str,
        now: str,
        job_uuid: Optional[str] = None,
        command_uuid: Optional[str] = None,
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """在调用方事务内追加一个可重放的任务运行事实。"""

        cursor = conn.execute(
            """
            INSERT INTO workflow_runtime_journal(
                workflow_task_uuid, workflow_node_job_uuid,
                workflow_task_command_uuid, kind, from_status, to_status,
                data, create_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_uuid,
                job_uuid,
                command_uuid,
                kind,
                from_status,
                to_status,
                _json(data or {}),
                now,
            ),
        )
        return int(cursor.lastrowid)

    def count_rows(self, table: str, *, include_deleted: bool = False) -> int:
        allowed = {
            "workflow",
            "workflow_node",
            "workflow_edge",
            "workflow_task",
            "workflow_task_command",
            "workflow_node_job",
            "execution_lock_lease",
            "execution_lock_waiter",
            "workflow_inventory_requirement",
            "workflow_inventory_allocation",
            "workflow_inventory_saga",
            "workflow_authoring",
            "frontend_event",
            "workflow_runtime_journal",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table {table!r}")
        where = (
            ""
            if include_deleted
            or table
            in {
                "workflow_authoring",
                "frontend_event",
                "workflow_inventory_allocation",
                "workflow_inventory_saga",
            }
            else " WHERE deleted_at IS NULL"
        )
        with self._lock:
            return int(
                self._conn.execute(f"SELECT COUNT(*) FROM {table}{where}").fetchone()[0]
            )

    # 行投影 ------------------------------------------------------------

    @staticmethod
    def _base(row: sqlite3.Row) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "uuid": row["uuid"],
            "create_time": row["create_time"],
            "update_time": row["update_time"],
            "meta_data": _load(row["meta_data"], {}),
        }
        if row["description"] is not None:
            result["description"] = row["description"]
        return result

    @classmethod
    def _workflow_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            **cls._base(row),
            "name": row["name"],
            "tags": _load(row["tags"], []),
            "revision": row["revision"],
        }

    @classmethod
    def _node_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_uuid": row["workflow_uuid"],
            "name": row["name"],
            "status": row["status"],
            "type": row["type"],
            "pose": _load(row["pose"], {}),
            "param": _load(row["param"], {}),
            "execution_policy": _load(row["execution_policy"], {}),
            "disabled": bool(row["disabled"]),
            "minimized": bool(row["minimized"]),
        }
        cls._add_optional(
            result,
            row,
            "workflow_node_template_uuid",
            "parent_uuid",
            "material_uuid",
            "icon",
            "footer",
            "action_name",
            "action_type",
            "script",
        )
        return result

    @classmethod
    def _edge_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            **cls._base(row),
            "source_node_uuid": row["source_node_uuid"],
            "target_node_uuid": row["target_node_uuid"],
            "source_handle_uuid": row["source_handle_uuid"],
            "target_handle_uuid": row["target_handle_uuid"],
        }

    @classmethod
    def _inventory_requirement_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_uuid": row["workflow_uuid"],
            "consume_node_uuid": row["consume_node_uuid"],
            "requirement_key": row["requirement_key"],
            "target_type": row["target_type"],
            "required_quantity": row["required_quantity"],
            "quantity_unit": row["quantity_unit"],
            "allow_split": bool(row["allow_split"]),
            "sort_order": row["sort_order"],
        }
        cls._add_optional(result, row, "reagent_info_uuid")
        return result

    @classmethod
    def _node_template_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "resource_template_uuid": row["resource_template_uuid"],
            "name": row["name"],
            "display_name": row["display_name"],
            "goal": _load(row["goal"], {}),
            "goal_default": _load(row["goal_default"], {}),
            "feedback": _load(row["feedback"], {}),
            "result": _load(row["result"], {}),
            "type": row["type"],
            "node_type": row["node_type"],
        }
        cls._add_optional(
            result,
            row,
            "class",
            "schema",
            "icon",
            "header",
            "footer",
        )
        return result

    @classmethod
    def _handle_template_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_node_template_uuid": row["workflow_node_template_uuid"],
            "handle_key": row["handle_key"],
            "io_type": row["io_type"],
            "display_name": row["display_name"],
            "type": row["type"],
            "required": bool(row["required"]),
        }
        cls._add_optional(result, row, "data_source", "data_key")
        return result

    @classmethod
    def _task_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        """把工作流任务（WorkflowTask）数据库行恢复为公共领域投影。

        参数：``row`` 是同一工作流写模型中的 SQLite 行。返回包含执行来源
        ``execution_kind`` 的字典；内部幂等键与请求指纹不对外暴露。
        """

        result = {
            **cls._base(row),
            "workflow_uuid": row["workflow_uuid"],
            "execution_kind": row["execution_kind"],
            "status": row["status"],
            "workflow_snapshot": _load(row["workflow_snapshot"], {}),
            "execution_plan": _load(row["execution_plan"], {}),
            "run_mode": row["run_mode"],
            "control_status": row["control_status"],
            "cleanup_status": row["cleanup_status"],
            "wait_reason": _load(row["wait_reason"], {}),
            "trace_context": _load(row["trace_context"], {}),
            "input": _load(row["input"], {}),
            "output": _load(row["output"], {}),
            "error_info": _load(row["error_info"], []),
        }
        cls._add_optional(
            result,
            row,
            "target_node_uuid",
            "timeout_at",
            "attention_reason",
            "terminal_ghost_detected_at",
            "reconciliation_resume_control_status",
            "started_at",
            "finished_at",
        )
        return result

    @classmethod
    def _task_command_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_task_uuid": row["workflow_task_uuid"],
            "type": row["type"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "result": _load(row["result"], {}),
            "trace_context": _load(row["trace_context"], {}),
        }
        cls._add_optional(result, row, "target_node_uuid", "consumed_at")
        return result

    @classmethod
    def _job_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_task_uuid": row["workflow_task_uuid"],
            "workflow_node_uuid": row["workflow_node_uuid"],
            "feedback_sequence": row["feedback_sequence"],
            "topological_index": row["topological_index"],
            "executor_kind": row["executor_kind"],
            "execution_policy": _load(row["execution_policy"], {}),
            "execution_timeout_seconds": row["execution_timeout_seconds"],
            "status": row["status"],
            "attempt": row["attempt"],
            "param": _load(row["param"], {}),
            "feedback_data": _load(row["feedback_data"], {}),
            "return_info": _load(row["return_info"], {}),
            "control_data": _load(row["control_data"], {}),
            "error_info": _load(row["error_info"], []),
            "wait_reason": _load(row["wait_reason"], {}),
        }
        cls._add_optional(
            result,
            row,
            "material_uuid",
            ("edge_agent_uuid", "edge_uuid"),
            "edge_command_uuid",
            "dispatch_deadline_at",
            "execution_deadline_at",
            "cancel_command_uuid",
            "cancel_ack_deadline_at",
            "cancel_complete_deadline_at",
            "cancel_accepted_at",
            "uncertainty_reason",
            "started_at",
            "finished_at",
        )
        return result

    @staticmethod
    def _runtime_event_row(row: sqlite3.Row) -> Dict[str, Any]:
        """把运行日志查询行投影成前端稳定合同。"""

        result: Dict[str, Any] = {
            "sequence": row["event_sequence"],
            "workflow_task_uuid": row["event_task_uuid"],
            "kind": row["event_kind"],
            "data": _load(row["event_data"], {}),
            "create_time": row["event_create_time"],
        }
        for column, output in (
            ("event_job_uuid", "workflow_node_job_uuid"),
            ("event_command_uuid", "workflow_task_command_uuid"),
            ("event_from_status", "from_status"),
            ("event_to_status", "to_status"),
            ("job_workflow_node_uuid", "workflow_node_uuid"),
            ("job_executor_kind", "executor_kind"),
            ("job_attempt", "attempt"),
        ):
            value = row[column]
            if value is not None:
                result[output] = value

        kind = row["event_kind"]
        to_status = row["event_to_status"]
        if kind == "job_transition" and to_status == "dispatched":
            result["param"] = _load(row["job_param"], {})
        if kind == "job_transition" and to_status in {
            "succeeded",
            "failed",
            "skipped",
            "canceled",
            "timeout",
        }:
            result["return_info"] = _load(row["job_return_info"], {})
            result["error_info"] = _load(row["job_error_info"], [])
        return result

    @staticmethod
    def _add_optional(
        result: Dict[str, Any],
        row: sqlite3.Row,
        *fields: str | Tuple[str, str],
    ) -> None:
        for field in fields:
            column, output = field if isinstance(field, tuple) else (field, field)
            value = row[column]
            if value is not None:
                result[output] = value


__all__ = [
    "StoreAuthoringConflict",
    "StoreConflict",
    "StoreNotFound",
    "StoreRevisionConflict",
    "WorkflowStore",
    "utc_now",
]
