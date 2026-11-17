# sample-lab

Uni-Lab OS 设备包工作区。

开始开发前先填写 [设备包需求卡](DEVICE_PACKAGE_REQUIREMENTS.md)，不要猜测设备动作、
参数单位、连接地址或安全联锁。

## 交付信息

- 用途：待填写
- 负责人：待填写
- 适用的 Uni-Lab OS 版本：待填写

生产地址、账号和密钥不得写入源码或启动图。

## 自带示例

- `sample_lab/devices/demo_device.py`：无硬件副作用的示例驱动，展示设备和动作定义；
- `sample_lab/workflows/demo_workflow.py`：调用示例设备动作的最小工作流；
- `deployment/graphs/dry-run.json`：只装载示例设备，不包含真实连接参数。

先运行测试和 `package inspect` 确认示例可用，再以相同结构替换为真实设备合同。
不要把示例动作的成功结果当成真实设备验收结果。
