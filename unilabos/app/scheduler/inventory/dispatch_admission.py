"""库存权威中的原子作业派发准入、Claim 与 Fence。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from unilabos.app.scheduler.resource_lock import conflicting_resource_lock_keys


_SCOPES = frozenset({"device", "material", "material_site"})


@dataclass(frozen=True, slots=True)
class DispatchResource:
    """派发前必须全有或全无取得的一个库存资源。"""

    lock_key: str
    scope: str
    material_uuid: str = ""
    site_uuid: str = ""


@dataclass(frozen=True, slots=True)
class TransferDispatchCondition:
    """机械臂转运在取得 Claim 的同一事务内必须成立的物理条件。"""

    material_uuid: str
    source_owner_material_uuid: str
    source_site_uuid: str
    target_owner_material_uuid: str
    target_site_uuid: str
    executor_material_uuid: str
    gripper_site_uuid: str


@dataclass(frozen=True, slots=True)
class DispatchAdmissionRequest:
    """一次不可变派发效果请求及其完整资源和条件快照。"""

    effect_uuid: str
    task_uuid: str
    job_uuid: str
    attempt: int
    parameter_hash: str
    expected_change_set: Mapping[str, Any]
    resources: tuple[DispatchResource, ...]
    transfer: TransferDispatchCondition | None = None


@dataclass(frozen=True, slots=True)
class DispatchFence:
    """一个资源 Claim 对应的严格递增栅栏令牌。"""

    lock_key: str
    fencing_token: int


@dataclass(frozen=True, slots=True)
class DispatchPermit:
    """库存权威已经原子签发、可供工作流库投影的派发凭据。"""

    effect_uuid: str
    claim_uuid: str
    task_uuid: str
    job_uuid: str
    attempt: int
    parameter_hash: str
    expected_change_set: Mapping[str, Any]
    fences: tuple[DispatchFence, ...]

    def as_mapping(self) -> dict[str, Any]:
        """返回工作流派发意图可持久化的公共凭据形状。

        参数：无。返回：Claim、效果身份、参数哈希、预期 ChangeSet 和按资源键
        排序的 Fence 副本。异常：无；值已在库存事务签发前完成校验。
        """

        return {
            "effect_uuid": self.effect_uuid,
            "claim_uuid": self.claim_uuid,
            "parameter_hash": self.parameter_hash,
            "expected_change_set": dict(self.expected_change_set),
            "fences": [
                {
                    "lock_key": fence.lock_key,
                    "fencing_token": fence.fencing_token,
                }
                for fence in self.fences
            ],
        }


@dataclass(frozen=True, slots=True)
class DispatchAdmissionDecision:
    """派发准入成功凭据或正常资源竞争的等待原因。"""

    permit: DispatchPermit | None = None
    wait_code: str = ""
    wait_message: str = ""
    blocking_task_uuid: str = ""
    blocking_job_uuid: str = ""

    @property
    def acquired(self) -> bool:
        """返回本次请求是否已经取得完整派发凭据。"""

        return self.permit is not None

    @property
    def effect_uuid(self) -> str:
        """返回成功凭据的效果 UUID；等待时为空。"""

        return self.permit.effect_uuid if self.permit is not None else ""

    @property
    def claim_uuid(self) -> str:
        """返回成功凭据的 Claim UUID；等待时为空。"""

        return self.permit.claim_uuid if self.permit is not None else ""

    @property
    def fences(self) -> tuple[DispatchFence, ...]:
        """返回成功凭据的 Fence；等待时为空元组。"""

        return self.permit.fences if self.permit is not None else ()


class DispatchAdmissionConflict(ValueError):
    """派发请求或持久准入事实损坏，不能降级为资源等待。"""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS station_execution_claim (
    claim_uuid TEXT PRIMARY KEY,
    effect_uuid TEXT NOT NULL UNIQUE,
    task_uuid TEXT NOT NULL,
    job_uuid TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    parameter_hash TEXT NOT NULL CHECK (length(trim(parameter_hash)) > 0),
    expected_change_set TEXT NOT NULL,
    resource_keys TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('prepared', 'reserved', 'running', 'released', 'uncertain')
    ),
    acquired_at TEXT NOT NULL,
    committed_at TEXT,
    released_at TEXT,
    update_time TEXT NOT NULL,
    UNIQUE(job_uuid, attempt)
);
CREATE INDEX IF NOT EXISTS ix_station_execution_claim_task_state
ON station_execution_claim(task_uuid, state, acquired_at, claim_uuid);

CREATE TABLE IF NOT EXISTS station_execution_fence_counter (
    lock_key TEXT PRIMARY KEY,
    last_fencing_token INTEGER NOT NULL CHECK (last_fencing_token > 0),
    update_time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS station_execution_lock_lease (
    claim_uuid TEXT NOT NULL,
    lock_key TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('device', 'material', 'material_site')),
    material_uuid TEXT,
    site_uuid TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    state TEXT NOT NULL CHECK (
        state IN ('prepared', 'reserved', 'running', 'released', 'uncertain')
    ),
    acquired_at TEXT NOT NULL,
    released_at TEXT,
    update_time TEXT NOT NULL,
    PRIMARY KEY(claim_uuid, lock_key),
    FOREIGN KEY(claim_uuid) REFERENCES station_execution_claim(claim_uuid)
);
CREATE INDEX IF NOT EXISTS ix_station_execution_lock_lease_active
ON station_execution_lock_lease(state, lock_key, acquired_at, claim_uuid);
"""


