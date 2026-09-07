"""复用库存 JSON 字段的最大装料量；兼容旧装料限制，不推算粉体密度。"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, NoReturn

CAPACITY_KEY = "capacity"
LOADING_LIMITS_KEY = "loading_limits"
CAPACITY_FIELDS = ("max_volume_ul", "max_mass_g")
_UNITS = {
    "ul": ("max_volume_ul", 1.0), "µl": ("max_volume_ul", 1.0),
    "μl": ("max_volume_ul", 1.0), "ml": ("max_volume_ul", 1000.0),
    "l": ("max_volume_ul", 1_000_000.0),
    "mg": ("max_mass_g", 0.001), "g": ("max_mass_g", 1.0),
    "kg": ("max_mass_g", 1000.0),
}


def _invalid(message: str) -> NoReturn:
    # 延迟导入，资源与试剂服务可共同复用此模块。
    from .backend_contract import BackendContractError, INVALID_PARAMETER

    raise BackendContractError(INVALID_PARAMETER, message)


def json_object(value: Any) -> dict[str, Any]:
    """解码存储层 JSON 对象；空字段视为未配置。"""
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_capacity(value: Any) -> dict[str, float]:
    """规范化正有限上限；缺失、null 或空对象表示未配置。"""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(CAPACITY_FIELDS):
        _invalid("上限须为对象，仅支持 max_volume_ul 和 max_mass_g")
    result = {}
    for key, raw in value.items():
        if raw is None:
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            _invalid(f"{key} 须为正有限数值")
        if isinstance(raw, bool) or not math.isfinite(number) or number <= 0:
            _invalid(f"{key} 须为正有限数值")
        result[key] = number
    return result


def stricter_capacity(*values: dict[str, float]) -> dict[str, float]:
    """同一维度取最小值，体积与质量互不换算。"""
    return {
        key: min(value[key] for value in values if key in value)
        for key in CAPACITY_FIELDS if any(key in value for value in values)
    }


def capacity_projection(config: Any, data: Any, template_meta: Any) -> dict[str, Any]:
    """合并领域包额定规格、旧 PLR 显式容量与 OS 实例配置。"""
    config, data, template_meta = map(json_object, (config, data, template_meta))
    legacy = []
    for source in (config, data):
        if source.get("max_volume") is not None:
            legacy.append(normalize_capacity({"max_volume_ul": source["max_volume"]}))
    rated = stricter_capacity(normalize_capacity(template_meta.get(CAPACITY_KEY)), *legacy)
    local = normalize_capacity(config.get(CAPACITY_KEY))
    return {"capacity": stricter_capacity(rated, local), "rated_capacity": rated}


def material_capacity(conn: sqlite3.Connection, material_uuid: str) -> dict[str, Any]:
    """在调用方事务内读取最新规格，避免数量与上限并发写入穿透。"""
    row = conn.execute(
        "SELECT m.config,m.data,t.meta_data,i.aggregate_version FROM material m "
        "LEFT JOIN material_inventory i ON i.material_uuid=m.uuid "
        "LEFT JOIN resource_template t ON t.uuid=m.resource_template_uuid "
        "AND t.deleted_at IS NULL WHERE m.uuid=? AND m.deleted_at IS NULL",
        (material_uuid,),
    ).fetchone()
    if row is None:
        return {"capacity": {}, "rated_capacity": {}, "material_revision": None}
    return {**capacity_projection(row["config"], row["data"], row["meta_data"]),
            "material_revision": row["aggregate_version"]}


def maximum_capacity(capacity: dict[str, float], metadata: Any) -> dict[str, float]:
    """读取实际生效的最大装料量，尚未编辑的旧记录继续应用旧限制。"""
    return stricter_capacity(capacity, normalize_capacity(json_object(metadata).get(LOADING_LIMITS_KEY)))


def validate_limits(limits: dict[str, float], capacity: dict[str, float]) -> None:
    """用户配置不得放宽已知同维度额定容量。"""
    for key, limit in limits.items():
        if key in capacity and limit > capacity[key]:
            label = "体积（µL）" if key == "max_volume_ul" else "质量（g）"
            _invalid(f"{label}上限 {limit:g} 超过容器规格 {capacity[key]:g}")


def validate_quantity(quantity: float, unit: str, capacity: dict[str, float],
                      loading_limits: dict[str, float]) -> None:
    """按同维度单位换算校验，不把容积或化学密度当作粉体质量上限。"""
    validate_limits(loading_limits, capacity)
    effective = stricter_capacity(capacity, loading_limits)
    if not effective:
        return
    dimension = _UNITS.get(str(unit).strip().lower())
    if dimension is None:
        _invalid("已配置容量上限，请使用 µL、mL、L、mg、g 或 kg 计量")
    key, factor = dimension
    if loading_limits and key not in loading_limits:
        _invalid("本次装料上限与数量单位不匹配，请配置同维度上限")
    if key in effective:
        actual = quantity * factor
        if not math.isfinite(actual) or actual > effective[key] + max(1e-9, effective[key] * 1e-12):
            _invalid(f"数量 {quantity:g} {unit} 超过上限 {effective[key] / factor:g} {unit}")


def reagent_metadata(values: dict[str, Any], previous: Any = None) -> dict[str, Any]:
    """合并元数据，旧客户端省略字段不会清除装料上限或分装血缘。"""
    result = {**json_object(previous), **json_object(values.get("meta_data"))}
    if LOADING_LIMITS_KEY in result:
        limits = normalize_capacity(result[LOADING_LIMITS_KEY])
        if limits:
            result[LOADING_LIMITS_KEY] = limits
        else:
            result.pop(LOADING_LIMITS_KEY)
    return result


def validate_material_config(conn: sqlite3.Connection, material_uuid: str,
                             config: dict[str, Any], *, replace_loading_limits: bool = False,
                             updating_reagent_uuid: str | None = None) -> dict[str, Any]:
    """校验新的实例配置与已有装料，在物料更新的同一事务中调用。"""
    row = conn.execute(
        "SELECT m.data,t.meta_data FROM material m LEFT JOIN resource_template t "
        "ON t.uuid=m.resource_template_uuid AND t.deleted_at IS NULL WHERE m.uuid=?",
        (material_uuid,),
    ).fetchone()
    projection = capacity_projection(config, row["data"], row["meta_data"])
    validate_limits(normalize_capacity(config.get(CAPACITY_KEY)), projection["rated_capacity"])
    for reagent in conn.execute(
        "SELECT uuid,quantity,quantity_unit,meta_data FROM reagent "
        "WHERE material_uuid=? AND deleted_at IS NULL", (material_uuid,),
    ):
        # 合并编辑由调用方用新余量校验，不能以修改前余量阻止一次性下调。
        if reagent["uuid"] == updating_reagent_uuid:
            continue
        limits = {} if replace_loading_limits else normalize_capacity(
            json_object(reagent["meta_data"]).get(LOADING_LIMITS_KEY))
        validate_quantity(reagent["quantity"], reagent["quantity_unit"], projection["capacity"], limits)
    return projection
