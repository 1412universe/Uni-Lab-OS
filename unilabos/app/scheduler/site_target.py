"""转运动作的库位（Site）稳定身份解析。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any


class SiteTargetResolutionError(ValueError):
    """目标库位无法安全解析或当前不可使用。

    参数：``code`` 是稳定诊断码，``message`` 是中文原因。返回：异常对象。
    异常：构造过程不再抛出其他异常。
    """

    def __init__(self, code: str, message: str) -> None:
        """保存稳定错误码和中文诊断。

        参数：``code`` 用于日志和测试判断，``message`` 供调用方展示。返回：无。
        异常：不主动抛出其他异常。
        """

        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ResolvedSiteTarget:
    """已经由本地库存权威确认的目标库位。

    参数：``uuid`` 是库位稳定身份，``name`` 是设备执行所需名称，
    ``owner_material_uuid`` 是拥有该库位的父物料身份。返回：不可变值对象。
    异常：构造本身不校验，统一由 ``resolve_site_target`` 校验。
    """

    uuid: str
    name: str
    owner_material_uuid: str


def resolve_site_target(
    inventory: Any,
    *,
    owner_material_uuid: str,
    site_uuid: str = "",
    site_name: str = "",
    occupant_material_uuid: str = "",
) -> ResolvedSiteTarget:
    """按稳定身份优先、名称兜底解析一个可用目标库位。

    参数：``inventory`` 是带 ``store.query_one`` 的本地库存服务；
    ``owner_material_uuid`` 是 ``mount_resource.uuid``；``site_uuid`` 是优先使用
    的库位稳定身份；``site_name`` 是旧工作流兼容名称；
    ``occupant_material_uuid`` 是本次准备放入的物料身份。返回：数据库中的规范
    库位 UUID、名称和拥有者。异常：库位不存在、归属错误、UUID 与名称不一致，
    或库位已被其他物料占用时抛 ``SiteTargetResolutionError``，不得退化执行。
    """

    store = getattr(inventory, "store", None)
    query_one = getattr(store, "query_one", None)
    if not callable(query_one):
        raise SiteTargetResolutionError(
            "site_authority_unavailable",
            "本地库存权威未初始化，无法解析目标库位",
        )

    # 四个身份分别表示库位拥有者、稳定选择器、兼容名称和准备放入的物料；
    # 名称只用于解析/一致性校验，互斥与归属始终使用稳定 UUID。
    owner = str(owner_material_uuid or "").strip()
    requested_uuid = str(site_uuid or "").strip()
    requested_name = str(site_name or "").strip()
    occupant = str(occupant_material_uuid or "").strip()
    if not owner:
        raise SiteTargetResolutionError(
            "site_owner_missing",
            "目标库位缺少所属父物料 mount_resource.uuid",
        )
    if not requested_uuid and not requested_name:
        raise SiteTargetResolutionError(
            "site_selector_missing",
            "目标库位必须提供 site_uuid 或 site 名称",
        )
    if occupant and owner == occupant:
        raise SiteTargetResolutionError(
            "site_self_mount",
            "待转移物料不能挂载到自身库位",
        )

    if requested_uuid:
        try:
            canonical_uuid = str(uuid.UUID(requested_uuid))
        except (AttributeError, TypeError, ValueError) as error:
            raise SiteTargetResolutionError(
                "invalid_site_uuid",
                "site_uuid 不是合法 UUID",
            ) from error
        row = query_one(
            "SELECT uuid, material_uuid, name, occupied_material_uuid, "
            "allowed_resource_template_uuids "
            "FROM site WHERE uuid=? AND deleted_at IS NULL",
            (canonical_uuid,),
        )
    else:
        row = query_one(
            "SELECT uuid, material_uuid, name, occupied_material_uuid, "
            "allowed_resource_template_uuids "
            "FROM site WHERE material_uuid=? AND LOWER(name)=LOWER(?) "
            "AND deleted_at IS NULL",
            (owner, requested_name),
        )

    if row is None:
        selector = (
            f"UUID {requested_uuid}" if requested_uuid else f"名称 {requested_name}"
        )
        raise SiteTargetResolutionError(
            "site_not_found",
            f"目标父物料 {owner} 下找不到库位（{selector}）",
        )
    if str(row["material_uuid"]) != owner:
        raise SiteTargetResolutionError(
            "site_owner_mismatch",
            f"库位 {row['uuid']} 不属于目标父物料 {owner}",
        )

    canonical_name = str(row["name"])
    if (
        requested_uuid
        and requested_name
        and canonical_name.casefold() != requested_name.casefold()
    ):
        raise SiteTargetResolutionError(
            "site_identity_mismatch",
            f"site_uuid {row['uuid']} 对应名称 {canonical_name}，与入参 {requested_name} 不一致",
        )

    occupied_by = str(row.get("occupied_material_uuid") or "").strip()
    if occupied_by and occupied_by != occupant:
        raise SiteTargetResolutionError(
            "site_occupied",
            f"目标库位 {canonical_name} 已被物料 {occupied_by} 占用",
        )
    # 空允许集表示不限制物料模板；非空时必须以待放物料的模板身份命中。
    raw_allowed_templates = row.get("allowed_resource_template_uuids") or []
    try:
        decoded_allowed_templates = (
            raw_allowed_templates
            if isinstance(raw_allowed_templates, (list, tuple))
            else json.loads(str(raw_allowed_templates))
        )
        allowed_templates = {
            str(item)
            for item in decoded_allowed_templates
            if str(item).strip()
        }
    except (TypeError, ValueError) as error:
        raise SiteTargetResolutionError(
            "invalid_site_template_constraint",
            f"目标库位 {canonical_name} 的允许物料类型配置无效",
        ) from error
    if allowed_templates:
        occupant_row = query_one(
            "SELECT resource_template_uuid FROM material "
            "WHERE uuid=? AND deleted_at IS NULL",
            (occupant,),
        )
        occupant_template_uuid = str(
            (occupant_row or {}).get("resource_template_uuid") or ""
        )
        if occupant_template_uuid not in allowed_templates:
            raise SiteTargetResolutionError(
                "site_template_not_allowed",
                f"物料 {occupant} 的模板不允许放入目标库位 {canonical_name}",
            )
    return ResolvedSiteTarget(
        uuid=str(row["uuid"]),
        name=canonical_name,
        owner_material_uuid=owner,
    )


__all__ = [
    "ResolvedSiteTarget",
    "SiteTargetResolutionError",
    "resolve_site_target",
]
