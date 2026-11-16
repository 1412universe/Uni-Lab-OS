from gn_lab.devices.gn_station_devices import LockingMechanismDevice
from unilabos.workflow.authoring import device, workflow, workflow_output


lockingmechanismdevice: LockingMechanismDevice = device()


@workflow(
    workflow_uuid="b16d9e95-8f38-4f3f-9647-a152bf0c40e1",
    displayname='锁紧模块单点调试',
    tags=[],
    meta_data={},
    workflow_type='normal',
)
def 锁紧模块单点调试():
    # unilab:node_uuid=518989c9-c492-411f-9ec5-78808b40fa88
    execute_command = lockingmechanismdevice.execute_command(cmd_type=16, jaw_force=0, jaw_position=0, timeout=180, x_pos=338, x_speed=300, y_pos=307, y_speed=300, z1_pos=0, z1_speed=300, z2_pos=1060, z2_speed=300)
    # unilab:node_uuid=419d6ca0-261d-47cb-9c42-928c6611dbe8 disabled=true
    execute_command_419d6ca0 = lockingmechanismdevice.execute_command(cmd_type=9, jaw_force=0.1, jaw_position=30, timeout=180, x_pos=3700, x_speed=500, y_pos=880, y_speed=500, z1_pos=0, z1_speed=0, z2_pos=935, z2_speed=500)
    # unilab:node_uuid=59582810-9d40-42eb-8401-7c3af19dd7f2 disabled=true
    execute_command_59582810 = lockingmechanismdevice.execute_command(cmd_type=10, jaw_force=0.1, jaw_position=11, timeout=180, x_pos=757, x_speed=500, y_pos=730, y_speed=500, z1_pos=0, z1_speed=0, z2_pos=965, z2_speed=500)
    # unilab:node_uuid=747d707b-de51-4952-b217-0ed577f63e60 disabled=true
    execute_command_747d707b = lockingmechanismdevice.execute_command(cmd_type=9, jaw_force=0.1, jaw_position=65, timeout=180, x_pos=2290, x_speed=500, y_pos=1010, y_speed=500, z1_pos=0, z1_speed=500, z2_pos=885, z2_speed=500)
    # unilab:node_uuid=720d1550-0486-4c6d-9d3d-8542d7377ee6 disabled=true
    execute_command_720d1550 = lockingmechanismdevice.execute_command(cmd_type=10, jaw_force=0.1, jaw_position=65, timeout=180, x_pos=757, x_speed=500, y_pos=730, y_speed=500, z1_pos=0, z1_speed=500, z2_pos=965, z2_speed=500)
    # unilab:node_uuid=60aee863-1b58-49d3-bf58-82cd34b956f1 disabled=true
    execute_command_60aee863 = lockingmechanismdevice.execute_command(cmd_type=12, jaw_force=0, jaw_position=0, timeout=180, x_pos=338, x_speed=500, y_pos=307, y_speed=500, z1_pos=1060, z1_speed=500, z2_pos=0, z2_speed=500)
    # unilab:node_uuid=3d81a69f-2a42-432d-a0bc-5b5a99842115
    execute_command_3d81a69f = lockingmechanismdevice.execute_command(cmd_type=15, jaw_force=1, jaw_position=2, timeout=180, x_pos=204, x_speed=500, y_pos=792, y_speed=500, z1_pos=1063, z1_speed=500, z2_pos=0, z2_speed=500)
    return workflow_output()
