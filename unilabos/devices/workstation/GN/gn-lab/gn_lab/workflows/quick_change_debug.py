"""从 legacy JSON 导入：快换模块单点调试（从复位到所有功能再到复位）.json。"""

from unilabos.workflow.authoring import device, workflow

from gn_lab.devices.gn_station_devices import RoboticArmDevice, QuickCarrierExchangeDevice

robot: RoboticArmDevice = device("gn_robotic_arm")
quick_change: QuickCarrierExchangeDevice = device("gn_quick_carrier_exchange")

@workflow(
    workflow_uuid="900f9f79-7dfb-42f2-89ce-f70e86d6b68c",
    displayname="快换模块单点调试（从复位到所有功能再到复位）",
    description="由 快换模块单点调试（从复位到所有功能再到复位）.json 转换，节点身份保持不变。",
)
def quick_change_debug() -> None:
    # 放入带玻璃管的板子
    # unilab:node_uuid=339db276-7b42-407e-b726-7eefe9c92707
    step_001 = robot.place(number=1, station="quick_change", timeout=180, x_speed=300, item_type="plate")
    # 快换-复位（起始）
    # unilab:node_uuid=1002b773-fded-4797-96ac-a5d160b0a5f0
    step_002 = quick_change.execute_command(x_pos=0, timeout=180, x_speed=0, cmd_type=14, push_pos=0, stir_rpm=0, z1_speed=0, z2_speed=0, z3_speed=0, stir_temp=0, top_z_pos=0, push_speed=0, push_z_pos=0, take_z_pos=0, stir_time_minutes=0)
    # unilab:node_uuid=1c06cf6d-315b-4473-8ed1-95c4ec7beae9
    step_003 = robot.pick(number=1, station="stack", timeout=180, x_speed=300, item_type="plate")
    # 顶出轴向上移动接收机械臂板子
    # unilab:node_uuid=2571c9eb-c6e0-4dec-82e1-177f1fabc13f
    step_004 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=80, stir_rpm=0, z1_speed=200, z2_speed=200, z3_speed=200, stir_temp=0, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=0, stir_time_minutes=0)
    # 推轴向右
    # unilab:node_uuid=9aa28d27-e66d-45f1-8d26-11c730109993
    step_005 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=80, stir_rpm=0, z1_speed=200, z2_speed=200, z3_speed=200, stir_temp=0, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=0, stir_time_minutes=0)
    # 接料轴下降
    # unilab:node_uuid=3902770f-9186-4836-aab5-201dafabfe0f
    step_006 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=80, stir_rpm=0, z1_speed=200, z2_speed=200, z3_speed=200, stir_temp=0, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=550, stir_time_minutes=0)
    # 物料顶出至接料轴
    # unilab:node_uuid=20ff596b-673a-408a-9a0b-d4c483da3784
    step_007 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=80, stir_rpm=0, z1_speed=200, z2_speed=170, z3_speed=200, stir_temp=0, top_z_pos=-30, push_speed=200, push_z_pos=0, take_z_pos=1010, stir_time_minutes=0)
    # 推轴向左
    # unilab:node_uuid=aea1703f-30d5-403c-a561-4b5b2e12b621
    step_008 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=0, stir_rpm=0, z1_speed=200, z2_speed=170, z3_speed=200, stir_temp=0, top_z_pos=-30, push_speed=200, push_z_pos=0, take_z_pos=1010, stir_time_minutes=0)
    # 顶出轴和接料轴到位置等待机械手放入板子
    # unilab:node_uuid=93b14d9e-8d7b-45ab-83cb-ea7b0fae57fd
    step_009 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=0, stir_rpm=0, z1_speed=200, z2_speed=200, z3_speed=200, stir_temp=0, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=150, stir_time_minutes=0)
    # 顶出轴和接料轴对接
    # unilab:node_uuid=5573915a-8734-466d-817c-9680b0b01674
    step_010 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=0, stir_rpm=0, z1_speed=200, z2_speed=200, z3_speed=200, stir_temp=0, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=550, stir_time_minutes=0)
    # 推轴向右
    # unilab:node_uuid=63bf4184-c630-4cfe-8301-cbfc4ee81cef
    step_011 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=80, stir_rpm=0, z1_speed=200, z2_speed=200, z3_speed=200, stir_temp=0, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=550, stir_time_minutes=0)
    # 物料转移至顶料轴上板子
    # unilab:node_uuid=439b34e3-100a-41d2-8b8f-c3aea826016a
    step_012 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=12, push_pos=80, z1_speed=200, z2_speed=200, z3_speed=200, top_z_pos=490, push_speed=200, push_z_pos=2000, take_z_pos=550)
    # 快换完成等待机械臂接收板子
    # unilab:node_uuid=2ebb6920-1daa-4c56-9fdc-fb0ce212caec
    step_013 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=12, push_pos=80, stir_rpm=0, z1_speed=200, z2_speed=200, z3_speed=200, stir_temp=0, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=550, stir_time_minutes=0)
    # 推料轴归0
    # unilab:node_uuid=fca79f3f-7345-47a3-ac5d-c90721b6c26a
    step_014 = quick_change.execute_command(x_pos=-20, timeout=180, x_speed=200, cmd_type=11, push_pos=0, z1_speed=200, z2_speed=200, z3_speed=200, top_z_pos=490, push_speed=200, push_z_pos=0, take_z_pos=20)
    # 快换-复位（结束）
    # unilab:node_uuid=28721c18-f389-49b3-bfb6-ec1fb41a696f
    step_015 = quick_change.execute_command(x_pos=0, timeout=180, x_speed=0, cmd_type=14, push_pos=0, stir_rpm=0, z1_speed=0, z2_speed=0, z3_speed=0, stir_temp=0, top_z_pos=0, push_speed=0, push_z_pos=0, take_z_pos=0, stir_time_minutes=0)
