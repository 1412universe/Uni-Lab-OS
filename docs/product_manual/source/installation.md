# 系统安装

:::{admonition} 阅读角色
- **业务负责人**：确认部署目的、设备包来源、运行环境、安全边界和成功标准。
- **开发或运维人员**：执行安装、配置、启动、升级和故障处理。
- **验收人员**：核对版本、运行状态、失败路径、安全配置和回滚能力。
:::

本页说明如何在一台新的 Mac 上安装 Uni-Lab OS，并加载用户自行准备的设备包。设备包决定现场有哪些设备、物料、启动图和可选仿真组件，因此安装系统后必须先检查设备包配置，再决定怎样启动。

:::{important}
Uni-Lab OS 是通用运行基础；设备包由用户自行准备，不随本页提供。示例中的目录、端口和名称都是占位符，必须替换为用户设备包中已经确认的配置。第一次启动建议使用模拟设备或隔离测试环境。
:::

## 1. 安装前需要用户提供什么

| 内容 | 用途 | 必须确认 |
| --- | --- | --- |
| 设备包目录或发布文件 | 提供设备、物料和工作流 | 来源可信、版本明确、文件完整 |
| `package.yaml` | 声明设备包身份和可发布内容 | 文件存在且能通过检查 |
| 启动图（Graph JSON） | 声明本次启动的设备与物料实例 | 使用模拟、测试还是真实配置 |
| Python 版本与依赖 | 创建兼容运行环境 | 与 Uni-Lab OS 版本兼容 |
| 可选仿真器 | 模拟 PLC 或其他外部设备 | 仅在设备包确实需要时安装 |
| 连接配置 | 串口、IP、端口、超时等 | 测试和生产配置分开 |
| 密钥提供方式 | API 令牌、账号、证书 | 不写入代码、启动图 或文档 |

如果用户还没有设备包，请先完成[工作区初始化](workspace-init.md)和[设备接入模板](device-template.md)，再返回本页。

## 2. 系统要求

| 项目 | 建议要求 |
| --- | --- |
| 操作系统 | macOS 10.15 或更高版本 |
| Python | 用户设备包指定的 3.11.x |
| Node.js | 前端所要求的版本 |
| 磁盘空间 | 至少 10 GB 可用空间 |
| 网络 | 能访问团队提供的软件源 |
| Docker | 只有设备包配置明确要求时才安装 |

## 3. 安装基础工具

### 3.1 Xcode Command Line Tools

```bash
xcode-select --install
```

系统提示已经安装时可以继续，否则等待安装完成。

### 3.2 Homebrew、Git、jq、Node.js 和 Miniforge

先按 Homebrew 官方说明完成安装，再执行：

```bash
brew install git jq node@24
brew link --overwrite --force node@24
brew install --cask miniforge
```

初始化 Miniforge：

```bash
MINIFORGE_ROOT="$(brew --prefix)/Caskroom/miniforge/base"
export PATH="$MINIFORGE_ROOT/bin:$MINIFORGE_ROOT/condabin:$PATH"
"$MINIFORGE_ROOT/condabin/conda" init zsh
exec zsh
```

检查工具：

```bash
git --version
jq --version
node --version
npm --version
mamba --version
```

每条命令都能显示版本号后再继续。

## 4. 准备系统和用户设备包

```bash
mkdir -p "$HOME/unilab-workspace"
export UNILAB_INSTALL_ROOT="$HOME/unilab-workspace"
export OS_ROOT="$UNILAB_INSTALL_ROOT/unilab-os"
export DEVICE_PACKAGE_ROOT="$UNILAB_INSTALL_ROOT/device-package"
export ENV_NAME="unilab"
```

把团队提供的 Uni-Lab OS 放入 `OS_ROOT`，把用户自己的设备包放入 `DEVICE_PACKAGE_ROOT`。可以从受控代码源下载，也可以解压正式发布文件；文档和共享脚本中不要保存内部下载地址或访问令牌。

检查必要文件：

```bash
test -f "$OS_ROOT/pyproject.toml" || echo "缺少 Uni-Lab OS"
test -f "$DEVICE_PACKAGE_ROOT/pyproject.toml" || echo "缺少设备包 pyproject.toml"
test -f "$DEVICE_PACKAGE_ROOT/package.yaml" || echo "缺少设备包 package.yaml"
```