def migrate_dispatch_admission_schema(connection: sqlite3.Connection) -> None:
    """幂等创建库存派发 Claim、Lease 与 Fence 表。

    参数：``connection`` 是库存初始化持有的 SQLite 连接。返回：无。异常：DDL
    或约束错误原样传播，由库存初始化整体回滚，禁止缺表降级运行。
    """

    connection.executescript(_SCHEMA)


def acquire_dispatch_permit(
    connection: sqlite3.Connection,
    request: DispatchAdmissionRequest,
) -> DispatchAdmissionDecision:
    """在库存写事务中复验条件并全有或全无取得资源 Claim。

    参数：``connection`` 已由 ``InventoryStore`` 以 ``BEGIN IMMEDIATE`` 串行化；
    ``request`` 包含稳定效果身份、最终参数哈希、预期变化、完整资源和可选转运
    条件。返回：成功时携带 ``DispatchPermit``；正常竞争时携带阻塞身份。异常：
    请求漂移、资源身份损坏或部署事实不完整时抛 ``DispatchAdmissionConflict``；
    条件暂时不满足由调用适配器转换为稳定库存条件错误。
    """

    normalized = _normalize_request(request)
    existing = connection.execute(
        "SELECT * FROM station_execution_claim WHERE job_uuid=? AND attempt=?",
        (normalized.job_uuid, normalized.attempt),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["state"]) == "released"
            and existing["committed_at"] is None
        ):
            return _reprepare_released_permit(
                connection,
                existing=existing,
                request=normalized,
            )
        return DispatchAdmissionDecision(
            permit=_replay_permit(connection, existing, normalized)
        )

    active = connection.execute(
        """
        SELECT lease.*, claim.task_uuid, claim.job_uuid
        FROM station_execution_lock_lease lease
        JOIN station_execution_claim claim USING(claim_uuid)
        WHERE lease.state IN ('prepared', 'reserved', 'running', 'uncertain')
        ORDER BY lease.acquired_at, lease.claim_uuid, lease.lock_key
        """
    ).fetchall()
    requested_keys = {resource.lock_key for resource in normalized.resources}
    for lease in active:
        if conflicting_resource_lock_keys(
            requested_keys,
            {str(lease["lock_key"])},
        ):
            return DispatchAdmissionDecision(
                wait_code="resource_claimed",
                wait_message=f"资源 {lease['lock_key']} 已由其他作业申领",
                blocking_task_uuid=str(lease["task_uuid"]),
                blocking_job_uuid=str(lease["job_uuid"]),
            )

    _validate_resource_facts(connection, normalized.resources)
    if normalized.transfer is not None:
        _validate_transfer_conditions(connection, normalized)

    now = _utc_now()
    claim_uuid = str(uuid4())
    resource_keys_json = _canonical_json(sorted(requested_keys))
    expected_change_json = _canonical_json(normalized.expected_change_set)
    connection.execute(
        """
        INSERT INTO station_execution_claim(
            claim_uuid,effect_uuid,task_uuid,job_uuid,attempt,parameter_hash,
            expected_change_set,resource_keys,state,acquired_at,committed_at,
            released_at,update_time
        ) VALUES (?,?,?,?,?,?,?,?,'prepared',?,NULL,NULL,?)
        """,
        (
            claim_uuid,
            normalized.effect_uuid,
            normalized.task_uuid,
            normalized.job_uuid,
            normalized.attempt,
            normalized.parameter_hash,
            expected_change_json,
            resource_keys_json,
            now,
            now,
        ),
    )
    fences: list[DispatchFence] = []
    for resource in normalized.resources:
        token = _next_fencing_token(connection, resource.lock_key, now=now)
        connection.execute(
            """
            INSERT INTO station_execution_lock_lease(
                claim_uuid,lock_key,scope,material_uuid,site_uuid,fencing_token,
                state,acquired_at,released_at,update_time
            ) VALUES (?,?,?,?,?,?,'prepared',?,NULL,?)
            """,
            (
                claim_uuid,
                resource.lock_key,
                resource.scope,
                resource.material_uuid or None,
                resource.site_uuid or None,
                token,
                now,
                now,
            ),
        )
        fences.append(DispatchFence(resource.lock_key, token))
    return DispatchAdmissionDecision(
        permit=DispatchPermit(
            effect_uuid=normalized.effect_uuid,
            claim_uuid=claim_uuid,
            task_uuid=normalized.task_uuid,
            job_uuid=normalized.job_uuid,
            attempt=normalized.attempt,
            parameter_hash=normalized.parameter_hash,
            expected_change_set=dict(normalized.expected_change_set),
            fences=tuple(fences),
        )
    )


