"""动作资源合同（ActionResourceContract）的纯声明式规范化。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class ActionResourceContractError(ValueError):
    """动作资源合同无法由 AST 安全编译。

    参数：``code`` 是稳定诊断码，``path`` 是合同内 JSON Pointer，``message``
    是中文诊断。返回：异常对象。异常：构造过程不抛出其他异常。
    """

    def __init__(self, code: str, path: str, message: str) -> None:
        """保存稳定诊断字段。

        参数：``code``、``path``、``message`` 分别表示机器码、字段路径和中文原因。
        返回：无。异常：不主动抛出其他异常。
        """

        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message


def normalize_action_resource_contract(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把装饰器静态字面量编译成版本化动作资源合同。

    参数：``value`` 是 ``@action(resource_contract=...)`` 经 AST 提取的 JSON
    对象；只允许参数名和资源角色，不允许运行时取得/释放代码。返回：字段顺序稳定、
    可直接嵌入动作 Schema 的第 1 版合同；省略时返回空字典。异常：字段未知、版本、
    参数名、设备托管或转运角色非法时抛 ``ActionResourceContractError``。
    """

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _fail("invalid_action_resource_contract", "/", "动作资源合同必须是对象")
    allowed = {"version", "required_device_params", "device_tenancy", "transfer"}
    unknown = set(value) - allowed
    if unknown:
        _fail(
            "unknown_action_resource_field",
            "/",
            "动作资源合同包含未知字段：" + ",".join(sorted(unknown)),
        )
    version = value.get("version", 1)
    if isinstance(version, bool) or version != 1:
        _fail(
            "unsupported_action_resource_version",
            "/version",
            "动作资源合同版本必须是 1",
        )
    normalized: dict[str, Any] = {"version": 1}
    if "required_device_params" in value:
        normalized["required_device_params"] = list(
            _parameter_names(
                value["required_device_params"],
                "/required_device_params",
            )
        )
    if value.get("device_tenancy") is not None:
        normalized["device_tenancy"] = _device_tenancy(value["device_tenancy"])
    if value.get("transfer") is not None:
        normalized["transfer"] = _transfer(value["transfer"])
    if len(normalized) == 1:
        _fail(
            "empty_action_resource_contract",
            "/",
            "动作资源合同除版本外至少声明一种资源语义",
        )
    return normalized


def validate_action_resource_contract_schema(
    contract: Mapping[str, Any],
    action_schema: Mapping[str, Any],
) -> None:
    """证明资源合同引用的参数存在且具有匹配的静态值类型。

    参数：``contract`` 是已规范化资源合同；``action_schema`` 是同一动作由 AST
    编译的完整第 2 版 Schema。返回：无。异常：设备/物料参数不是 ResourceSlot、
    库位参数不是字符串或任一参数不存在时抛 ``ActionResourceContractError``，
    防止运行时按名称猜测资源身份。
    """

    properties = action_schema.get("properties")
    goal = properties.get("goal") if isinstance(properties, Mapping) else None
    goal_properties = goal.get("properties") if isinstance(goal, Mapping) else None
    if not isinstance(goal_properties, Mapping):
        _fail(
            "invalid_action_schema",
            "/",
            "动作资源合同缺少可验证的 Goal Schema",
        )
    resource_fields: list[tuple[str, str]] = []
    for index, name in enumerate(contract.get("required_device_params", [])):
        resource_fields.append((str(name), f"/required_device_params/{index}"))
    tenancy = contract.get("device_tenancy")
    if isinstance(tenancy, Mapping):
        resource_fields.append(
            (str(tenancy["material_param"]), "/device_tenancy/material_param")
        )
        for field in ("acquire_device_param", "release_device_param"):
            if tenancy.get(field):
                resource_fields.append(
                    (str(tenancy[field]), f"/device_tenancy/{field}")
                )
    transfer = contract.get("transfer")
    if isinstance(transfer, Mapping):
        resource_fields.extend(
            (
                (str(transfer["material_param"]), "/transfer/material_param"),
                (
                    str(transfer["target_owner_param"]),
                    "/transfer/target_owner_param",
                ),
            )
        )
        for field in ("target_site_uuid_param", "target_site_name_param"):
            name = str(transfer.get(field) or "")
            if not name:
                continue
            schema = goal_properties.get(name)
            if not isinstance(schema, Mapping):
                _fail(
                    "unknown_action_resource_parameter",
                    f"/transfer/{field}",
                    f"动作资源合同引用不存在的参数 {name}",
                )
            field_type = schema.get("type")
            allowed_types = (
                set(field_type) if isinstance(field_type, list) else {field_type}
            )
            if "string" not in allowed_types:
                _fail(
                    "invalid_site_parameter_type",
                    f"/transfer/{field}",
                    f"库位参数 {name} 必须是字符串",
                )
    for name, path in resource_fields:
        schema = goal_properties.get(name)
        if not isinstance(schema, Mapping):
            _fail(
                "unknown_action_resource_parameter",
                path,
                f"动作资源合同引用不存在的参数 {name}",
            )
        if not _is_resource_reference_schema(schema):
            _fail(
                "invalid_resource_parameter_type",
                path,
                f"资源参数 {name} 必须是 ResourceSlot",
            )


def _is_resource_reference_schema(value: Mapping[str, Any]) -> bool:
    """判断动作字段是否是规范 ResourceSlot 引用 Schema。

    参数：``value`` 是 Goal 字段 Schema。返回：字段要求 ``uuid`` 字符串时为真，
    否则为假。异常：不主动抛出异常。
    """

    properties = value.get("properties")
    uuid_schema = properties.get("uuid") if isinstance(properties, Mapping) else None
    required = value.get("required")
    return (
        value.get("type") == "object"
        and isinstance(uuid_schema, Mapping)
        and uuid_schema.get("type") == "string"
        and isinstance(required, list)
        and "uuid" in required
    )


