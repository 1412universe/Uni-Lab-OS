"""
锁紧模块设备驱动。

协议：opcua_gn1.3.9.csv「锁紧模块」（前缀 Lock_）；握手与 gn_quick_carrier_exchange 一致：
    清 CompleteFB → 写参 → Lock_CmdType → Lock_CmdTrig=1 → 等 Lock_CompleteFB=1 → 清零。

对外仅暴露 execute_command（Lock_CmdType + 写参）；测试流程预设供本地调试。

指令类型 (Lock_CmdType)：
    1=X向左 2=X向右 3=Y向左 4=Y向右
    5=Z1向左 6=Z1向右 7=Z2向左 8=Z2向右
    9=夹爪夹取 10=夹爪放置 11=电批拧紧 12=电批拧松
    13=夹爪夹紧 14=夹爪松开 16=复位
"""

import os
import time
import logging
import threading
from enum import Enum
from typing import Optional

from unilabos.utils.log import logger
from unilabos.registry.decorators import action, device, not_action
from unilabos.device_comms.opcua_client.node.uniopcua import DataType
from unilabos.devices.workstation.GN.gn_station_base import GNStationClient

# CSV 写 DOUBLE，汇川 OPC 实际多为 FLOAT(REAL)；按 Double 写会 BadTypeMismatch
_JAW_FLOAT_NODES = ("Lock_JawPosition", "Lock_JawForce")

DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "opcua_gn1.3.9.csv",
)
# 注册表 init 仍引用旧名 xlsx_path，与 csv_path 同义
DEFAULT_XLSX_PATH = DEFAULT_CSV_PATH

# Lock_CmdType（opcua_gn1.3.7+ / 1.3.9 一致）
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
    11: "电批拧紧",
    12: "电批拧松",
    13: "夹爪夹紧",
    14: "夹爪松开",
    16: "复位",
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
    SCREW_TIGHTEN = 11
    SCREW_LOOSEN = 12
    JAW_CLAMP = 13
    JAW_RELEASE = 14
    RESET = 16


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
    ("6.拧螺丝（拧紧）", LockCommand.SCREW_TIGHTEN, dict(
        x_pos=1270, y_pos=2030, z1_pos=1065, z2_pos=0,
        x_speed=500, y_speed=500, z1_speed=500, z2_speed=500,
        jaw_position=0.0, jaw_force=0.0,
    )),
]


_EXECUTE_CMD_DOC = (
    "按 Lock_CmdType 执行 opcua_gn1.3.9 指令。"
    "1=X左 2=X右 3=Y左 4=Y右 5=Z1左 6=Z1右 7=Z2左 8=Z2右 "
    "9=夹爪夹取 10=夹爪放置 11=电批拧紧 12=电批拧松 13=夹爪夹紧 14=夹爪松开 16=复位。"
    "轴运动写对应 PosSet/Speed；夹取/放置/电批另写 jaw_position/jaw_force。"
)


@device(
    id="gn_locking_mechanism",
    display_name="锁紧模块",
    category=["workstation"],
    description="GN 锁紧模块：opcua_gn1.3.9，按完成反馈边沿执行命令",
    icon="",
    version="2.0.0",
)
class LockingMechanismDevice(GNStationClient):
    """锁紧模块设备类（OPC 前缀 Lock_，通过 self.plc 共享 GN 工站单例 OPC UA 会话）。"""

    PREFIX = "Lock_"
    CMD_TYPE_NODE = "Lock_CmdType"
    CMD_TRIG_NODE = "Lock_CmdTrig"
    COMPLETE_NODE = "Lock_CompleteFB"
    POSITION_NODES = {
        "Lock_XPosSet": "Lock_XPosFB",
        "Lock_YPosSet": "Lock_YPosFB",
        "Lock_Z1PosSet": "Lock_Z1PosFB",
        "Lock_Z2PosSet": "Lock_Z2PosFB",
    }
    # 运动/夹取/电批类：CompleteFB 超时后可按位置反馈兜底
    _POSITION_COMMANDS = frozenset(range(1, 13))

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
        setpoints = self._build_setpoints(
            x_pos=x_pos, y_pos=y_pos, z1_pos=z1_pos, z2_pos=z2_pos,
            x_speed=x_speed, y_speed=y_speed, z1_speed=z1_speed, z2_speed=z2_speed,
            jaw_position=jaw_position, jaw_force=jaw_force,
        )
        return self._run(
            cmd_type=cmd,
            description=LOCK_CMD_LABELS[cmd],
            setpoints=setpoints,
            timeout=float(timeout),
            allow_position_fallback=cmd in self._POSITION_COMMANDS,
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
    ) -> dict:
        mapping = {
            "Lock_XPosSet": x_pos,
            "Lock_YPosSet": y_pos,
            "Lock_Z1PosSet": z1_pos,
            "Lock_Z2PosSet": z2_pos,
            "Lock_XSpeed": x_speed,
            "Lock_YSpeed": y_speed,
            "Lock_Z1Speed": z1_speed,
            "Lock_Z2Speed": z2_speed,
            # 显式 float，配合节点 DataType.FLOAT
            "Lock_JawPosition": float(jaw_position) if jaw_position is not None else None,
            "Lock_JawForce": float(jaw_force) if jaw_force is not None else None,
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
        """下发 CmdType → CmdTrig=1，等待 CompleteFB=1 后清理。"""
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        self.set_node_value(self.CMD_TRIG_NODE, 0)
        if not self.set_node_value(self.CMD_TYPE_NODE, int(cmd_type)):
            raise ValueError(f"Lock_CmdType={cmd_type} 写入失败")
        if not self.set_node_value(self.CMD_TRIG_NODE, 1):
            raise ValueError("Lock_CmdTrig=1 写入失败")

        completed = False
        try:
            completed = self._wait_until_true(
                self.COMPLETE_NODE,
                timeout=timeout,
                description=f"{description}完成",
            )
            if not completed and allow_position_fallback:
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
        return {
            "success": True,
            "message": f"{description}完成",
            "cmd_type": int(cmd_type),
        }

    @not_action
    def _wait_until_true(
        self,
        node_name: str,
        timeout: float = 180.0,
        interval: float = 0.2,
        description: str = None,
    ) -> bool:
        desc = description or node_name
        logger.info(f"等待 {desc}（节点: {node_name}）...")
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            value = self.get_node_value(node_name, force_read=True)
            if value:
                logger.info(f"✓ {desc}（[{node_name}]={value}）")
                return True
            time.sleep(interval)
        value = self.get_node_value(node_name, force_read=True)
        logger.error(f"✗ 等待 {desc} 超时（{timeout}s，[{node_name}]={value!r}）")
        return False

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
        return status

    @not_action
    def _log_status(self, prefix: str = "状态反馈") -> None:
        status = self.get_status()
        logger.info(
            f"{prefix}: X={status['X']} Y={status['Y']} "
            f"Z1={status['Z1']} Z2={status['Z2']} "
            f"完成={status['complete']}"
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
