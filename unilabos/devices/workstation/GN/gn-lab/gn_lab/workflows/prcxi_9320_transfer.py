"""从 legacy JSON 导入：9320移液.json。"""

from unilabos.workflow.authoring import device, workflow

from gn_lab.devices.gn_prcxi import GN_PRCXI9300Handler

prcxi: GN_PRCXI9300Handler = device("PRCXI")

@workflow(
    workflow_uuid="8a15d439-59ab-43a3-928a-1aa8350d260f",
    displayname="9320移液",
    description="由 9320移液.json 转换，节点身份保持不变。",
)
def prcxi_9320_transfer() -> None:
    # unilab:node_uuid=def08fdb-60c1-48b6-9d27-dfb7910753b5
    step_001 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=f9591274-a2dc-4800-898d-308f904298f0
    step_002 = prcxi.transfer_liquid(delays=None, spread="wide", mix_vol=None, offsets=None, sources=[{"uuid": "bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf"}], targets=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], asp_vols=[300], dis_vols=[300], mix_rate=None, mix_stage="none", mix_times=None, none_keys=[], tip_racks=[{"uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}], touch_tip=False, is_96_well=True, source_slots=7, target_slots=16, tip_rack_slots=15, use_channels=[], liquid_height=None, asp_flow_rates=[300], dis_flow_rates=[300], post_air_volume=None, dispensing_method=None, mix_liquid_height=None, blow_out_air_volume=[20], hover_below_liquid_level=None, blow_out_air_volume_before=[], z_start_point_offset_height=None, post_discharge_pause_time_ms=None)
    # unilab:node_uuid=a87f66c1-c915-4481-93bb-9cf288caec84
    step_003 = prcxi.run_protocol(protocol_id=None)
