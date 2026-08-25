"""本地 ``transfer_resource`` 的目标库位解析与执行互斥测试。"""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import pytest

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.inventory.backend_contract import BackendResourceService
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.app.scheduler.models import WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler

_SITE_UUID_A = "22222222-2222-4222-8222-222222222222"
_SITE_UUID_B = "33333333-3333-4333-8333-333333333333"


@pytest.fixture()
def inventory(
    tmp_path: Path,
) -> Iterator[tuple[InventoryStore, InventoryService, dict[str, str]]]:
    """建立一个父物料、两个空库位和两件待转移物料。

    参数：``tmp_path`` 提供隔离 SQLite 路径。返回：存储、库存服务和稳定身份
    字典。异常：夹具结束时始终关闭数据库。
    """

    store = InventoryStore(str(tmp_path / "site-lock.db"))
    backend = BackendResourceService(store)
    templates = backend.sync_resource_templates(
        [
            {
                "id": "test.site-owner",
                "display_name": "测试库位父物料",
                "registry_type": "resource",
                "class": {},
            },
            {
                "id": "test.transfer-item",
                "display_name": "测试转运物料",
                "registry_type": "material",
                "class": {},
            },
        ]
    )["templates"]
    template_by_name = {item["name"]: item["uuid"] for item in templates}
    owner = backend.create_material(
        {
            "resource_template_uuid": template_by_name["test.site-owner"],
            "barcode": "SITE-OWNER",
            "name": "库位父物料",
        }
    )
    first = backend.create_material(
        {
            "resource_template_uuid": template_by_name["test.transfer-item"],
            "barcode": "TRANSFER-A",
            "name": "待转物料 A",
        }
    )
    second = backend.create_material(
        {
            "resource_template_uuid": template_by_name["test.transfer-item"],
            "barcode": "TRANSFER-B",
            "name": "待转物料 B",
        }
    )
    with store.transaction() as connection:
        for site_uuid, name, order in (
            (_SITE_UUID_A, "A1", 0),
            (_SITE_UUID_B, "B1", 1),
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
                    "2026-08-25T00:00:00Z",
                    "2026-08-25T00:00:00Z",
                    owner["uuid"],
                    name,
                    order,
                    json.dumps([template_by_name["test.transfer-item"]]),
                ),
            )
    try:
        yield (
            store,
            InventoryService(store),
            {
                "owner": owner["uuid"],
                "first": first["uuid"],
                "second": second["uuid"],
            },
        )
    finally:
        store.close()


def _material_reference_schema() -> dict[str, Any]:
    """返回只含稳定 UUID 的规范 ``ResourceSlot`` 锁合同。

    参数：无。返回：带动作物料锁标记的对象 Schema。异常：无。
    """

    return {
        "type": "object",
        "x-unilabos-material-lock": True,
        "properties": {"uuid": {"type": "string", "format": "uuid"}},
        "required": ["uuid"],
        "additionalProperties": False,
    }


def _action_schema(*, include_site_uuid: bool = True) -> dict[str, Any]:
    """返回转运动作冻结合同。

    参数：``include_site_uuid`` 为假时模拟升级前只含 ``site`` 的历史合同。
    返回：严格 Goal 参数 Schema。异常：无。
    """

    properties: dict[str, Any] = {
        "resource": _material_reference_schema(),
        "mount_resource": _material_reference_schema(),
        "site": {"type": "string", "default": ""},
    }
    if include_site_uuid:
        properties["site_uuid"] = {
            "type": "string",
            "format": "uuid",
            "default": "",
        }
    return {
        "type": "object",
        "properties": {
            "goal": {
                "type": "object",
                "properties": properties,
                "required": ["resource", "mount_resource"],
                "additionalProperties": False,
            }
        },
        "required": ["goal"],
    }


def _node(
    node_id: str,
    *,
    device_id: str,
    resource_uuid: str,
    owner_uuid: str,
    site_uuid: str = "",
    site: str = "",
    include_site_uuid: bool = True,
) -> WorkflowNode:
    """构造真实参数形状的 ``transfer_resource`` 工作流节点。

    参数：节点、设备、待转物料、父物料和两个库位选择字段；
    ``include_site_uuid`` 控制是否模拟历史合同。返回：冻结了动作合同的节点。
    异常：无。
    """

    param: dict[str, Any] = {
        "resource": {"uuid": resource_uuid},
        "mount_resource": {"uuid": owner_uuid},
    }
    if site:
        param["site"] = site
    if site_uuid:
        param["site_uuid"] = site_uuid
    return WorkflowNode(
        id=node_id,
        device_id=device_id,
        action_name="transfer_resource",
        action_type="goal",
        param=param,
        param_schema=_action_schema(include_site_uuid=include_site_uuid),
        always_free=True,
    )


