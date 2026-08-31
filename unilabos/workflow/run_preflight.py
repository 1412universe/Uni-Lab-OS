"""工作流候选运行预检（RunPreflight）的只读报告构造。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from unilabos.workflow._execution_plan_graph import ExecutionPlanBuildError
from unilabos.workflow.execution_plan import ExecutionPlanBuilder
from unilabos.workflow.store import StoreConflict, utc_now


def _check(
    *,
    check_type: str,
    status: str,
    code: str,
    message: str,
    blocking: bool = False,
    node_uuid: str | None = None,
    node_name: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一个 Backend 形状的预检检查项。"""

    result: dict[str, Any] = {
        "type": check_type,
        "status": status,
        "code": code,
        "message": message,
        "blocking": blocking,
        "details": dict(details or {}),
    }
    if node_uuid is not None:
        result["node_uuid"] = node_uuid
    if node_name:
        result["node_name"] = node_name
    return result


def _finalize(report: dict[str, Any]) -> dict[str, Any]:
    """根据检查集合计算汇总状态和计数。"""

    checks = report["checks"]
    summary = report["summary"]
    summary["passed_check_count"] = sum(item["status"] == "passed" for item in checks)
    summary["blocking_check_count"] = sum(
        item["status"] == "blocked" for item in checks
    )
    summary["deferred_check_count"] = sum(
        item["status"] == "deferred" for item in checks
    )
    summary["confirmation_required_count"] = sum(
        item["status"] == "confirmation_required" for item in checks
    )
    report["can_run"] = not any(
        item["status"] == "blocked" and item["blocking"] for item in checks
    )
    if not report["can_run"]:
        report["status"] = "blocked"
    elif summary["confirmation_required_count"]:
        report["status"] = "requires_confirmation"
    else:
        report["status"] = "ready"
    return report


def _append_quantity_inventory_check(
    report: dict[str, Any],
    quantity_inventory_check: Mapping[str, Any] | None,
) -> None:
    """把可选共享数量库存预检追加到报告且不改变其他检查。"""

    if quantity_inventory_check is None:
        return
    blocked = quantity_inventory_check.get("status") == "blocked"
    report["checks"].append(
        _check(
            check_type="quantity_inventory",
            status="blocked" if blocked else "passed",
            code=(
                "quantity_inventory_unavailable"
                if blocked
                else "quantity_inventory_ready"
            ),
            message=str(
                quantity_inventory_check.get("message")
                or (
                    "共享数量库存当前不足"
                    if blocked
                    else "共享数量库存当前可完成整任务准入"
                )
            ),
            blocking=blocked,
            details={
                "allocation_count": int(
                    quantity_inventory_check.get("allocation_count") or 0
                )
            },
        )
    )


