"""本地调度器（EdgeScheduler）：Edge 侧执行态推进的唯一入口。

重排触发点（硬性约定，二者都强制全量 reschedule）：

1. **每个工作流提交**（``submit_workflow``）
2. **每个子 action 完成**（``on_job_finished``，含成功/失败）

每次 reschedule：

    收集所有 RUNNING 工作流的 ready 节点
      → TaskOrderer 排序（本地 stub 或 HTTP 调 uni-lab-scheduler）
      → 按序下发；动作键或设备级互斥键被占用的节点跳过，等下一次触发
      → 下发前解析父节点传参（gjson/sjson + ``@@@`` 语义）

不做一次性拓扑序：ready 集合每次触发点都重新计算、重新排序。

物料衔接（注入本地库存服务（InventoryService）时启用；spec 无物料字段则行为完全不变）：

- submit：汇总 DAG 全部物料需求，入队前 all-or-nothing 预留；
  不足 → workflow 置 ``waiting_for_material``，不进入执行队列，每次重排重试预留
- 节点下发前只持有预留，不提前扣减库存
- 明确成功结果提交时：预留 → FIFO lot 消费 + 实例 deploy（幂等键
  workflow:node:attempt）
- 失败、取消或跳过不扣减；工作流终态释放剩余 active 预留（依据 DB，不依赖内存）

动作物料锁与库位锁（Action Material Lock / Site Lock）：

- 下发前校验最终参数，并从规范动作 Schema 提取物料 UUID
- ``transfer_resource`` 的 ``site_uuid`` 是独立可选参数；优先按稳定 UUID 查库位，
  未提供时再按 ``mount_resource.uuid + site`` 名称解析
- 整物料锁覆盖其所有子库位；同一库位串行，同一物料下不同库位可并行
- 实体型物料需求的 ``instance_uuid`` 自动并入同一物料锁键
- job 完成 / 工作流取消时释放
"""

from __future__ import annotations

