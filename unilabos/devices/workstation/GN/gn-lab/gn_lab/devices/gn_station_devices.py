"""Kernel-v2 设备包注册层。

真机驱动实现仍在 ``unilabos/devices/workstation/GN/``；本模块仅提供
``@device`` 装饰的薄子类，供工作区 ``external_only`` 模式下 AST 扫描与
``community.gn_lab.<id>`` 图引用。业务逻辑不变。
"""

from __future__ import annotations

from typing import Optional

from unilabos.registry.decorators import action, device

from unilabos.devices.workstation.GN.gn_centrifuge import CentrifugeDevice as _CentrifugeDevice
from unilabos.devices.workstation.GN.gn_centrifuge_tube_liquid_handling import (
    CentrifugeTubeLiquidHandlingDevice as _CentrifugeTubeLiquidHandlingDevice,
)
from unilabos.devices.workstation.GN.gn_locking_mechanism import (
    LockingMechanismDevice as _LockingMechanismDevice,
)
from unilabos.devices.workstation.GN.gn_quick_carrier_exchange import (
    QuickCarrierExchangeDevice as _QuickCarrierExchangeDevice,
)
from unilabos.devices.workstation.GN.gn_solid_weighing import SolidWeighingDevice as _SolidWeighingDevice
from unilabos.devices.workstation.GN.gn_stack import StackDevice as _StackDevice
from unilabos.devices.workstation.GN.gn_standard_oven import StandardOvenDevice as _StandardOvenDevice
from unilabos.devices.workstation.GN.gn_vacuum_oven import VacuumOvenDevice as _VacuumOvenDevice
from unilabos.devices.workstation.GN.gn_workstation import GNWorkstation as _GNWorkstation
from unilabos.devices.workstation.GN.robotic_arm import RoboticArmDevice as _RoboticArmDevice
from unilabos.devices.workstation.GN.rotary_stack import RotaryStackDevice as _RotaryStackDevice


@device(
    id="gn_workstation",
    displayname="GN 有机合成工作站",
    category=["workstation"],
    description=(
        "GN 有机合成工作站顶层设备：机械手 + 功能子设备 + PRCXI；"
        "GnPlcClient 单例共享 OPC UA 会话。"
    ),
    icon="gn_full_station_icon.webp",
    version="1.0.0",
)
class GNWorkstation(_GNWorkstation):
    """设备包注册：GN 工作站顶层。"""

    @action(parent=True)
    def get_plc_stats(self) -> dict:
        return super().get_plc_stats()

    @action(parent=True)
    def get_system_ready(self) -> dict:
        return super().get_system_ready()

    @action(parent=True)
    def system_reset(self, timeout: float = 180.0) -> dict:
        return super().system_reset(timeout=timeout)

    @action(parent=True)
    def system_stop(self) -> dict:
        return super().system_stop()

    @action(parent=True)
    def reconnect_plc(self) -> dict:
        return super().reconnect_plc()

    @action(parent=True)
    def release_plc(self) -> dict:
        return super().release_plc()