def transition_dispatch_permit(
    connection: sqlite3.Connection,
    *,
    claim_uuid: str,
    target_state: str,
) -> None:
    """幂等推进库存 Claim 与全部 Lease 的物理生命周期。

    参数：``connection`` 是库存写事务；``claim_uuid`` 是已签发凭据；
    ``target_state`` 只接受 reserved、running、uncertain、released。返回：无。
    异常：Claim 缺失、逆向或非法转换时抛 ``DispatchAdmissionConflict``。
    """

    allowed = {
        "reserved": {"prepared", "reserved"},
        "running": {"reserved", "running"},
        "uncertain": {"prepared", "reserved", "running", "uncertain"},
        "released": {
            "prepared",
            "reserved",
            "running",
            "uncertain",
            "released",
        },
    }
    if target_state not in allowed:
        raise DispatchAdmissionConflict(f"非法库存 Claim 目标状态：{target_state}")
    row = connection.execute(
        "SELECT state FROM station_execution_claim WHERE claim_uuid=?",
        (claim_uuid,),
    ).fetchone()
    if row is None:
        raise DispatchAdmissionConflict(f"库存 Claim 不存在：{claim_uuid}")
    current = str(row["state"])
    if current not in allowed[target_state]:
        raise DispatchAdmissionConflict(
            f"库存 Claim 不能从 {current} 进入 {target_state}：{claim_uuid}"
        )
    if current == target_state:
        return
    now = _utc_now()
    released_at = now if target_state == "released" else None
    committed_at = now if target_state == "reserved" else None
    connection.execute(
        """
        UPDATE station_execution_claim
        SET state=?, committed_at=COALESCE(committed_at, ?),
            released_at=?, update_time=?
        WHERE claim_uuid=?
        """,
        (target_state, committed_at, released_at, now, claim_uuid),
    )
    connection.execute(
        """
        UPDATE station_execution_lock_lease
        SET state=?, released_at=?, update_time=?
        WHERE claim_uuid=?
        """,
        (target_state, released_at, now, claim_uuid),
    )


