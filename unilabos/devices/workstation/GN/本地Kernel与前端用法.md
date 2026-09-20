# GN 工作站 — 本地 Kernel-v2 / Workspace / 前端用法（Mac）

本文记录当前机器上的实际操作顺序，对应 **Uni-Lab OS 0.11.4（kernel-v2）** + 本目录 GN 驱动与 `demo-lab` 示例设备包。换电脑或重装后按此文档执行即可。

---

## 1. 环境前提

| 项 | 说明 |
|----|------|
| OS 安装目录 | `~/uni-lab-os`（安装器写入，已改 `~/.zshrc` 的 `condabin`） |
| 源码仓库 | `/Users/fenglongli/Downloads/Uni-Lab-OS`（分支 `GN-20260909`：kernel-v2 + GN 目录） |
| 示例设备包 | `unilabos/devices/workstation/GN/demo-lab` |
| **GN 真机设备包（kernel-v2）** | `unilabos/devices/workstation/GN/gn-lab`（图：`deployment/graphs/gn-station.json`，class 为 `community.gn_lab.*`） |
| GN legacy 启动图 | `unilabos/devices/workstation/GN/GN_station.json`（`unilab -g` 旧入口，id 为 `gn_*`） |
| Node（仅改前端源码时需要） | Homebrew `node@22`，路径见下文 |

**重要：** 终端里 `unilab` / `python` / `pip` 必须来自 `~/uni-lab-os/bin`，**不要**用系统 `/Library/Frameworks/Python.framework/Versions/3.13/` 下的命令（会缺依赖、报 `msgcenterpy` / `typing_extensions` 等错）。

---

## 2. 每次开新终端：PATH

**只跑 Backend / `unilab`：**

```bash
source ~/.zshrc
export PATH="/Users/fenglongli/uni-lab-os/bin:$PATH"
which unilab
which python
```

**要同时跑前端 dev（Node + unilab 一起）：**

```bash
export PATH="/usr/local/Homebrew/opt/node@22/bin:/Users/fenglongli/uni-lab-os/bin:$PATH"
node -v    # 本机已验证 v22.23.2
which unilab
```

验证 OS（四条都成功即安装 OK）：

```bash
python -c "import unilabos; print(unilabos.__version__)"
unilab --help
unilab workspace --help
```

---

## 3. 安装 / 更新源码（改 GN 驱动或切分支后执行）

在 **仓库根目录** 可编辑安装 OS（本地改代码立即生效）：

```bash
cd /Users/fenglongli/Downloads/Uni-Lab-OS
pip install -e .
```

可选：补齐 pip 依赖（与文档 4.3 一致）：

```bash
uv pip install --python "$(which python)" -r unilabos/utils/requirements.txt
```

若 Backend 启动报 `typing_extensions` / `sentinel` 相关错误：

```bash
pip install -U 'typing_extensions>=4.12'
```

---

## 4. 安全体验：demo-lab + Workspace（不接 GN 真机）

### 4.1 变量与设备包安装

```bash
export DEVICE_PACKAGE_ROOT="/Users/fenglongli/Downloads/Uni-Lab-OS/unilabos/devices/workstation/GN/demo-lab"
cd "$DEVICE_PACKAGE_ROOT"
pip install -e .
unilab package inspect --path . --out dist/inspect
```

### 4.2 启动工作区（dry-run）

**务必加 `--wait`**，否则 CLI 可能在 Backend 就绪前报 `backend_start_failed`（进程其实可能已在起）。

```bash
unilab workspace start \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --graph "$DEVICE_PACKAGE_ROOT/deployment/graphs/dry-run.json" \
  --runtime-mode dry-run \
  --startup-mode develop \
  --wait 180 \
  --json
```

### 4.3 查看状态与控制台

```bash
unilab workspace status --workspace "$DEVICE_PACKAGE_ROOT" --json
```

- 看 `components.backend.phase` 是否为 `ready`
- 看 `components.backend.address`（例如 `http://127.0.0.1:55600`）
- **若已构建静态前端**：浏览器打开 `{backend.address}/console/`
- 未构建静态包时 `/console/` 可能 404，需用下文 **第 5 节** Vite 开发前端

### 4.4 停止

仅需要关闭时再执行（**不要**在 `status` 已是 `ready` 时反复 stop/start）：

```bash
unilab workspace stop \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --json
```

失败时查 Backend 日志：

```bash
unilab workspace logs \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component backend \
  --json
```

**注意：** 运行 `unilab workspace` 的终端尽量保持打开；关掉可能影响 Host 子进程。

