"""调用示例设备动作的最小 Uni-Lab OS 工作流。"""

from typing import TypedDict

from sample_lab.devices.demo_device import DemoDevice
from unilabos.workflow.authoring import device, workflow


class DemoWorkflowResult(TypedDict):
    success: bool
    echoed_text: str


demo_device: DemoDevice = device("demo_device_01")


@workflow(
    workflow_uuid="f0e636c3-021e-5833-a9d5-da041f66fd8a",
    displayname="示例设备回显",
    description="调用示例设备动作并返回结果。",
)
def run_demo(*, text: str = "Hello Uni-Lab OS") -> DemoWorkflowResult:
    # unilab:node_uuid=b7359298-b5f1-55ba-86af-4de4b18252ee
    echoed = demo_device.echo(text=text)
    return {
        "success": echoed.success,
        "echoed_text": echoed.echoed_text,
    }
