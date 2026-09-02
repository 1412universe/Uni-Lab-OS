"""库存权威原子派发准入（DispatchPermit）测试。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from unilabos.app.scheduler.inventory.backend_contract import (
    MATERIAL_ACTIVE_CLAIM_CONFLICT,
    BackendContractError,
    BackendResourceService,
)
from unilabos.app.scheduler.inventory.dispatch_admission import (
    AliquotDispatchCondition,
    DispatchAdmissionRequest,
    DispatchAdmissionConflict,
    DispatchResource,
    InventoryMutationConflict,
    OperateInPlaceCondition,
    TransferDispatchCondition,
)
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.station_resource import (
    AliquotReceipt,
    MaterialAliquotCommand,
    MaterialTransferCommand,
    StationResourceError,
    TransferResourceRequest,
)
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.app.scheduler.inventory.content_contract import (
    BackendContainerContentService,
)

SOURCE_SITE = "10000000-0000-4000-8000-000000000101"
TARGET_SITE = "10000000-0000-4000-8000-000000000102"
GRIPPER_SITE = "10000000-0000-4000-8000-000000000103"
TARGET_SITE_FALLBACK = "10000000-0000-4000-8000-000000000104"


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
                f"material/{identities['source_device']}/site/{SOURCE_SITE}/exclusive"
            ),
            scope="material_site",
            material_uuid=identities["source_device"],
            site_uuid=SOURCE_SITE,
        ),
        DispatchResource(
            lock_key=(
                f"material/{identities['target_device']}/site/{TARGET_SITE}/exclusive"
            ),
            scope="material_site",
            material_uuid=identities["target_device"],
            site_uuid=TARGET_SITE,
        ),
        DispatchResource(
            lock_key=(f"material/{identities['robot']}/site/{GRIPPER_SITE}/exclusive"),
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
        "SELECT state,parameter_hash FROM station_execution_claim WHERE claim_uuid=?",
        (permit.claim_uuid,),
    ) == {"state": "prepared", "parameter_hash": "sha256:test-parameters"}


def test_gate7_claims_fallback_site_in_same_inventory_transaction(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """首选库位已有 Claim 时 Gate 7 必须在同一事务回退下一候选。"""

    store, service, identities = station_inventory
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO site(
                uuid,create_time,update_time,meta_data,material_uuid,name,
                sort_order,allowed_resource_template_uuids,
                occupied_material_uuid,position_x,position_y,position_z,
                depth,length,width
            ) VALUES (?,?,?,'{}',?,?,1,'[]',NULL,0,0,0,0,0,0)
            """,
            (
                TARGET_SITE_FALLBACK,
                "2026-08-31T00:00:00Z",
                "2026-08-31T00:00:00Z",
                identities["target_device"],
                "IN-FALLBACK",
            ),
        )
    blocker = DispatchAdmissionRequest(
        effect_uuid="50000000-0000-4000-8000-000000000199",
        task_uuid="30000000-0000-4000-8000-000000000199",
        job_uuid="40000000-0000-4000-8000-000000000199",
        attempt=1,
        parameter_hash="sha256:block-target-b",
        expected_change_set={"kind": "no_inventory_change"},
        resources=(
            DispatchResource(
                lock_key=(
                    f"material/{identities['target_device']}/site/"
                    f"{TARGET_SITE}/exclusive"
                ),
                scope="material_site",
                material_uuid=identities["target_device"],
                site_uuid=TARGET_SITE,
            ),
        ),
    )
    service.station_resources.acquire_dispatch_permit(blocker)

    first = _request(identities)
    target_key = f"material/{identities['target_device']}/site/{TARGET_SITE}/exclusive"
    fallback_resources = tuple(
        resource for resource in first.resources if resource.lock_key != target_key
    ) + (
        DispatchResource(
            lock_key=(
                f"material/{identities['target_device']}/site/"
                f"{TARGET_SITE_FALLBACK}/exclusive"
            ),
            scope="material_site",
            material_uuid=identities["target_device"],
            site_uuid=TARGET_SITE_FALLBACK,
        ),
    )
    fallback = replace(
        first,
        effect_uuid="50000000-0000-4000-8000-000000000102",
        parameter_hash="sha256:fallback-parameters",
        expected_change_set={
            "kind": "material_transfer",
            "material_uuid": identities["vessel"],
            "source_site_uuid": SOURCE_SITE,
            "target_site_uuid": TARGET_SITE_FALLBACK,
        },
        resources=fallback_resources,
        transfer=replace(first.transfer, target_site_uuid=TARGET_SITE_FALLBACK),
    )

    decision = service.station_resources.acquire_dispatch_permit_candidates(
        (first, fallback)
    )

    assert decision.acquired is True
    assert decision.selected_candidate_index == 1
    assert decision.permit is not None
    assert decision.permit.parameter_hash == "sha256:fallback-parameters"
    claimed_sites = {
        row["site_uuid"]
        for row in store.query_all(
            "SELECT site_uuid FROM station_execution_lock_lease "
            "WHERE claim_uuid=? AND site_uuid IS NOT NULL",
            (decision.claim_uuid,),
        )
    }
    assert TARGET_SITE_FALLBACK in claimed_sites
    assert TARGET_SITE not in claimed_sites

    service.station_resources.transition_dispatch_permit(
        decision.claim_uuid,
        target_state="released",
    )
    replay = service.station_resources.acquire_dispatch_permit_candidates(
        (first, fallback)
    )

    assert replay.acquired is True
    assert replay.selected_candidate_index == 1
    assert replay.claim_uuid == decision.claim_uuid
    assert [fence.fencing_token for fence in replay.fences] == [
        fence.fencing_token + 1 for fence in decision.fences
    ]


