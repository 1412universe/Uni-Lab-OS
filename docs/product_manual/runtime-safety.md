# 运行模式、安全与恢复

## 开发模式与产品模式

| 模式 | 用途 | 调度行为 | 可用控制 |
| --- | --- | --- | --- |
| `develop` | 新流程逐步验证 | 同一时间只接受一个开发任务 | 普通/step、暂停、选择下一节点、继续、取消 |
| `product` | 已发布流程连续运行与并行调度 | 允许多个任务并存，按资源约束仲裁 | 普通运行；不提供单步调试 |

当前目标环境实测为 `develop`。模式切换只影响之后创建的任务，不迁移已有任务。系统会在有活动任务、未满足条件或其他阻塞项时拒绝切换。

:::{note}
“产品模式”描述调度行为，不代表部署自动获得生产安全、鉴权、TLS 或真机验收。当前公网演示环境仍应按授权测试系统对待。
:::

## 任务为何能恢复

创建任务时，系统会冻结工作流修订、执行图、节点参数和设备绑定，并为任务维护持久状态。恢复依赖以下机制：

- 幂等任务/命令身份，防止网络重试重复创建物理动作；
- 设备、物料和工位执行锁；
- 试剂数量预留和消耗台账；
- Scheduler、Workspace 和 Edge 的持久快照/日志；
- Edge 回连、命令反馈和未知结果解决；
- 人工确认、干预、转移结算和强制解锁的审计原因。

浏览器页面不是运行权威。关闭或刷新 Console 不会取消任务；重新打开后应从服务端读取最新状态。

## 资源锁与库存预留

调度器只在节点的所有条件满足时派发：

- 前置节点完成；
- 设备在线且可派发；
- 所需设备、物料和站点没有被其他任务占用；
- 材料来源唯一且位置匹配；
- 试剂数量已成功预留；
- 人工确认或干预已经完成。

锁与预留用于阻止逻辑冲突，但不能代替现场联锁。不要通过手工改库位、删除库存或强制释放来消除正常等待。

## 物理动作与逻辑事实

请始终区分三类事实：

1. **计划事实**：工作流希望物料到达哪里、设备执行什么动作。
2. **软件事实**：Job、feedback、返回值、锁和库存台账记录了什么。
3. **现场事实**：物体实际位置、夹爪负载、容器盖状态、液位、秤读数和设备运动状态。

正常情况下三者应一致。发生超时、断线、重启或人工介入时，必须先确认现场事实，再用转移结算、UNKNOWN 解决或干预入口让软件事实收敛。不要用计划事实猜测现场。

## 机械臂未知结果

SZLab 机械臂网关会先持久化命令再下发，并按 `command_id` 精确重放。跨重启仍处于 in-flight 的命令会进入 `UNKNOWN`，系统不会自动再次运动。UNKNOWN 还会阻止新的机械臂运动，直到操作员完成只读对账并提交带原因的解决决定。

这种 fail-closed 行为是为了避免“原动作已经完成，但网络没有返回”时重复取放或倒液。

## 安全停机与排空

管理员需要维护或升级运行时，应先进入 scheduler drain：

1. 停止派发新的设备 Job；
2. 等待已在途 Job 在限定时间内结束；
3. 核对没有未处理的 manual confirmation、UNKNOWN 或物料结算；
4. 再停止 Edge/Workspace；
5. 启动后先检查 Readiness、设备数量和库存，再恢复派发。

当前 SZLab Kubernetes 清单的终止流程最多等待约 60 秒进入持久恢复，Pod 终止宽限为 90 秒。不要以旧文档中的 600/660 秒作为当前事实。

## 日志、Trace 与审计

- 任务详情是用户第一入口；
- SigNoZ Trace 关联 Scheduler 与 Edge；
- SZLab 动作日志记录 START/SUCCESS/FAIL、耗时和关键阶段，并对 password、token、authorization、access key 等字段脱敏；
- 库存历史记录试剂和物料变化；
- 恢复操作必须保留原因、期望修订或唯一 resolution ID。

Trace 系统采用 fail-open：可观测性服务故障不应直接阻止工艺执行。因此“没有 Trace”不等于“任务没有运行”，需要结合任务和 Edge 状态判断。

## 公网与权限

当前代码的 Backend/Console 没有通用用户认证中间件，`edge_key` 也只是稳定身份而不是秘密。面向生产公网时应：

- 只公开受控的文档/Console 入口；
- 在反向代理或 Ingress 层启用 HTTPS 和身份认证；
- 限制写 API、Swagger、管理和恢复接口；
- 保持 PLC、Scheduler、OTLP、数据库和 Edge 控制通道在集群内；
- 使用短期、可撤销凭证并记录访问审计。

本次部署只公开静态说明书 NodePort，没有改变 Runtime、Edge 或 PLC-Sim 的现有网络策略。

<div class="evidence">
<strong>实现依据</strong>：<code>frontend/src/components/StartupModeDialog.tsx</code> 与 <code>AppShell.tsx</code>（模式门禁）；<code>unilabos/workflow/execution_plan.py</code>、<code>execution_lock_lease.py</code> 与 <code>task_runtime_projection.py</code>（冻结、锁和恢复）；<code>Uni-Lab-SZLab/devices/szlab_mixer_robot/standard_gateway.py</code>（UNKNOWN）；<code>deployment/kubernetes-docker-desktop/edge-namespace/stack.yaml</code>（drain/termination）；<code>uni-lab-backend/internal/web/router.go</code>（服务中间件边界）。
</div>
