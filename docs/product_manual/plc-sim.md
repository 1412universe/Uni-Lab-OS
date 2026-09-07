# PLC-Sim 仿真器

PLC-Sim 用一组可写的 OPC UA 节点模拟 SZLab PLC，并由握手代理把设备命令推进为接受、完成、复位、超时或故障结果。完成本页后，你可以打开当前环境的 PLC-Sim、判断仿真链路是否就绪，并在自己的本地环境中把它接到 Uni-Lab OS。

## 当前环境入口

<div class="entry-links">
<p><strong>PLC-Sim Web GUI</strong>：<a href="http://115.190.137.109:30160/">打开 OPC UA 仿真控制台</a></p>
<p><strong>PLC-Sim 健康检查</strong>：<a href="http://115.190.137.109:30160/api/health">查看 GUI 服务健康状态</a></p>
<p><strong>Uni-Lab OS Console</strong>：<a href="http://115.190.137.109:30183/console/">运行和观察 SZLab 工作流</a></p>
</div>

截至 2026-09-07，目标环境的部署状态为：

| 项目 | 当前事实 |
| --- | --- |
| Web GUI | `http://115.190.137.109:30160/`，<span class="status status-ready">当前可用</span> |
| PLC-Sim 版本 | `0.2.6` |
| 公网 OPC UA | `opc.tcp://115.190.137.109:30161/xuse_sim/`，只供获准的协议客户端联调 |
| 集群内 Graph 地址 | `opc.tcp://plc-sim:4855`，供当前 Edge Runtime 使用 |
| OPC UA Server | 正在运行，加载 1,591 个变量 |
| 握手代理 | 正在运行，`profile=szlab` |
| Edge 连接 | 已建立 Activated OPC UA Session |

这些是检查时刻的状态。每次运行工作流前，仍应重新查看 GUI 顶部的 OPC UA 和握手代理状态。

:::{danger}
当前 GUI 和 OPC UA 都通过明文端口公开，没有登录、TLS 或操作权限隔离，而且正在承载共享的 SZLab 仿真。普通用户只能观察状态、连接和变量；不要点击“停止服务”“停止代理”，不要修改在线变量，也不要切换节点模型、端口或代理配置。任何这类写操作都可能让正在运行的工作流失败或产生误导性的设备结果。

公网 OPC UA 当前允许匿名、`NoSecurity` 连接。它只能用于获准的测试客户端，不应承载敏感数据，也不能被当作生产环境的安全配置范例。
:::

## 它在工作流链路中的位置

```text
Workflow Task
     │
     ▼
Workspace Backend 内置 Scheduler
     │ 派发 Job
     ▼
Edge Runtime ──► SZLab Driver ──OPC UA──► PLC-Sim Server
     ▲                                      │
     └──────── feedback / result ◄── SZLab Handshake Agent
```

PLC-Sim 只模拟 PLC 节点、设备握手、动作时序和部分传感器事实。工作流定义、DAG 推进、设备/物料/工位锁、库存预留和任务恢复仍由 Uni-Lab OS 负责。

还要区分两种“模拟”：

| 方式 | Edge 行为 | 是否使用 PLC-Sim |
| --- | --- | --- |
| `runtimeMode=dry-run` | 不构造设备 Driver，直接返回模拟动作结果 | 否 |
| `runtimeMode=normal` + 仿真 Graph | 构造真实 Driver，但 Driver 的 OPC UA 地址指向 PLC-Sim | 是 |

因此，当前 SZLab 演示环境虽然显示 `action_mode=real`，实际 PLC 端点仍是 PLC-Sim。这里的 `real` 表示“经过真实驱动调用链”，不表示已经连接物理 PLC。

## 在共享环境中查看 PLC-Sim

### 1. 打开仿真控制台

