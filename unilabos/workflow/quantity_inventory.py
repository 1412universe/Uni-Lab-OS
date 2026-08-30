"""工作流数量型库存绑定、预留投影与可恢复消费。

本模块复用工作流库既有 ``workflow_inventory_*`` 表和库存库既有
``reagent``、``current_substance``、``inventory_ledger``、``sync_outbox``。
两个 SQLite 事务域之间不伪装原子事务：先持久化可重放意图，再以稳定事件身份
更新库存，最后收敛工作流分配状态。
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.app.scheduler.inventory.workflow_quantity import (
    WorkflowQuantityInventoryAuthority,
    WorkflowQuantityReservationError,
)
from unilabos.workflow.store import (
    StoreConflict,
    StoreNotFound,
    WorkflowStore,
    utc_now,
)
from unilabos.workflow.task_input import PreparedTaskInput


def _json(value: Any) -> str:
    """编码稳定紧凑 JSON；参数必须是可编码业务值，返回 UTF-8 文本。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: Any, fallback: Any) -> Any:
    """解码持久 JSON；非法历史值返回调用方提供的安全默认值。"""

    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _uuid(value: Any, *, field: str) -> str:
    """规范非空 UUID；参数 ``field`` 用于稳定错误说明。"""

    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError) as error:
        raise StoreConflict(f"{field} 不是合法 UUID") from error
    if parsed.int == 0:
        raise StoreConflict(f"{field} 不能是 nil UUID")
    return str(parsed)


def _positive(value: Any, *, field: str) -> float:
    """规范有限正数；零、负数、NaN 与无穷值均关闭式拒绝。"""

    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise StoreConflict(f"{field} 必须是正数") from error
    if not math.isfinite(number) or number <= 0:
        raise StoreConflict(f"{field} 必须是正数")
    return number


def _non_negative(value: Any, *, field: str) -> float:
    """规范有限非负数；用于设备上报的实际消耗量。"""

    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise StoreConflict(f"{field} 必须是非负数") from error
    if not math.isfinite(number) or number < 0:
        raise StoreConflict(f"{field} 必须是非负数")
    return number


def _quantity_equal(left: float, right: float) -> bool:
    """按 Backend 相同的相对容差比较两个库存数量。"""

    tolerance = max(1.0, abs(left), abs(right)) * 1e-9
    return abs(left - right) <= tolerance


