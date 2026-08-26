"""本地工作流存储（Workflow Store）的增量 Schema 迁移。"""

from __future__ import annotations

import sqlite3


def _execute_script_in_transaction(
    connection: sqlite3.Connection,
    script: str,
) -> None:
    """在调用方现有事务内逐条执行一段 SQLite DDL。

    参数：``connection`` 是已经 ``BEGIN`` 的连接；``script`` 是可含触发器的完整
    DDL。返回无。异常：任一语句失败时原样传播，由调用方回滚。这里不使用
    ``executescript``，因为后者会隐式提交并破坏初始化事务边界。
    """

    statement_lines: list[str] = []
    for line in script.splitlines():
        statement_lines.append(line)
        statement = "\n".join(statement_lines).strip()
        if statement and sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement_lines.clear()
    if "\n".join(statement_lines).strip():
        raise sqlite3.OperationalError("SQLite 迁移包含不完整语句")


def ensure_device_action_run_schema(connection: sqlite3.Connection) -> None:
    """补齐设备单动作运行（DeviceActionRun）所需 Task 身份字段。

    参数：``connection`` 是 ``WorkflowStore`` 初始化期间持有的唯一写连接。
    返回：无返回值；函数幂等增加 ``execution_kind``、幂等键和请求指纹，并把
    ``workflow_uuid`` 调整为可空，使直接设备动作不伪造工作流（Workflow）。
    异常会交给调用方回滚整个初始化事务。
    """

    # ``task_columns`` 是当前数据库已经持久化的 Task 列集合，用于兼容原地升级。
    task_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(workflow_task)").fetchall()
    }
    if "execution_kind" not in task_columns:
        connection.execute(
            """
            ALTER TABLE workflow_task
            ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'workflow'
                CHECK (execution_kind IN ('workflow', 'ad_hoc_device_action'))
            """
        )
    if "idempotency_key" not in task_columns:
        connection.execute("ALTER TABLE workflow_task ADD COLUMN idempotency_key TEXT")
    if "request_fingerprint" not in task_columns:
        connection.execute(
            """
            ALTER TABLE workflow_task
            ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT ''
            """
        )

    # ``table_sql`` 是 SQLite 保存的建表合同；旧版本把 workflow_uuid 声明为
    # NOT NULL，必须只改这一段才能容纳不创建 Workflow 的直接设备动作。
    table_row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'workflow_task'"
    ).fetchone()
    table_sql = str(table_row["sql"] or "") if table_row is not None else ""
    if "workflow_uuid TEXT NOT NULL" in table_sql:
        # SQLite 不能直接 DROP NOT NULL；采用 Backend 000045 已验证的
        # writable_schema 技术，只替换精确片段并推进 schema_version。
        current_schema_version = int(
            connection.execute("PRAGMA schema_version").fetchone()[0]
        )
        connection.execute("PRAGMA writable_schema = ON")
        try:
            connection.execute(
                """
                UPDATE sqlite_schema
                SET sql = replace(
                    sql,
                    'workflow_uuid TEXT NOT NULL,',
                    'workflow_uuid TEXT,'
                )
                WHERE type = 'table' AND name = 'workflow_task'
                """
            )
            connection.execute(
                f"PRAGMA schema_version = {current_schema_version + 1}"
            )
        finally:
            connection.execute("PRAGMA writable_schema = OFF")

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_workflow_task_execution_kind
        ON workflow_task(execution_kind, create_time DESC, uuid DESC)
        WHERE deleted_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_execution_idempotency
        ON workflow_task(execution_kind, idempotency_key)
        WHERE deleted_at IS NULL AND idempotency_key IS NOT NULL
        """
    )


def ensure_task_material_admission_schema(connection: sqlite3.Connection) -> None:
    """补齐本地任务物料准入（TaskMaterialAdmission）的持久事实。

    参数：``connection`` 是 ``WorkflowStore`` 初始化事务持有的唯一写连接。
    返回：无返回值；函数幂等创建准入、绑定和任务物料占有（TaskMaterialClaim）
    表，并为旧任务表补充结构化等待原因。异常由调用方回滚初始化事务。

    这些表对齐 Backend 的公开运行语义；本地 ``inventory_reservation`` 仍是 Edge
    库存实现细节，不替代这里面向任务聚合的持久事实。
    """

    task_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(workflow_task)").fetchall()
    }
    if "wait_reason" not in task_columns:
        connection.execute(
            """
            ALTER TABLE workflow_task
            ADD COLUMN wait_reason TEXT NOT NULL DEFAULT '{}'
            """
        )

    _execute_script_in_transaction(
        connection,
        """
        CREATE TABLE IF NOT EXISTS workflow_task_material_admission (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_task_uuid TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('blocked', 'admitted', 'rejected')),
            attempt INTEGER NOT NULL CHECK (attempt > 0),
            revision INTEGER NOT NULL CHECK (revision > 0),
            reason TEXT,
            wait_reason TEXT NOT NULL DEFAULT '{}',
            evaluated_at TEXT NOT NULL,
            admitted_at TEXT,
            CHECK (
                (status = 'admitted' AND admitted_at IS NOT NULL AND reason IS NULL)
                OR (status IN ('blocked', 'rejected')
                    AND admitted_at IS NULL AND reason IS NOT NULL)
            ),
            FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_material_admission_task
            ON workflow_task_material_admission(workflow_task_uuid)
            WHERE deleted_at IS NULL;

        CREATE TABLE IF NOT EXISTS workflow_task_material_binding (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_task_uuid TEXT NOT NULL,
            workflow_node_uuid TEXT NOT NULL,
            workflow_node_job_uuid TEXT NOT NULL,
            resource_template_uuid TEXT NOT NULL,
            material_uuid TEXT NOT NULL,
            site_uuid TEXT,
            flow_role TEXT NOT NULL
                CHECK (flow_role IN (
                    'primary_sample', 'aliquot_sample', 'reagent', 'consumable'
                )),
            custody_policy TEXT NOT NULL
                CHECK (custody_policy IN ('task_exclusive', 'shared_source')),
            FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid),
            FOREIGN KEY(workflow_node_job_uuid) REFERENCES workflow_node_job(uuid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_material_binding_node
            ON workflow_task_material_binding(workflow_task_uuid, workflow_node_uuid)
            WHERE deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_material_binding_job
            ON workflow_task_material_binding(
                workflow_task_uuid, workflow_node_job_uuid
            ) WHERE deleted_at IS NULL;
        CREATE INDEX IF NOT EXISTS ix_workflow_task_material_binding_material
            ON workflow_task_material_binding(material_uuid, workflow_task_uuid);

        CREATE TABLE IF NOT EXISTS workflow_task_material_claim (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_task_uuid TEXT NOT NULL,
            workflow_node_uuid TEXT NOT NULL,
            workflow_node_job_uuid TEXT NOT NULL,
            material_uuid TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'released')),
            revision INTEGER NOT NULL CHECK (revision > 0),
            acquired_at TEXT NOT NULL,
            released_at TEXT,
            CHECK (
                (status = 'active' AND released_at IS NULL)
                OR (status = 'released' AND released_at IS NOT NULL)
            ),
            FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid),
            FOREIGN KEY(workflow_node_job_uuid) REFERENCES workflow_node_job(uuid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_material_claim_node
            ON workflow_task_material_claim(workflow_task_uuid, workflow_node_uuid)
            WHERE deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_material_claim_job
            ON workflow_task_material_claim(
                workflow_task_uuid, workflow_node_job_uuid
            ) WHERE deleted_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_task_material_claim_active_material
            ON workflow_task_material_claim(material_uuid)
            WHERE deleted_at IS NULL AND status = 'active';
        CREATE INDEX IF NOT EXISTS ix_workflow_task_material_claim_task_status
            ON workflow_task_material_claim(workflow_task_uuid, status, uuid);

        CREATE TRIGGER IF NOT EXISTS trg_release_terminal_task_material_claims
        AFTER UPDATE OF status, cleanup_status ON workflow_task
        FOR EACH ROW
        WHEN (
            NEW.status = 'succeeded'
            OR (
                NEW.status IN ('failed', 'canceled', 'timeout')
                AND NEW.cleanup_status = 'settled'
            )
        ) AND (
            OLD.status IS NOT NEW.status
            OR OLD.cleanup_status IS NOT NEW.cleanup_status
        )
        BEGIN
            UPDATE workflow_task_material_claim
            SET status = 'released',
                released_at = COALESCE(NEW.finished_at, CURRENT_TIMESTAMP),
                revision = revision + 1,
                update_time = CURRENT_TIMESTAMP
            WHERE workflow_task_uuid = NEW.uuid
              AND status = 'active'
              AND deleted_at IS NULL;
        END;
        """,
    )


def ensure_execution_lock_schema(connection: sqlite3.Connection) -> None:
    """补齐本地作业执行占用（ExecutionLockLease）与等待事实。

    参数：``connection`` 是 ``WorkflowStore`` 初始化事务持有的唯一写连接。
    返回无；函数幂等补充作业等待原因，并创建执行占用与公平等待表。异常由
    调用方回滚初始化事务。

    本地工作流库只保存跨重启安全所需的锁身份和状态；物料与库位实体仍属于
    库存库，因此这里故意不声明跨库外键，也不伪造跨库原子提交能力。
    """

    job_columns = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(workflow_node_job)"
        ).fetchall()
    }
    if "wait_reason" not in job_columns:
        connection.execute(
            """
            ALTER TABLE workflow_node_job
            ADD COLUMN wait_reason TEXT NOT NULL DEFAULT '{}'
            """
        )

    _execute_script_in_transaction(
        connection,
        """
        CREATE TABLE IF NOT EXISTS execution_lock_lease (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_task_uuid TEXT NOT NULL,
            workflow_node_job_uuid TEXT NOT NULL,
            lock_key TEXT NOT NULL,
            scope TEXT NOT NULL
                CHECK (scope IN ('device', 'material', 'material_site')),
            material_uuid TEXT,
            site_uuid TEXT,
            state TEXT NOT NULL
                CHECK (state IN ('reserved', 'running', 'released', 'uncertain')),
            acquired_at TEXT NOT NULL,
            released_at TEXT,
            CHECK (
                (state IN ('reserved', 'running', 'uncertain')
                    AND released_at IS NULL)
                OR (state = 'released' AND released_at IS NOT NULL)
            ),
            FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid),
            FOREIGN KEY(workflow_node_job_uuid) REFERENCES workflow_node_job(uuid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_lock_lease_active_key
            ON execution_lock_lease(lock_key)
            WHERE deleted_at IS NULL
              AND state IN ('reserved', 'running', 'uncertain');
        CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_lock_lease_active_job_key
            ON execution_lock_lease(workflow_node_job_uuid, lock_key)
            WHERE deleted_at IS NULL
              AND state IN ('reserved', 'running', 'uncertain');
        CREATE INDEX IF NOT EXISTS ix_execution_lock_lease_job_state
            ON execution_lock_lease(workflow_node_job_uuid, state, lock_key);

        CREATE TABLE IF NOT EXISTS execution_lock_waiter (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_task_uuid TEXT NOT NULL,
            workflow_node_job_uuid TEXT NOT NULL,
            lock_key TEXT NOT NULL,
            scope TEXT NOT NULL
                CHECK (scope IN ('device', 'material', 'material_site')),
            material_uuid TEXT,
            site_uuid TEXT,
            state TEXT NOT NULL CHECK (state IN ('waiting', 'released')),
            enqueued_at TEXT NOT NULL,
            released_at TEXT,
            CHECK (
                (state = 'waiting' AND released_at IS NULL)
                OR (state = 'released' AND released_at IS NOT NULL)
            ),
            FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid),
            FOREIGN KEY(workflow_node_job_uuid) REFERENCES workflow_node_job(uuid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_lock_waiter_active_job_key
            ON execution_lock_waiter(workflow_node_job_uuid, lock_key)
            WHERE deleted_at IS NULL AND state = 'waiting';
        CREATE INDEX IF NOT EXISTS ix_execution_lock_waiter_fairness
            ON execution_lock_waiter(enqueued_at, workflow_task_uuid,
                                     workflow_node_job_uuid, lock_key)
            WHERE deleted_at IS NULL AND state = 'waiting';

        CREATE TRIGGER IF NOT EXISTS trg_release_inactive_execution_lock_waiters
        AFTER UPDATE OF status ON workflow_node_job
        FOR EACH ROW
        WHEN NEW.status <> 'pending' AND OLD.status IS NOT NEW.status
        BEGIN
            UPDATE execution_lock_waiter
            SET state = 'released',
                released_at = CURRENT_TIMESTAMP,
                update_time = CURRENT_TIMESTAMP
            WHERE workflow_node_job_uuid = NEW.uuid
              AND state = 'waiting'
              AND deleted_at IS NULL;
        END;
        """,
    )


def ensure_local_cancellation_schema(connection: sqlite3.Connection) -> None:
    """补齐 Local 模式设备取消受理事实与超时扫描索引。

    参数：``connection`` 是 ``WorkflowStore`` 初始化事务持有的唯一写连接。
    返回无；函数幂等增加 ``cancel_accepted_at`` 并建立待取消作业截止时间索引。
    异常由调用方回滚初始化事务。该字段只表示本地执行器已受理停止请求，不表示
    设备已经安全停止；最终结算仍以作业终态为准。
    """

    job_columns = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(workflow_node_job)"
        ).fetchall()
    }
    if "cancel_accepted_at" not in job_columns:
        connection.execute(
            "ALTER TABLE workflow_node_job ADD COLUMN cancel_accepted_at TEXT"
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_workflow_node_job_local_cancel_deadline
        ON workflow_node_job(
            cancel_ack_deadline_at,
            cancel_complete_deadline_at,
            uuid
        )
        WHERE deleted_at IS NULL AND status = 'cancel_requested'
        """
    )


def ensure_workflow_inventory_schema(connection: sqlite3.Connection) -> None:
    """创建逻辑库存需求、运行分配投影与跨库 Saga 状态。

    工作流库只保存定义和审计投影；试剂/当前内容物的数量仍由
    ``inventory.db`` 裁决。``workflow_inventory_saga`` 是两个 SQLite
    事务域之间的可重放意图，不伪装跨库原子事务。
    """

    _execute_script_in_transaction(
        connection,
        """
        CREATE TABLE IF NOT EXISTS workflow_inventory_requirement (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_uuid TEXT NOT NULL,
            consume_node_uuid TEXT NOT NULL,
            requirement_key TEXT NOT NULL,
            target_type TEXT NOT NULL
                CHECK (target_type IN ('reagent_info', 'current_substance')),
            reagent_info_uuid TEXT,
            required_quantity REAL NOT NULL CHECK (required_quantity > 0),
            quantity_unit TEXT NOT NULL CHECK (length(trim(quantity_unit)) > 0),
            allow_split INTEGER NOT NULL DEFAULT 0 CHECK (allow_split IN (0, 1)),
            sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
            CHECK (
                (target_type = 'reagent_info' AND reagent_info_uuid IS NOT NULL)
                OR (target_type = 'current_substance' AND reagent_info_uuid IS NULL)
            ),
            FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid),
            FOREIGN KEY(consume_node_uuid) REFERENCES workflow_node(uuid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_inventory_requirement_active
            ON workflow_inventory_requirement(workflow_uuid, requirement_key)
            WHERE deleted_at IS NULL;
        CREATE INDEX IF NOT EXISTS ix_workflow_inventory_requirement_node
            ON workflow_inventory_requirement(consume_node_uuid, sort_order, uuid)
            WHERE deleted_at IS NULL;

        CREATE TABLE IF NOT EXISTS workflow_inventory_allocation (
            uuid TEXT PRIMARY KEY,
            workflow_task_uuid TEXT NOT NULL,
            workflow_node_job_uuid TEXT NOT NULL,
            requirement_key TEXT NOT NULL,
            inventory_type TEXT NOT NULL
                CHECK (inventory_type IN ('reagent', 'current_substance')),
            inventory_uuid TEXT NOT NULL,
            material_uuid TEXT NOT NULL,
            reserved_quantity REAL NOT NULL CHECK (reserved_quantity > 0),
            quantity_unit TEXT NOT NULL CHECK (length(trim(quantity_unit)) > 0),
            status TEXT NOT NULL
                CHECK (status IN ('reserved', 'consumed', 'released')),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
            reserved_at TEXT NOT NULL,
            consumed_at TEXT,
            released_at TEXT,
            CHECK (
                (status = 'reserved' AND consumed_at IS NULL AND released_at IS NULL)
                OR (status = 'consumed' AND consumed_at IS NOT NULL AND released_at IS NULL)
                OR (status = 'released' AND consumed_at IS NULL AND released_at IS NOT NULL)
            ),
            FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid),
            FOREIGN KEY(workflow_node_job_uuid) REFERENCES workflow_node_job(uuid)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_inventory_allocation_subject
            ON workflow_inventory_allocation(
                workflow_task_uuid, requirement_key, inventory_type, inventory_uuid
            );
        CREATE INDEX IF NOT EXISTS ix_workflow_inventory_allocation_task_status
            ON workflow_inventory_allocation(workflow_task_uuid, status, uuid);
        CREATE INDEX IF NOT EXISTS ix_workflow_inventory_allocation_job_status
            ON workflow_inventory_allocation(workflow_node_job_uuid, status, uuid);

        CREATE TABLE IF NOT EXISTS workflow_inventory_saga (
            workflow_task_uuid TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (
                status IN (
                    'reserve_pending', 'reserved', 'consume_pending',
                    'release_pending', 'settled', 'compensation_pending'
                )
            ),
            operation_key TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            last_error TEXT,
            attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
            update_time TEXT NOT NULL,
            FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid)
        );
        CREATE INDEX IF NOT EXISTS ix_workflow_inventory_saga_status
            ON workflow_inventory_saga(status, update_time, workflow_task_uuid);
        """,
    )


__all__ = [
    "ensure_device_action_run_schema",
    "ensure_execution_lock_schema",
    "ensure_local_cancellation_schema",
    "ensure_task_material_admission_schema",
    "ensure_workflow_inventory_schema",
]
