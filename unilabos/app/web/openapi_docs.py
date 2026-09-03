"""把 FastAPI 生成的 OpenAPI 整理成可直接阅读的中文 Swagger 文档。"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from fastapi import FastAPI

from unilabos.app.web.openapi_descriptions import (
    OPERATION_DESCRIPTIONS,
    PARAMETER_MEANINGS,
    REQUEST_BODY_MEANINGS,
    SUMMARY_TRANSLATIONS,
)
from unilabos.app.web.openapi_field_descriptions import (
    FIELD_MEANINGS,
    SCHEMA_FIELD_MEANINGS,
)

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
NULL_TYPES = {"null", "None", "NoneType"}
FORBIDDEN_DESCRIPTION_TERMS = (
    "backend",
    "dto",
    "envelope",
    "authority",
    "cas 冲突",
    "sse",
    "幂等",
    "游标",
    "投影",
    "哈希",
    "修订",
    "信封",
)
RESPONSE_DESCRIPTIONS = {
    "200": "请求成功。返回字段和数据类型见下方响应结构。",
    "201": "创建成功。返回字段和数据类型见下方响应结构。",
    "202": "请求已接收，服务会继续处理。",
    "204": "请求成功，没有返回内容。",
    "400": "请求内容不正确。",
    "401": "访问凭证缺失或不正确。",
    "403": "当前调用方没有权限执行该操作。",
    "404": "要查询或操作的内容不存在。",
    "409": "请求与当前数据发生冲突，已有数据不会被覆盖。",
    "422": "参数格式或取值不正确，错误位置见返回内容。",
    "429": "请求过于频繁，请稍后重试。",
    "500": "服务处理请求时发生错误。",
    "503": "服务尚未准备好或当前不可用，请稍后重试。",
}
TAG_TRANSLATIONS = {
    "api": ("基础与设备", "服务状态、文件、设备和设备动作接口。"),
    "authoring-transform": ("工作流编辑", "检查工作流编辑内容并生成 Python 源码。"),
    "backend-resource-contract": ("物料与资源", "物料、试剂、样品、成分和资源模板接口。"),
    "device-telemetry": ("设备状态", "设备属性、关节状态和装配关系的上报与订阅接口。"),
    "edge-scheduler": ("调度", "任务安排、设备任务、运行历史和人工错误处理接口。"),
    "experiment-operation-category": ("实验操作类别", "实验操作类别的查询和维护接口。"),
    "inventory": ("库存", "库存批次、实例、预留、流水和同步接口。"),
    "inventory-material-compat": ("物料查询", "按现有物料查询格式读取库存。"),
    "lab": ("实验室布局", "实验室区域、摆放位置和装配关系接口。"),
    "local-edge-control": ("设备任务通信", "调度进程与设备运行进程之间的任务通信接口。"),
    "manual-exclusive": ("设备手动使用", "查询、取得和释放设备手动使用权。"),
    "workflow": ("工作流", "工作流定义、任务、节点任务和运行记录接口。"),
    "workflow-template": ("工作流节点模板", "工作流编辑时可用的节点模板接口。"),
    "workspace-authoring": ("工作区领域包", "查询当前工作区加载的领域包。"),
    "workspace-material-assets": ("物料模型", "查询工作区提供的物料外形和模型文件。"),
}


def _contains_chinese(text: str) -> bool:
    """判断文字中是否已经有中文。"""

    return any("\u4e00" <= character <= "\u9fff" for character in text)


def _is_plain_description(text: str) -> bool:
    """判断现有说明是否为中文且不含面向实现的术语。"""

    normalized = text.casefold()
    return _contains_chinese(text) and not any(
        term in normalized for term in FORBIDDEN_DESCRIPTION_TERMS
    )


def _schema_name(schema: Mapping[str, Any]) -> str | None:
    """从一个引用结构中取出模型名。"""

    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference:
        return None
    return reference.rsplit("/", 1)[-1]


def _schema_type(
    schema: Mapping[str, Any],
    component_schemas: Mapping[str, Any] | None = None,
    seen_references: frozenset[str] = frozenset(),
) -> str:
    """把 OpenAPI 数据类型翻译成 Swagger 中可直接阅读的中文。"""

    referenced_name = _schema_name(schema)
    if referenced_name is not None:
        if (
            component_schemas is not None
            and referenced_name not in seen_references
            and isinstance(component_schemas.get(referenced_name), Mapping)
        ):
            return _schema_type(
                component_schemas[referenced_name],
                component_schemas,
                seen_references | {referenced_name},
            )
        return "对象"

    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list):
        nullable = any(
            isinstance(item, Mapping) and item.get("type") in NULL_TYPES
            for item in variants
        )
        names = []
        for item in variants:
            if not isinstance(item, Mapping) or item.get("type") in NULL_TYPES:
                continue
            name = _schema_type(item, component_schemas, seen_references)
            if name not in names:
                names.append(name)
        rendered = "或".join(names) if names else "任意 JSON 值"
        return f"{rendered}（可为空）" if nullable else rendered

    composed = schema.get("allOf")
    if isinstance(composed, list) and composed:
        names = [
            _schema_type(item, component_schemas, seen_references)
            for item in composed
            if isinstance(item, Mapping)
        ]
        return "和".join(dict.fromkeys(names)) or "对象"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        nullable = any(item in NULL_TYPES for item in schema_type)
        names = [
            _primitive_type(item)
            for item in schema_type
            if item not in NULL_TYPES
        ]
        rendered = "或".join(dict.fromkeys(names)) or "任意 JSON 值"
        return f"{rendered}（可为空）" if nullable else rendered
    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, Mapping):
            return (
                "数组（每项是"
                f"{_schema_type(items, component_schemas, seen_references)}）"
            )
        return "数组"
    if schema_type:
        return _primitive_type(str(schema_type))
    if "properties" in schema or "additionalProperties" in schema:
        return "对象"
    return "任意 JSON 值"


def _primitive_type(schema_type: str) -> str:
    """翻译一个基础 OpenAPI 类型。"""

    return {
        "string": "字符串",
        "integer": "整数",
        "number": "数字",
        "boolean": "布尔值",
        "object": "对象",
        "array": "数组",
        "null": "空值",
    }.get(schema_type, schema_type)


def _with_parameter_facts(
    meaning: str,
    *,
    required: bool,
    type_name: str,
    presence_label: str = "是否必传",
) -> str:
    """在含义后明确写出是否需要出现和数据类型。"""

    base = meaning.split("\n\n是否", 1)[0].strip().rstrip("。")
    required_text = "是" if required else "否"
    return (
        f"{base}。\n\n{presence_label}：{required_text}。"
        f"数据类型：{type_name}。"
    )


def _parameter_meaning(parameter: Mapping[str, Any]) -> str:
    """优先使用明确中文说明，没有时按已知公开参数名解释。"""

    name = str(parameter.get("name", "")).strip()
    if name in PARAMETER_MEANINGS:
        return PARAMETER_MEANINGS[name]
    existing = str(parameter.get("description", "")).strip()
    if existing and _is_plain_description(existing):
        return existing
    return f"名称为 {name} 的接口参数"


def _field_meaning(
    schema_name: str,
    field_name: str,
    field_schema: Mapping[str, Any],
) -> str:
    """查找请求或返回字段的中文含义。"""

    override = SCHEMA_FIELD_MEANINGS.get((schema_name, field_name))
    if override:
        return override
    known = FIELD_MEANINGS.get(field_name)
    if known:
        return known
    existing = str(field_schema.get("description", "")).strip()
    if existing and _is_plain_description(existing):
        return existing
    return f"名称为 {field_name} 的字段"


def _referenced_schema_names(value: Any) -> set[str]:
    """收集一段 OpenAPI 结构直接引用的数据模型名称。"""

    references: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            referenced_name = _schema_name(current)
            if referenced_name is not None:
                references.add(referenced_name)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return references


def _expand_schema_references(
    roots: set[str],
    component_schemas: Mapping[str, Any],
) -> set[str]:
    """沿模型中的字段引用找出完整的请求或返回结构。"""

    expanded = set(roots)
    pending = list(roots)
    while pending:
        schema_name = pending.pop()
        component = component_schemas.get(schema_name)
        for referenced_name in _referenced_schema_names(component):
            if referenced_name not in expanded:
                expanded.add(referenced_name)
                pending.append(referenced_name)
    return expanded


def _component_usage(
    schema: Mapping[str, Any],
    component_schemas: Mapping[str, Any],
) -> tuple[set[str], set[str]]:
    """分别找出请求和返回会用到的数据模型。"""

    request_roots: set[str] = set()
    response_roots: set[str] = set()
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, Mapping):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, Mapping):
                continue
            request_roots.update(
                _referenced_schema_names(operation.get("requestBody"))
            )
            response_roots.update(_referenced_schema_names(operation.get("responses")))
    return (
        _expand_schema_references(request_roots, component_schemas),
        _expand_schema_references(response_roots, component_schemas),
    )


def _translate_tags(schema: MutableMapping[str, Any]) -> None:
    """把 Swagger 分组名替换为中文，并补充分组用途。"""

    used_tags: list[str] = []
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, MutableMapping):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, MutableMapping):
                continue
            translated = []
            for tag in operation.get("tags", []):
                chinese_name = TAG_TRANSLATIONS.get(str(tag), (str(tag), ""))[0]
                if chinese_name not in translated:
                    translated.append(chinese_name)
                if chinese_name not in used_tags:
                    used_tags.append(chinese_name)
            operation["tags"] = translated

    descriptions = {
        name: description
        for name, description in TAG_TRANSLATIONS.values()
    }
    schema["tags"] = [
        {"name": name, "description": descriptions.get(name, "相关接口。")}
        for name in used_tags
    ]


def _enrich_operation(
    method: str,
    path: str,
    operation: MutableMapping[str, Any],
    component_schemas: Mapping[str, Any],
) -> None:
    """补齐一个接口的名称、用途、参数、请求体和返回说明。"""

    original_summary = str(operation.get("summary", "")).strip()
    summary = SUMMARY_TRANSLATIONS.get(original_summary, original_summary)
    operation["summary"] = summary or f"{method.upper()} {path}"

    explicit_description = OPERATION_DESCRIPTIONS.get((method.upper(), path))
    if explicit_description:
        operation["description"] = explicit_description
    else:
        operation["description"] = f"{operation['summary']}。"

    for parameter in operation.get("parameters", []):
        if not isinstance(parameter, MutableMapping):
            continue
        parameter_schema = parameter.get("schema")
        if not isinstance(parameter_schema, Mapping):
            parameter_schema = {}
        parameter["description"] = _with_parameter_facts(
            _parameter_meaning(parameter),
            required=bool(parameter.get("required", False)),
            type_name=_schema_type(parameter_schema, component_schemas),
        )

    request_body = operation.get("requestBody")
    if isinstance(request_body, MutableMapping):
        content = request_body.get("content", {})
        first_schema: Mapping[str, Any] = {}
        if isinstance(content, Mapping):
            for media in content.values():
                if isinstance(media, Mapping) and isinstance(media.get("schema"), Mapping):
                    first_schema = media["schema"]
                    break
        model_name = _schema_name(first_schema)
        meaning = REQUEST_BODY_MEANINGS.get(model_name or "")
        if not meaning:
            meaning = "请按照下方字段填写请求内容。"
        request_body["description"] = _with_parameter_facts(
            meaning,
            required=bool(request_body.get("required", False)),
            type_name=_schema_type(first_schema, component_schemas),
        )

    responses = operation.get("responses", {})
    if isinstance(responses, MutableMapping):
        for status, response in responses.items():
            if not isinstance(response, MutableMapping):
                continue
            existing = str(response.get("description", "")).strip()
            if not existing or not _is_plain_description(existing):
                response["description"] = RESPONSE_DESCRIPTIONS.get(
                    str(status),
                    "请求处理结果。返回字段和数据类型见下方响应结构。",
                )


def enrich_openapi_schema(schema: MutableMapping[str, Any]) -> None:
    """原地补齐一份 OpenAPI 文档的中文说明。"""

    info = schema.setdefault("info", {})
    if isinstance(info, MutableMapping):
        info["title"] = "UniLabOS 接口文档"
        info["description"] = (
            "这里列出当前进程实际提供的接口。请求参数会标明用途、是否必传和"
            "数据类型；返回字段会标明是否一定返回和数据类型。"
        )

    component_schemas = schema.get("components", {}).get("schemas", {})
    if not isinstance(component_schemas, Mapping):
        component_schemas = {}
    request_schemas, response_schemas = _component_usage(schema, component_schemas)

    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, MutableMapping):
            continue
        for method, operation in path_item.items():
            if method in HTTP_METHODS and isinstance(operation, MutableMapping):
                _enrich_operation(method, str(path), operation, component_schemas)

    if isinstance(component_schemas, Mapping):
        for schema_name, component in component_schemas.items():
            if not isinstance(component, MutableMapping):
                continue
            if "enum" in component and "properties" not in component:
                component["description"] = "该字段允许使用的值见下方列表。"
            else:
                component["description"] = REQUEST_BODY_MEANINGS.get(
                    str(schema_name),
                    "下方列出这个数据结构包含的字段。",
                )
            required_fields = set(component.get("required", []))
            properties = component.get("properties", {})
            if not isinstance(properties, Mapping):
                continue
            for field_name, field_schema in properties.items():
                if not isinstance(field_schema, MutableMapping):
                    continue
                used_in_request = schema_name in request_schemas
                used_in_response = schema_name in response_schemas
                if used_in_response and not used_in_request:
                    presence_label = "是否一定返回"
                elif used_in_request and used_in_response:
                    presence_label = "请求和返回中是否必须有"
                else:
                    presence_label = "是否必传"
                field_schema["description"] = _with_parameter_facts(
                    _field_meaning(str(schema_name), str(field_name), field_schema),
                    required=field_name in required_fields,
                    type_name=_schema_type(field_schema, component_schemas),
                    presence_label=presence_label,
                )

    _translate_tags(schema)


def install_openapi_documentation(app: FastAPI) -> None:
    """让一个 FastAPI 应用在生成 Swagger 时自动使用中文说明。"""

    if getattr(app.state, "plain_chinese_openapi_installed", False):
        return
    original_openapi = app.openapi

    def documented_openapi() -> dict[str, Any]:
        """生成并缓存补充过说明的 OpenAPI。"""

        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = original_openapi()
        enrich_openapi_schema(schema)
        app.openapi_schema = schema
        return schema

    # 即使调用方曾提前生成过原始 OpenAPI，也要在下一次读取时重新生成说明。
    app.openapi_schema = None
    app.openapi = documented_openapi  # type: ignore[method-assign]
    app.state.plain_chinese_openapi_installed = True


__all__ = ["enrich_openapi_schema", "install_openapi_documentation"]
