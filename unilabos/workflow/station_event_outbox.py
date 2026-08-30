"""工站调度进程向 Backend 投影事实的事务发件箱。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.store import StoreConflict, StoreNotFound, WorkflowStore, utc_now


def _json(value: Any) -> str:
    """把发件箱载荷编码为稳定 JSON 文本。"""

    return encode_json(value, sort_keys=True).decode("utf-8")


def _load(value: str) -> Any:
    """把发件箱中的 JSON 文本解码为 Python 值。"""

    return decode_json_bytes(value.encode("utf-8"))


def ensure_station_event_outbox_schema(connection: sqlite3.Connection) -> None:
    """幂等创建单调有序的工站事件发件箱。

    参数：``connection`` 是调用方持有的工作流 SQLite 事务连接。返回无。异常：
    建表或建索引失败时原样抛出 SQLite 异常，由外层事务整体回滚。
    """

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_station_event_outbox (
            station_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uuid TEXT NOT NULL UNIQUE,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            event_type TEXT NOT NULL,
            aggregate_type TEXT NOT NULL,
            aggregate_uuid TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_uuid TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            acked_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_workflow_station_event_outbox_pending
        ON workflow_station_event_outbox(station_sequence)
        WHERE acked_at IS NULL
        """
    )


def append_station_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_uuid: str,
    source_kind: str,
    source_uuid: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """在业务事实的同一事务内幂等追加一个 Backend 投影事件。

    参数：身份字段共同描述事件来源；``payload`` 是已经在工站库落盘的事实快照。
    返回带 ``event_uuid`` 和单调 ``station_sequence`` 的事件。异常：必填身份为空或
    同一幂等键内容不同抛 ``StoreConflict``；数据库异常由调用方事务处理。
    """

    normalized = {
        "event_type": str(event_type or "").strip(),
        "aggregate_type": str(aggregate_type or "").strip(),
        "aggregate_uuid": str(aggregate_uuid or "").strip(),
        "source_kind": str(source_kind or "").strip(),
        "source_uuid": str(source_uuid or "").strip(),
        "idempotency_key": str(idempotency_key or "").strip(),
    }
    if not all(normalized.values()) or not isinstance(payload, Mapping):
        raise StoreConflict("工站事件身份或载荷不完整")
    payload_json = _json(dict(payload))
    existing = connection.execute(
        """
        SELECT * FROM workflow_station_event_outbox
        WHERE idempotency_key = ?
        """,
        (normalized["idempotency_key"],),
    ).fetchone()
    if existing is not None:
        expected = (
            normalized["event_type"],
            normalized["aggregate_type"],
            normalized["aggregate_uuid"],
            normalized["source_kind"],
            normalized["source_uuid"],
            payload_json,
        )
        actual = (
            existing["event_type"],
            existing["aggregate_type"],
            existing["aggregate_uuid"],
            existing["source_kind"],
            existing["source_uuid"],
            existing["payload"],
        )
        if actual != expected:
            raise StoreConflict(
                f"工站事件幂等键对应的内容冲突：{normalized['idempotency_key']}"
            )
        return _event_row(existing)

    now = utc_now()
    event_uuid = str(uuid4())
    connection.execute(
        """
        INSERT INTO workflow_station_event_outbox(
            event_uuid, create_time, update_time, event_type, aggregate_type,
            aggregate_uuid, source_kind, source_uuid, idempotency_key, payload,
            delivery_attempts, last_attempt_at, acked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)
        """,
        (
            event_uuid,
            now,
            now,
            normalized["event_type"],
            normalized["aggregate_type"],
            normalized["aggregate_uuid"],
            normalized["source_kind"],
            normalized["source_uuid"],
            normalized["idempotency_key"],
            payload_json,
        ),
    )
    row = connection.execute(
        "SELECT * FROM workflow_station_event_outbox WHERE event_uuid = ?",
        (event_uuid,),
    ).fetchone()
    assert row is not None
    return _event_row(row)


