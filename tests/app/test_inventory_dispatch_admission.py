"""库存权威原子派发准入（DispatchPermit）测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unilabos.app.scheduler.inventory.backend_contract import BackendResourceService
from unilabos.app.scheduler.inventory.dispatch_admission import (
    DispatchAdmissionRequest,
    DispatchResource,
    TransferDispatchCondition,
)
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.station_resource import StationResourceError
from unilabos.app.scheduler.inventory.store import InventoryStore

SOURCE_SITE = "10000000-0000-4000-8000-000000000101"
TARGET_SITE = "10000000-0000-4000-8000-000000000102"
GRIPPER_SITE = "10000000-0000-4000-8000-000000000103"


@pytest.fixture()
def station_inventory(
    tmp_path: Path,
) -> tuple[InventoryStore, InventoryService, dict[str, str]]:
    """建立来源设备、目标设备、机械臂、空夹爪与待搬物料事实。

    参数：``tmp_path`` 是隔离数据库目录。返回：库存存储、业务服务和资源身份。
    异常：夹具构造失败原样传播；结束时关闭数据库。
    """

    store = InventoryStore(str(tmp_path / "dispatch-inventory.db"))
    backend = BackendResourceService(store)
    templates = backend.sync_resource_templates(
        [
            {
                "id": "test.dispatch-device",
                "display_name": "测试设备",
                "registry_type": "resource",
                "class": {},
            },
            {
                "id": "test.dispatch-vessel",
                "display_name": "测试容器",
                "registry_type": "material",
                "class": {},
            },
        ]
    )["templates"]
    template_by_name = {item["name"]: item["uuid"] for item in templates}
    identities: dict[str, str] = {}
    for key, barcode in (
        ("source_device", "SOURCE-DEVICE"),
        ("target_device", "TARGET-DEVICE"),
        ("robot", "ROBOT"),
    ):
        material = backend.create_material(
            {
                "resource_template_uuid": template_by_name["test.dispatch-device"],
                "barcode": barcode,
                "name": barcode,
            }
        )
        identities[key] = material["uuid"]
    vessel = backend.create_material(
        {
            "resource_template_uuid": template_by_name["test.dispatch-vessel"],
            "parent_uuid": identities["source_device"],
            "barcode": "VESSEL",
            "name": "待搬容器",
        }
    )
    identities["vessel"] = vessel["uuid"]
    with store.transaction() as connection:
        connection.execute(
            "UPDATE material SET type='device' WHERE uuid IN (?,?,?)",
            (
                identities["source_device"],
                identities["target_device"],
                identities["robot"],
            ),
        )
        for site_uuid, owner_uuid, name, occupant, metadata in (
            (
                SOURCE_SITE,
                identities["source_device"],
                "OUT",
                identities["vessel"],
                {},
            ),
            (TARGET_SITE, identities["target_device"], "IN", None, {}),
            (
                GRIPPER_SITE,
                identities["robot"],
                "GRIPPER",
                None,
                {"unilab": {"resource_role": "robot.gripper"}},
            ),
        ):
            connection.execute(
                """
                INSERT INTO site(
                    uuid,create_time,update_time,meta_data,material_uuid,name,
                    sort_order,allowed_resource_template_uuids,
                    occupied_material_uuid,position_x,position_y,position_z,
                    depth,length,width
                ) VALUES (?,?,?,?,?,?,0,'[]',?,0,0,0,0,0,0)
                """,
                (
                    site_uuid,
                    "2026-08-31T00:00:00Z",
                    "2026-08-31T00:00:00Z",
                    json.dumps(metadata),
                    owner_uuid,
                    name,
                    occupant,
                ),
            )
    service = InventoryService(store)
    try:
        yield store, service, identities
    finally:
        store.close()


def _request(
    identities: dict[str, str],
    *,
    job_uuid: str = "40000000-0000-4000-8000-000000000101",
) -> DispatchAdmissionRequest:
    """构造包含转运全部资源和条件快照的准入请求。

    参数：``identities`` 是夹具资源身份；``job_uuid`` 是竞争作业身份。返回：
    冻结参数哈希、预期变更及完整锁集合。异常：无。
    """

    resources = (
        DispatchResource(
            lock_key=f"/devices/{identities['source_device']}",
            scope="device",
            material_uuid=identities["source_device"],
        ),
        DispatchResource(
            lock_key=f"/devices/{identities['target_device']}",
            scope="device",
            material_uuid=identities["target_device"],
        ),
        DispatchResource(
            lock_key=f"/devices/{identities['robot']}",
            scope="device",
            material_uuid=identities["robot"],
        ),
        DispatchResource(
            lock_key=f"material/{identities['vessel']}/exclusive",
            scope="material",
            material_uuid=identities["vessel"],
        ),
        DispatchResource(
            lock_key=(
                f"material/{identities['source_device']}/site/"
                f"{SOURCE_SITE}/exclusive"
            ),
            scope="material_site",
            material_uuid=identities["source_device"],
            site_uuid=SOURCE_SITE,
        ),
        DispatchResource(
            lock_key=(
                f"material/{identities['target_device']}/site/"
                f"{TARGET_SITE}/exclusive"
            ),
            scope="material_site",
            material_uuid=identities["target_device"],
            site_uuid=TARGET_SITE,
        ),
        DispatchResource(
            lock_key=(
                f"material/{identities['robot']}/site/"
                f"{GRIPPER_SITE}/exclusive"
            ),
            scope="material_site",
            material_uuid=identities["robot"],
            site_uuid=GRIPPER_SITE,
        ),
    )
    return DispatchAdmissionRequest(
        effect_uuid=f"50000000-0000-4000-8000-{job_uuid[-12:]}",
        task_uuid="30000000-0000-4000-8000-000000000101",
        job_uuid=job_uuid,
        attempt=1,
        parameter_hash="sha256:test-parameters",
        expected_change_set={
            "kind": "material_transfer",
            "material_uuid": identities["vessel"],
            "source_site_uuid": SOURCE_SITE,
            "target_site_uuid": TARGET_SITE,
        },
        resources=resources,
        transfer=TransferDispatchCondition(
            material_uuid=identities["vessel"],
            source_owner_material_uuid=identities["source_device"],
            source_site_uuid=SOURCE_SITE,
            target_owner_material_uuid=identities["target_device"],
            target_site_uuid=TARGET_SITE,
            executor_material_uuid=identities["robot"],
            gripper_site_uuid=GRIPPER_SITE,
        ),
    )


def test_transfer_conditions_and_all_claims_commit_in_one_inventory_transaction(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """门禁 7 应一次产生 Claim、全部 Fence 和可审计预期变更。

    参数：``station_inventory`` 提供完整转运事实。返回：无；断言库存库中的
    Claim 与七项 Fence 使用同一身份。异常：准入失败表示完整资源集未被证明。
    """

    store, service, identities = station_inventory
    permit = service.station_resources.acquire_dispatch_permit(_request(identities))

    assert permit.acquired is True
    assert permit.claim_uuid
    assert permit.effect_uuid.startswith("50000000-")
    assert len(permit.fences) == 7
    assert store.query_one(
        "SELECT state,parameter_hash FROM station_execution_claim "
        "WHERE claim_uuid=?",
        (permit.claim_uuid,),
    ) == {"state": "prepared", "parameter_hash": "sha256:test-parameters"}


def test_changed_target_fact_rolls_back_whole_claim(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """目标库位在门禁窗口被占用时不得留下机械臂或设备的部分 Claim。

    参数：``station_inventory`` 提供预先解析过的请求。返回：无；断言库存事实
    改变后抛稳定等待条件，Claim 与 Lease 均为零。异常：仅预期资源条件冲突。
    """

    store, service, identities = station_inventory
    request = _request(identities)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE site SET occupied_material_uuid=? WHERE uuid=?",
            (identities["robot"], TARGET_SITE),
        )

    with pytest.raises(StationResourceError) as raised:
        service.station_resources.acquire_dispatch_permit(request)

    assert raised.value.code == "site_occupied"
    assert store.query_all("SELECT * FROM station_execution_claim") == []
    assert store.query_all("SELECT * FROM station_execution_lock_lease") == []

    # B 的目标条件失败后，C 必须仍能取得机械臂；这直接证明门禁没有发生
    # “先占机械臂、再发现目标库位不满足”的部分取得。
    robot_only = DispatchAdmissionRequest(
        effect_uuid="50000000-0000-4000-8000-000000000103",
        task_uuid="30000000-0000-4000-8000-000000000103",
        job_uuid="40000000-0000-4000-8000-000000000103",
        attempt=1,
        parameter_hash="sha256:robot-only",
        expected_change_set={"kind": "no_inventory_change"},
        resources=(
            DispatchResource(
                lock_key=f"/devices/{identities['robot']}",
                scope="device",
                material_uuid=identities["robot"],
            ),
        ),
    )
    third = service.station_resources.acquire_dispatch_permit(robot_only)

    assert third.acquired is True
    assert len(store.query_all("SELECT * FROM station_execution_claim")) == 1


def test_competing_job_cannot_claim_any_member_of_an_active_resource_set(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """另一个 Task/Job 不能在完整 Claim 存活期间先抢到机械臂。

    参数：``station_inventory`` 提供共享机械臂和位置。返回：无；断言第二次准入
    返回阻塞身份且不创建第二个 Claim。异常：无，资源竞争属于正常等待。
    """

    store, service, identities = station_inventory
    first = service.station_resources.acquire_dispatch_permit(_request(identities))
    second = service.station_resources.acquire_dispatch_permit(
        _request(
            identities,
            job_uuid="40000000-0000-4000-8000-000000000102",
        )
    )

    assert first.acquired is True
    assert second.acquired is False
    assert second.wait_code == "resource_claimed"
    assert second.blocking_job_uuid == "40000000-0000-4000-8000-000000000101"
    assert len(store.query_all("SELECT * FROM station_execution_claim")) == 1


def test_startup_releases_only_unprojected_prepared_permits(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """跨库崩溃恢复只释放尚未投影到工作流库的 prepared Permit。"""

    store, service, identities = station_inventory
    orphan = service.station_resources.acquire_dispatch_permit(_request(identities))
    assert orphan.permit is not None

    released = service.station_resources.release_unprojected_dispatch_permits(
        known_claim_uuids=()
    )

    assert released == (orphan.claim_uuid,)
    assert store.query_one(
        "SELECT state FROM station_execution_claim WHERE claim_uuid=?",
        (orphan.claim_uuid,),
    ) == {"state": "released"}


def test_startup_keeps_prepared_permit_already_projected_to_workflow(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """工作流库已经持有同一 Claim 时，启动恢复不得回收其库存 Permit。"""

    store, service, identities = station_inventory
    projected = service.station_resources.acquire_dispatch_permit(_request(identities))

    released = service.station_resources.release_unprojected_dispatch_permits(
        known_claim_uuids=(projected.claim_uuid,)
    )

    assert released == ()
    assert store.query_one(
        "SELECT state FROM station_execution_claim WHERE claim_uuid=?",
        (projected.claim_uuid,),
    ) == {"state": "prepared"}


def test_released_prepared_permit_can_be_reprepared_for_same_job_attempt(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """门禁后续未通过时，未提交物理边界的同一效果可重新取得新 Fence。"""

    _store, service, identities = station_inventory
    request = _request(identities)
    first = service.station_resources.acquire_dispatch_permit(request)
    service.station_resources.transition_dispatch_permit(
        first.claim_uuid,
        target_state="released",
    )

    replay = service.station_resources.acquire_dispatch_permit(request)

    assert replay.acquired is True
    assert replay.claim_uuid == first.claim_uuid
    assert [fence.fencing_token for fence in replay.fences] == [
        fence.fencing_token + 1 for fence in first.fences
    ]