@device(
    id="gn_robotic_arm",
    displayname="机械手",
    category=["workstation"],
    description="GN 机械手：OPC UA，工站与子位置抓放。",
    version="5.1.0",
)
class RoboticArmDevice(_RoboticArmDevice):
    """设备包注册：机械手。"""

    @action(parent=True)
    def pick(
        self,
        station: str,
        item_type: str = "plate",
        number: int = 1,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        return super().pick(
            station=station,
            item_type=item_type,
            number=number,
            x_speed=x_speed,
            timeout=timeout,
        )

    @action(parent=True)
    def place(
        self,
        station: str,
        item_type: str = "plate",
        number: int = 1,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        return super().place(
            station=station,
            item_type=item_type,
            number=number,
            x_speed=x_speed,
            timeout=timeout,
        )

    @action(parent=True)
    def transfer(
        self,
        source: str,
        target: str,
        item_type: str = "plate",
        source_number: int = 1,
        target_number: int = 1,
        x_speed: int = 300,
        timeout: float = 180.0,
    ) -> dict:
        return super().transfer(
            source=source,
            target=target,
            item_type=item_type,
            source_number=source_number,
            target_number=target_number,
            x_speed=x_speed,
            timeout=timeout,
        )

    @action(parent=True)
    def enable(self, timeout: float = 60.0) -> dict:
        return super().enable(timeout=timeout)

    @action(parent=True)
    def disable(self, timeout: float = 60.0) -> dict:
        return super().disable(timeout=timeout)

    @action(parent=True)
    def reset(self, timeout: float = 120.0) -> dict:
        return super().reset(timeout=timeout)

    @action(parent=True)
    def go_home(self, timeout: float = 180.0) -> dict:
        return super().go_home(timeout=timeout)

    @action(parent=True)
    def stop(self) -> dict:
        return super().stop()

    @action(parent=True)
    def move_to_station(
        self,
        station: str,
        item_type: str = "plate",
        x_speed: int = 300,
        timeout: float = 120.0,
    ) -> dict:
        return super().move_to_station(
            station=station,
            item_type=item_type,
            x_speed=x_speed,
            timeout=timeout,
        )


@device(
    id="gn_centrifuge_tube_liquid_handling",
    displayname="离心管液体处理",
    category=["workstation"],
    description="GN 离心管液体处理：分样/移液/开关盖。",
)
class CentrifugeTubeLiquidHandlingDevice(_CentrifugeTubeLiquidHandlingDevice):
    """设备包注册：离心管液体处理。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        z1_pos: Optional[int] = None,
        p1_pos: Optional[int] = None,
        z2_pos: Optional[int] = None,
        p2_pos: Optional[int] = None,
        z3_pos: Optional[int] = None,
        z4_pos: Optional[int] = None,
        m_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        p1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        p2_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        z4_speed: Optional[int] = None,
        m_speed: Optional[int] = None,
        mix_counts: Optional[int] = None,
        ultrasound_time: Optional[int] = None,
        shaking_speed: Optional[int] = None,
        shaking_time: Optional[int] = None,
        small_gripper_angle: Optional[int] = None,
        small_gripper_force: Optional[int] = None,
        ch8_blow: Optional[int] = None,
        ch1_blow: Optional[int] = None,
        ch8_reverse_aspirate: Optional[int] = None,
        ch1_reverse_aspirate: Optional[int] = None,
        open_lid_start: Optional[int] = None,
        open_lid_end: Optional[int] = None,
        close_lid_start: Optional[int] = None,
        close_lid_end: Optional[int] = None,
        open_lid_speed: Optional[int] = None,
        close_lid_speed: Optional[int] = None,
        open_lid_rotation_speed: Optional[int] = None,
        close_lid_rotation_speed: Optional[int] = None,
        thermo1_temperature: Optional[int] = None,
        thermo1_time: Optional[int] = None,
        thermo2_temperature: Optional[int] = None,
        thermo2_time: Optional[int] = None,
        thermo3_temperature: Optional[int] = None,
        thermo3_time: Optional[int] = None,
        thermo4_temperature: Optional[int] = None,
        thermo4_time: Optional[int] = None,
        ultrasound_stop: bool = False,
        timeout: float = 180.0,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            x_pos=x_pos,
            y_pos=y_pos,
            z1_pos=z1_pos,
            p1_pos=p1_pos,
            z2_pos=z2_pos,
            p2_pos=p2_pos,
            z3_pos=z3_pos,
            z4_pos=z4_pos,
            m_pos=m_pos,
            x_speed=x_speed,
            y_speed=y_speed,
            z1_speed=z1_speed,
            p1_speed=p1_speed,
            z2_speed=z2_speed,
            p2_speed=p2_speed,
            z3_speed=z3_speed,
            z4_speed=z4_speed,
            m_speed=m_speed,
            mix_counts=mix_counts,
            ultrasound_time=ultrasound_time,
            shaking_speed=shaking_speed,
            shaking_time=shaking_time,
            small_gripper_angle=small_gripper_angle,
            small_gripper_force=small_gripper_force,
            ch8_blow=ch8_blow,
            ch1_blow=ch1_blow,
            ch8_reverse_aspirate=ch8_reverse_aspirate,
            ch1_reverse_aspirate=ch1_reverse_aspirate,
            open_lid_start=open_lid_start,
            open_lid_end=open_lid_end,
            close_lid_start=close_lid_start,
            close_lid_end=close_lid_end,
            open_lid_speed=open_lid_speed,
            close_lid_speed=close_lid_speed,
            open_lid_rotation_speed=open_lid_rotation_speed,
            close_lid_rotation_speed=close_lid_rotation_speed,
            thermo1_temperature=thermo1_temperature,
            thermo1_time=thermo1_time,
            thermo2_temperature=thermo2_temperature,
            thermo2_time=thermo2_time,
            thermo3_temperature=thermo3_temperature,
            thermo3_time=thermo3_time,
            thermo4_temperature=thermo4_temperature,
            thermo4_time=thermo4_time,
            ultrasound_stop=ultrasound_stop,
            timeout=timeout,
        )


@device(
    id="gn_quick_carrier_exchange",
    displayname="快换模块",
    category=["workstation"],
    description="GN 快换模块：OPC UA 控制。",
    version="2.0.0",
)
class QuickCarrierExchangeDevice(_QuickCarrierExchangeDevice):
    """设备包注册：快换。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        top_z_pos: Optional[int] = None,
        take_z_pos: Optional[int] = None,
        push_pos: Optional[int] = None,
        push_z_pos: Optional[int] = None,
        x_speed: Optional[int] = None,
        z1_speed: Optional[int] = None,
        z2_speed: Optional[int] = None,
        push_speed: Optional[int] = None,
        z3_speed: Optional[int] = None,
        stir_rpm: Optional[int] = None,
        stir_temp: Optional[int] = None,
        stir_time_minutes: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            x_pos=x_pos,
            top_z_pos=top_z_pos,
            take_z_pos=take_z_pos,
            push_pos=push_pos,
            push_z_pos=push_z_pos,
            x_speed=x_speed,
            z1_speed=z1_speed,
            z2_speed=z2_speed,
            push_speed=push_speed,
            z3_speed=z3_speed,
            stir_rpm=stir_rpm,
            stir_temp=stir_temp,
            stir_time_minutes=stir_time_minutes,
            timeout=timeout,
        )


@device(
    id="gn_solid_weighing",
    displayname="固体加样",
    category=["workstation"],
    description="GN 固体加样。",
    version="2.0.0",
)
class SolidWeighingDevice(_SolidWeighingDevice):
    """设备包注册：固体加样。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        x_pos: Optional[int] = None,
        y_pos: Optional[int] = None,
        material_z_pos: Optional[int] = None,
        gripper_z_pos: Optional[int] = None,
        door_pos: Optional[int] = None,
        volune_weight: Optional[int] = None,
        x_speed: Optional[int] = None,
        y_speed: Optional[int] = None,
        material_z_speed: Optional[int] = None,
        gripper_z_speed: Optional[int] = None,
        door_speed: Optional[int] = None,
        remove_static: int = 0,
        timeout: float = 180.0,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            x_pos=x_pos,
            y_pos=y_pos,
            material_z_pos=material_z_pos,
            gripper_z_pos=gripper_z_pos,
            door_pos=door_pos,
            volune_weight=volune_weight,
            x_speed=x_speed,
            y_speed=y_speed,
            material_z_speed=material_z_speed,
            gripper_z_speed=gripper_z_speed,
            door_speed=door_speed,
            remove_static=remove_static,
            timeout=timeout,
        )


@device(
    id="gn_rotary_stack",
    displayname="旋转堆栈",
    category=["workstation"],
    description="GN 旋转堆栈：绝对列定位（复位后列 N=(N-1)*300）。",
    version="3.1.0",
)
class RotaryStackDevice(_RotaryStackDevice):
    """设备包注册：旋转堆栈（拓扑 id gn_rotary_stack）。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        r_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        r_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
        timeout: float = 120.0,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            r_pos=r_pos,
            z_pos=z_pos,
            r_speed=r_speed,
            z_speed=z_speed,
            timeout=timeout,
        )


