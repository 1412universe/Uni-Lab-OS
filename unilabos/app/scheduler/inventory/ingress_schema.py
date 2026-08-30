"""工站入口预留的库存数据库增量结构。"""

from __future__ import annotations

import sqlite3

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS station_ingress_reservation (
    uuid TEXT PRIMARY KEY NOT NULL,
    create_time DATETIME NOT NULL,
    update_time DATETIME NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    backend_task_uuid TEXT,
    invocation_key TEXT,
    carrier_material_uuid TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('reserved', 'in_transit', 'received', 'expired', 'canceled')
    ),
    expires_at DATETIME NOT NULL,
    transport_started_at DATETIME,
    received_at DATETIME,
    canceled_at DATETIME,
    cancel_reason TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    FOREIGN KEY (carrier_material_uuid) REFERENCES material (uuid)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_station_ingress_state_expiry
    ON station_ingress_reservation (state, expires_at, uuid);
CREATE INDEX IF NOT EXISTS idx_station_ingress_backend_task
    ON station_ingress_reservation (backend_task_uuid, invocation_key);

CREATE TABLE IF NOT EXISTS station_ingress_reservation_site (
    reservation_uuid TEXT PRIMARY KEY NOT NULL,
    site_uuid TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (reservation_uuid) REFERENCES station_ingress_reservation (uuid)
        ON DELETE RESTRICT,
    FOREIGN KEY (site_uuid) REFERENCES site (uuid) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_station_ingress_active_site
    ON station_ingress_reservation_site (site_uuid) WHERE active = 1;
"""


def migrate_ingress_schema(connection: sqlite3.Connection) -> None:
    """幂等创建入口预留表。

    参数：``connection`` 是库存权威持有的 SQLite 连接。返回：无。异常：结构
    不兼容或外键约束失败时原样传播，调用方不得在半迁移数据库上继续启动。
    """

    connection.executescript(_SCHEMA)


__all__ = ["migrate_ingress_schema"]