def test_transfer_endpoint_warehouses_lock_their_device_ancestors(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """仓库拥有库位时，门禁应校验其真实设备祖先而非把仓库当设备。"""

    store, service, identities = station_inventory
    source_warehouse = "20000000-0000-4000-8000-000000000101"
    target_warehouse = "20000000-0000-4000-8000-000000000102"
    with store.transaction() as connection:
        for uuid, parent_uuid, name in (
            (source_warehouse, identities["source_device"], "来源仓库"),
            (target_warehouse, identities["target_device"], "目标仓库"),
        ):
            connection.execute(
                """
                INSERT INTO material(
                    uuid,create_time,update_time,deleted_at,description,meta_data,
                    resource_template_uuid,parent_uuid,class,type,barcode,name,
                    config,data
                )
                SELECT ?,create_time,update_time,NULL,description,'{}',
                       resource_template_uuid,?,class,'warehouse',?,?,'{}','{}'
                FROM material WHERE uuid=?
                """,
                (uuid, parent_uuid, f"WAREHOUSE-{uuid[-3:]}", name, parent_uuid),
            )
        connection.execute(
            "UPDATE site SET material_uuid=? WHERE uuid=?",
            (source_warehouse, SOURCE_SITE),
        )
        connection.execute(
            "UPDATE site SET material_uuid=? WHERE uuid=?",
            (target_warehouse, TARGET_SITE),
        )

    request = _request(identities)
    endpoint_site_keys = {
        f"material/{identities['source_device']}/site/{SOURCE_SITE}/exclusive",
        f"material/{identities['target_device']}/site/{TARGET_SITE}/exclusive",
    }
    resources = tuple(
        resource
        for resource in request.resources
        if resource.lock_key not in endpoint_site_keys
    ) + (
        DispatchResource(
            lock_key=f"material/{source_warehouse}/site/{SOURCE_SITE}/exclusive",
            scope="material_site",
            material_uuid=source_warehouse,
            site_uuid=SOURCE_SITE,
        ),
        DispatchResource(
            lock_key=f"material/{target_warehouse}/site/{TARGET_SITE}/exclusive",
            scope="material_site",
            material_uuid=target_warehouse,
            site_uuid=TARGET_SITE,
        ),
    )
    request = replace(
        request,
        resources=resources,
        transfer=replace(
            request.transfer,
            source_owner_material_uuid=source_warehouse,
            target_owner_material_uuid=target_warehouse,
        ),
    )

    permit = service.station_resources.acquire_dispatch_permit(request)

    assert permit.acquired is True
    assert len(permit.fences) == 7


def test_transfer_expected_change_must_match_condition_snapshot(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """转运条件与预期变化不一致时不得创建 Claim。"""

    store, service, identities = station_inventory
    request = replace(
        _request(identities),
        expected_change_set={
            "kind": "material_transfer",
            "material_uuid": identities["vessel"],
            "source_site_uuid": SOURCE_SITE,
            "target_site_uuid": "wrong-target-site",
        },
    )

    with pytest.raises(DispatchAdmissionConflict) as raised:
        service.station_resources.acquire_dispatch_permit(request)

    assert "ChangeSet" in str(raised.value)
    assert store.query_all("SELECT * FROM station_execution_claim") == []


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
    assert raised.value.resources == (
        {
            "scope": "material_site",
            "material_uuid": identities["target_device"],
            "site_uuid": TARGET_SITE,
        },
    )
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


def test_missing_transfer_source_reports_the_blocked_material(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """来源库位消失时，等待事实应指向待搬物料而不是目标库位。"""

    store, service, identities = station_inventory
    with store.transaction() as connection:
        connection.execute(
            "UPDATE site SET occupied_material_uuid=NULL WHERE uuid=?",
            (SOURCE_SITE,),
        )

    with pytest.raises(StationResourceError) as raised:
        service.station_resources.resolve_transfer_resources(
            TransferResourceRequest(
                resource_material_uuid=identities["vessel"],
                target_site_uuid=TARGET_SITE,
                target_owner_material_uuid=identities["target_device"],
                executor_material_uuid=identities["robot"],
                gripper_site_role="robot.gripper",
                require_device_owners=True,
            )
        )

    assert raised.value.code == "transfer_source_site_missing"
    assert raised.value.resources == (
        {"scope": "material", "material_uuid": identities["vessel"]},
    )


def test_occupied_gripper_reports_the_actual_gripper_site(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """夹爪被占用时，等待事实应指向夹爪库位而不是转运目标库位。"""

    store, service, identities = station_inventory
    with store.transaction() as connection:
        connection.execute(
            "UPDATE site SET occupied_material_uuid=? WHERE uuid=?",
            (identities["target_device"], GRIPPER_SITE),
        )

    with pytest.raises(StationResourceError) as raised:
        service.station_resources.resolve_transfer_resources(
            TransferResourceRequest(
                resource_material_uuid=identities["vessel"],
                target_site_uuid=TARGET_SITE,
                target_owner_material_uuid=identities["target_device"],
                executor_material_uuid=identities["robot"],
                gripper_site_role="robot.gripper",
                require_device_owners=True,
            )
        )

    assert raised.value.code == "gripper_site_occupied"
    assert raised.value.resources == (
        {
            "scope": "material_site",
            "material_uuid": identities["robot"],
            "site_uuid": GRIPPER_SITE,
        },
    )


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


def test_public_site_placement_cannot_bypass_active_claim(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """公共 Backend 物料接口不得在活动 Claim 存活时移除或改放物料。

    参数：``station_inventory`` 提供已占用来源库位。返回：无；断言公共
    ``update_material(site_placement=remove)`` 在同一库存事务内发现活动 Lease，
    返回稳定冲突码且来源占用不变。异常：未阻止时测试保持 RED。
    """

    store, service, identities = station_inventory
    permit = service.station_resources.acquire_dispatch_permit(_request(identities))
    assert permit.acquired is True

    with pytest.raises(BackendContractError) as raised:
        BackendResourceService(store).update_material(
            identities["vessel"],
            {"site_placement": {"action": "remove"}},
        )

    assert raised.value.code == MATERIAL_ACTIVE_CLAIM_CONFLICT
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (SOURCE_SITE,),
    ) == {"occupied_material_uuid": identities["vessel"]}


def test_legacy_inventory_move_cannot_bypass_active_claim(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """旧 InventoryService 写入口也必须服从同一活动 Claim 防线。

    参数：``station_inventory`` 提供共享规范表和兼容视图。返回：无；断言
    ``move_instance`` 在写物料/库位前抛稳定领域冲突，且目标仍为空。异常：公开
    命令绕过 Claim 时测试保持 RED。
    """

    store, service, identities = station_inventory
    permit = service.station_resources.acquire_dispatch_permit(_request(identities))
    assert permit.acquired is True

    with pytest.raises(InventoryMutationConflict):
        service.move_instance(
            identities["vessel"],
            identities["target_device"],
            "IN",
            actor="public-command",
        )

    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (TARGET_SITE,),
    ) == {"occupied_material_uuid": None}


def test_physical_settlement_is_the_only_claim_authorized_inventory_writer(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """Scheduler 只有携带完整 Permit 的 PhysicalSettlement 能提交物理事实。

    参数：``station_inventory`` 提供活动 Claim。返回：无；断言同一 Claim 的
    effect/job/attempt/parameter hash/ChangeSet/Fence 全部匹配后，来源与目标库位在
    一个事务内切换。异常：任何凭据缺失或漂移必须关闭式失败。
    """

    store, service, identities = station_inventory
    request = _request(identities)
    decision = service.station_resources.acquire_dispatch_permit(request)
    assert decision.permit is not None
    permit = decision.permit
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid,
        target_state="reserved",
    )
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid,
        target_state="running",
    )

    settled = service.station_resources.settle_material_transfer(
        MaterialTransferCommand(
            material_uuid=identities["vessel"],
            target_owner_material_uuid=identities["target_device"],
            target_site_uuid=TARGET_SITE,
            target_site_name="IN",
            actor="station_scheduler.physical_settlement",
            causation_id=request.job_uuid,
            effect_uuid=permit.effect_uuid,
            claim_uuid=permit.claim_uuid,
            job_uuid=request.job_uuid,
            attempt=request.attempt,
            parameter_hash=request.parameter_hash,
            expected_change_set=request.expected_change_set,
            fences=permit.fences,
        )
    )

    assert settled["edge_uuid"] == identities["vessel"]
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (SOURCE_SITE,),
    ) == {"occupied_material_uuid": None}
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (TARGET_SITE,),
    ) == {"occupied_material_uuid": identities["vessel"]}
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid,
        target_state="released",
    )
    assert (
        service.station_resources.settle_material_transfer(
            MaterialTransferCommand(
                material_uuid=identities["vessel"],
                target_owner_material_uuid=identities["target_device"],
                target_site_uuid=TARGET_SITE,
                target_site_name="IN",
                actor="station_scheduler.physical_settlement",
                causation_id=request.job_uuid,
                effect_uuid=permit.effect_uuid,
                claim_uuid=permit.claim_uuid,
                job_uuid=request.job_uuid,
                attempt=request.attempt,
                parameter_hash=request.parameter_hash,
                expected_change_set=request.expected_change_set,
                fences=permit.fences,
            )
        )
        == settled
    )