def test_site_uuid_has_priority_and_fills_canonical_site_name(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证只传 ``site_uuid`` 时解析并下发规范库位名。

    参数：``inventory`` 提供隔离库存。返回：无。异常：解析或派发不符合合同
    时由断言报告。
    """

    _, service, identities = inventory
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=service)

    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-site-uuid",
            nodes=[
                _node(
                    "a",
                    device_id="device-a",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                    site_uuid=_SITE_UUID_A,
                )
            ],
        )
    )

    assert len(result["dispatched"]) == 1
    assert dispatcher.dispatched[0]["always_free"] is True
    assert dispatcher.dispatched[0]["action_args"]["site"] == "A1"
    assert dispatcher.dispatched[0]["action_args"]["site_uuid"] == _SITE_UUID_A
    job_uuid = result["dispatched"][0]["job_id"]
    assert set(scheduler.snapshot()["inflight_jobs"][job_uuid]["resource_locks"]) == {
        f"material/{identities['first']}/exclusive",
        f"material/{identities['owner']}/site/{_SITE_UUID_A}/exclusive",
    }


def test_legacy_site_name_resolves_without_requiring_site_uuid(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证旧工作流只传库位名仍可解析并派发。

    参数：``inventory`` 提供隔离库存。返回：无。异常：兼容路径失效时由断言
    报告。
    """

    _, service, identities = inventory
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=service)
    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-site-name",
            nodes=[
                _node(
                    "a",
                    device_id="device-a",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                    site="a1",
                    include_site_uuid=False,
                )
            ],
        )
    )

    assert len(result["dispatched"]) == 1
    assert dispatcher.dispatched[0]["action_args"] == {
        "resource": {"uuid": identities["first"]},
        "mount_resource": {"uuid": identities["owner"]},
        "site": "A1",
    }


def test_unknown_legacy_site_name_falls_back_to_whole_parent_lock(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证未知历史库位名退回整父物料忙碌键。

    参数：``inventory`` 提供隔离库存。返回：无。异常：兼容派发或保守互斥
    失效时由断言报告。
    """

    _, service, identities = inventory
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=service)
    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-legacy-unknown-site",
            nodes=[
                _node(
                    "a",
                    device_id="device-a",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                    site="LEGACY-SITE",
                    include_site_uuid=False,
                )
            ],
        )
    )

    assert len(result["dispatched"]) == 1
    assert dispatcher.dispatched[0]["action_args"]["site"] == "LEGACY-SITE"
    job_uuid = result["dispatched"][0]["job_id"]
    assert set(scheduler.snapshot()["inflight_jobs"][job_uuid]["resource_locks"]) == {
        f"material/{identities['first']}/exclusive",
        f"material/{identities['owner']}/exclusive",
    }


def test_site_uuid_from_another_owner_is_rejected(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证库位不属于 ``mount_resource`` 时失败关闭。

    参数：``inventory`` 提供隔离库存。返回：无。异常：错误库位越过派发边界
    时由断言报告。
    """

    _, service, identities = inventory
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=service)
    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-foreign-site",
            nodes=[
                _node(
                    "a",
                    device_id="device-a",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["second"],
                    site_uuid=_SITE_UUID_A,
                )
            ],
        )
    )

    assert result["dispatched"] == []
    assert dispatcher.dispatched == []