def release_unprojected_dispatch_permits(
    connection: sqlite3.Connection,
    *,
    known_claim_uuids: Sequence[str],
) -> tuple[str, ...]:
    """释放未跨库投影的 prepared Permit，收敛准入崩溃窗口。

    参数：库存事务和工作流库仍可识别的 Claim UUID 集合。返回：本次按 UUID
    排序释放的库存 Claim。异常：身份非法或数据库错误原样传播。reserved、
    running、uncertain Claim 已可能越过物理边界，永远不会由此恢复入口释放。
    """

    known: set[str] = set()
    for value in known_claim_uuids:
        try:
            known.add(str(UUID(str(value))))
        except (AttributeError, TypeError, ValueError) as error:
            raise DispatchAdmissionConflict("工作流 Claim 身份非法") from error
    rows = connection.execute(
        "SELECT claim_uuid FROM station_execution_claim "
        "WHERE state='prepared' ORDER BY acquired_at, claim_uuid"
    ).fetchall()
    released = tuple(
        str(row["claim_uuid"])
        for row in rows
        if str(row["claim_uuid"]) not in known
    )
    for claim_uuid in released:
        transition_dispatch_permit(
            connection,
            claim_uuid=claim_uuid,
            target_state="released",
        )
    return released


def _normalize_request(request: DispatchAdmissionRequest) -> DispatchAdmissionRequest:
    """校验并稳定排序派发请求，阻止同键资源定义漂移。

    参数：``request`` 是调度器构造的候选请求。返回：字段规范且资源按锁键排序的
    新对象。异常：身份、哈希、预期变化或资源不合法时抛
    ``DispatchAdmissionConflict``。
    """

    for field, value in (
        ("effect_uuid", request.effect_uuid),
        ("task_uuid", request.task_uuid),
        ("job_uuid", request.job_uuid),
    ):
        try:
            UUID(str(value))
        except (AttributeError, TypeError, ValueError) as error:
            raise DispatchAdmissionConflict(f"{field} 不是合法 UUID") from error
    if request.attempt <= 0 or not str(request.parameter_hash or "").strip():
        raise DispatchAdmissionConflict("派发 attempt 或 parameter_hash 非法")
    if not isinstance(request.expected_change_set, Mapping):
        raise DispatchAdmissionConflict("expected_change_set 必须是对象")
    resources: dict[str, DispatchResource] = {}
    for item in request.resources:
        if not isinstance(item, DispatchResource):
            raise DispatchAdmissionConflict("派发资源必须使用 DispatchResource")
        lock_key = str(item.lock_key or "").strip()
        scope = str(item.scope or "").strip()
        if not lock_key or scope not in _SCOPES:
            raise DispatchAdmissionConflict("派发资源缺少合法 lock_key 或 scope")
        normalized = DispatchResource(
            lock_key=lock_key,
            scope=scope,
            material_uuid=str(item.material_uuid or "").strip(),
            site_uuid=str(item.site_uuid or "").strip(),
        )
        previous = resources.get(lock_key)
        if previous is not None and previous != normalized:
            raise DispatchAdmissionConflict(f"同一派发资源定义冲突：{lock_key}")
        resources[lock_key] = normalized
    return DispatchAdmissionRequest(
        effect_uuid=str(request.effect_uuid),
        task_uuid=str(request.task_uuid),
        job_uuid=str(request.job_uuid),
        attempt=request.attempt,
        parameter_hash=str(request.parameter_hash).strip(),
        expected_change_set=dict(request.expected_change_set),
        resources=tuple(resources[key] for key in sorted(resources)),
        transfer=request.transfer,
    )


