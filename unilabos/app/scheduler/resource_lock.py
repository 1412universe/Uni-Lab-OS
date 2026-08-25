"""本地调度器的物料与库位执行占用键及冲突判定。"""

from __future__ import annotations

from collections.abc import Collection, Iterable

_EXCLUSIVE = "exclusive"
_MATERIAL = "material"
_SITE = "site"


def material_lock_key(material_uuid: str) -> str:
    """生成整物料独占键。

    参数：``material_uuid`` 是物料（Material）的稳定身份。返回：
    ``material/{uuid}/exclusive`` 规范键。异常：无；调用方负责先校验身份。
    """

    return f"{_MATERIAL}/{material_uuid}/{_EXCLUSIVE}"


def site_lock_key(owner_material_uuid: str, site_uuid: str) -> str:
    """生成父物料下具体库位的独占键。

    参数：``owner_material_uuid`` 是拥有库位的父物料身份，``site_uuid`` 是库位
    （Site）稳定身份。返回：可与整物料键进行父子冲突判断的规范键。异常：无；
    调用方负责先校验两个身份以及归属关系。

    该键只服务当前 OS 进程的准入互斥，不具备跨重启恢复或栅栏令牌语义。
    """

    return f"{_MATERIAL}/{owner_material_uuid}/{_SITE}/{site_uuid}/{_EXCLUSIVE}"


def normalize_resource_lock_keys(keys: Iterable[str]) -> set[str]:
    """规范化一个作业持有的执行资源键集合。

    参数：``keys`` 可同时包含物料、库位和未来扩展资源键。返回：保留未知键，
    并在整物料键存在时删除同一物料下的冗余库位键。异常：无；无法识别的键
    不参与父子归并，但仍按原值保留，避免静默丢失既有互斥事实。
    """

    # ``whole_materials`` 是本作业已经整对象独占的父物料身份；其子 Site 键
    # 不再增加任何互斥能力，应从最终持有集合移除。
    normalized = set(keys)
    whole_materials: set[str] = set()
    for key in normalized:
        parsed = _parse_material_lock_key(key)
        if parsed is not None and parsed[1] is None:
            whole_materials.add(parsed[0])

    result: set[str] = set()
    for key in normalized:
        parsed = _parse_material_lock_key(key)
        if parsed is None or parsed[1] is None or parsed[0] not in whole_materials:
            result.add(key)
    return result


def conflicting_resource_lock_keys(
    requested: Collection[str],
    held: Collection[str],
) -> set[str]:
    """找出申请集合中与已持有集合冲突的执行资源键。

    参数：``requested`` 是候选作业准备取得的键，``held`` 是当前所有在途作业
    已持有的键。返回：发生冲突的申请键；整物料与其任一子库位互斥，同一库位
    互斥，同一物料下不同库位可并行；未知键继续按完全相等判断。异常：无。
    """

    # 先处理完全相等键，再补充整物料与子 Site 的层级冲突。
    conflicts = set(requested) & set(held)
    parsed_held = [
        parsed for key in held if (parsed := _parse_material_lock_key(key)) is not None
    ]
    for requested_key in requested:
        requested_lock = _parse_material_lock_key(requested_key)
        if requested_lock is None:
            continue
        requested_material, requested_site = requested_lock
        for held_material, held_site in parsed_held:
            if requested_material != held_material:
                continue
            if (
                requested_site is None
                or held_site is None
                or requested_site == held_site
            ):
                conflicts.add(requested_key)
                break
    return conflicts


def _parse_material_lock_key(key: str) -> tuple[str, str | None] | None:
    """解析规范物料/库位互斥键。

    参数：``key`` 是调度器保存的任意执行资源键。返回：物料 UUID 与可选库位
    UUID；不是规范物料/库位键时返回 ``None``。异常：无。
    """

    parts = key.split("/")
    if len(parts) == 3 and parts[0] == _MATERIAL and parts[2] == _EXCLUSIVE:
        return parts[1], None
    if (
        len(parts) == 5
        and parts[0] == _MATERIAL
        and parts[2] == _SITE
        and parts[4] == _EXCLUSIVE
    ):
        return parts[1], parts[3]
    return None


__all__ = [
    "conflicting_resource_lock_keys",
    "material_lock_key",
    "normalize_resource_lock_keys",
    "site_lock_key",
]