def test_same_site_waits_but_distinct_sites_can_run_in_parallel(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证真实宿主动作同库位串行、不同库位并行。

    参数：``inventory`` 提供隔离库存。返回：无。异常：库位忙碌键或
    ``always_free`` 设备排队语义失效时由断言报告。
    """

    _, service, identities = inventory
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher(), inventory=service)
    first = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-first",
            nodes=[
                _node(
                    "first",
                    device_id="host_node",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                    site_uuid=_SITE_UUID_A,
                )
            ],
        )
    )
    blocked = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-same-site",
            nodes=[
                _node(
                    "blocked",
                    device_id="host_node",
                    resource_uuid=identities["second"],
                    owner_uuid=identities["owner"],
                    site_uuid=_SITE_UUID_A,
                )
            ],
        )
    )
    parallel = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-other-site",
            nodes=[
                _node(
                    "parallel",
                    device_id="host_node",
                    resource_uuid=identities["second"],
                    owner_uuid=identities["owner"],
                    site_uuid=_SITE_UUID_B,
                )
            ],
        )
    )

    assert len(first["dispatched"]) == 1
    assert blocked["dispatched"] == []
    assert [item["node_id"] for item in parallel["dispatched"]] == ["parallel"]


@pytest.mark.parametrize(
    ("site_uuid", "site", "expected_code"),
    [
        ("not-a-uuid", "", "invalid_site_uuid"),
        (_SITE_UUID_A, "B1", "site_identity_mismatch"),
        ("44444444-4444-4444-8444-444444444444", "", "site_not_found"),
    ],
)
def test_invalid_site_selection_fails_closed(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
    site_uuid: str,
    site: str,
    expected_code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证非法库位选择全部失败关闭。

    参数：``inventory`` 是隔离库存；``site_uuid``、``site`` 和
    ``expected_code`` 描述错误样例；``caplog`` 捕获稳定诊断。返回：无。
    异常：非法选择被派发或诊断漂移时由断言报告。
    """

    _, service, identities = inventory
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=service)
    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id=f"wf-invalid-{expected_code}",
            nodes=[
                _node(
                    "a",
                    device_id="device-a",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                    site_uuid=site_uuid,
                    site=site,
                )
            ],
        )
    )

    assert result["dispatched"] == []
    assert dispatcher.dispatched == []
    assert (
        scheduler.workflow_snapshot(f"wf-invalid-{expected_code}")["state"] == "failed"
    )
    assert expected_code in caplog.text


def test_occupied_site_is_rejected_before_dispatch(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证目标库位被其他物料占用时在派发前失败。

    参数：``inventory`` 提供可修改的隔离库存。返回：无。异常：占用校验失效
    时由断言报告。
    """

    store, service, identities = inventory
    with store.transaction() as connection:
        connection.execute(
            "UPDATE site SET occupied_material_uuid=? WHERE uuid=?",
            (identities["second"], _SITE_UUID_A),
        )
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=service)
    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-occupied-site",
            nodes=[
                _node(
                    "a",
                    device_id="device-a",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                    site_uuid=_SITE_UUID_A,
                )
            ],
        )
    )

    assert result["dispatched"] == []
    assert dispatcher.dispatched == []


def test_site_rejects_material_template_not_allowed_by_site(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证目标库位拒绝其合同未允许的物料模板。

    参数：``inventory`` 提供可修改的隔离库存。返回：无。异常：不兼容物料
    越过派发边界时由断言报告。
    """

    store, service, identities = inventory
    owner_template = store.query_one(
        "SELECT resource_template_uuid FROM material WHERE uuid=?",
        (identities["owner"],),
    )["resource_template_uuid"]
    with store.transaction() as connection:
        connection.execute(
            "UPDATE site SET allowed_resource_template_uuids=? WHERE uuid=?",
            (json.dumps([owner_template]), _SITE_UUID_A),
        )
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=service)
    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-template-not-allowed",
            nodes=[
                _node(
                    "a",
                    device_id="host_node",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                    site_uuid=_SITE_UUID_A,
                )
            ],
        )
    )

    assert result["dispatched"] == []
    assert dispatcher.dispatched == []


def test_missing_site_keeps_legacy_whole_parent_material_lock(
    inventory: tuple[InventoryStore, InventoryService, dict[str, str]],
) -> None:
    """验证未指定库位时仍独占整个 ``mount_resource``。

    参数：``inventory`` 提供隔离库存。返回：无。异常：遗留互斥语义漂移时
    由断言报告。
    """

    _, service, identities = inventory
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher(), inventory=service)
    result = scheduler.submit_workflow(
        WorkflowSpec(
            workflow_id="wf-whole-parent",
            nodes=[
                _node(
                    "a",
                    device_id="device-a",
                    resource_uuid=identities["first"],
                    owner_uuid=identities["owner"],
                )
            ],
        )
    )

    job_uuid = result["dispatched"][0]["job_id"]
    assert set(scheduler.snapshot()["inflight_jobs"][job_uuid]["resource_locks"]) == {
        f"material/{identities['first']}/exclusive",
        f"material/{identities['owner']}/exclusive",
    }
