"""设备执行传输状态与业务结果的唯一终态规范化模块。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_executor_outcome(
    status: str,
    return_info: Any,
    *,
    cancel_requested: bool = False,
) -> str:
    """把执行器外层状态与动作业务返回统一成作业终态。

    参数：``status`` 是执行传输层报告的状态；``return_info`` 是原始动作结果或
    含 ``return_value`` 的结果封装；``cancel_requested`` 表示作业此前已请求取消。
    返回：``succeeded``、``failed``、``canceled`` 或 ``timeout``。异常：无；
    未知外层状态关闭失败，且动作明确 ``success=false`` 始终覆盖传输成功。
    """

    normalized_status = str(status or "").strip().lower()
    outcome = {
        "success": "succeeded",
        "succeeded": "succeeded",
        "failed": "failed",
        "canceled": "canceled",
        "timeout": "timeout",
    }.get(normalized_status, "failed")
    action_result = (
        return_info.get("return_value")
        if isinstance(return_info, Mapping) and "return_value" in return_info
        else return_info
    )
    if (
        outcome == "succeeded"
        and isinstance(action_result, Mapping)
        and action_result.get("success") is False
    ):
        outcome = "failed"
    if cancel_requested and outcome == "failed":
        return "canceled"
    return outcome


__all__ = ["normalize_executor_outcome"]