def _validate_resource_facts(
    connection: sqlite3.Connection,
    resources: Sequence[DispatchResource],
) -> None:
    """证明请求引用的设备、物料与库位均存在且归属一致。

    参数：库存事务和规范资源集合。返回：无。异常：引用缺失或类型/归属不一致
    时抛 ``DispatchAdmissionConflict``，不得签发部分 Claim。
    """

    for resource in resources:
        if resource.scope == "device":
            row = connection.execute(
                "SELECT type FROM material WHERE uuid=? AND deleted_at IS NULL",
                (resource.material_uuid,),
            ).fetchone()
            if row is None or str(row["type"]) != "device":
                raise DispatchAdmissionConflict(
                    f"设备资源不存在或类型错误：{resource.material_uuid}"
                )
        elif resource.scope == "material":
            row = connection.execute(
                "SELECT 1 FROM material WHERE uuid=? AND deleted_at IS NULL",
                (resource.material_uuid,),
            ).fetchone()
            if row is None:
                raise DispatchAdmissionConflict(
                    f"物料资源不存在：{resource.material_uuid}"
                )
        else:
            row = connection.execute(
                "SELECT material_uuid FROM site WHERE uuid=? AND deleted_at IS NULL",
                (resource.site_uuid,),
            ).fetchone()
            if row is None or str(row["material_uuid"]) != resource.material_uuid:
                raise DispatchAdmissionConflict(
                    f"库位不存在或归属不一致：{resource.site_uuid}"
                )


def _validate_transfer_conditions(
    connection: sqlite3.Connection,
    request: DispatchAdmissionRequest,
) -> None:
    """在 Claim 写入前复验来源、目标、机械臂夹爪和预期变化。

    参数：库存事务和含转运条件的规范请求。返回：无。异常：条件已被并发改变时
    抛内部 ``_TemporaryCondition``；合同/资源集合不完整时抛
    ``DispatchAdmissionConflict``。
    """

    condition = request.transfer
    assert condition is not None
    source = connection.execute(
        "SELECT material_uuid,occupied_material_uuid FROM site "
        "WHERE uuid=? AND deleted_at IS NULL",
        (condition.source_site_uuid,),
    ).fetchone()
    if (
        source is None
        or str(source["material_uuid"]) != condition.source_owner_material_uuid
        or str(source["occupied_material_uuid"] or "") != condition.material_uuid
    ):
        raise TemporaryDispatchCondition(
            "transfer_source_site_missing",
            "待搬物料已经不在准入时确认的来源库位",
        )
    target = connection.execute(
        "SELECT material_uuid,occupied_material_uuid FROM site "
        "WHERE uuid=? AND deleted_at IS NULL",
        (condition.target_site_uuid,),
    ).fetchone()
    if target is None or str(target["material_uuid"]) != condition.target_owner_material_uuid:
        raise DispatchAdmissionConflict("目标库位不存在或归属已经改变")
    if str(target["occupied_material_uuid"] or ""):
        raise TemporaryDispatchCondition("site_occupied", "目标库位当前已有物料")
    ingress = connection.execute(
        "SELECT 1 FROM station_ingress_reservation_site "
        "WHERE site_uuid=? AND active=1 LIMIT 1",
        (condition.target_site_uuid,),
    ).fetchone()
    if ingress is not None:
        raise TemporaryDispatchCondition(
            "site_ingress_reserved",
            "目标库位已为运输中的入口载体预留",
        )
    gripper = connection.execute(
        "SELECT material_uuid,occupied_material_uuid FROM site "
        "WHERE uuid=? AND deleted_at IS NULL",
        (condition.gripper_site_uuid,),
    ).fetchone()
    if gripper is None or str(gripper["material_uuid"]) != condition.executor_material_uuid:
        raise DispatchAdmissionConflict("机械臂夹爪库位不存在或归属已经改变")
    if str(gripper["occupied_material_uuid"] or ""):
        raise TemporaryDispatchCondition(
            "gripper_site_occupied",
            "机械臂夹爪库位当前已有物料",
        )

    required_keys = {
        f"/devices/{condition.source_owner_material_uuid}",
        f"/devices/{condition.target_owner_material_uuid}",
        f"/devices/{condition.executor_material_uuid}",
        f"material/{condition.material_uuid}/exclusive",
        (
            f"material/{condition.source_owner_material_uuid}/site/"
            f"{condition.source_site_uuid}/exclusive"
        ),
        (
            f"material/{condition.target_owner_material_uuid}/site/"
            f"{condition.target_site_uuid}/exclusive"
        ),
        (
            f"material/{condition.executor_material_uuid}/site/"
            f"{condition.gripper_site_uuid}/exclusive"
        ),
    }
    actual_keys = {resource.lock_key for resource in request.resources}
    missing = sorted(required_keys - actual_keys)
    if missing:
        raise DispatchAdmissionConflict(
            "机械臂转运 Claim 缺少完整资源：" + ",".join(missing)
        )
    expected = request.expected_change_set
    required_change = {
        "kind": "material_transfer",
        "material_uuid": condition.material_uuid,
        "source_site_uuid": condition.source_site_uuid,
        "target_site_uuid": condition.target_site_uuid,
    }
    if any(expected.get(key) != value for key, value in required_change.items()):
        raise DispatchAdmissionConflict("转运预期 ChangeSet 与条件快照不一致")


