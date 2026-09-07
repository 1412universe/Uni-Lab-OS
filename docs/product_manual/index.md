# Uni-Lab OS 产品使用说明书

Uni-Lab OS 把实验室中的设备、物料、试剂和实验流程组织在同一套运行系统中。你可以在控制台中准备实验资源、选择并发布工作流、提交任务，并在多个实验共享设备和工位时观察调度、处理异常和追踪结果。

<div class="manual-meta">
适用环境：SZLab 演示环境　·　手册版本：2026.09.07　·　事实基线：Uni-Lab-OS f2295f7c2de4，Uni-Lab-SZLab f0958f5b2d1b
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

- **体验已有部署（支线）**：先读[认识产品](overview.md)和[使用 Console](console.md)，再按[体验已部署的安全工作流](quickstart.md)操作当前授权环境。这条支线不安装产品，也不会建立自己的实验室仓库。
- **从零建立实验室（主线）**：依次完成[安装并启动本地产品](installation.md) → [环境与运行配置](environment.md) → [先理解工作流](workflow-concepts.md) → [开发一个可加载的实验室仓库](lab-repository.md) → [设备与动作](devices.md)。
- **可选 AI 建仓**：先了解[产品入口与 Workbench](interfaces.md)，再使用[AI 仓库生成器（实验性）](repository-builder-skill.md)。它是建仓辅助分支，不替代领域仓库合同、人工审查或四道验证门。
- **学习并创作工作流**：先[手写并运行第一个工作流（SZLab 教学）](first-workflow.md)，再学习[物料](materials.md)、[试剂](reagents.md)、[工作流编排特性](workflow-features.md)和[可复用实验操作](operations.md)，最后按[用 AI 编写工作流（推荐）](ai-workflow-authoring.md)创作后续流程。
- **部署并运行**：先阅读[运行模式、安全与恢复](runtime-safety.md)。SZLab 仿真部署还要先准备[PLC-Sim](plc-sim.md)，其他实验室可以跳过；然后完成[Kubernetes 部署与上线](deployment.md)，再进入[管理与运行工作流](workflows.md)和[任务与并行调度](tasks.md)。
- **进入 SZLab 联调**：运行环境上线后，按[SZLab 场景指南](szlab.md)准备资源、运行已发布流程并解释结果。
- **管理与排障**：使用[故障排查](troubleshooting.md)、[能力状态表](capability-matrix.md)、[API 使用参考](api-reference.md)和[事实依据与版本](evidence.md)。

## 新手学习路线

<ol class="learning-path">
  <li><strong>安装</strong><span>建立 Python 3.11 + ROS 环境，安装 OS/SZLab，并编译 Console。</span></li>
  <li><strong>配置</strong><span>分清 Conda 环境、Workspace、Graph、运行模式与本地配置。</span></li>
  <li><strong>建模</strong><span>理解 Workflow、Node、Task、Job、修订和发布合同。</span></li>
  <li><strong>建仓</strong><span>定义包身份、设备、Graph 和工作流清单，让 Backend 与 Edge 加载。</span></li>
  <li><strong>可选 AI 建仓</strong><span>在本地 Workbench 中让 Agent 辅助生成或迁移领域仓库，再由人完成验证门。</span></li>
  <li><strong>手写闭环</strong><span>亲手完成最小流程的导入、发布、预检和 dry-run Task，具备审查能力。</span></li>
  <li><strong>准备资源</strong><span>理解物料、库位、试剂身份、库存和数量预留，再编排物料流程。</span></li>
  <li><strong>进阶编排</strong><span>掌握条件、循环、并行、资源区间、人工确认与子工作流。</span></li>
  <li><strong>AI 创作</strong><span>让 AI 扫描当前 Catalog、驱动、资源和 SZLab 参考流程，再生成静态 DSL。</span></li>
  <li><strong>安全部署</strong><span>核对运行模式、镜像、Workspace、Graph、Secret、网络和回滚，再部署上线。</span></li>
  <li><strong>运行验收</strong><span>发布、零写入预检、创建 Task，并核对 Job、资源等待、输出和 Trace。</span></li>
</ol>

部署前的基础学习路径固定使用 `dry-run + develop`。完成后可以按[PLC-Sim 仿真器](plc-sim.md)进入“真实 Driver + 模拟 PLC”的隔离联调。两者都不代表可以切换到真机；真实设备仍需单独完成连接、联锁、急停、物料和恢复验收。

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
:caption: 认识与快速体验
:maxdepth: 2

overview
console
quickstart
```

```{toctree}
:caption: 安装与实验室开发
:maxdepth: 2

installation
environment
workflow-concepts
lab-repository
devices
interfaces
repository-builder-skill
```

```{toctree}
:caption: 工作流创作
:maxdepth: 2

first-workflow
materials
reagents
workflow-features
operations
ai-workflow-authoring
```

```{toctree}
:caption: 部署与运行
:maxdepth: 2

runtime-safety
plc-sim
deployment
workflows
tasks
```

```{toctree}
:caption: SZLab 场景
:maxdepth: 2

szlab
```

```{toctree}
:caption: 排障与参考
:maxdepth: 2

troubleshooting
capability-matrix
api-reference
evidence
```
