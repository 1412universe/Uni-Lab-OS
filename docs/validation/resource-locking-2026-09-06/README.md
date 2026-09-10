# 工作流资源锁实现与实测记录

> 最新复审及剩余人工解锁问题见 [第二轮复审记录](review/README.md)。

核查日期：2026-09-06。基于本地 Uni-Lab-OS 的 `213e5190` 加本次工作区修改。需求来源为用户提供的 `workflow-resource-locking-design.md` 和 `workflow-resource-locking.md`；文档中的示例与操作说明只作为需求依据。

## 结论

原代码没有完整实现两个文档的资源锁合同。本次修复了连续区间、join、共同外层并行、整站环检测、拆分搬运预留和持久租约交接等关键缺口，并完成下面列出的实际验证。不能据此宣称两个文档所有业务场景均已验收；尤其不能把普通动作完成位当作任意物理状态安全交接的证明。

## 实际结果

- mamba `unilab` 的 Python 3.11：**1096 passed, 4 skipped**，55.94 秒。四项跳过是原有 Windows 原生文件发布测试，在当前 macOS 不适用；另有两条 lark 依赖弃用警告。完整输出见 [pytest.log](pytest.log)。
- 本地 Kubernetes `edge` 的 PLC Sim：**6 次真实 OPC UA 命令、4 个持久工作流任务全部 succeeded，最终库存 active claims 为 0**。见 [plc-live.json](plc-live.json)。
- 实测一：同一 S06 的两个连续动作之间，高优先级等待任务不能插入。
- 实测二：共同根范围内的 S06/S07 在任一完成前均已派发；外部竞争任务等两者实际完成之后才派发。两次派发间隔约 14 ms。
- 完成依据为 PLC Sim `加工完成/工艺完成` 状态，读取明确完成后才回报调度器；不是按预计时长直接报告成功。
- SigNoz ClickHouse 已查到本次服务 `unilab-resource-lock-plc-validation` 的 **4 条工作流 trace、6 个 action.run、6 个 workflow.job.dispatch，error_spans 为 0**。见 [signoz-traces.json](signoz-traces.json)。

## 代码修复与需求对应

| 文档合同 | 本次实现及验证 |
|---|---|
| Action 默认资源、根/词法资源、同实例别名合并 | 执行设备默认加入计划；合并附加资源而非覆盖；规范资源绑定后重新计算占用区间。模板和执行计划测试通过。 |
| 默认连续持锁跨 join | 沿依赖图计算连续区间；join 等待期间持有；竞争兄弟需要同资源时解除安全默认连续区间，持料情形拒绝。持久调度测试通过。 |
| 显式边界、同路径嵌套、兄弟局部范围 | 显式边界保留；同路径重叠范围合并，局部兄弟所有权分离。编译和调度测试通过。 |
| 共同外层范围中的并行 | 同外层范围可由不同设备并行继承；同一设备仍串行。共享 fence 不让第二分支使第一分支完成凭据失效，全部分支结束才释放。两种完成顺序测试及 PLC Sim 实测通过。 |
| 顺序敏感兄弟 | 支持冻结合同 order_sensitive；无显式依赖的同资源敏感动作拒绝。 |
| 整站静态无环 | 提交时合并在途 bound 计划；保留跨三个以上工作流的中间资源关系；依赖证明不能重叠的反向关系不误报；输出来源诊断。 |
| 绑定变化 | 绑定内容进入 plan_id；同一真实实例的不同别名重新编译，防止错误保留两份所有权。 |
| pick/place 配对及完整集合 | 新增 transfer_step 参数映射，按物料匹配唯一 place，pick 前加入目标设备、目标 Site、搬运器和运动资源。缺少唯一目标或实际 transfer 结算合同则拒绝。 |
| Site 与设备占用独立、整组取得 | 在库存同一事务检查最终目标空闲并取得完整资源；任一目标不满足时全部回滚。不能用设备锁代替 Site 检查。 |
| 来源提前释放、物料真实位置 | 持久库存测试验证 source → gripper → target，pick 后保留目标与搬运器占用，来源可释放；place 仅接受本物料已在对应 gripper 的事实。 |
| 旧 occupancy 计划 | 在存储和 spec 编译入口重新编译迁移，不信任输入的 static_acyclic 布尔值。 |
| 循环、恢复和完成清理 | 循环实例映射到模板区间；恢复连续持有者；按全部区间成员的实际终态决定清理，避免并行尾部先结束导致早放锁或泄漏。 |

主要新增用例位于 `tests/scheduler_core/test_resource_plan_safety_contract.py`、`test_shared_scope_runtime.py`；持久库存覆盖在 `tests/app/test_inventory_dispatch_admission.py`。本次保留了原工作区已有的 ADR 与未跟踪测试。

## 验收边界和仍需补齐的范围

1. 实际 PLC 验证使用真实调度器、持久工作流库及库存权威，通过 CallbackDispatcher 接到已部署 PLC Sim；没有经过生产 ROS 驱动的全套接线、Web UI 上传和实际机械臂。模拟工位完成信号的成功不能代替那些路径的验收。
2. 拆分 pick/place 的物料位置和 Site 预留已通过真实 SQLite 事务及结算测试；本次没有让 PLC Sim 执行一条包含实际夹爪物料传感器反馈的完整搬运工艺。
3. 文档列举的开盖、对位、临时工具、运动区等一般安全交接状态，仍依赖动作显式声明 physical_hold_resources / 外层范围及驱动事实；当前没有能自动推断所有这些物理状态的通用证明系统。未声明的物理责任不能仅凭普通成功回执判定已满足文档第 6 节。
4. 新执行计划入口会生成设备资源计划；仍兼容没有 resource_plan 的旧 WorkflowSpec。不能把这一兼容入口也宣称为已完成整站静态证明。资源参数需在编译时提供可解析的 resource_bindings；任意任务输入到资源绑定的全自动推导没有作为本次完成项。
5. 对位板、四板装入、提前转位、离心配平及拍照刮板整套业务示例没有逐条在 PLC Sim 跑过。因此“两个文档全部功能验收通过”的结论仍不成立。

## 复现

在仓库根目录执行：

```bash
/Users/xiongyanfei/.local/share/mamba/envs/unilab/bin/python -m pytest -p no:launch_testing -p no:launch_ros -q tests/workflow tests/scheduler_core tests/app/test_inventory_dispatch_admission.py tests/registry/test_action_resource_contract.py
```

两个 ROS pytest 插件会将无关目录当作 launch 测试收集，故本轮显式禁用。使用的解释器就是用户允许的 mamba 环境；未把系统 Python 的结果当作该环境结果。

PLC Sim 复现需要两个独立终端保持端口转发：

```bash
kubectl -n edge port-forward svc/plc-sim 24855:4855
kubectl -n edge port-forward svc/signoz-otel 24317:4317
```

然后在仓库根目录执行（会实际操作模拟器 S06/S07）：

```bash
PYTHONPATH="$PWD" /Users/xiongyanfei/.local/share/mamba/envs/unilab/bin/python scripts/validation/resource_lock_plc_live.py --endpoint opc.tcp://127.0.0.1:24855/xuse_sim/ --otel-endpoint http://127.0.0.1:24317 --output /tmp/resource-lock-plc-live.json
```

S06/S07 工艺号使用 1。复位需要同时清除参数写入完成及工艺号；首次探索中发现只清除参数标志不足以复位 S07，修正握手后重新跑完整场景并通过。本记录只引用最终完整通过的那次运行。