@device(
    id="gn_stack",
    displayname="旋转堆栈（legacy id）",
    category=["workstation"],
    description="与 gn_rotary_stack 同实现；部分工作流 JSON 仍引用 gn_stack。",
    version="2.0.0",
)
class StackDevice(_StackDevice):
    """设备包注册：历史 id gn_stack。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        r_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        r_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
        timeout: float = 180.0,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            r_pos=r_pos,
            z_pos=z_pos,
            r_speed=r_speed,
            z_speed=z_speed,
            timeout=timeout,
        )


@device(
    id="gn_centrifuge",
    displayname="离心机",
    category=["workstation"],
    description="GN 离心机：OPC UA execute_command。",
    version="2.0.0",
)
class CentrifugeDevice(_CentrifugeDevice):
    """设备包注册：离心机。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        y_pos: Optional[int] = None,
        z_pos: Optional[int] = None,
        inner_z_pos: Optional[int] = None,
        rpm: Optional[int] = None,
        time_minutes: Optional[int] = None,
        y_speed: Optional[int] = None,
        z_speed: Optional[int] = None,
        plate_no: Optional[int] = None,
        temperature: Optional[int] = None,
        timeout: float = 120.0,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            y_pos=y_pos,
            z_pos=z_pos,
            inner_z_pos=inner_z_pos,
            rpm=rpm,
            time_minutes=time_minutes,
            y_speed=y_speed,
            z_speed=z_speed,
            plate_no=plate_no,
            temperature=temperature,
            timeout=timeout,
        )


