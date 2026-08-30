"""目标 Edge 入口库位预留状态机测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from unilabos.app.scheduler.inventory.api import create_app
from unilabos.app.scheduler.inventory.backend_contract import BackendResourceService
from unilabos.app.scheduler.inventory.ingress import (
    IngressReservationError,
    StationIngressAuthority,
)
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.station_resource import (
    SqliteStationResourceInventory,
    StationResourceError,
    TargetSiteRequest,
)
from unilabos.app.scheduler.inventory.store import InventoryStore


@pytest.fixture
def ingress_inventory(tmp_path):
    """构造两个有序入口库位与两个可搬运载体。

    参数：``tmp_path`` 提供隔离数据库目录。返回：库存、可控时钟、入口权威和
    身份集合。异常：建模失败原样传播；fixture 结束后关闭库存连接。
    """

    store = InventoryStore(str(tmp_path / "ingress.db"))
    backend = BackendResourceService(store)
    templates = backend.sync_resource_templates(
        [
            {
                "id": "test.ingress-rack",
                "display_name": "入口架",
                "registry_type": "resource",
                "class": {},
            },
            {
                "id": "test.carrier",
                "display_name": "可搬运载体",
                "registry_type": "material",
                "class": {},
            },
        ]
    )["templates"]
    template_by_name = {row["name"]: row["uuid"] for row in templates}
    rack = backend.create_material(
        {
            "resource_template_uuid": template_by_name["test.ingress-rack"],
            "barcode": "INGRESS-RACK",
            "name": "工站入口",
        }
    )
    carriers = [
        backend.create_material(
            {
                "resource_template_uuid": template_by_name["test.carrier"],
                "barcode": f"CARRIER-{index}",
                "name": f"载体 {index}",
            }
        )
        for index in (1, 2)
    ]
    site_slow = str(uuid4())
    site_first = str(uuid4())
    timestamp = "2026-08-30T00:00:00+00:00"
    with store.transaction() as connection:
        for site_uuid, name, order in (
            (site_slow, "IN-B", 20),
            (site_first, "IN-A", 10),
        ):
            connection.execute(
                """
                INSERT INTO site(
                    uuid,create_time,update_time,meta_data,material_uuid,name,
                    sort_order,allowed_resource_template_uuids,
                    occupied_material_uuid,position_x,position_y,position_z,
                    depth,length,width
                ) VALUES (?,?,?,'{}',?,?,?,?,NULL,0,0,0,0,0,0)
                """,
                (
                    site_uuid,
                    timestamp,
                    timestamp,
                    rack["uuid"],
                    name,
                    order,
                    json.dumps([template_by_name["test.carrier"]]),
                ),
            )
    clock: dict[str, datetime] = {"now": datetime(2026, 8, 30, tzinfo=timezone.utc)}
    authority = StationIngressAuthority(
        store,
        edge_id="edge-test",
        lab_id="lab-test",
        now=lambda: clock["now"],
    )
    try:
        yield (
            store,
            clock,
            authority,
            {
                "rack": rack["uuid"],
                "carriers": [row["uuid"] for row in carriers],
                "sites": (site_slow, site_first),
            },
        )
    finally:
        store.close()


def _reserve(
    authority: StationIngressAuthority,
    identities: dict[str, Any],
    *,
    index: int = 0,
    key: str = "reserve-1",
    ttl_seconds: int = 30,
) -> dict[str, Any]:
    """按逆序候选提交入口预留，返回持久投影。"""

    return authority.reserve(
        idempotency_key=key,
        carrier_material_uuid=identities["carriers"][index],
        candidate_site_uuids=identities["sites"],
        ttl_seconds=ttl_seconds,
        backend_task_uuid="backend-task-1",
        invocation_key=f"invocation-{index}",
    )


def test_ingress_selects_first_available_site_and_replays_idempotently(
    ingress_inventory,
) -> None:
    """入口组按库位顺序选择，活动预留互斥且命令重放不重复写事件。"""

    store, _clock, authority, identities = ingress_inventory
    first = _reserve(authority, identities)
    event_count = len(store.pending_outbox(0, 100))
    replay = _reserve(authority, identities)
    second = _reserve(authority, identities, index=1, key="reserve-2")

    assert first["site_uuid"] == identities["sites"][1]
    assert replay == first
    assert second["site_uuid"] == identities["sites"][0]
    assert len(store.pending_outbox(0, 100)) == event_count + 1
    with pytest.raises(IngressReservationError, match="没有可接收"):
        authority.reserve(
            idempotency_key="reserve-3",
            carrier_material_uuid=identities["carriers"][0],
            candidate_site_uuids=identities["sites"],
            ttl_seconds=30,
        )


def test_reserved_ingress_expires_and_releases_site(ingress_inventory) -> None:
    """尚未运输的预留到期后释放库位，另一命令可立即重新选择该位置。"""

    _store, clock, authority, identities = ingress_inventory
    first = _reserve(authority, identities, ttl_seconds=10)
    clock["now"] += timedelta(seconds=11)

    assert authority.get(first["uuid"])["state"] == "expired"
    replacement = _reserve(authority, identities, index=1, key="replacement")
    assert replacement["site_uuid"] == first["site_uuid"]


def test_normal_job_target_selection_never_steals_ingress_reservation(
    ingress_inventory,
) -> None:
    """普通 DAG 节点跳过 AGV 已预留入口；全部预留时失败关闭。

    参数：``ingress_inventory`` 提供共享同一 SQLite 权威的入口与节点选择模块。
    返回：无。异常：若两个深模块看到不同占用事实，断言明确暴露回归。
    """

    store, _clock, authority, identities = ingress_inventory
    inventory = SqliteStationResourceInventory(store, move_material=lambda **_: {})
    first = _reserve(authority, identities)

    selected = inventory.resolve_target_site(
        TargetSiteRequest(
            owner_material_uuid=identities["rack"],
            equivalent_site_uuids=identities["sites"],
            occupant_material_uuid=identities["carriers"][1],
        )
    )
    assert selected.uuid != first["site_uuid"]

    _reserve(authority, identities, index=1, key="reserve-second")
    with pytest.raises(StationResourceError, match="没有可接收"):
        inventory.resolve_target_site(
            TargetSiteRequest(
                owner_material_uuid=identities["rack"],
                equivalent_site_uuids=identities["sites"],
                occupant_material_uuid=identities["carriers"][0],
            )
        )


def test_in_transit_never_expires_and_receive_commits_site_occupancy(
    ingress_inventory,
) -> None:
    """运输中预留跨过 TTL 仍保持，接收时原子形成入口库位物理事实。"""

    store, clock, authority, identities = ingress_inventory
    reserved = _reserve(authority, identities, ttl_seconds=5)
    in_transit = authority.mark_in_transit(reserved["uuid"])
    clock["now"] += timedelta(days=3)

    assert authority.expire_due() == 0
    assert authority.get(reserved["uuid"])["state"] == "in_transit"
    received = authority.receive(reserved["uuid"])
    site = store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (received["site_uuid"],),
    )
    carrier = store.query_one(
        "SELECT parent_uuid FROM material WHERE uuid=?",
        (received["carrier_material_uuid"],),
    )

    assert in_transit["state"] == "in_transit"
    assert received["state"] == "received"
    assert site == {"occupied_material_uuid": identities["carriers"][0]}
    assert carrier == {"parent_uuid": identities["rack"]}


def test_in_transit_requires_manual_cancel_reason(ingress_inventory) -> None:
    """运输中只能由带理由的人工取消释放，且不同理由重放必须冲突。"""

    _store, clock, authority, identities = ingress_inventory
    reserved = _reserve(authority, identities, ttl_seconds=5)
    authority.mark_in_transit(reserved["uuid"])
    clock["now"] += timedelta(days=3)

    with pytest.raises(IngressReservationError, match="reason 不能为空"):
        authority.cancel(reserved["uuid"], reason="")
    canceled = authority.cancel(reserved["uuid"], reason="AGV 人工撤回")
    assert canceled["state"] == "canceled"
    assert canceled["cancel_reason"] == "AGV 人工撤回"
    assert authority.cancel(reserved["uuid"], reason="AGV 人工撤回") == canceled
    with pytest.raises(IngressReservationError, match="另一理由"):
        authority.cancel(reserved["uuid"], reason="另一个原因")


def test_ingress_http_contract_uses_persistent_authority(ingress_inventory) -> None:
    """公开 HTTP 接口只传命令，状态转换和库位事实仍由入口深模块持久化。"""

    store, _clock, _authority, identities = ingress_inventory
    client = TestClient(create_app(InventoryService(store)))
    created_response = client.post(
        "/api/v1/inventory/ingress-reservations",
        json={
            "idempotency_key": "http-reserve",
            "carrier_material_uuid": identities["carriers"][0],
            "candidate_site_uuids": list(identities["sites"]),
            "ttl_seconds": 60,
            "backend_task_uuid": "backend-http",
            "invocation_key": "invocation-http",
        },
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["state"] == "reserved"

    in_transit = client.post(
        f"/api/v1/inventory/ingress-reservations/{created['uuid']}/in-transit"
    )
    received = client.post(
        f"/api/v1/inventory/ingress-reservations/{created['uuid']}/receive"
    )

    assert in_transit.status_code == 200
    assert in_transit.json()["state"] == "in_transit"
    assert received.status_code == 200
    assert received.json()["state"] == "received"
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (created["site_uuid"],),
    ) == {"occupied_material_uuid": identities["carriers"][0]}