import logging
import threading
import time
import uuid as uuid_mod
from collections import deque
from collections.abc import Mapping
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.dispatch import (
    CancelDispatchState,
    CommittedJobOutcome,
    Dispatcher,
    RecordingDispatcher,
    build_job_start_payload,
)
from unilabos.app.scheduler.estimation import DurationEstimator
from unilabos.app.scheduler.inventory.domain import InsufficientStock
from unilabos.app.scheduler.models import (
    DispatchedJob,
    ReadyTask,
    WorkflowSpec,
    WorkflowState,
    priority_weight,
)
from unilabos.app.scheduler.ordering import (
    OrderingContext,
    StableLocalOrderer,
    TaskOrderer,
)
from unilabos.app.scheduler.param_resolver import ParamResolveError
from unilabos.app.scheduler.resource_lock import (
    conflicting_resource_lock_keys,
    material_lock_key,
    normalize_resource_lock_keys,
    site_lock_key,
)
from unilabos.app.scheduler.site_target import (
    ResolvedSiteTarget,
    SiteTargetResolutionError,
    resolve_site_target,
)
from unilabos.registry.material_lock_schema import (
    MaterialLockSchemaError,
    compile_material_lock_schema,
)
from unilabos.utils.tracing import (
    DetachedSpan,
    add_event,
    span,
    start_detached_span,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_IN_FLIGHT_JOBS = 100
_DEFAULT_MAX_ACTIVE_TASKS = 500
_DEFAULT_MAX_TASKS_PER_WORKFLOW = 100


class ExecutionPolicyError(ValueError):
    """冻结节点执行策略无法安全转成持久调度声明。"""


def _resource_argument_uuid(value: Any, *, argument_name: str) -> str:
    """从动作物料引用中读取并规范化稳定 UUID。

    参数：``value`` 可以是规范 ``ResourceSlot`` 字典或内部 PLR 富对象；
    ``argument_name`` 用于错误说明。返回：规范小写 UUID 字符串。异常：字段缺失
    或格式非法时抛 ``SiteTargetResolutionError``，防止库位锁退化为名称猜测。
    """

    raw_uuid: Any = None
    if isinstance(value, Mapping):
        raw_uuid = value.get("uuid") or value.get("unilabos_uuid")
    else:
        raw_uuid = getattr(value, "unilabos_uuid", None) or getattr(
            value,
            "uuid",
            None,
        )
    try:
        return str(uuid_mod.UUID(str(raw_uuid)))
    except (AttributeError, TypeError, ValueError) as error:
        raise SiteTargetResolutionError(
            "invalid_resource_uuid",
            f"{argument_name} 缺少合法 uuid",
        ) from error


def _device_key_from_strict_action_key(action_key: Any) -> str | None:
    """从严格动作级忙碌键提取设备级内存互斥键。

    参数：``action_key`` 是外部忙碌提供者返回的候选键。
    返回：仅当输入严格符合 ``/devices/{device_id}/{action_name}`` 且设备、动作
    均非空时返回 ``/devices/{device_id}``；其他输入返回 ``None``。
    异常：不主动抛出异常；非字符串和歧义路径一律不解析，避免误扩大互斥范围。

    该转换只桥接既有动作级内存事实，不产生持久作业执行占用
    （JobExecutionClaim）或栅栏（Fence）。
    """

    if not isinstance(action_key, str):
        return None
    path_parts = action_key.split("/")
    if (
        len(path_parts) != 4
        or path_parts[0] != ""
        or path_parts[1] != "devices"
        or not path_parts[2]
        or not path_parts[3]
    ):
        return None
    return f"/devices/{path_parts[2]}"


class EdgeScheduler:
    def __init__(
        self,
        orderer: Optional[TaskOrderer] = None,
        dispatcher: Optional[Dispatcher] = None,
        external_busy_keys: Optional[Set[str]] = None,
        busy_key_provider: Optional["Callable[[], Set[str]]"] = None,
        workflow_state_listener: Optional["Callable[[str, str], None]"] = None,
        inventory: Any = None,
        material_lock_resolver: Optional[
            "Callable[[str, str, Dict[str, Any]], tuple[str, ...]]"
        ] = None,
        estimator: Optional[DurationEstimator] = None,
        timeline_capacity: int = 400,
        monitor: Any = None,
        history: Any = None,
        max_in_flight_jobs: int = _DEFAULT_MAX_IN_FLIGHT_JOBS,
        max_active_tasks: int = _DEFAULT_MAX_ACTIVE_TASKS,
        max_tasks_per_workflow: int = _DEFAULT_MAX_TASKS_PER_WORKFLOW,
    ):
        """装配本地执行态调度器（Scheduler）。

        Args:
            orderer: 对已就绪任务进行稳定排序的策略。
            dispatcher: 把作业（Job）提交给执行器的适配器。
            external_busy_keys: 启动时已知的外部设备占用键。
            busy_key_provider: 实时读取设备占用键的函数。
            workflow_state_listener: 工作流（Workflow）终态通知函数。
            inventory: 本地库存（Inventory）预留、消费和释放服务。
            material_lock_resolver: 遗留直接调用根据实时注册表
                （Registry）Schema 与最终参数解析物料 UUID 的兼容函数。
            estimator: 动作预计时长计算器。
            timeline_capacity: 内存时间线最多保留的作业数量。
            monitor: 实时监控事件输出适配器。
            history: 遗留工作流执行历史存储。
            max_in_flight_jobs: 全局尚未收敛的在途作业上限。
            max_active_tasks: 全局运行中工作流任务上限。
            max_tasks_per_workflow: 同一工作流定义的运行任务上限。
        """

        for field, value in (
            ("max_in_flight_jobs", max_in_flight_jobs),
            ("max_active_tasks", max_active_tasks),
            ("max_tasks_per_workflow", max_tasks_per_workflow),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} 必须是正整数")
        if max_tasks_per_workflow > max_active_tasks:
            raise ValueError(
                "max_tasks_per_workflow 不能大于 max_active_tasks"
            )

        self._orderer = orderer or StableLocalOrderer()
        self._dispatcher = dispatcher or RecordingDispatcher()
        self._lock = threading.RLock()

        self._workflows: Dict[str, WorkflowRun] = {}
        # workflow_id -> 本次单步命令唯一允许派发的节点。只在一次重排期间存在。
        self._step_targets: Dict[str, str] = {}
        # job_id -> DispatchedJob（完成回调路由 + 资源锁）
        self._inflight: Dict[str, DispatchedJob] = {}
        # 外部注入的锁（例如 DeviceActionManager 已占用的设备），可选
        self._external_busy_keys = (
            external_busy_keys if external_busy_keys is not None else set()
        )
        # 实时锁视图提供者（微后端 busy_device_action_keys），可选
        self._busy_key_provider = busy_key_provider
        # 工作流终态通知（success/failed/canceled 各通知一次；锁外触发）
        self._workflow_state_listener = workflow_state_listener
        self._notified_workflows: Set[str] = set()
        self._reschedule_count = 0
        # 可选 InventoryService（duck-typed：reserve_workflow / consume_reservation /
        # quarantine_reservation / release_workflow）；None = 物料衔接整体关闭
        self._inventory = inventory
        # 有物料需求的 workflow（其余 workflow 不产生任何 inventory 调用）
        self._material_workflows: Set[str] = set()
        # 动作物料锁解析器消费规范动作 Schema；None 仅用于无注册表的隔离测试。
        self._material_lock_resolver = material_lock_resolver
        # job_id -> 该作业（Job）持有的物料与库位锁键；完成或取消时释放。
        self._job_resource_locks: Dict[str, Set[str]] = {}
        # 时长预估器（declared / historical / auto 三种 mode，内含两种计算模式）
        self._estimator = estimator or DurationEstimator()
        # 泳道图时间线：已完结 job 的起止记录（环形缓冲）
        self._timeline: Deque[Dict[str, Any]] = deque(maxlen=timeline_capacity)
        # 实时监控总线（duck-typed emit(channel, type, data)）；None = 关闭
        self._monitor = monitor
        # 工作流执行历史（WorkflowHistoryStore，独立 SQLite）；None = 不落盘
        self._history = history
        # 容量裁决由标准 Task/Job 持久状态完成；调度器只公开同一组配置，禁止
        # 另建进程内计数权威。默认值与 Backend 主线保持一致。
        self._max_in_flight_jobs = max_in_flight_jobs
        self._max_active_tasks = max_active_tasks
        self._max_tasks_per_workflow = max_tasks_per_workflow
        # 长生命周期根 span：workflow → action/job。只保存上下文/句柄，不保存 payload。
        self._workflow_spans: Dict[str, DetachedSpan] = {}
        self._job_spans: Dict[str, DetachedSpan] = {}
        # 生命周期监听器仅承载标准 Task/Job 兼容回写，不成为第二个状态权威。
        self._job_pre_dispatch_listeners: List[
            Callable[[Dict[str, Any]], bool | None]
        ] = []
        self._job_execution_wait_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._job_dispatch_accepted_listeners: List[Callable[[str], None]] = []
        self._job_dispatch_uncertain_listeners: List[
            Callable[[str, str], None]
        ] = []
        self._job_cancel_accepted_listeners: List[Callable[[str], None]] = []
        self._job_cancel_uncertain_listeners: List[
            Callable[[str, str], None]
        ] = []
        self._job_cancel_no_send_listeners: List[Callable[[str], None]] = []
        self._job_feedback_listeners: List[
            Callable[[str, Dict[str, Any]], None]
        ] = []
        self._job_finished_listeners: List[Callable[[str, bool, Any, str], None]] = []
        self._job_outcome_listeners: List[
            Callable[[str, CommittedJobOutcome], None]
        ] = []
        self._job_settled_listeners: List[Callable[[str, bool, Any, str], None]] = []
        # 准入重试监听器把尚未注册为旧调度运行的来源受阻任务接到同一个公开
        # 重排触发点；监听器本身仍由工作流任务桥拥有。
        self._admission_retry_listeners: List[Callable[[], None]] = []

    @property
    def inventory_service(self) -> Any:
        """返回本地调度器持有的库存服务（InventoryService）。

        参数：无。返回：同一库存权威（Inventory Authority）实例；未装配时为
        ``None``。该只读属性只供组合与桥接层验证和复用，禁止替换权威。
        """

        return self._inventory

    @property
    def max_in_flight_jobs(self) -> int:
        """返回全局在途作业容量。"""

        return self._max_in_flight_jobs

    @property
    def max_active_tasks(self) -> int:
        """返回全局运行任务容量。"""

        return self._max_active_tasks

    @property
    def max_tasks_per_workflow(self) -> int:
        """返回同一工作流定义的运行任务容量。"""

        return self._max_tasks_per_workflow

    def _emit_monitor(
        self, channel: str, event_type: str, data: Dict[str, Any]
    ) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.emit(channel, event_type, data)
        except Exception:  # noqa: BLE001 - 监控故障不影响调度
            pass

    def _safe_history(self, method: str, *args: Any, **kwargs: Any) -> None:
        """写执行历史；持久化故障不影响调度。"""
        if self._history is None:
            return
        try:
            getattr(self._history, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("[EdgeScheduler] history.%s failed", method)

    def set_workflow_state_listener(
        self, listener: "Callable[[str, str], None]"
    ) -> None:
        """替换工作流终态监听器；参数 ``listener`` 接收工作流身份和旧状态值。"""

        self._workflow_state_listener = listener

    def add_admission_retry_listener(self, listener: Callable[[], None]) -> None:
        """注册公开重排前的准入重试（AdmissionRetry）监听器。

        参数：``listener`` 负责重试尚未注册到旧调度器的持久任务。返回无；监听器
        异常会关闭失败并阻止本轮旧调度重排。
        """

        self._admission_retry_listeners.append(listener)

    def remove_admission_retry_listener(self, listener: Callable[[], None]) -> None:
        """移除准入重试（AdmissionRetry）监听器。

        参数：``listener`` 必须是此前注册的同一回调。返回无；重复移除保持幂等。
        """

        self._admission_retry_listeners = [
            current
            for current in self._admission_retry_listeners
            if current != listener
        ]

    def add_job_pre_dispatch_listener(
        self,
        listener: Callable[[Dict[str, Any]], bool | None],
    ) -> None:
        """注册作业派发前监听器。

        参数：``listener`` 接收即将派发的作业摘要。监听器返回 ``False`` 表示
        持久执行资源暂不可用，本轮保持等待；``None`` 兼容旧监听器并视为允许。
        异常会中止物理派发，禁止形成先发设备后记数据库的窗口。
        """

        self._job_pre_dispatch_listeners.append(listener)

    def remove_job_pre_dispatch_listener(
        self,
        listener: Callable[[Dict[str, Any]], bool | None],
    ) -> None:
        """移除派发前监听器；参数 ``listener`` 必须是此前注册的同一回调。"""

        self._job_pre_dispatch_listeners = [
            current
            for current in self._job_pre_dispatch_listeners
            if current != listener
        ]

    def add_job_execution_wait_listener(
        self,
        listener: Callable[[Dict[str, Any]], None],
    ) -> None:
        """注册执行资源忙等待监听器，使内存阻塞也留下持久等待事实。"""

        self._job_execution_wait_listeners.append(listener)

    def remove_job_execution_wait_listener(
        self,
        listener: Callable[[Dict[str, Any]], None],
    ) -> None:
        """幂等移除执行资源忙等待监听器。"""

        self._job_execution_wait_listeners = [
            current
            for current in self._job_execution_wait_listeners
            if current != listener
        ]

    def add_job_dispatch_accepted_listener(
        self,
        listener: Callable[[str], None],
    ) -> None:
        """注册执行适配器接受作业后的持久确认监听器。"""

        self._job_dispatch_accepted_listeners.append(listener)

    def remove_job_dispatch_accepted_listener(
        self,
        listener: Callable[[str], None],
    ) -> None:
        """幂等移除执行接受监听器。"""

        self._job_dispatch_accepted_listeners = [
            current
            for current in self._job_dispatch_accepted_listeners
            if current != listener
        ]

    def add_job_dispatch_uncertain_listener(
        self,
        listener: Callable[[str, str], None],
    ) -> None:
        """注册派发边界异常后的执行未知监听器。"""

        self._job_dispatch_uncertain_listeners.append(listener)

    def remove_job_dispatch_uncertain_listener(
        self,
        listener: Callable[[str, str], None],
    ) -> None:
        """幂等移除执行未知监听器。"""

        self._job_dispatch_uncertain_listeners = [
            current
            for current in self._job_dispatch_uncertain_listeners
            if current != listener
        ]

    def add_job_cancel_accepted_listener(
        self,
        listener: Callable[[str], None],
    ) -> None:
        """注册本地执行器明确接受取消后的持久投影监听器。"""

        self._job_cancel_accepted_listeners.append(listener)

    def remove_job_cancel_accepted_listener(
        self,
        listener: Callable[[str], None],
    ) -> None:
        """幂等移除本地取消受理监听器。"""

        self._job_cancel_accepted_listeners = [
            current
            for current in self._job_cancel_accepted_listeners
            if current != listener
        ]

    def add_job_cancel_uncertain_listener(
        self,
        listener: Callable[[str, str], None],
    ) -> None:
        """注册取消不能确认时的执行未知监听器。"""

        self._job_cancel_uncertain_listeners.append(listener)

    def remove_job_cancel_uncertain_listener(
        self,
        listener: Callable[[str, str], None],
    ) -> None:
        """幂等移除取消执行未知监听器。"""

        self._job_cancel_uncertain_listeners = [
            current
            for current in self._job_cancel_uncertain_listeners
            if current != listener
        ]

    def add_job_cancel_no_send_listener(
        self,
        listener: Callable[[str], None],
    ) -> None:
        """注册可证明未越过物理边界的取消监听器。"""

        self._job_cancel_no_send_listeners.append(listener)

    def remove_job_cancel_no_send_listener(
        self,
        listener: Callable[[str], None],
    ) -> None:
        """幂等移除未发送取消监听器。"""

        self._job_cancel_no_send_listeners = [
            current
            for current in self._job_cancel_no_send_listeners
            if current != listener
        ]

    def add_job_finished_listener(
        self,
        listener: Callable[[str, bool, Any, str], None],
    ) -> None:
        """注册作业完成监听器。

        参数：``listener`` 接收 Job UUID、成功标记、返回值和旧异常决策类型。返回
        无；用于把旧调度结果投影回标准工作流节点作业（WorkflowNodeJob）。
        """

        self._job_finished_listeners.append(listener)

    def add_job_outcome_listener(
        self,
        listener: Callable[[str, CommittedJobOutcome], None],
    ) -> None:
        """注册 Backend-shaped 不可变结果监听器。

        参数：``listener`` 接收作业 UUID 与完整终态证据。返回无。异常：无；实际
        回调异常在结果投影时向上传播，调度器会保留在途状态供投递重放。
        """

        self._job_outcome_listeners.append(listener)

    def add_job_feedback_listener(
        self,
        listener: Callable[[str, Dict[str, Any]], None],
    ) -> None:
        """注册 Edge 已持久反馈的标准工作流投影监听器。"""

        self._job_feedback_listeners.append(listener)

    def remove_job_feedback_listener(
        self,
        listener: Callable[[str, Dict[str, Any]], None],
    ) -> None:
        """幂等移除作业反馈监听器。"""

        self._job_feedback_listeners = [
            current for current in self._job_feedback_listeners if current != listener
        ]

    def remove_job_finished_listener(
        self,
        listener: Callable[[str, bool, Any, str], None],
    ) -> None:
        """移除作业完成监听器；参数 ``listener`` 必须是此前注册的同一回调。"""

        self._job_finished_listeners = [
            current for current in self._job_finished_listeners if current != listener
        ]

    def remove_job_outcome_listener(
        self,
        listener: Callable[[str, CommittedJobOutcome], None],
    ) -> None:
        """幂等移除不可变结果监听器。

        参数：``listener`` 是此前注册的同一回调。返回无。异常：无；未注册时保持
        原集合不变。
        """

        self._job_outcome_listeners = [
            current for current in self._job_outcome_listeners if current != listener
        ]

    def replay_persisted_edge_projections(
        self,
        *,
        feedback_listener: Callable[[str, Dict[str, Any]], None],
        outcome_listener: Callable[[str, CommittedJobOutcome], None] | None = None,
        finished_listener: Callable[[str, bool, Any, str], None] | None = None,
    ) -> Dict[str, int]:
        """把 Edge 本地已提交证据直接重放给持久工作流投影。

        进程重启后内存 DAG 尚未恢复，因此不绕经
        ``on_job_finished`` 的内存在途作业查找。无持久 Edge 发件箱的
        执行后端返回空计数。
        """

        replay = getattr(self._dispatcher, "replay_pending_projections", None)
        if not callable(replay):
            return {"feedback": 0, "outcomes": 0}
        result = replay(
            feedback_listener=feedback_listener,
            outcome_listener=outcome_listener,
            finished_listener=finished_listener,
        )
        if not isinstance(result, Mapping):
            raise ValueError("Edge 重放返回值必须是对象")
        return {
            "feedback": int(result.get("feedback", 0)),
            "outcomes": int(result.get("outcomes", 0)),
        }

    def add_job_settled_listener(
        self,
        listener: Callable[[str, bool, Any, str], None],
    ) -> None:
        """注册 DAG 节点结算监听器；仅在完成事实持久化并更新内存 DAG 后调用。"""

        self._job_settled_listeners.append(listener)

    def remove_job_settled_listener(
        self,
        listener: Callable[[str, bool, Any, str], None],
    ) -> None:
        """幂等移除节点结算监听器。"""

        self._job_settled_listeners = [
            current for current in self._job_settled_listeners if current != listener
        ]

    def _notify_job_pre_dispatch(self, dispatching: Dict[str, Any]) -> bool:
        """同步通知派发意图；参数 ``dispatching`` 是即将越过执行边界的摘要。

        返回：全部监听器允许派发时为真；任一标准持久准入返回 ``False`` 时为假。
        任何监听器异常都会阻止执行适配器调用。
        """

        for listener in tuple(self._job_pre_dispatch_listeners):
            if listener(dict(dispatching)) is False:
                return False
        return True

    def _notify_job_execution_wait(self, waiting: Dict[str, Any]) -> None:
        """在内存设备锁或动作物料锁先命中时同步持久化等待顺序。"""

        for listener in tuple(self._job_execution_wait_listeners):
            listener(dict(waiting))

    def _notify_job_dispatch_accepted(self, job_id: str) -> None:
        """在执行适配器返回接受后同步推进持久作业与租约。"""

        for listener in tuple(self._job_dispatch_accepted_listeners):
            listener(job_id)

    def _notify_job_dispatch_uncertain(self, job_id: str, reason: str) -> None:
        """在派发意图已提交但适配器未明确返回时保守记录执行未知。"""

        for listener in tuple(self._job_dispatch_uncertain_listeners):
            listener(job_id, reason)

    def _notify_job_cancel_accepted(self, job_id: str) -> None:
        """记录执行器已接受取消；该事实不释放在途作业或资源锁。"""

        for listener in tuple(self._job_cancel_accepted_listeners):
            listener(job_id)

    def _notify_job_cancel_uncertain(self, job_id: str, reason: str) -> None:
        """把取消拒绝、能力缺失或边界异常保守通知为执行未知。"""

        for listener in tuple(self._job_cancel_uncertain_listeners):
            listener(job_id, reason)

    def _notify_job_cancel_no_send(self, job_id: str) -> None:
        """通知持久层该作业可证明从未越过物理执行边界。"""

        for listener in tuple(self._job_cancel_no_send_listeners):
            listener(job_id)

    def _notify_job_finished(
        self,
        job_id: str,
        success: bool,
        ret_value: Any,
        suc_type: str,
    ) -> None:
        """在清理本地在途状态前通知一次完成事实。

        参数分别是作业身份、成功标记、设备返回值和旧异常决策类型。返回无；投影
        失败向上抛出，使同一完成事实可以投递重放（DeliveryReplay）；调用方不得
        在全部监听器确认前释放在途作业或动作物料锁（Action Material Lock）。
        """

        for listener in tuple(self._job_finished_listeners):
            listener(job_id, success, ret_value, suc_type)

    def _notify_job_outcome(
        self,
        job_id: str,
        outcome: CommittedJobOutcome,
    ) -> None:
        """在释放在途状态前投递完整不可变结果。

        参数：``job_id`` 是作业身份；``outcome`` 是 Edge 已提交终态与证据。返回
        无。异常：任一持久投影失败时原样传播，调用方不得释放在途作业和占用。
        """

        for listener in tuple(self._job_outcome_listeners):
            listener(job_id, outcome)

    def on_job_feedback(self, job_id: str, sample: Dict[str, Any]) -> None:
        """把 Edge 已提交反馈同步投递给工作流持久投影。"""

        for listener in tuple(self._job_feedback_listeners):
            listener(job_id, dict(sample))

    def _notify_job_settled(
        self,
        job_id: str,
        success: bool,
        ret_value: Any,
        suc_type: str,
    ) -> None:
        """在持久完成事实和 DAG 结算都成立后通知后继控制逻辑。"""

        for listener in tuple(self._job_settled_listeners):
            listener(job_id, success, ret_value, suc_type)

    # ── 触发点 1：任务进来 ────────────────────────────────────

    def submit_workflow(self, spec: WorkflowSpec) -> Dict[str, Any]:
        with self._lock:
            if (
                spec.workflow_id in self._workflows
                or spec.workflow_id in self._workflow_spans
            ):
                raise ValueError(f"workflow {spec.workflow_id} already submitted")
            workflow_trace = start_detached_span(
                "workflow.task.run",
                attributes={
                    "workflow.uuid": spec.workflow_id,
                    "workflow.task.uuid": spec.task_id,
                    "lab.id": spec.lab_id,
                    "workflow.plan.node_count": len(spec.nodes),
                    "workflow.priority": str(spec.priority),
                },
            )
            # 先登记 span 也充当 submit 占位，避免并发同 ID 覆盖对方的追踪句柄。
            self._workflow_spans[spec.workflow_id] = workflow_trace
        try:
            with workflow_trace.activate():
                with span(
                    "workflow.task.submit",
                    attributes={
                        "workflow.uuid": spec.workflow_id,
                        "workflow.task.uuid": spec.task_id,
                    },
                ):
                    return self._submit_workflow(spec)
        except BaseException as exc:
            workflow_trace.fail(exc)
            workflow_trace.end()
            self._workflow_spans.pop(spec.workflow_id, None)
            raise

    def _submit_workflow(self, spec: WorkflowSpec) -> Dict[str, Any]:
        """提交工作流并立即重排。返回本次下发结果。

        带物料需求时：入队前整 DAG all-or-nothing 预留；不足则置
        ``waiting_for_material``（不进入执行队列，后续每次重排自动重试）。
        """
        with self._lock:
            if spec.workflow_id in self._workflows:
                raise ValueError(f"workflow {spec.workflow_id} already submitted")
            run = WorkflowRun(spec)  # 构图 + 环检测，失败直接抛
            self._workflows[spec.workflow_id] = run

            requirements = spec.material_requirements_by_node()
            if requirements:
                if self._inventory is None:
                    logger.warning(
                        "[EdgeScheduler] workflow %s declares materials but no inventory "
                        "service wired; proceeding without reservation",
                        spec.workflow_id,
                    )
                else:
                    self._material_workflows.add(spec.workflow_id)
                    if not self._try_reserve(run):
                        run.state = WorkflowState.WAITING_MATERIAL

            logger.info(
                "[EdgeScheduler] workflow %s submitted (%d nodes, state=%s), reschedule",
                spec.workflow_id,
                len(spec.nodes),
                run.state.value,
            )
            self._emit_monitor(
                "scheduler",
                "workflow_submitted",
                {
                    "workflow_id": spec.workflow_id,
                    "nodes": len(spec.nodes),
                    "state": run.state.value,
                    "priority": str(spec.priority),
                },
            )
            self._safe_history("record_submitted", spec, run.state.value)
            dispatched = self._reschedule_locked()
            notifications = self._collect_terminal_notifications()
        self._fire_notifications(notifications)
        return {
            "workflow_id": spec.workflow_id,
            "state": run.state.value,
            "dispatched": dispatched,
        }

    def step_workflow(
        self,
        workflow_id: str,
        target_node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """让暂停的单步工作流只派发一个就绪节点，随后立即恢复暂停。

        参数：``workflow_id`` 是已提交运行身份；``target_node_id`` 可指定本次
        必须放行的就绪节点，空值按稳定图顺序选择第一个。返回本轮派发摘要。
        异常：未知任务、非单步任务、非暂停状态或目标尚未就绪时抛 ``ValueError``。
        """

        with self._lock:
            run = self._workflows.get(workflow_id)
            if run is None:
                raise ValueError(f"workflow {workflow_id} not found")
            if run.spec.run_mode != "step":
                raise ValueError(f"workflow {workflow_id} is not in step mode")
            if run.state is not WorkflowState.PAUSED:
                raise ValueError(f"workflow {workflow_id} is not paused")

            # ready_nodes 只允许 RUNNING 状态读取；该活动态仅存在于本次锁内重排。
            run.state = WorkflowState.RUNNING
            ready_nodes = run.ready_nodes()
            if target_node_id is None:
                selected = ready_nodes[0] if ready_nodes else None
            else:
                selected = next(
                    (node for node in ready_nodes if node.id == target_node_id),
                    None,
                )
            if selected is None:
                run.state = WorkflowState.PAUSED
                raise ValueError("step target is not ready")

            self._step_targets[workflow_id] = selected.id
            try:
                dispatched = self._reschedule_locked()
            finally:
                self._step_targets.pop(workflow_id, None)
                if run.state is WorkflowState.RUNNING:
                    run.state = WorkflowState.PAUSED
            notifications = self._collect_terminal_notifications()
            result = {
                "workflow_id": workflow_id,
                "state": run.state.value,
                "dispatched": dispatched,
            }
        self._fire_notifications(notifications)
        return result

    def pause_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Pause future dispatch for a normal workflow without stopping in-flight work."""

        with self._lock:
            run = self._workflows.get(workflow_id)
            if run is None:
                raise ValueError(f"workflow {workflow_id} not found")
            if run.spec.run_mode == "step":
                raise ValueError(f"workflow {workflow_id} is in step mode")
            if run.state in self._TERMINAL_STATES:
                raise ValueError(f"workflow {workflow_id} is terminal")
            if run.state is not WorkflowState.PAUSED:
                run.state = WorkflowState.PAUSED
                self._emit_monitor(
                    "scheduler",
                    "workflow_paused",
                    {"workflow_id": workflow_id, "reason": "command"},
                )
                self._safe_history("record_state", workflow_id, "paused")
            return {"workflow_id": workflow_id, "state": run.state.value}

    def resume_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Resume a command-paused normal workflow and run one scheduling round."""

        with self._lock:
            run = self._workflows.get(workflow_id)
            if run is None:
                raise ValueError(f"workflow {workflow_id} not found")
            if run.spec.run_mode == "step":
                raise ValueError(f"workflow {workflow_id} is in step mode")
            if run.state is not WorkflowState.PAUSED:
                raise ValueError(f"workflow {workflow_id} is not paused")
            run.state = WorkflowState.RUNNING
            dispatched = self._reschedule_locked()
            notifications = self._collect_terminal_notifications()
            result = {
                "workflow_id": workflow_id,
                "state": run.state.value,
                "dispatched": dispatched,
            }
        self._fire_notifications(notifications)
        return result

    def restore_workflow(
        self,
        spec: WorkflowSpec,
        completed_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从持久成功事实恢复一个未终态工作流（Workflow）。

        参数：``spec`` 是原任务冻结规格；``completed_results`` 按节点
        UUID 提供已持久成功的返回值。返回：恢复后状态与本轮新派
        发摘要。异常：未知完成节点、重复运行或派发失败原样传播；
        已完成节点只恢复 DAG 状态，绝不重放设备动作。
        """

        completed_node_ids = set(completed_results)
        known_node_ids = {node.id for node in spec.nodes if not node.disabled}
        unknown_node_ids = completed_node_ids - known_node_ids
        if unknown_node_ids:
            raise ValueError(
                f"workflow {spec.workflow_id} has unknown completed nodes: "
                f"{sorted(unknown_node_ids)}"
            )
        with self._lock:
            if (
                spec.workflow_id in self._workflows
                or spec.workflow_id in self._workflow_spans
            ):
                raise ValueError(f"workflow {spec.workflow_id} already submitted")
            workflow_trace = start_detached_span(
                "workflow.task.run",
                attributes={
                    "workflow.uuid": spec.workflow_id,
                    "workflow.task.uuid": spec.task_id,
                    "workflow.plan.node_count": len(spec.nodes),
                    "workflow.recovered.node_count": len(completed_results),
                },
            )
            self._workflow_spans[spec.workflow_id] = workflow_trace
        try:
            with workflow_trace.activate(), self._lock:
                run = WorkflowRun(spec)
                self._workflows[spec.workflow_id] = run
                requirements = spec.material_requirements_by_node()
                if requirements and self._inventory is not None:
                    self._material_workflows.add(spec.workflow_id)
                    if not self._try_reserve(run):
                        run.state = WorkflowState.WAITING_MATERIAL
                for node in spec.nodes:
                    if node.id in completed_results:
                        run.mark_finished(node.id, completed_results[node.id])
                logger.info(
                    "[EdgeScheduler] workflow %s restored (%d/%d nodes completed)",
                    spec.workflow_id,
                    len(completed_results),
                    len(spec.nodes),
                )
                self._emit_monitor(
                    "scheduler",
                    "workflow_restored",
                    {
                        "workflow_id": spec.workflow_id,
                        "completed_nodes": len(completed_results),
                        "nodes": len(spec.nodes),
                        "state": run.state.value,
                    },
                )
                dispatched = self._reschedule_locked()
                notifications = self._collect_terminal_notifications()
            self._fire_notifications(notifications)
            return {
                "workflow_id": spec.workflow_id,
                "state": run.state.value,
                "dispatched": dispatched,
            }
        except BaseException as exc:
            workflow_trace.fail(exc)
            workflow_trace.end()
            self._workflow_spans.pop(spec.workflow_id, None)
            raise

    def _try_reserve(self, run: WorkflowRun) -> bool:
        """尝试整 DAG 预留；不足返回 False（幂等，可反复重试）。"""
        try:
            self._inventory.reserve_workflow(
                run.spec.workflow_id, run.spec.material_requirements_by_node()
            )
            return True
        except InsufficientStock as exc:
            logger.info(
                "[EdgeScheduler] workflow %s waiting for material: %s",
                run.spec.workflow_id,
                exc,
            )
            return False

    # ── 触发点 2：子 action 完成 ──────────────────────────────

    def on_job_outcome(
        self,
        job_id: str,
        outcome: CommittedJobOutcome,
    ) -> Dict[str, Any]:
        """按 Backend-shaped 结果结算本地作业（Job）。

        参数：``job_id`` 是稳定作业身份；``outcome`` 保留 Edge 已提交的成功、
        失败、取消或超时终态及证据。返回：本轮工作流状态和后续派发摘要。异常：
        持久投影或库存结算失败原样传播；不会把失败类终态压缩成普通失败。
        """

        if outcome.unknown_command_ids:
            # 结果不明不是失败终态。先把证据投影为 ExecutionUnknown，同时保留
            # scheduler 的在途作业和动作资源占用，等待设备或人工完成物理对账。
            with self._lock:
                if job_id not in self._inflight:
                    logger.warning("[EdgeScheduler] unknown Job outcome: %s", job_id)
                    return {"dispatched": []}
                self._notify_job_outcome(job_id, outcome)
                return {
                    "state": "execution_unknown",
                    "dispatched": [],
                }

        success = outcome.outcome == "succeeded"
        ret_value = outcome.return_info.get(
            "return_value",
            outcome.return_info,
        )
        return self._finish_job_with_trace(
            job_id,
            success,
            ret_value,
            "normal" if success else outcome.outcome,
            committed_outcome=outcome,
        )

    def on_job_finished(
        self,
        job_id: str,
        success: bool,
        ret_value: Any = None,
        suc_type: str = "normal",
    ) -> Dict[str, Any]:
        """结算旧执行适配器的四参数完成回调。

        参数：作业身份、成功标记、返回值与旧异常分类。返回：工作流状态和后续派发
        摘要。异常：投影或库存结算失败原样传播。新 Edge HTTP 路径应使用
        :meth:`on_job_outcome`，本方法只保留旧执行适配器兼容。
        """

        return self._finish_job_with_trace(job_id, success, ret_value, suc_type)

    def _finish_job_with_trace(
        self,
        job_id: str,
        success: bool,
        ret_value: Any,
        suc_type: str,
        *,
        committed_outcome: CommittedJobOutcome | None = None,
    ) -> Dict[str, Any]:
        """在同一追踪边界内完成保真或旧式结果结算。

        参数：前四项是旧调度结果；``committed_outcome`` 存在时是优先投影的完整
        Edge 证据。返回结算摘要。异常：结算错误向上传播；追踪句柄始终关闭。
        """

        action_trace = self._job_spans.get(job_id)
        if action_trace is None:
            return self._on_job_finished(
                job_id,
                success,
                ret_value,
                suc_type,
                committed_outcome=committed_outcome,
            )
        try:
            with action_trace.activate():
                add_event(
                    "action.result",
                    {
                        "workflow.job.uuid": job_id,
                        "action.success": success,
                        "action.success.type": suc_type,
                    },
                    span=action_trace.span,
                )
                if not success:
                    action_trace.error("action execution failed")
                return self._on_job_finished(
                    job_id,
                    success,
                    ret_value,
                    suc_type,
                    committed_outcome=committed_outcome,
                )
        finally:
            action_trace.end()
            self._job_spans.pop(job_id, None)

    def _on_job_finished(
        self,
        job_id: str,
        success: bool,
        ret_value: Any = None,
        suc_type: str = "normal",
        *,
        committed_outcome: CommittedJobOutcome | None = None,
    ) -> Dict[str, Any]:
        """作业（Job）完成回调：写回结果、清理依赖并强制重排。

        ``suc_type`` 来自设备侧异常决策（registry.action_policy）：
        normal / skip / operator_intervention。skip 表示动作报错后人工选择
        跳过——节点按成功推进，但不把本节点预留结算为库存消耗。
        """
        with self._lock:
            job = self._inflight.get(job_id)
            if job is None:
                logger.warning("[EdgeScheduler] unknown job finished: %s", job_id)
                return {"dispatched": []}

            run = self._workflows.get(job.workflow_id)
            if run is None:
                return {"dispatched": []}

            # 库存只在设备明确成功后结算。扣减失败时保留在途作业、执行占用和
            # 完成投递，禁止出现“作业成功但库存仍未扣减”的公开事实。
            if (
                success
                and suc_type != "skip"
                and job.workflow_id in self._material_workflows
            ):
                node = next(
                    (candidate for candidate in run.spec.nodes if candidate.id == job.node_id),
                    None,
                )
                if node is not None and node.material_requirements:
                    self._inventory.consume_reservation(job.workflow_id, job.node_id)

            # 标准完成事实必须先持久化；任一监听器失败时保留在途作业与资源锁，
            # 允许设备对同一结果进行投递重放（DeliveryReplay）。库存消费本身也
            # 以相同 workflow/node/attempt 幂等，重放不会重复扣减。
            if committed_outcome is not None:
                self._notify_job_outcome(job_id, committed_outcome)
            else:
                self._notify_job_finished(job_id, success, ret_value, suc_type)
            self._inflight.pop(job_id, None)
            self._job_resource_locks.pop(job_id, None)

            # 泳道图时间线：记录实际起止 + 喂给历史统计（EMA）+ 历史库落盘
            canceled_by_executor = not success and suc_type == "canceled"
            self._record_timeline(
                job,
                success=success,
                suc_type=suc_type,
                ret_value=ret_value,
                state="canceled" if canceled_by_executor else "",
            )

            if success:
                run.mark_finished(job.node_id, ret_value)
            elif canceled_by_executor:
                # 取消请求本身不结算节点；只有执行器返回明确取消终态后，才消费
                # 在途节点并允许后续物理清理释放占用。
                run.mark_canceled(job.node_id)
            else:
                run.mark_failed(job.node_id)
                # 失败工作流的未下发节点不再推进；已下发的等它们各自回调
                logger.warning(
                    "[EdgeScheduler] node %s failed, workflow %s stops advancing",
                    job.node_id,
                    job.workflow_id,
                )

            # 调试器的继续/断点推进必须等当前节点已经写入 WorkflowRun；否则下一
            # 节点仍被依赖判定为未就绪。完成事实监听与 DAG 结算监听明确分阶段。
            self._notify_job_settled(job_id, success, ret_value, suc_type)

            logger.info(
                "[EdgeScheduler] job %s (wf=%s node=%s success=%s) finished, reschedule",
                job_id[:8],
                job.workflow_id,
                job.node_id,
                success,
            )
            dispatched = self._reschedule_locked()
            result = {
                "workflow_id": job.workflow_id,
                "workflow_state": run.state.value,
                "dispatched": dispatched,
            }
            notifications = self._collect_terminal_notifications()
        self._fire_notifications(notifications)
        return result

    def resolve_manual_confirmation(
        self,
        job_id: str,
        *,
        approved: bool,
        param: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """批准后以同一作业身份下发真实设备动作，拒绝则明确失败。"""

        if not approved:
            return self.on_job_finished(
                job_id,
                False,
                {"manual_confirmation": "rejected"},
                "normal",
            )
        complete_without_dispatch = False
        with self._lock:
            job = self._inflight.get(job_id)
            if job is None:
                raise ValueError("人工确认对应的作业不在运行中")
            run = self._workflows.get(job.workflow_id)
            node = run.node(job.node_id) if run is not None else None
            if run is None or node is None or not node.is_manual_confirm():
                raise ValueError("作业不是人工确认节点")
            if job.manual_action_dispatched:
                return {
                    "workflow_id": job.workflow_id,
                    "workflow_state": run.state.value,
                    "dispatched": [],
                }
            if not node.manual_continues_device_action:
                job.manual_action_dispatched = True
                complete_without_dispatch = True
            if complete_without_dispatch:
                approved_param = {}
                payload = None
            else:
                approved_param = (
                    dict(param) if param is not None else dict(job.resolved_args)
                )
                approved_locks = self._resource_lock_keys(node, approved_param)
                held_locks = self._job_resource_locks.get(job_id, set())
                if approved_locks != held_locks:
                    raise ValueError("批准参数改变了物料或库位占用范围")
                payload = build_job_start_payload(
                    job_id=job_id,
                    task_id=run.spec.task_id,
                    workflow_id=job.workflow_id,
                    node_id=job.node_id,
                    device_id=node.device_id,
                    action_name=node.action_name,
                    action_type=node.action_type,
                    action_args=approved_param,
                    always_free=node.always_free,
                )
            if complete_without_dispatch:
                pass
            else:
                assert payload is not None
                try:
                    self._dispatcher.dispatch(payload)
                except BaseException:
                    self._notify_job_dispatch_uncertain(
                        job_id,
                        "manual_confirmation_dispatch_acceptance_unknown",
                    )
                    raise
                job.resolved_args = approved_param
                job.manual_action_dispatched = True
            dispatched_result = {
                "workflow_id": job.workflow_id,
                "workflow_state": run.state.value,
                "dispatched": ([] if complete_without_dispatch else [
                    {
                        "job_id": job_id,
                        "workflow_id": job.workflow_id,
                        "node_id": job.node_id,
                        "device_action_key": job.device_action_key,
                    }
                ]),
            }
        if complete_without_dispatch:
            return self.on_job_finished(
                job_id,
                True,
                {"manual_confirmation": "approved"},
                "normal",
            )
        return dispatched_result

    def expire_manual_confirmation(self, job_id: str) -> Dict[str, Any]:
        """按持久截止时间关闭仍在等待的人工确认，不触发物理动作。"""

        with self._lock:
            job = self._inflight.get(job_id)
            if job is None:
                raise ValueError("人工确认对应的作业不在运行中")
            run = self._workflows.get(job.workflow_id)
            node = run.node(job.node_id) if run is not None else None
            if run is None or node is None or not node.is_manual_confirm():
                raise ValueError("作业不是人工确认节点")
            if job.manual_action_dispatched:
                raise ValueError("人工确认已经越过设备派发边界")
        return self.on_job_finished(
            job_id,
            False,
            {"manual_confirmation": "timed_out"},
            "manual_confirmation_timeout",
        )

    def request_uncertain_resolution(
        self,
        job_id: str,
        *,
        reason: str,
    ) -> Dict[str, Any]:
        """请求 Edge 证明未知设备命令已取消；这里只创建命令，不释放占用。"""

        store = getattr(self._dispatcher, "store", None)
        create_resolution = getattr(store, "create_unknown_resolution", None)
        if not callable(create_resolution):
            raise ValueError("当前执行边界不支持 UNKNOWN 处置证明")
        result = create_resolution(job_id, reason=reason)
        if not isinstance(result, Mapping):
            raise ValueError("UNKNOWN 处置命令返回非法")
        return dict(result)

    # ── 重排核心 ─────────────────────────────────────────────

    def reschedule(self) -> List[Dict[str, Any]]:
        """手动触发重排并先通知来源准入重试监听器。

        参数：无。返回：本轮旧调度器实际派发摘要。异常：准入监听器或调度
        失败原样传播；监听器在调度锁外运行，可把受阻任务安全注册到本调度器。
        """

        with self._lock:
            admission_retry_listeners = tuple(self._admission_retry_listeners)
        for listener in admission_retry_listeners:
            listener()
        with self._lock:
            return self._reschedule_locked()

    def _reschedule_locked(self) -> List[Dict[str, Any]]:
        with span(
            "workflow.task.reconcile",
            attributes={"scheduler.round": self._reschedule_count + 1},
        ) as reschedule_span:
            dispatched = self._reschedule_impl()
            add_event(
                "workflow.task.reconcile.result",
                {"scheduler.dispatched.count": len(dispatched)},
                span=reschedule_span,
            )
            return dispatched

    def _reschedule_impl(self) -> List[Dict[str, Any]]:
        """执行一轮完整重排，并下发当前能够安全执行的作业（Job）。

        参数：无；读取当前调度器（Scheduler）的工作流、库存、动作物料锁和
        进程内设备忙碌事实。
        Returns:
            本轮成功派发的作业摘要列表；物料冲突保持等待，合同错误标记失败。

        异常：参数解析和动作物料锁合同错误在对应工作流节点上失败关闭；库存
        或派发基础设施异常按既有边界处理。设备级互斥只提供当前进程安全桥，
        不表示已经取得持久作业执行占用（JobExecutionClaim）。
        """

        self._reschedule_count += 1

        # 等料工作流每次重排重试预留（补料后自动恢复 RUNNING）
        if self._inventory is not None:
            for run in self._workflows.values():
                workflow_trace = self._workflow_spans.get(run.spec.workflow_id)
                activation = (
                    workflow_trace.activate()
                    if workflow_trace is not None
                    else span("workflow.material.retry")
                )
                with activation:
                    reserved = (
                        run.state is WorkflowState.WAITING_MATERIAL
                        and self._try_reserve(run)
                    )
                if reserved:
                    run.state = WorkflowState.RUNNING
                    logger.info(
                        "[EdgeScheduler] workflow %s material reserved, resume running",
                        run.spec.workflow_id,
                    )
                    self._emit_monitor(
                        "scheduler",
                        "workflow_resumed",
                        {
                            "workflow_id": run.spec.workflow_id,
                            "reason": "material_reserved",
                        },
                    )
                    self._safe_history("record_state", run.spec.workflow_id, "running")

        ready: List[ReadyTask] = []
        for run in self._workflows.values():
            if run.state is not WorkflowState.RUNNING:
                continue
            weight = priority_weight(run.spec.priority)
            for node in run.ready_nodes():
                step_target = self._step_targets.get(run.spec.workflow_id)
                if step_target is not None and node.id != step_target:
                    continue
                ready.append(
                    ReadyTask(
                        workflow_id=run.spec.workflow_id,
                        node=node,
                        priority_weight=weight,
                        submitted_at=run.spec.submitted_at,
                    )
                )

        if not ready:
            return []

        busy = self._busy_keys()
        held_resource_locks = self._held_resource_locks()
        ordered = self._orderer.order(ready, OrderingContext(set(busy)))

        dispatched: List[Dict[str, Any]] = []
        for task in ordered:
            # 动作键继续服务时长估算、遥测与既有执行协议；设备键独立负责保证
            # 同一设备上的不同动作不会在本轮或跨重排并行派发。
            action_key = task.node.device_action_key
            device_key = task.node.device_lock_key
            # 人工确认和动作合同声明的 always_free 均不占设备锁；后者仍需通过
            # 下方物料/库位执行资源键准入，不能借免排队语义绕过资源安全。
            manual_confirm = task.node.is_manual_confirm()
            bypass_device_lock = manual_confirm or task.node.always_free
            # ``job_id`` 优先复用标准工作流节点作业（WorkflowNodeJob）身份；旧整图
            # 没有提供时才维持历史随机身份行为。等待与派发必须使用同一身份。
            job_id = task.node.job_id or uuid_mod.uuid4().hex

            run = self._workflows[task.workflow_id]
            try:
                resolved_args = run.resolve_params(task.node.id)
            except ParamResolveError as exc:
                logger.error(
                    "[EdgeScheduler] param resolve failed for wf=%s node=%s: %s",
                    task.workflow_id,
                    task.node.id,
                    exc,
                )
                run.mark_failed(task.node.id)
                continue

            # Schema 或库位解析失败必须关闭执行，不能退化为“没有资源锁”。
            try:
                resolved_args, resolved_site = self._resolve_transfer_site_target(
                    task.node,
                    resolved_args,
                )
                lock_keys = self._resource_lock_keys(
                    task.node,
                    resolved_args,
                    resolved_site=resolved_site,
                )
                access_region_locks = self._access_region_execution_locks(
                    run,
                    task.node,
                )
            except (
                ExecutionPolicyError,
                MaterialLockSchemaError,
                SiteTargetResolutionError,
            ) as error:
                logger.error(
                    "[EdgeScheduler] 动作资源锁解析失败 "
                    "wf=%s node=%s code=%s path=%s: %s",
                    task.workflow_id,
                    task.node.id,
                    getattr(error, "code", "invalid_execution_policy"),
                    getattr(error, "path", "/"),
                    getattr(error, "message", str(error)),
                )
                run.mark_failed(task.node.id)
                continue
            conflicting_lock_keys = conflicting_resource_lock_keys(
                lock_keys,
                held_resource_locks,
            )
            device_conflict = not bypass_device_lock and (
                action_key in busy or device_key in busy
            )
            execution_locks: list[dict[str, Any]] = []
            if not bypass_device_lock:
                execution_locks.append({"lock_key": device_key, "scope": "device"})
            for lock_key in sorted(lock_keys):
                parts = lock_key.split("/")
                if len(parts) == 3:
                    execution_locks.append(
                        {
                            "lock_key": lock_key,
                            "scope": "material",
                            "material_uuid": parts[1],
                        }
                    )
                elif len(parts) == 5:
                    execution_locks.append(
                        {
                            "lock_key": lock_key,
                            "scope": "material_site",
                            "material_uuid": parts[1],
                            "site_uuid": parts[3],
                        }
                    )
            execution_locks.extend(access_region_locks)
            if device_conflict or conflicting_lock_keys:
                blocking_job = next(
                    (
                        candidate
                        for candidate in sorted(
                            self._inflight.values(), key=lambda item: item.job_id
                        )
                        if (
                            device_conflict
                            and (
                                candidate.device_action_key == action_key
                                or candidate.device_id == task.node.device_id
                            )
                        )
                        or conflicting_resource_lock_keys(
                            lock_keys,
                            self._job_resource_locks.get(candidate.job_id, set()),
                        )
                    ),
                    None,
                )
                self._notify_job_execution_wait(
                    {
                        "job_id": job_id,
                        "workflow_id": task.workflow_id,
                        "node_id": task.node.id,
                        "resolved_args": resolved_args,
                        "execution_locks": execution_locks,
                        "blocking_job_id": (
                            blocking_job.job_id if blocking_job is not None else None
                        ),
                        "blocking_workflow_id": (
                            blocking_job.workflow_id
                            if blocking_job is not None
                            else None
                        ),
                    }
                )
                logger.info(
                    "[EdgeScheduler] node %s waits for execution lock(s) %s (wf=%s)",
                    task.node.id,
                    sorted(
                        set(conflicting_lock_keys)
                        | ({device_key} if device_conflict else set())
                    ),
                    task.workflow_id,
                )
                continue
            payload = build_job_start_payload(
                job_id=job_id,
                task_id=run.spec.task_id,
                workflow_id=task.workflow_id,
                node_id=task.node.id,
                device_id=task.node.device_id,
                action_name=task.node.action_name,
                action_type=task.node.action_type,
                action_args=resolved_args,
                always_free=task.node.always_free,
            )
            # 预估基于 sjson 覆写后的 resolved 参数：父节点经 gjson/sjson 传下来的
            # 实际值（如 time）直接决定声明式预估结果
            estimated_s, estimate_source = self._estimator.estimate(
                action_key, resolved_args
            )
            workflow_trace = self._workflow_spans.get(task.workflow_id)
            action_trace = start_detached_span(
                "action.run",
                parent_context=(
                    workflow_trace.context if workflow_trace is not None else None
                ),
                attributes={
                    "workflow.job.uuid": job_id,
                    "workflow.uuid": task.workflow_id,
                    "workflow.node.uuid": task.node.id,
                    "device.name": task.node.device_id,
                    "action.name": task.node.action_name,
                    "action.type": task.node.action_type,
                    "action.manual_confirm": manual_confirm,
                },
            )
            self._job_spans[job_id] = action_trace
            dispatch_intent_committed = False
            try:
                with action_trace.activate():
                    with span(
                        "workflow.job.dispatch",
                        attributes={
                            "workflow.job.uuid": job_id,
                            "workflow.uuid": task.workflow_id,
                            "workflow.node.uuid": task.node.id,
                            "device.name": task.node.device_id,
                            "action.name": task.node.action_name,
                        },
                    ):
                        # 标准任务/作业必须先提交派发意图，才能越过物理执行边界。
                        admitted = self._notify_job_pre_dispatch(
                            {
                                "job_id": job_id,
                                "workflow_id": task.workflow_id,
                                "node_id": task.node.id,
                                "device_action_key": action_key,
                                "estimated_s": round(estimated_s, 3),
                                "estimate_source": estimate_source,
                                "resolved_args": resolved_args,
                                "execution_locks": execution_locks,
                            }
                        )
                        if not admitted:
                            action_trace.event(
                                "action.waiting_for_execution_lock",
                                {"workflow.job.uuid": job_id},
                            )
                            action_trace.end()
                            self._job_spans.pop(job_id, None)
                            continue
                        dispatch_intent_committed = True
                        # 派发意图持久化后，先保守登记本地在途作业和动作物料锁，再
                        # 调用不可原子确认的执行适配器。适配器异常不得回滚这些事实。
                        run.mark_dispatched(task.node.id)
                        self._inflight[job_id] = DispatchedJob(
                            job_id=job_id,
                            workflow_id=task.workflow_id,
                            node_id=task.node.id,
                            device_action_key=action_key,
                            device_id=task.node.device_id,
                            action_name=task.node.action_name,
                            resolved_args=dict(resolved_args),
                            estimated_s=estimated_s,
                            estimate_source=estimate_source,
                        )
                        if lock_keys:
                            self._job_resource_locks[job_id] = lock_keys
                            held_resource_locks |= lock_keys
                        if not manual_confirm:
                            self._dispatcher.dispatch(payload)
                        self._notify_job_dispatch_accepted(job_id)
            except BaseException as exc:
                action_trace.fail(exc)
                action_trace.end()
                self._job_spans.pop(job_id, None)
                if dispatch_intent_committed:
                    try:
                        self._notify_job_dispatch_uncertain(
                            job_id,
                            "local_dispatch_acceptance_unknown",
                        )
                    except BaseException as projection_error:
                        raise projection_error from exc
                raise
            # 人工确认节点不进入执行器，但仍已在上方登记为在途作业，由统一完成
            # 接口提交明确结果。
            action_trace.event(
                "action.dispatched",
                {
                    "workflow.job.uuid": job_id,
                    "action.estimate.seconds": estimated_s,
                    "action.estimate.source": estimate_source,
                },
            )
            if not bypass_device_lock:
                # 同轮立即登记两种键；后续候选即使动作不同，也不能绕过设备互斥。
                busy.update((action_key, device_key))
            # ``dispatched_item`` 同时供返回值、监控和标准 Task/Job 状态投影使用。
            dispatched_item = {
                "job_id": job_id,
                "workflow_id": task.workflow_id,
                "node_id": task.node.id,
                "device_action_key": action_key,
                "estimated_s": round(estimated_s, 3),
                "estimate_source": estimate_source,
            }
            dispatched.append(dispatched_item)
            self._emit_monitor(
                "action",
                "job_dispatched",
                {
                    "job_id": job_id,
                    "workflow_id": task.workflow_id,
                    "node_id": task.node.id,
                    "device_id": task.node.device_id,
                    "action_name": task.node.action_name,
                    "device_action_key": action_key,
                    "estimated_s": round(estimated_s, 3),
                    "estimate_source": estimate_source,
                    "manual_confirm": manual_confirm,
                },
            )
            if not manual_confirm:
                self._emit_monitor(
                    "device",
                    "device_busy",
                    {
                        "device_id": task.node.device_id,
                        "action_name": task.node.action_name,
                        "device_action_key": action_key,
                        "job_id": job_id,
                        "workflow_id": task.workflow_id,
                    },
                )

        if ready:
            self._emit_monitor(
                "scheduler",
                "reschedule",
                {
                    "round": self._reschedule_count,
                    "ready": len(ready),
                    "dispatched": len(dispatched),
                },
            )
        return dispatched

    # 终态集合与云端 workflow_task 一致；TIMEOUT 当前由云端判定，列入以备
    # Edge 后续本地超时（词汇不再变更）。
    _TERMINAL_STATES = (
        WorkflowState.SUCCESS,
        WorkflowState.FAILED,
        WorkflowState.CANCELED,
        WorkflowState.TIMEOUT,
    )

    def _collect_terminal_notifications(self) -> List["tuple[str, str]"]:
        """收集已经物理结算的终态工作流。

        参数：无；调用方必须持有调度器锁。返回：尚未通知且已经没有在途作业的
        ``(workflow_id, state)`` 列表。仍有设备动作在途的失败任务延后通知与库存
        释放，避免业务失败先于物理停止时让同一物料被再次使用。
        """

        pending: List["tuple[str, str]"] = []
        for wid, run in self._workflows.items():
            if (
                run.state not in self._TERMINAL_STATES
                or wid in self._notified_workflows
                or any(job.workflow_id == wid for job in self._inflight.values())
            ):
                continue
            self._notified_workflows.add(wid)
            pending.append((wid, run.state.value))
            self._emit_monitor(
                "scheduler",
                "workflow_state",
                {"workflow_id": wid, "state": run.state.value},
            )
            self._safe_history("record_state", wid, run.state.value)
        return pending

    def _fire_notifications(self, notifications: List["tuple[str, str]"]) -> None:
        for wid, state in notifications:
            workflow_trace = self._workflow_spans.get(wid)
            activation = (
                workflow_trace.activate()
                if workflow_trace is not None
                else span("workflow.task.terminal")
            )
            with activation:
                add_event(
                    "workflow.task.terminal",
                    {"workflow.uuid": wid, "workflow.state": state},
                    span=workflow_trace.span if workflow_trace is not None else None,
                )
                if workflow_trace is not None and state != WorkflowState.SUCCESS.value:
                    workflow_trace.error(f"workflow {state}")
                # 终态工作流释放剩余 active 预留（幂等，依据 DB 状态而非内存）
                if wid in self._material_workflows:
                    self._safe_inventory_call(
                        "release_workflow",
                        wid,
                        reason=f"workflow_{state}",
                    )
                if self._workflow_state_listener is not None:
                    try:
                        self._workflow_state_listener(wid, state)
                    except Exception:  # noqa: BLE001 - 通知失败不影响调度
                        logger.exception(
                            "[EdgeScheduler] workflow state listener failed"
                        )
            if workflow_trace is not None:
                workflow_trace.end()
                self._workflow_spans.pop(wid, None)

    def _safe_inventory_call(self, method: str, *args: Any, **kwargs: Any) -> None:
        """调用 inventory（release/quarantine 等善后操作）；失败记日志不阻断调度。"""
        if self._inventory is None:
            return
        try:
            getattr(self._inventory, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001 - 善后失败可由人工经 inventory API 补救
            logger.exception("[EdgeScheduler] inventory.%s failed", method)

    # ── 执行资源锁 ───────────────────────────────────────────

    def _held_resource_locks(self) -> Set[str]:
        held: Set[str] = set()
        for keys in self._job_resource_locks.values():
            held |= keys
        return held

    def _resolve_transfer_site_target(
        self,
        node: Any,
        resolved_args: Dict[str, Any],
    ) -> tuple[Dict[str, Any], ResolvedSiteTarget | None]:
        """解析转运动作的目标库位并规范化设备执行名称。

        参数：``node`` 是候选工作流节点；``resolved_args`` 是合并上游输出后的
        最终参数。返回：规范化参数副本与可选库位目标；非 ``transfer_resource``
        动作或没有指定库位时原样返回。异常：显式 ``site_uuid`` 无法验证时抛
        ``SiteTargetResolutionError``；旧任务只有 ``site`` 且未装配库存权威时
        保留原有整父物料锁行为，不破坏历史本地工作流。
        """

        if getattr(node, "action_name", "") != "transfer_resource":
            return resolved_args, None
        site_uuid = str(resolved_args.get("site_uuid") or "").strip()
        site_name = str(resolved_args.get("site") or "").strip()
        if not site_uuid and not site_name:
            return resolved_args, None

        store = getattr(self._inventory, "store", None)
        if not callable(getattr(store, "query_one", None)):
            if site_uuid:
                raise SiteTargetResolutionError(
                    "site_authority_unavailable",
                    "提供 site_uuid 时必须先初始化本地库存权威",
                )
            return resolved_args, None

        mount_uuid = _resource_argument_uuid(
            resolved_args.get("mount_resource"),
            argument_name="mount_resource",
        )
        resource_uuid = _resource_argument_uuid(
            resolved_args.get("resource"),
            argument_name="resource",
        )
        try:
            target = resolve_site_target(
                self._inventory,
                owner_material_uuid=mount_uuid,
                site_uuid=site_uuid,
                site_name=site_name,
                occupant_material_uuid=resource_uuid,
            )
        except SiteTargetResolutionError as error:
            if site_uuid or error.code != "site_not_found":
                raise
            # 历史工作流只保存设备侧库位名；本地 Site 投影尚未补齐时维持旧执行
            # 语义，并保守占用整个父物料。显式 UUID 绝不走该兼容分支。
            logger.warning(
                "[EdgeScheduler] 旧库位名未进入本地库存；退回整父物料忙碌键 "
                "wf_node=%s site=%s",
                getattr(node, "id", ""),
                site_name,
            )
            return resolved_args, None
        canonical_args = dict(resolved_args)
        # 设备驱动沿用库位名称；稳定 UUID 只用于身份解析和本地互斥。
        canonical_args["site"] = target.name
        return canonical_args, target

    @staticmethod
    def _access_region_execution_locks(run: Any, node: Any) -> list[dict[str, Any]]:
        """把冻结访问区域策略转换为由后续作业持有的持久占用声明。

        参数：``run`` 是当前工作流运行，``node`` 是准备派发的入口节点。返回：
        零或一个 ``access_region`` 请求；释放作业身份直接来自同一冻结规格。
        异常：策略损坏、释放节点缺失或释放作业没有稳定身份时抛
        ``ExecutionPolicyError``，禁止退化为普通短锁继续执行。
        """

        policy = getattr(node, "execution_policy", {})
        access = policy.get("access_region") if isinstance(policy, Mapping) else None
        if access is None:
            return []
        if not isinstance(access, Mapping):
            raise ExecutionPolicyError("access_region 必须是对象")
        region_key = str(access.get("key") or "").strip()
        material_uuid = str(access.get("lock_material_uuid") or "").strip()
        release_node_uuid = str(access.get("release_node_uuid") or "").strip()
        release_node = next(
            (
                candidate
                for candidate in run.spec.nodes
                if candidate.id == release_node_uuid
            ),
            None,
        )
        if (
            not region_key
            or not material_uuid
            or release_node is None
            or release_node.executor_kind != "material_transfer"
            or not release_node.job_id
        ):
            raise ExecutionPolicyError("访问区域缺少有效的物料转移释放作业")
        return [
            {
                "lock_key": f"access_region/{material_uuid}/{region_key}",
                "scope": "access_region",
                "material_uuid": material_uuid,
                "access_region_key": region_key,
                "lease_owner_job_uuid": release_node.job_id,
            }
        ]

    def _resource_lock_keys(
        self,
        node: Any,
        resolved_args: Dict[str, Any],
        *,
        resolved_site: ResolvedSiteTarget | None = None,
    ) -> Set[str]:
        """生成节点本次执行需要持有的物料锁和库位锁键。

        参数：``node`` 是当前准备派发的工作流节点（WorkflowNode），
        ``resolved_args`` 是合并上游输出后的最终动作参数。返回：使用
        ``material/{uuid}/exclusive`` 物料锁；``resolved_site`` 非空时把
        ``mount_resource`` 的整物料锁替换为具体库位锁。异常：冻结动作合同
        （Action Contract）、遗留注册表（Registry）Schema 或最终参数不能安全
        解析时抛 ``MaterialLockSchemaError``。
        """

        keys: Set[str] = set()
        frozen_schema = getattr(node, "param_schema", None)
        if frozen_schema is not None:
            material_uuids = compile_material_lock_schema(
                frozen_schema
            ).material_lock_uuids(resolved_args)
            keys.update(material_lock_key(item) for item in material_uuids)
        elif self._material_lock_resolver is not None:
            # ``material_uuids`` 只为无冻结合同的遗留直接调用读取实时注册表。
            material_uuids = self._material_lock_resolver(
                node.device_id,
                node.action_name,
                resolved_args,
            )
            keys.update(
                material_lock_key(material_uuid)
                for material_uuid in material_uuids
            )
        if resolved_site is not None:
            keys.discard(material_lock_key(resolved_site.owner_material_uuid))
            keys.add(
                site_lock_key(
                    resolved_site.owner_material_uuid,
                    resolved_site.uuid,
                )
            )
        # 工作流级实体物料需求是独立声明；若它要求整父物料，则必须覆盖上方
        # 动作参数派生出的细粒度库位键，不能被替换逻辑误删。
        for req in getattr(node, "material_requirements", []) or []:
            if getattr(req, "instance_uuid", ""):
                keys.add(material_lock_key(req.instance_uuid))
        # 实体型物料需求可能与动作合同中的子库位成员指向同一拥有者；整物料
        # 占用覆盖子库位，归一化后避免同一作业保存冗余键。
        return normalize_resource_lock_keys(keys)

    def _busy_keys(self) -> Set[str]:
        """合并外部与本地在途作业的动作级、设备级内存忙碌键。

        参数：无；外部键来自构造注入集合和可选实时提供者。
        返回：供一次准入重排使用的忙碌键副本；不会把人工确认节点计入互斥。
        异常：外部提供者异常会被记录，并沿用既有降级，仅使用已知本地事实。

        该集合不会跨进程重启恢复，也没有占用 UUID 或栅栏令牌，因此不是持久
        作业执行占用（JobExecutionClaim）。
        """

        busy = set(self._external_busy_keys)
        if self._busy_key_provider is not None:
            try:
                busy |= set(self._busy_key_provider())
            except Exception:  # noqa: BLE001 - 锁视图失败时退化为 inflight 视图
                logger.exception("[EdgeScheduler] busy_key_provider failed")
        # 外部执行层仍使用动作级忙碌键；保留原键用于既有协议，同时把严格
        # 形状稳定提升为设备键，使取消后的物理在途作业继续阻塞同设备其他动作。
        for external_action_key in tuple(busy):
            device_key = _device_key_from_strict_action_key(external_action_key)
            if device_key is not None:
                busy.add(device_key)
        for job in self._inflight.values():
            # 由在途作业身份回到其工作流节点，只为识别不占设备的人工确认节点。
            run = self._workflows.get(job.workflow_id)
            node = run.node(job.node_id) if run is not None else None
            # 人工确认节点只等待操作者输入，不使用设备执行器，也不建立设备互斥。
            if node is not None and node.is_manual_confirm():
                continue
            busy.add(job.device_action_key)
            busy.add(f"/devices/{job.device_id}")
        return busy

    # ── 泳道图时间线 ─────────────────────────────────────────

    def _record_timeline(
        self,
        job: DispatchedJob,
        success: bool,
        suc_type: str = "normal",
        state: str = "",
        ret_value: Any = None,
    ) -> None:
        """job 完结（成功/失败/取消）时记录时间线并喂历史统计（须在锁内调用）。"""
        ended_at = time.time()
        actual_s = max(0.0, ended_at - job.dispatched_at)
        if not state:
            state = "success" if success else "failed"
        # 只有正常成功的样本才进入历史统计（skip/失败/取消的时长不代表真实执行）
        if success and suc_type == "normal":
            self._estimator.observe(job.device_action_key, actual_s)
        entry = {
            "job_id": job.job_id,
            "workflow_id": job.workflow_id,
            "node_id": job.node_id,
            "device_id": job.device_id,
            "action_name": job.action_name,
            "device_action_key": job.device_action_key,
            "started_at": job.dispatched_at,
            "ended_at": ended_at,
            "actual_s": round(actual_s, 3),
            "estimated_s": round(job.estimated_s, 3),
            "estimate_source": job.estimate_source,
            "state": state,
            "suc_type": suc_type,
        }
        self._timeline.append(entry)
        # 历史库落盘（独立 SQLite；含截断后的返回值，供审计/回放）
        self._safe_history("record_job", entry, ret_value)
        self._emit_monitor(
            "action",
            "job_finished",
            {
                "job_id": job.job_id,
                "workflow_id": job.workflow_id,
                "node_id": job.node_id,
                "device_id": job.device_id,
                "action_name": job.action_name,
                "device_action_key": job.device_action_key,
                "state": state,
                "suc_type": suc_type,
                "actual_s": round(actual_s, 3),
                "estimated_s": round(job.estimated_s, 3),
            },
        )
        self._emit_monitor(
            "device",
            "device_idle",
            {
                "device_id": job.device_id,
                "action_name": job.action_name,
                "device_action_key": job.device_action_key,
                "job_id": job.job_id,
            },
        )

    def timeline(self, window_s: float = 3600.0) -> Dict[str, Any]:
        """泳道图数据：执行中 job + 窗口内已完结 job + 预估器状态。

        泳道由前端按 device_id（或 device_action_key）分组；running 条目
        用 started_at + estimated_s 画预估终点，completed 条目画实际区间。
        """
        now = time.time()
        cutoff = now - max(window_s, 0.0)
        with self._lock:
            running = [
                {
                    "job_id": j.job_id,
                    "workflow_id": j.workflow_id,
                    "node_id": j.node_id,
                    "device_id": j.device_id,
                    "action_name": j.action_name,
                    "device_action_key": j.device_action_key,
                    "started_at": j.dispatched_at,
                    "elapsed_s": round(max(0.0, now - j.dispatched_at), 3),
                    "estimated_s": round(j.estimated_s, 3),
                    "estimate_source": j.estimate_source,
                }
                for j in self._inflight.values()
            ]
            completed = [e for e in self._timeline if e["ended_at"] >= cutoff]
            return {
                "now": now,
                "window_s": window_s,
                "running": running,
                "completed": completed,
                "estimator": {
                    "mode": self._estimator.mode,
                    "default_s": self._estimator.default_s,
                    "stats": self._estimator.stats(),
                },
            }

    def device_status(self) -> List[Dict[str, Any]]:
        """设备占用视图（监控面板）：busy 来自 inflight，idle 来自时间线痕迹。"""
        now = time.time()
        with self._lock:
            devices: Dict[str, Dict[str, Any]] = {}
            # 时间线里出现过的设备默认 idle（带最近一次动作）
            for entry in self._timeline:
                dev = entry["device_id"] or entry["device_action_key"]
                cur = devices.get(dev)
                if cur is None or entry["ended_at"] > cur.get("last_seen", 0):
                    devices[dev] = {
                        "device_id": dev,
                        "status": "idle",
                        "last_action": entry["action_name"],
                        "last_state": entry["state"],
                        "last_seen": entry["ended_at"],
                    }
            # 在执行 job 的设备置 busy
            for j in self._inflight.values():
                dev = j.device_id or j.device_action_key
                devices[dev] = {
                    "device_id": dev,
                    "status": "busy",
                    "action_name": j.action_name,
                    "job_id": j.job_id,
                    "workflow_id": j.workflow_id,
                    "started_at": j.dispatched_at,
                    "elapsed_s": round(max(0.0, now - j.dispatched_at), 3),
                    "estimated_s": round(j.estimated_s, 3),
                    "estimate_source": j.estimate_source,
                    "last_seen": now,
                }
            return sorted(devices.values(), key=lambda d: d["device_id"])

    # ── 查询 ─────────────────────────────────────────────────

    def workflow_snapshot(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            run = self._workflows.get(workflow_id)
            if run is None:
                return None
            snap = run.snapshot()
            # 叠加在执行 job_id：前端对 manual_confirm 节点凭它调 /jobs/{id}/finish
            nodes = snap.get("nodes", {})
            for job_id, job in self._inflight.items():
                if job.workflow_id == workflow_id and job.node_id in nodes:
                    nodes[job.node_id]["job_id"] = job_id
            return snap

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "workflows": {
                    wid: run.snapshot() for wid, run in self._workflows.items()
                },
                "inflight_jobs": {
                    job_id: {
                        "workflow_id": j.workflow_id,
                        "node_id": j.node_id,
                        "device_action_key": j.device_action_key,
                        "resource_locks": sorted(
                            self._job_resource_locks.get(job_id, set())
                        ),
                        "started_at": j.dispatched_at,
                        "estimated_s": round(j.estimated_s, 3),
                        "estimate_source": j.estimate_source,
                    }
                    for job_id, j in self._inflight.items()
                },
                "reschedule_count": self._reschedule_count,
            }

    def cancel_workflow(self, workflow_id: str) -> bool:
        """停止后续派发并请求执行器取消全部在途设备作业。

        参数：``workflow_id`` 是本地工作流任务（WorkflowTask）身份。返回：找到
        运行并提交取消时为真，不存在时为假。异常：持久监听器或执行适配器异常
        不会释放在途作业；调用方可重放命令。

        未发送作业可立即结算；已发送作业始终保留在 ``_inflight`` 与资源锁中，
        直到设备明确返回终态。执行器拒绝、缺少取消能力或调用异常只产生执行未知
        （ExecutionUnknown），绝不伪造设备已经停止。
        """

        with self._lock:
            run = self._workflows.get(workflow_id)
            if run is None:
                return False
            run.cancel()
            canceling_jobs = [
                job_id
                for job_id, j in self._inflight.items()
                if j.workflow_id == workflow_id
            ]
            cancel_method = getattr(self._dispatcher, "cancel", None)
            for job_id in canceling_jobs:
                try:
                    state = (
                        cancel_method(
                            job_id,
                            lambda accepted, current_job_id=job_id: (
                                self._notify_job_cancel_accepted(current_job_id)
                                if accepted
                                else self._notify_job_cancel_uncertain(
                                    current_job_id,
                                    "local_cancel_rejected",
                                )
                            ),
                        )
                        if callable(cancel_method)
                        else CancelDispatchState.UNAVAILABLE
                    )
                except BaseException:
                    self._notify_job_cancel_uncertain(
                        job_id,
                        "local_cancel_request_exception",
                    )
                    continue
                try:
                    normalized_state = CancelDispatchState(state)
                except ValueError:
                    normalized_state = CancelDispatchState.UNAVAILABLE
                if normalized_state == CancelDispatchState.REQUESTED:
                    continue
                if normalized_state == CancelDispatchState.NOT_SENT:
                    # No-send Proof 允许同步结算并释放内存锁；持久监听器必须先成功。
                    self._notify_job_cancel_no_send(job_id)
                    job = self._inflight.pop(job_id, None)
                    self._job_resource_locks.pop(job_id, None)
                    action_trace = self._job_spans.pop(job_id, None)
                    if action_trace is not None:
                        action_trace.event(
                            "action.cancel_no_send",
                            {"workflow.job.uuid": job_id},
                        )
                        action_trace.end()
                    if job is not None:
                        run.mark_canceled(job.node_id)
                        self._record_timeline(
                            job,
                            success=False,
                            suc_type="canceled",
                            state="canceled",
                        )
                    self._notify_job_settled(job_id, False, None, "canceled")
                    continue
                self._notify_job_cancel_uncertain(
                    job_id,
                    "local_cancel_acceptance_unavailable",
                )
            notifications = self._collect_terminal_notifications()
        self._fire_notifications(notifications)
        return True


__all__ = ["EdgeScheduler"]
