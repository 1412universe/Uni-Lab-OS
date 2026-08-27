"""工作流数量库存的库存侧权威协议。

本模块复用既有 ``inventory_reservation``、``inventory_ledger`` 与
``sync_outbox``。工作流库只保存分配投影和 Saga；可用数量、活动预留、实际扣减
与释放事实均在同一个库存 SQLite 写事务中判断和提交。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from unilabos.app.scheduler.inventory.store import InventoryStore


class WorkflowQuantityReservationError(ValueError):
    """表示工作流数量预留、释放或消费违反库存权威约束。"""


def _json(value: Any) -> str:
    """编码稳定 JSON；参数必须可编码，返回用于幂等比较的紧凑文本。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: Any, fallback: Any) -> Any:
    """解码持久 JSON；参数非法时返回调用方提供的安全默认值。"""

    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _quantity_unit(unit: Any) -> tuple[str, float] | None:
    """识别 Backend 支持的质量或体积单位；未知单位返回 ``None``。"""

    definitions = {
        "mg": ("mass", 0.001),
        "g": ("mass", 1.0),
        "kg": ("mass", 1000.0),
        "µl": ("volume", 0.001),
        "ul": ("volume", 0.001),
        "ml": ("volume", 1.0),
        "l": ("volume", 1000.0),
    }
    return definitions.get(str(unit or "").strip().casefold())


def convert_quantity(
    quantity: float,
    source_unit: str,
    target_unit: str,
    *,
    inventory_type: str,
    physical_state: Any,
    concentration_value: Any,
    density_g_per_ml: Any,
) -> tuple[float, dict[str, Any]] | None:
    """按 Backend 规则把上报数量换算到库存单位。

    参数：数量、源/目标单位及试剂物性。返回：换算数量和审计信息；未知单位或
    不安全的质量/体积跨维度换算返回 ``None``。异常：无，调用方按单位不匹配
    记录事实且不得扣减库存。
    """

    source = _quantity_unit(source_unit)
    target = _quantity_unit(target_unit)
    if source is None or target is None:
        return None
    source_dimension, source_rate = source
    target_dimension, target_rate = target
    if source_dimension == target_dimension:
        converted = quantity * source_rate / target_rate
        return converted, {
            "source_quantity": quantity,
            "source_unit": source_unit,
            "target_unit": target_unit,
            "result_quantity": converted,
            "kind": "same_dimension",
        }
    try:
        density = float(density_g_per_ml)
    except (TypeError, ValueError):
        return None
    if (
        inventory_type != "reagent"
        or str(physical_state or "").strip().casefold() != "liquid"
        or concentration_value is not None
        or not math.isfinite(density)
        or density <= 0
    ):
        return None
    source_base = quantity * source_rate
    converted = (
        source_base / density / target_rate
        if source_dimension == "mass"
        else source_base * density / target_rate
    )
    return converted, {
        "source_quantity": quantity,
        "source_unit": source_unit,
        "target_unit": target_unit,
        "result_quantity": converted,
        "kind": "cross_dimension",
        "density_g_per_ml": density,
    }


def _workflow_quantity_allocations(raw: Any) -> list[dict[str, Any]]:
    """读取库存预留载荷中的数量分配；非本协议记录返回空数组。"""

    payload = _load(raw, {})
    if not isinstance(payload, Mapping) or payload.get("kind") != "workflow_quantity":
        return []
    allocations = payload.get("allocations")
    if not isinstance(allocations, list) or any(
        not isinstance(item, Mapping) for item in allocations
    ):
        raise WorkflowQuantityReservationError("库存数量预留载荷损坏")
    return [dict(item) for item in allocations]


def active_workflow_reserved_quantity(
    connection: sqlite3.Connection,
    *,
    inventory_type: str,
    inventory_uuid: str,
    excluding_task_uuid: str = "",
) -> float:
    """在库存事务内汇总一个实例的活动工作流预留。

    参数：库存连接、实例类型/身份及可选排除的任务身份。返回：与库存实例同单位的
    活动预留总量。异常：预留载荷损坏时关闭式失败，禁止继续修改库存。
    """

    rows = connection.execute(
        "SELECT workflow_id,amounts_json FROM inventory_reservation "
        "WHERE status='active'"
    ).fetchall()
    total = 0.0
    for row in rows:
        if excluding_task_uuid and str(row["workflow_id"]) == excluding_task_uuid:
            continue
        for allocation in _workflow_quantity_allocations(row["amounts_json"]):
            if (
                str(allocation.get("inventory_type")) == inventory_type
                and str(allocation.get("inventory_uuid")) == inventory_uuid
            ):
                total += float(allocation["reserved_quantity"])
    return total