@device(
    id="gn_standard_oven",
    displayname="常规烘箱",
    category=["workstation"],
    description="GN 常规烘箱。",
    version="2.0.0",
)
class StandardOvenDevice(_StandardOvenDevice):
    """设备包注册：常规烘箱。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        temperature: Optional[int] = None,
        hours: int = 0,
        minutes: int = 0,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            temperature=temperature,
            hours=hours,
            minutes=minutes,
            wait=wait,
            timeout=timeout,
        )

    @action(parent=True, auto_prefix=True)
    def open_door(self) -> dict:
        return super().open_door()

    @action(parent=True, auto_prefix=True)
    def close_door(self) -> dict:
        return super().close_door()


@device(
    id="gn_vacuum_oven",
    displayname="真空烘箱",
    category=["workstation"],
    description="GN 真空烘箱。",
    version="2.0.0",
)
class VacuumOvenDevice(_VacuumOvenDevice):
    """设备包注册：真空烘箱。"""

    @action(parent=True, auto_prefix=False)
    def execute_command(
        self,
        cmd_type: int,
        temperature_1: Optional[int] = None,
        minutes_1: Optional[int] = None,
        vacuum_high_1: Optional[int] = None,
        vacuum_low_1: Optional[int] = None,
        temperature_2: Optional[int] = None,
        minutes_2: Optional[int] = None,
        vacuum_high_2: Optional[int] = None,
        vacuum_low_2: Optional[int] = None,
        temperature_3: Optional[int] = None,
        minutes_3: Optional[int] = None,
        vacuum_high_3: Optional[int] = None,
        vacuum_low_3: Optional[int] = None,
        temperature_4: Optional[int] = None,
        minutes_4: Optional[int] = None,
        vacuum_high_4: Optional[int] = None,
        vacuum_low_4: Optional[int] = None,
        temperature_5: Optional[int] = None,
        minutes_5: Optional[int] = None,
        vacuum_high_5: Optional[int] = None,
        vacuum_low_5: Optional[int] = None,
        temperature_6: Optional[int] = None,
        minutes_6: Optional[int] = None,
        vacuum_high_6: Optional[int] = None,
        vacuum_low_6: Optional[int] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
        segment: Optional[int] = None,
        temperature: Optional[int] = None,
        minutes: Optional[int] = None,
        vacuum_high: Optional[int] = None,
        vacuum_low: Optional[int] = None,
    ) -> dict:
        return super().execute_command(
            cmd_type=cmd_type,
            temperature_1=temperature_1,
            minutes_1=minutes_1,
            vacuum_high_1=vacuum_high_1,
            vacuum_low_1=vacuum_low_1,
            temperature_2=temperature_2,
            minutes_2=minutes_2,
            vacuum_high_2=vacuum_high_2,
            vacuum_low_2=vacuum_low_2,
            temperature_3=temperature_3,
            minutes_3=minutes_3,
            vacuum_high_3=vacuum_high_3,
            vacuum_low_3=vacuum_low_3,
            temperature_4=temperature_4,
            minutes_4=minutes_4,
            vacuum_high_4=vacuum_high_4,
            vacuum_low_4=vacuum_low_4,
            temperature_5=temperature_5,
            minutes_5=minutes_5,
            vacuum_high_5=vacuum_high_5,
            vacuum_low_5=vacuum_low_5,
            temperature_6=temperature_6,
            minutes_6=minutes_6,
            vacuum_high_6=vacuum_high_6,
            vacuum_low_6=vacuum_low_6,
            wait=wait,
            timeout=timeout,
            segment=segment,
            temperature=temperature,
            minutes=minutes,
            vacuum_high=vacuum_high,
            vacuum_low=vacuum_low,
        )


@device(
    id="gn_locking_mechanism",
    displayname="锁紧模块",
    category=["workstation"],
    description="GN 锁紧模块。",
    version="2.0.0",
)
class LockingMechanismDevice(_LockingMechanismDevice):
    """设备包注册：锁紧（未在默认拓扑 children 中时可单独挂载）。"""

    @action(parent=True, auto_prefix=False)
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
        return super().execute_command(
            cmd_type=cmd_type,
            x_pos=x_pos,
            y_pos=y_pos,
            z1_pos=z1_pos,
            z2_pos=z2_pos,
            x_speed=x_speed,
            y_speed=y_speed,
            z1_speed=z1_speed,
            z2_speed=z2_speed,
            jaw_position=jaw_position,
            jaw_force=jaw_force,
            timeout=timeout,
        )
