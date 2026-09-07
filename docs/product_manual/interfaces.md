# 其他产品入口

Uni-Lab 代码库包含多种客户端和部署角色。本页用于说明它们各自能做什么；当前 `xiongyanfei` 的主要用户入口仍是内置 Console。

## 内置 Console

地址：`/console/`

适合浏览器中的日常实验操作，包含总监控、物料、试剂、实验室操作、工作流和任务六页。它与 Python Workspace Backend 同源，目标环境已经部署。

## Theia Workbench

Theia Workbench 是桌面/Workbench 产品，Activity Bar 包含：

- 设备；
- 机器人动作调试；
- 机器人点位；
- 实验台；
- 试剂；
- 物料；
- 工作流；
- Agent；
- 环境管理和设置。

实际功能由连接 Profile 决定：

| 能力 | Go Backend Profile | Python Local Profile |
| --- | --- | --- |
| 设备目录与动作运行 | 支持 | 支持 |
| 强制解锁 | 不支持 | 支持 |
| 工作流读写、运行和 SSE | 支持 | 支持 |
| Python 源码编辑 | 不支持 | 支持 |
| 物料读取 | 支持 | 支持 |
| 物料写入 | 不支持 | 不支持 |
| 试剂身份 CRUD | 支持 | 不支持 |
| 试剂库存 | CRUD + 历史 | 只读 |

Local Profile 以 Python 源码/编译结果为权威；Backend Profile 以画布 Graph 为权威。切换 Profile 只影响之后的任务，不迁移已有任务，且未保存修改会阻止切换。

Workbench 的实验台只读展示逻辑站点占用，不是传感器事实；机器人点位当前不可用；Catalog 的新建、删除、变更日志和状态筛选被隐藏。旧 breakpoint debugger 与当前退役 Debug API 不兼容。

### Workbench 远程共享

桌面环境管理器可以启动一个秘密链接进行远程共享。代码要求非 loopback 监听必须配置 TLS 证书、HTTPS public origin、Host/Origin 校验，并用一次性 token 换取 HttpOnly cookie；默认 capability 有效期为 12 小时，停止或重启会撤销旧 generation。

同一个远程 Workbench 是共享会话，不支持多人协同编辑协议。不要让两个人同时保存同一文件。

## Go Backend 管理后台

`uni-lab-backend` 自带管理 SPA，包含总览、材料空间、设备与 Edge、库存、模板、工作流编辑、运行、模拟器/调试和 API/事件工作台九个页面。它支持：

- 材料关系图、2.5D shape、不可变状态和台账；
- 试剂身份/库存与化合物查询；
- Edge/设备和可派发状态；
- 工作流 Graph 编辑、自动保存、撤销/重做、发布与组合；
- Task 控制、人工确认、干预、反馈、错误和 Trace；
- API 请求与 SSE 调试。

当前目标命名空间使用 Python Workspace Backend/Edge 架构，不应把该管理后台的按钮写成当前 Console 操作步骤。

## 命令行

安装 Uni-Lab OS 后，代码注册三个主要入口：`unilab`、`unilab-supervisor` 和 `unilab-mcp`。

常用命令族：

```text
unilab workspace status|start|stop|restart|reset-local
unilab workflow list|inspect|run|watch|command
unilab material scene|layout|template
unilab package inspect|build|upload|add|update|remove
unilab login|logout|whoami
unilab template-sync
unilab instance-sync
unilab doctor net|talker|listener|fake-device
```

从零创建一个能被这些命令检查、构建并由 Workspace 加载的领域包，见[开发一个可加载的实验室仓库](lab-repository.md)。

`reset-local` 需要显式确认；`whoami` 只显示凭证来源，不会向服务端验证身份。旧 `workflow debug` 走退役 API，不应使用。

## MCP Agent 工具

安装可选 `mcp` 依赖后，`unilab-mcp --workspace <领域工作区绝对路径>` 暴露：

- Workspace 状态、单组件启停、日志和重置；
- 工作流列表、检查、Task 运行/观察/命令，以及等待 Authoring 修订；
- 物料 scene 捕获与检查；
- 布局检查、预览和应用；
- 可视回归比较与批准；
- 模板校验。

MCP 适合为 AI 提供当前工作区和运行状态，但当前没有工作流源码生成、写入、导入、发布或运行前预检工具。`wait_authoring` 只在 revision 增长或存在非空诊断时可靠返回，不能用它判断同一 revision 下的无诊断 candidate 已经生成；这时应读取 Authoring REST 状态。推荐的 AI 创作组合与安装命令见[用 AI 编写工作流](ai-workflow-authoring.md)。

MCP 是由客户端拉起的本地命令型 Server，不是公网服务，也不是人类权限系统。`run_workflow` 会直接创建 Task，不能提交完整库存绑定和优先级字段，且它的 `operation_id` 不是创建幂等键；必须放在人工审查、发布和产品预检之后。重置仍需显式确认。`debug_workflow` 和带 `hold_uuid` 的旧调试命令受 HTTP 410 限制；请改用 `run_mode="step"` 的标准 Task 和普通 `step`/`resume`/`cancel` 命令。已注册的 `switch_workspace_authority` 当前会被 Workspace Host 以 `backend_mode_removed` 拒绝，不能当作可用能力。真机环境也不要用单组件 MCP stop 代替 `unilab workspace stop` 的完整排空流程。

## PLC-Sim 与 Modbus-Sim

Uni-Lab-Sim 仓库提供：

- PLC-Sim：OPC UA、PLC 动作/传感器、握手代理、SZLab/PTLC 设备包仿真和 Web GUI；当前环境可从 [PLC-Sim Web GUI](http://115.190.137.109:30160/) 访问，完整步骤见[PLC-Sim 仿真器](plc-sim.md)；
- Modbus-Sim：Modbus TCP、RTU RS-485、RTU RS-232、ASCII、多从站、CSV 寄存器和报文监视。

两者是设备/工业协议联调工具，不承担 Scheduler、库存权威或运动安全。默认 GUI 面向本机/可信实验室网络，没有公网用户鉴权。

## 不应作为正式入口的代码

- `unilab-os-task-console-prototype` 是历史任务台原型，多个按钮是占位，所谓 SSE 实际为 15 秒轮询。
- `kernel-web` 中有设备广场等实验页面，但没有挂到正式 Theia Activity Bar。
- `apps/cloud-web` 目前只是未来占位。
- VS Code 扩展负责打开/高亮工作流和资源、地图选择、诊断与发布快照，不会启动或控制 Runtime。

<div class="evidence">
<strong>实现依据</strong>：<code>uni-lab-fe/packages/workbench-theia/src/browser/unilab-workbench-contribution.ts</code>（Workbench 入口）；<code>packages/services/src/capabilities.ts</code>（Profile 矩阵）；<code>apps/desktop/src/renderer/environment-manager.tsx</code> 与 <code>remote-facade.mjs</code>（远程共享）；<code>uni-lab-backend/frontend/src/App.tsx</code>（后台页面）；<code>Uni-Lab-OS/setup.py</code> 与 <code>unilabos/agent_tools/workflow.py</code>（CLI/MCP）。
</div>