class StationEventOutboxStore:
    """提供发件箱拉取、投递尝试和 Backend ACK 的持久接口。"""

    def __init__(self, store: WorkflowStore) -> None:
        """绑定工作流存储并确保发件箱结构存在。

        参数：``store`` 是工站调度进程独占写入的工作流存储。返回无。异常：迁移
        失败时原样抛出，避免在缺少持久投影能力时继续运行。
        """

        self._store = store
        with store.transaction() as connection:
            ensure_station_event_outbox_schema(connection)

    def list_pending(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """按工站序列读取尚未获得 Backend ACK 的事件。

        参数：``limit`` 是本批最大事件数，范围 1..1000。返回按
        ``station_sequence`` 升序的事件。异常：非法批量大小抛 ``StoreConflict``。
        """

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise StoreConflict("发件箱批量大小必须在 1..1000 之间")
        # 发件箱适配器只通过 WorkflowStore 的公开只读接口访问共享连接；不得
        # 依赖其锁和连接私有字段，也不为纯查询获取 SQLite 写锁。
        with self._store.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_station_event_outbox
                WHERE acked_at IS NULL
                ORDER BY station_sequence ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_event_row(row) for row in rows]

    def mark_delivery_attempt(
        self,
        *,
        event_uuid: str,
        station_sequence: int,
    ) -> dict[str, Any]:
        """记录一次 HTTP 投递尝试，但不把事件误标为已发布。

        参数：两个身份必须同时匹配同一事件。返回更新后的事件。异常：事件不存在
        抛 ``StoreNotFound``，身份不一致或已 ACK 后重试抛 ``StoreConflict``。
        """

        with self._store.transaction() as connection:
            row = _required_event(
                connection,
                event_uuid=event_uuid,
                station_sequence=station_sequence,
            )
            if row["acked_at"] is not None:
                raise StoreConflict("已获得 Backend ACK 的事件不能再次投递")
            now = utc_now()
            connection.execute(
                """
                UPDATE workflow_station_event_outbox
                SET delivery_attempts = delivery_attempts + 1,
                    last_attempt_at = ?, update_time = ?
                WHERE event_uuid = ? AND station_sequence = ? AND acked_at IS NULL
                """,
                (now, now, event_uuid, station_sequence),
            )
            return _event_row(
                _required_event(
                    connection,
                    event_uuid=event_uuid,
                    station_sequence=station_sequence,
                )
            )

    def acknowledge(
        self,
        *,
        event_uuid: str,
        station_sequence: int,
    ) -> dict[str, Any]:
        """在收到 Backend 精确 ACK 后标记事件及其来源证据已发布。

        参数：``event_uuid`` 与 ``station_sequence`` 防止错序 ACK 误结算其他事件。
        返回已 ACK 的事件；重复 ACK 幂等返回。异常：事件不存在抛
        ``StoreNotFound``，两个身份不对应时抛 ``StoreConflict``。
        """

        with self._store.transaction() as connection:
            row = _required_event(
                connection,
                event_uuid=event_uuid,
                station_sequence=station_sequence,
            )
            if row["acked_at"] is not None:
                return _event_row(row)
            now = utc_now()
            connection.execute(
                """
                UPDATE workflow_station_event_outbox
                SET acked_at = ?, update_time = ?
                WHERE event_uuid = ? AND station_sequence = ? AND acked_at IS NULL
                """,
                (now, now, event_uuid, station_sequence),
            )
            _mark_source_published(connection, row=row, published_at=now)
            return _event_row(
                _required_event(
                    connection,
                    event_uuid=event_uuid,
                    station_sequence=station_sequence,
                )
            )


def _required_event(
    connection: sqlite3.Connection,
    *,
    event_uuid: str,
    station_sequence: int,
) -> sqlite3.Row:
    """按双身份读取一个发件箱事件并区分不存在与身份冲突。"""

    row = connection.execute(
        "SELECT * FROM workflow_station_event_outbox WHERE event_uuid = ?",
        (str(event_uuid or "").strip(),),
    ).fetchone()
    if row is None:
        raise StoreNotFound(f"station event {event_uuid} not found")
    if int(row["station_sequence"]) != station_sequence:
        raise StoreConflict("工站事件 UUID 与序列不匹配")
    return row


def _mark_source_published(
    connection: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    published_at: str,
) -> None:
    """把 ACK 时间投影到反馈或结果来源行，派发事件无需额外标记。"""

    if row["source_kind"] == "job_feedback":
        connection.execute(
            """
            UPDATE workflow_node_job_feedback_history
            SET published_at = ?, update_time = ?
            WHERE uuid = ? AND published_at IS NULL AND deleted_at IS NULL
            """,
            (published_at, published_at, row["source_uuid"]),
        )
    elif row["source_kind"] == "job_result":
        connection.execute(
            """
            UPDATE workflow_node_job_result
            SET consumed_at = ?, update_time = ?
            WHERE uuid = ? AND consumed_at IS NULL AND deleted_at IS NULL
            """,
            (published_at, published_at, row["source_uuid"]),
        )


def _event_row(row: sqlite3.Row) -> dict[str, Any]:
    """把发件箱 SQLite 行投影为稳定公开对象。"""

    return {
        "event_uuid": row["event_uuid"],
        "station_sequence": int(row["station_sequence"]),
        "event_type": row["event_type"],
        "aggregate_type": row["aggregate_type"],
        "aggregate_uuid": row["aggregate_uuid"],
        "source_kind": row["source_kind"],
        "source_uuid": row["source_uuid"],
        "idempotency_key": row["idempotency_key"],
        "payload": _load(row["payload"]),
        "delivery_attempts": int(row["delivery_attempts"]),
        "last_attempt_at": row["last_attempt_at"],
        "acked_at": row["acked_at"],
        "created_at": row["create_time"],
    }


__all__ = [
    "StationEventOutboxStore",
    "append_station_event",
    "ensure_station_event_outbox_schema",
]
