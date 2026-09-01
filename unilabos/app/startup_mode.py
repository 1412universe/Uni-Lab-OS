"""Uni-Lab OS 启动模式与工作流可见范围。"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class OSStartupMode(str, Enum):
    """Uni-Lab OS 的启动可见范围。"""

    DEVELOP = "develop"
    PRODUCT = "product"


_startup_mode = OSStartupMode.DEVELOP


def set_startup_mode(mode: str | OSStartupMode) -> OSStartupMode:
    """设置当前进程的 OS 启动模式。

    参数：``mode`` 必须是 ``develop`` 或 ``product``，用于决定当前进程公开的
    工作流定义范围。返回：规范化后的 ``OSStartupMode``。异常：传入未知模式时
    抛出 ``ValueError``，且不会改变已有模式。状态不变量：模式是进程级全局值，
    由启动命令在装配 HTTP 服务前设置一次。
    """

    global _startup_mode
    try:
        normalized = mode if isinstance(mode, OSStartupMode) else OSStartupMode(mode)
    except (TypeError, ValueError) as error:
        raise ValueError("OS 启动模式必须是 develop 或 product") from error
    _startup_mode = normalized
    return normalized


def get_startup_mode() -> OSStartupMode:
    """读取当前进程的 OS 启动模式。

    参数：无。返回：启动命令设置的 ``OSStartupMode``。异常：无；在尚未显式设置
    时返回 ``develop``，保持旧版直接启动的可见范围不变。
    """

    return _startup_mode


def reset_startup_mode() -> OSStartupMode:
    """将启动模式恢复为兼容旧直接启动的调试范围。

    参数：无。返回：恢复后的 ``OSStartupMode.DEVELOP``。异常：无；测试或同一进程
    重新装配应用时可用它清理上一次启动命令留下的全局状态。
    """

    return set_startup_mode(OSStartupMode.DEVELOP)


def allows_experiment_operations() -> bool:
    """判断当前模式是否允许展示实验操作。

    参数：无。返回：调试模式为 ``True``，生产模式为 ``False``。异常：无；该判断
    只控制读模型可见性，不改变工作流定义或发布事实。
    """

    return get_startup_mode() is OSStartupMode.DEVELOP


def is_workflow_visible(workflow: Mapping[str, Any]) -> bool:
    """判断一个工作流读模型是否可以在当前启动模式展示。

    参数：``workflow`` 是包含 ``workflow_type`` 和派生 ``status`` 的公开工作流
    读模型。返回：调试模式展示普通工作流和实验操作的全部状态；生产模式只展示
    ``workflow_type=normal`` 且 ``status=published`` 的工作流。异常：缺少字段时
    按不可见处理，避免生产模式把未完成或未知类型的数据泄露给前端。
    """

    if get_startup_mode() is OSStartupMode.DEVELOP:
        return True
    return (
        workflow.get("workflow_type") == "normal"
        and workflow.get("status") == "published"
    )


def visible_workflow_filter(
    workflow_type: str | None,
    status: str | None,
    operation_category_uuid: str | None,
) -> tuple[str | None, str | None, str | None, bool]:
    """把请求筛选条件收敛为当前模式允许的工作流查询。

    参数：三个值来自工作流列表查询参数。返回：调整后的类型、状态、类别和
    ``always_empty`` 标记。生产模式固定查询已发布普通工作流；请求实验操作、
    源码状态或实验操作类别时直接返回空页。异常：无；枚举合法性仍由工作流服务
    负责校验，防止可见性策略吞掉真实请求错误。
    """

    if get_startup_mode() is OSStartupMode.DEVELOP:
        return workflow_type, status, operation_category_uuid, False
    if (
        workflow_type not in (None, "normal")
        or status not in (None, "published")
        or operation_category_uuid is not None
    ):
        return "normal", "published", None, True
    return "normal", "published", None, False


__all__ = [
    "OSStartupMode",
    "allows_experiment_operations",
    "get_startup_mode",
    "is_workflow_visible",
    "reset_startup_mode",
    "set_startup_mode",
    "visible_workflow_filter",
]
