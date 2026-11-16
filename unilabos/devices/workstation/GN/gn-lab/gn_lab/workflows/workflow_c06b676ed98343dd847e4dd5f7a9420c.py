from gn_lab.devices.gn_station_devices import RotaryStackDevice
from unilabos.workflow.authoring import device, workflow, workflow_output


rotarystackdevice: RotaryStackDevice = device()


@workflow(
    workflow_uuid="c06b676e-d983-43dd-847e-4dd5f7a9420c",
    displayname='demo',
    tags=[],
    meta_data={},
    workflow_type='normal',
)
def demo():
    # [execute_command]: execute_command
    # unilab:node_uuid=3f205ffb-da25-4d36-abb4-959e39e49fcc
    step_001 = rotarystackdevice.execute_command(cmd_type=1, r_pos=30, r_speed=10, timeout=120)
    return workflow_output()