def test_failed_transfer_can_settle_at_claimed_source_without_moving_material(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """未执行的失败转运可按同一 Claim 证明物料仍在来源库位。"""

    store, service, identities = station_inventory
    request = _request(identities)
    decision = service.station_resources.acquire_dispatch_permit(request)
    assert decision.permit is not None
    permit = decision.permit
    for state in ("reserved", "running", "uncertain"):
        service.station_resources.transition_dispatch_permit(
            permit.claim_uuid,
            target_state=state,
        )

    settled = service.station_resources.settle_material_transfer(
        MaterialTransferCommand(
            material_uuid=identities["vessel"],
            target_owner_material_uuid=identities["source_device"],
            target_site_uuid=SOURCE_SITE,
            target_site_name="OUT",
            actor="physical_settlement",
            causation_id=request.job_uuid,
            effect_uuid=permit.effect_uuid,
            claim_uuid=permit.claim_uuid,
            job_uuid=request.job_uuid,
            attempt=request.attempt,
            parameter_hash=request.parameter_hash,
            expected_change_set=request.expected_change_set,
            fences=permit.fences,
        )
    )

    assert settled["edge_uuid"] == identities["vessel"]
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (SOURCE_SITE,),
    ) == {"occupied_material_uuid": identities["vessel"]}
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (TARGET_SITE,),
    ) == {"occupied_material_uuid": None}


