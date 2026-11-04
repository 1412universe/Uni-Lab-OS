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
