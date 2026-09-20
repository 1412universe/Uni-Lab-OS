"""从 legacy JSON 导入：金四面体CTAC与CTAB混合溶液制备.json。"""

from unilabos.workflow.authoring import device, workflow

from gn_lab.devices.gn_prcxi import GN_PRCXI9300Handler
from gn_lab.devices.gn_station_devices import RotaryStackDevice, RoboticArmDevice, SolidWeighingDevice

stack: RotaryStackDevice = device("gn_rotary_stack")
robot: RoboticArmDevice = device("gn_robotic_arm")
prcxi: GN_PRCXI9300Handler = device("PRCXI")
solid: SolidWeighingDevice = device("gn_solid_weighing")

@workflow(
    workflow_uuid="a138bc18-9c16-4552-8696-e4f43903b9ad",
    displayname="金四面体CTAC与CTAB混合溶液制备",
    description="由 金四面体CTAC与CTAB混合溶液制备.json 转换，节点身份保持不变。",
)
def gold_tetrahedron_ctac_ctab() -> None:
    # 堆栈复位
    # unilab:node_uuid=4043eba9-7c1e-450a-ae2c-68952fe9ea5a
    step_001 = stack.execute_command(r_pos=0, z_pos=0, r_speed=0, timeout=120, z_speed=0, cmd_type=7)
    # 机械臂-从堆栈76取枪头盒
    # unilab:node_uuid=0f6be485-a365-4bb5-8a46-638aa088bf2e
    step_002 = robot.pick(number=76, station="stack", timeout=180, x_speed=300, item_type="plate")
    # 机械臂-枪头盒放入9320
    # unilab:node_uuid=9b6727d8-6d79-4a50-a9e4-78f22d28432c
    step_003 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # 协议-枪头盒装载(T16->T15)
    # unilab:node_uuid=2903ed13-5321-4740-a569-8d2ea27e64cd
    step_004 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # 枪头盒T16->T15
    # unilab:node_uuid=f45e9599-b1e0-44bc-b863-15029998dde4
    step_005 = prcxi.move_plate(to=15, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # 执行-枪头盒装载
    # unilab:node_uuid=c170d914-8d6a-4c95-86db-fcb0b48b2adf
    step_006 = prcxi.run_protocol(protocol_id=None)
    # 机械臂-从堆栈8取8通道板
    # unilab:node_uuid=e5529d78-d4b9-4ed9-a4b2-8772382f1f09
    step_007 = robot.pick(number=8, station="stack", timeout=180, x_speed=300, item_type="plate")
    # 机械臂-8通道板放入固体加样仪
    # unilab:node_uuid=ea6184ff-b65e-4cb5-8270-19075205a830
    step_008 = robot.place(number=1, station="solid", timeout=30000, x_speed=400, item_type="plate")
    # 固体加样-复位
    # unilab:node_uuid=704b16f3-f66a-449b-8196-ea244430a629
    step_009 = solid.execute_command(x_pos=0, y_pos=0, timeout=180, x_speed=500, y_speed=500, cmd_type=20, door_pos=0, door_speed=0, gripper_z_pos=0, volune_weight=0, material_z_pos=0, gripper_z_speed=0, material_z_speed=0)
    # 夹取料架
    # unilab:node_uuid=08ec91c5-c1ed-4da7-bdf8-bb5684e16c1e
    step_010 = solid.execute_command(x_pos=-80, y_pos=2100, timeout=180, x_speed=300, y_speed=300, cmd_type=12, door_pos=0, door_speed=0, gripper_z_pos=2050, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 放置加样工位
    # unilab:node_uuid=8ed14d1c-684b-4e60-9d54-e7572fbfb0d1
    step_011 = solid.execute_command(x_pos=-1840, y_pos=1790, timeout=180, x_speed=300, y_speed=300, cmd_type=13, door_pos=0, door_speed=0, gripper_z_pos=930, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 取料筒向里-CTAC（2号粉料桶）第1/8次
    # unilab:node_uuid=f05553b7-223e-4deb-820d-02aecb2f4603
    step_012 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第1/8次 (x=-624)
    # unilab:node_uuid=817ed94e-7c8b-4fa8-bc1b-e798091b2a25
    step_013 = solid.execute_command(x_pos=-634, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向外-CTAC（2号粉料桶）第1/8次
    # unilab:node_uuid=6e086566-90ea-42eb-b7d7-7553f1fd1f80
    step_014 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 取料筒向外-CTAC（2号粉料桶）第2/8次
    # unilab:node_uuid=1b0007ba-caf7-474e-b5b9-62fec3993ce3
    step_015 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第2/8次 (x=-534)
    # unilab:node_uuid=fa6f6719-71f7-4ec2-b1c5-c4bcef779a9c
    step_016 = solid.execute_command(x_pos=-544, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向里-CTAC（2号粉料桶）第2/8次
    # unilab:node_uuid=6e0dddb6-83d7-400c-9088-8281e2d5aff2
    step_017 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 取料筒向外-CTAC（2号粉料桶）第3/8次
    # unilab:node_uuid=dc6f8e1c-45d3-4598-af18-edfd770755a1
    step_018 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第3/8次 (x=-444)
    # unilab:node_uuid=e62f598f-da84-4bc1-9e72-5e5c634f8973
    step_019 = solid.execute_command(x_pos=-454, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向里-CTAC（2号粉料桶）第3/8次
    # unilab:node_uuid=ee7f9b42-11ab-4ca1-8cb9-00c14767c23f
    step_020 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 取料筒向外-CTAC（2号粉料桶）第4/8次
    # unilab:node_uuid=63bb761b-32be-4827-80f5-edfbcb873031
    step_021 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第4/8次 (x=-354)
    # unilab:node_uuid=27415a35-69fc-4446-beb8-be0e0276a6f3
    step_022 = solid.execute_command(x_pos=-364, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向里-CTAC（2号粉料桶）第4/8次
    # unilab:node_uuid=16b5ca60-c64d-436b-963b-78cae31ae065
    step_023 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 取料筒向外-CTAC（2号粉料桶）第5/8次
    # unilab:node_uuid=8dfe76c3-41e5-4bf0-aebb-9a0d12f2c90b
    step_024 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第5/8次 (x=-264)
    # unilab:node_uuid=7148f3be-da61-454d-9f54-cbb05f617860
    step_025 = solid.execute_command(x_pos=-274, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向里-CTAC（2号粉料桶）第5/8次
    # unilab:node_uuid=302f1ffc-db4d-42e1-96d6-6208f79b1c1c
    step_026 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 取料筒向外-CTAC（2号粉料桶）第6/8次
    # unilab:node_uuid=ad375b62-713d-4b72-b842-f9ebe86c0b40
    step_027 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第6/8次 (x=-174)
    # unilab:node_uuid=f0ddc7d3-4172-4da7-a73e-737757dea709
    step_028 = solid.execute_command(x_pos=-184, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向里-CTAC（2号粉料桶）第6/8次
    # unilab:node_uuid=d28d7598-33e1-4eca-8e1b-78ba06e06b21
    step_029 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 取料筒向外-CTAC（2号粉料桶）第7/8次
    # unilab:node_uuid=1ce1b936-7c3e-48ef-b30d-e6cc078809f4
    step_030 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第7/8次 (x=-84)
    # unilab:node_uuid=eaa39fb6-6d92-4b31-ad11-11e160050f4c
    step_031 = solid.execute_command(x_pos=-94, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向里-CTAC（2号粉料桶）第7/8次
    # unilab:node_uuid=8ab376b1-dee3-437f-adc3-c14f1d061a13
    step_032 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 取料筒向外-CTAC（2号粉料桶）第8/8次
    # unilab:node_uuid=2a1f5961-7613-4995-94fc-88b17a45ce95
    step_033 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 料筒加样-CTAC 128mg 第8/8次 (x=6)
    # unilab:node_uuid=3a6069c2-7564-430d-a105-dd1800b75d2b
    step_034 = solid.execute_command(x_pos=-4, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1280, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # 放料筒向里-CTAC（2号粉料桶）第8/8次
    # unilab:node_uuid=39f5b1fd-636a-4410-8d28-91a0c8b0aa17
    step_035 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # 夹取加样工位
    # unilab:node_uuid=679acb92-5b83-4bed-bec0-733f86fc92a5
    step_036 = solid.execute_command(x_pos=-1840, y_pos=1790, timeout=180, x_speed=300, y_speed=300, cmd_type=12, door_pos=0, door_speed=0, gripper_z_pos=2050, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 放回料架
    # unilab:node_uuid=05635bbc-51e3-4184-a835-d801ec16cd26
    step_037 = solid.execute_command(x_pos=-80, y_pos=2100, timeout=180, x_speed=300, y_speed=300, cmd_type=13, door_pos=0, door_speed=0, gripper_z_pos=930, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 固体加样-复位
    # unilab:node_uuid=e7bf4c71-0b48-42b0-8b6b-ce8e36fd9ed0
    step_038 = solid.execute_command(x_pos=0, y_pos=0, timeout=180, x_speed=500, y_speed=500, cmd_type=20, door_pos=0, door_speed=0, gripper_z_pos=0, volune_weight=0, material_z_pos=0, gripper_z_speed=0, material_z_speed=0)
    # 机械臂-从固体加样仪取样
    # unilab:node_uuid=ddafa1f6-55f0-428f-8e42-8752ad7454c0
    step_039 = robot.pick(number=1, station="solid", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-放入9320
    # unilab:node_uuid=b485df9e-6f8d-4100-b7f4-f39d5e18a741
    step_040 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # 协议-9320主流程(T16->T8, 滴定, T15->T16)
    # unilab:node_uuid=26f26879-32d9-439e-896d-5cd24c13d516
    step_041 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # T16->T8
    # unilab:node_uuid=1f24afe4-d615-4e97-b344-15b51dde9d88
    step_042 = prcxi.move_plate(to=8, from_=16, force=5, plate=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=1, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=1, intermediate_locations=[], pickup_distance_from_top=0)
    # T7加水20ml->T8（分两次），枪头T15
    # unilab:node_uuid=3bbf979a-e58c-41b9-9f63-ffaceacb1c16
    step_043 = prcxi.titration_liquid(offsets=None, sources=[{"uuid": "bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf"}], targets=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], asp_vols=[1000], dis_vols=[1000], none_keys=[], tip_racks=[{"uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}], is_96_well=True, repeat_count=20, use_channels=[], cycle_delay_s=10, liquid_height=None, asp_flow_rates=[300], dis_flow_rates=[300], post_air_volume=None, dispensing_method=["BottleNeck"], blow_out_air_volume=[20], hover_below_liquid_level=None, blow_out_air_volume_before=[], z_start_point_offset_height=None, post_discharge_pause_time_ms=None)
    # T15->T16
    # unilab:node_uuid=4343b374-1991-4a39-9dfc-2ba1a1e04633
    step_044 = prcxi.move_plate(to=16, from_=15, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # 执行-9320主流程
    # unilab:node_uuid=efc46b90-26df-49d9-9a83-6b33ddf9d686
    step_045 = prcxi.run_protocol(protocol_id=None)
    # 机械臂-取9320 T16（默认参数）
    # unilab:node_uuid=a035ef72-47a1-4780-b897-499676fc072c
    step_046 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # 机械臂-放回堆栈76
    # unilab:node_uuid=c560030a-2b5c-453e-8439-5d72fd17f67c
    step_047 = robot.place(number=76, station="stack", timeout=180, x_speed=300, item_type="plate")
    # 堆栈复位
    # unilab:node_uuid=6e2dc62b-1e67-4fad-b08c-b502e8782586
    step_048 = stack.execute_command(r_pos=0, z_pos=0, r_speed=0, timeout=120, z_speed=0, cmd_type=7)
    # unilab:node_uuid=3ddf4a21-eb81-48f8-ae3e-06e74ee0bfe6
    step_049 = robot.pick(number=59, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=6ee0e77b-0f8a-4d11-92f2-3a820cbce141
    step_050 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=ee0d619d-86d5-4f55-b974-c1b171e00247
    step_051 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # 枪头盒T16->T11
    # unilab:node_uuid=6b9be2ee-7240-4e34-bd48-ffc64bd7f89c
    step_052 = prcxi.move_plate(to=11, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=9b2f6b94-6c05-474f-8d4e-53533cbf3b05
    step_053 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=c3b31449-5227-4629-bc71-cc78e5bcf6f9
    step_054 = robot.pick(number=60, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=ea5b60b4-a8f3-45a6-8831-1fb56970a96b
    step_055 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=20de019c-d40c-43d2-955a-cb94f4c65bef
    step_056 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # T16->T12
    # unilab:node_uuid=44426b8f-a09b-4320-b994-3281da2baf16
    step_057 = prcxi.move_plate(to=12, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=22d1e840-0bb2-4e08-97f1-479b6c35cd13
    step_058 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=e20ad357-f794-4470-aad6-150982d5425e
    step_059 = robot.pick(number=61, station="stack", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=91c529f3-77b4-4194-9fe8-8250da17a8f5
    step_060 = robot.place(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=58b0f982-f5a0-471b-be5f-4b5c362d7d43
    step_061 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # T16->T15
    # unilab:node_uuid=fba6968f-b581-4252-8fa1-7da537f6d928
    step_062 = prcxi.move_plate(to=15, from_=16, force=5, plate=[{"uuid": "e74c153c-a29f-4653-a665-b9e1cc1711a9"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=b14674c9-665a-4376-9f4a-5cfe162241c7
    step_063 = prcxi.run_protocol(protocol_id=None)
    # 堆栈复位
    # unilab:node_uuid=14d2851d-6787-4b76-b71b-88ca9497685a
    step_064 = stack.execute_command(r_pos=0, z_pos=0, r_speed=0, timeout=120, z_speed=0, cmd_type=7)
    # Step1: 堆栈1号位取96孔板至固体加样(CTAC+CTAB)
    # unilab:node_uuid=e66468c5-9349-4865-87c7-15e0b9fc461e
    step_065 = robot.pick(number=1, station="stack", timeout=300, x_speed=400, item_type="plate")
    # 机械臂-放入固体加样仪(96孔板1号板位)
    # unilab:node_uuid=24651578-227e-4da1-9bfb-9ea54063cbba
    step_066 = robot.place(number=1, station="solid", timeout=30000, x_speed=400, item_type="plate")
    # 固体加样-复位
    # unilab:node_uuid=b44e5bad-5f7d-4a70-842b-799cc2421495
    step_067 = solid.execute_command(x_pos=0, y_pos=0, timeout=180, x_speed=0, y_speed=0, cmd_type=20, door_pos=0, door_speed=0, gripper_z_pos=0, volune_weight=0, material_z_pos=0, gripper_z_speed=0, material_z_speed=0)
    # 夹取料架
    # unilab:node_uuid=249a199b-8a2e-463d-9e1c-253a758ab172
    step_068 = solid.execute_command(x_pos=-80, y_pos=2100, timeout=180, x_speed=300, y_speed=300, cmd_type=12, door_pos=0, door_speed=0, gripper_z_pos=2050, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 放置加样工位
    # unilab:node_uuid=bbc8e0dc-6437-4088-9130-985e48fb59e3
    step_069 = solid.execute_command(x_pos=-1840, y_pos=1790, timeout=180, x_speed=300, y_speed=300, cmd_type=13, door_pos=0, door_speed=0, gripper_z_pos=930, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # Step1-R1: 取1号桶CTAB
    # unilab:node_uuid=5745cbad-3478-4297-a5ea-1f6e395e6868
    step_070 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R1: 加CTAB 182mg @X-625
    # unilab:node_uuid=535ae3fc-383e-4e5d-bc37-7622d46439bf
    step_071 = solid.execute_command(x_pos=-634, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1820, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R1: 放回1号桶CTAB
    # unilab:node_uuid=3aa717b3-75ef-4859-a784-fa8333f5b440
    step_072 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R1: 取2号桶CTAC
    # unilab:node_uuid=192cd367-50fb-4c29-af69-0f1e7ce20a2a
    step_073 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R1: 加CTAC 160mg @X-625
    # unilab:node_uuid=cee4aa2a-e366-4b47-bea5-2364a751c17d
    step_074 = solid.execute_command(x_pos=-634, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R1: 放回2号桶CTAC
    # unilab:node_uuid=be06fd7b-c868-4ad8-b494-ab809c9f6880
    step_075 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R2: 取1号桶CTAC
    # unilab:node_uuid=a639490c-6b89-457c-9c38-8758400d6bd5
    step_076 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=1, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R2: 加CTAC 182mg @X-616
    # unilab:node_uuid=9945e7d5-cbb4-4860-a177-54bedcc8a9da
    step_077 = solid.execute_command(x_pos=-544, y_pos=650, timeout=15000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=1, volune_weight=300, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R2: 放回1号桶CTAC
    # unilab:node_uuid=0bafe827-1ef5-4119-8faa-4e31693ed8d1
    step_078 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R2: 取2号桶CTAB
    # unilab:node_uuid=3ef70bad-1846-4d9e-a5b0-bfe182c0ff4c
    step_079 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R2: 加CTAB 160mg @X-616
    # unilab:node_uuid=3324e296-5f95-45eb-905f-493aefd63ce5
    step_080 = solid.execute_command(x_pos=-544, y_pos=650, timeout=6000, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R2: 放回2号桶CTAB
    # unilab:node_uuid=5a744210-3f77-47de-a45c-834b2b39a923
    step_081 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R3: 取1号桶CTAC
    # unilab:node_uuid=609ece97-d567-4b85-a020-7c858f2e868e
    step_082 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R3: 加CTAC 182mg @X-607
    # unilab:node_uuid=e434fb7d-3849-4dd0-b72f-ec09d28a0f90
    step_083 = solid.execute_command(x_pos=-454, y_pos=650, timeout=1500, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1820, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R3: 放回1号桶CTAC
    # unilab:node_uuid=3bbf9d15-aefd-46b1-978f-f685f6b40628
    step_084 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R3: 取2号桶CTAB
    # unilab:node_uuid=cf0515b7-b152-44aa-81fe-67f54a08e1ba
    step_085 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R3: 加CTAB 160mg @X-607
    # unilab:node_uuid=e7415c79-67b9-4fb6-9cd2-c4430de817fd
    step_086 = solid.execute_command(x_pos=-454, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R3: 放回2号桶CTAB
    # unilab:node_uuid=6f4e8518-07b5-4138-a47f-6aa1c3c09feb
    step_087 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R4: 取1号桶CTAC
    # unilab:node_uuid=8f88e025-db17-4589-9475-21a29fb81c49
    step_088 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R4: 加CTAC 182mg @X-598
    # unilab:node_uuid=83fe289e-7db8-4c1f-81d6-583dcc36fa3c
    step_089 = solid.execute_command(x_pos=-364, y_pos=650, timeout=1500, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1820, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R4: 放回1号桶CTAC
    # unilab:node_uuid=540a9775-fe4b-4118-a513-350caab0bae5
    step_090 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R4: 取2号桶CTAB
    # unilab:node_uuid=24d85480-498c-4cfa-b74b-87e458cc0144
    step_091 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R4: 加CTAB 160mg @X-598
    # unilab:node_uuid=d1725174-5525-4a28-bf41-31abce51e3f6
    step_092 = solid.execute_command(x_pos=-364, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R4: 放回2号桶CTAB
    # unilab:node_uuid=8203b3ba-b03d-4579-9929-fd68b4f2a115
    step_093 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R5: 取1号桶CTAC
    # unilab:node_uuid=9a55a41c-2bc1-42b3-938a-ac7f61ad25a0
    step_094 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R5: 加CTAC 182mg @X-589
    # unilab:node_uuid=5db6e5c2-e4f6-4566-b641-9cce3499976e
    step_095 = solid.execute_command(x_pos=-274, y_pos=650, timeout=1500, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1820, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R5: 放回1号桶CTAC
    # unilab:node_uuid=42c10b36-e15f-4251-90ee-4461e7082842
    step_096 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R5: 取2号桶CTAB
    # unilab:node_uuid=45203e22-5b79-4326-bc9a-e9935a320483
    step_097 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R5: 加CTAB 160mg @X-589
    # unilab:node_uuid=408d0457-361c-4050-8405-9f92717f9032
    step_098 = solid.execute_command(x_pos=-274, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R5: 放回2号桶CTAB
    # unilab:node_uuid=286d231a-f9dc-404d-83f7-4e2c81f9817e
    step_099 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R6: 取1号桶CTAC
    # unilab:node_uuid=4441e19e-7160-401a-9a3a-79a0e0d6d3d9
    step_100 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R6: 加CTAC 182mg @X-580
    # unilab:node_uuid=20bd8c67-e56c-4227-be9c-58c72bf2b2b3
    step_101 = solid.execute_command(x_pos=-184, y_pos=650, timeout=1500, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1820, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R6: 放回1号桶CTAC
    # unilab:node_uuid=cab4ef3b-997c-45cb-88ab-05f7cc612596
    step_102 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R6: 取2号桶CTAB
    # unilab:node_uuid=4fe88b60-9e85-4197-84e9-2e00e4346a4a
    step_103 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R6: 加CTAB 160mg @X-580
    # unilab:node_uuid=bedb7502-197d-4ff2-874a-ee3d7231b20c
    step_104 = solid.execute_command(x_pos=-184, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R6: 放回2号桶CTAB
    # unilab:node_uuid=06f79c55-95b5-43ce-b4c6-f6a66cbb3b28
    step_105 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R7: 取1号桶CTAC
    # unilab:node_uuid=42a3da81-0574-41c1-b1e5-adc255c7306d
    step_106 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R7: 加CTAC 182mg @X-571
    # unilab:node_uuid=867ec591-9a77-48f6-b6a3-7275b6c25364
    step_107 = solid.execute_command(x_pos=-94, y_pos=650, timeout=1500, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1820, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R7: 放回1号桶CTAC
    # unilab:node_uuid=33a63184-4ecd-4819-9684-b2f83c912cbd
    step_108 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R7: 取2号桶CTAB
    # unilab:node_uuid=586956c0-7565-4576-acff-fb999f4c4929
    step_109 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R7: 加CTAB 160mg @X-571
    # unilab:node_uuid=9932f62f-05ad-45c1-9371-54a38d4d7f2a
    step_110 = solid.execute_command(x_pos=-94, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R7: 放回2号桶CTAB
    # unilab:node_uuid=f902188d-8f8e-4e6b-bd4e-e4a5b0bd16a9
    step_111 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R8: 取1号桶CTAC
    # unilab:node_uuid=8263d997-1a7f-4f06-b61b-eb0f202e842b
    step_112 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R8: 加CTAC 182mg @X-562
    # unilab:node_uuid=2f4b15c4-7d74-4473-b98c-ebdeed0c7cda
    step_113 = solid.execute_command(x_pos=-4, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1820, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R8: 放回1号桶CTAC
    # unilab:node_uuid=79c8dca8-c85e-4f50-98e9-d4562c07c4f2
    step_114 = solid.execute_command(x_pos=-4274, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R8: 取2号桶CTAB
    # unilab:node_uuid=cbc68b79-d508-438e-aa6d-b97aa51810c3
    step_115 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=9, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # Step1-R8: 加CTAB 160mg @X-562
    # unilab:node_uuid=7a4320ba-c3ba-46b2-9660-6e2616a50b77
    step_116 = solid.execute_command(x_pos=-4, y_pos=650, timeout=600, x_speed=500, y_speed=500, cmd_type=11, door_pos=3600, door_speed=150, gripper_z_pos=0, remove_static=0, volune_weight=1600, material_z_pos=136000, gripper_z_speed=0, material_z_speed=300)
    # Step1-R8: 放回2号桶CTAB
    # unilab:node_uuid=a8837417-4471-4413-b79e-a51b9e5cf82b
    step_117 = solid.execute_command(x_pos=-3684, y_pos=210, timeout=180, x_speed=500, y_speed=500, cmd_type=19, door_pos=0, door_speed=0, gripper_z_pos=0, remove_static=0, volune_weight=0, material_z_pos=239000, gripper_z_speed=0, material_z_speed=0)
    # -夹取加样工位
    # unilab:node_uuid=c053029d-d240-4010-8b5f-306a4645f6ed
    step_118 = solid.execute_command(x_pos=-1840, y_pos=1790, timeout=180, x_speed=300, y_speed=300, cmd_type=12, door_pos=0, door_speed=0, gripper_z_pos=930, remove_static=0, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 放回料架
    # unilab:node_uuid=5c5b44fb-5e0c-4d8b-981c-a7029853187e
    step_119 = solid.execute_command(x_pos=-80, y_pos=2100, timeout=180, x_speed=300, y_speed=300, cmd_type=13, door_pos=0, door_speed=0, gripper_z_pos=2050, remove_static=0, volune_weight=0, material_z_pos=0, gripper_z_speed=300, material_z_speed=0)
    # 固体加样-复位
    # unilab:node_uuid=4aabd7cd-1eb0-4e02-8f76-271b6ec3710c
    step_120 = solid.execute_command(x_pos=0, y_pos=0, timeout=180, x_speed=0, y_speed=0, cmd_type=20, door_pos=0, door_speed=0, gripper_z_pos=0, volune_weight=0, material_z_pos=0, gripper_z_speed=0, material_z_speed=0)
    # 机械臂-从固体加样仪取样
    # unilab:node_uuid=3f68f21b-0884-45c8-af9c-d258dfe73a72
    step_121 = robot.pick(number=1, station="solid", timeout=30000, x_speed=400, item_type="plate")
    # 机械臂-放入9320
    # unilab:node_uuid=a259120f-4700-4087-899f-68792b0cd0a5
    step_122 = robot.place(number=1, station="nine9320", timeout=30000, x_speed=400, item_type="plate")
    # 9320协议A（Step2-4 震荡）
    # unilab:node_uuid=40507481-c883-4a02-b1ef-45b9afc5743c
    step_123 = prcxi.create_protocol(none_keys=[], protocol_date="", protocol_name="", protocol_type="", protocol_author="", protocol_version="", protocol_description="")
    # Step2: T16->T14
    # unilab:node_uuid=5470da9e-f659-4b98-a9a8-a1b72429b7c2
    step_124 = prcxi.move_plate(to=14, from_=16, force=5, plate=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", destination_offset={"x": 0, "y": 0, "z": 0}, intermediate_locations=[], pickup_distance_from_top=0)
    # Step2: T7 加水20ml->T14，枪头T15
    # unilab:node_uuid=64932934-d435-4d54-b68a-7e65f50c32be
    step_125 = prcxi.titration_liquid(offsets=None, sources=[{"uuid": "bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf"}], targets=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], asp_vols=[833], dis_vols=[833], none_keys=[], tip_racks=[{"uuid": "695548f2-3e97-4913-a2c5-e19b3b07cac9"}], is_96_well=True, repeat_count=2, use_channels=[], cycle_delay_s=10, liquid_height=None, asp_flow_rates=[300], dis_flow_rates=[300], post_air_volume=None, dispensing_method=["BottleNeck"], blow_out_air_volume=[20], hover_below_liquid_level=None, blow_out_air_volume_before=[], z_start_point_offset_height=None, post_discharge_pause_time_ms=None)
    # Step3: T14->T3
    # unilab:node_uuid=45ec77dc-31c9-4a95-bc13-4933c5c4feb7
    step_126 = prcxi.move_plate(to=3, from_=14, force=5, plate=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", destination_offset={"x": 0, "y": 0, "z": 0}, intermediate_locations=[], pickup_distance_from_top=0)
    # Step3: 震荡 500rpm 35°C 15min
    # unilab:node_uuid=fe267bc7-8210-45f6-bb87-e1ea5f3a366e
    step_127 = prcxi.shaking_incubation_action(time=900, is_wait=True, amplitude=500, module_no=1, temperature=35)
    # Step4: T3->T14
    # unilab:node_uuid=0a77d9e2-cf67-46f9-99e4-e7ff36da89f4
    step_128 = prcxi.move_plate(to=14, from_=3, force=5, plate=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", destination_offset={"x": 0, "y": 0, "z": 0}, intermediate_locations=[], pickup_distance_from_top=0)
    # Step4: T15->T16
    # unilab:node_uuid=0e18c09a-7614-4821-8702-9784eed40eb3
    step_129 = prcxi.move_plate(to=16, from_=15, force=5, plate=[{"uuid": "f260b8ba-a629-42ea-8340-548d765b4479"}], hierarchy=1, get_direction="", pickup_offset={"x": 0, "y": 0, "z": 0}, put_direction="", drop_direction="", resource_offset={"x": 0, "y": 0, "z": 0}, pickup_direction="", put_down_position=2, destination_offset={"x": 0, "y": 0, "z": 0}, pinch_it_up_position=2, intermediate_locations=[], pickup_distance_from_top=0)
    # unilab:node_uuid=548bb6e8-9797-4bad-857c-1b20040e94ab
    step_130 = prcxi.run_protocol(protocol_id=None)
    # unilab:node_uuid=210662b2-da91-4a71-a3cb-84553703a2ba
    step_131 = robot.pick(number=1, station="nine9320", timeout=180, x_speed=300, item_type="plate")
    # unilab:node_uuid=d1d5d67b-0409-4b6f-b0dd-0b9fbc02a895
    step_132 = robot.place(number=59, station="stack", timeout=180, x_speed=300, item_type="plate")