class TemporaryDispatchCondition(ValueError):
    """库存原子门禁观察到可由其他任务改变的运行条件。"""

    def __init__(self, code: str, message: str) -> None:
        """保存稳定等待码和中文原因。

        参数：``code`` 是调度等待原因；``message`` 是展示文本。返回：无。异常：
        无；该内部异常由工站库存适配器转换为统一 ``StationResourceError``。
        """

        super().__init__(message)
        self.code = code
        self.message = message


def _replay_permit(
    connection: sqlite3.Connection,
    existing: sqlite3.Row,
    request: DispatchAdmissionRequest,
) -> DispatchPermit:
    """验证同一次 Job 尝试的重放请求并恢复原 Permit。

    参数：库存事务、既有 Claim 和规范请求。返回：原 Claim/Fence 凭据。异常：
    效果、参数、预期变化、资源集合漂移或 Claim 已释放时抛冲突。
    """

    _assert_replay_matches(existing, request)
    if str(existing["state"]) == "released":
        raise DispatchAdmissionConflict("已释放的库存 Claim 不能重新派发")
    fence_rows = connection.execute(
        "SELECT lock_key,fencing_token FROM station_execution_lock_lease "
        "WHERE claim_uuid=? ORDER BY lock_key",
        (existing["claim_uuid"],),
    ).fetchall()
    return DispatchPermit(
        effect_uuid=request.effect_uuid,
        claim_uuid=str(existing["claim_uuid"]),
        task_uuid=request.task_uuid,
        job_uuid=request.job_uuid,
        attempt=request.attempt,
        parameter_hash=request.parameter_hash,
        expected_change_set=dict(request.expected_change_set),
        fences=tuple(
            DispatchFence(str(row["lock_key"]), int(row["fencing_token"]))
            for row in fence_rows
        ),
    )


def _reprepare_released_permit(
    connection: sqlite3.Connection,
    *,
    existing: sqlite3.Row,
    request: DispatchAdmissionRequest,
) -> DispatchAdmissionDecision:
    """重新准备从未提交到物理边界、但被后续门禁释放的同一 Permit。

    参数：库存事务、released 且 committed_at 为空的原 Claim、同一 Job 尝试请求。
    返回：条件仍满足时复用 Claim/effect 并签发全新 Fence；资源冲突时返回等待。
    异常：请求漂移、库存条件损坏或 SQLite 错误原样传播。
    """

    _assert_replay_matches(existing, request)
    requested_keys = {resource.lock_key for resource in request.resources}
    active = connection.execute(
        """
        SELECT lease.*, claim.task_uuid, claim.job_uuid
        FROM station_execution_lock_lease lease
        JOIN station_execution_claim claim USING(claim_uuid)
        WHERE lease.state IN ('prepared', 'reserved', 'running', 'uncertain')
          AND lease.claim_uuid <> ?
        ORDER BY lease.acquired_at, lease.claim_uuid, lease.lock_key
        """,
        (existing["claim_uuid"],),
    ).fetchall()
    for lease in active:
        if conflicting_resource_lock_keys(
            requested_keys,
            {str(lease["lock_key"])},
        ):
            return DispatchAdmissionDecision(
                wait_code="resource_claimed",
                wait_message=f"资源 {lease['lock_key']} 已由其他作业申领",
                blocking_task_uuid=str(lease["task_uuid"]),
                blocking_job_uuid=str(lease["job_uuid"]),
            )
    _validate_resource_facts(connection, request.resources)
    if request.transfer is not None:
        _validate_transfer_conditions(connection, request)
    now = _utc_now()
    claim_uuid = str(existing["claim_uuid"])
    connection.execute(
        """
        UPDATE station_execution_claim
        SET state='prepared', acquired_at=?, released_at=NULL, update_time=?
        WHERE claim_uuid=? AND state='released' AND committed_at IS NULL
        """,
        (now, now, claim_uuid),
    )
    fences: list[DispatchFence] = []
    for resource in request.resources:
        token = _next_fencing_token(connection, resource.lock_key, now=now)
        changed = connection.execute(
            """
            UPDATE station_execution_lock_lease
            SET fencing_token=?, state='prepared', acquired_at=?,
                released_at=NULL, update_time=?
            WHERE claim_uuid=? AND lock_key=? AND state='released'
            """,
            (token, now, now, claim_uuid, resource.lock_key),
        ).rowcount
        if changed != 1:
            raise DispatchAdmissionConflict("重准备 Permit 的资源 Lease 不完整")
        fences.append(DispatchFence(resource.lock_key, token))
    return DispatchAdmissionDecision(
        permit=DispatchPermit(
            effect_uuid=request.effect_uuid,
            claim_uuid=claim_uuid,
            task_uuid=request.task_uuid,
            job_uuid=request.job_uuid,
            attempt=request.attempt,
            parameter_hash=request.parameter_hash,
            expected_change_set=dict(request.expected_change_set),
            fences=tuple(fences),
        )
    )