def build_run_preflight_report(
    *,
    graph: Mapping[str, Any],
    run_mode: str,
    target_node_uuid: str | None,
    material_resolver: Callable[[str], Mapping[str, Any] | None] | None,
    quantity_inventory_check: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """只读检查当前图能否形成执行计划及其明显运行前条件。

    参数：``graph`` 是同一修订快照；运行模式和可选目标固定候选范围；
    ``material_resolver`` 只读确认设备物料身份；可选数量库存检查来自同一候选
    输入的只读库存快照。返回可展示报告；本函数不创建 Task/Job、不预留库存、
    不取得执行锁，动态条件明确标成 ``deferred``。
    """

    workflow = graph["workflow"]
    report: dict[str, Any] = {
        "workflow_uuid": workflow["uuid"],
        "workflow_revision": workflow["revision"],
        "run_mode": run_mode,
        "status": "ready",
        "can_run": True,
        "checked_at": utc_now(),
        "summary": {
            "execution_node_count": 0,
            "passed_check_count": 0,
            "blocking_check_count": 0,
            "deferred_check_count": 0,
            "confirmation_required_count": 0,
        },
        "checks": [],
    }
    if target_node_uuid is not None:
        report["target_node_uuid"] = target_node_uuid
    try:
        plan, _jobs = ExecutionPlanBuilder().build(
            graph,
            run_mode=run_mode,
            target_node_uuid=target_node_uuid,
        )
    except (
        ExecutionPlanBuildError,
        KeyError,
        StoreConflict,
        TypeError,
        ValueError,
    ) as error:
        report["checks"].append(
            _check(
                check_type="execution_plan",
                status="blocked",
                code="execution_plan_invalid",
                message=str(error),
                blocking=True,
            )
        )
        _append_quantity_inventory_check(report, quantity_inventory_check)
        return _finalize(report)

    planned_nodes = plan["nodes"]
    report["summary"]["execution_node_count"] = len(planned_nodes)
    _append_quantity_inventory_check(report, quantity_inventory_check)
    report["checks"].append(
        _check(
            check_type="execution_plan",
            status="passed",
            code="execution_plan_ready",
            message="工作流执行计划可以生成",
            details={"execution_node_count": len(planned_nodes)},
        )
    )
    graph_nodes = {
        str(node["uuid"]): node
        for node in graph.get("nodes", [])
        if isinstance(node, Mapping) and isinstance(node.get("uuid"), str)
    }
    for planned in planned_nodes:
        node_uuid = str(planned["uuid"])
        node = graph_nodes.get(node_uuid, {})
        kind = str(planned["kind"])
        node_name = str(node.get("name") or "")
        if kind == "manual_confirm":
            report["checks"].append(
                _check(
                    check_type="manual_confirmation",
                    status="confirmation_required",
                    code="manual_confirmation_required",
                    message="运行到该节点时需要人工确认",
                    node_uuid=node_uuid,
                    node_name=node_name,
                )
            )
        if kind == "material_source":
            report["checks"].append(
                _check(
                    check_type="material_source",
                    status="deferred",
                    code="material_source_checked_at_admission",
                    message="物料来源将在任务准入时按同一冻结条件重新解析",
                    node_uuid=node_uuid,
                    node_name=node_name,
                )
            )
        device_selector = planned.get("device_selector")
        if (
            kind in {"device_action", "material_transfer"}
            and isinstance(device_selector, Mapping)
            and device_selector
        ):
            report["checks"].append(
                _check(
                    check_type="device_selection",
                    status="deferred",
                    code="device_instance_selected_at_dispatch",
                    message="具体设备实例将在派发门禁按冻结设备类选择并原子占用",
                    node_uuid=node_uuid,
                    node_name=node_name,
                    details={
                        "resource_template_uuid": str(
                            device_selector.get("resource_template_uuid") or ""
                        )
                    },
                )
            )
        material_uuid = planned.get("material_uuid")
        if kind == "device_action" and isinstance(material_uuid, str):
            material = (
                material_resolver(material_uuid)
                if material_resolver is not None
                else None
            )
            if not isinstance(material, Mapping):
                report["checks"].append(
                    _check(
                        check_type="device",
                        status="blocked",
                        code="device_material_unavailable",
                        message="设备物料不存在或当前不可用",
                        blocking=True,
                        node_uuid=node_uuid,
                        node_name=node_name,
                    )
                )
            else:
                report["checks"].append(
                    _check(
                        check_type="device",
                        status="deferred",
                        code="device_dispatch_recheck_required",
                        message="设备在线状态与动作能力将在派发时原子复核",
                        node_uuid=node_uuid,
                        node_name=node_name,
                        details={"material_uuid": material_uuid},
                    )
                )
    report["checks"].append(
        _check(
            check_type="resource_lock",
            status="deferred",
            code="resource_admission_at_dispatch",
            message=(
                "静态资源取得顺序已验证；设备、库位、物料条件和当前占用只能在"
                "派发门禁的库存事务中原子复核"
            ),
        )
    )
    return _finalize(report)


__all__ = ["build_run_preflight_report"]
