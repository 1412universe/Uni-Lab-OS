"""从 legacy JSON 导入：金四面体AA溶液制备与溶液的混合，并放到T16等待离心.json。"""

from unilabos.workflow.authoring import device, workflow

from gn_lab.devices.gn_prcxi import GN_PRCXI9300Handler
from gn_lab.devices.gn_station_devices import RoboticArmDevice, RotaryStackDevice, SolidWeighingDevice

robot: RoboticArmDevice = device("gn_robotic_arm")
prcxi: GN_PRCXI9300Handler = device("PRCXI")
stack: RotaryStackDevice = device("gn_rotary_stack")
solid: SolidWeighingDevice = device("gn_solid_weighing")

@workflow(
    workflow_uuid="4b868761-9468-46ec-86c5-7ab64b6b8854",
    displayname="金四面体AA溶液制备与溶液的混合，并放到T16等待离心",
    description="由 金四面体AA溶液制备与溶液的混合，并放到T16等待离心.json 转换，节点身份保持不变。",
)
def gold_tetrahedron_aa_mix() -> None:
    # Step7a: 堆栈62号枪头盒 -> T15 (pick)
    # unilab:node_uuid=473378df-cb8e-4dd6-a3cc-2271cf529a06
    step_001 = robot.pick(number=62, station="stack", timeout=30000, x_speed=400, item_type="plate")
    # Step7a: 堆栈62号枪头盒 -> T15 (place 9320)
    # unilab:node_uuid=055d4b6a-0b26-4c1f-9ffb-2a16e1fcef87
    step_002 = robot.place(number=1, station="nine9320", timeout=30000, x_speed=400, item_type="plate")
    # Step7a: 堆栈62号枪头盒 -> T15 移液
    # unilab:node_uuid=9ee36691-7e18-402f-9342-6d7e72ee2d1e
    step_003 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # Step7a: 堆栈62号枪头盒 -> T15move T16->T15
    # unilab:node_uuid=917b5a0b-3b9b-4a91-8080-73e3f90d40e6
    step_004 = prcxi.move_plate(to=15, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # 执行 Step7a: 堆栈62号枪头盒 -> T15
    # unilab:node_uuid=e595065d-27ad-4b90-a2b3-c05ad5541220
    step_005 = prcxi.run_protocol(protocol_id=None)
    # Step7c: 堆栈6 96孔板
    # unilab:node_uuid=4729a791-31d6-454d-ae56-db85cb1ab432
    step_006 = robot.pick(number=6, station="stack", timeout=30000, x_speed=400, item_type="plate")
    # Step7c: 放到9320
    # unilab:node_uuid=76d98eb7-04ca-49c1-872b-9b88a398fc79
    step_007 = robot.place(number=1, station="nine9320", timeout=30000, x_speed=400, item_type="plate")
    # Step7a: T16->T10 混合溶液最后放置处
    # unilab:node_uuid=4aa3d6f2-7718-4c58-8b92-94ab9a4b60b4
    step_008 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # T16->T10
    # unilab:node_uuid=a27bd28c-d29d-4f3a-8db8-45daa9f38bec
    step_009 = prcxi.move_plate(to=10, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=1, intermediate_locations=[], pickup_distance_from_top=0)
    # Step11: T14 CTAB与CTAC混合溶液 780ul->T10，枪头T15
    # unilab:node_uuid=8af7f34f-65d2-429f-a618-69f3c529f8b1
    step_010 = prcxi.transfer_liquid(delays=[], spread="wide", offsets=[], sources=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], targets=[{"uuid": "8e1cb526-32c4-4c6c-bb22-6faa81e209c5"}], asp_vols=[780], dis_vols=[780], mix_stage="none", none_keys=[], tip_racks=[{"uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}], touch_tip=False, is_96_well=True, source_slots=14, target_slots=10, use_channels=[], liquid_height=[], asp_flow_rates=[100], dis_flow_rates=[100], tip_rack_slots=11, post_air_volume=[], dispensing_method=[], blow_out_air_volume=[20], hover_below_liquid_level=[], blow_out_air_volume_before=[], z_start_point_offset_height=[], post_discharge_pause_time_ms=[])
    # unilab:node_uuid=35195ba9-ebce-42d2-ad24-a398bb5b5694
    step_011 = prcxi.run_protocol(protocol_id=None)
    # 堆栈复位
    # unilab:node_uuid=f9642dff-d008-4c09-af03-a5784d59e99f
    step_012 = stack.execute_command(r_pos=0, z_pos=0, r_speed=0, timeout=120, z_speed=0, cmd_type=7)
    # Step8: 堆栈2号位取板至固体加样(AA)
    # unilab:node_uuid=c3f8d295-5aef-4358-9c94-369ef72c57a1
    step_013 = robot.pick(number=2, station="stack", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-放入固体加样仪(96孔板1号板位)
    # unilab:node_uuid=c1dba55a-d3e2-4ee4-baeb-2afda5ab01eb
    step_014 = robot.place(number=1, station="solid", timeout=30000, x_speed=400, item_type="plate")
    # 夹取料架
    # unilab:node_uuid=20d72762-eb87-49f2-8f4f-83ac1ce379ad
    step_015 = solid.execute_command(x_pos=-80, y_pos=2100, timeout=180, x_speed=300, y_speed=300, cmd_type=12, door_pos=0, door_speed=0, gripper_z_pos=2050, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 放置加样工位
    # unilab:node_uuid=ca356893-e77c-4828-b157-ec4f8a711380
    step_016 = solid.execute_command(x_pos=-1840, y_pos=1790, timeout=180, x_speed=300, y_speed=300, cmd_type=13, door_pos=0, door_speed=0, gripper_z_pos=930, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # Step8-R1: 取3号桶AA
    # unilab:node_uuid=bac3ceec-03a8-4f87-b3c5-5f70019c4378
    step_017 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R1: 加AA 211.34mg @X-625
    # unilab:node_uuid=ac64c9fa-8e2a-4efb-9f22-895c650e4ea1
    step_018 = solid.execute_command(x_pos=-634, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R1: 放回3号桶AA
    # unilab:node_uuid=e80b7aa3-dd29-4186-b4b8-058f45db8999
    step_019 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R2: 取3号桶AA
    # unilab:node_uuid=b0e58a9d-5074-481c-85a8-41ca9f975a19
    step_020 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R2: 加AA 211.34mg @X-616
    # unilab:node_uuid=9364b1df-a933-411e-981b-9c9641b4e864
    step_021 = solid.execute_command(x_pos=-544, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R2: 放回3号桶AA
    # unilab:node_uuid=6f5007a7-af57-4b21-a047-0dea37f60547
    step_022 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R3: 取3号桶AA
    # unilab:node_uuid=5ea9a4b4-d539-4616-b345-d18f46615e6b
    step_023 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R3: 加AA 211.34mg @X-607
    # unilab:node_uuid=29386e46-1efa-48e7-b659-1ed045996ad0
    step_024 = solid.execute_command(x_pos=-454, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R3: 放回3号桶AA
    # unilab:node_uuid=2b9e5142-0f7c-46fa-b811-8428c3152ed3
    step_025 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R4: 取3号桶AA
    # unilab:node_uuid=608fa595-a942-429b-b78d-30f436795070
    step_026 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R4: 加AA 211.34mg @X-598
    # unilab:node_uuid=51529e03-f4f2-42b6-b790-6546c345a617
    step_027 = solid.execute_command(x_pos=-364, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R4: 放回3号桶AA
    # unilab:node_uuid=869b50eb-b4aa-4156-81f8-7df9d9f57b28
    step_028 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R5: 取3号桶AA
    # unilab:node_uuid=09c0fbd1-93a6-4da8-b728-baf36e7c09ec
    step_029 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R5: 加AA 211.34mg @X-589
    # unilab:node_uuid=0535cc4d-7cae-413d-bddb-9ec2a97e4004
    step_030 = solid.execute_command(x_pos=-274, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R5: 放回3号桶AA
    # unilab:node_uuid=547f5b7f-d5e7-4a47-a2b9-d8b04d02fecf
    step_031 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R6: 取3号桶AA
    # unilab:node_uuid=0c6e04a5-02a2-4a11-869a-96180b2f40a4
    step_032 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R6: 加AA 211.34mg @X-580
    # unilab:node_uuid=79d7b91e-f6af-4d5a-a867-c73ee0494949
    step_033 = solid.execute_command(x_pos=-184, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R6: 放回3号桶AA
    # unilab:node_uuid=5e7e7683-1bbf-4fcd-bbc8-6a979fcbc796
    step_034 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R7: 取3号桶AA
    # unilab:node_uuid=85e01fcb-3831-4567-862b-f4110879c1a3
    step_035 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R7: 加AA 211.34mg @X-571
    # unilab:node_uuid=0f222a15-b89b-4bf4-be29-479c9298dfc0
    step_036 = solid.execute_command(x_pos=-94, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R7: 放回3号桶AA
    # unilab:node_uuid=f039e530-03cb-4c67-9694-205685aafdc8
    step_037 = solid.execute_command(x_pos=-3084, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R8: 取3号桶AA
    # unilab:node_uuid=2c2d8d4f-b582-4ae4-988d-2085d01ef6ec
    step_038 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step8-R8: 加AA 211.34mg @X-562
    # unilab:node_uuid=3020fcfb-da23-467c-a69a-b85c32204da4
    step_039 = solid.execute_command(x_pos=-4, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=2113, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step8-R8: 放回3号桶AA
    # unilab:node_uuid=440db233-4dd8-4745-a024-7da75b7f9986
    step_040 = solid.execute_command(x_pos=-3094, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # -夹取加样工位
    # unilab:node_uuid=678da8d3-da60-497d-bdc7-823518369eec
    step_041 = solid.execute_command(x_pos=-1840, y_pos=1790, timeout=180, x_speed=300, y_speed=300, cmd_type=12, door_pos=0, door_speed=0, gripper_z_pos=930, remove_static=0, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 放回料架
    # unilab:node_uuid=5e6bf1f2-e28e-4bda-a494-9b2a0648ecac
    step_042 = solid.execute_command(x_pos=-80, y_pos=2100, timeout=180, x_speed=300, y_speed=300, cmd_type=13, door_pos=0, door_speed=0, gripper_z_pos=2050, remove_static=0, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 固体加样-复位
    # unilab:node_uuid=4bd30c5d-4cc3-4bc4-b35b-396883f83a48
    step_043 = solid.execute_command(x_pos=0, y_pos=0, timeout=180, x_speed=0, y_speed=0, cmd_type=20, door_pos=0, door_speed=0, gripper_z_pos=0, volune_weight=0, material_z_pos=0, gripper_z_speed=0, material_z_speed=0)
    # 机械臂-从固体加样仪取样
    # unilab:node_uuid=c7ff5c4a-4673-45ff-a92f-dd8d124519ca
    step_044 = robot.pick(number=1, station="solid", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-放入9320
    # unilab:node_uuid=46038444-3c56-4444-bba6-8798dc7718db
    step_045 = robot.place(number=1, station="nine9320", timeout=30000, x_speed=400, item_type="plate")
    # AA溶液加液
    # unilab:node_uuid=c9e32c05-60d0-4a61-bce8-59da56c496c3
    step_046 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # Step9: T16->T6
    # unilab:node_uuid=4d566f8e-dda9-4b8c-a2ed-cf3828fd3d5a
    step_047 = prcxi.move_plate(to=6, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, intermediate_locations=[], pickup_distance_from_top=0)
    # 加水20ml，T7->T6，枪头用T11
    # unilab:node_uuid=3ebbbbf4-bb67-4c5c-81be-61591e462d32
    step_048 = prcxi.titration_liquid(offsets=None, sources=[{"uuid": "bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf"}], targets=[{"uuid": "dd9a0925-8aa7-44c0-a1bc-7dc9776ee6e0"}], asp_vols=[833], dis_vols=[833], none_keys=[], tip_racks=[{"uuid": "2c525d62-d238-48d5-892a-993e5c0065d7"}], is_96_well=True, repeat_count=2, use_channels=[], cycle_delay_s=10, liquid_height=None, asp_flow_rates=[300], dis_flow_rates=[300], post_air_volume=None, dispensing_method=["BottleNeck"], blow_out_air_volume=[20], hover_below_liquid_level=None, blow_out_air_volume_before=[], z_start_point_offset_height=None, post_discharge_pause_time_ms=None)
    # Step9: T6->T3
    # unilab:node_uuid=105afec5-4722-4a5a-8b6d-8920bc7853eb
    step_049 = prcxi.move_plate(to=3, from_=6, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=1, intermediate_locations=[], pickup_distance_from_top=0)
    # 9320震荡混匀 32°C 30min 3800（先按100来）
    # unilab:node_uuid=0fa3510a-cb51-4b8a-906e-16f007b2768b
    step_050 = prcxi.shaking_incubation_action(time=1800, is_wait=True, amplitude=100, module_no=1, temperature=32)
    # Step9: T3->T6
    # unilab:node_uuid=1c322757-5b66-454e-b2bd-06645a9fb662
    step_051 = prcxi.move_plate(to=6, from_=3, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=1, intermediate_locations=[], pickup_distance_from_top=0)
    # Step10: T6AA 390ul->T10，枪头T12
    # unilab:node_uuid=ed314513-a601-4019-937d-dfdcc3ca9c25
    step_052 = prcxi.transfer_liquid(delays=[], spread="wide", offsets=[], sources=[{"uuid": "dd9a0925-8aa7-44c0-a1bc-7dc9776ee6e0"}], targets=[{"uuid": "8e1cb526-32c4-4c6c-bb22-6faa81e209c5"}], asp_vols=[390], dis_vols=[390], mix_stage="none", none_keys=[], tip_racks=[{"uuid": "0014ea7c-9743-452a-96ab-5407f475a32c"}], touch_tip=False, is_96_well=True, source_slots=3, target_slots=10, use_channels=[], liquid_height=[], asp_flow_rates=[100], dis_flow_rates=[100], tip_rack_slots=12, post_air_volume=[], dispensing_method=[], blow_out_air_volume=[20], hover_below_liquid_level=[], blow_out_air_volume_before=[], z_start_point_offset_height=[], post_discharge_pause_time_ms=[])
    # AA溶液配制
    # unilab:node_uuid=cb0e3523-1c70-4d9f-9074-2b90551767d9
    step_053 = prcxi.run_protocol(protocol_id=None)
    # AA拿走
    # unilab:node_uuid=408b1996-3197-4285-8a84-cb9d65215fe3
    step_054 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # AA拿走 T6->T16
    # unilab:node_uuid=99516985-baff-4472-be61-55e1728d366c
    step_055 = prcxi.move_plate(to=16, from_=6, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, intermediate_locations=[], pickup_distance_from_top=0)
    # AA拿走
    # unilab:node_uuid=48376f07-7b5c-4dcb-8ed7-c3394ce13dc7
    step_056 = prcxi.run_protocol(protocol_id=None)
    # 机械臂-从9320取
    # unilab:node_uuid=489ed6ab-73f9-44e0-8ada-94d275821bd1
    step_057 = robot.pick(number=1, station="nine9320", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-放入堆栈2号位
    # unilab:node_uuid=c2f8b30a-92b3-4dce-a1ec-268d1a6b2b3b
    step_058 = robot.place(number=2, station="stack", timeout=30000, x_speed=400, item_type="plate")
    # 换枪头盒
    # unilab:node_uuid=15c89c1d-a748-42c7-8ecb-bdf6ebd110e9
    step_059 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # Step9: T15->T16
    # unilab:node_uuid=07f2a1c2-6660-4e49-8241-3b1744086d1a
    step_060 = prcxi.move_plate(to=16, from_=15, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # 换枪头盒
    # unilab:node_uuid=0e897325-a216-4e75-a5ce-22f9177c2dfe
    step_061 = prcxi.run_protocol(protocol_id=None)
    # 机械臂-从9320取
    # unilab:node_uuid=01c853d8-c7b0-437f-8863-8da4e170a8f4
    step_062 = robot.pick(number=1, station="nine9320", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-放入堆栈62
    # unilab:node_uuid=0c3396b5-267c-4886-89e8-d0ca89838493
    step_063 = robot.place(number=62, station="stack", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-从堆栈取61
    # unilab:node_uuid=0c5e7409-73fa-4274-943c-6d39b46fc805
    step_064 = robot.pick(number=61, station="stack", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-放入9320
    # unilab:node_uuid=d21d7e73-b37d-4cdf-a020-f0cb3af06f66
    step_065 = robot.place(number=1, station="nine9320", timeout=30000, x_speed=400, item_type="plate")
    # 换枪头盒
    # unilab:node_uuid=f9cdfdcb-e1d6-424f-bdb9-2c3d5ac38f12
    step_066 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # T16->T15
    # unilab:node_uuid=ece46779-1894-49e9-a49c-535111eec7c0
    step_067 = prcxi.move_plate(to=15, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # 换枪头
    # unilab:node_uuid=10b2cb4a-8733-4021-9bc6-1ceaf4e327ed
    step_068 = prcxi.run_protocol(protocol_id=None)
    # 溶液添加混合
    # unilab:node_uuid=280dc040-d99b-4d32-8e1a-5306da2a7ded
    step_069 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # Step11: T2 金球溶液39ul->T10，枪头T15
    # unilab:node_uuid=9bac4e9b-f132-4d3e-884f-8bdf6779833d
    step_070 = prcxi.transfer_liquid(delays=[], spread="wide", offsets=[], sources=[{"uuid": "94316d9c-4c66-43da-b964-416c51435179"}], targets=[{"uuid": "8e1cb526-32c4-4c6c-bb22-6faa81e209c5"}], asp_vols=[39], dis_vols=[39], mix_stage="none", none_keys=[], tip_racks=[{"uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}], touch_tip=False, is_96_well=True, source_slots=14, target_slots=10, use_channels=[], liquid_height=[], asp_flow_rates=[100], dis_flow_rates=[100], tip_rack_slots=11, post_air_volume=[], dispensing_method=[], blow_out_air_volume=[20], hover_below_liquid_level=[], blow_out_air_volume_before=[], z_start_point_offset_height=[], post_discharge_pause_time_ms=[])
    # 种子溶液
    # unilab:node_uuid=d7369a32-218b-4f0e-915b-912b70b2fec0
    step_071 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=1a16e48e-b6e6-4878-a70e-abfc922debd9
    step_072 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=889e452a-5f30-42d2-ab88-8cbe985082c0
    step_073 = prcxi.move_plate(to=16, from_=12, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=fa78bc7d-7d6a-4adc-8900-2a16e4d1a543
    step_074 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=9c7de428-f2e2-4d47-a474-8fa1b7995756
    step_075 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=4e852e39-b5ae-46ed-85b6-21a44f4e9db5
    step_076 = robot.place(number=59, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=3c24c606-98e4-456e-b60e-107c8a23a818
    step_077 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=a07544b0-b277-4483-853f-9a08957d0f09
    step_078 = prcxi.move_plate(to=16, from_=15, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=ee05cc71-141e-4139-aa7e-23076239a4f5
    step_079 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=43d21f0d-716a-4cda-9e48-8b323865b877
    step_080 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=f997148f-afa7-4f82-8003-cc7b4e4c8c68
    step_081 = robot.place(number=60, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=da23c814-277e-4e24-89b9-154e11bb6b60
    step_082 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=6b2f8b63-6140-4a0c-8f7c-6c84b9520645
    step_083 = prcxi.move_plate(to=16, from_=11, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=596a2afa-d7aa-4053-ad3f-768c80cf41fe
    step_084 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=c65fa8f9-3385-4329-b244-b34fb6302987
    step_085 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=bd56e00f-c615-4581-b57a-1c7f312ddbda
    step_086 = robot.place(number=61, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=5da2028a-1223-42f5-b1f3-79e1d253abdc
    step_087 = robot.pick(number=63, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=5cb0efe8-2c84-4fa3-8b35-2cd3b9ff8b49
    step_088 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=9989ae07-69d6-4b51-a0f7-a8e6469c5fec
    step_089 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=faee26c0-7224-4373-a427-1052211fcf3b
    step_090 = prcxi.move_plate(to=11, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=f74b4aef-25e5-4410-99db-863660c898eb
    step_091 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=d0f1105d-af9a-49fb-8947-5d7380dda6f2
    step_092 = robot.pick(number=64, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=1102b943-7c8b-4b0c-8a00-f0455ff61578
    step_093 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=9d3a94ff-0ce1-4eb7-ba99-4337023e593e
    step_094 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=542b7e32-8546-4472-a52f-bad07761097c
    step_095 = prcxi.move_plate(to=12, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=6548e9d3-2861-4858-9b72-0af3ed1a7007
    step_096 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=f8d1ede2-c85a-4440-9c2c-b1908cbc8e29
    step_097 = robot.pick(number=65, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=a41feb99-7b61-45b0-9916-e2d84a4002b5
    step_098 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=27d8df28-2119-4591-84a5-d64614fea19a
    step_099 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=0887b1cf-4a1a-4203-acde-2e990c3ff325
    step_100 = prcxi.move_plate(to=15, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=a6a9d50a-2ff0-4386-92e3-4e1641355c64
    step_101 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=40f80248-6348-4eb1-82b2-93b4b655a4f7
    step_102 = robot.pick(number=7, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=a4dc3ba9-f94d-450e-9aa3-b3312fb8a00d
    step_103 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # Step7a: T16->T10
    # unilab:node_uuid=57007e39-74e2-407c-a853-3025239975ab
    step_104 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # T16->T6，把氯金酸放到T6
    # unilab:node_uuid=81ee9925-02af-4c5d-b878-72a90945ef92
    step_105 = prcxi.move_plate(to=6, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, intermediate_locations=[], pickup_distance_from_top=0)
    # T10>T3
    # unilab:node_uuid=e662b61e-f2fa-43bc-9b2a-c2b317497f93
    step_106 = prcxi.move_plate(to=3, from_=10, force=5, plate=[{"uuid": "dd9a0925-8aa7-44c0-a1bc-7dc9776ee6e0"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=b3afdc91-97b9-4096-889d-390f8ccdd2b2
    step_107 = prcxi.shaking_incubation_action(time=300000, is_wait=False, amplitude=1200, module_no=1, temperature=32)
    # unilab:node_uuid=bf32e07b-2f91-4bac-8286-e06ee160e8a1
    step_108 = prcxi.titration_liquid(offsets=None, sources=[{"uuid": "dd9a0925-8aa7-44c0-a1bc-7dc9776ee6e0"}], targets=[{"uuid": "a2e75ff7-6b1a-494a-8b18-0b6d93619a11"}], asp_vols=[4.8], dis_vols=[4.8], none_keys=[], tip_racks=[{"uuid": "2c525d62-d238-48d5-892a-993e5c0065d7"}], is_96_well=True, repeat_count=27, use_channels=[], cycle_delay_s=180, liquid_height=None, asp_flow_rates=[20], dis_flow_rates=[20], post_air_volume=None, dispensing_method=["BottleNeck"], blow_out_air_volume=[20], hover_below_liquid_level=None, blow_out_air_volume_before=[300], z_start_point_offset_height=None, post_discharge_pause_time_ms=None)
    # unilab:node_uuid=e9a7678d-b859-4c86-a30d-38ebb83836a5
    step_109 = prcxi.titration_liquid(offsets=None, sources=[{"uuid": "dd9a0925-8aa7-44c0-a1bc-7dc9776ee6e0"}], targets=[{"uuid": "a2e75ff7-6b1a-494a-8b18-0b6d93619a11"}], asp_vols=[4.8], dis_vols=[4.8], none_keys=[], tip_racks=[{"uuid": "0014ea7c-9743-452a-96ab-5407f475a32c"}], is_96_well=True, repeat_count=27, use_channels=[], cycle_delay_s=180, liquid_height=None, asp_flow_rates=[20], dis_flow_rates=[20], post_air_volume=None, dispensing_method=["BottleNeck"], blow_out_air_volume=[20], hover_below_liquid_level=None, blow_out_air_volume_before=[300], z_start_point_offset_height=None, post_discharge_pause_time_ms=None)
    # unilab:node_uuid=ecd5653b-6f0b-487a-87a6-883689459374
    step_110 = prcxi.titration_liquid(offsets=None, sources=[{"uuid": "dd9a0925-8aa7-44c0-a1bc-7dc9776ee6e0"}], targets=[{"uuid": "a2e75ff7-6b1a-494a-8b18-0b6d93619a11"}], asp_vols=[4.8], dis_vols=[4.8], none_keys=[], tip_racks=[{"uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}], is_96_well=True, repeat_count=27, use_channels=[], cycle_delay_s=180, liquid_height=None, asp_flow_rates=[20], dis_flow_rates=[20], post_air_volume=None, dispensing_method=["BottleNeck"], blow_out_air_volume=[20], hover_below_liquid_level=None, blow_out_air_volume_before=[300], z_start_point_offset_height=None, post_discharge_pause_time_ms=None)
    # unilab:node_uuid=cb61b1a6-a6d0-4d1d-b14f-b8dc8343a727
    step_111 = prcxi.shaking_incubation_action(time=5, is_wait=True, amplitude=300, module_no=1, temperature=50)
    # unilab:node_uuid=e1b6c0ce-6c74-46bc-a1d4-82d4ebc006f1
    step_112 = prcxi.run_protocol(protocol_id=None)
    # 恒温静置
    # unilab:node_uuid=aff0af1c-e533-4642-a1ef-f787e37cdf6d
    step_113 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # 9320恒温静置 35°C 10min
    # unilab:node_uuid=7001054f-f850-4a4b-97f0-1026b2abbe10
    step_114 = prcxi.shaking_incubation_action(time=600, is_wait=True, amplitude=1, module_no=1, temperature=35)
    # 9320协议C（Step9-11+静置）
    # unilab:node_uuid=e571c9e5-0f87-45fb-aebb-dc614c65539e
    step_115 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=fcc18112-4339-4315-8f26-d7da728cdc67
    step_116 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=0bbfef89-6d0a-4c05-83cc-17a53412812a
    step_117 = prcxi.move_plate(to=16, from_=15, force=5, plate=[{"uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=b47bde09-d733-431f-8059-421abb7b596f
    step_118 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=ee34b60a-1ec1-4c9c-8ff0-58d1cf011e87
    step_119 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=500, item_type="plate")
    # unilab:node_uuid=65d6a0a8-d26a-4162-bcf2-cdd3fc7da25e
    step_120 = robot.place(number=63, station="stack", timeout=180, x_speed=500, item_type="plate")
    # unilab:node_uuid=5d4491b7-e387-4fe2-bb06-38354c81a182
    step_121 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=585b2d2b-3b0a-4eea-a9ee-62a89aaf4904
    step_122 = prcxi.move_plate(to=16, from_=12, force=5, plate=[{"uuid": "0014ea7c-9743-452a-96ab-5407f475a32c"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=7102d847-f3e3-4698-81d6-c396c34d3521
    step_123 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=b0946f04-85e1-40a4-80d3-b3d67349313c
    step_124 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=500, item_type="plate")
    # unilab:node_uuid=b595c9e7-05b7-43f2-9520-4f7b60637857
    step_125 = robot.place(number=64, station="stack", timeout=180, x_speed=500, item_type="plate")
    # unilab:node_uuid=7f6e94e1-1bde-4f75-8a1d-58fd3e4cbea4
    step_126 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=1362c516-1945-4580-b1b4-6c67b0a79055
    step_127 = prcxi.move_plate(to=16, from_=11, force=5, plate=[{"uuid": "2c525d62-d238-48d5-892a-993e5c0065d7"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=aa785112-3648-47fa-a4e5-fdf4d5ffe8f3
    step_128 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=f204eaeb-ef34-4096-8dfa-2e0719fea835
    step_129 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=500, item_type="plate")
    # unilab:node_uuid=a42accf7-0974-45fa-95fb-eedc7c1c308a
    step_130 = robot.place(number=65, station="stack", timeout=180, x_speed=500, item_type="plate")
    # unilab:node_uuid=5b7a2bff-d461-4c97-bc01-b9050671adcc
    step_131 = robot.pick(number=66, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=894ef3df-c243-4995-ab37-46486b26bc18
    step_132 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=c2ad8e0a-bea9-43ae-a89f-af0670b580e9
    step_133 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=088c0c04-0651-4560-b4e6-552186698c39
    step_134 = prcxi.move_plate(to=11, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=69bb055c-8b4e-4c87-a33c-0b231e0826f7
    step_135 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=e9c61b9e-b227-436f-af34-dc96f587751e
    step_136 = robot.pick(number=67, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=c4312bf8-6308-4e9d-964c-abf18cc256ea
    step_137 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=c06c93ac-8dec-4b8e-bed1-05f2803ed5f9
    step_138 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=0e11f16c-a166-47d9-823e-74859ec7f5a1
    step_139 = prcxi.move_plate(to=12, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=e1fb16af-cdb9-452f-909b-f9cfeb919d68
    step_140 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=1cf929cd-3ca1-4ee7-a38b-9a6d8e1ff502
    step_141 = robot.pick(number=68, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=20ba7a53-603a-4abd-a70a-50d8268e49ec
    step_142 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=65110692-4a2e-4c43-8137-bc846851b697
    step_143 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # unilab:node_uuid=dabb815a-21b8-4949-98e7-88403aaa0af5
    step_144 = prcxi.move_plate(to=15, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=8a3f52a7-6333-4566-b6dd-57cfa95b354d
    step_145 = prcxi.run_protocol(protocol_id=None)
    # 恒温静置
    # unilab:node_uuid=8fc5457c-ca9f-4b3e-95f0-fffc9e474698
    step_146 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # 离心前: T3->T16
    # unilab:node_uuid=7c341c69-9c53-4ca1-9f57-c4c585e67a33
    step_147 = prcxi.move_plate(to=16, from_=3, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=1, intermediate_locations=[], pickup_distance_from_top=0)
    # 9320协议C（Step9-11+静置）
    # unilab:node_uuid=f1384802-8f34-4e5d-b31e-470d529426fe
    step_148 = prcxi.run_protocol(protocol_id=None)