class WorkflowQuantityInventory:
    """隐藏工作流库与库存库之间的数量型库存协议。

    工作流库保存逻辑需求、Task 分配与 Saga；库存库保存实际余量和不可变台账。
    该对象只复用调度器已经持有的 ``InventoryService``，不会创建第二库存权威。
    """

    def __init__(self, workflow_store: WorkflowStore, inventory_service: Any) -> None:
        """绑定两个既有写权威。

        参数：``workflow_store`` 是任务与分配写模型；``inventory_service`` 必须是
        调度器持有且公开 ``store/edge_id/lab_id`` 的库存权威。返回无。异常：组合
        未提供可事务写入的库存库时抛 ``TypeError``，禁止降级成不校验数量。
        """

        inventory_store = getattr(inventory_service, "store", None)
        if not isinstance(inventory_store, InventoryStore):
            raise TypeError("数量型工作流库存必须复用调度器的 InventoryStore")
        self._workflow_store = workflow_store
        self._authority = WorkflowQuantityInventoryAuthority(
            inventory_store,
            edge_id=str(getattr(inventory_service, "edge_id", "edge-default")),
            lab_id=str(getattr(inventory_service, "lab_id", "edge-lab")),
        )

    def prepare_task_allocations(
        self,
        connection: sqlite3.Connection,
        *,
        graph: Mapping[str, Any],
        prepared: PreparedTaskInput,
        task_uuid: str,
        bindings: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """在任务创建事务内校验显式绑定并构造分配行。

        参数：``connection`` 是已取得工作流写锁的事务；``graph/prepared`` 来自
        同一图快照；``task_uuid`` 是本次运行身份；``bindings`` 是 HTTP
        ``inventory_bindings``。返回：可与 Task/Jobs 同事务插入的分配列表。
        异常：需求缺绑定、拆分/类型/单位/化学身份不匹配、库存缺失或可用量不足
        时抛 ``StoreConflict``，调用方回滚任务与全部作业。
        """

        allocations = self._build_task_allocations(
            graph=graph,
            prepared=prepared,
            task_uuid=task_uuid,
            bindings=bindings,
        )
        try:
            self._authority.reserve_task(task_uuid, allocations)
        except WorkflowQuantityReservationError as error:
            raise StoreConflict(str(error)) from error
        return allocations

    def preflight_task_allocations(
        self,
        *,
        graph: Mapping[str, Any],
        prepared: PreparedTaskInput,
        bindings: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """只读检查数量型库存绑定能否完成整任务准入。

        参数：图快照、候选执行计划和运行时绑定与正式创建路径完全相同。返回：
        经过身份、单位、需求总量和当前可用量验证的分配预览。异常：任一条件不满足
        时抛 ``StoreConflict``。本方法不写 WorkflowTask、预留、台账或 Outbox；
        正式提交仍须在写事务内重新检查。
        """

        allocations = self._build_task_allocations(
            graph=graph,
            prepared=prepared,
            task_uuid="00000000-0000-4000-8000-000000000001",
            bindings=bindings,
        )
        try:
            self._authority.preflight_task(allocations)
        except WorkflowQuantityReservationError as error:
            raise StoreConflict(str(error)) from error
        return allocations

    def _build_task_allocations(
        self,
        *,
        graph: Mapping[str, Any],
        prepared: PreparedTaskInput,
        task_uuid: str,
        bindings: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """规范绑定并构造任务分配，不产生任何持久写入。"""

        job_by_node = {
            str(job.get("workflow_node_uuid")): str(job.get("uuid"))
            for job in prepared.jobs
        }
        requirements = [
            dict(requirement)
            for requirement in graph.get("inventory_requirements", [])
            if isinstance(requirement, Mapping)
            and str(requirement.get("consume_node_uuid")) in job_by_node
        ]
        requirement_by_key = {
            str(requirement.get("requirement_key") or "").strip(): requirement
            for requirement in requirements
        }
        if "" in requirement_by_key or len(requirement_by_key) != len(requirements):
            raise StoreConflict("工作流库存需求键缺失或重复")

        normalized_bindings: dict[str, list[dict[str, Any]]] = {}
        seen: set[tuple[str, str, str]] = set()
        seen_node_inventory: set[tuple[str, str, str]] = set()
        for index, raw in enumerate(bindings):
            if not isinstance(raw, Mapping):
                raise StoreConflict(f"inventory_bindings[{index}] 必须是对象")
            requirement_key = str(raw.get("requirement_key") or "").strip()
            requirement = requirement_by_key.get(requirement_key)
            if requirement is None:
                raise StoreConflict(
                    f"inventory_bindings[{index}] 未对应本次执行的逻辑需求"
                )
            inventory_type = str(raw.get("inventory_type") or "").strip().lower()
            expected_type = (
                "reagent"
                if requirement.get("target_type") == "reagent_info"
                else "current_substance"
            )
            if inventory_type != expected_type:
                raise StoreConflict(f"库存绑定 {requirement_key} 的类型与需求不匹配")
            inventory_uuid = _uuid(
                raw.get("inventory_uuid"),
                field=f"inventory_bindings[{index}].inventory_uuid",
            )
            reserved_quantity = _positive(
                raw.get("reserved_quantity"),
                field=f"inventory_bindings[{index}].reserved_quantity",
            )
            quantity_unit = str(raw.get("quantity_unit") or "").strip()
            if (
                quantity_unit.casefold()
                != str(requirement.get("quantity_unit") or "").strip().casefold()
            ):
                raise StoreConflict(
                    f"库存绑定 {requirement_key} 必须使用需求声明的单位"
                )
            identity = (requirement_key, inventory_type, inventory_uuid)
            if identity in seen:
                raise StoreConflict(f"库存绑定 {requirement_key} 包含重复库存实例")
            seen.add(identity)
            node_identity = (
                str(requirement["consume_node_uuid"]),
                inventory_type,
                inventory_uuid,
            )
            if node_identity in seen_node_inventory:
                raise StoreConflict("同一节点不能把一个库存实例绑定给多个逻辑需求")
            seen_node_inventory.add(node_identity)
            normalized_bindings.setdefault(requirement_key, []).append(
                {
                    "requirement_key": requirement_key,
                    "inventory_type": inventory_type,
                    "inventory_uuid": inventory_uuid,
                    "reserved_quantity": reserved_quantity,
                    "quantity_unit": quantity_unit,
                }
            )

        allocations: list[dict[str, Any]] = []
        for requirement in requirements:
            requirement_key = str(requirement["requirement_key"])
            selected = normalized_bindings.get(requirement_key, [])
            if not selected:
                raise StoreConflict(f"库存需求 {requirement_key} 没有运行时绑定")
            if not bool(requirement.get("allow_split")) and len(selected) != 1:
                raise StoreConflict(f"库存需求 {requirement_key} 不允许拆分绑定")
            total = sum(float(binding["reserved_quantity"]) for binding in selected)
            required = float(requirement["required_quantity"])
            if not _quantity_equal(total, required):
                raise StoreConflict(
                    f"库存需求 {requirement_key} 预留 {total:g}，应为 {required:g}"
                )
            for binding in selected:
                try:
                    content = self._authority.content(
                        binding["inventory_type"], binding["inventory_uuid"]
                    )
                except WorkflowQuantityReservationError as error:
                    raise StoreConflict(str(error)) from error
                if (
                    content["quantity_unit"].casefold()
                    != binding["quantity_unit"].casefold()
                ):
                    raise StoreConflict("库存实例单位与工作流绑定单位不一致")
                if requirement.get("target_type") == "reagent_info" and content.get(
                    "reagent_info_uuid"
                ) != requirement.get("reagent_info_uuid"):
                    raise StoreConflict("试剂实例与工作流要求的试剂身份不一致")
                allocation_uuid = str(
                    uuid5(
                        NAMESPACE_URL,
                        "unilab:workflow-inventory-allocation:"
                        f"{task_uuid}:{requirement_key}:{binding['inventory_type']}:"
                        f"{binding['inventory_uuid']}",
                    )
                )
                allocations.append(
                    {
                        "uuid": allocation_uuid,
                        "workflow_task_uuid": task_uuid,
                        "workflow_node_job_uuid": job_by_node[
                            str(requirement["consume_node_uuid"])
                        ],
                        "requirement_key": requirement_key,
                        "inventory_type": binding["inventory_type"],
                        "inventory_uuid": binding["inventory_uuid"],
                        "material_uuid": content["material_uuid"],
                        "reserved_quantity": binding["reserved_quantity"],
                        "quantity_unit": binding["quantity_unit"],
                    }
                )
        return allocations

    def consume_successful_job(
        self,
        *,
        task_uuid: str,
        job_uuid: str,
        consumptions: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """结算一个明确成功作业的数量型库存。

        参数：任务和作业身份限定既有预留；``consumptions`` 是 Edge 可选实际用量，
        未上报的已预留库存按预留量消费。返回：由统一库存台账还原的消费事实。
        异常：未计划库存、重复上报、实际量超过预留或余额不足时抛
        ``StoreConflict``；意图会在扣减前持久化，重启后可幂等恢复。
        """

        normalized_task = _uuid(task_uuid, field="workflow_task_uuid")
        normalized_job = _uuid(job_uuid, field="workflow_node_job_uuid")
        operation_key = f"consume:{normalized_job}"
        with self._workflow_store.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_inventory_allocation
                WHERE workflow_task_uuid = ? AND workflow_node_job_uuid = ?
                ORDER BY inventory_type, inventory_uuid
                """,
                (normalized_task, normalized_job),
            ).fetchall()
            allocations = [dict(row) for row in rows]
            if allocations and all(row["status"] == "consumed" for row in allocations):
                return self._authority.list_job_consumptions(normalized_job)
            if any(row["status"] != "reserved" for row in allocations):
                raise StoreConflict("工作流库存分配已被释放或部分结算")
            audit_context = self._job_audit_context(
                connection,
                task_uuid=normalized_task,
                job_uuid=normalized_job,
            )
            records = self._build_consumption_records(
                allocations,
                consumptions,
                audit_context=audit_context,
            )
            if not allocations and consumptions:
                raise StoreConflict("作业上报了未在执行计划中预留的库存消耗")
            if not records:
                return []
            now = utc_now()
            connection.execute(
                """
                INSERT INTO workflow_inventory_saga(
                    workflow_task_uuid,status,operation_key,payload,last_error,
                    attempt,update_time
                ) VALUES (?, 'consume_pending', ?, ?, NULL, 1, ?)
                ON CONFLICT(workflow_task_uuid) DO UPDATE SET
                    status='consume_pending', operation_key=excluded.operation_key,
                    payload=excluded.payload, last_error=NULL,
                    attempt=workflow_inventory_saga.attempt + 1,
                    update_time=excluded.update_time
                """,
                (normalized_task, operation_key, _json({"records": records}), now),
            )
        return self._apply_pending_consumption(
            task_uuid=normalized_task,
            operation_key=operation_key,
            records=records,
        )

    def recover_pending(self) -> None:
        """重放未收敛消费/释放并清理任务创建崩溃残留。

        参数与返回均为空。稳定事件重放不会重复扣减；库存已预留但 Task 未落库时
        执行补偿释放，库存已提交但工作流投影未提交时只补齐工作流状态。
        """

        def task_exists(task_uuid: str) -> bool:
            """查询工作流任务是否已提交；参数为任务 UUID，返回存在性。"""

            try:
                self._workflow_store.get_task(task_uuid)
            except StoreNotFound:
                return False
            return True

        self._authority.release_orphans(task_exists)
        with self._workflow_store.transaction() as connection:
            rows = connection.execute(
                """
                SELECT workflow_task_uuid,operation_key,payload
                FROM workflow_inventory_saga
                WHERE status IN ('consume_pending','release_pending')
                ORDER BY update_time, workflow_task_uuid
                """
            ).fetchall()
        for row in rows:
            operation_key = str(row["operation_key"])
            payload = _load(row["payload"], {})
            if operation_key.startswith("release:"):
                reason = (
                    str(payload.get("reason") or "workflow_terminal")
                    if isinstance(payload, Mapping)
                    else "workflow_terminal"
                )
                self.release_task(str(row["workflow_task_uuid"]), reason=reason)
                continue
            records = payload.get("records") if isinstance(payload, Mapping) else None
            if not isinstance(records, list):
                raise StoreConflict("库存消费 Saga 载荷损坏")
            self._apply_pending_consumption(
                task_uuid=str(row["workflow_task_uuid"]),
                operation_key=operation_key,
                records=[
                    dict(record) for record in records if isinstance(record, Mapping)
                ],
            )

    def discard_uncommitted_task(self, task_uuid: str) -> None:
        """补偿任务创建事务未提交前已经写入库存权威的数量预留。

        参数：``task_uuid`` 是尚未成为工作流事实的拟创建任务身份。返回无。异常：
        库存事务失败原样传播，调用方必须报告内部错误；相同身份重复补偿幂等，且
        只释放本协议写入的现有库存预留。
        """

        normalized = _uuid(task_uuid, field="workflow_task_uuid")
        self._authority.release_task(
            normalized,
            reason="workflow_task_creation_rolled_back",
        )

    def release_task(self, task_uuid: str, *, reason: str) -> None:
        """先释放库存权威预留，再把工作流分配投影收敛为 released。

        参数：``task_uuid`` 是终态任务；``reason`` 是审计原因。返回无。异常：
        库存释放失败时保留 ``release_pending`` 供恢复扫描重放；重复释放幂等，
        绝不先把工作流投影标记为已释放。
        """

        normalized_task = _uuid(task_uuid, field="workflow_task_uuid")
        with self._workflow_store.transaction() as connection:
            pending = connection.execute(
                "SELECT COUNT(*) FROM workflow_inventory_allocation "
                "WHERE workflow_task_uuid=? AND status='reserved'",
                (normalized_task,),
            ).fetchone()[0]
            if not pending:
                return
            now = utc_now()
            connection.execute(
                """
                INSERT INTO workflow_inventory_saga(
                    workflow_task_uuid,status,operation_key,payload,last_error,
                    attempt,update_time
                ) VALUES (?, 'release_pending', ?, ?, NULL, 1, ?)
                ON CONFLICT(workflow_task_uuid) DO UPDATE SET
                    status='release_pending', operation_key=excluded.operation_key,
                    payload=excluded.payload,last_error=NULL,
                    attempt=workflow_inventory_saga.attempt+1,
                    update_time=excluded.update_time
                """,
                (
                    normalized_task,
                    f"release:{normalized_task}",
                    _json({"reason": str(reason)}),
                    now,
                ),
            )
        try:
            self._authority.release_task(normalized_task, reason=reason)
        except BaseException as error:
            with self._workflow_store.transaction() as connection:
                connection.execute(
                    "UPDATE workflow_inventory_saga SET last_error=?,update_time=? "
                    "WHERE workflow_task_uuid=? AND status='release_pending'",
                    (str(error), utc_now(), normalized_task),
                )
            raise
        with self._workflow_store.transaction() as connection:
            connection.execute(
                """
                UPDATE workflow_inventory_allocation
                SET status='released', released_at=?, revision=revision+1
                WHERE workflow_task_uuid=? AND status='reserved'
                """,
                (utc_now(), normalized_task),
            )
            connection.execute(
                """
                UPDATE workflow_inventory_saga
                SET status='settled',last_error=NULL,update_time=?
                WHERE workflow_task_uuid=? AND operation_key=?
                """,
                (utc_now(), normalized_task, f"release:{normalized_task}"),
            )

    def list_task_consumptions(self, task_uuid: str) -> list[dict[str, Any]]:
        """读取一个任务的库存消费台账投影；结果按发生时间和事件身份排序。"""

        normalized = _uuid(task_uuid, field="workflow_task_uuid")
        return self._authority.list_task_consumptions(normalized)

    def list_job_consumptions(self, job_uuid: str) -> list[dict[str, Any]]:
        """读取一个作业的库存消费台账投影；不存在时返回空数组。"""

        normalized = _uuid(job_uuid, field="workflow_node_job_uuid")
        return self._authority.list_job_consumptions(normalized)

    def list_reagent_consumptions(self, reagent_uuid: str) -> list[dict[str, Any]]:
        """读取试剂库存实例的消费谱系，删除后的历史身份仍可查询。

        参数：``reagent_uuid`` 是库存实例身份。返回：按发生时间倒序的消费事实；
        无历史时为空。异常：非法 UUID 抛 ``StoreConflict``。
        """

        normalized = _uuid(reagent_uuid, field="reagent_uuid")
        return self._authority.list_reagent_consumptions(normalized)

    def _build_consumption_records(
        self,
        allocations: Sequence[Mapping[str, Any]],
        consumptions: Sequence[Mapping[str, Any]],
        *,
        audit_context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """把 Edge 实际用量、预留分配与冻结审计上下文合并成稳定记录。"""

        actual_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
        for index, raw in enumerate(consumptions):
            if not isinstance(raw, Mapping):
                raise StoreConflict(f"inventory_consumptions[{index}] 必须是对象")
            inventory_type = str(raw.get("inventory_type") or "").strip().lower()
            if inventory_type not in {"reagent", "current_substance"}:
                raise StoreConflict(f"inventory_consumptions[{index}] 类型无效")
            inventory_uuid = _uuid(
                raw.get("inventory_uuid"),
                field=f"inventory_consumptions[{index}].inventory_uuid",
            )
            identity = (inventory_type, inventory_uuid)
            if identity in actual_by_identity:
                raise StoreConflict("同一库存实例不能重复上报消耗")
            quantity_unit = str(raw.get("quantity_unit") or "").strip()
            if not quantity_unit:
                raise StoreConflict(f"inventory_consumptions[{index}] 缺少数量单位")
            actual_by_identity[identity] = {
                "actual_quantity": _non_negative(
                    raw.get("actual_quantity"),
                    field=f"inventory_consumptions[{index}].actual_quantity",
                ),
                "reported_quantity_unit": quantity_unit,
            }
        records: list[dict[str, Any]] = []
        planned_identities: set[tuple[str, str]] = set()
        for allocation in allocations:
            identity = (
                str(allocation["inventory_type"]),
                str(allocation["inventory_uuid"]),
            )
            if identity in planned_identities:
                raise StoreConflict("作业包含重复的库存预留")
            planned_identities.add(identity)
            actual = actual_by_identity.get(identity)
            actual_quantity = (
                float(actual["actual_quantity"])
                if actual is not None
                else float(allocation["reserved_quantity"])
            )
            event_uuid = str(
                uuid5(
                    NAMESPACE_URL,
                    "unilab:workflow-inventory-consumption:"
                    f"{allocation['workflow_node_job_uuid']}:{allocation['uuid']}",
                )
            )
            records.append(
                {
                    **dict(allocation),
                    **dict(audit_context),
                    "event_uuid": event_uuid,
                    "planned_quantity": float(allocation["reserved_quantity"]),
                    "actual_quantity": actual_quantity,
                    "reported_quantity_unit": (
                        str(actual["reported_quantity_unit"])
                        if actual is not None
                        else str(allocation["quantity_unit"])
                    ),
                }
            )
        unplanned = actual_by_identity.keys() - planned_identities
        if unplanned:
            raise StoreConflict("作业上报了未在执行计划中预留的库存消耗")
        return records

    @staticmethod
    def _job_audit_context(
        connection: sqlite3.Connection,
        *,
        task_uuid: str,
        job_uuid: str,
    ) -> dict[str, Any]:
        """从冻结任务和作业读取消费审计名称。

        参数：工作流事务、任务与作业身份。返回：节点身份、工作流名和节点名；
        快照缺少展示名时使用空字符串。异常：Task/Job 关系不存在时关闭式冲突。
        """

        row = connection.execute(
            """
            SELECT task.workflow_snapshot,workflow.name AS workflow_name,
                   job.workflow_node_uuid
            FROM workflow_task AS task
            JOIN workflow ON workflow.uuid=task.workflow_uuid
            JOIN workflow_node_job AS job
              ON job.workflow_task_uuid=task.uuid AND job.uuid=?
            WHERE task.uuid=? AND task.deleted_at IS NULL
              AND workflow.deleted_at IS NULL AND job.deleted_at IS NULL
            """,
            (job_uuid, task_uuid),
        ).fetchone()
        if row is None:
            raise StoreConflict("库存消费所属 Task 或 Job 不存在")
        snapshot = _load(row["workflow_snapshot"], {})
        node_name = ""
        if isinstance(snapshot, Mapping):
            nodes = snapshot.get("nodes")
            if isinstance(nodes, list):
                for node in nodes:
                    if isinstance(node, Mapping) and str(node.get("uuid")) == str(
                        row["workflow_node_uuid"]
                    ):
                        node_name = str(node.get("name") or "")
                        break
        return {
            "workflow_node_uuid": str(row["workflow_node_uuid"]),
            "workflow_name": str(row["workflow_name"] or ""),
            "node_name": node_name,
        }

    def _apply_pending_consumption(
        self,
        *,
        task_uuid: str,
        operation_key: str,
        records: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """在库存权威应用消费，再幂等收敛工作流侧分配与 Saga。"""

        try:
            self._authority.apply_consumption(
                task_uuid=task_uuid,
                operation_key=operation_key,
                records=records,
            )
        except BaseException as error:
            with self._workflow_store.transaction() as connection:
                connection.execute(
                    """
                    UPDATE workflow_inventory_saga
                    SET last_error=?,update_time=?
                    WHERE workflow_task_uuid=? AND operation_key=?
                    """,
                    (str(error), utc_now(), task_uuid, operation_key),
                )
            raise

        with self._workflow_store.transaction() as connection:
            allocation_uuids = [str(record["uuid"]) for record in records]
            marks = ",".join("?" for _ in allocation_uuids)
            if allocation_uuids:
                connection.execute(
                    f"""
                    UPDATE workflow_inventory_allocation
                    SET status='consumed',consumed_at=?,revision=revision+1
                    WHERE uuid IN ({marks}) AND workflow_task_uuid=?
                      AND status='reserved'
                    """,
                    (utc_now(), *allocation_uuids, task_uuid),
                )
            remaining = connection.execute(
                """
                SELECT COUNT(*) FROM workflow_inventory_allocation
                WHERE workflow_task_uuid=? AND status='reserved'
                """,
                (task_uuid,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE workflow_inventory_saga
                SET status=?,last_error=NULL,update_time=?
                WHERE workflow_task_uuid=? AND operation_key=?
                """,
                (
                    "reserved" if remaining else "settled",
                    utc_now(),
                    task_uuid,
                    operation_key,
                ),
            )
        job_uuid = str(records[0]["workflow_node_job_uuid"]) if records else ""
        return self._authority.list_job_consumptions(job_uuid) if job_uuid else []


__all__ = ["WorkflowQuantityInventory"]
