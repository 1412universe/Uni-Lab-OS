"""
锁紧模块设备驱动。

协议：opcua_gn1.4.0.csv「锁紧模块」（前缀 Lock_）；握手与 gn_quick_carrier_exchange 一致：
    清 CompleteFB → 写参 → Lock_CmdType → Lock_CmdTrig=1 → 等 Lock_CompleteFB=1 → 清零。
    CompleteFB：1=成功，2=失败（看 Lock_ScrewResult）。

对外仅暴露 execute_command（Lock_CmdType + 写参）；测试流程预设供本地调试。

指令类型 (Lock_CmdType)：
    1=X向左 2=X向右 3=Y向左 4=Y向右
    5=Z1向左 6=Z1向右 7=Z2向左 8=Z2向右
    9=夹爪夹取 10=夹爪放置
    11=电批终锁(扭矩锁死) 12=电批拧松
    13=夹爪夹紧 14=夹爪松开
    15=电批预锁(按圈数,不锁死) 16=复位

电批写参（×比例见 CSV / 协议「锁紧预锁说明」）：
    pre_turns → Lock_PreTurnsSet（×0.1 圈，Type=15 必写）
    pre_torque_limit → Lock_PreTorqueLimit（×0.001 N.m，Type=15 必写）
    final_torque → Lock_FinalTorqueSet（×0.001 N.m，Type=11 必写；Type=15 不写）
"""

import os
import time
import logging
import threading
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.device_comms.opcua_client.node.uniopcua import DataType, NodeType
from unilabos.devices.workstation.GN.base_opcua_client import OpcUaNode
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient

# CSV 写 DOUBLE，汇川 OPC 实际多为 FLOAT(REAL)；按 Double 写会 BadTypeMismatch
_JAW_FLOAT_NODES = ("Lock_JawPosition", "Lock_JawForce")

_OPC_NS = "ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA."
# 1.4.0 电批点位：工站若先加载旧 CSV，在此强制注册，避免 PreTurns 等节点缺失
_SCREW_OPC_NODES = (
    OpcUaNode(name="Lock_PreTurnsSet", node_type=NodeType.VARIABLE, node_id=_OPC_NS + "Lock_PreTurnsSet", data_type=DataType.INT16),
    OpcUaNode(name="Lock_PreTorqueLimit", node_type=NodeType.VARIABLE, node_id=_OPC_NS + "Lock_PreTorqueLimit", data_type=DataType.INT16),
    OpcUaNode(name="Lock_FinalTorqueSet", node_type=NodeType.VARIABLE, node_id=_OPC_NS + "Lock_FinalTorqueSet", data_type=DataType.INT16),
    OpcUaNode(name="Lock_ScrewResult", node_type=NodeType.VARIABLE, node_id=_OPC_NS + "Lock_ScrewResult", data_type=DataType.INT16),
    OpcUaNode(name="Lock_TorqueFB", node_type=NodeType.VARIABLE, node_id=_OPC_NS + "Lock_TorqueFB", data_type=DataType.INT16),
    OpcUaNode(name="Lock_AngleFB", node_type=NodeType.VARIABLE, node_id=_OPC_NS + "Lock_AngleFB", data_type=DataType.INT16),
)

DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "opcua_gn1.4.0.csv",
)
# 注册表 init 仍引用旧名 xlsx_path，与 csv_path 同义
DEFAULT_XLSX_PATH = DEFAULT_CSV_PATH

# Lock_CmdType（opcua_gn1.4.0）
LOCK_CMD_LABELS = {
    1: "X向左",
    2: "X向右",
    3: "Y向左",
    4: "Y向右",
    5: "Z1向左",
    6: "Z1向右",
    7: "Z2向左",
    8: "Z2向右",
    9: "夹爪夹取",
    10: "夹爪放置",
    11: "电批终锁",
    12: "电批拧松",
    13: "夹爪夹紧",
    14: "夹爪松开",
    15: "电批预锁",
    16: "复位",
}

# Lock_ScrewResult：0无 1OK 2滑牙 3浮锁 4超时 5故障 6预锁到扭矩上限(算成功)
SCREW_RESULT_LABELS = {
    0: "无",
    1: "OK",
    2: "滑牙",
    3: "浮锁",
    4: "超时",
    5: "故障",
    6: "预锁到扭矩上限",
}


