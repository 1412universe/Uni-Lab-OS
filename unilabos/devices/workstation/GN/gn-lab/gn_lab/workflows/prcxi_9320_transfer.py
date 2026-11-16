from gn_lab.devices.gn_station_devices import CentrifugeTubeLiquidHandlingDevice
from gn_lab.devices.gn_station_devices import RotaryStackDevice
from unilabos.workflow.authoring import device, workflow, group, parallel, workflow_output


centrifugetubeliquidhandlingdevice: CentrifugeTubeLiquidHandlingDevice = device()
rotarystackdevice: RotaryStackDevice = device()


@workflow(
    workflow_uuid="8a15d439-59ab-43a3-928a-1aa8350d260f",
    displayname='9320移液',
    tags=[],
    meta_data={},
    description='由 9320移液.json 转换，节点身份保持不变。',
)
def prcxi_9320_transfer():
    with parallel():
        # [旋转堆栈]: 旋转堆栈
        # unilab:node_uuid=dddd0000-1111-4222-8333-444455556660
        with group(name='branch_1'):
            # [execute_command]: execute_command
            # unilab:node_uuid=04e262d6-8b59-4edc-8495-493425259992
            execute_command = rotarystackdevice.execute_command(cmd_type=2, r_pos=90, r_speed=20, timeout=120)
        # [离心管]: 离心管
        # unilab:node_uuid=dddd0001-1111-4222-8333-444455556661
        with group(name='branch_2'):
            # [execute_command]: execute_command
            # unilab:node_uuid=701b40cd-d010-4819-96cd-3a38ef8057fb
            execute_command_701b40cd = centrifugetubeliquidhandlingdevice.execute_command(cmd_type=36)
    return workflow_output()
