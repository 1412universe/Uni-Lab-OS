"""PRCXI 注册层（独立模块，避免与 OPC 子设备一并 import 时拖慢启动）。"""

from __future__ import annotations

from typing import Optional

from unilabos.registry.annotations import JSONValue
from unilabos.registry.decorators import action, device
from unilabos.registry.placeholder_type import ResourceSlot

from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi import (
    PRCXI9300Handler as _PRCXI9300Handler,
)


@device(
    id="gn_prcxi",
    displayname="PRCXI 9320 液体处理器",
    category=["liquid_handling"],
    description="GN 现场 9320（PRCXI9300Handler，驱动在 liquid_handling/prcxi/prcxi.py，TCP 协议口由启动图 host/port 指定）。",
    version="1.0.0",
)
class GN_PRCXI9300Handler(_PRCXI9300Handler):
    """设备包注册层：真机逻辑全部在 ``liquid_handling/prcxi/prcxi.py`` 的 PRCXI9300Handler。

    图节点 id 仍为 PRCXI；class 用 community.gn_lab.gn_prcxi。此处只暴露 AST 动作，
    不改 9320 通信、建方案、移液步骤。
    """

    @action(parent=True)
    async def move_plate(
        self,
        plate: Optional[list[ResourceSlot]] = None,
        to: int = 0,
        from_: Optional[int] = None,
        intermediate_locations: Optional[list[dict[str, JSONValue]]] = None,
        pickup_offset: Optional[dict[str, JSONValue]] = None,
        destination_offset: Optional[dict[str, JSONValue]] = None,
        drop_direction: Optional[str] = None,
        pickup_direction: Optional[str] = None,
        pickup_distance_from_top: float = 0.0,
        hierarchy: int = 1,
        pinch_it_up_position: int = 1,
        put_down_position: int = 1,
        force: int = 5,
        get_direction: Optional[str] = None,
        put_direction: Optional[str] = None,
        resource_offset: Optional[dict[str, JSONValue]] = None,
    ) -> dict[str, JSONValue]:
        return await super().move_plate(
            plate=plate,
            to=to,
            from_=from_,
            intermediate_locations=intermediate_locations,
            pickup_offset=pickup_offset,
            destination_offset=destination_offset,
            drop_direction=drop_direction,
            pickup_direction=pickup_direction,
            pickup_distance_from_top=pickup_distance_from_top,
            hierarchy=hierarchy,
            pinch_it_up_position=pinch_it_up_position,
            put_down_position=put_down_position,
            force=force,
            get_direction=get_direction,
            put_direction=put_direction,
            resource_offset=resource_offset,
        )

    @action(parent=True)
    async def transfer_liquid(
        self,
        sources: Optional[list[ResourceSlot]] = None,
        targets: Optional[list[ResourceSlot]] = None,
        tip_racks: Optional[list[ResourceSlot]] = None,
        use_channels: Optional[list[int]] = None,
        asp_vols: Optional[list[float]] = None,
        dis_vols: Optional[list[float]] = None,
        asp_flow_rates: Optional[list[float]] = None,
        dis_flow_rates: Optional[list[float]] = None,
        offsets: Optional[list[dict[str, JSONValue]]] = None,
        touch_tip: bool = False,
        liquid_height: Optional[list[float]] = None,
        blow_out_air_volume: Optional[list[float]] = None,
        blow_out_air_volume_before: Optional[list[float]] = None,
        post_air_volume: Optional[list[float]] = None,
        spread: str = "wide",
        is_96_well: bool = False,
        mix_stage: str = "none",
        mix_times: Optional[list[int]] = None,
        mix_vol: Optional[int] = None,
        mix_rate: Optional[int] = None,
        mix_liquid_height: Optional[float] = None,
        delays: Optional[list[int]] = None,
        pre_aspirate_from_target: Optional[float] = None,
        dispensing_method: Optional[list[str]] = None,
        hover_below_liquid_level: Optional[list[int]] = None,
        z_start_point_offset_height: Optional[list[int]] = None,
        post_discharge_pause_time_ms: Optional[list[int]] = None,
        none_keys: Optional[list[str]] = None,
        source_slots: Optional[int] = None,
        target_slots: Optional[int] = None,
        tip_rack_slots: Optional[int] = None,
    ) -> dict[str, JSONValue]:
        return await super().transfer_liquid(
            sources=sources,
            targets=targets,
            tip_racks=tip_racks,
            use_channels=use_channels,
            asp_vols=asp_vols,
            dis_vols=dis_vols,
            asp_flow_rates=asp_flow_rates,
            dis_flow_rates=dis_flow_rates,
            offsets=offsets,
            touch_tip=touch_tip,
            liquid_height=liquid_height,
            blow_out_air_volume=blow_out_air_volume,
            blow_out_air_volume_before=blow_out_air_volume_before,
            post_air_volume=post_air_volume,
            spread=spread,
            is_96_well=is_96_well,
            mix_stage=mix_stage,
            mix_times=mix_times,
            mix_vol=mix_vol,
            mix_rate=mix_rate,
            mix_liquid_height=mix_liquid_height,
            delays=delays,
            pre_aspirate_from_target=pre_aspirate_from_target,
            dispensing_method=dispensing_method,
            hover_below_liquid_level=hover_below_liquid_level,
            z_start_point_offset_height=z_start_point_offset_height,
            post_discharge_pause_time_ms=post_discharge_pause_time_ms,
            none_keys=none_keys or [],
            source_slots=source_slots,
            target_slots=target_slots,
            tip_rack_slots=tip_rack_slots,
        )

    @action(parent=True)
    async def titration_liquid(
        self,
        sources: Optional[list[ResourceSlot]] = None,
        targets: Optional[list[ResourceSlot]] = None,
        tip_racks: Optional[list[ResourceSlot]] = None,
        use_channels: Optional[list[int]] = None,
        asp_vols: Optional[list[float]] = None,
        dis_vols: Optional[list[float]] = None,
        asp_flow_rates: Optional[list[float]] = None,
        dis_flow_rates: Optional[list[float]] = None,
        offsets: Optional[list[dict[str, JSONValue]]] = None,
        liquid_height: Optional[list[float]] = None,
        blow_out_air_volume: Optional[list[float]] = None,
        blow_out_air_volume_before: Optional[list[float]] = None,
        post_air_volume: Optional[list[float]] = None,
        repeat_count: int = 1,
        cycle_delay_s: float = 0.0,
        is_96_well: bool = True,
        dispensing_method: Optional[list[str]] = None,
        hover_below_liquid_level: Optional[list[int]] = None,
        z_start_point_offset_height: Optional[list[int]] = None,
        post_discharge_pause_time_ms: Optional[list[int]] = None,
        none_keys: Optional[list[str]] = None,
        source_slots: Optional[int] = None,
        target_slots: Optional[int] = None,
        tip_rack_slots: Optional[int] = None,
    ) -> dict[str, JSONValue]:
        return await super().titration_liquid(
            sources=sources,
            targets=targets,
            tip_racks=tip_racks,
            use_channels=use_channels,
            asp_vols=asp_vols,
            dis_vols=dis_vols,
            asp_flow_rates=asp_flow_rates,
            dis_flow_rates=dis_flow_rates,
            offsets=offsets,
            liquid_height=liquid_height,
            blow_out_air_volume=blow_out_air_volume,
            blow_out_air_volume_before=blow_out_air_volume_before,
            post_air_volume=post_air_volume,
            repeat_count=repeat_count,
            cycle_delay_s=cycle_delay_s,
            is_96_well=is_96_well,
            dispensing_method=dispensing_method,
            hover_below_liquid_level=hover_below_liquid_level,
            z_start_point_offset_height=z_start_point_offset_height,
            post_discharge_pause_time_ms=post_discharge_pause_time_ms,
            none_keys=none_keys or [],
            source_slots=source_slots,
            target_slots=target_slots,
            tip_rack_slots=tip_rack_slots,
        )

    @action(parent=True)
    async def create_protocol(
        self,
        protocol_name: str = "",
        protocol_description: str = "",
        protocol_version: str = "",
        protocol_author: str = "",
        protocol_date: str = "",
        protocol_type: str = "",
        none_keys: Optional[list[str]] = None,
    ) -> dict[str, JSONValue]:
        await super().create_protocol(
            protocol_name=protocol_name,
            protocol_description=protocol_description,
            protocol_version=protocol_version,
            protocol_author=protocol_author,
            protocol_date=protocol_date,
            protocol_type=protocol_type,
            none_keys=none_keys or [],
        )
        return {"success": True}

    @action(parent=True)
    async def run_protocol(self, protocol_id: Optional[str] = None) -> dict[str, JSONValue]:
        return await super().run_protocol(protocol_id=protocol_id)

    @action(parent=True, auto_prefix=False)
    async def shaking_incubation_action(
        self,
        time: int,
        module_no: int,
        amplitude: int,
        is_wait: bool,
        temperature: int,
    ) -> dict[str, JSONValue]:
        return await super().shaking_incubation_action(
            time=time,
            module_no=module_no,
            amplitude=amplitude,
            is_wait=is_wait,
            temperature=temperature,
        )