---

## 5. 前端源码开发（`frontend/`）

仅修改 React 源码时需要 Node **≥ 22.13**。`npm run dev` **必须在 `frontend/` 目录执行**；在仓库根目录 `/Users/fenglongli/Downloads/Uni-Lab-OS` 运行会报错：

```text
ENOENT: no such file or directory, open '.../Uni-Lab-OS/package.json'
```

（根目录没有 `package.json`，只有 `frontend/package.json`。）

### 5.1 推荐：两个终端

| 终端 | 作用 | 能否关 |
|------|------|--------|
| **A** | `unilab workspace`（Backend + Edge） | 不要关，关可能影响 Host |
| **B** | `npm run dev`（Vite 前端） | 按 `Ctrl+C` 会停前端；要再看页面需重新 `npm run dev` |

终端 A 先确认 Backend 为 `ready`（示例地址：`http://127.0.0.1:55600`，**以你本机 `status` 为准**）：

```bash
export PATH="/Users/fenglongli/uni-lab-os/bin:$PATH"
export DEVICE_PACKAGE_ROOT="/Users/fenglongli/Downloads/Uni-Lab-OS/unilabos/devices/workstation/GN/demo-lab"
unilab workspace status --workspace "$DEVICE_PACKAGE_ROOT" --json
```

终端 B 启动前端（**整段复制，不要单独粘贴以 `#` 开头的注释行**，否则 zsh 可能报 `command not found: #`）：

```bash
export PATH="/usr/local/Homebrew/opt/node@22/bin:/Users/fenglongli/uni-lab-os/bin:$PATH"
export DEVICE_PACKAGE_ROOT="/Users/fenglongli/Downloads/Uni-Lab-OS/unilabos/devices/workstation/GN/demo-lab"
cd /Users/fenglongli/Downloads/Uni-Lab-OS/frontend
npm ci
BACKEND_URL=$(unilab workspace status --workspace "$DEVICE_PACKAGE_ROOT" --json \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['components']['backend']['address'])")
VITE_EDGE_API_URL="$BACKEND_URL" npm run dev -- --host 127.0.0.1 --port 4174
```

成功时终端类似：

```text
VITE v8.2.2  ready in 653 ms
➜  Local:   http://127.0.0.1:4174/console/
```

浏览器打开：**http://127.0.0.1:4174/console/**（不要关终端 B，除非不再需要页面）。

Backend 重启后 `address` 端口会变：在 **仍位于 `frontend/` 目录** 的终端 B 里重新执行 `BACKEND_URL=...` 与 `VITE_EDGE_API_URL=... npm run dev ...`。

生产构建（写入 Python 包静态目录，随 OS 提供 `/console/`）：

```bash
cd /Users/fenglongli/Downloads/Uni-Lab-OS/frontend
npm run build
# 产物：../unilabos/app/web/static/console/
```

---

## 6. GN 工作流：先仿真，再真机

驱动实现仍在 `GN/` 根目录；**kernel-v2 工作区**通过 `gn-lab` 包挂载设备（`community.gn_lab.gn_workstation` 等）。已登记 4 条金四面体/快换工作流（由同目录 JSON 转换，节点 UUID 不变）。

```bash
export GN_PACKAGE="/Users/fenglongli/Downloads/Uni-Lab-OS/unilabos/devices/workstation/GN/gn-lab"
cd "$GN_PACKAGE"
pip install -e .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q
```

### 6.1 仿真（不连 PLC / PRCXI）

`--runtime-mode dry-run` 会把 `action_mode` 设为 `simulate`，驱动跳过 OPC 连接；图用 `deployment/graphs/dry-run.json`。

```bash
unilab workspace start \
  --workspace "$GN_PACKAGE" \
  --component all \
  --graph deployment/graphs/dry-run.json \
  --runtime-mode dry-run \
  --startup-mode develop \
  --wait 300
```

控制台工作流页应能看到：

- 快换模块单点调试（从复位到所有功能再到复位）
- 金四面体-从第一次离心到结束
- 金四面体AA溶液制备与溶液的混合，并放到T16等待离心
- 金四面体CTAC与CTAB混合溶液制备

建议先跑 **快换单点调试**（节点少），再跑三条金四面体。仿真成功只证明调度/参数能走通，**不能**代替真机。

若 JSON 有更新，重新生成 Python 源码：

```bash
python scripts/convert_legacy_workflows.py
```

### 6.2 真机

先 `workspace stop`，再改 `deployment/graphs/gn-station.json` 里 OPC `url`、PRCXI `host`/`port` 为现场值。