## 5. 先识别设备包需要哪些组件

不要直接套用某个固定启动命令。先检查用户设备包中的 `package.yaml`、启动图、README 和部署配置，填写下面的启动确认表：

| 检查项 | 判断方法 | 对安装的影响 |
| --- | --- | --- |
| 包内有 `virtual`/`mock` 设备 | 启动图 的 `class` 指向模拟类型 | 通常不需要外部仿真器 |
| 启动图 包含 PLC/OPC UA | 节点配置含 PLC 或 OPC UA 地址 | 需要真实 PLC 或匹配的仿真器 |
| 启动图 包含串口设备 | `config` 含 `port`、`baudrate` | 需要线缆、端口权限或串口模拟器 |
| 启动图 包含网络/API 设备 | `config` 含服务地址 | 需要服务可达、认证和 TLS 配置 |
| 设备包包含前端扩展 | 包配置明确声明 | 按设备包说明构建对应扩展 |
| 设备包要求额外进程 | 部署配置列出组件 | 安装并记录启动、停止和健康检查方式 |
| 启动图 指向真实设备 | 地址和实例属于现场生产配置 | 只能在受控现场按真机流程启动 |

:::{warning}
设备包中出现某个配置字段，不代表可以猜测其值。缺少 启动图、点位表、端口、证书或仿真器时，应停止在静态检查阶段并向用户索取，不能从其他设备包复制。
:::

## 6. 确认设备包的目录和配置

安装阶段只核对交付是否完整，不在这里重新定义设备包目录、启动图 字段或工作流格式。完整规范统一见[设备包规范与系统启动](unilabos-installation.md)。

安装人员需要确认以下内容：

| 检查项 | 合格条件 |
| --- | --- |
| 包入口 | `pyproject.toml` 存在，Python 版本和依赖要求明确 |
| 发布清单 | `package.yaml` 存在，登记的工作流源文件可以找到 |
| 启动图 | 设备包负责人明确指出本次使用的模拟、联调或生产 启动图 |
| 连接参数 | IP、端口、串口和超时来自现场交付表，不使用猜测值 |
| 敏感信息 | 账号、令牌和证书通过环境变量或密钥系统提供 |
| 附加组件 | 需要仿真器或厂商服务时，提供版本、启动、检查和停止方法 |

设备包交付不完整时，应记录缺失项并退回补充。安装人员不能自行编造设备类型、协议点位、物料尺寸、安全范围或生产地址。

## 7. 创建 Python 环境

```bash
mamba create -n "$ENV_NAME" \
  --override-channels \
  -c uni-lab \
  -c robostack-staging \
  -c conda-forge \
  python=3.11.14 \
  "uni-lab::unilabos-env=0.11.4" \
  -y
```

本页采用源码安装路径，因此使用 `unilabos-env` 准备 Python/ROS 依赖，再在下一步安装 Uni-Lab OS 源码。如果设备包锁定了其他精确版本，必须先确认它与 Uni-Lab OS 兼容，不能只修改其中一个版本。

## 8. 安装 Uni-Lab OS 和用户设备包

```bash
mamba run -n "$ENV_NAME" \
  python "$OS_ROOT/scripts/dev_install.py" --use-pip

mamba run -n "$ENV_NAME" \
  python -m pip install -e "$DEVICE_PACKAGE_ROOT"
```

执行设备包检查：

```bash
mamba run -n "$ENV_NAME" unilab package inspect \
  --path "$DEVICE_PACKAGE_ROOT" \
  --out "$UNILAB_INSTALL_ROOT/inspect-output"
```

检查报告必须能识别用户设备包声明的设备、物料和工作流。重复 ID、导入失败、缺失文件或无法解析的类型必须处理完再启动。

## 9. 安装前端

发布包已经带有构建后前端时可以跳过。需要从源码构建时执行：

```bash
cd "$OS_ROOT/frontend"
npm ci
npm test -- --run
npm run build
```

## 10. 选择并检查启动图

从用户设备包中选择与本次目的相符的 启动图：

