"""工站调度进程物料转移结算的行为合同。"""

from __future__ import annotations

from typing import Any

import pytest

from unilabos.app.scheduler.inventory.station_resource import (
    MaterialTransferCommand,
)
from unilabos.workflow.material_transfer_settlement import (
    MaterialTransferSettlement,
)
from unilabos.workflow.store import StoreConflict

JOB_UUID = "40000000-0000-4000-8000-000000000001"
MATERIAL_UUID = "50000000-0000-4000-8000-000000000001"
PARENT_UUID = "60000000-0000-4000-8000-000000000001"
NODE_UUID = "30000000-0000-4000-8000-000000000001"


def _transfer_plan() -> dict[str, Any]:
    """返回新架构唯一允许的冻结物料转运合同。

    参数：无。返回：使用明确物料、目标设备、目标库位与夹爪角色的执行计划。
    异常：无；该夹具不提供旧式固定参数名兜底。
    """

    return {
        "nodes": [
            {
                "uuid": NODE_UUID,
                "action_resource_contract": {
                    "version": 1,
                    "transfer": {
                        "material_param": "resource",
                        "target_owner_param": "mount_resource",
                        "target_site_uuid_param": "",
                        "target_site_name_param": "site",
                        "gripper_site_role": "robot.gripper",
                    },
                },
            }
        ]
    }


class _RecordingInventory:
    """记录调度进程提交的库存移动命令。"""

    def __init__(self) -> None:
        """创建空调用列表；参数、返回和异常均为空。"""

        self.calls: list[dict[str, Any]] = []

    def settle_material_transfer(
        self,
        command: MaterialTransferCommand,
    ) -> dict[str, Any]:
        """记录物料移动命令并返回确定性快照。

        参数：``command`` 是结算模块提交的稳定身份、目标和审计字段。返回：记录
        的完整命令。异常：测试固定库位 UUID 之外的值仍按显式名称记录，不访问
        数据库；该替身只验证公开库存接口。
        """

        site_name = command.target_site_name
        if command.target_site_uuid == "70000000-0000-4000-8000-000000000001":
            site_name = "A1"
        item = {
            "edge_uuid": command.material_uuid,
            "parent_uuid": command.target_owner_material_uuid,
            "slot_id": site_name,
            "actor": command.actor,
            "causation_id": command.causation_id,
        }
        self.calls.append(item)
        return item


def test_successful_material_transfer_uses_actual_job_param_and_stable_cause() -> None:
    """成功转运只使用持久实参并以 Job UUID 形成稳定结算身份。

    参数无。返回无；断言物料、父级、库位和因果身份逐字段传给库存权威。
    异常：结算合同漂移会使测试失败。
    """

    inventory = _RecordingInventory()
    settlement = MaterialTransferSettlement(inventory)

    result = settlement.settle_success(
        job={
            "uuid": JOB_UUID,
            "workflow_node_uuid": NODE_UUID,
            "executor_kind": "material_transfer",
            "param": {
                "resource": {"uuid": MATERIAL_UUID},
                "mount_resource": {"uuid": PARENT_UUID},
                "site": "A1",
            },
        },
        execution_plan=_transfer_plan(),
    )

    assert result == inventory.calls[0]
    assert inventory.calls == [
        {
            "edge_uuid": MATERIAL_UUID,
            "parent_uuid": PARENT_UUID,
            "slot_id": "A1",
            "actor": "station_scheduler.material_transfer",
            "causation_id": (f"workflow-node-job:{JOB_UUID}:material-transfer"),
        }
    ]


def test_non_transfer_job_is_noop_but_transfer_without_inventory_fails_closed() -> None:
    """普通 Job 不改库存，转运 Job 缺少库存权威时关闭式失败。

    参数无。返回无；断言设备动作返回 ``None``，同样输入改为 material_transfer
    后抛 ``StoreConflict``。异常：错误被吞掉会使测试失败。
    """

    settlement = MaterialTransferSettlement(None)
    ordinary = {
        "uuid": JOB_UUID,
        "executor_kind": "device_action",
        "param": {},
    }

    assert settlement.settle_success(job=ordinary) is None
    with pytest.raises(StoreConflict, match="库存权威"):
        settlement.settle_success(
            job={
                **ordinary,
                "workflow_node_uuid": NODE_UUID,
                "executor_kind": "material_transfer",
            },
            execution_plan=_transfer_plan(),
        )


def test_transfer_job_without_frozen_contract_is_rejected() -> None:
    """新项目不允许按执行种类猜测任何物料转运参数名。

    参数：无。返回：无；断言缺少冻结 AST 资源合同时先于库存访问关闭失败。
    异常：只接受稳定 ``StoreConflict``，不存在兼容兜底。
    """

    with pytest.raises(StoreConflict, match="缺少冻结资源合同"):
        MaterialTransferSettlement(_RecordingInventory()).settle_success(
            job={
                "uuid": JOB_UUID,
                "workflow_node_uuid": NODE_UUID,
                "executor_kind": "material_transfer",
                "param": {},
            }
        )


def test_typed_robot_action_settles_inventory_from_frozen_resource_contract() -> None:
    """普通设备动作声明 transfer 语义后也必须提交本站库存位置变化。

    参数：无。返回：无；断言结算从冻结计划读取自定义参数名，并用目标库位 UUID
    查询名称，而不是依赖 ``material_transfer`` 执行种类或 Host 固定参数名。
    异常：计划或库存查询不完整时生产结算失败关闭。
    """

    inventory = _RecordingInventory()
    settlement = MaterialTransferSettlement(inventory)
    site_uuid = "70000000-0000-4000-8000-000000000001"
    node_uuid = NODE_UUID
    job = {
        "uuid": JOB_UUID,
        "workflow_node_uuid": node_uuid,
        "executor_kind": "device_action",
        "param": {
            "sample": {"uuid": MATERIAL_UUID},
            "target_device": {"uuid": PARENT_UUID},
            "target_position": site_uuid,
        },
    }
    plan = {
        "nodes": [
            {
                "uuid": node_uuid,
                "action_resource_contract": {
                    "version": 1,
                    "transfer": {
                        "material_param": "sample",
                        "target_owner_param": "target_device",
                        "target_site_uuid_param": "target_position",
                        "target_site_name_param": "",
                        "gripper_site_role": "robot.gripper",
                    },
                },
            }
        ]
    }

    settlement.settle_success(job=job, execution_plan=plan)

    assert inventory.calls[0]["edge_uuid"] == MATERIAL_UUID
    assert inventory.calls[0]["parent_uuid"] == PARENT_UUID
    assert inventory.calls[0]["slot_id"] == "A1"