class LockCommand(int, Enum):
    """锁紧模块指令类型 (Lock_CmdType)"""

    X_LEFT = 1
    X_RIGHT = 2
    Y_LEFT = 3
    Y_RIGHT = 4
    Z1_LEFT = 5
    Z1_RIGHT = 6
    Z2_LEFT = 7
    Z2_RIGHT = 8
    JAW_PICK = 9
    JAW_PLACE = 10
    SCREW_FINAL = 11
    SCREW_LOOSEN = 12
    JAW_CLAMP = 13
    JAW_RELEASE = 14
    SCREW_PRE = 15
    RESET = 16

    # 兼容旧名
    SCREW_TIGHTEN = 11


# 锁紧模块测试流程预设（本地 run_test_flow，非注册动作）
TEST_FLOW_PRESETS = [
    ("1.夹取耗材", LockCommand.JAW_PICK, dict(
        x_pos=3700, y_pos=930, z1_pos=0, z2_pos=1105,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=30.0, jaw_force=0.1,
    )),
    ("2.放置耗材", LockCommand.JAW_PLACE, dict(
        x_pos=760, y_pos=780, z1_pos=0, z2_pos=1150,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=11.0, jaw_force=0.1,
    )),
    ("3.夹取盖板", LockCommand.JAW_PICK, dict(
        x_pos=2290, y_pos=980, z1_pos=0, z2_pos=875,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=65.0, jaw_force=0.1,
    )),
    ("4.放置盖板", LockCommand.JAW_PLACE, dict(
        x_pos=760, y_pos=740, z1_pos=0, z2_pos=760,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=65.0, jaw_force=0.1,
    )),
    ("5.取螺丝（拧松）", LockCommand.SCREW_LOOSEN, dict(
        x_pos=1086, y_pos=430, z1_pos=1070, z2_pos=0,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=0.0, jaw_force=0.0,
    )),
    ("6.预锁（2.0圈/0.30N.m）", LockCommand.SCREW_PRE, dict(
        x_pos=1270, y_pos=2030, z1_pos=0, z2_pos=1065,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        pre_turns=20, pre_torque_limit=300,
    )),
    ("7.终锁（0.60N.m）", LockCommand.SCREW_FINAL, dict(
        x_pos=1270, y_pos=2030, z1_pos=0, z2_pos=1065,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        final_torque=600,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 Lock_CmdType 执行 opcua_gn1.4.0 指令。"
    "1=X左 2=X右 3=Y左 4=Y右 5=Z1左 6=Z1右 7=Z2左 8=Z2右 "
    "9=夹爪夹取 10=夹爪放置 11=电批终锁 12=电批拧松 13=夹爪夹紧 14=夹爪松开 "
    "15=电批预锁 16=复位。"
    "轴运动写对应 PosSet/Speed；夹取/放置写 jaw_position/jaw_force；"
    "预锁写 pre_turns/pre_torque_limit（勿写 final_torque）；终锁写 final_torque。"
    "CompleteFB=1 成功，=2 失败（见 Lock_ScrewResult）。"
)


@device(
    id="gn_locking_mechanism",
    display_name="锁紧模块",
    category=["workstation"],
    description="GN 锁紧模块：opcua_gn1.4.0，支持预锁/终锁与 CompleteFB 成败判定",
    icon="",
    version="2.2.0",
)
class LockingMechanismDevice(GNStationClient):
    """锁紧模块设备类（OPC 前缀 Lock_，通过 self.plc 共享 GN 工站单例 OPC UA 会话）。"""

    PREFIX = "Lock_"
    CMD_TYPE_NODE = "Lock_CmdType"
    CMD_TRIG_NODE = "Lock_CmdTrig"
    COMPLETE_NODE = "Lock_CompleteFB"
    SCREW_RESULT_NODE = "Lock_ScrewResult"
    TORQUE_FB_NODE = "Lock_TorqueFB"
    ANGLE_FB_NODE = "Lock_AngleFB"
    POSITION_NODES = {
        "Lock_XPosSet": "Lock_XPosFB",
        "Lock_YPosSet": "Lock_YPosFB",
        "Lock_Z1PosSet": "Lock_Z1PosFB",
        "Lock_Z2PosSet": "Lock_Z2PosFB",
    }
    # 轴/夹爪：CompleteFB 超时后可按位置反馈兜底；电批 11/12/15 禁止位置兜底
    _POSITION_COMMANDS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14})
    _SCREW_COMMANDS = frozenset({
        int(LockCommand.SCREW_FINAL),
        int(LockCommand.SCREW_LOOSEN),
        int(LockCommand.SCREW_PRE),
    })

    def __init__(
        self,
        url: str,
        csv_path: str = DEFAULT_CSV_PATH,
        xlsx_path: str = None,
        username: str = None,
        password: str = None,
        use_subscription: bool = True,
        cache_timeout: float = 5.0,
        subscription_interval: int = 500,
        *args,
        **kwargs,
    ):
        path = csv_path or xlsx_path or DEFAULT_CSV_PATH
        super().__init__(
            url=url,
            csv_path=path,
            username=username,
            password=password,
            use_subscription=use_subscription,
            cache_timeout=cache_timeout,
            subscription_interval=subscription_interval,
            *args,
            **kwargs,
        )
        # 确保 1.4.0 电批节点已注册（工站先加载旧 CSV 时仍可找到 PreTurns 等）
        self.register_node_list(list(_SCREW_OPC_NODES))
        self._command_lock = threading.Lock()
        self._fix_jaw_node_dtypes()

    @action(auto_prefix=True, description=_EXECUTE_CMD_DOC)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        jaw_position: Optional[float] = None,
        jaw_force: Optional[float] = None,
        pre_turns: Optional[int] = None,
        pre_torque_limit: Optional[int] = None,
        final_torque: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        """唯一注册动作：持锁间隔 → 清 CompleteFB → 写参 → CmdType → CmdTrig → 等 CompleteFB。"""
        cmd = int(cmd_type)
        if cmd not in LOCK_CMD_LABELS:
            raise ValueError(
                f"不支持的 Lock_CmdType={cmd}，有效: {sorted(LOCK_CMD_LABELS.keys())}"
            )
        if timeout is None or float(timeout) <= 0:
            timeout = 180.0
        self._validate_screw_params(
            cmd,
            pre_turns=pre_turns,
            pre_torque_limit=pre_torque_limit,
            final_torque=final_torque,
        )
        setpoints = self._build_setpoints(
            x_pos=x_pos, y_pos=y_pos, z1_pos=z1_pos, z2_pos=z2_pos,
            x_speed=x_speed, y_speed=y_speed, z1_speed=z1_speed, z2_speed=z2_speed,
            jaw_position=jaw_position, jaw_force=jaw_force,
            pre_turns=pre_turns, pre_torque_limit=pre_torque_limit,
            final_torque=final_torque,
            cmd_type=cmd,
        )
        return self._run(
            cmd_type=cmd,
            description=LOCK_CMD_LABELS[cmd],
            setpoints=setpoints,
            timeout=float(timeout),
            allow_position_fallback=cmd in self._POSITION_COMMANDS,
        )

    @staticmethod
    def _positive_int(value: Optional[int]) -> Optional[int]:
        """云端 goal_default=0 视为未设置。"""
        if value is None:
            return None
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return None
        return iv if iv > 0 else None

    @staticmethod
    def _optional_float(value: Optional[float]) -> Optional[float]:
        """夹爪等：None/0 视为未设置（云端默认 0）。"""
        if value is None:
            return None
        try:
            fv = float(value)
        except (TypeError, ValueError):
            return None
        return fv if fv != 0.0 else None

    @not_action
    def _validate_screw_params(
        self,
        cmd: int,
        *,
        pre_turns: Optional[int],
        pre_torque_limit: Optional[int],
        final_torque: Optional[int],
    ) -> None:
        pre_turns = self._positive_int(pre_turns)
        pre_torque_limit = self._positive_int(pre_torque_limit)
        final_torque = self._positive_int(final_torque)
        if cmd == int(LockCommand.SCREW_PRE):
            if pre_turns is None:
                raise ValueError("电批预锁(CmdType=15) 必须写 pre_turns>0（×0.1圈，如20=2.0圈）")
            if pre_torque_limit is None:
                raise ValueError(
                    "电批预锁(CmdType=15) 必须写 pre_torque_limit>0（×0.001N.m，如300=0.300N.m）"
                )
            # 协议：预锁不写 FinalTorqueSet；仅当同时传入终锁扭矩时才校验大小关系
            if final_torque is not None and pre_torque_limit >= final_torque:
                raise ValueError(
                    f"预锁扭矩上限({pre_torque_limit}) 必须 < 终锁扭矩({final_torque})"
                )
        if cmd == int(LockCommand.SCREW_FINAL):
            if final_torque is None:
                raise ValueError(
                    "电批终锁(CmdType=11) 必须写 final_torque>0（×0.001N.m，如600=0.600N.m）"
                )

    @not_action
    def _fix_jaw_node_dtypes(self) -> None:
        """夹爪位置/力度按 FLOAT 写入，避免 CSV DOUBLE 与 PLC REAL 类型不匹配。"""
        for name in _JAW_FLOAT_NODES:
            node = self._node_registry.get(name)
            if node is not None and hasattr(node, "_data_type"):
                if node._data_type != DataType.FLOAT:
                    logger.info(f"{name} 数据类型 {node._data_type} → FLOAT（对齐 PLC REAL）")
                    node._data_type = DataType.FLOAT

    @not_action
    def _build_setpoints(
        self,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        jaw_position: Optional[float] = None,
        jaw_force: Optional[float] = None,
        pre_turns: Optional[int] = None,
        pre_torque_limit: Optional[int] = None,
        final_torque: Optional[int] = None,
        cmd_type: Optional[int] = None,
    ) -> dict:
        # 预锁(15)按协议不写 FinalTorqueSet；终锁(11)不写预锁圈数/上限
        pre_turns_v = self._positive_int(pre_turns)
        pre_limit_v = self._positive_int(pre_torque_limit)
        final_v = self._positive_int(final_torque)
        if cmd_type == int(LockCommand.SCREW_PRE):
            final_v = None
        elif cmd_type == int(LockCommand.SCREW_FINAL):
            pre_turns_v = None
            pre_limit_v = None
        mapping = {
            "Lock_XPosSet": x_pos,
            "Lock_YPosSet": y_pos,
            "Lock_Z1PosSet": z1_pos,
            "Lock_Z2PosSet": z2_pos,
            "Lock_XSpeed": x_speed,
            "Lock_YSpeed": y_speed,
            "Lock_Z1Speed": z1_speed,
            "Lock_Z2Speed": z2_speed,
            "Lock_JawPosition": self._optional_float(jaw_position),
            "Lock_JawForce": self._optional_float(jaw_force),
            "Lock_PreTurnsSet": pre_turns_v,
            "Lock_PreTorqueLimit": pre_limit_v,
            "Lock_FinalTorqueSet": final_v,
        }
        return {node: val for node, val in mapping.items() if val is not None}

    @not_action
    def _run(
        self,
        cmd_type: int,
        description: str,
        setpoints: Optional[dict] = None,
        timeout: float = 180.0,
        allow_position_fallback: bool = False,
    ) -> dict:
        with self._command_lock:
            # 云端连续 job 时避免上一条未完成就写 Set / 清 CompleteFB
            time.sleep(5)
            if not self.set_node_value(self.COMPLETE_NODE, 0):
                logger.warning(
                    f"锁紧模块：{self.COMPLETE_NODE} 预清零失败（可能只读或链路异常），继续下发"
                )
            logger.info(f"锁紧模块：{description} (CmdType={cmd_type})")
            if setpoints:
                logger.info(f"锁紧模块 Set 参数：{setpoints}")
                for node, value in setpoints.items():
                    if not self.set_node_value(node, value):
                        raise ValueError(f"写入 {node}={value} 失败")
            return self._trigger_and_wait(
                cmd_type,
                description,
                setpoints=setpoints or {},
                allow_position_fallback=allow_position_fallback,
                timeout=timeout,
            )

    @not_action
    def _trigger_and_wait(
        self,
        cmd_type: int,
        description: str,
        setpoints: dict,
        allow_position_fallback: bool,
        timeout: float,
    ) -> dict:
        """下发 CmdType → CmdTrig=1，等待 CompleteFB=1 后清理（=2 判失败）。"""
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        if not self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type)):
            raise ValueError(f"Lock_CmdType={cmd_type} 写入失败")
        if not self.set_node_value(self.CMD_TRIG_NODE, 1):
            raise ValueError("Lock_CmdTrig=1 写入失败")

        completed = False
        try:
            ok, complete_fb = self._wait_complete_fb(
                timeout=timeout,
                description=f"{description}完成",
            )
            if ok:
                completed = True
            elif complete_fb == 2:
                raise ValueError(self._format_screw_failure(description))
            elif allow_position_fallback:
                targets = self._position_targets(setpoints)
                if targets and self._positions_reached(targets):
                    logger.warning(
                        f"{description}：CompleteFB 未回 1，但位置已到位，作超时兜底"
                    )
                    completed = True
            if not completed:
                raise ValueError(f"{description}失败，Lock_CompleteFB 未变为 1")
        finally:
            trigger_cleared = self.set_node_value(self.CMD_TRIG_NODE, 0)
            command_cleared = self.set_node_value(self.CMD_TYPE_NODE, 0)
            trigger_value = self.get_node_value(self.CMD_TRIG_NODE, force_read=True)
            command_value = self.get_node_value(self.CMD_TYPE_NODE, force_read=True)
            logger.info(
                f"锁紧模块命令清理：CmdTrig={trigger_value!r}，CmdType={command_value!r}"
            )
            if completed and (
                not trigger_cleared
                or not command_cleared
                or trigger_value != 0
                or command_value != 0
            ):
                raise ValueError(
                    "动作已完成，但命令清零失败："
                    f"Lock_CmdTrig={trigger_value!r}, Lock_CmdType={command_value!r}"
                )

        self._log_status(f"{description}后")
        result = {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": int(cmd_type),
        }
        if int(cmd_type) in self._SCREW_COMMANDS:
            result.update(self._read_screw_feedback())
        return result

    @not_action
    def _wait_complete_fb(
        self,
        timeout: float = 180.0,
        interval: float = 0.2,
        description: str = None,
    ) -> tuple:
        """等待 CompleteFB：1 成功；2 失败；超时返回 (False, 末次值)。"""
        desc = description or self.COMPLETE_NODE
        logger.info(f"等待 {desc}（节点: {self.COMPLETE_NODE}，1成功/2失败）...")
        start = time.monotonic()
        last = None
        while time.monotonic() - start < timeout:
            value = self.get_node_value(self.COMPLETE_NODE, force_read=True)
            last = value
            try:
                iv = int(value) if value is not None else 0
            except (TypeError, ValueError):
                iv = 0
            if iv == 1:
                logger.info(f"✓ {desc}（[{self.COMPLETE_NODE}]=1）")
                return True, 1
            if iv == 2:
                logger.error(f"✗ {desc}失败（[{self.COMPLETE_NODE}]=2）")
                return False, 2
            time.sleep(interval)
        logger.error(f"✗ 等待 {desc} 超时（{timeout}s，[{self.COMPLETE_NODE}]={last!r}）")
        return False, last

    @not_action
    def _read_screw_feedback(self) -> dict:
        out = {}
        for key, node in (
            ("screw_result", self.SCREW_RESULT_NODE),
            ("torque_fb", self.TORQUE_FB_NODE),
            ("angle_fb", self.ANGLE_FB_NODE),
        ):
            try:
                out[key] = self.get_node_value(node, force_read=True)
            except Exception:
                out[key] = None
        return out

    @not_action
    def _format_screw_failure(self, description: str) -> str:
        fb = self._read_screw_feedback()
        result = fb.get("screw_result")
        try:
            result_i = int(result) if result is not None else None
        except (TypeError, ValueError):
            result_i = None
        label = SCREW_RESULT_LABELS.get(result_i, "未知") if result_i is not None else "未知"
        return (
            f"{description}失败：Lock_CompleteFB=2，"
            f"Lock_ScrewResult={result!r}（{label}），"
            f"TorqueFB={fb.get('torque_fb')!r}，AngleFB={fb.get('angle_fb')!r}"
        )

    @not_action
    def _position_targets(self, setpoints: dict) -> dict:
        targets = {}
        for setpoint_node, feedback_node in self.POSITION_NODES.items():
            if setpoint_node not in setpoints:
                continue
            targets[feedback_node] = int(setpoints[setpoint_node])
        return targets

    @not_action
    def _positions_reached(
        self,
        position_targets: dict,
        tolerance: int = 5,
        stable_samples: int = 3,
        interval: float = 0.1,
        sample_timeout: float = 2.0,
    ) -> bool:
        if not position_targets:
            return False
        start = time.monotonic()
        stable_count = 0
        last_values = {}
        while time.monotonic() - start < sample_timeout:
            last_values = {
                node: self.get_node_value(node, force_read=True)
                for node in position_targets
            }
            all_reached = all(
                value is not None and abs(int(value) - target) <= tolerance
                for node, target in position_targets.items()
                for value in (last_values[node],)
            )
            stable_count = stable_count + 1 if all_reached else 0
            if stable_count >= stable_samples:
                logger.info(f"✓ 位置到位兜底：{last_values}")
                return True
            time.sleep(interval)
        logger.warning(f"位置兜底未满足，当前={last_values}，目标={position_targets}")
        return False

    @not_action
    def run_test_flow(self) -> dict:
        """按锁紧模块测试流程预设依次 execute_command（本地调试用）"""
        logger.info("锁紧模块：开始整体测试流程...")
        for step_name, cmd_type, preset in TEST_FLOW_PRESETS:
            logger.info(f"--- {step_name} (CmdType={int(cmd_type)}) ---")
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            self.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        logger.info("锁紧模块：整体测试流程完成")
        return {"success": True, "message": "锁紧模块测试流程完成"}

    @not_action
    def get_positions(self) -> dict:
        return {
            "X": self.get_node_value("Lock_XPosFB", force_read=True),
            "Y": self.get_node_value("Lock_YPosFB", force_read=True),
            "Z1": self.get_node_value("Lock_Z1PosFB", force_read=True),
            "Z2": self.get_node_value("Lock_Z2PosFB", force_read=True),
        }

    @not_action
    def get_status(self) -> dict:
        status = self.get_positions()
        status["complete"] = self.get_node_value(self.COMPLETE_NODE, force_read=True)
        status["jaw_position"] = self.get_node_value("Lock_JawPosition", force_read=True)
        status["jaw_force"] = self.get_node_value("Lock_JawForce", force_read=True)
        status.update(self._read_screw_feedback())
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: X={status['X']} Y={status['Y']} "
            f"Z1={status['Z1']} Z2={status['Z2']} "
            f"完成={status['complete']} "
            f"ScrewResult={status.get('screw_result')} "
            f"TorqueFB={status.get('torque_fb')} AngleFB={status.get('angle_fb')}"
        )