| 目的 | 启动图 要求 |
| --- | --- |
| 软件功能验证 | 全部使用模拟设备，不包含真实生产地址 |
| PLC 联调 | 指向隔离 PLC 或仿真器，点位与设备包一致 |
| 设备集成测试 | 只包含获准参与测试的真实设备 |
| 正式生产 | 使用经过审批和版本锁定的生产 启动图 |

```bash
export GRAPH_FILE="$DEVICE_PACKAGE_ROOT/<GRAPH_RELATIVE_PATH>"
test -f "$GRAPH_FILE" || echo "启动图 文件不存在"
jq empty "$GRAPH_FILE" && echo "启动图 格式正确"
```

启动前确认：

- 每个节点 `id` 唯一；
- `class` 从设备包检查报告复制；
- `config` 与设备构造参数一致；
- 设备和物料的父子关系完整；
- 密码和令牌通过环境变量或密钥服务提供；
- 模拟、测试和生产地址没有混用。

## 11. 按设备包配置启动可选组件

只有设备包明确要求时，才启动 PLC 仿真器、厂商服务或其他进程。每个可选组件都应有：

- 明确的版本和配置文件；
- 端口和网络范围；
- 启动、健康检查和停止命令；
- 与 启动图 对应的连接地址；
- 故障、超时和恢复方法。

启动 PLC/OPC UA 仿真器时，先确认端口没有被占用：

```bash
lsof -nP -iTCP:<PLC_GUI_PORT> -iTCP:<PLC_PROTOCOL_PORT> -sTCP:LISTEN
```

共享仿真环境中不要修改点位、停止服务或切换配置。仿真只能验证通信和流程逻辑，不能替代真机安全验收。

## 12. 启动 Uni-Lab OS

```bash
mamba run -n "$ENV_NAME" unilab workspace start \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --graph "$GRAPH_FILE" \
  --runtime-mode dry-run \
  --startup-mode develop \
  --json
```

查看状态：

```bash
mamba run -n "$ENV_NAME" unilab workspace status \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --json
```

Uni-Lab OS 显示整体就绪且设备已连接后再打开操作页面。页面地址以状态命令实际输出为准，不在文档中固定某台机器的 IP 或动态端口。

启动完成后，应保存本次安装记录：Uni-Lab OS 版本、设备包版本、启动图 文件及校验值、运行模式、实际启用的附加组件和验收时间。以后升级时按这份记录比较，不能只替换其中一个组件后直接进入生产。

## 13. 第一次业务验收

优先选择用户设备包中专门用于验证、不会产生危险动作的工作流：

1. 确认设备页中的实例、中文名称和状态正确；
2. 确认动作参数有单位、范围和清楚的填写说明；
3. 执行“运行准备”或预检；
4. 使用明确的测试参数创建任务；
5. 查看每个步骤和最终结果；
6. 验证参数越界、设备离线和超时的失败路径。

如果设备包没有安全的测试工作流，不要随意选择生产工作流代替。应先由设备包作者补充模拟或只读验证路径。

## 14. 停止服务

```bash
mamba run -n "$ENV_NAME" unilab workspace stop \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --json
```

如使用外部仿真器或厂商服务，应先停止 Uni-Lab OS，再按用户设备包规定的顺序关闭其他组件。

## 15. 常见问题

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| 找不到 `mamba` | 终端未加载 Miniforge | 重新打开终端或重新初始化 |
| 找不到设备类型 | 设备包未安装、模块未导入或 ID 不一致 | 重新运行设备包检查，从报告复制类型名称 |
| 启动图 无法加载 | JSON、父子关系或配置字段错误 | 用 `jq empty` 和 启动图 检查逐项修正 |
| 页面请求失败 | Uni-Lab OS 未就绪或仍使用旧地址 | 从 `workspace status` 获取当前地址 |
| 工作流一直等待 | 设备离线、物料未就绪或安全条件未满足 | 查看具体步骤和设备状态，不要盲目重试 |
| 动作重复执行 | 超时后直接重新提交 | 先查询原任务，再决定是否恢复 |
| PLC 连接失败 | 仿真器/真实 PLC 与 启动图 配置不一致 | 核对协议地址、点位表、命名空间和握手规则 |

安装完成后，继续阅读[设备包规范与系统启动](unilabos-installation.md)和[环境与运行配置](environment.md)。
