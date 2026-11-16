"""ChemWorldModel / 旧版 JSON 导入时的本地模板与连线句柄重映射。

ChemWorldModel 导出的 ``template_uuid`` 来自另一套环境；本地 kernel-v2 用设备
动作目录生成稳定 UUID。导入时按 ``device_name`` + 动作名回落到本地模板，并把
``ready`` 等句柄键补成当前目录的 Handle UUID。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from unilabos.workflow.authoring_kernel import AuthoringCatalogAction


def normalize_imported_action_name(raw: Any) -> str:
    """把 CWM 的 ``auto-execute_command`` 等名称规范成本地动作业务名。"""

    name = str(raw or "").strip()
    if name.startswith("auto-"):
        name = name[5:].strip()
    return name


def _device_hints(node: Mapping[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("device_name", "resource_name", "resource_template_name"):
        value = str(node.get(key) or "").strip()
        if value and value not in hints:
            hints.append(value)
    return hints


def _resource_names(template: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    resource = template.get("resource_template")
    if isinstance(resource, Mapping):
        for key in ("name", "display_name"):
            value = str(resource.get(key) or "").strip()
            if value and value not in names:
                names.append(value)
    meta = template.get("meta_data")
    if isinstance(meta, Mapping):
        unilab = meta.get("unilab")
        nested = unilab.get("resource_template") if isinstance(unilab, Mapping) else None
        if isinstance(nested, Mapping):
            value = str(nested.get("name") or "").strip()
            if value and value not in names:
                names.append(value)
        nested = meta.get("resource_template")
        if isinstance(nested, Mapping):
            value = str(nested.get("name") or "").strip()
            if value and value not in names:
                names.append(value)
    class_identity = str(template.get("class") or "").strip()
    if class_identity and class_identity not in names:
        names.append(class_identity)
    return names


def _device_matches(hint: str, resource_names: Sequence[str]) -> bool:
    needle = hint.strip()
    if not needle:
        return False
    for name in resource_names:
        if (
            name == needle
            or name.endswith("." + needle)
            or name.endswith("/" + needle)
            or name.endswith(":" + needle)
            or needle in name.split(".")
            or needle in name.split(":")
        ):
            return True
    return False


def resolve_catalog_action(
    actions: Sequence[AuthoringCatalogAction],
    node: Mapping[str, Any],
) -> AuthoringCatalogAction | None:
    """按设备提示 + 动作名从本地目录解析动作；找不到返回 ``None``。"""

    action_name = normalize_imported_action_name(
        node.get("template_name") or node.get("name") or node.get("action_name")
    )
    if not action_name:
        return None
    hints = _device_hints(node)
    matches: list[AuthoringCatalogAction] = []
    for action in actions:
        template = action.template
        if str(template.get("name") or "").strip() != action_name:
            continue
        resource_names = _resource_names(template)
        if hints:
            if any(_device_matches(hint, resource_names) for hint in hints):
                matches.append(action)
        else:
            matches.append(action)
    if len(matches) == 1:
        return matches[0]
    if not matches and hints:
        # 再宽松一轮：只比动作名，且资源名后缀命中任一 hint
        for action in actions:
            template = action.template
            if str(template.get("name") or "").strip() != action_name:
                continue
            resource_names = _resource_names(template)
            lowered_hints = [hint.lower() for hint in hints]
            if any(
                any(hint in name.lower() for hint in lowered_hints)
                for name in resource_names
            ):
                matches.append(action)
        if len(matches) == 1:
            return matches[0]
    return None


def resolve_handle_uuid(
    action: AuthoringCatalogAction | None,
    *,
    handle_key: str,
    io_type: str,
) -> str | None:
    """按句柄键与方向取本地 Handle UUID。"""

    if action is None:
        return None
    key = str(handle_key or "").strip() or "ready"
    direction = str(io_type or "").strip().lower()
    for handle in action.handles:
        if (
            str(handle.get("handle_key") or "").strip() == key
            and str(handle.get("io_type") or "").strip().lower() == direction
        ):
            uuid = str(handle.get("uuid") or "").strip()
            return uuid or None
    return None


def empty_as_none(value: Any) -> Any:
    """把空字符串当成缺失，避免 UUID 校验把 ``""`` 当成非法值。"""

    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def normalize_imported_node_uuid(raw: Any) -> str:
    """把 CWM 复合节点标识归一化为标准 UUID。

    ChemWorldModel 组合工作流时会把祖先路径拼进节点身份，导出形如
    ``<根流程>__<子流程>__<节点>`` 的三段复合串（112 个字符）。本地 kernel-v2
    的节点身份是单个 UUID，直接校验必然失败，因此这里取**最后一段**作为节点
    身份。

    为什么末段可靠：CWM 的每个末段都是该节点自身的 UUID，且同一份导出内全局
    唯一；祖先段只表达"它来自哪个组合"，不参与节点身份。取末段既保持稳定
    （同一文件重复导入得到同一身份），也让 ``edges`` 的端点引用能一一对上。

    参数：``raw`` 是原始节点/边端点标识。返回：合法 UUID 字符串。异常：既不是
    复合路径、本身也不是合法 UUID 时抛 ``ValueError``，由调用方转成可读诊断。
    """

    from uuid import UUID

    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValueError("空标识")
    candidate = text
    if "__" in text:
        candidate = text.split("__")[-1].strip()
    try:
        return str(UUID(candidate))
    except (TypeError, ValueError) as error:
        raise ValueError(f"不是合法 UUID：{text}") from error


def normalize_imported_edge_uuid(raw: Any) -> str | None:
    """归一化 CWM 复合边标识；边没有末段 UUID 时返回 ``None`` 由调用方生成。

    CWM 的边标识可能形如 ``<源复合标识>><目标复合标识>``，其中不含独立 UUID。
    这种形状无法稳定还原成单个 UUID，交给调用方按端点生成即可。

    参数：``raw`` 是原始边标识。返回：合法 UUID 或 ``None``。异常：无。
    """

    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    if ">" in text:
        return None
    try:
        return normalize_imported_node_uuid(text)
    except ValueError:
        return None


# CWM 的类型名用首字母大写（``Group``/``Condition``/``RepeatUntil``），本机的
# 无模板类型词表全是小写；不归一化会报「无模板节点的 type 不受支持」。
_CWM_STANDALONE_TYPE_ALIASES = {
    "group": "group",
    "condition": "condition",
    "repeatuntil": "repeat_until",
    "repeat_until": "repeat_until",
}


def normalize_imported_node_type(raw: Any) -> str:
    """归一化 CWM 导出的节点类型名。

    参数：``raw`` 是节点 ``type`` 字段。返回：本机可识别的类型名；不属于已知
    别名时原样返回（交给后续校验给出准确诊断），空值返回空串。异常：无。
    """

    text = "" if raw is None else str(raw).strip()
    if not text:
        return ""
    key = text.replace("-", "").replace(" ", "").lower()
    return _CWM_STANDALONE_TYPE_ALIASES.get(key, text)


def prune_undeclared_params(
    param: Any,
    *,
    template: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """剔除本机动作模板未声明的参数。

    CWM 导出的 ``param`` 可能带本机驱动已经没有的字段（例如锁紧机构早期版本的
    ``pre_turns``/``final_torque``/``pre_torque_limit``）。本机模板把
    ``additionalProperties`` 设为 ``false``，这类字段会让整份文件导入失败。

    这里选择**丢弃并报告**，而不是让整份文件失败：其余参数与节点拓扑都是有效
    的，一个遗留字段不该阻断导入。被丢弃的字段名返回给调用方，由导入结果如实
    呈现，避免静默改变用户数据。

    参数：``param`` 是节点原始参数；``template`` 是该节点最终使用的本地模板。
    返回：``(清理后的参数, 被丢弃的字段名列表)``。异常：无——模板没有声明
    ``goal`` 时原样保留参数（无从判断合法性）。
    """

    if not isinstance(param, Mapping):
        return {}, []
    declared = template.get("goal")
    if not isinstance(declared, Mapping) or not declared:
        return dict(param), []
    kept = {str(key): value for key, value in param.items() if str(key) in declared}
    dropped = sorted(str(key) for key in param if str(key) not in declared)
    return kept, dropped


# CWM 的物料引用形如 ``{id, name, uuid}``：``id`` 是 Deck 路径、``name`` 是展示名，
# 本机物料引用合同只接受 ``{uuid}`` 且 ``additionalProperties: false``。
_CWM_MATERIAL_REF_EXTRA_KEYS = frozenset({"id", "name"})


def normalize_imported_material_refs(value: Any) -> Any:
    """递归把 CWM 物料引用 ``{id, name, uuid}`` 收敛成 ``{uuid}``。

    判定条件刻意收紧：对象**必须同时**含 ``uuid`` 与至少一个 CWM 独有键
    （``id``/``name``），且除这三者外没有别的字段。这样普通业务对象（例如同时
    带 ``name`` 和 ``uuid`` 但不是物料引用的配置）不会被误删字段。

    参数：``value`` 是节点 ``param`` 或任意嵌套结构。返回：净化后的新结构
    （不修改入参）。异常：无；非容器原样返回。
    """

    if isinstance(value, Mapping):
        text_uuid = value.get("uuid")
        is_material_ref = (
            isinstance(text_uuid, str)
            and text_uuid.strip() != ""
            and bool(_CWM_MATERIAL_REF_EXTRA_KEYS & set(value.keys()))
            and set(value.keys()) <= ({"uuid"} | _CWM_MATERIAL_REF_EXTRA_KEYS)
        )
        if is_material_ref:
            return {"uuid": text_uuid}
        return {
            key: normalize_imported_material_refs(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_imported_material_refs(item) for item in value]
    return value


# CWM 导出用站号整数（Robot_ModuleNoSet），本机 GN 机械手驱动用站点名查 STATIONS。
# 这张表与 unilabos/devices/workstation/GN/robotic_arm.py 的 STATIONS 一一对应；
# 只在模板把 ``station`` 声明为字符串、且值是表内站号时才转换。
_CWM_STATION_MODULE_NAMES = {
    1: "oven",
    2: "locking",
    3: "quick_change",
    4: "centrifuge",
    5: "vacuum_oven",
    6: "nine9320",
    7: "tube",
    8: "stack",
    9: "solid",
    10: "finished_area",
}


def normalize_imported_station_param(value: Any, *, declared_type: Any) -> Any:
    """把 CWM 的站号整数换成本机站点名。

    CWM 用 ``Robot_ModuleNoSet`` 的模块号表达工站（``8`` 表示旋转堆栈），本机
    机械手驱动用 ``STATIONS`` 的站点名查表。两者语义一致、编号一一对应，因此
    可以无损转换；但**只在模板声明该字段为字符串时**才做，避免把真正需要整数
    的字段改坏。

    参数：``value`` 是参数值，``declared_type`` 是该字段在模板 schema 里的类型
    （字符串或字符串数组）。返回：转换后的值；不满足条件时原样返回。异常：无。
    """

    if isinstance(declared_type, (list, tuple, set, frozenset)):
        types = {str(item) for item in declared_type}
    else:
        types = {str(declared_type)}
    if "string" not in types:
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    return _CWM_STATION_MODULE_NAMES.get(value, value)