```bash
unilab workspace start \
  --workspace "$GN_PACKAGE" \
  --component all \
  --graph deployment/graphs/gn-station.json \
  --runtime-mode normal \
  --startup-mode develop \
  --wait 300
```

从 legacy `GN_station.json` 重新生成 kernel 图：

```bash
python scripts/convert_gn_station_graph.py
```

---

## 7. GN 真站 + 云端（Edge 旧入口）

真机 **推荐** 用第 6 节 `gn-lab` + `workspace start`。若仍走旧 `unilab -g`，须挂载设备包且图里 class 为 `community.gn_lab.*`（见 `gn-lab/deployment/graphs/gn-station.json`），例如：

```bash
cd /Users/fenglongli/Downloads/Uni-Lab-OS
pip install -e unilabos/devices/workstation/GN/gn-lab

unilab -g unilabos/devices/workstation/GN/gn-lab/deployment/graphs/gn-station.json \
  --devices unilabos/devices/workstation/GN/gn-lab/gn_lab \
```

legacy `GN_station.json`（class 为 `gn_*`）仅适用于仍带内置 `@device` 扫描的旧分支；当前 kernel-v2 驱动注册在 `gn-lab` 包内。

旧示例（AK/SK 见 `start` 文件，**路径用正斜杠**）：

```bash
unilab -g unilabos/devices/workstation/GN/GN_station.json \
  --ak <你的AK> \
  --sk <你的SK> \
  --upload_registry \
  --addr https://leap-lab.bohrium.com/api/v1 \
  --disable_browser
```

等价：

```bash
python unilabos/app/main.py -g unilabos/devices/workstation/GN/GN_station.json ...
```

kernel-v2 文档要求：真机/生产应逐步做成独立设备包 + 单独启动图，**不要**用 `demo-lab` 的 `dry-run.json` 承载生产地址。

---

## 8. 常见问题速查

| 现象 | 处理 |
|------|------|
| `python: can't open file '...unilabosappmain.py'` | 路径用了 `\`，改为 `/` |
| `No module named 'msgcenterpy'` | `which unilab` 指到 3.13，改 PATH 用 `~/uni-lab-os/bin` |
| `start` 报 `backend_start_failed` 但 `status` 为 `ready` | 未加 `--wait` 或旧进程仍在；以 `status` 为准 |
| 误执行 `workspace stop` 后页面不可用 | 用 4.2 带 `--wait 180` 再 `start` |
| `/console/` 404 | 未 `npm run build`；开发期用 4174 的 Vite |
| `demo-lab` 的 `pip install -e .` 找不到 `unilabos` | 先在仓库根目录 `pip install -e .` |
| `npm error ENOENT .../Uni-Lab-OS/package.json` | 未 `cd frontend`；先 `cd .../Uni-Lab-OS/frontend` 再 `npm run dev` |
| `zsh: command not found: #` | 把文档里的 `# 注释` 行也粘贴进终端了；只粘贴命令行 |
| 前端曾 `ready` 但页面打不开 | 是否按了 `Ctrl+C` 停掉了 Vite；或 Backend 已 stop，先查 `workspace status` |
| `pip install -e .` 后又在根目录跑 `npm run dev` | 根目录只能装 Python；前端命令仍要在 `frontend/` |
| 总监控「错误处置状态暂不可用 / 请刷新状态后操作」 | ① `curl "$BACKEND_URL/api/v1/stations/local/error-handling"`：404 多为 Backend 进程过旧（升级代码后未重启），用 `workspace start --component backend --graph deployment/graphs/dry-run.json --runtime-mode dry-run --wait 180` 重启；503 为调度桥未就绪，等 readiness 为 `ready` 再试。② 重启 Backend 后端口会变，必须在 `frontend/` 终端重新取 `BACKEND_URL` 并重启 Vite（`VITE_EDGE_API_URL` 仍指向旧端口时会一直读失败）。 |

---

## 9. 相关文件

| 文件 | 用途 |
|------|------|
| `start` | GN 真机 + leap-lab 示例命令 |
| `demo-lab/README.md` | 示例设备包完整演示流程 |
| `GN_station.json` | GN 现场设备拓扑 |
| 仓库 `frontend/README.md` | 前端构建与代理说明 |
| 仓库 `docs/product_manual/installation.html` | 官方 kernel-v2 安装与第 7 节 dry-run |

---

*文档更新时间：2026-09-16（补充：错误处置 banner 与 Backend 重启/Vite 端口对齐；须在 `frontend/` 下 `npm run dev`）。*
