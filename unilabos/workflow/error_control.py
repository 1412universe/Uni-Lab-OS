"""Edge 工站错误处置权威：暂停、人工会话与恢复命令共用运行库。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from unilabos.workflow.event_writer import append_frontend_event
from unilabos.workflow.intervention import ensure_intervention_schema
from unilabos.workflow.store import StoreConflict, StoreNotFound, WorkflowStore, utc_now

OPTIONS = [
    {"id": "cancel_task", "label": "取消当前任务"},
    {"id": "retry_current_node", "label": "重试当前节点"},
    {"id": "enter_manual_handling", "label": "进入手动处理"},
]


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def ensure_error_control_schema(conn: sqlite3.Connection) -> None:
    """扩展已有执行事实，只新增工站控制、控制命令和处置会话三张表。"""
    ensure_intervention_schema(conn)
    for table, fields in {
        "workflow_intervention": {
            "decision_kind": "TEXT NOT NULL DEFAULT 'device_intervention'",
            "station_id": "TEXT",
            "error_event_key": "TEXT",
            "error_phase": "TEXT",
            "stage": "TEXT NOT NULL DEFAULT 'DECISION_REQUIRED'",
            "initial_action": "TEXT",
            "final_action": "TEXT",
            "session_id": "TEXT",
            "observed_error_epoch": "INTEGER NOT NULL DEFAULT 0",
        },
        "workflow_node_job": {
            "current_attempt": "INTEGER NOT NULL DEFAULT 1",
            "retry_of_job_uuid": "TEXT",
            "execution_source": "TEXT NOT NULL DEFAULT 'workflow'",
            "definition_fingerprint": "TEXT",
            "start_state": "TEXT NOT NULL DEFAULT 'NOT_STARTED'",
        },
    }.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in fields.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                if table == "workflow_node_job" and name == "start_state":
                    conn.execute(
                        "UPDATE workflow_node_job SET start_state='UNKNOWN' WHERE status IN ('dispatched','running','cancel_requested')"
                    )
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ux_workflow_node_current_attempt'"
        ).fetchone()
        is None
    ):
        conn.execute("""UPDATE workflow_node_job SET current_attempt=0 WHERE current_attempt=1 AND EXISTS (
        SELECT 1 FROM workflow_node_job newer WHERE newer.workflow_task_uuid=workflow_node_job.workflow_task_uuid
        AND COALESCE(json_extract(newer.control_data,'$.runtime_node_id'),newer.workflow_node_uuid)=COALESCE(json_extract(workflow_node_job.control_data,'$.runtime_node_id'),workflow_node_job.workflow_node_uuid)
        AND newer.deleted_at IS NULL AND (newer.attempt>workflow_node_job.attempt OR (newer.attempt=workflow_node_job.attempt AND (newer.create_time,newer.uuid)>(workflow_node_job.create_time,workflow_node_job.uuid))))""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_node_current_attempt
        ON workflow_node_job(workflow_task_uuid, COALESCE(json_extract(control_data,'$.runtime_node_id'),workflow_node_uuid))
        WHERE current_attempt=1 AND deleted_at IS NULL""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_edge_error_episode
        ON workflow_intervention(station_id, workflow_node_job_uuid, error_event_key)
        WHERE decision_kind = 'edge_node_error'""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workflow_station_control (
        station_id TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'RUNNING'
            CHECK(mode IN ('RUNNING','PAUSED')),
        version INTEGER NOT NULL DEFAULT 1, error_epoch INTEGER NOT NULL DEFAULT 0,
        active_session_id TEXT, last_control_command_uuid TEXT, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workflow_station_control_command (
        command_uuid TEXT PRIMARY KEY, station_id TEXT NOT NULL, operation TEXT NOT NULL,
        idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        expected_version INTEGER NOT NULL, applied_version INTEGER, error_epoch INTEGER NOT NULL,
        operator_id TEXT NOT NULL, operator_confirmed INTEGER NOT NULL DEFAULT 0,
        snapshot_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING'
            CHECK(status IN ('PENDING','APPLIED','FAILED')),
        created_at TEXT NOT NULL, applied_at TEXT,
        UNIQUE(station_id,operation,idempotency_key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workflow_error_session (
        session_id TEXT PRIMARY KEY, station_id TEXT NOT NULL, decision_id TEXT NOT NULL UNIQUE,
        stage TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, summary TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL, completed_by TEXT, created_at TEXT NOT NULL,
        completed_at TEXT, resolved_at TEXT)""")


class StationExecutionControl:
    """把新错误暂停和人工决定保存为一个可恢复、可比较版本的控制事实。"""

    def __init__(
        self, store: WorkflowStore, *, station_id: str = "local", lock: Any = None
    ) -> None:
        self.store = store
        self.recovering = False
        self.station_id = station_id
        self.lock = lock if lock is not None else threading.RLock()
        with store.transaction() as conn:
            ensure_error_control_schema(conn)
            conn.execute(
                """INSERT OR IGNORE INTO workflow_station_control(station_id,updated_at)
                VALUES(?,?)""",
                (station_id, utc_now()),
            )

    def snapshot(self) -> dict[str, Any]:
        """在一个读快照中返回控制状态、独立错误和活动会话。"""
        with self.lock, self.store.read() as conn:
            return self._snapshot(conn)

    def allows_task(self, task_uuid: str) -> bool:
        """普通调度读取同一持久闸门；错误手动任务由独立入口放行。"""
        with self.lock, self.store.read() as conn:
            station = conn.execute(
                "SELECT mode FROM workflow_station_control WHERE station_id=?",
                (self.station_id,),
            ).fetchone()
            task = conn.execute(
                "SELECT control_status FROM workflow_task WHERE uuid=?", (task_uuid,)
            ).fetchone()
            pending = conn.execute(
                "SELECT 1 FROM workflow_intervention WHERE workflow_task_uuid=? AND decision_kind='edge_node_error' AND stage!='RESOLVED' LIMIT 1",
                (task_uuid,),
            ).fetchone()
            return (
                not self.recovering
                and station[0] == "RUNNING"
                and pending is None
                and (task is None or task[0] != "error_waiting")
            )

    def is_current(self, job_uuid: str) -> bool:
        with self.lock, self.store.read() as conn:
            row = conn.execute(
                "SELECT current_attempt FROM workflow_node_job WHERE uuid=?",
                (job_uuid,),
            ).fetchone()
            return row is None or bool(row[0])

    def awaits_decision(self, job_uuid: str) -> bool:
        with self.lock, self.store.read() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM workflow_intervention WHERE workflow_node_job_uuid=? AND decision_kind='edge_node_error' AND stage!='RESOLVED' LIMIT 1",
                    (job_uuid,),
                ).fetchone()
                is not None
            )

    def is_manual(self, job_uuid: str) -> bool:
        with self.lock, self.store.read() as conn:
            row = conn.execute(
                "SELECT execution_source FROM workflow_node_job WHERE uuid=?",
                (job_uuid,),
            ).fetchone()
            return row is not None and row[0] == "error_manual"

    def allows_delivery(self, job_uuid: str) -> bool:
        """出队只检查当前控制状态；取消消息不经过该普通开始入口。"""
        with self.lock, self.store.read() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_node_job WHERE uuid=?", (job_uuid,)
            ).fetchone()
            if row is None or self.recovering:
                return False
            if row["execution_source"] == "error_manual":
                # 撤销后的旧投递仍可被接收并按持久 no-send 事实结算。
                return True
            return row["start_state"] != "NOT_STARTED" or self.allows_task(
                row["workflow_task_uuid"]
            )

    def accept_manual(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
        operator_id: str,
        prepare: Callable[..., Any],
    ) -> dict[str, Any]:
        """先查原始请求幂等键，再读取最新模板；会话与执行身份在同事务保存。"""
        from unilabos.workflow.device_action_run_store import DeviceActionRunStore

        operation = f"manual:{session_id}"
        with self.lock:
            with self.store.read() as conn:
                existing = self._existing(
                    conn, operation, idempotency_key, request, operator_id
                )
                if existing is not None:
                    return existing
                self._check_station(conn, request)
                session = self._read_session(conn, session_id)
                decision = self._read_decision(conn, session["decision_id"])
                metadata = {
                    "execution_source": "error_manual",
                    "session_id": session_id,
                    "decision_id": decision["decision_id"],
                    "source_task_uuid": decision["workflow_task_uuid"],
                    "source_job_uuid": decision["workflow_node_job_uuid"],
                    "source_node_id": json.loads(decision["meta_data"])[
                        "source_node_id"
                    ],
                    "operator_id": operator_id,
                }
            task, job = prepare(
                request,
                idempotency_key=f"error-manual:{session_id}:{idempotency_key}",
                metadata=metadata,
            )
            with self.store.transaction() as conn:
                existing = self._existing(
                    conn, operation, idempotency_key, request, operator_id
                )
                if existing is not None:
                    return existing
                station = self._check_station(conn, request)
                session = self._read_session(conn, session_id)
                if (
                    station["mode"] != "PAUSED"
                    or station["active_session_id"] != session_id
                    or session["stage"] != "MANUAL_HANDLING"
                    or session["version"] != request.get("expected_session_version")
                ):
                    raise StoreConflict("session_version_conflict")
                DeviceActionRunStore.insert_prepared(conn, task=task, job=job)
                command_uuid = str(uuid4())
                conn.execute(
                    """UPDATE workflow_task SET trace_context=(SELECT trace_context FROM workflow_task WHERE uuid=?) WHERE uuid=?""",
                    (metadata["source_task_uuid"], task["uuid"]),
                )
                conn.execute(
                    """UPDATE workflow_node_job SET execution_source='error_manual',definition_fingerprint=?,
                    edge_command_uuid=?,meta_data=? WHERE uuid=?""",
                    (
                        request["action_version"],
                        command_uuid,
                        _json(metadata),
                        job["uuid"],
                    ),
                )
                result = {
                    "manual_command_id": command_uuid,
                    "execution_task_uuid": task["uuid"],
                    "execution_job_uuid": job["uuid"],
                    **metadata,
                }
                return self._save_command(
                    conn,
                    operation=operation,
                    key=idempotency_key,
                    request=request,
                    operator=operator_id,
                    result=result,
                    applied=True,
                )

    def manual_action(self, command_uuid: str) -> dict[str, Any]:
        with self.lock, self.store.read() as conn:
            row = conn.execute(
                """SELECT * FROM workflow_node_job WHERE edge_command_uuid=? AND json_extract(meta_data,'$.execution_source')='error_manual'""",
                (command_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound("手动执行命令不存在")
            metadata = json.loads(row["meta_data"])
            self._read_session(conn, metadata["session_id"])
            return {
                "manual_command_id": command_uuid,
                "execution_job": WorkflowStore._job_row(row),
                "execution_task": WorkflowStore._task_row(
                    conn.execute(
                        "SELECT * FROM workflow_task WHERE uuid=?",
                        (row["workflow_task_uuid"],),
                    ).fetchone()
                ),
                **metadata,
            }

    def accept_stop(
        self,
        command_uuid: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """停止独立执行命令，不依赖会话仍处于手动阶段。"""
        with self.lock, self.store.transaction() as conn:
            operation = f"stop:{command_uuid}"
            existing = self._existing(
                conn, operation, idempotency_key, request, operator_id
            )
            if existing is not None:
                return existing
            job = conn.execute(
                "SELECT * FROM workflow_node_job WHERE edge_command_uuid=? AND json_extract(meta_data,'$.execution_source')='error_manual'",
                (command_uuid,),
            ).fetchone()
            if job is None:
                raise StoreNotFound("手动动作不存在")
            self._read_session(conn, json.loads(job["meta_data"])["session_id"])
            return self._save_command(
                conn,
                operation=operation,
                key=idempotency_key,
                request=request,
                operator=operator_id,
                applied=False,
                result={
                    "action": "stop_manual_action",
                    "source_task_uuid": job["workflow_task_uuid"],
                    "execution_job_uuid": job["uuid"],
                    "manual_command_id": command_uuid,
                    "reason": request.get("reason", "用户停止手动动作"),
                },
            )

    def begin_execution(self, job_uuid: str, command_uuid: str) -> str:
        """紧邻实际动作调用的不可撤回开始边界，不可用作预取排队票。"""
        with self.lock, self.store.transaction() as conn:
            job = conn.execute(
                "SELECT * FROM workflow_node_job WHERE uuid=?", (job_uuid,)
            ).fetchone()
            if job is None or job["edge_command_uuid"] != command_uuid:
                raise StoreConflict("execution_identity_conflict")
            if self.recovering:
                return "WAIT"
            if job["start_state"] != "NOT_STARTED":
                return "ALREADY_STARTED"  # 重放仅返回事实，绝不再授权调用。
            if job["status"] not in {"dispatched", "running"}:
                return (
                    "CANCELED"
                    if job["status"] in {"canceled", "cancel_requested"}
                    else "WAIT"
                )
            if job["execution_source"] == "error_manual":
                metadata = json.loads(job["meta_data"])
                session = self._read_session(conn, metadata["session_id"])
                if session["stage"] != "MANUAL_HANDLING":
                    return "CANCELED"
            elif not self.allows_task(job["workflow_task_uuid"]):
                return "WAIT"
            conn.execute(
                "UPDATE workflow_node_job SET start_state='START_COMMITTED',update_time=? WHERE uuid=?",
                (utc_now(), job_uuid),
            )
            self._emit(conn, job["workflow_task_uuid"])
            return "START"

    def execution_start_state(self, job_uuid: str) -> str:
        """读取开始事实；不签发许可，也不改变状态。"""
        return str(self.store.get_job(job_uuid)["start_state"])

    def recovery_dispatch_payloads(self) -> list[dict[str, Any]]:
        """只返回可证明未启动的持久出站意图；不会重算参数或身份。"""
        with self.lock, self.store.read() as conn:
            rows = conn.execute(
                "SELECT control_data FROM workflow_node_job WHERE start_state='NOT_STARTED' AND status='dispatched' AND executor_kind!='manual_confirm' AND deleted_at IS NULL"
            ).fetchall()
            return [
                json.loads(row["control_data"])["dispatch_payload"]
                for row in rows
                if json.loads(row["control_data"]).get("dispatch_payload")
            ]

    def prepare_manual_dispatch(
        self, job_uuid: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """只验证会话和命令身份，手动派发不创建任何资源 Claim 或 Fence。"""
        with self.lock, self.store.transaction() as conn:
            job = conn.execute(
                "SELECT * FROM workflow_node_job WHERE uuid=? AND execution_source='error_manual'",
                (job_uuid,),
            ).fetchone()
            if job is None or job["status"] != "pending":
                return None
            metadata = json.loads(job["meta_data"])
            session = self._read_session(conn, metadata["session_id"])
            if session["stage"] != "MANUAL_HANDLING":
                return None
            now = utc_now()
            credentials = {
                "command_uuid": job["edge_command_uuid"],
                "execution_source": "error_manual",
                "attempt": job["attempt"],
                "claim_uuid": None,
                "fences": [],
                "effect_uuid": None,
                "parameter_hash": None,
                "expected_change_set": {},
            }
            data = json.loads(job["control_data"])
            data["dispatch_payload"] = {**payload, **credentials}
            conn.execute(
                "UPDATE workflow_node_job SET status='dispatched',control_data=?,update_time=? WHERE uuid=?",
                (json.dumps(data, ensure_ascii=False), now, job_uuid),
            )
            conn.execute(
                "UPDATE workflow_task SET status='running',started_at=COALESCE(started_at,?),update_time=? WHERE uuid=?",
                (now, now, job["workflow_task_uuid"]),
            )
            self._emit(conn, job["workflow_task_uuid"])
            return {
                "command_uuid": job["edge_command_uuid"],
                "execution_source": "error_manual",
                "attempt": job["attempt"],
                "claim_uuid": None,
                "fences": [],
                "effect_uuid": None,
                "parameter_hash": None,
                "expected_change_set": {},
            }

    def _snapshot(self, conn: sqlite3.Connection) -> dict[str, Any]:
        station = dict(
            conn.execute(
                "SELECT * FROM workflow_station_control WHERE station_id=?",
                (self.station_id,),
            ).fetchone()
        )
        station["station_version"] = station.pop("version")
        station["snapshot_id"] = (
            f"{self.station_id}:{station['station_version']}:{station['error_epoch']}"
        )
        station["active_session"] = None
        if station["active_session_id"]:
            station["active_session"] = dict(
                conn.execute(
                    "SELECT * FROM workflow_error_session WHERE session_id=?",
                    (station["active_session_id"],),
                ).fetchone()
            )
        station["errors"] = [
            self._decision(row)
            for row in conn.execute(
                """SELECT * FROM workflow_intervention WHERE station_id=?
            AND decision_kind='edge_node_error' AND stage!='RESOLVED' ORDER BY opened_at,uuid""",
                (self.station_id,),
            )
        ]
        station["manual_actions"] = self._manual_summaries(conn)
        station["control_commands"] = []
        for row in conn.execute(
            "SELECT command_uuid,operation,status,snapshot_json FROM workflow_station_control_command WHERE station_id=? ORDER BY created_at DESC LIMIT 100",
            (self.station_id,),
        ):
            execution = json.loads(row["snapshot_json"]).get("execution_task_uuid")
            pending_delivery = (
                execution is not None
                and conn.execute(
                    "SELECT 1 FROM workflow_task WHERE uuid=? AND status='pending'",
                    (execution,),
                ).fetchone()
                is not None
            )
            station["control_commands"].append(
                {
                    "command_uuid": row["command_uuid"],
                    "operation": row["operation"],
                    "status": row["status"],
                    "needs_apply": pending_delivery or row["status"] == "PENDING",
                }
            )
        station["event_cursor"] = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM frontend_event"
        ).fetchone()[0]
        return station

    def _manual_summaries(
        self, conn: sqlite3.Connection, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            WorkflowStore._job_row(row)
            for row in conn.execute(
                """SELECT j.* FROM workflow_node_job j
            JOIN workflow_error_session s ON s.session_id=json_extract(j.meta_data,'$.session_id')
            WHERE s.station_id=? AND json_extract(j.meta_data,'$.execution_source')='error_manual' AND (? IS NULL OR s.session_id=?)
            ORDER BY j.create_time DESC LIMIT 200""",
                (self.station_id, session_id, session_id),
            )
        ]

    def get_command(self, command_uuid: str) -> dict[str, Any]:
        with self.lock, self.store.read() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_station_control_command WHERE command_uuid=? AND station_id=?",
                (command_uuid, self.station_id),
            ).fetchone()
            if row is None:
                raise StoreNotFound("控制命令不存在")
            return self._command_result(conn, row)

    def reject_command(self, command_uuid: str, message: str) -> dict[str, Any]:
        """已受理的事实修正被确定拒绝时终结意图，重启不反复应用坏版本。"""
        with self.lock, self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_station_control_command WHERE command_uuid=? AND station_id=?",
                (command_uuid, self.station_id),
            ).fetchone()
            if row is None:
                raise StoreNotFound("控制命令不存在")
            if row["status"] == "PENDING":
                result = {**json.loads(row["snapshot_json"]), "error": message}
                now = utc_now()
                conn.execute(
                    "UPDATE workflow_station_control_command SET status='FAILED',snapshot_json=?,applied_at=? WHERE command_uuid=?",
                    (_json(result), now, command_uuid),
                )
                conn.execute(
                    "UPDATE workflow_task_command SET status='rejected',result=?,consumed_at=?,update_time=? WHERE uuid=?",
                    (_json(result), now, now, command_uuid),
                )
                self._emit(conn, result.get("source_task_uuid"))
            return self._command_result(
                conn,
                conn.execute(
                    "SELECT * FROM workflow_station_control_command WHERE command_uuid=?",
                    (command_uuid,),
                ).fetchone(),
            )

    def occupancies(self) -> list[dict[str, Any]]:
        from unilabos.workflow.execution_lock_lease import list_execution_locks

        with self.lock, self.store.read() as conn:
            locks = [
                {
                    **row,
                    "occupancy_kind": "execution_lock",
                    "version": f"{row['update_time']}:{row['fencing_token']}",
                }
                for row in list_execution_locks(conn)
                if row["state"] != "released"
            ]
            tenancies = [
                {
                    **dict(row),
                    "occupancy_kind": "device_tenancy",
                    "workflow_node_job_uuid": row["acquired_by_job_uuid"],
                    "lock_key": row["device_lock_key"],
                    "version": row["update_time"],
                }
                for row in conn.execute(
                    "SELECT * FROM task_device_tenancy WHERE state='active'"
                )
            ]
            return locks + tenancies

    def accept_auxiliary(
        self,
        operation: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """在工站事务内保存库存或指定占用处置意图，跨库存库以相同身份补偿。"""
        with self.lock, self.store.transaction() as conn:
            existing = self._existing(
                conn, operation, idempotency_key, request, operator_id
            )
            if existing is not None:
                return existing
            station = self._check_station(conn, request)
            if (
                station["mode"] != "PAUSED"
                or not str(request.get("reason") or "").strip()
            ):
                raise StoreConflict("人工处置要求工站暂停并填写原因")
            session_id = request.get("session_id")
            if session_id:
                session = self._read_session(conn, session_id)
                if (
                    station["active_session_id"] != session_id
                    or session["stage"] != "MANUAL_HANDLING"
                    or session["version"] != request.get("expected_session_version")
                ):
                    raise StoreConflict("session_version_conflict")
                decision = self._read_decision(conn, session["decision_id"])
            else:
                decision = self._read_decision(conn, request["decision_id"])
            if decision["stage"] not in {"DECISION_REQUIRED", "MANUAL_HANDLING"}:
                raise StoreConflict("decision_conflict")
            result = {
                **request,
                "operator_id": operator_id,
                "source_task_uuid": decision["workflow_task_uuid"],
                "source_job_uuid": decision["workflow_node_job_uuid"],
                "action": "inventory_correction"
                if operation.startswith("inventory:")
                else "resource_disposition",
            }
            if operation.startswith("resource:"):
                ids = request.get("occupancy_ids") or []
                if (
                    not ids
                    or len(set(ids)) != len(ids)
                    or request.get("operation") != "release"
                ):
                    raise ValueError("必须选择不同占用并使用 release 操作")
                occupancies = []
                for identity in ids:
                    row = conn.execute(
                        "SELECT * FROM execution_lock_lease WHERE uuid=? AND deleted_at IS NULL",
                        (identity,),
                    ).fetchone()
                    if row is None:
                        tenancy = conn.execute(
                            "SELECT * FROM task_device_tenancy WHERE uuid=?",
                            (identity,),
                        ).fetchone()
                        if tenancy is not None:
                            if (
                                request.get("expected_versions", {}).get(identity)
                                != tenancy["update_time"]
                            ):
                                raise StoreConflict("occupancy_version_conflict")
                            occupancies.append(
                                {
                                    **dict(tenancy),
                                    "occupancy_kind": "device_tenancy",
                                    "lock_key": tenancy["device_lock_key"],
                                    "workflow_node_job_uuid": tenancy[
                                        "acquired_by_job_uuid"
                                    ],
                                }
                            )
                            continue
                    if row is None:
                        raise StoreNotFound("资源占用不存在")
                    if (
                        request.get("expected_versions", {}).get(identity)
                        != f"{row['update_time']}:{row['fencing_token']}"
                    ):
                        raise StoreConflict("occupancy_version_conflict")
                    occupancies.append(dict(row))
                result["occupancies"] = occupancies
            related = request.get("related_manual_command_id")
            if related:
                row = conn.execute(
                    "SELECT meta_data FROM workflow_node_job WHERE edge_command_uuid=? AND execution_source='error_manual'",
                    (related,),
                ).fetchone()
                if (
                    row is None
                    or json.loads(row["meta_data"]).get("session_id") != session_id
                ):
                    raise StoreConflict("手动命令不属于当前会话")
            return self._save_command(
                conn,
                operation=operation,
                key=idempotency_key,
                request=request,
                operator=operator_id,
                result=result,
                applied=False,
            )

    def apply_resource_facts(self, command: dict[str, Any]) -> None:
        """仅释放已选行；执行证据、未知状态和结果路由始终保留。"""
        from unilabos.workflow.execution_lock_lease import (
            record_execution_lock_operator_action,
        )

        with self.lock, self.store.transaction() as conn:
            now = utc_now()
            for item in command["result"]["occupancies"]:
                if item.get("occupancy_kind") == "device_tenancy":
                    row = conn.execute(
                        "SELECT * FROM task_device_tenancy WHERE uuid=?",
                        (item["uuid"],),
                    ).fetchone()
                    if row["state"] != "released":
                        if row["update_time"] != item["update_time"]:
                            raise StoreConflict("occupancy_version_conflict")
                        conn.execute(
                            "UPDATE task_device_tenancy SET state='released',released_by_job_uuid=acquired_by_job_uuid,released_at=?,update_time=? WHERE uuid=?",
                            (now, now, item["uuid"]),
                        )
                        self._emit(conn, item["workflow_task_uuid"])
                    continue
                row = conn.execute(
                    "SELECT * FROM execution_lock_lease WHERE uuid=?", (item["uuid"],)
                ).fetchone()
                if row["state"] == "released":
                    continue
                if (
                    row["claim_uuid"] != item["claim_uuid"]
                    or row["fencing_token"] != item["fencing_token"]
                ):
                    raise StoreConflict("occupancy_version_conflict")
                conn.execute(
                    "UPDATE execution_lock_lease SET state='released',released_at=?,update_time=? WHERE uuid=?",
                    (now, now, item["uuid"]),
                )
                if (
                    conn.execute(
                        "SELECT 1 FROM execution_lock_lease WHERE claim_uuid=? AND state!='released' AND deleted_at IS NULL",
                        (item["claim_uuid"],),
                    ).fetchone()
                    is None
                ):
                    conn.execute(
                        "UPDATE execution_claim SET state='released',released_at=?,update_time=? WHERE claim_uuid=?",
                        (now, now, item["claim_uuid"]),
                    )
                for job in conn.execute(
                    "SELECT uuid,control_data FROM workflow_node_job WHERE workflow_task_uuid=? AND deleted_at IS NULL",
                    (item["workflow_task_uuid"],),
                ):
                    data = json.loads(job["control_data"])
                    same_fence = any(
                        fence.get("lock_key") == item["lock_key"]
                        and fence.get("fencing_token") == item["fencing_token"]
                        for fence in data.get("dispatch_payload", {}).get("fences", [])
                    )
                    if job["uuid"] != item["workflow_node_job_uuid"] and not same_fence:
                        continue
                    data["operator_released_keys"] = sorted(
                        set(data.get("operator_released_keys", [])) | {item["lock_key"]}
                    )
                    conn.execute(
                        "UPDATE workflow_node_job SET control_data=? WHERE uuid=?",
                        (_json(data), job["uuid"]),
                    )
                record_execution_lock_operator_action(
                    conn,
                    task_uuid=item["workflow_task_uuid"],
                    job_uuid=item["workflow_node_job_uuid"],
                    lease_uuid=item["uuid"],
                    claim_uuid=item["claim_uuid"],
                    expected_claim_uuid=item["claim_uuid"],
                    expected_fencing_token=item["fencing_token"],
                    reason=command["result"]["reason"],
                    physical_settlement_confirmed=False,
                    result="released",
                    released_lock_uuids=[item["uuid"]],
                    now=now,
                )
                self._emit(conn, item["workflow_task_uuid"])

    def events(self, after: int = 0) -> dict[str, Any]:
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM frontend_event WHERE id>? AND event='station.error_handling.changed' ORDER BY id LIMIT 200",
                (after,),
            ).fetchall()
            return {
                "items": [
                    {
                        "id": row["id"],
                        "event": row["event"],
                        "data": json.loads(row["data"]),
                    }
                    for row in rows
                ],
                "next_cursor": rows[-1]["id"] if rows else after,
            }

    @staticmethod
    def _decision(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for field in ("meta_data", "options", "selected_option"):
            result[field] = json.loads(result[field])
        result["decision_version"] = result["revision"]
        result["source_task_uuid"] = result["workflow_task_uuid"]
        result["source_job_uuid"] = result["workflow_node_job_uuid"]
        return result

    def _emit(self, conn: sqlite3.Connection, task_uuid: str | None = None) -> None:
        now = utc_now()
        append_frontend_event(
            conn,
            event="station.error_handling.changed",
            data={"station_id": self.station_id},
            now=now,
        )
        if task_uuid:
            append_frontend_event(
                conn,
                event="workflow.runtime.changed",
                data={"workflow_task_uuid": task_uuid},
                now=now,
            )

    def _check_station(
        self, conn: sqlite3.Connection, request: dict[str, Any]
    ) -> dict[str, Any]:
        station = self._snapshot(conn)
        if request.get("expected_station_version") != station["station_version"]:
            raise StoreConflict("station_version_conflict")
        if (
            "observed_error_epoch" in request
            and request["observed_error_epoch"] != station["error_epoch"]
        ):
            raise StoreConflict("station_version_conflict")
        return station

    def _read_decision(self, conn: sqlite3.Connection, identity: str) -> sqlite3.Row:
        row = conn.execute(
            """SELECT * FROM workflow_intervention WHERE decision_id=?
            AND station_id=? AND decision_kind='edge_node_error'""",
            (identity, self.station_id),
        ).fetchone()
        if row is None:
            raise StoreNotFound("错误决定不存在")
        return row

    def _read_session(self, conn: sqlite3.Connection, identity: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM workflow_error_session WHERE session_id=? AND station_id=?",
            (identity, self.station_id),
        ).fetchone()
        if row is None:
            raise StoreNotFound("处置会话不存在")
        return row

    def _command_result(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        return {
            "command_uuid": row["command_uuid"],
            "status": row["status"],
            "result": json.loads(row["snapshot_json"]),
            "station": self._snapshot(conn),
        }

    def _existing(
        self,
        conn: sqlite3.Connection,
        operation: str,
        key: str,
        request: dict[str, Any],
        operator: str,
    ) -> dict[str, Any] | None:
        if not key.strip() or len(key) > 255 or not operator.strip():
            raise ValueError("缺少有效幂等键或操作者")
        row = conn.execute(
            """SELECT * FROM workflow_station_control_command
            WHERE station_id=? AND operation=? AND idempotency_key=?""",
            (self.station_id, operation, key),
        ).fetchone()
        if row is None:
            return None
        fingerprint = hashlib.sha256(_json(request).encode()).hexdigest()
        if row["request_hash"] != fingerprint or row["operator_id"] != operator:
            raise StoreConflict("idempotency_conflict")
        return self._command_result(conn, row)

    def _save_command(
        self,
        conn: sqlite3.Connection,
        *,
        operation: str,
        key: str,
        request: dict[str, Any],
        operator: str,
        result: dict[str, Any],
        applied: bool,
    ) -> dict[str, Any]:
        identity, now = str(uuid4()), utc_now()
        conn.execute(
            """UPDATE workflow_station_control SET version=version+1,
            last_control_command_uuid=?,updated_at=? WHERE station_id=?""",
            (identity, now, self.station_id),
        )
        station = self._snapshot(conn)
        conn.execute(
            """INSERT INTO workflow_station_control_command(command_uuid,station_id,operation,
            idempotency_key,request_hash,expected_version,applied_version,error_epoch,operator_id,
            operator_confirmed,snapshot_json,status,created_at,applied_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                identity,
                self.station_id,
                operation,
                key,
                hashlib.sha256(_json(request).encode()).hexdigest(),
                request.get("expected_station_version", station["station_version"] - 1),
                station["station_version"],
                station["error_epoch"],
                operator,
                int(request.get("operator_confirmed") is True),
                _json(result),
                "APPLIED" if applied else "PENDING",
                now,
                now if applied else None,
            ),
        )
        task_uuid = result.get("execution_task_uuid") or result.get("source_task_uuid")
        action = result.get("action")
        command_type = {
            "retry_current_node": "retry_node",
            "cancel_task": "cancel",
        }.get(action)
        if operation.startswith("manual:"):
            command_type = "execute_manual_action"
        elif operation.startswith("stop:"):
            command_type = "stop_manual_action"
        elif operation.startswith("inventory:"):
            command_type = "inventory_correction"
        elif operation.startswith("resource:"):
            command_type = "resource_disposition"
        if command_type and task_uuid:
            conn.execute(
                """INSERT INTO workflow_task_command(uuid,create_time,update_time,meta_data,
                workflow_task_uuid,type,idempotency_key,status,result,trace_context,consumed_at)
                VALUES(?,?,?,?,?,?,?, ?,?,'{}',?)""",
                (
                    identity,
                    now,
                    now,
                    _json({"station_command_uuid": identity}),
                    task_uuid,
                    command_type,
                    f"station:{identity}",
                    "succeeded" if applied else "pending",
                    _json(result),
                    now if applied else None,
                ),
            )
        self._emit(conn, result.get("source_task_uuid"))
        return self._command_result(
            conn,
            conn.execute(
                "SELECT * FROM workflow_station_control_command WHERE command_uuid=?",
                (identity,),
            ).fetchone(),
        )

    @staticmethod
    def _confirm_resume(station: dict[str, Any], request: dict[str, Any]) -> None:
        if (
            request.get("operator_confirmed") is not True
            or request.get("confirm_snapshot_id") != station["snapshot_id"]
        ):
            raise StoreConflict("operator_confirmation_required")

    def decide(
        self,
        decision_id: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """先保存选择和任务意图；实际应用成功后才结束会话和恢复调度。"""
        operation = f"decision:{decision_id}"
        with self.lock, self.store.transaction() as conn:
            existing = self._existing(
                conn, operation, idempotency_key, request, operator_id
            )
            if existing is not None:
                return existing
            station = self._check_station(conn, request)
            decision = self._read_decision(conn, decision_id)
            if (
                request.get("expected_decision_version") != decision["revision"]
                or decision["stage"] != "DECISION_REQUIRED"
            ):
                raise StoreConflict("decision_conflict")
            action = request.get("action")
            if action not in {option["id"] for option in OPTIONS}:
                raise ValueError("不支持的错误处理动作")
            result = {
                "decision_id": decision_id,
                "source_task_uuid": decision["workflow_task_uuid"],
                "source_job_uuid": decision["workflow_node_job_uuid"],
                "action": action,
            }
            now = utc_now()
            if action == "enter_manual_handling":
                if station["active_session_id"] or decision["session_id"]:
                    raise StoreConflict("active_session_conflict")
                session_id = str(uuid4())
                conn.execute(
                    """INSERT INTO workflow_error_session(session_id,station_id,decision_id,stage,created_by,created_at)
                    VALUES(?,?,?,'MANUAL_HANDLING',?,?)""",
                    (session_id, self.station_id, decision_id, operator_id, now),
                )
                conn.execute(
                    """UPDATE workflow_intervention SET stage='MANUAL_HANDLING',initial_action=?,
                    session_id=?,revision=revision+1,update_time=? WHERE decision_id=?""",
                    (action, session_id, now, decision_id),
                )
                conn.execute(
                    "UPDATE workflow_station_control SET mode='PAUSED',active_session_id=? WHERE station_id=?",
                    (session_id, self.station_id),
                )
                result["session_id"] = session_id
            else:
                if decision["session_id"]:
                    session = self._read_session(conn, decision["session_id"])
                    if (
                        request.get("session_id") != session["session_id"]
                        or request.get("expected_session_version") != session["version"]
                        or session["stage"] != "DECISION_REQUIRED"
                    ):
                        raise StoreConflict("session_version_conflict")
                elif station["active_session_id"]:
                    raise StoreConflict("active_session_conflict")
                if request.get("resume_requested", False):
                    self._confirm_resume(station, request)
                result["resume_requested"] = request.get("resume_requested") is True
                if action == "retry_current_node":
                    result["retry_job_uuid"] = self._create_retry(conn, decision)
                else:
                    # 取消意图先冻结本任务，物理停止请求由调度桥在事务外应用。
                    conn.execute(
                        "UPDATE workflow_task SET status='canceling',update_time=? WHERE uuid=? AND status IN ('pending','running')",
                        (now, decision["workflow_task_uuid"]),
                    )
                conn.execute(
                    """UPDATE workflow_intervention SET stage='APPLYING',status='selected',
                    initial_action=COALESCE(initial_action,?),final_action=?,revision=revision+1,update_time=?,
                    decided_at=?,decision_idempotency_key=? WHERE decision_id=?""",
                    (action, action, now, now, idempotency_key, decision_id),
                )
            return self._save_command(
                conn,
                operation=operation,
                key=idempotency_key,
                request=request,
                operator=operator_id,
                result=result,
                applied=action == "enter_manual_handling",
            )

    def retry_ancestor_ids(self, job_uuid: str) -> set[str]:
        """读取本次重试的来源链，只允许同任务同一运行节点的历史尝试。"""
        job = self.store.get_job(job_uuid)
        ancestors: set[str] = set()
        current = job
        while current.get("retry_of_job_uuid"):
            identity = str(current["retry_of_job_uuid"])
            if identity in ancestors:
                raise StoreConflict("重试来源链存在循环")
            current = self.store.get_job(identity)
            if (
                current["workflow_task_uuid"] != job["workflow_task_uuid"]
                or current["workflow_node_uuid"] != job["workflow_node_uuid"]
            ):
                raise StoreConflict("重试来源链身份不一致")
            ancestors.add(identity)
        return ancestors

    def _create_retry(self, conn: sqlite3.Connection, decision: sqlite3.Row) -> str:
        old = conn.execute(
            "SELECT * FROM workflow_node_job WHERE uuid=?",
            (decision["workflow_node_job_uuid"],),
        ).fetchone()
        task = conn.execute(
            "SELECT status FROM workflow_task WHERE uuid=?",
            (old["workflow_task_uuid"],),
        ).fetchone()
        if not old["current_attempt"] or task["status"] not in {"pending", "running"}:
            raise StoreConflict("decision_conflict")
        identity, now = str(uuid4()), utc_now()
        conn.execute(
            "UPDATE workflow_node_job SET current_attempt=0 WHERE uuid=?",
            (old["uuid"],),
        )
        control = json.loads(old["control_data"])
        # 仅继承节点/循环身份，执行 Claim、结果和取消状态均属于旧 attempt。
        control = {
            key: value
            for key, value in control.items()
            if key in {"control_path", "iteration_index", "runtime_node_id"}
        }
        conn.execute(
            """INSERT INTO workflow_node_job(uuid,create_time,update_time,meta_data,
            workflow_task_uuid,workflow_node_uuid,material_uuid,feedback_sequence,topological_index,
            executor_kind,execution_policy,execution_timeout_seconds,status,attempt,param,
            feedback_data,return_info,control_data,error_info,retry_of_job_uuid,definition_fingerprint,edge_command_uuid)
            VALUES(?,?,?,?,?,?,?,0,?,?,?,?,'pending',?,?,'{}','{}',?,'[]',?,?,?)""",
            (
                identity,
                now,
                now,
                old["meta_data"],
                old["workflow_task_uuid"],
                old["workflow_node_uuid"],
                old["material_uuid"],
                old["topological_index"],
                old["executor_kind"],
                old["execution_policy"],
                old["execution_timeout_seconds"],
                old["attempt"] + 1,
                old["param"],
                _json(control),
                old["uuid"],
                old["definition_fingerprint"],
                str(uuid4()),
            ),
        )
        return identity

    def complete(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """关闭新手动提交并返回同一会话的最终选择阶段，工站保持暂停。"""
        return self._session_transition(
            session_id,
            request,
            idempotency_key=idempotency_key,
            operator_id=operator_id,
            returning=False,
        )

    def return_to_manual(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """最终决定前返回手动处理；不复活已撤销的命令。"""
        return self._session_transition(
            session_id,
            request,
            idempotency_key=idempotency_key,
            operator_id=operator_id,
            returning=True,
        )

    def _session_transition(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        idempotency_key: str,
        operator_id: str,
        returning: bool,
    ) -> dict[str, Any]:
        operation = f"{'return' if returning else 'complete'}:{session_id}"
        with self.lock, self.store.transaction() as conn:
            existing = self._existing(
                conn, operation, idempotency_key, request, operator_id
            )
            if existing is not None:
                return existing
            station = self._check_station(conn, request)
            session = self._read_session(conn, session_id)
            expected_stage = "DECISION_REQUIRED" if returning else "MANUAL_HANDLING"
            if (
                station["active_session_id"] != session_id
                or session["stage"] != expected_stage
                or request.get("expected_session_version") != session["version"]
            ):
                raise StoreConflict("session_version_conflict")
            decision = self._read_decision(conn, session["decision_id"])
            if decision["stage"] == "APPLYING":
                raise StoreConflict("decision_conflict")
            target = "MANUAL_HANDLING" if returning else "DECISION_REQUIRED"
            now = utc_now()
            conn.execute(
                """UPDATE workflow_error_session SET stage=?,version=version+1,summary=?,
                completed_by=?,completed_at=? WHERE session_id=?""",
                (
                    target,
                    request.get("summary", session["summary"]),
                    operator_id,
                    now,
                    session_id,
                ),
            )
            conn.execute(
                "UPDATE workflow_intervention SET stage=?,revision=revision+1,update_time=? WHERE decision_id=?",
                (target, now, session["decision_id"]),
            )
            result = {
                "session_id": session_id,
                "source_task_uuid": decision["workflow_task_uuid"],
            }
            if not returning:
                canceled = []
                for job in conn.execute(
                    """SELECT * FROM workflow_node_job WHERE execution_source='error_manual'
                    AND json_extract(meta_data,'$.session_id')=? AND start_state='NOT_STARTED'
                    AND status IN ('pending','dispatched','running')""",
                    (session_id,),
                ).fetchall():
                    from unilabos.workflow.job_evidence import record_job_result

                    record_job_result(
                        conn,
                        job_row=job,
                        outcome="canceled",
                        return_info={"cancel_reason": "local_no_send_proof"},
                        error_info=[],
                    )
                    conn.execute(
                        """UPDATE workflow_node_job SET status='canceled' ,finished_at=?,update_time=?,
                        return_info=?,control_data=? WHERE uuid=?""",
                        (
                            now,
                            now,
                            _json({"cancel_reason": "local_no_send_proof"}),
                            _json({"canceled_before_start": True}),
                            job["uuid"],
                        ),
                    )
                    conn.execute(
                        "UPDATE workflow_task SET status='canceled',finished_at=?,update_time=? WHERE uuid=?",
                        (now, now, job["workflow_task_uuid"]),
                    )
                    canceled.append(job["edge_command_uuid"])
                    self._emit(conn, job["workflow_task_uuid"])
                result["canceled_before_start"] = canceled
            return self._save_command(
                conn,
                operation=operation,
                key=idempotency_key,
                request=request,
                operator=operator_id,
                result=result,
                applied=True,
            )

    def mark_applied(self, command_uuid: str) -> dict[str, Any]:
        """由调度桥在任务意图实际应用后调用；新错误版本不允许被旧恢复覆盖。"""
        with self.lock, self.store.transaction() as conn:
            command = conn.execute(
                "SELECT * FROM workflow_station_control_command WHERE command_uuid=? AND station_id=?",
                (command_uuid, self.station_id),
            ).fetchone()
            if command is None:
                raise StoreNotFound("控制命令不存在")
            if command["status"] != "PENDING":
                return self._command_result(conn, command)
            result = json.loads(command["snapshot_json"])
            now = utc_now()
            conn.execute(
                "UPDATE workflow_task_command SET status='succeeded',consumed_at=?,update_time=? WHERE uuid=?",
                (now, now, command_uuid),
            )
            if not command["operation"].startswith("decision:"):
                conn.execute(
                    "UPDATE workflow_station_control_command SET status='APPLIED',applied_at=? WHERE command_uuid=?",
                    (now, command_uuid),
                )
                self._emit(conn, result.get("source_task_uuid"))
                return self._command_result(
                    conn,
                    conn.execute(
                        "SELECT * FROM workflow_station_control_command WHERE command_uuid=?",
                        (command_uuid,),
                    ).fetchone(),
                )
            decision = self._read_decision(conn, result["decision_id"])
            conn.execute(
                "UPDATE workflow_intervention SET stage='RESOLVED',update_time=?,revision=revision+1 WHERE decision_id=?",
                (now, decision["decision_id"]),
            )
            if decision["session_id"]:
                conn.execute(
                    "UPDATE workflow_error_session SET stage='RESOLVED',resolved_at=?,version=version+1 WHERE session_id=?",
                    (now, decision["session_id"]),
                )
                conn.execute(
                    "UPDATE workflow_station_control SET active_session_id=NULL WHERE station_id=? AND active_session_id=?",
                    (self.station_id, decision["session_id"]),
                )
            if result.get("action") == "cancel_task":
                conn.execute(
                    """UPDATE workflow_intervention SET stage='RESOLVED',final_action='cancel_task',revision=revision+1,update_time=?
                    WHERE workflow_task_uuid=? AND decision_kind='edge_node_error' AND stage!='RESOLVED'""",
                    (now, decision["workflow_task_uuid"]),
                )
            unresolved = conn.execute(
                """SELECT 1 FROM workflow_intervention WHERE workflow_task_uuid=?
                AND decision_kind='edge_node_error' AND stage!='RESOLVED' LIMIT 1""",
                (decision["workflow_task_uuid"],),
            ).fetchone()
            if unresolved is None:
                conn.execute(
                    """UPDATE workflow_task SET control_status=CASE WHEN execution_mode IN ('step','switching_to_step') THEN 'paused' ELSE 'active' END,update_time=?
                    WHERE uuid=? AND control_status='error_waiting'""",
                    (now, decision["workflow_task_uuid"]),
                )
            station = self._snapshot(conn)
            if (
                result.get("resume_requested")
                and station["station_version"] == command["applied_version"]
                and station["error_epoch"] == command["error_epoch"]
            ):
                conn.execute(
                    "UPDATE workflow_station_control SET mode='RUNNING' WHERE station_id=?",
                    (self.station_id,),
                )
            conn.execute(
                "UPDATE workflow_station_control SET version=version+1,updated_at=? WHERE station_id=?",
                (now, self.station_id),
            )
            conn.execute(
                "UPDATE workflow_station_control_command SET status='APPLIED',applied_at=? WHERE command_uuid=?",
                (now, command_uuid),
            )
            self._emit(conn, decision["workflow_task_uuid"])
            return self._command_result(
                conn,
                conn.execute(
                    "SELECT * FROM workflow_station_control_command WHERE command_uuid=?",
                    (command_uuid,),
                ).fetchone(),
            )

    def resume(
        self, request: dict[str, Any], *, idempotency_key: str, operator_id: str
    ) -> dict[str, Any]:
        """人工确认恢复；其他已知错误、在途和未知结果的数量不是门禁。"""
        with self.lock, self.store.transaction() as conn:
            existing = self._existing(
                conn, "resume", idempotency_key, request, operator_id
            )
            if existing is not None:
                return existing
            station = self._check_station(conn, request)
            self._confirm_resume(station, request)
            if station["active_session_id"]:
                raise StoreConflict("active_session_conflict")
            conn.execute(
                "UPDATE workflow_station_control SET mode='RUNNING' WHERE station_id=?",
                (self.station_id,),
            )
            return self._save_command(
                conn,
                operation="resume",
                key=idempotency_key,
                request=request,
                operator=operator_id,
                result={"resume_requested": True},
                applied=True,
            )

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.lock, self.store.read() as conn:
            session = dict(self._read_session(conn, session_id))
            session["decision"] = self._decision(
                self._read_decision(conn, session["decision_id"])
            )
            session["manual_actions"] = self._manual_summaries(conn, session_id)
            return session

    def pending_commands(self) -> list[dict[str, Any]]:
        with self.lock, self.store.read() as conn:
            return [
                self._command_result(conn, row)
                for row in conn.execute(
                    "SELECT * FROM workflow_station_control_command WHERE station_id=? AND status='PENDING' ORDER BY created_at,command_uuid",
                    (self.station_id,),
                )
            ]

    def report_error(
        self, job_uuid: str, *, phase: str, message: str, event_key: str = "execution"
    ) -> dict[str, Any]:
        """同一 job 的未知到明确结果沿用同一错误事实；仅新事实再次暂停。"""
        with self.lock, self.store.transaction() as conn:
            existing = conn.execute(
                """SELECT * FROM workflow_intervention WHERE station_id=?
                AND workflow_node_job_uuid=? AND error_event_key=? AND decision_kind='edge_node_error'""",
                (self.station_id, job_uuid, event_key),
            ).fetchone()
            if existing is not None:
                return self._decision(existing)
            job = conn.execute(
                "SELECT * FROM workflow_node_job WHERE uuid=? AND deleted_at IS NULL",
                (job_uuid,),
            ).fetchone()
            if job is None:
                raise StoreNotFound(f"作业不存在：{job_uuid}")
            task_uuid = job["workflow_task_uuid"]
            task = conn.execute(
                "SELECT * FROM workflow_task WHERE uuid=?", (task_uuid,)
            ).fetchone()
            if job["execution_source"] == "error_manual":
                evidence = json.loads(job["control_data"])
                observed = evidence.get("observed_manual_errors", [])
                mode = conn.execute(
                    "SELECT mode FROM workflow_station_control WHERE station_id=?",
                    (self.station_id,),
                ).fetchone()[0]
                if event_key in observed or mode == "PAUSED":
                    if event_key not in observed:
                        evidence["observed_manual_errors"] = observed + [event_key]
                        conn.execute(
                            "UPDATE workflow_node_job SET control_data=? WHERE uuid=?",
                            (_json(evidence), job_uuid),
                        )
                    return {"suppressed": True, "source_job_uuid": job_uuid}
            now, identity = utc_now(), str(uuid4())
            conn.execute(
                """UPDATE workflow_station_control SET mode='PAUSED', version=version+1,
                error_epoch=error_epoch+1,updated_at=? WHERE station_id=?""",
                (now, self.station_id),
            )
            epoch = conn.execute(
                "SELECT error_epoch FROM workflow_station_control WHERE station_id=?",
                (self.station_id,),
            ).fetchone()[0]
            revision = conn.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM workflow_intervention WHERE workflow_node_job_uuid=?",
                (job_uuid,),
            ).fetchone()[0]
            resume_status = task["control_status"]
            if resume_status == "error_waiting":
                previous = conn.execute(
                    """SELECT resume_control_status FROM workflow_intervention
                    WHERE workflow_task_uuid=? AND decision_kind='edge_node_error' ORDER BY opened_at LIMIT 1""",
                    (task_uuid,),
                ).fetchone()
                resume_status = previous[0] if previous else "active"
            conn.execute(
                """INSERT INTO workflow_intervention(uuid,create_time,update_time,meta_data,
                workflow_task_uuid,workflow_node_job_uuid,decision_id,revision,status,options,
                resume_control_status,opened_at,decision_kind,station_id,error_event_key,error_phase,observed_error_epoch)
                VALUES(?,?,?,?,?,?,?,?,'open',?,?,?,'edge_node_error',?,?,?,?)""",
                (
                    identity,
                    now,
                    now,
                    _json(
                        {
                            "message": message,
                            "source_node_id": (
                                json.loads(job["control_data"]).get("runtime_node_id")
                                or job["workflow_node_uuid"]
                            ),
                        }
                    ),
                    task_uuid,
                    job_uuid,
                    identity,
                    revision,
                    _json(OPTIONS),
                    resume_status,
                    now,
                    self.station_id,
                    event_key,
                    phase,
                    epoch,
                ),
            )
            conn.execute(
                """UPDATE workflow_task SET status='running',control_status='error_waiting',update_time=?
                WHERE uuid=? AND status IN ('pending','running')""",
                (now, task_uuid),
            )
            if phase == "prepare" and job["status"] == "pending":
                from unilabos.workflow.job_evidence import record_job_result

                errors = [{"code": "node_preparation_failed", "message": message}]
                record_job_result(
                    conn,
                    job_row=job,
                    outcome="failed",
                    return_info={},
                    error_info=errors,
                )
                conn.execute(
                    "UPDATE workflow_node_job SET status='failed',error_info=?,finished_at=?,update_time=? WHERE uuid=?",
                    (_json(errors), now, now, job_uuid),
                )
            self._emit(conn, task_uuid)
            return self._decision(
                conn.execute(
                    "SELECT * FROM workflow_intervention WHERE uuid=?", (identity,)
                ).fetchone()
            )
