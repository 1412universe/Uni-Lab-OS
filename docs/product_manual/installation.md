# 安装并启动本地产品

完成本页后，你会得到一个可以打开 Console、编译 SZLab 工作流并以模拟动作运行任务的本地 Uni-Lab OS。整个首次启动使用 `dry-run`，不会构造 SZLab 真机驱动，也不会连接 PLC、机械臂或其他实验设备。

## 安装结果

安装完成时应同时满足四项：

- `unilab` 命令可用；
- SZLab 领域包检查通过；
- Console 已编译并能从 Workspace Backend 打开；
- Backend 与 Edge 都为 `ready`，Edge 已登记设备动作目录。

本页按当前手册事实基线安装源码版产品。它适合学习、工作流创作和安全模拟；生产部署需要另行配置镜像、持久卷、TLS、身份认证和真实设备接入。

## 1. 准备工具

| 工具 | 要求 | 用途 |
| --- | --- | --- |
| Git | 可用的当前版本 | 获取 Uni-Lab OS 与 SZLab 源码 |
| Mamba | Miniforge/Mambaforge 均可 | 安装带 ROS 2 核心的隔离环境 |
| Python | `3.11.14` | 上游 README 与当前构建配方的共同基线 |
| Node.js | `22.13.0` 或更高 | 编译 React Console |
| npm | 随 Node.js 安装 | 按锁文件安装前端依赖 |
| curl | 任意当前版本 | 执行健康检查 |

先确认已有工具：

```bash
git --version
mamba --version
node --version
npm --version
curl --version
```

如果 `node --version` 低于 `v22.13.0`，先升级 Node.js。前端的 `package.json` 会拒绝不满足版本要求的环境。

## 2. 创建开发环境

```bash
mamba create -n unilabos \
  --override-channels \
  -c uni-lab \
  -c robostack-staging \
  -c conda-forge \
  python=3.11.14 \
  "uni-lab::unilabos-env=0.11.4" \
  uv \
  -y
mamba activate unilabos
python --version
```

最后一条命令应显示 `Python 3.11.14`。`unilabos-env` 提供完整 Edge dry-run 所需的 ROS 2 核心；只创建一个裸 Python 环境不足以运行双进程拓扑。以后每次打开新终端，都要先执行 `mamba activate unilabos`。