打开 [PLC-Sim Web GUI](http://115.190.137.109:30160/)，点击“仿真服务”（页面标题为“OPC UA 仿真”）。顶部状态区应同时显示：

- OPC UA：运行中；
- 握手代理：运行中。

如果页面无法打开，先访问[健康检查](http://115.190.137.109:30160/api/health)。返回 `{"ok": true}` 只证明 GUI 进程可用，不能代替下面的 Server、Agent 和客户端连接检查。

### 2. 核对 OPC UA Server

在“OPC UA Server”卡片只读核对：

- 节点模型为“CSV / SZLab”；
- 变量表为 `data/szlab_plc_0810.csv`；
- 监听端口为 `4855`；
- 命名空间索引为 `4`；
- 命名空间 URI 为 `urn:xuse:sim`；
- 页面显示的当前端点已经启动。

页面中的 `127.0.0.1:4855` 是 PLC-Sim Pod 内部端点。浏览器访问使用 `:30160`；集群内 Edge 使用 `plc-sim:4855`。不要把 HTTP GUI 地址填进 OPC UA Graph。

### 3. 核对 SZLab 握手代理

在“握手代理”卡片确认代理类型为“SZLab 设备包仿真”且状态为运行中。当前实现以 package mode 常驻 Robot、S04–S09 和 S1 适配协议；“初始场景”只决定启动时的兼容状态，不会按某一个工作流裁掉其他协议。

共享环境不需要你重新选择场景或点击启动。若你在自己的隔离实例中启动新会话，优先使用“设备包就绪场景（`all`）”，第一次联调保持时间倍率 `1`，再根据测试目的设置 S04 位置、储液泵和模拟天平读数。

### 4. 查看 Edge 是否接入

在“客户端连接”区域检查：

- TCP 连接数大于 0；
- 至少存在一个已激活的 OPC UA Session；
- 客户端地址中可以看到 Edge Pod 的连接。

没有客户端连接时，即使 Server 和 Agent 都显示运行，Uni-Lab OS 也不能通过 PLC-Sim 执行动作。

### 5. 只读观察变量

在在线变量区域可以搜索节点、加入监控、手动刷新或定时读取。共享环境只建议读取，不要启用“维护写入”；写入值会立即改变 PLC-Sim 状态，并可能与握手代理形成竞争。

完成检查后，打开 [Uni-Lab OS Console](http://115.190.137.109:30183/console/)，按[SZLab 场景指南](szlab.md)选择已发布工作流、准备物料、执行预检并创建 Task。任务详情和 Trace 是工作流运行事实，PLC-Sim 变量只用于辅助观察设备协议。

## 管理员恢复共享仿真

只有确认没有活动 Task、在途 Job 或其他使用者时，管理员才可以恢复停止的 Server/Agent：

1. 先在 Uni-Lab OS 检查任务与 Scheduler，必要时完成安全 drain。
2. 在 PLC-Sim 的“OPC UA Server”中选择“CSV / SZLab”，使用 `data/szlab_plc_0810.csv`、`0.0.0.0:4855`、命名空间 `4` 和 `urn:xuse:sim`，然后启动服务。
3. 等 Server 显示运行后，在“握手代理”中选择“SZLab 设备包仿真”、`127.0.0.1:4855`、`config/szlab_handshake.yaml` 和初始场景 `all`，再启动代理。
4. 等客户端连接出现 Activated Session，再检查 Uni-Lab OS 的 Edge Readiness 和在线设备。
5. 重新预检工作流；不要恢复或重发结果未知的原 Job。

正确的启动顺序是 PLC-Sim Server → SZLab Handshake Agent → Uni-Lab OS Edge。停止时应先排空 Uni-Lab OS，再停止 Agent 和 Server。

PLC-Sim Pod 重建后，GUI 可能先通过 Kubernetes Readiness，但 GUI 托管的 Server 和 Agent 子进程仍需重新启动。此时必须重新完成 Server、Agent、Edge Session 三项检查，不能只看 Pod 为 Ready。

## 在本地安装并接入

PLC-Sim 要求 Python 3.11。为避免改变已经验证的 Uni-Lab OS 依赖集合，给模拟器使用独立虚拟环境：

```bash
git clone https://github.com/raoyi971102-gif/PLC-Sim.git \
  "$LAB_ROOT/PLC-Sim"

python3.11 -m venv "$LAB_ROOT/PLC-Sim/.venv"
"$LAB_ROOT/PLC-Sim/.venv/bin/python" -m pip install \
  "$LAB_ROOT/PLC-Sim/PLC-Sim"
"$LAB_ROOT/PLC-Sim/.venv/bin/plc-sim" gui \
  --host 127.0.0.1 \
  --port 18765
```

打开 `http://127.0.0.1:18765/`，按本页前面的管理员启动步骤依次启动 OPC UA Server 和 SZLab 握手代理。保持 PLC-Sim 终端运行，再打开一个已激活 `unilabos` 环境的新终端启动 Uni-Lab OS。

需要让设备 Driver 真正连接本机仿真器时，使用 SZLab 的本地仿真 Graph，并显式选择 `normal`：

```bash
unilab workspace start \
  --workspace "$LAB_ROOT/Uni-Lab-SZLab" \
  --graph deployment/graphs/szlab-plc-sim-local.json \
  --runtime-mode normal \
  --startup-mode develop \
  --wait 300 \
  --json
```

:::{warning}
只有在确认所用 Graph 的全部外部端点都指向隔离仿真服务时，才能使用上面的 `normal`。如果 Graph 中混入真实 PLC、机械臂、相机或其他设备地址，Driver 可能产生真实动作。首次学习工作流仍应使用安装教程中的 `dry-run + develop`。
:::

本地停止时先执行统一 Workspace 停止，让 Scheduler 排空：

```bash
unilab workspace stop \
  --workspace "$LAB_ROOT/Uni-Lab-SZLab" \
  --wait 300 \
  --json
```

确认没有在途 Job 后，在 PLC-Sim GUI 中依次停止握手代理和 OPC UA Server，最后结束 GUI 进程。Workspace Host 代码也支持配置 `plcSimulatorProjectPath`、`plcVariableTablePath` 和握手参数来托管 PLC-Sim，但独立环境更适合新手安装和故障隔离。

PLC-Sim 对未登记或未建模的动作会以 `unsupported` 失败，不会伪造成功。当前行为快照也不等于所有已发布工作流都已经过端到端仿真验收；是否可运行仍以该工作流的设备映射、预检结果和实际 Task Trace 为准。

## 成功标准

开始 SZLab 仿真工作流前，至少满足：

- PLC-Sim GUI 健康；
- OPC UA Server 与 SZLab 握手代理都在运行；
- Edge 存在 Activated OPC UA Session；
- Uni-Lab OS Readiness 为 `ready`，Edge 已连接；
- `szlab_poly_plc` 在线且可派发；
- 使用的 Graph 明确指向 PLC-Sim，而不是真机；
- 工作流预检通过，任务输入和物料来源已经人工复核。

<div class="evidence">
<strong>实现依据</strong>：<code>PLC-Sim/PLC-Sim/gui/static/{index.html,simulation.js,variables.js}</code>（GUI 页面、Server/Agent 控制和变量监控）；<code>PLC-Sim/PLC-Sim/{server.py,szlab_handshake_agent.py,szlab_package_runtime.py}</code>（OPC UA 与 SZLab package mode）；<code>unilabos/workspace_host/{launch,host}.py</code>（本地 PLC-Sim 配置、启动顺序和 GUI API）；<code>Uni-Lab-SZLab/deployment/graphs/szlab-plc-sim-local.json</code>（本机仿真端点）；目标 Kubernetes 的 Deployment、Service、GUI 健康/状态接口与 OPC UA 连接实测。
</div>