if __name__ == "__main__":
    logging.getLogger("unilabos").setLevel(logging.INFO)

    LOCKING_URL = "opc.tcp://192.168.6.6:4840"
    STATUS_LOG_INTERVAL = 15.0

    dev = LockingMechanismDevice(
        url=LOCKING_URL,
        csv_path=DEFAULT_CSV_PATH,
        use_subscription=False,
    )
    time.sleep(2)
    logger.info(f"锁紧模块连通性测试: {dev.get_status()}")

    status_log_running = True

    def _status_log_worker():
        while status_log_running:
            try:
                dev._log_status("实时位置")
            except Exception as e:
                logger.warning(f"状态反馈日志异常: {e}")
            time.sleep(STATUS_LOG_INTERVAL)

    threading.Thread(target=_status_log_worker, daemon=True, name="LockStatusLog").start()

    while True:
        print("请选择操作：")
        for idx, (name, cmd, _) in enumerate(TEST_FLOW_PRESETS, start=1):
            print(f"{idx} {name} (CmdType={int(cmd)})")
        print("16 复位 (CmdType=16)")
        print("98 整体测试流程")
        print("99 退出")
        choice = input("请输入操作序号：").strip()
        if choice == "99":
            break
        if choice == "98":
            dev.run_test_flow()
        elif choice == "16":
            dev.execute_command(cmd_type=16)
        elif choice.isdigit() and 1 <= int(choice) <= len(TEST_FLOW_PRESETS):
            name, cmd_type, preset = TEST_FLOW_PRESETS[int(choice) - 1]
            preset_args = dict(preset)
            step_timeout = preset_args.pop("timeout", 180.0)
            dev.execute_command(cmd_type=int(cmd_type), timeout=step_timeout, **preset_args)
        else:
            print("无效的操作序号，请重新输入。")

    status_log_running = False
    dev.disconnect()
    print("退出程序。")