上游 README 还提供 `unilabos` 和 `unilabos-full` 两种环境。为什么本教程选择 `unilabos-env`，以及三者的适用边界，见[环境与运行配置](environment.md#选择-conda-环境)。

## 3. 获取两份源码

两份仓库应放在同一个父目录中。下面的 `LAB_ROOT` 只是本教程变量，可以改成你有写权限的目录：

```bash
export LAB_ROOT="${PWD}/unilab-workspace"
mkdir -p "$LAB_ROOT"
cd "$LAB_ROOT"

git clone --branch product/durable-scheduler-kernel-v3 \
  https://github.com/Uni-Lab-OS/Uni-Lab-OS.git
git clone https://github.com/Uni-Lab-OS/Uni-Lab-SZLab.git
```

本手册审计时的提交是：

```text
Uni-Lab-OS     f2295f7c2de4890306a89dd22f1bb25fcf4fb7b4
Uni-Lab-SZLab  f0958f5b2d1ba0b145993778c2e1d22519819fba
```

用下面的命令记录你实际安装的版本；后续排错时应一并提供：

```bash
git -C "$LAB_ROOT/Uni-Lab-OS" rev-parse HEAD
git -C "$LAB_ROOT/Uni-Lab-SZLab" rev-parse HEAD
```

:::{note}
工作流作者需要使用 OS 当前产品分支，因为本手册中的静态 Python DSL、发布合同、Workspace Host 双进程拓扑和 Console 页面都以该分支为依据。不要把其他分支的命令与本教程混用。
:::

## 4. 安装 Uni-Lab OS

仓库自带的开发安装脚本先执行 editable install，再通过 `uv` 安装运行依赖；`uv` 不可用时会回退到 `pip`。

```bash
cd "$LAB_ROOT/Uni-Lab-OS"
python scripts/dev_install.py --use-pip
python -m pip install "PyYAML>=6"
```

验证 Python 包和命令入口：

```bash
python -c "import unilabos; print(unilabos.__version__)"
python -c "import rclpy, yaml; print('ROS 与配置依赖：OK')"
python -m pip check
unilab --help
unilab workspace --help
```

预期包版本为 `0.11.4`，并且 `workspace` 帮助中能看到 `start`、`stop`、`restart`、`status` 和 `logs`。当前源码在入口模块导入阶段就需要 `PyYAML`，所以这里显式补装，而不是等待运行期依赖检查。

## 5. 编译 Console

干净的源码检出不包含 `unilabos/app/web/static/console/index.html`。缺少它时 Backend API 仍可启动，但 `/console/` 不会挂载，因此这一步不是可选项。

```bash
cd "$LAB_ROOT/Uni-Lab-OS/frontend"
npm ci
npm run build
test -f ../unilabos/app/web/static/console/index.html
```

`npm run build` 会把生产静态文件直接写入 Python 包的 Console 目录。因为 OS 采用 editable install，之后启动的 Workspace Backend 会读取这份构建结果。

## 6. 安装并检查 SZLab 包

```bash
cd "$LAB_ROOT/Uni-Lab-SZLab"
python -m pip install -e . --no-deps
./scripts/check-package.sh
```

检查脚本会先编译完整包目录，再用 SZLab 图执行 OS 只读检查。出现 `[ERROR]`、`个错误` 或非零退出码都表示不能继续启动，应先修复报告中的第一个错误。

:::{tip}
`Uni-Lab-OS` 已声明 `pylabrobot`，其 requirements 也包含 `opcua` 和 `requests`；因此 SZLab 仓库按自己的安装说明使用 `--no-deps`，避免第二次改变已验证的 OS 依赖集合。
:::

## 7. 以安全模拟模式启动

使用 Workspace Host 的统一生命周期命令，不要直接执行历史 `start-*.sh`：

```bash
unilab workspace start \
  --workspace "$LAB_ROOT/Uni-Lab-SZLab" \
  --graph deployment/graphs/szlab-local-debug.json \
  --runtime-mode dry-run \
  --startup-mode develop \
  --wait 300 \
  --json
```

这条命令会管理两个独立进程：

```text
Console / API
      │
      ▼
Workspace Backend ── edge_control ──► Edge Runtime
  定义、库存、调度                      模拟动作目录
```

`dry-run` 会把 Edge 设置为 `action_mode=simulate`，并在独立图副本中关闭设备的 `auto_connect`。Edge 从 Registry 登记设备和动作合同，但不构造设备驱动；任务仍会经过正常的定义、预检、调度、Job 和结果通道。

需要在不连接物理 PLC 的情况下验证真实 SZLab Driver 与 OPC UA 握手时，完成本教程后再进入[PLC-Sim 仿真器](plc-sim.md)。该路径使用 `runtimeMode=normal` 和明确指向模拟器的专用 Graph，与本页的 `dry-run` 不是同一种模拟。

不要用 `--backend simple`。当前 `simple` 与 `automancer` 分支没有实现可启动的 backend；受支持的完整路径由 Workspace Host 固定使用 ROS backend。

## 8. 做安装验收

查看状态：

```bash
unilab workspace status \
  --workspace "$LAB_ROOT/Uni-Lab-SZLab" \
  --json
```

从输出中找到 `components.backend.address`，例如 `http://127.0.0.1:49152`。把实际值填到下面的教程变量：

```bash
export BACKEND_URL="http://127.0.0.1:49152"
curl -fsS "$BACKEND_URL/api/v1/health"
curl -fsS "$BACKEND_URL/api/v1/readiness"
curl -fsS "$BACKEND_URL/api/v1/edge/readiness"
```

验收标准：

- Health 返回 `status: ok`，Scheduler 为 `ready`；
- Readiness 返回 `status: ready`，工作流加载数等于总数；
- Edge Readiness 返回 `connected: true`，设备数大于 0；
- 浏览器打开 `BACKEND_URL/console/` 后，页面不显示断线快照或演示数据。

如果要开发自己的实验室领域包，接下来进入[工作流基础](workflow-concepts.md)和[实验室仓库接入](lab-repository.md)。如果只在 SZLab 中编写流程，则先完成[手写教程](first-workflow.md)与[编排特性](workflow-features.md)，之后日常使用[AI 辅助方式](ai-workflow-authoring.md)。

## 9. 安全停止

完成练习后使用统一停止命令：

```bash
unilab workspace stop \
  --workspace "$LAB_ROOT/Uni-Lab-SZLab" \
  --wait 300 \
  --json
```

统一停止会先让 Scheduler 排空，不再派发新 Job，等待已派发结果收敛，再依次停止 Edge 和 Backend。不要通过删除 `.unilabos`、直接杀进程或重建任务来代替排空。

## 可选分支：安装并启动本地 Theia Workbench

Theia Workbench 是可选的开发客户端，不是 Console、CLI 或工作流运行的安装前置。只有需要 IDE、环境管理或本地 Agent 时才进入本节；集群和公网发布仍按[Kubernetes 部署与上线](deployment.md)操作。

开始前应完成本页的 OS 与 SZLab 安装，并先停止同一 Workspace 的已有会话。本页前面安装的 Node.js `22.13.0` 可直接复用；Workbench README 支持的 Node 主版本是 20 或 22。

所选 Conda 环境还必须同时包含可执行的 Python 和 `unilab` 命令。

克隆正式前端仓库，并按仓库锁定的 pnpm `10.13.1` 安装依赖：

```bash
cd "$LAB_ROOT"
git clone https://github.com/Uni-Lab-OS/uni-lab-fe.git
cd "$LAB_ROOT/uni-lab-fe"
corepack enable
pnpm install
test "$(pnpm --version)" = "10.13.1"
```

激活前面安装 OS 的环境，再从前端仓库启动正式 Theia 入口：

```bash
mamba activate unilabos
cd "$LAB_ROOT/uni-lab-fe"

THEIA_WORKSPACE="$LAB_ROOT/Uni-Lab-SZLab" \
UNILAB_OS_PROJECT="$LAB_ROOT/Uni-Lab-OS" \
UNILAB_PYTHON_ENV="$CONDA_PREFIX" \
THEIA_PORT=3100 \
pnpm workbench
```

`THEIA_WORKSPACE` 必须是含 `deployment/local_config.py` 的领域仓库根目录。启动器会校验 Python 环境中的 Python 与 `unilab`，并让终端、Python 扩展和托管 OS 使用同一环境。

另开终端执行下面的检查，再打开浏览器：

```bash
curl -fsS http://127.0.0.1:3100/ >/dev/null
```

访问 `http://127.0.0.1:3100` 后，应看到设备、物料、工作流和环境管理等入口。`pnpm workbench` 在前台运行；结束时按 `Ctrl+C`，由启动器清理它管理的进程。

只使用 Workbench 而不需要 Agent 时，可在启动命令中设置 `UNILAB_AGENT_ENABLED=0`。若要使用 AI 仓库生成器，还需 AionUi `2.1.52+` 的本地 Agent 载荷；非默认位置用 `UNILAB_AIONUI_APP` 指定，然后进入[使用 AI 仓库生成器（实验性）](repository-builder-skill.md)。

## 安装失败时先看这里

| 现象 | 最可能原因 | 处理 |
| --- | --- | --- |
| `unilab: command not found` | 没有激活环境，或 editable install 失败 | 重新激活 `unilabos` 环境，再运行安装脚本 |
| `/console/` 为 404，但 Health 正常 | React Console 没有构建 | 在 `frontend` 执行 `npm ci && npm run build` 后重启 Workspace |
| Node engine 不匹配 | Node.js 低于 22.13 | 升级 Node，再重新 `npm ci` |
| `package inspect` 报动作或类型不存在 | OS 与 SZLab 版本不匹配，或领域包未安装 | 核对两个 commit，并重新安装 SZLab editable package |
| Backend 就绪、Edge 未连接 | Edge 进程启动失败 | 查看 `unilab workspace logs --component edge ... --json` |
| 参数 `--skip_env_check` 或 `--test_mode` 不识别 | 使用了旧启动脚本/旧文档 | 改用本页的 `workspace start --runtime-mode dry-run` |

更多处理方法见[故障排查](troubleshooting.md)。

<div class="evidence">
<strong>实现依据</strong>
<p><a href="https://github.com/deepmodeling/Uni-Lab-OS#quick-start">上游 README Quick Start</a>、当前 <code>README.md</code> 与 <code>setup.py</code>（环境类型、Python 版本、包版本和安装入口）。</p>
<p><code>.conda/environment/recipe.yaml</code>（ROS 开发环境）；<code>scripts/dev_install.py</code>（editable install 与依赖安装）。</p>
<p><code>frontend/package.json</code>、<code>frontend/vite.config.ts</code> 与 <code>unilabos/app/web/console.py</code>（Node 要求、构建输出和 Console 挂载）。</p>
<p><code>unilabos/workspace_host/launch.py</code>（Backend/Edge 双进程与 dry-run）；<code>Uni-Lab-SZLab/pyproject.toml</code>、<code>scripts/check-package.sh</code>（SZLab 安装和检查）。</p>
</div>
