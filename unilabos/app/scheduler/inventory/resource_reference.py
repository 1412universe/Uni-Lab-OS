"""从本地库存权威构造可信工作流资源身份只读解析器。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.workflow.models import validate_uuid
from unilabos.workflow.resource_reference import ResourceReferenceResolver


class InventoryResourceReferenceError(RuntimeError):
    """本地库存无法唯一证明部署资源 ID 对应的实际物料身份。"""


def build_inventory_resource_reference_resolver(
    inventory_store: InventoryStore,
) -> ResourceReferenceResolver:
    """构造按活动库存事实解析 ``resource_ref`` 的只读端口。

    参数：``inventory_store`` 是当前本地主机唯一库存权威（Inventory
    Authority）。返回：可按实际物料 UUID 或资源图 ``source_node_id`` 查询的
    解析函数；函数只返回实际物料 UUID 与资源模板 UUID，不返回可变快照。
    异常：参数不是 ``InventoryStore`` 时抛出 ``TypeError``；解析阶段的 SQLite
    故障或业务 ID 歧义抛出 ``InventoryResourceReferenceError``，调用编译器
    统一收敛为关闭式公共诊断。
    """

    if not isinstance(inventory_store, InventoryStore):
        raise TypeError("inventory_store 必须是 InventoryStore")

    def resolve_inventory_resource_reference(
        resource_id: str,
    ) -> Mapping[str, Any] | None:
        """解析一个实际物料 UUID 或资源图部署业务 ID。

        参数：``resource_id`` 是作者源码中的静态标识。返回：唯一活动物料的
        ``uuid`` 与 ``resource_template_uuid`` 摘要；身份不存在时返回 ``None``。
        异常：空 ID、数据库故障或多个活动物料共享业务 ID 时抛出
        ``InventoryResourceReferenceError``；绝不按名称、条码或模板猜测。
        """

        if (
            not isinstance(resource_id, str)
            or not resource_id.strip()
            or resource_id != resource_id.strip()
        ):
            raise InventoryResourceReferenceError("部署资源 ID 必须是非空字符串")
        try:
            try:
                # ``material_uuid`` 命中时仅按权威主键读取，禁止同字符串再回退
                # ``source_node_id`` 并制造两个不同的身份解释。
                material_uuid = validate_uuid(resource_id)
            except (TypeError, ValueError):
                material_uuid = None
            if material_uuid is not None:
                rows = inventory_store.query_all(
                    """
                    SELECT uuid, resource_template_uuid
                    FROM material
                    WHERE uuid = ? AND deleted_at IS NULL
                    LIMIT 2
                    """,
                    (material_uuid,),
                )
            else:
                # ``rows`` 只接受 C3 本地资源图启动投影写入的来源事实；其他物料
                # 的名称、条码或任意元数据不能取得部署资源身份权威。
                rows = inventory_store.query_all(
                    """
                    SELECT uuid, resource_template_uuid
                    FROM material
                    WHERE deleted_at IS NULL
                      AND json_extract(meta_data, '$.source') = 'resource-tree-set'
                      AND json_extract(meta_data, '$.source_node_id') = ?
                    ORDER BY uuid
                    LIMIT 2
                    """,
                    (resource_id,),
                )
        except sqlite3.Error as error:
            raise InventoryResourceReferenceError(
                "库存权威读取部署资源身份失败"
            ) from error
        if not rows:
            return None
        if len(rows) != 1:
            raise InventoryResourceReferenceError(
                f"部署资源 ID 未唯一映射到活动物料: {resource_id}"
            )
        # ``material_identity`` 是与数据库行分离的最小只读回执。
        material_identity = rows[0]
        return {
            "uuid": material_identity.get("uuid"),
            "resource_template_uuid": material_identity.get(
                "resource_template_uuid"
            ),
        }

    return resolve_inventory_resource_reference


__all__ = [
    "InventoryResourceReferenceError",
    "build_inventory_resource_reference_resolver",
]


class InventoryReagentReferenceResolver:
    """只读试剂目录端口：按 CAS 把作者声明解析为当前实验室的 ``reagent_info`` 身份。

    与 ``ux_reagent_info_cas_active`` 唯一索引同语义：只看未删除条目，按大小写
    不敏感的 CAS 精确匹配；不按名称、别名或分子式猜测。
    """

    def __init__(self, inventory_store: InventoryStore) -> None:
        if not isinstance(inventory_store, InventoryStore):
            raise TypeError("inventory_store 必须是 InventoryStore")
        self._inventory_store = inventory_store

    def resolve_reagent_cas(self, cas: str) -> str | None:
        """返回 CAS 对应的 ``reagent_info_uuid``；目录中不存在时返回 ``None``。

        参数：``cas`` 是作者源码里已通过格式与校验位检查的静态 CAS。异常：空值或
        SQLite 故障抛出 ``InventoryResourceReferenceError``，由编译器收敛为诊断。
        """
        if not isinstance(cas, str) or not cas.strip():
            raise InventoryResourceReferenceError("试剂 CAS 必须是非空字符串")
        try:
            rows = self._inventory_store.query_all(
                """
                SELECT uuid FROM reagent_info
                WHERE deleted_at IS NULL AND LOWER(cas) = LOWER(?)
                ORDER BY uuid
                """,
                (cas.strip(),),
            )
        except sqlite3.Error as error:
            raise InventoryResourceReferenceError(
                f"试剂目录查询失败：{error}"
            ) from error
        if not rows:
            return None
        if len(rows) > 1:
            raise InventoryResourceReferenceError(f"试剂目录中 CAS {cas} 不唯一")
        return str(rows[0]["uuid"])


def build_inventory_reagent_reference_resolver(
    inventory_store: InventoryStore,
) -> InventoryReagentReferenceResolver:
    """构造与库存权威同库的只读试剂目录端口。"""
    return InventoryReagentReferenceResolver(inventory_store)
