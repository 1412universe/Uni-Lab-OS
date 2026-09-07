# Uni-Lab OS 产品使用说明书

Uni-Lab OS 把实验室中的设备、物料、试剂和实验流程组织在同一套运行系统中。你可以在控制台中准备实验资源、选择并发布工作流、提交任务，并在多个实验共享设备和工位时观察调度、处理异常和追踪结果。

<div class="manual-meta">
适用环境：xiongyanfei / SZLab 演示环境　·　手册版本：2026.09.07　·　事实基线：Uni-Lab-OS f2295f7c2de4，Uni-Lab-SZLab f0958f5b2d1b
</div>

<div class="entry-links">
<p><strong>产品控制台</strong>：<a href="http://115.190.137.109:30183/console/">打开 Uni-Lab OS Console</a></p>
<p><strong>PLC 仿真</strong>：<a href="http://115.190.137.109:30160/">打开 PLC-Sim Web GUI</a></p>
<p><strong>接口文档</strong>：<a href="http://115.190.137.109:30183/api/docs">打开 Swagger API</a></p>
<p><strong>运行追踪</strong>：<a href="http://115.190.137.109:30151/">打开 SigNoZ</a></p>
</div>

:::{warning}
当前演示入口通过明文 HTTP 公网开放，产品代码没有为这些入口提供通用的用户登录与权限中间件。只在获得授权的测试场景中使用，不要录入生产凭证、敏感配方或真实业务数据。生产部署应在入口前增加 TLS、身份认证和访问控制。
:::

## 从哪里开始

- 从零开发自己的实验室：依次完成[安装并启动本地产品](installation.md) → [环境与运行配置](environment.md) → [开发一个可加载的实验室仓库](lab-repository.md) → [先理解工作流](workflow-concepts.md) → [用 AI 编写工作流（推荐）](ai-workflow-authoring.md) → [手写并运行第一个工作流](first-workflow.md) → [工作流编排特性](workflow-features.md)。
- 只体验现有环境：先读[认识产品](overview.md)，再按[首次运行一个安全工作流](quickstart.md)完成闭环。
- 实验操作员：重点阅读[物料](materials.md)、[试剂](reagents.md)、[工作流](workflows.md)和[任务与并行调度](tasks.md)。
- SZLab 用户：先用[PLC-Sim 仿真器](plc-sim.md)确认设备协议链路，再在[SZLab 场景指南](szlab.md)中查看当前已发布工作流和工位能力。
- 管理与排障：阅读[运行模式、安全与恢复](runtime-safety.md)、[故障排查](troubleshooting.md)和[能力状态表](capability-matrix.md)。

## 新手学习路线

<ol class="learning-path">
  <li><strong>安装</strong><span>建立 Python 3.11 + ROS 环境，安装 OS/SZLab，并编译 Console。</span></li>
  <li><strong>配置</strong><span>分清 Conda 环境、Workspace、Graph、运行模式与本地配置。</span></li>
  <li><strong>建仓</strong><span>定义包身份、设备、Graph 和工作流清单，让 Backend 与 Edge 加载。</span></li>
  <li><strong>建模</strong><span>理解 Workflow、Node、Task、Job、修订和发布合同。</span></li>
  <li><strong>AI 创作</strong><span>让 AI 先扫描当前 Catalog、驱动与 SZLab 参考流程，再生成静态 DSL。</span></li>
  <li><strong>读懂</strong><span>亲手完成一个最小流程，能够审查和排查 AI 生成的代码。</span></li>
  <li><strong>运行</strong><span>发布、零写入预检、创建 Task，并核对 Job 与输出。</span></li>
  <li><strong>进阶</strong><span>掌握条件、循环、并行、资源、物料、人工确认与子工作流。</span></li>
</ol>

整个基础学习路径固定使用 `dry-run + develop`。完成后可以按[PLC-Sim 仿真器](plc-sim.md)进入“真实 Driver + 模拟 PLC”的隔离联调；两者都不代表可以切换到真机，真实设备仍需单独完成连接、联锁、急停、物料和恢复验收。

## 本手册如何描述能力

同一功能在源码、控制台和线上环境中可能处于不同阶段。本手册统一使用以下状态：

| 状态 | 含义 |
| --- | --- |
| <span class="status status-ready">当前可用</span> | 目标命名空间已部署，且有可操作入口。 |
| <span class="status status-config">需要配置</span> | 代码已实现，但依赖设备、凭证、联网服务或运行模式。 |
| <span class="status status-limited">当前受限</span> | 只有 API 或其他客户端支持，当前 Console 没有入口，或能力有明确限制。 |
| <span class="status status-unavailable">当前不可用</span> | 接口已退役、执行器未启用，或界面只是占位。 |
| <span class="status status-experimental">实验验证</span> | 只存在于验证夹具、未提交工作树或历史仿真记录中。 |

“源码已定义”不等于“当前线上已发布”，而“仿真通过”也不等于“真机已完成安全验收”。各页会明确说明这些边界。

```{toctree}
:caption: 开始使用
:maxdepth: 2

overview
installation
environment
quickstart
console
```

```{toctree}
:caption: 编写工作流
:maxdepth: 2

lab-repository
workflow-concepts
ai-workflow-authoring
first-workflow
workflow-features
workflows
operations
tasks
```

```{toctree}
:caption: 功能指南
:maxdepth: 2

materials
reagents
devices
plc-sim
```

```{toctree}
:caption: SZLab 场景
:maxdepth: 2

szlab
```

```{toctree}
:caption: 运行与参考
:maxdepth: 2

runtime-safety
troubleshooting
capability-matrix
interfaces
api-reference
evidence
```