def _device_tenancy(value: Any) -> dict[str, Any]:
    """规范装载期间设备托管声明。

    参数：``value`` 是 ``device_tenancy`` 字面量。返回：稳定参数名合同。异常：
    模式、字段或取得/释放关系非法时抛 ``ActionResourceContractError``。
    """

    if not isinstance(value, Mapping):
        _fail(
            "invalid_device_tenancy",
            "/device_tenancy",
            "device_tenancy 必须是对象",
        )
    allowed = {
        "mode",
        "material_param",
        "acquire_device_param",
        "release_device_param",
    }
    if set(value) - allowed:
        _fail(
            "unknown_device_tenancy_field",
            "/device_tenancy",
            "device_tenancy 包含未知字段",
        )
    if value.get("mode") != "task_while_loaded":
        _fail(
            "invalid_device_tenancy_mode",
            "/device_tenancy/mode",
            "设备托管模式必须是 task_while_loaded",
        )
    material_param = _parameter_name(
        value.get("material_param"),
        "/device_tenancy/material_param",
    )
    acquire = _optional_parameter_name(
        value.get("acquire_device_param"),
        "/device_tenancy/acquire_device_param",
    )
    release = _optional_parameter_name(
        value.get("release_device_param"),
        "/device_tenancy/release_device_param",
    )
    if not acquire and not release:
        _fail(
            "empty_device_tenancy_transition",
            "/device_tenancy",
            "设备托管至少声明取得或释放设备",
        )
    if acquire and acquire == release:
        _fail(
            "invalid_device_tenancy_transition",
            "/device_tenancy",
            "同一动作不能取得并释放同一设备参数",
        )
    return {
        "mode": "task_while_loaded",
        "material_param": material_param,
        "acquire_device_param": acquire,
        "release_device_param": release,
    }


def _transfer(value: Any) -> dict[str, str]:
    """规范机械臂转运完整资源集参数映射。

    参数：``value`` 是 ``transfer`` 字面量。返回：待搬物料、目标父物料、目标
    库位 UUID/名称和机械臂夹爪库位角色的稳定映射。异常：字段缺失、未知或参数名
    非法时抛 ``ActionResourceContractError``。
    """

    if not isinstance(value, Mapping):
        _fail("invalid_transfer_contract", "/transfer", "transfer 必须是对象")
    allowed = {
        "material_param",
        "target_owner_param",
        "target_site_uuid_param",
        "target_site_name_param",
        "gripper_site_role",
    }
    if set(value) - allowed:
        _fail(
            "unknown_transfer_field",
            "/transfer",
            "transfer 包含未知字段",
        )
    material_param = _parameter_name(
        value.get("material_param"),
        "/transfer/material_param",
    )
    target_owner_param = _parameter_name(
        value.get("target_owner_param"),
        "/transfer/target_owner_param",
    )
    site_uuid_param = _optional_parameter_name(
        value.get("target_site_uuid_param"),
        "/transfer/target_site_uuid_param",
    )
    site_name_param = _optional_parameter_name(
        value.get("target_site_name_param"),
        "/transfer/target_site_name_param",
    )
    if not site_uuid_param and not site_name_param:
        _fail(
            "transfer_site_parameter_missing",
            "/transfer",
            "transfer 至少声明目标库位 UUID 或名称参数",
        )
    gripper_role = _parameter_name(
        value.get("gripper_site_role"),
        "/transfer/gripper_site_role",
    )
    return {
        "material_param": material_param,
        "target_owner_param": target_owner_param,
        "target_site_uuid_param": site_uuid_param,
        "target_site_name_param": site_name_param,
        "gripper_site_role": gripper_role,
    }


def _parameter_names(value: Any, path: str) -> tuple[str, ...]:
    """校验无重复参数名数组。

    参数：``value`` 是可疑数组，``path`` 是诊断路径。返回：稳定参数名元组。
    异常：值非数组或包含重复项时抛 ``ActionResourceContractError``。
    """

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail("invalid_parameter_names", path, "设备参数声明必须是数组")
    names = tuple(_parameter_name(item, path) for item in value)
    if len(set(names)) != len(names):
        _fail("duplicate_parameter_name", path, "设备参数声明包含重复项")
    return names


def _parameter_name(value: Any, path: str) -> str:
    """校验一个声明参数名或资源角色名。

    参数：``value`` 是可疑值，``path`` 是诊断路径。返回：原始非空字符串。
    异常：值不是无首尾空白的字符串时抛 ``ActionResourceContractError``。
    """

    if not isinstance(value, str) or not value or value != value.strip():
        _fail("invalid_parameter_name", path, "参数名必须是无首尾空白的非空字符串")
    return value


def _optional_parameter_name(value: Any, path: str) -> str:
    """校验一个可省略的声明参数名。

    参数：``value`` 是可疑值、``None`` 或已规范化的空字符串，``path`` 是诊断
    路径。返回：省略时为空字符串，否则返回规范名称。异常：非空值非法时抛
    ``ActionResourceContractError``；重复规范化保持幂等。
    """

    if value is None or value == "":
        return ""
    return _parameter_name(value, path)


def _fail(code: str, path: str, message: str) -> None:
    """抛出稳定动作资源合同诊断。

    参数：``code``、``path``、``message`` 是异常字段。返回：永不返回。异常：始终
    抛出 ``ActionResourceContractError``。
    """

    raise ActionResourceContractError(code, path, message)


__all__ = [
    "ActionResourceContractError",
    "normalize_action_resource_contract",
    "validate_action_resource_contract_schema",
]
