# 第二轮资源锁复审记录

日期：2026-09-06。基准：`213e5190` 与当前未提交工作区；同前轮两个用户文档。按 code-review 技能并行核查 Standards / Spec，并持续增加失败回归、修复和复验。以下记录替代前一轮的测试计数，历史证据保留。

## 当前结论

本轮已证实的资源计划、调度、租约和验证脚本缺陷已修复，相关主回归通过。**尚不能宣称整个工作区“没有问题”**：扩展检查中的两项原有人工强制解锁测试仍失败，涉及尚未明确的恢复合同，已向用户提问。没有删除、改写或标记跳过这两项测试。

## Standards

- 为新编译/运行辅助函数补齐类型标注，移除已被替代且无调用的旧连续算法、旧查环函数与未使用参数。
- 合并首次准入与重新准入的冲突检查、物理前置条件检查及 fence 选择，防止两个入口再次分化。
- 修复实测脚本过早写 passed：现在必须所有清理、连接关闭、OTel flush 成功后才写通过。异常会记录 failed。覆盖正常完成、flush 失败、PLC 连接失败。
- 本轮未留下已确认的硬性规范违规。代码风格启发式不作为功能通过的替代依据。

## Spec

| 修复项 | 回归覆盖 |
|---|---|
| 并行区间的释放边界不能取单个拓扑尾节点，否则漏检真实循环等待 | 所有相关区间成员必须有依赖证明已经结束，新增漏检反例 |
| 已由共同前驱原子取得的资源不应再次构造反向取得关系 | 双重根/词法范围并行分支不再误报环 |
| 同实例不同角色和别名必须与运行时使用同一资源身份 | device/motion/tool 合并、绑定前符号环延后到真实绑定验证、搬运描述符别名递归修复 |
| pick 到 place 的载运资源不能中途断开 | 完整配对区间检查，拒绝 pick→move→无关动作→place 中途放锁 |
| 实际参数不能使用冻结计划以外的设备 | v2 设备/导轨/工具参数进入实际锁需求；参数与绑定不一致拒绝；纯静态 transfer 角色仍通过资源绑定取得 |
| 缺失区间投影及无法映射到库存的资源不能静默丢弃 | 缺失投影、额外设备、不可表示资源键均拒绝派发 |
| 已冻结物料需求和参数物料必须进入静态集合 | 与实际动作相同 Schema 规则提取已知物料，保持冻结注册表语义 |
| 第二轮、外层及嵌套循环不能误用别轮完成状态 | 真实持久化两轮调度、外层根范围和嵌套归属测试 |
| 部分交接不能要求旧 Claim 全部资源同时结束 | 只交接本次资源；A 完成唤醒 A 等待者时 B 仍保持占用，库存/镜像一致 |
| 共享范围重准备不能阻塞自己或刷新仍在运行分支的 fence | 共用准入判断、继承相同 fence；不允许借 preheld 绕过 uncertain 租约 |
| 重准备也必须重新检查原位操作等物理前置条件 | 物料已经离开时拒绝重准备，事务不产生部分凭据 |
| 并行失败/取消的完成顺序不能造成旧占用残留 | 两种顺序、未知结果保留及迟到确定回执；父任务终态后仍接受在途分支结算 |
| 确定失败后未启动的后继不能使普通资源永久占用 | pending / 未实例化后继在终止任务中视为不再执行；实际在途与未知命令仍阻塞释放 |
| 失败/跳过/取消的 place 不是物理交接成功 | 仍持料时保留资源；终态批量清理也不得提前抹掉镜像，标记 resource_handoff_required |

主要新增回归：

- `tests/scheduler_core/test_resource_plan_safety_contract.py`
- `tests/scheduler_core/test_resource_plan_dispatch_review.py`
- `tests/scheduler_core/test_resource_interval_partial_cleanup.py`
- `tests/scheduler_core/test_shared_scope_runtime.py`
- `tests/app/test_resource_handoff_regressions.py`
- `tests/validation/test_resource_lock_plc_live.py`

## 最终实测

主回归 **1137 passed, 4 skipped, 2 warnings，48.11 秒**。四项跳过是当前 macOS 不适用的原生 Windows 文件发布测试，两项警告为 lark 依赖弃用。见 [pytest.log](pytest.log)。命令：

```bash
/Users/xiongyanfei/.local/share/mamba/envs/unilab/bin/python -m pytest -p no:launch_testing -p no:launch_ros -q tests/workflow tests/scheduler_core tests/app/test_inventory_dispatch_admission.py tests/app/test_resource_handoff_regressions.py tests/registry/test_action_resource_contract.py tests/validation/test_resource_lock_plc_live.py
```

最新代码再次连接本地 Kubernetes edge PLC Sim：**6 次真实 OPC UA 动作、4 个工作流任务全部成功、最终 active claims=0**。验证连续动作防插队以及共同范围 S06/S07 并行，实际完成位返回后才结算。见 [plc-live.json](plc-live.json)。

SigNoz 实际存储了 **4 个 workflow.task.run、6 个 action.run、6 个 workflow.job.dispatch，error_spans=0**。查询窗口为最终运行对应 Unix 时间 1788692219 至 1788692235。见 [signoz-traces.json](signoz-traces.json)。

测试脚本使用实际持久化调度器、库存事务和真实 OPC UA 边界，仍不等于生产 ROS 驱动及所有业务装板工艺已经现场验收。前轮报告的现场业务验收边界仍有效。

## 尚待确认：人工强制解锁

扩展执行 `tests/app/test_manual_resource_unlock_api.py` 时两项失败，原因是现有 API 不支持 `unlock_resources`。完整输出见 [manual-unlock-review.log](manual-unlock-review.log)，其中另外四项为已纳入主回归的库存测试。

原测试直接把任务/作业数据库改为 failed/uncertain，调度器仍有在途命令、库存仍有 running Claim；随后要求仅凭 confirmed_physical_safe=True 就移除所有占用并让竞争任务派发。现有恢复入口要求确定停止证据，搬运另需实际位置对账。两者是不同恢复合同，不能通过悄悄删除在途保护来使测试转绿。

已向用户列出选择：要求停止证据和必要的位置对账，或提供带完整审计的操作员强制解除。收到明确选择后才实现该命令的最终语义。本轮未触碰原测试文件。
