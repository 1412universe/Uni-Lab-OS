# SZLab 选择来源库位工作流完整流程验证

日期：2026-09-07（Asia/Shanghai）。分支：`product/durable-scheduler-kernel-v3`。

工作流：`single_sample_atomic_attachment_robot_atomic_select_sites`，UUID：`21c7b6d8-2938-43fd-9db7-4f8db24a6dca`。

## 正常执行路径

使用 `unilab` mamba 环境，通过正式 `DomainBackendClient.create_task` 提交 normal 任务，经过 SZLab Local Backend、调度器、Edge 控制协议、ROS 驱动及 OPC UA，连接本地 Kubernetes `edge/plc-sim`。进程使用 `action_mode=real`，设备端点为 PLC 模拟器。没有直接调用驱动、伪造节点成功或修改运行数据库。

| 检查 | 第一轮 | 第二轮（保留库存重启后） |
| --- | --- | --- |
| Task UUID | `d75fbc2f-56a7-4846-93bc-674fa6c4d6f3` | `6c8a03a6-10ea-4f1f-9635-ac8fcee471fc` |
| 终态 | succeeded | succeeded |
| 成功节点 | 35/35 | 35/35 |
| 创建至终态 | 110.26 秒 | 112.12 秒 |
| 设备作业 Claim | 25，全部 released | 25，全部 released |
| 执行锁 Lease | 153，全部 released | 153，全部 released |
| PLC 握手 | 45 accepted / 45 completed / 45 reset | 45 accepted / 45 completed / 45 reset |
| 拍照和开盖运行交集 | 0.205 秒 | 0.176 秒 |
| SigNoz span | 427，无错误标记 | 427，无错误标记 |

覆盖来源准入、烧杯搬运、加粉、双路加液、试剂瓶开盖、移液、关盖归还、搅拌、测密度、拍照与样品瓶开盖并行、倒液、成品关盖、S11 中转、烧杯和成品瓶回到来源位置。

## 物料与终态

两轮使用同一组来源：烧杯 L1B2、样品瓶 L1A2、粗粉 P03、细粉 P04、试剂瓶 R1C2。五个物料 UUID 不变，父资源及实际 Site 占用均回到来源位置；机械臂 GRIPPER 的 occupied_material_uuid 为 null。

每轮三笔消耗分别为 10 mL、10 mL、0.5 mL，实际扣减与计划一致。任务预留为 5 released / 2 consumed，无活动预留；执行锁 API 返回空列表，设备托管为 0。只读查询另行确认 Claim/Lease 全部 released。

输出包含完整物料引用、目标粉量 1 g、模拟密度 1 g/mL、检测结果 OK。photo_path 为空，本次不代表实物成像质量或真实计量准确性验证。

## 实际发现及修复

初次任务 `d4255a95-7ffd-4577-a0c5-f4084d7ac181` 在物料准入后被计划外资源检查拦截，未生成设备 Claim，已通过正式取消命令清理。

修复补齐首次派发前的库存资源绑定：沿冻结透传合同传播来源物料身份，从库存权威取得仓库父设备和夹爪库位，在事务内持久化完整资源计划。下游业务参数仍经原有数据依赖传递，已绑定计划重放不重新绑定。

原子搬运完成物理结算后作为默认锁的安全释放边界，避免自动跨工站持锁形成循环取得关系；显式区域作用域和独立 pick/place 的持料语义保留。计划外资源检查及 UUID 校验继续生效。

最终回归：OS 工作流、调度、库存及资源合同测试 **1178 passed / 4 skipped**；SZLab 相关测试 **36 passed**。新增测试覆盖跨动作物料身份、夹爪锁、冻结幂等性和显式作用域保留。

## 证据与复查

- run-1/、run-2/：任务、节点、库存、消耗、运行事件、PLC 事件、Trace span、并行交集。
- claim-audit.json：失败初次提交及两轮成功任务的 Claim/Lease 统计。
- s03-sites.json、s07-sites.json、s10-sites.json、robot-sites.json：最终库位占用回执。
- core-tests.log、szlab-tests.log：最终回归输出。
- verify_full_flow.py：只读复核脚本，参数为任务 UUID 和证据输出目录。

SZLab 控制台：<http://127.0.0.1:51803/console/?page=tasks>。未清空库存或历史任务，模拟消耗记录保留。