def test_failed_transfer_cannot_settle_at_unclaimed_site(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """失败转运对账不能把物料写入原 Claim 未覆盖的库位。"""

    store, service, identities = station_inventory
    unclaimed_site = "10000000-0000-4000-8000-000000000105"
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO site(
                uuid,create_time,update_time,meta_data,material_uuid,name,
                sort_order,allowed_resource_template_uuids,
                occupied_material_uuid,position_x,position_y,position_z,
                depth,length,width
            ) VALUES (?,?,?,'{}',?,?,2,'[]',NULL,0,0,0,0,0,0)
            """,
            (
                unclaimed_site,
                "2026-08-31T00:00:00Z",
                "2026-08-31T00:00:00Z",
                identities["target_device"],
                "UNCLAIMED",
            ),
        )
    request = _request(identities)
    decision = service.station_resources.acquire_dispatch_permit(request)
    assert decision.permit is not None
    permit = decision.permit

    with pytest.raises(StationResourceError, match="实际库位"):
        service.station_resources.settle_material_transfer(
            MaterialTransferCommand(
                material_uuid=identities["vessel"],
                target_owner_material_uuid=identities["target_device"],
                target_site_uuid=unclaimed_site,
                target_site_name="UNCLAIMED",
                effect_uuid=permit.effect_uuid,
                claim_uuid=permit.claim_uuid,
                job_uuid=request.job_uuid,
                attempt=request.attempt,
                parameter_hash=request.parameter_hash,
                expected_change_set=request.expected_change_set,
                fences=permit.fences,
            )
        )


def test_released_claim_without_settlement_evidence_cannot_first_write(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """已释放 Claim 只允许读取既有 effect 证据，不能首次修改库存。"""

    store, service, identities = station_inventory
    request = _request(identities)
    decision = service.station_resources.acquire_dispatch_permit(request)
    assert decision.permit is not None
    permit = decision.permit
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid,
        target_state="reserved",
    )
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid,
        target_state="released",
    )

    with pytest.raises(DispatchAdmissionConflict, match="禁止首次修改库存"):
        service.station_resources.settle_material_transfer(
            MaterialTransferCommand(
                material_uuid=identities["vessel"],
                target_owner_material_uuid=identities["target_device"],
                target_site_uuid=TARGET_SITE,
                target_site_name="IN",
                effect_uuid=permit.effect_uuid,
                claim_uuid=permit.claim_uuid,
                job_uuid=request.job_uuid,
                attempt=request.attempt,
                parameter_hash=request.parameter_hash,
                expected_change_set=request.expected_change_set,
                fences=permit.fences,
            )
        )
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (SOURCE_SITE,),
    ) == {"occupied_material_uuid": identities["vessel"]}


def test_physical_settlement_rejects_stale_fence_without_partial_write(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """PhysicalSettlement 的任一 Fence 漂移都不得留下部分库存变化。"""

    store, service, identities = station_inventory
    request = _request(identities)
    decision = service.station_resources.acquire_dispatch_permit(request)
    assert decision.permit is not None
    permit = decision.permit
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid,
        target_state="reserved",
    )
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid,
        target_state="running",
    )
    stale_fences = (
        replace(permit.fences[0], fencing_token=permit.fences[0].fencing_token + 1),
        *permit.fences[1:],
    )

    with pytest.raises(DispatchAdmissionConflict, match="Fence"):
        service.station_resources.settle_material_transfer(
            MaterialTransferCommand(
                material_uuid=identities["vessel"],
                target_owner_material_uuid=identities["target_device"],
                target_site_uuid=TARGET_SITE,
                target_site_name="IN",
                effect_uuid=permit.effect_uuid,
                claim_uuid=permit.claim_uuid,
                job_uuid=request.job_uuid,
                attempt=request.attempt,
                parameter_hash=request.parameter_hash,
                expected_change_set=request.expected_change_set,
                fences=stale_fences,
            )
        )

    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (SOURCE_SITE,),
    ) == {"occupied_material_uuid": identities["vessel"]}
    assert store.query_one(
        "SELECT occupied_material_uuid FROM site WHERE uuid=?",
        (TARGET_SITE,),
    ) == {"occupied_material_uuid": None}


def test_operate_in_place_gate_rechecks_device_site_and_material_together(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """原位操作必须原子证明物料仍占用实际执行设备内的精确库位。"""

    _store, service, identities = station_inventory
    request = DispatchAdmissionRequest(
        effect_uuid="50000000-0000-4000-8000-000000000199",
        task_uuid="30000000-0000-4000-8000-000000000199",
        job_uuid="40000000-0000-4000-8000-000000000199",
        attempt=1,
        parameter_hash="sha256:operate-in-place",
        expected_change_set={"kind": "no_inventory_change"},
        resources=(
            DispatchResource(
                lock_key=f"/devices/{identities['source_device']}",
                scope="device",
                material_uuid=identities["source_device"],
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
        ),
        operate_in_place=OperateInPlaceCondition(
            material_uuid=identities["vessel"],
            site_owner_material_uuid=identities["source_device"],
            site_uuid=SOURCE_SITE,
            device_material_uuid=identities["source_device"],
        ),
    )

    decision = service.station_resources.acquire_dispatch_permit(request)
    assert decision.acquired is True
    service.station_resources.transition_dispatch_permit(
        decision.claim_uuid,
        target_state="released",
    )

    wrong_device = replace(
        request,
        effect_uuid="50000000-0000-4000-8000-000000000198",
        job_uuid="40000000-0000-4000-8000-000000000198",
        operate_in_place=replace(
            request.operate_in_place,
            device_material_uuid=identities["target_device"],
        ),
    )
    with pytest.raises(DispatchAdmissionConflict, match="实际执行设备"):
        service.station_resources.acquire_dispatch_permit(wrong_device)


def test_aliquot_claim_and_full_receipt_settle_source_and_targets_atomically(
    station_inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """分装必须锁定来源和全部目标，成功回执在一个库存事务内完成内容转移。"""

    store, service, identities = station_inventory
    backend = BackendResourceService(store)
    vessel_template = store.query_one(
        "SELECT resource_template_uuid FROM material WHERE uuid=?",
        (identities["vessel"],),
    )["resource_template_uuid"]
    with store.transaction() as connection:
        connection.execute(
            "UPDATE resource_template SET tags='[\"container\"]' WHERE uuid=?",
            (vessel_template,),
        )
    targets = tuple(
        backend.create_material(
            {
                "resource_template_uuid": vessel_template,
                "barcode": f"ALIQUOT-{index}",
                "name": f"分装目标 {index}",
            }
        )["uuid"]
        for index in (1, 2)
    )
    BackendContainerContentService(store).create_current_substance(
        {
            "material_uuid": identities["vessel"],
            "name": "母液",
            "components": [],
            "quantity": 10,
            "quantity_unit": "mL",
            "physical_state": "liquid",
        }
    )
    effect_uuid = "50000000-0000-4000-8000-000000000299"
    request = DispatchAdmissionRequest(
        effect_uuid=effect_uuid,
        task_uuid="30000000-0000-4000-8000-000000000299",
        job_uuid="40000000-0000-4000-8000-000000000299",
        attempt=1,
        parameter_hash="sha256:aliquot",
        expected_change_set={
            "kind": "material_content_aliquot",
            "source_material_uuid": identities["vessel"],
            "target_material_uuids": list(targets),
        },
        resources=tuple(
            DispatchResource(
                lock_key=f"material/{material_uuid}/exclusive",
                scope="material",
                material_uuid=material_uuid,
            )
            for material_uuid in (identities["vessel"], *targets)
        ),
        aliquot=AliquotDispatchCondition(
            source_material_uuid=identities["vessel"],
            target_material_uuids=targets,
        ),
    )
    decision = service.station_resources.acquire_dispatch_permit(request)
    assert decision.permit is not None
    permit = decision.permit
    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid, target_state="reserved"
    )
    command = MaterialAliquotCommand(
        source_material_uuid=identities["vessel"],
        receipts=(
            AliquotReceipt(targets[0], 2.5, "mL"),
            AliquotReceipt(targets[1], 1.5, "mL"),
        ),
        effect_uuid=permit.effect_uuid,
        claim_uuid=permit.claim_uuid,
        job_uuid=request.job_uuid,
        attempt=request.attempt,
        parameter_hash=request.parameter_hash,
        expected_change_set=request.expected_change_set,
        fences=permit.fences,
    )
    settled = service.station_resources.settle_material_aliquot(command)

    assert settled["source_quantity"] == 6.0
    rows = store.query_all(
        "SELECT material_uuid,quantity FROM current_substance ORDER BY material_uuid"
    )
    assert {row["material_uuid"]: row["quantity"] for row in rows} == {
        identities["vessel"]: 6.0,
        targets[0]: 2.5,
        targets[1]: 1.5,
    }

    service.station_resources.transition_dispatch_permit(
        permit.claim_uuid, target_state="released"
    )
    assert service.station_resources.settle_material_aliquot(command) == settled
    assert store.query_one(
        "SELECT COUNT(*) AS amount FROM inventory_ledger "
        "WHERE causation_id=? AND op_type LIKE 'current_substance.aliquot_%'",
        (effect_uuid,),
    ) == {"amount": 3}


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