def assert_workflow_quantity_mutation_allowed(
    connection: sqlite3.Connection,
    *,
    inventory_type: str,
    inventory_uuid: str,
    quantity: float,
    current_unit: str,
    next_unit: str,
) -> None:
    """保护普通库存写操作不得侵占工作流活动预留。

    参数：库存事务、实例身份、修改后余量与修改前后单位。返回无。异常：修改单位或
    把余量降到活动预留以下时抛 ``WorkflowQuantityReservationError``；增加余量
    始终允许，且本函数不改变任何库存事实。
    """

    reserved = active_workflow_reserved_quantity(
        connection,
        inventory_type=inventory_type,
        inventory_uuid=inventory_uuid,
    )
    if reserved <= 1e-9:
        return
    if current_unit.casefold() != next_unit.casefold():
        raise WorkflowQuantityReservationError("活动工作流预留存在，不能修改库存单位")
    if float(quantity) + 1e-9 < reserved:
        raise WorkflowQuantityReservationError(
            f"修改后余量 {float(quantity):g} 小于工作流已预留数量 {reserved:g}"
        )


class WorkflowQuantityInventoryAuthority:
    """隐藏工作流协调器对库存表、台账和发件箱的全部了解。"""

    def __init__(self, store: InventoryStore, *, edge_id: str, lab_id: str) -> None:
        """绑定既有库存权威。

        参数：``store`` 是唯一库存写入口；Edge/Lab 身份用于事件发件箱。返回无。
        异常：无；所有写操作都复用 ``InventoryStore.transaction`` 串行提交。
        """

        self._store = store
        self._edge_id = str(edge_id)
        self._lab_id = str(lab_id)

    def content(self, inventory_type: str, inventory_uuid: str) -> dict[str, Any]:
        """读取活动库存实例与容器身份；不存在或已删除时关闭式失败。"""

        row = self._store.query_one(
            self._content_sql(inventory_type),
            (inventory_uuid,),
        )
        if row is None:
            raise WorkflowQuantityReservationError(
                f"库存实例 {inventory_type}:{inventory_uuid} 不存在或已删除"
            )
        return row

    def reserve_task(
        self,
        task_uuid: str,
        allocations: Sequence[Mapping[str, Any]],
    ) -> None:
        """在库存权威内全有或全无地预留一个任务的数量。

        参数：任务身份和工作流库待写分配。返回无。异常：库存缺失、单位变化、余量
        不足或同一任务重放内容冲突时抛 ``WorkflowQuantityReservationError``。
        同一任务/Job/分配内容重放幂等，不重复写预留、台账或发件箱。
        """

        by_job: dict[str, list[dict[str, Any]]] = {}
        for allocation in allocations:
            by_job.setdefault(str(allocation["workflow_node_job_uuid"]), []).append(
                dict(allocation)
            )
        expected_payloads = {
            job_uuid: {
                "kind": "workflow_quantity",
                "workflow_task_uuid": task_uuid,
                "workflow_node_job_uuid": job_uuid,
                "allocations": sorted(
                    items,
                    key=lambda item: (
                        str(item["inventory_type"]),
                        str(item["inventory_uuid"]),
                        str(item["uuid"]),
                    ),
                ),
            }
            for job_uuid, items in by_job.items()
        }
        with self._store.transaction() as connection:
            existing_rows = connection.execute(
                "SELECT * FROM inventory_reservation WHERE workflow_id=?",
                (task_uuid,),
            ).fetchall()
            existing_quantity = {
                str(row["node_id"]): row
                for row in existing_rows
                if _workflow_quantity_allocations(row["amounts_json"])
            }
            if existing_quantity:
                if set(existing_quantity) != set(expected_payloads):
                    raise WorkflowQuantityReservationError("任务数量预留身份发生变化")
                for job_uuid, expected in expected_payloads.items():
                    row = existing_quantity[job_uuid]
                    if row["status"] != "active" or _load(
                        row["amounts_json"], {}
                    ) != expected:
                        raise WorkflowQuantityReservationError(
                            "任务数量预留与首次提交内容冲突"
                        )
                return

            requested: dict[tuple[str, str], float] = {}
            for allocation in allocations:
                inventory_type = str(allocation["inventory_type"])
                inventory_uuid = str(allocation["inventory_uuid"])
                current = self._tx_content(
                    connection,
                    inventory_type=inventory_type,
                    inventory_uuid=inventory_uuid,
                )
                if str(current["quantity_unit"]).casefold() != str(
                    allocation["quantity_unit"]
                ).casefold():
                    raise WorkflowQuantityReservationError(
                        "库存实例单位与工作流绑定单位不一致"
                    )
                identity = (inventory_type, inventory_uuid)
                requested[identity] = requested.get(identity, 0.0) + float(
                    allocation["reserved_quantity"]
                )
            for (inventory_type, inventory_uuid), quantity in requested.items():
                current = self._tx_content(
                    connection,
                    inventory_type=inventory_type,
                    inventory_uuid=inventory_uuid,
                )
                reserved = active_workflow_reserved_quantity(
                    connection,
                    inventory_type=inventory_type,
                    inventory_uuid=inventory_uuid,
                    excluding_task_uuid=task_uuid,
                )
                if float(current["quantity"]) - reserved + 1e-9 < quantity:
                    raise WorkflowQuantityReservationError(
                        "库存实例可用数量不足，无法完成整任务预留"
                    )

            now_ms = int(time.time() * 1000)
            for job_uuid, payload in expected_payloads.items():
                reservation_uuid = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"unilab:workflow-quantity-reservation:{task_uuid}:{job_uuid}",
                    )
                )
                connection.execute(
                    "INSERT INTO inventory_reservation("
                    "reservation_id,workflow_id,node_id,attempt,status,amounts_json,"
                    "created_at,version) VALUES(?,?,?,1,'active',?,?,1)",
                    (reservation_uuid, task_uuid, job_uuid, _json(payload), now_ms),
                )
                event_uuid = str(
                    uuid5(NAMESPACE_URL, f"unilab:reserve-event:{reservation_uuid}")
                )
                InventoryStore.tx_append_inventory_event(
                    connection,
                    entry_uuid=event_uuid,
                    edge_id=self._edge_id,
                    lab_id=self._lab_id,
                    occurred_at=now_ms,
                    aggregate_type="workflow_inventory_reservation",
                    aggregate_id=reservation_uuid,
                    aggregate_version=1,
                    event_type="workflow_inventory.reserved",
                    payload={**payload, "reservation_state": "reserved"},
                    actor="workflow",
                    reason="workflow_inventory_reserve",
                    causation_id=f"reserve:{task_uuid}",
                    workflow_task_uuid=task_uuid,
                    workflow_node_job_uuid=job_uuid,
                )

    def release_task(self, task_uuid: str, *, reason: str) -> None:
        """幂等释放一个任务在库存权威中的全部活动数量预留。

        参数：任务身份与审计原因。返回无。异常：库存事务失败原样传播；已消费或
        已释放记录不回退，重复释放不追加事件。
        """

        with self._store.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM inventory_reservation "
                "WHERE workflow_id=? AND status='active' ORDER BY node_id",
                (task_uuid,),
            ).fetchall()
            now_ms = int(time.time() * 1000)
            for row in rows:
                allocations = _workflow_quantity_allocations(row["amounts_json"])
                if not allocations:
                    continue
                revision = int(row["version"]) + 1
                connection.execute(
                    "UPDATE inventory_reservation SET status='released',version=? "
                    "WHERE reservation_id=? AND status='active'",
                    (revision, row["reservation_id"]),
                )
                event_uuid = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"unilab:release-event:{row['reservation_id']}",
                    )
                )
                InventoryStore.tx_append_inventory_event(
                    connection,
                    entry_uuid=event_uuid,
                    edge_id=self._edge_id,
                    lab_id=self._lab_id,
                    occurred_at=now_ms,
                    aggregate_type="workflow_inventory_reservation",
                    aggregate_id=str(row["reservation_id"]),
                    aggregate_version=revision,
                    event_type="workflow_inventory.released",
                    payload={
                        "kind": "workflow_quantity",
                        "workflow_task_uuid": task_uuid,
                        "workflow_node_job_uuid": str(row["node_id"]),
                        "reservation_state": "released",
                        "reason": str(reason),
                    },
                    actor="workflow",
                    reason=str(reason),
                    causation_id=f"release:{task_uuid}",
                    workflow_task_uuid=task_uuid,
                    workflow_node_job_uuid=str(row["node_id"]),
                )

    def release_orphans(self, task_exists: Callable[[str], bool]) -> int:
        """释放库存已预留但工作流任务未落库的崩溃残留。

        参数：``task_exists`` 查询工作流权威。返回：完成释放的任务数。异常：库存
        载荷损坏或释放失败原样传播；存在任务的活动预留保持不变。
        """

        rows = self._store.query_all(
            "SELECT DISTINCT workflow_id,amounts_json FROM inventory_reservation "
            "WHERE status='active' ORDER BY workflow_id"
        )
        candidates = {
            str(row["workflow_id"])
            for row in rows
            if _workflow_quantity_allocations(row["amounts_json"])
        }
        released = 0
        for task_uuid in sorted(candidates):
            if task_exists(task_uuid):
                continue
            self.release_task(task_uuid, reason="orphan_task_not_committed")
            released += 1
        return released

    def apply_consumption(
        self,
        *,
        task_uuid: str,
        operation_key: str,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        """在库存事务内幂等扣减实际用量并结算对应预留。

        参数：任务、稳定操作键与冻结消费记录。返回无。异常：实例消失、实际用量
        超过预留、余量不足或修订冲突时关闭式失败；同一事件 UUID 重放不重复扣减。
        """

        if not records:
            return
        job_uuid = str(records[0]["workflow_node_job_uuid"])
        now_ms = int(time.time() * 1000)
        now_text = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._store.transaction() as connection:
            reservation = connection.execute(
                "SELECT * FROM inventory_reservation "
                "WHERE workflow_id=? AND node_id=? AND attempt=1",
                (task_uuid, job_uuid),
            ).fetchone()
            if reservation is None or not _workflow_quantity_allocations(
                reservation["amounts_json"]
            ):
                raise WorkflowQuantityReservationError("作业没有库存侧数量预留")
            if reservation["status"] not in {"active", "consumed"}:
                raise WorkflowQuantityReservationError("作业数量预留已被释放")
            for record in records:
                event_uuid = str(record["event_uuid"])
                if connection.execute(
                    "SELECT 1 FROM inventory_ledger WHERE entry_uuid=?",
                    (event_uuid,),
                ).fetchone() is not None:
                    continue
                self._tx_consume_record(
                    connection,
                    task_uuid=task_uuid,
                    operation_key=operation_key,
                    record=record,
                    now_ms=now_ms,
                    now_text=now_text,
                )
            if reservation["status"] == "active":
                revision = int(reservation["version"]) + 1
                connection.execute(
                    "UPDATE inventory_reservation SET status='consumed',version=? "
                    "WHERE reservation_id=? AND status='active'",
                    (revision, reservation["reservation_id"]),
                )

    def list_task_consumptions(self, task_uuid: str) -> list[dict[str, Any]]:
        """按任务返回本模块写入的实际消费事实。"""

        return self._consumptions(
            "workflow_task_uuid=?", (task_uuid,), reverse=False
        )

    def list_job_consumptions(self, job_uuid: str) -> list[dict[str, Any]]:
        """按作业返回本模块写入的实际消费事实。"""

        return self._consumptions(
            "workflow_node_job_uuid=?", (job_uuid,), reverse=False
        )

    def list_reagent_consumptions(self, reagent_uuid: str) -> list[dict[str, Any]]:
        """按试剂实例倒序返回实际消费谱系，删除后的历史仍可读取。"""

        return self._consumptions(
            "subject_type='reagent' AND aggregate_id=?", (reagent_uuid,), reverse=True
        )

    @staticmethod
    def _content_sql(inventory_type: str) -> str:
        """返回受控库存类型的活动实例查询；未知类型关闭式失败。"""

        if inventory_type == "reagent":
            return """
                SELECT reagent.uuid,reagent.material_uuid,reagent.reagent_info_uuid,
                       reagent.quantity,reagent.quantity_unit,reagent.revision,
                       reagent.physical_state,reagent.density_g_per_ml,
                       reagent.concentration_value,reagent_info.name AS display_name,
                       material.barcode AS container_barcode
                FROM reagent JOIN material ON material.uuid=reagent.material_uuid
                JOIN reagent_info ON reagent_info.uuid=reagent.reagent_info_uuid
                WHERE reagent.uuid=? AND reagent.deleted_at IS NULL
                  AND material.deleted_at IS NULL AND reagent_info.deleted_at IS NULL
            """
        if inventory_type == "current_substance":
            return """
                SELECT current_substance.uuid,current_substance.material_uuid,
                       current_substance.quantity,current_substance.quantity_unit,
                       current_substance.revision,NULL AS reagent_info_uuid,
                       NULL AS density_g_per_ml,
                       current_substance.physical_state,NULL AS concentration_value,
                       COALESCE(current_substance.name,material.name) AS display_name,
                       material.barcode AS container_barcode
                FROM current_substance
                JOIN material ON material.uuid=current_substance.material_uuid
                WHERE current_substance.uuid=? AND current_substance.deleted_at IS NULL
                  AND material.deleted_at IS NULL
            """
        raise WorkflowQuantityReservationError("库存类型无效")

    def _tx_content(
        self,
        connection: sqlite3.Connection,
        *,
        inventory_type: str,
        inventory_uuid: str,
    ) -> sqlite3.Row:
        """在既有库存事务内读取活动实例；缺失时关闭式失败。"""

        row = connection.execute(
            self._content_sql(inventory_type),
            (inventory_uuid,),
        ).fetchone()
        if row is None:
            raise WorkflowQuantityReservationError("库存实例不存在或已删除")
        return row

    def _tx_consume_record(
        self,
        connection: sqlite3.Connection,
        *,
        task_uuid: str,
        operation_key: str,
        record: Mapping[str, Any],
        now_ms: int,
        now_text: str,
    ) -> None:
        """在库存事务内应用单条稳定消费事件；调用方负责去重。"""

        inventory_type = str(record["inventory_type"])
        inventory_uuid = str(record["inventory_uuid"])
        current = self._tx_content(
            connection,
            inventory_type=inventory_type,
            inventory_uuid=inventory_uuid,
        )
        live_unit = str(current["quantity_unit"])
        planned_unit = str(record["quantity_unit"])
        reported_unit = str(record["reported_quantity_unit"])
        planned_conversion = convert_quantity(
            float(record["planned_quantity"]),
            planned_unit,
            live_unit,
            inventory_type=inventory_type,
            physical_state=current["physical_state"],
            concentration_value=current["concentration_value"],
            density_g_per_ml=current["density_g_per_ml"],
        )
        actual_conversion = convert_quantity(
            float(record["actual_quantity"]),
            reported_unit,
            live_unit,
            inventory_type=inventory_type,
            physical_state=current["physical_state"],
            concentration_value=current["concentration_value"],
            density_g_per_ml=current["density_g_per_ml"],
        )
        balance_before = float(current["quantity"])
        planned_quantity = float(record["planned_quantity"])
        actual_quantity = float(record["actual_quantity"])
        balance_after = balance_before
        status = "unit_mismatch"
        revision = int(current["revision"])
        if planned_conversion is not None and actual_conversion is not None:
            planned_quantity = planned_conversion[0]
            actual_quantity = actual_conversion[0]
            if actual_quantity > planned_quantity + 1e-9:
                raise WorkflowQuantityReservationError(
                    "实际消耗量不能超过本作业的预留量"
                )
            if balance_before + 1e-9 < actual_quantity:
                raise WorkflowQuantityReservationError(
                    "库存当前余量不足，不能提交作业成功结果"
                )
            balance_after = balance_before - actual_quantity
            status = "consumed"
            revision += 1
            table = "reagent" if inventory_type == "reagent" else "current_substance"
            changed = connection.execute(
                f"UPDATE {table} SET quantity=?,revision=?,update_time=? "
                "WHERE uuid=? AND deleted_at IS NULL AND revision=?",
                (
                    balance_after,
                    revision,
                    now_text,
                    inventory_uuid,
                    current["revision"],
                ),
            ).rowcount
            if changed != 1:
                raise WorkflowQuantityReservationError(
                    "库存修订已变化，请重放同一作业结果"
                )
        consumption = {
            "workflow_task_uuid": task_uuid,
            "workflow_node_job_uuid": str(record["workflow_node_job_uuid"]),
            "workflow_node_uuid": str(record["workflow_node_uuid"]),
            "planned_quantity": planned_quantity,
            "actual_quantity": actual_quantity,
            "planned_quantity_unit": planned_unit,
            "reported_quantity_unit": reported_unit,
            "quantity_unit": live_unit,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "status": status,
            "display_name": str(current["display_name"] or ""),
            "container_barcode": str(current["container_barcode"] or ""),
            "workflow_name": str(record["workflow_name"] or ""),
            "node_name": str(record["node_name"] or ""),
        }
        if (
            actual_conversion is not None
            and reported_unit.casefold() != live_unit.casefold()
        ):
            consumption["quantity_conversion"] = actual_conversion[1]
        event_action = "consume" if status == "consumed" else "adjust"
        InventoryStore.tx_append_inventory_event(
            connection,
            entry_uuid=str(record["event_uuid"]),
            edge_id=self._edge_id,
            lab_id=self._lab_id,
            occurred_at=now_ms,
            aggregate_type=inventory_type,
            aggregate_id=inventory_uuid,
            aggregate_version=revision,
            event_type=f"{inventory_type}.{event_action}",
            payload={
                "changes": {
                    "result": {
                        "quantity": balance_after,
                        "quantity_unit": live_unit,
                        "revision": revision,
                        "consumption": consumption,
                    }
                },
                "extension": {},
                **consumption,
            },
            actor="workflow",
            reason="workflow_inventory_consumption",
            causation_id=operation_key,
            material_uuid=str(record["material_uuid"]),
            subject_type=inventory_type,
            quantity_delta=balance_after - balance_before,
            quantity_unit=live_unit,
            revision=revision,
            workflow_task_uuid=task_uuid,
            workflow_node_job_uuid=str(record["workflow_node_job_uuid"]),
        )

    def _consumptions(
        self,
        where: str,
        params: tuple[Any, ...],
        *,
        reverse: bool,
    ) -> list[dict[str, Any]]:
        """按受控条件读取统一台账并投影消费 DTO。"""

        order = "DESC" if reverse else "ASC"
        rows = self._store.query_all(
            "SELECT * FROM inventory_ledger WHERE "
            + where
            + " AND subject_type IN ('reagent','current_substance') "
            + f"ORDER BY occurred_at {order},entry_uuid {order}",
            params,
        )
        return [
            self._consumption_row(row)
            for row in rows
            if self._is_consumption(row)
        ]

    @staticmethod
    def _is_consumption(row: Mapping[str, Any]) -> bool:
        """判断统一库存台账行是否包含工作流消费快照。"""

        payload = _load(row.get("delta_json"), {})
        return (
            isinstance(payload, Mapping)
            and isinstance(payload.get("workflow_task_uuid"), str)
            and "status" in payload
        )

    @staticmethod
    def _consumption_row(row: Mapping[str, Any]) -> dict[str, Any]:
        """把统一台账事件投影成 Backend 兼容库存消费事实。"""

        payload = _load(row.get("delta_json"), {})
        return {
            "uuid": str(row["entry_uuid"]),
            "workflow_task_uuid": payload.get("workflow_task_uuid"),
            "workflow_node_job_uuid": payload.get("workflow_node_job_uuid"),
            "workflow_node_uuid": payload.get("workflow_node_uuid"),
            "inventory_type": str(row["subject_type"]),
            "inventory_uuid": str(row["aggregate_id"]),
            "planned_quantity": payload.get("planned_quantity"),
            "actual_quantity": payload.get("actual_quantity"),
            "quantity_unit": payload.get("quantity_unit"),
            "balance_before": payload.get("balance_before"),
            "balance_after": payload.get("balance_after"),
            "status": payload.get("status"),
            "consumed_at": datetime.fromtimestamp(
                int(row["occurred_at"]) / 1000,
                timezone.utc,
            ).isoformat().replace("+00:00", "Z"),
            "meta_data": {
                "planned_quantity_unit": payload.get("planned_quantity_unit"),
                "reported_quantity_unit": payload.get("reported_quantity_unit"),
                **(
                    {"quantity_conversion": payload["quantity_conversion"]}
                    if payload.get("quantity_conversion") is not None
                    else {}
                ),
            },
            "display_name": str(payload.get("display_name") or ""),
            "container_barcode": str(payload.get("container_barcode") or ""),
            "workflow_name": str(payload.get("workflow_name") or ""),
            "node_name": str(payload.get("node_name") or ""),
        }


__all__ = [
    "WorkflowQuantityInventoryAuthority",
    "WorkflowQuantityReservationError",
    "active_workflow_reserved_quantity",
    "assert_workflow_quantity_mutation_allowed",
    "convert_quantity",
]
