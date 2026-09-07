# 设备与动作

## 当前 Console 的入口边界

目标环境的内置 Console 没有独立“设备管理”或“单动作调试”页面。日常用户可以在总监控中查看运行概况，在工作流和任务中观察设备动作；设备目录与单动作 Task 由 API 或 Theia Workbench 提供。

这意味着“设备包里定义了动作”不等于“当前 Console 有一个按钮可以直接执行它”。对于真机动作，应优先使用经过发布、预检和资源锁保护的工作流。

设备开发者如果要创建自己的领域包，应先完成[开发一个可加载的实验室仓库](lab-repository.md)：该教程说明 `@device`、Graph `class`、实例 `id` 和 Workflow 选择器之间的真实加载关系。

## 当前在线节点

目标环境实测 Edge 已连接并报告 10 个在线节点：

| 在线节点 | 用户理解 | 主要作用 |
| --- | --- | --- |
| `host_node` | Host 协调节点 | 物料位置提交和主机侧协调。 |
| `szlab_control_flow_probe` | 软件控制流探针 | 验证条件、循环等无工艺副作用逻辑。 |
| `szlab_poly_plc` | PLC 网关 | OPC UA 连接、状态读取、写入和有限等待。 |
| `szlab_mixer_robot` | 机械臂 | 取放、转运、倒液和未知结果恢复。 |
| `szlab_mixer_stirrer` | S04 搅拌站 | 搅拌、温度、时长和物料感知动作。 |
| `szlab_mixer_photoshotting` | S05 拍照站 | 触发拍照工位动作。 |
| `szlab_mixer_pump` | S06 加液站 | 单/双溶剂加液和物料感知动作。 |
| `szlab_s07_solid_addition` | S07 固体加料站 | 扫码、称量、投粉和称量历史。 |
| `szlab_s08_cap_station` | S08 开合盖站 | 样品瓶与试剂瓶开盖、关盖。 |
| `szlab_mixer_pipetting_station` | S09 移液站 | 移液、吸头状态和密度测量流程。 |

S1 工作站虽然在设备包 Catalog 中有驱动定义，但没有实例化到当前活动 Graph，因此不是本环境的在线设备。

## 动作如何执行

一次标准设备动作遵循以下路径：

1. 工作流节点绑定设备实例和动作模板。
2. 创建任务时冻结节点参数和设备绑定。
3. 调度器取得设备、物料和工位锁后创建 Job。
4. Edge 拉取完整 Job 参数并调用设备驱动。
5. 设备持续返回 feedback，结束时返回成功、失败、取消或未知结果。
6. Scheduler 推进后继节点；物料转移动作在物理成功后唯一一次提交位置。

设备动作和 Task 使用幂等身份，防止网络重试产生第二次物理动作。不要通过重复提交新任务来代替恢复原任务。

## 单动作运行

如果必须单独验证设备动作：

- 使用连接到 Python Local Profile 的 Theia Workbench“设备”页，或由管理员使用设备动作 Task API；
- 核对设备、动作、材料绑定和参数；
- 只在现场安全条件成立、设备允许远程自动运行时提交；
- 在任务页观察 feedback 和结果，必要时取消；
- 不要使用已退役的 `/api/v1/debug/*` 接口。

## SZLab 设备的已知边界

| 设备 | 代码中的实际边界 | 使用影响 |
| --- | --- | --- |
| S05 拍照 | 相机 URL 获取当前返回空字符串；算法集成与双视图方法不是公开动作。 | 可以验证工位动作链路，不能把 `photo_path` 或检测结果当作已接通的真实图像算法结果。 |
| S06 加液 | `skip_level_check`、`skip_robot` 等兼容参数在公开实现中被丢弃。 | 不要把这些字段当作可用安全开关。 |
| S07 投粉 | 底层可生成称量历史文件；工作流公开输出是下发质量。 | `commanded_powder_mass_g` 表示指令值，不是最终实测质量。 |
| S08 开合盖 | 工位状态和瓶盖约束校验当前被关闭，未从 Graph/UI 传入。 | 运行前需现场确认容器和盖状态。 |
| S09 移液 | 工位 bind/release 是关闭的占位；秤稳定标志未使用。 | 密度/移液流程仍需按设备反馈和现场规程复核。 |
| 机械臂 | 跨重启的在途命令会变成 `UNKNOWN`，不会自动重发。 | 必须先现场核对，再用原任务的恢复流程解决。 |

:::{danger}
强制释放设备锁只改变调度器的逻辑占用，不会停止机械运动，也不能证明设备已经自然结束。必须先执行现场停机/确认，核对精确 holder 和 fencing 信息，并填写审计原因。
:::

## PLC-Sim 的作用

PLC-Sim 可以模拟 OPC UA 命令、动作过程、传感器变化、延时、超时和故障；仓库还提供独立的 Modbus TCP/RTU/ASCII 模拟器。它适合离线联调，不负责工作流调度、物料权威状态或机器人运动安全。仿真通过不能代替真机的 IP、NodeId、联锁、急停、工位、容量和恢复语义验收。

当前环境的 Web GUI、Server/Agent 检查、在线变量观察和本地接入步骤见[PLC-Sim 仿真器](plc-sim.md)。

<div class="evidence">
<strong>实现依据</strong>：目标环境 <code>/api/v1/online-devices</code> 实测；<code>Uni-Lab-SZLab/szlab_poly_studio/devices/</code>（设备实现）；<code>common/plc_gateway.py</code>（PLC 调用路径）；<code>common/action_logging.py</code>（动作日志与脱敏）；<code>devices/szlab_mixer_robot/standard_gateway.py</code>（幂等 journal 与 UNKNOWN 恢复）；<code>PLC-Sim/README.md</code> 与 <code>PLC-Sim/PLC-Sim/</code>（协议仿真边界）。
</div>
