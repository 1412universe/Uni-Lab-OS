"""无硬件副作用的教学驱动；接入真实设备时请替换本文件。"""

from typing import TypedDict

from unilabos.registry.decorators import action, device


class EchoResult(TypedDict):
    success: bool
    echoed_text: str


@device(
    id="demo_device",
    category=["example"],
    displayname="示例设备",
    description="回显文本的无硬件副作用教学设备。",
    version="0.1.0",
    metadata={"hardware_side_effects": False},
)
class DemoDevice:
    """演示设备定义与动作实现之间的最小接口。"""

    @action(
        displayname="回显文本",
        description="原样返回输入文本，用于验证设备包调用链。",
        estimate_duration_fixed=1.0,
    )
    def echo(self, text: str) -> EchoResult:
        """回显输入文本，不连接或控制任何硬件。"""

        return {"success": True, "echoed_text": text}
