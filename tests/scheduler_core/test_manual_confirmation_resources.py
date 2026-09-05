"""人工确认与资源计划/执行占用衔接的调度核心模拟。"""

from __future__ import annotations

from tests.scheduler_core.conftest import CoreRuntime, stable_uuid
from tests.scheduler_core.test_manual_confirmation import _manual_node_job
from unilabos.workflow.resource_lock_plan import (
    bind_station_resource_plan,
    compile_template_resource_plan,
    resource_plan_for_node,
    serialize_resource_plan,
)
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection


def test_manual_confirmation_simulation_reuses_claim_and_fence_after_approval(
    core_runtime: CoreRuntime,
) -> None:
    """模拟人工批准沿用同一作业执行占用与栅栏，随后只下发一次真实动作。"""

    _pending, job_uuid = core_runtime.submit_manual(task_name="manual-claim-reuse")
    projection = TaskRuntimeProjection(core_runtime.workflow_store)
    reserved_claim = projection.get_execution_claim(job_uuid)
    assert reserved_claim is not None
    assert reserved_claim["state"] == "reserved"

    service = WorkflowService(
        core_runtime.workflow_store,
        task_scheduler_bridge=core_runtime.bridge,
    )
    approved = service.decide_manual_confirmation(job_uuid, action="approve")

    running_claim = projection.get_execution_claim(job_uuid)
    assert running_claim is not None
    assert running_claim["claim_uuid"] == reserved_claim["claim_uuid"]
    assert running_claim["fences"] == reserved_claim["fences"]
    assert running_claim["state"] == "running"
    assert [item["job_id"] for item in core_runtime.dispatcher.dispatched] == [
        job_uuid
    ]
    payload = core_runtime.dispatcher.dispatched[0]
    assert payload["claim_uuid"] == reserved_claim["claim_uuid"]
    assert payload["fences"] == reserved_claim["fences"]
    assert next(job for job in approved["jobs"] if job["uuid"] == job_uuid)[
        "manual_confirmation"
    ]["status"] == "approved"


def test_manual_confirmation_simulation_keeps_bound_resource_plan_identity(
    core_runtime: CoreRuntime,
) -> None:
    """模拟带绑定资源计划的人工确认，批准前后保留同一计划与区间身份。"""

    manual_node, manual_job = _manual_node_job(
        core_runtime,
        task_name="manual-resource-plan",
        index=0,
        device_id="reactor-a",
    )
    # 资源计划身份来自冻结节点和工站设备实例绑定，不从运行时重新推导。
    resource_graph = {
        "workflow_uuid": stable_uuid("workflow:manual-resource-plan"),
        "nodes": [
            {
                "uuid": manual_node["uuid"],
                "resource_defaults": ["manual-device"],
            }
        ],
        "edges": [],
    }
    template_plan = compile_template_resource_plan(resource_graph)
    bound_plan = bind_station_resource_plan(
        template_plan,
        {
            "manual-device": {
                "instance_uuid": core_runtime.device_materials["reactor-a"],
                "kind": "device",
            }
        },
    )
    node_projection = resource_plan_for_node(bound_plan, manual_node["uuid"])
    manual_node.update(
        {
            "resource_plan_id": bound_plan.plan_id,
            "resource_interval_ids": [
                item["interval_id"] for item in node_projection["intervals"]
            ],
            "resource_acquire_set_id": node_projection["acquire_sets"][0][
                "acquire_set_id"
            ],
        }
    )
    manual_job.update(
        {
            "resource_plan_id": bound_plan.plan_id,
            "resource_interval_ids": list(manual_node["resource_interval_ids"]),
            "resource_acquire_set_id": manual_node["resource_acquire_set_id"],
        }
    )
    pending = core_runtime.submit_frozen(
        task_name="manual-resource-plan",
        execution_plan={
            "version": 1,
            "run_mode": "normal",
            "target_node_uuid": None,
            "nodes": [manual_node],
            "handles": [],
            "edges": [],
            "capabilities": list(bound_plan.capabilities),
            "resource_plan": serialize_resource_plan(bound_plan),
        },
        jobs=[manual_job],
    )

    assert pending["task"]["execution_plan"]["resource_plan"]["plan_id"] == (
        bound_plan.plan_id
    )
    before = core_runtime.scheduler.snapshot()["inflight_jobs"][manual_job["uuid"]]
    assert before["resource_plan_id"] == bound_plan.plan_id
    assert before["resource_interval_ids"] == manual_node["resource_interval_ids"]
    assert before["resource_acquire_set_id"] == manual_node["resource_acquire_set_id"]

    service = WorkflowService(
        core_runtime.workflow_store,
        task_scheduler_bridge=core_runtime.bridge,
    )
    approved = service.decide_manual_confirmation(
        manual_job["uuid"],
        action="approve",
    )
    after = core_runtime.scheduler.snapshot()["inflight_jobs"][manual_job["uuid"]]
    assert after["resource_plan_id"] == before["resource_plan_id"]
    assert after["resource_interval_ids"] == before["resource_interval_ids"]
    assert after["resource_acquire_set_id"] == before["resource_acquire_set_id"]
    assert next(job for job in approved["jobs"] if job["uuid"] == manual_job["uuid"])[
        "manual_confirmation"
    ]["status"] == "approved"