def _assert_replay_matches(
    existing: sqlite3.Row,
    request: DispatchAdmissionRequest,
) -> None:
    """验证同一 Job 尝试的稳定效果、参数和完整资源未漂移。"""

    expected_fields = {
        "effect_uuid": request.effect_uuid,
        "task_uuid": request.task_uuid,
        "parameter_hash": request.parameter_hash,
        "expected_change_set": _canonical_json(request.expected_change_set),
        "resource_keys": _canonical_json(
            sorted(resource.lock_key for resource in request.resources)
        ),
    }
    for field, expected in expected_fields.items():
        if str(existing[field]) != str(expected):
            raise DispatchAdmissionConflict(
                f"同一作业尝试的派发请求发生漂移：{field}"
            )


def _next_fencing_token(
    connection: sqlite3.Connection,
    lock_key: str,
    *,
    now: str,
) -> int:
    """在库存事务内为一个资源分配严格递增的 Fence。

    参数：库存事务、规范锁键和统一时间。返回：新的正整数令牌。异常：SQLite
    错误原样传播，调用方必须与 Claim 插入处于同一事务。
    """

    connection.execute(
        """
        INSERT INTO station_execution_fence_counter(
            lock_key,last_fencing_token,update_time
        ) VALUES (?,1,?)
        ON CONFLICT(lock_key) DO UPDATE SET
            last_fencing_token=last_fencing_token+1,
            update_time=excluded.update_time
        """,
        (lock_key, now),
    )
    row = connection.execute(
        "SELECT last_fencing_token FROM station_execution_fence_counter "
        "WHERE lock_key=?",
        (lock_key,),
    ).fetchone()
    assert row is not None
    return int(row["last_fencing_token"])


def _canonical_json(value: Any) -> str:
    """把可 JSON 化值编码为稳定 UTF-8 文本。

    参数：``value`` 是预期变化或资源键集合。返回：按键排序且无多余空白的文本。
    异常：不可 JSON 化值抛 ``DispatchAdmissionConflict``。
    """

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise DispatchAdmissionConflict("派发请求包含不可序列化值") from error


def _utc_now() -> str:
    """返回库存准入事实使用的 UTC ISO 时间。"""

    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DispatchAdmissionConflict",
    "DispatchAdmissionDecision",
    "DispatchAdmissionRequest",
    "DispatchFence",
    "DispatchPermit",
    "DispatchResource",
    "TemporaryDispatchCondition",
    "TransferDispatchCondition",
    "acquire_dispatch_permit",
    "migrate_dispatch_admission_schema",
    "release_unprojected_dispatch_permits",
    "transition_dispatch_permit",
]
