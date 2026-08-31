"""工站调度读取设备、库位（Site）与转运事实的窄库存接缝。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from unilabos.app.scheduler.inventory.dispatch_admission import (
    DispatchAdmissionDecision,
    DispatchAdmissionRequest,
    TemporaryDispatchCondition,
    acquire_dispatch_permit,
    release_unprojected_dispatch_permits,
    transition_dispatch_permit,
)
from unilabos.app.scheduler.inventory.store import InventoryStore


class StationResourceError(ValueError):
    """库存权威无法证明调度所需工站资源事实。"""

    def __init__(self, code: str, message: str) -> None:
        """保存稳定诊断码和中文原因。

        参数：``code`` 是调度可判断的稳定错误码；``message`` 是供日志和界面
        展示的中文原因。返回：无。异常：构造过程不访问外部状态。
        """

        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TargetSiteRequest:
    """一次目标库位（Site）选择所需的完整库存条件。"""

    owner_material_uuid: str
    site_uuid: str = ""
    site_name: str = ""
    equivalent_site_uuids: tuple[str, ...] = ()
    occupant_material_uuid: str = ""
    unavailable_site_uuids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StationSiteTarget:
    """库存权威已确认可接收物料的目标库位（Site）。"""

    uuid: str
    name: str
    owner_material_uuid: str


@dataclass(frozen=True, slots=True)
class TransferResourceRequest:
    """机械臂转运完整资源事实的解析请求。"""

    resource_material_uuid: str
    target_site_uuid: str
    target_owner_material_uuid: str
    executor_material_uuid: str = ""
    gripper_site_role: str = ""
    require_device_owners: bool = False


@dataclass(frozen=True, slots=True)
class TransferResourceFacts:
    """库存权威已证明的转运来源、设备祖先和夹爪库位事实。"""

    source_site_uuid: str
    source_owner_material_uuid: str
    source_device_material_uuid: str
    target_device_material_uuid: str
    gripper_site_uuid: str


@dataclass(frozen=True, slots=True)
class MaterialTransferCommand:
    """成功作业向库存权威提交的幂等物料移动命令。"""

    material_uuid: str
    target_owner_material_uuid: str
    target_site_uuid: str = ""
    target_site_name: str = ""
    actor: str = ""
    causation_id: str = ""


class StationResourceInventory(Protocol):
    """向调度器隐藏库存表、SQL 和资源树遍历的深模块接口。"""

    def is_device_material(
        self,
        material_uuid: str,
        *,
        resource_template_uuid: str,
    ) -> bool:
        """判断具体物料是否为指定模板的设备实例。

        参数：``material_uuid`` 是候选设备物料身份；``resource_template_uuid``
        是冻结执行计划要求的模板身份。返回：两者匹配且候选类型为设备时为真。
        异常：库存读取失败原样传播，不得把读取失败降级成不匹配。
        """

    def resolve_target_site(self, request: TargetSiteRequest) -> StationSiteTarget:
        """按稳定选择器解析当前可接收物料的目标库位（Site）。

        参数：``request`` 包含父资源、显式库位或等价组及当前不可用集合。返回：
        已验证归属、模板和占用条件的规范库位。异常：事实不足或条件冲突时抛
        ``StationResourceError``；底层读取故障原样传播。
        """

    def resolve_transfer_resources(
        self,
        request: TransferResourceRequest,
    ) -> TransferResourceFacts:
        """解析机械臂转运需要同时占用的全部库存资源事实。

        参数：``request`` 标识被搬物料、目标库位、目标设备和可选机械臂。返回：
        来源库位、来源/目标设备和夹爪库位的规范资源集合。异常：任一资源无法从
        库存事实证明时抛 ``StationResourceError``；底层读取故障原样传播。
        """

    def settle_material_transfer(
        self,
        command: MaterialTransferCommand,
    ) -> Mapping[str, Any]:
        """以稳定因果身份幂等结算一次成功物料转移。

        参数：``command`` 包含物料、目标父资源、目标库位与因果身份。返回：库存
        权威持久化后的移动结果。异常：目标身份不完整、结算冲突或数据库故障时
        原样传播，调用方不得自行补写库位占用。
        """

    def acquire_dispatch_permit(
        self,
        request: DispatchAdmissionRequest,
    ) -> DispatchAdmissionDecision:
        """在库存事务内复验条件并取得完整 Claim/Fence。

        参数：``request`` 是稳定派发效果、最终参数、预期变化与全资源集合。返回：
        成功 Permit 或正常竞争等待结果。异常：运行条件改变时抛稳定
        ``StationResourceError``；合同和数据库故障原样传播。
        """

    def transition_dispatch_permit(
        self,
        claim_uuid: str,
        *,
        target_state: str,
    ) -> None:
        """推进库存 Claim 的派发、运行、不确定或释放状态。

        参数：``claim_uuid`` 是 Permit 身份；``target_state`` 是目标生命周期状态。
        返回：无。异常：非法转换或数据库故障原样传播。
        """

    def release_unprojected_dispatch_permits(
        self,
        *,
        known_claim_uuids: Sequence[str],
    ) -> tuple[str, ...]:
        """释放未在工作流库留下 Claim 投影的 prepared Permit。

        参数：``known_claim_uuids`` 是工作流权威当前仍保存的全部活动 Claim。
        返回：本次释放的库存 Claim UUID。异常：身份或数据库错误原样传播。
        """


class SqliteStationResourceInventory:
    """基于本地库存 SQLite 的工站资源读取与结算适配器。"""

    def __init__(
        self,
        store: InventoryStore,
        *,
        move_material: Callable[..., dict[str, Any]],
    ) -> None:
        """绑定库存存储和既有物料移动写入口。

        参数：``store`` 是本地库存权威的 SQLite 适配器；``move_material`` 是
        ``InventoryService.move_instance`` 的事务写入口。返回：无。异常：构造
        不访问数据库；具体查询或结算错误在对应方法中原样传播。
        """

        self._store = store
        self._move_material = move_material

    def is_device_material(
        self,
        material_uuid: str,
        *,
        resource_template_uuid: str,
    ) -> bool:
        """判断具体物料是否为指定模板的设备实例。

        参数：``material_uuid`` 是设备候选物料身份；
        ``resource_template_uuid`` 是冻结执行计划要求的设备模板身份。返回：仅当
        活跃物料同时匹配模板且类型为 ``device`` 时为真。异常：SQLite 读取错误
        原样传播，调用方不得把读取失败解释成匹配。
        """

        material = self._store.query_one(
            "SELECT resource_template_uuid, type FROM material "
            "WHERE uuid = ? AND deleted_at IS NULL",
            (material_uuid,),
        )
        return bool(
            material is not None
            and str(material.get("resource_template_uuid") or "")
            == resource_template_uuid
            and str(material.get("type") or "") == "device"
        )

    def resolve_target_site(self, request: TargetSiteRequest) -> StationSiteTarget:
        """按稳定身份、显式等价组和当前占用解析目标库位（Site）。

        参数：``request`` 同时携带库位拥有者、单一选择器或等价组、待放物料和
        本轮已不可用库位。返回：按 ``sort_order`` 选出的规范库位身份与名称。
        异常：选择器、归属、占用或模板约束无法满足时抛
        ``StationResourceError``；SQLite 错误原样传播并关闭式阻止派发。
        """

        owner = str(request.owner_material_uuid or "").strip()
        requested_uuid = str(request.site_uuid or "").strip()
        requested_name = str(request.site_name or "").strip()
        requested_group = tuple(
            str(item or "").strip() for item in request.equivalent_site_uuids
        )
        occupant = str(request.occupant_material_uuid or "").strip()
        unavailable = {
            str(item or "").strip() for item in request.unavailable_site_uuids
        }
        if not owner:
            raise StationResourceError(
                "site_owner_missing",
                "目标库位缺少所属父物料 mount_resource.uuid",
            )
        if not requested_uuid and not requested_name and not requested_group:
            raise StationResourceError(
                "site_selector_missing",
                "目标库位必须提供 site_uuid、site 名称或显式等价库位组",
            )
        if requested_group and (requested_uuid or requested_name):
            raise StationResourceError(
                "site_selector_conflict",
                "显式库位与等价库位组不能同时提供",
            )
        if occupant and owner == occupant:
            raise StationResourceError(
                "site_self_mount",
                "待转移物料不能挂载到自身库位",
            )

        candidate_rows = self._site_candidates(
            owner_material_uuid=owner,
            site_uuid=requested_uuid,
            site_name=requested_name,
            equivalent_site_uuids=requested_group,
        )
        if not candidate_rows:
            selector = (
                "等价库位组"
                if requested_group
                else (
                    f"UUID {requested_uuid}"
                    if requested_uuid
                    else f"名称 {requested_name}"
                )
            )
            raise StationResourceError(
                "site_not_found",
                f"目标父物料 {owner} 下找不到库位（{selector}）",
            )
        for candidate in candidate_rows:
            if str(candidate["material_uuid"]) != owner:
                raise StationResourceError(
                    "site_owner_mismatch",
                    f"库位 {candidate['uuid']} 不属于目标父物料 {owner}",
                )

        first_candidate = candidate_rows[0]
        canonical_name = str(first_candidate["name"])
        if (
            requested_uuid
            and requested_name
            and canonical_name.casefold() != requested_name.casefold()
        ):
            raise StationResourceError(
                "site_identity_mismatch",
                f"site_uuid {first_candidate['uuid']} 对应名称 {canonical_name}，"
                f"与入参 {requested_name} 不一致",
            )

        occupant_template_uuid = self._material_template_uuid(occupant)
        active_ingress_sites = self._active_ingress_site_uuids(
            tuple(str(candidate["uuid"]) for candidate in candidate_rows)
        )
        selected: dict[str, Any] | None = None
        for candidate in candidate_rows:
            candidate_uuid = str(candidate["uuid"])
            if candidate_uuid in unavailable or candidate_uuid in active_ingress_sites:
                continue
            occupied_by = str(candidate.get("occupied_material_uuid") or "").strip()
            if occupied_by and occupied_by != occupant:
                continue
            allowed_templates = _allowed_template_uuids(
                candidate.get("allowed_resource_template_uuids"),
                site_name=str(candidate["name"]),
            )
            if allowed_templates and occupant_template_uuid not in allowed_templates:
                continue
            selected = candidate
            break
        if selected is None:
            self._raise_unavailable_site(
                candidate=first_candidate,
                requested_group=requested_group,
                unavailable_site_uuids=unavailable,
                active_ingress_site_uuids=active_ingress_sites,
                occupant_material_uuid=occupant,
            )
        assert selected is not None
        return StationSiteTarget(
            uuid=str(selected["uuid"]),
            name=str(selected["name"]),
            owner_material_uuid=owner,
        )

    def resolve_transfer_resources(
        self,
        request: TransferResourceRequest,
    ) -> TransferResourceFacts:
        """解析转运来源、两端设备祖先和空夹爪库位。

        参数：``request`` 提供待搬物料、已选目标库位、机械臂身份与夹爪角色。
        返回：调度器构造完整作业执行占用（JobExecutionClaim）所需的稳定库存
        事实。异常：来源、父链、设备祖先或夹爪条件无法证明时抛
        ``StationResourceError``，绝不返回部分资源集合。
        """

        source = self._store.query_one(
            "SELECT uuid, material_uuid FROM site "
            "WHERE occupied_material_uuid = ? AND deleted_at IS NULL "
            "ORDER BY sort_order ASC, create_time ASC, uuid ASC LIMIT 1",
            (request.resource_material_uuid,),
        )
        if source is None:
            raise StationResourceError(
                "transfer_source_site_missing",
                "被搬运物料没有可证明的来源库位",
            )
        source_site_uuid = str(source["uuid"])
        source_owner_uuid = str(source["material_uuid"])
        source_device_uuid = self._owning_device_uuid(source_owner_uuid)
        target_device_uuid = self._owning_device_uuid(
            request.target_owner_material_uuid
        )
        if request.require_device_owners and (
            not source_device_uuid or not target_device_uuid
        ):
            raise StationResourceError(
                "transfer_device_owner_missing",
                "机械臂转运的来源库位和目标库位必须能追溯到设备",
            )
        gripper_site_uuid = ""
        gripper_role = str(request.gripper_site_role or "").strip()
        executor_uuid = str(request.executor_material_uuid or "").strip()
        if gripper_role:
            if not executor_uuid:
                raise StationResourceError(
                    "transfer_executor_missing",
                    "机械臂转运缺少实际执行器物料 UUID",
                )
            gripper_site_uuid = self._empty_role_site_uuid(
                owner_material_uuid=executor_uuid,
                role=gripper_role,
            )
        return TransferResourceFacts(
            source_site_uuid=source_site_uuid,
            source_owner_material_uuid=source_owner_uuid,
            source_device_material_uuid=source_device_uuid,
            target_device_material_uuid=target_device_uuid,
            gripper_site_uuid=gripper_site_uuid,
        )

    def settle_material_transfer(
        self,
        command: MaterialTransferCommand,
    ) -> Mapping[str, Any]:
        """验证目标库位并调用库存写模型幂等移动物料。

        参数：``command`` 提供物料、目标父级、目标库位和稳定作业因果身份。
        返回：库存写模型提交后的物料快照。异常：目标库位不再可用时抛
        ``StationResourceError``；库存版本或物料冲突由写模型原样传播。
        """

        target = self.resolve_target_site(
            TargetSiteRequest(
                owner_material_uuid=command.target_owner_material_uuid,
                site_uuid=command.target_site_uuid,
                site_name=command.target_site_name,
                occupant_material_uuid=command.material_uuid,
            )
        )
        return self._move_material(
            command.material_uuid,
            parent_uuid=command.target_owner_material_uuid,
            slot_id=target.name,
            actor=command.actor,
            causation_id=command.causation_id,
        )

    def acquire_dispatch_permit(
        self,
        request: DispatchAdmissionRequest,
    ) -> DispatchAdmissionDecision:
        """在库存单写事务内完成条件复验与完整资源 Claim/Fence。

        参数：``request`` 是调度器冻结的派发效果和全资源请求。返回：成功 Permit
        或资源竞争等待结果。异常：可变化条件转为 ``StationResourceError``；合同
        冲突与 SQLite 故障原样传播，整个事务自动回滚。
        """

        try:
            with self._store.transaction() as connection:
                return acquire_dispatch_permit(connection, request)
        except TemporaryDispatchCondition as error:
            raise StationResourceError(error.code, error.message) from error

    def transition_dispatch_permit(
        self,
        claim_uuid: str,
        *,
        target_state: str,
    ) -> None:
        """在库存事务内幂等推进 Claim 与全部 Lease。

        参数：``claim_uuid`` 是库存签发身份；``target_state`` 是 reserved、
        running、uncertain 或 released。返回：无。异常：非法转换与 SQLite 故障
        原样传播，调用方不得只修改工作流库投影。
        """

        with self._store.transaction() as connection:
            transition_dispatch_permit(
                connection,
                claim_uuid=claim_uuid,
                target_state=target_state,
            )

    def release_unprojected_dispatch_permits(
        self,
        *,
        known_claim_uuids: Sequence[str],
    ) -> tuple[str, ...]:
        """在库存事务内回收未被工作流库投影的 prepared Permit。

        参数：工作流库仍保存的活动 Claim 身份。返回：本次释放身份。异常：身份
        或 SQLite 错误原样传播，已越过 prepared 的 Claim 不受影响。
        """

        with self._store.transaction() as connection:
            return release_unprojected_dispatch_permits(
                connection,
                known_claim_uuids=known_claim_uuids,
            )

    def _site_candidates(
        self,
        *,
        owner_material_uuid: str,
        site_uuid: str,
        site_name: str,
        equivalent_site_uuids: Sequence[str],
    ) -> list[dict[str, Any]]:
        """读取单一选择器或等价组对应的规范库位行。

        参数：父物料、精确 UUID、兼容名称和等价组已经过互斥校验。返回：按
        ``sort_order`` 排序的候选行。异常：UUID 非法、重复或引用缺失时抛
        ``StationResourceError``；SQLite 错误原样传播。
        """

        if equivalent_site_uuids:
            try:
                canonical_group = tuple(
                    str(uuid.UUID(item)) for item in equivalent_site_uuids
                )
            except (AttributeError, TypeError, ValueError) as error:
                raise StationResourceError(
                    "invalid_site_group",
                    "等价库位组包含非法 UUID",
                ) from error
            if len(set(canonical_group)) != len(canonical_group):
                raise StationResourceError(
                    "invalid_site_group",
                    "等价库位组包含重复 UUID",
                )
            placeholders = ",".join("?" for _ in canonical_group)
            rows = self._store.query_all(
                "SELECT uuid, material_uuid, name, sort_order, "
                "occupied_material_uuid, allowed_resource_template_uuids "
                f"FROM site WHERE uuid IN ({placeholders}) "
                "AND deleted_at IS NULL "
                "ORDER BY sort_order ASC, create_time ASC, uuid ASC",
                canonical_group,
            )
            found = {str(row["uuid"]) for row in rows}
            missing = sorted(set(canonical_group) - found)
            if missing:
                raise StationResourceError(
                    "site_not_found",
                    "等价库位组引用不存在的库位：" + ",".join(missing),
                )
            return rows
        if site_uuid:
            try:
                canonical_uuid = str(uuid.UUID(site_uuid))
            except (AttributeError, TypeError, ValueError) as error:
                raise StationResourceError(
                    "invalid_site_uuid",
                    "site_uuid 不是合法 UUID",
                ) from error
            row = self._store.query_one(
                "SELECT uuid, material_uuid, name, occupied_material_uuid, "
                "allowed_resource_template_uuids "
                "FROM site WHERE uuid=? AND deleted_at IS NULL",
                (canonical_uuid,),
            )
            return [row] if row is not None else []
        row = self._store.query_one(
            "SELECT uuid, material_uuid, name, occupied_material_uuid, "
            "allowed_resource_template_uuids "
            "FROM site WHERE material_uuid=? AND LOWER(name)=LOWER(?) "
            "AND deleted_at IS NULL",
            (owner_material_uuid, site_name),
        )
        return [row] if row is not None else []

    def _material_template_uuid(self, material_uuid: str) -> str:
        """读取具体物料的资源模板 UUID。

        参数：``material_uuid`` 是准备放入目标库位的物料身份。返回：活跃物料的
        模板 UUID；空身份或不存在时返回空字符串，由库位模板约束失败关闭。
        异常：SQLite 读取错误原样传播。
        """

        if not material_uuid:
            return ""
        row = self._store.query_one(
            "SELECT resource_template_uuid FROM material "
            "WHERE uuid=? AND deleted_at IS NULL",
            (material_uuid,),
        )
        return str((row or {}).get("resource_template_uuid") or "")

    def _active_ingress_site_uuids(
        self,
        site_uuids: Sequence[str],
    ) -> set[str]:
        """批量读取仍由 AGV 入口预留占用的候选库位。

        参数：``site_uuids`` 是本轮目标选择已经验证存在的候选 UUID。返回：活动
        入口预留绑定的库位集合；空候选返回空集合。异常：SQLite 读取错误原样
        传播，调度器必须失败关闭，不能让普通节点抢占运输目的地。
        """

        if not site_uuids:
            return set()
        placeholders = ",".join("?" for _ in site_uuids)
        rows = self._store.query_all(
            "SELECT site_uuid FROM station_ingress_reservation_site "
            f"WHERE active=1 AND site_uuid IN ({placeholders})",
            tuple(site_uuids),
        )
        return {str(row["site_uuid"]) for row in rows}

    def _owning_device_uuid(self, material_uuid: str) -> str:
        """沿物料父链查找最近设备祖先。

        参数：``material_uuid`` 是库位直接拥有者。返回：最近设备物料 UUID；没有
        设备祖先时返回空字符串。异常：父链循环时抛 ``StationResourceError``，
        防止损坏资源树绕过设备互斥。
        """

        current = str(material_uuid or "").strip()
        visited: set[str] = set()
        while current:
            if current in visited:
                raise StationResourceError(
                    "material_parent_cycle",
                    "库存物料父链存在循环",
                )
            visited.add(current)
            row = self._store.query_one(
                "SELECT type, parent_uuid FROM material "
                "WHERE uuid = ? AND deleted_at IS NULL",
                (current,),
            )
            if row is None:
                return ""
            if str(row.get("type") or "") == "device":
                return current
            current = str(row.get("parent_uuid") or "").strip()
        return ""

    def _empty_role_site_uuid(
        self,
        *,
        owner_material_uuid: str,
        role: str,
    ) -> str:
        """选择执行器下唯一且空闲的具名角色库位。

        参数：``owner_material_uuid`` 是机械臂物料 UUID；``role`` 是动作资源合同
        声明的夹爪库位角色。返回：匹配库位的稳定 UUID。异常：角色重复、缺失、
        元数据损坏或位置有物料时抛 ``StationResourceError``。
        """

        rows = self._store.query_all(
            "SELECT uuid, meta_data, occupied_material_uuid FROM site "
            "WHERE material_uuid = ? AND deleted_at IS NULL "
            "ORDER BY sort_order ASC, create_time ASC, uuid ASC",
            (owner_material_uuid,),
        )
        matches = [row for row in rows if _site_role(row.get("meta_data")) == role]
        if len(matches) != 1:
            raise StationResourceError(
                "gripper_site_role_invalid",
                f"机械臂夹爪库位角色 {role} 必须且只能配置一个，当前为 {len(matches)} 个",
            )
        occupied = str(matches[0].get("occupied_material_uuid") or "").strip()
        if occupied:
            raise StationResourceError(
                "gripper_site_occupied",
                f"机械臂夹爪库位已有物料 {occupied}，不能开始新的转运",
            )
        return str(matches[0]["uuid"])

    @staticmethod
    def _raise_unavailable_site(
        *,
        candidate: Mapping[str, Any],
        requested_group: Sequence[str],
        unavailable_site_uuids: set[str],
        active_ingress_site_uuids: set[str],
        occupant_material_uuid: str,
    ) -> None:
        """把候选不可用原因收敛为稳定调度错误。

        参数：首个候选、是否为等价组、本轮已申领集合和待放物料身份用于判定
        最具体原因。返回：无。异常：始终抛 ``StationResourceError``。
        """

        if requested_group:
            raise StationResourceError(
                "site_group_unavailable",
                "等价库位组当前没有可接收目标物料的位置",
            )
        site_uuid = str(candidate["uuid"])
        site_name = str(candidate["name"])
        if site_uuid in active_ingress_site_uuids:
            raise StationResourceError(
                "site_ingress_reserved",
                f"目标库位 {site_name} 已为运输中的入口载体预留",
            )
        if site_uuid in unavailable_site_uuids:
            raise StationResourceError(
                "site_claimed",
                f"目标库位 {site_name} 已由其他作业申领",
            )
        occupied_by = str(candidate.get("occupied_material_uuid") or "").strip()
        if occupied_by and occupied_by != occupant_material_uuid:
            raise StationResourceError(
                "site_occupied",
                f"目标库位 {site_name} 已被物料 {occupied_by} 占用",
            )
        raise StationResourceError(
            "site_template_not_allowed",
            f"物料 {occupant_material_uuid} 的模板不允许放入目标库位 {site_name}",
        )


def _allowed_template_uuids(raw_value: Any, *, site_name: str) -> set[str]:
    """解码库位允许物料模板集合。

    参数：``raw_value`` 是 SQLite JSON 文本或已解码序列；``site_name`` 用于诊断。
    返回：去空白后的模板 UUID 集合。异常：值不是合法 JSON 数组时抛
    ``StationResourceError``，禁止把损坏约束解释为无限制。
    """

    try:
        decoded = (
            raw_value
            if isinstance(raw_value, (list, tuple))
            else json.loads(str(raw_value or "[]"))
        )
    except (TypeError, ValueError) as error:
        raise StationResourceError(
            "invalid_site_template_constraint",
            f"目标库位 {site_name} 的允许物料类型配置无效",
        ) from error
    if not isinstance(decoded, (list, tuple)):
        raise StationResourceError(
            "invalid_site_template_constraint",
            f"目标库位 {site_name} 的允许物料类型配置无效",
        )
    return {str(item) for item in decoded if str(item).strip()}


def _site_role(raw_metadata: Any) -> str:
    """读取库位部署元数据中的规范资源角色。

    参数：``raw_metadata`` 是 SQLite JSON 文本或已解码对象。返回：
    ``meta_data.unilab.resource_role`` 文本，缺省为空。异常：JSON 损坏时抛
    ``StationResourceError``，不把损坏元数据解释为无角色。
    """

    try:
        metadata = (
            raw_metadata
            if isinstance(raw_metadata, dict)
            else json.loads(str(raw_metadata or "{}"))
        )
    except (TypeError, ValueError) as error:
        raise StationResourceError(
            "invalid_site_metadata",
            "库位 meta_data 不是合法 JSON",
        ) from error
    unilab = metadata.get("unilab") if isinstance(metadata, dict) else None
    if not isinstance(unilab, dict):
        return ""
    return str(unilab.get("resource_role") or "").strip()


__all__ = [
    "MaterialTransferCommand",
    "SqliteStationResourceInventory",
    "StationResourceError",
    "StationResourceInventory",
    "StationSiteTarget",
    "TargetSiteRequest",
    "TransferResourceFacts",
    "TransferResourceRequest",
]
