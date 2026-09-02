"""冻结条件分支与 RepeatUntil 的调度核心合同。"""

from __future__ import annotations

from typing import Any

from tests.scheduler_core.conftest import CoreRuntime, stable_uuid


def device_node(
    *,
    node_uuid: str,
    parent_uuid: str | None,
    device_id: str,
    material_uuid: str,
    action: str,
    param: dict[str, Any] | None = None,
    carry_bindings: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """构造静态冻结计划中的最小设备动作节点。"""

    return {
        "uuid": node_uuid,
        "parent_uuid": parent_uuid,
        "kind": "device_action",
        "param": dict(param or {}),
        "execution_policy": {},
        "action_resource_contract": {},
        "device_id": device_id,
        "material_uuid": material_uuid,
        "action_name": action,
        "action_type": "UniLabJsonCommand",
        "param_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                }
            },
            "additionalProperties": False,
        },
        "material_requirements": [],
        "carry_bindings": dict(carry_bindings or {}),
    }


def job(
    *,
    job_uuid: str,
    node_uuid: str,
    index: int,
    kind: str,
    param: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造冻结计划已创建的持久 Job。"""

    return {
        "uuid": job_uuid,
        "workflow_node_uuid": node_uuid,
        "topological_index": index,
        "executor_kind": kind,
        "execution_policy": {},
        "execution_timeout_seconds": 0,
        "param": dict(param or {}),
    }


def dependency(source: str, target: str, *, name: str) -> dict[str, Any]:
    """构造不传值的冻结依赖边。"""

    return {
        "uuid": stable_uuid(f"control-edge:{name}"),
        "source_node_uuid": source,
        "target_node_uuid": target,
        "source_handle_uuid": "",
        "target_handle_uuid": "",
        "dependency_only": True,
    }


def test_frozen_condition_selects_one_branch_and_persists_the_other_as_skipped(
    core_runtime: CoreRuntime,
) -> None:
    """条件节点只在本地求值，Fake Dispatcher 只能看到命中分支。"""

    condition_uuid = stable_uuid("condition:selector")
    selected_uuid = stable_uuid("condition:selected")
    fallback_uuid = stable_uuid("condition:fallback")
    condition_job = stable_uuid("job:condition:selector")
    selected_job = stable_uuid("job:condition:selected")
    fallback_job = stable_uuid("job:condition:fallback")
    region = {
        "predecessor_node_uuids": [],
        "bindings": {
            "should_analyze": {
                "kind": "workflow_input",
                "parameter": "should_analyze",
            }
        },
        "branches": [
            {
                "label": "if",
                "condition": {"var": "should_analyze"},
                "node_uuids": [selected_uuid],
                "entry_node_uuids": [selected_uuid],
                "exit_node_uuids": [selected_uuid],
            },
            {
                "label": "else",
                "condition": None,
                "node_uuids": [fallback_uuid],
                "entry_node_uuids": [fallback_uuid],
                "exit_node_uuids": [fallback_uuid],
            },
        ],
    }
    plan = {
        "version": 2,
        "run_mode": "normal",
        "capabilities": ["condition_expression_v1", "control_regions_v1"],
        "nodes": [
            {
                "uuid": condition_uuid,
                "parent_uuid": None,
                "kind": "condition",
                "param": region,
                "control_region": region,
                "execution_policy": {},
                "action_resource_contract": {},
            },
            device_node(
                node_uuid=selected_uuid,
                parent_uuid=condition_uuid,
                device_id="reactor-a",
                material_uuid=core_runtime.device_materials["reactor-a"],
                action="selected",
            ),
            device_node(
                node_uuid=fallback_uuid,
                parent_uuid=condition_uuid,
                device_id="reactor-b",
                material_uuid=core_runtime.device_materials["reactor-b"],
                action="fallback",
            ),
        ],
        "handles": [],
        "edges": [
            dependency(condition_uuid, selected_uuid, name="selected"),
            dependency(condition_uuid, fallback_uuid, name="fallback"),
        ],
    }
    aggregate = core_runtime.submit_frozen(
        task_name="condition",
        execution_plan=plan,
        jobs=[
            job(
                job_uuid=condition_job,
                node_uuid=condition_uuid,
                index=0,
                kind="condition",
                param=region,
            ),
            job(
                job_uuid=selected_job,
                node_uuid=selected_uuid,
                index=1,
                kind="device_action",
            ),
            job(
                job_uuid=fallback_job,
                node_uuid=fallback_uuid,
                index=2,
                kind="device_action",
            ),
        ],
        resolved_input={"should_analyze": True},
    )

    assert [item["job_id"] for item in core_runtime.dispatcher.dispatched] == [
        selected_job
    ]
    assert core_runtime.workflow_store.get_job(condition_job)["status"] == "succeeded"
    assert core_runtime.workflow_store.get_job(fallback_job)["status"] == "skipped"

    core_runtime.scheduler.on_job_finished(selected_job, True, {"selected": True})
    assert (
        core_runtime.workflow_store.get_task(aggregate["task"]["uuid"])["status"]
        == "succeeded"
    )


def test_repeat_until_persists_each_iteration_before_dispatch(
    core_runtime: CoreRuntime,
) -> None:
    """RepeatUntil 每轮使用新 Job 身份，满足严格布尔条件后才派发后继。"""

    repeat_uuid = stable_uuid("repeat:control")
    measure_uuid = stable_uuid("repeat:measure")
    adjust_uuid = stable_uuid("repeat:adjust")
    final_uuid = stable_uuid("repeat:final")
    repeat_job = stable_uuid("job:repeat:control")
    final_job = stable_uuid("job:repeat:final")
    dose_handle = stable_uuid("handle:repeat:dose")
    region = {
        "predecessor_node_uuids": [],
        "successor_node_uuids": [final_uuid],
        "loop_variable": "loop",
        "max_iterations": 3,
        "initial_carry": {"dose": {"kind": "literal", "value": 1}},
        "next_carry": {
            "dose": {
                "kind": "node_result",
                "node_uuid": adjust_uuid,
                "result_path": ["next_dose"],
            }
        },
        "until": {"field": {"var": "measurement"}, "name": "qualified"},
        "bindings": {"measurement": {"kind": "node_result", "node_uuid": measure_uuid}},
        "node_uuids": [measure_uuid, adjust_uuid],
        "entry_node_uuids": [measure_uuid],
        "exit_node_uuids": [adjust_uuid],
    }
    plan = {
        "version": 2,
        "run_mode": "normal",
        "capabilities": [
            "condition_expression_v1",
            "control_regions_v1",
            "dynamic_iteration_jobs_v1",
        ],
        "nodes": [
            {
                "uuid": repeat_uuid,
                "parent_uuid": None,
                "kind": "repeat_until",
                "param": region,
                "control_region": region,
                "execution_policy": {},
                "action_resource_contract": {},
            },
            device_node(
                node_uuid=measure_uuid,
                parent_uuid=repeat_uuid,
                device_id="reactor-a",
                material_uuid=core_runtime.device_materials["reactor-a"],
                action="measure",
            ),
            device_node(
                node_uuid=adjust_uuid,
                parent_uuid=repeat_uuid,
                device_id="reactor-b",
                material_uuid=core_runtime.device_materials["reactor-b"],
                action="adjust",
                carry_bindings={
                    dose_handle: {
                        "control_region_uuid": repeat_uuid,
                        "key": "dose",
                    }
                },
            ),
            device_node(
                node_uuid=final_uuid,
                parent_uuid=None,
                device_id="robot-a",
                material_uuid=core_runtime.device_materials["robot-a"],
                action="finish",
            ),
        ],
        "handles": [
            {
                "uuid": dose_handle,
                "node_uuid": adjust_uuid,
                "data_source": "executor",
                "handle_key": "dose",
                "data_key": "dose",
                "io_type": "target",
            }
        ],
        "edges": [
            dependency(measure_uuid, adjust_uuid, name="repeat-body"),
            dependency(repeat_uuid, measure_uuid, name="repeat-entry"),
            dependency(repeat_uuid, final_uuid, name="repeat-exit"),
        ],
    }
    aggregate = core_runtime.submit_frozen(
        task_name="repeat",
        execution_plan=plan,
        jobs=[
            job(
                job_uuid=repeat_job,
                node_uuid=repeat_uuid,
                index=0,
                kind="repeat_until",
                param=region,
            ),
            job(
                job_uuid=final_job,
                node_uuid=final_uuid,
                index=3,
                kind="device_action",
            ),
        ],
    )

    first_measure = core_runtime.dispatcher.dispatched[-1]
    assert first_measure["action"] == "measure"
    core_runtime.scheduler.on_job_finished(
        first_measure["job_id"], True, {"qualified": False}
    )
    first_adjust = core_runtime.dispatcher.dispatched[-1]
    assert first_adjust["action"] == "adjust"
    assert first_adjust["action_args"]["dose"] == 1

    core_runtime.scheduler.on_job_finished(
        first_adjust["job_id"], True, {"next_dose": 2}
    )
    second_measure = core_runtime.dispatcher.dispatched[-1]
    assert second_measure["action"] == "measure"
    assert second_measure["job_id"] != first_measure["job_id"]
    core_runtime.scheduler.on_job_finished(
        second_measure["job_id"], True, {"qualified": True}
    )
    second_adjust = core_runtime.dispatcher.dispatched[-1]
    assert second_adjust["action"] == "adjust"
    assert second_adjust["action_args"]["dose"] == 2

    core_runtime.scheduler.on_job_finished(
        second_adjust["job_id"], True, {"next_dose": 3}
    )
    assert core_runtime.dispatcher.dispatched[-1]["job_id"] == final_job
    core_runtime.scheduler.on_job_finished(final_job, True, {"done": True})

    task_uuid = aggregate["task"]["uuid"]
    persisted_jobs = core_runtime.workflow_store.list_jobs(task_uuid)
    assert core_runtime.workflow_store.get_task(task_uuid)["status"] == "succeeded"
    assert len(persisted_jobs) == 6
    assert {item["status"] for item in persisted_jobs} == {"succeeded"}
    assert len({item["job_id"] for item in core_runtime.dispatcher.dispatched}) == 5
