"""访问区域策略从冻结工作流规格进入真实调度派发的合同。"""

from __future__ import annotations

from typing import Any

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowEdge, WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler

TASK_UUID = "81000000-0000-4000-8000-000000000001"
SOURCE_NODE_UUID = "82000000-0000-4000-8000-000000000001"
RELEASE_NODE_UUID = "82000000-0000-4000-8000-000000000002"
SOURCE_JOB_UUID = "83000000-0000-4000-8000-000000000001"
RELEASE_JOB_UUID = "83000000-0000-4000-8000-000000000002"
MATERIAL_UUID = "84000000-0000-4000-8000-000000000001"


def _node(
    *,
    node_uuid: str,
    job_uuid: str,
    action_name: str,
    executor_kind: str,
    execution_policy: dict[str, Any] | None = None,
) -> WorkflowNode:
    """构造带稳定作业身份和冻结策略的可派发节点。

    参数：节点/作业身份、动作名、执行责任和可选策略决定测试计划。返回：不依赖
    注册表回读的工作流节点。异常：无；固定值全部由调用方控制。
    """

    return WorkflowNode(
        id=node_uuid,
        job_id=job_uuid,
        device_id="reactor-a",
        action_name=action_name,
        action_type="UniLabJsonCommand",
        param={},
        param_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                }
            },
            "required": ["goal"],
        },
        executor_kind=executor_kind,
        execution_policy=dict(execution_policy or {}),
    )


def test_scheduler_dispatches_access_region_with_later_release_job_identity() -> None:
    """入口派发必须携带由后续物料转移作业持有的访问区域声明。

    参数：无。返回无。异常：调度器丢失策略、使用入口 Job 作为租约持有者或把
    释放节点再次声明为入口时由断言暴露。持久化由投影层测试单独覆盖。
    """

    policy = {
        "access_region": {
            "key": "s08-s09-reagent-corridor",
            "lock_material_uuid": MATERIAL_UUID,
            "release_node_uuid": RELEASE_NODE_UUID,
        }
    }
    spec = WorkflowSpec(
        workflow_id=TASK_UUID,
        task_id=TASK_UUID,
        nodes=[
            _node(
                node_uuid=SOURCE_NODE_UUID,
                job_uuid=SOURCE_JOB_UUID,
                action_name="inspect",
                executor_kind="device_action",
                execution_policy=policy,
            ),
            _node(
                node_uuid=RELEASE_NODE_UUID,
                job_uuid=RELEASE_JOB_UUID,
                action_name="transfer_resource",
                executor_kind="material_transfer",
            ),
        ],
        edges=[
            WorkflowEdge(
                uuid="85000000-0000-4000-8000-000000000001",
                source_node_id=SOURCE_NODE_UUID,
                target_node_id=RELEASE_NODE_UUID,
            )
        ],
    )
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    dispatching: list[dict[str, Any]] = []
    scheduler.add_job_pre_dispatch_listener(
        lambda payload: dispatching.append(payload) or True
    )

    submitted = scheduler.submit_workflow(spec)

    assert [item["job_id"] for item in submitted["dispatched"]] == [SOURCE_JOB_UUID]
    access = next(
        item
        for item in dispatching[0]["execution_locks"]
        if item["scope"] == "access_region"
    )
    assert access == {
        "lock_key": (f"access_region/{MATERIAL_UUID}/s08-s09-reagent-corridor"),
        "scope": "access_region",
        "material_uuid": MATERIAL_UUID,
        "access_region_key": "s08-s09-reagent-corridor",
        "lease_owner_job_uuid": RELEASE_JOB_UUID,
    }

    scheduler.on_job_finished(SOURCE_JOB_UUID, True, {"ok": True})

    assert dispatching[1]["job_id"] == RELEASE_JOB_UUID
    assert all(
        item["scope"] != "access_region" for item in dispatching[1]["execution_locks"]
    )
