"""Backend 工站调用的规范化请求与稳定幂等身份。"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from unilabos.workflow.json_codec import encode_json
from unilabos.workflow.models import normalize_json_object, validate_uuid


class StationWorkflowSubmissionInvalid(ValueError):
    """工站调用在进入工作流服务编排前未通过边界校验。"""


@dataclass(frozen=True, slots=True)
class StationWorkflowSubmission:
    """一次规范且可确定性重放的 Backend 工站工作流调用。"""

    backend_task_uuid: str
    invocation_key: str
    workflow_name: str
    input_value: dict[str, Any]
    priority: float
    inventory_bindings: tuple[dict[str, Any], ...]
    description: str | None
    meta_data: dict[str, Any]
    request_fingerprint: str


def prepare_station_workflow_submission(
    *,
    backend_task_uuid: str,
    invocation_key: str,
    workflow_name: str,
    input_value: dict[str, Any],
    priority: float,
    inventory_bindings: list[dict[str, Any]] | None,
    description: str | None,
    meta_data: dict[str, Any] | None,
) -> StationWorkflowSubmission:
    """校验工站调用并生成不含可变工作流修订的稳定请求指纹。

    参数：Backend Task UUID 与调用键标识一次上游调用；工作流名称、入口参数、
    优先级、库存绑定和公开元数据构成完整请求事实。返回：不可变规范命令。异常：
    UUID、文本、JSON 对象或有限优先级非法时抛
    ``StationWorkflowSubmissionInvalid``，不访问数据库且不产生 Task/Job。
    """

    try:
        normalized_backend_task_uuid = validate_uuid(backend_task_uuid)
        normalized_invocation_key = str(invocation_key or "").strip()
        normalized_workflow_name = str(workflow_name or "").strip()
        normalized_input = normalize_json_object(input_value)
        normalized_meta_data = normalize_json_object(meta_data or {})
        normalized_bindings = tuple(
            normalize_json_object(binding) for binding in (inventory_bindings or [])
        )
        normalized_priority = float(priority)
        normalized_description = (
            description.strip() if description is not None else None
        )
        normalized_description = normalized_description or None
        if (
            not normalized_invocation_key
            or len(normalized_invocation_key) > 256
            or not normalized_workflow_name
            or len(normalized_workflow_name) > 256
            or not math.isfinite(normalized_priority)
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError) as error:
        raise StationWorkflowSubmissionInvalid("工站工作流调用参数非法") from error

    payload = {
        "backend_task_uuid": normalized_backend_task_uuid,
        "invocation_key": normalized_invocation_key,
        "workflow_name": normalized_workflow_name,
        "input": normalized_input,
        "priority": normalized_priority,
        "inventory_bindings": list(normalized_bindings),
        "description": normalized_description,
        "meta_data": normalized_meta_data,
    }
    fingerprint = hashlib.sha256(encode_json(payload, sort_keys=True)).hexdigest()
    return StationWorkflowSubmission(
        backend_task_uuid=normalized_backend_task_uuid,
        invocation_key=normalized_invocation_key,
        workflow_name=normalized_workflow_name,
        input_value=normalized_input,
        priority=normalized_priority,
        inventory_bindings=normalized_bindings,
        description=normalized_description,
        meta_data=normalized_meta_data,
        request_fingerprint=fingerprint,
    )


__all__ = [
    "StationWorkflowSubmission",
    "StationWorkflowSubmissionInvalid",
    "prepare_station_workflow_submission",
]
