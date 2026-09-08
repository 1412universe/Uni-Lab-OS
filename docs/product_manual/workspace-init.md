# 初始化工作区（unilab workspace init）

:::{admonition} 阅读角色
- **业务负责人**：确定设备包用途、业务名称、负责人和保存位置。
- **开发人员**：创建目录和基础文件，并填写包身份与初始配置。
- **验收人员**：确认没有覆盖既有项目，且生成结构符合设备包规范。
:::

:::{note}
`unilab workspace init` 用于安装 OS 后创建第一个设备包，不要求本机预先存在设备包。该命令只创建全新的工作区；目标路径已经存在时会直接失败，不会合并或覆盖任何文件。
:::

## 这一步会得到什么

完成后，你会得到一个带教学示例的设备包项目：一个无硬件副作用的回显驱动、一个调用该动作的工作流，以及只装载示例设备的 `dry-run` 启动图。它可以立即接受静态检查和测试，但还不能连接真实设备。

开始前只需要确定：

| 项目 | 示例 | 要求 |
| --- | --- | --- |
| 设备包保存目录 | `/Users/name/labs/sample-lab` | 使用尚不存在的新目录，不覆盖现有项目 |
| 包名 | `sample-lab` | 只用小写英文字母、数字、点、连字符和下划线，并以字母开头 |
| 负责人 | `张三` | 负责版本、现场配置和交付确认 |
| 初始用途 | `样品前处理设备包` | 初始化后填写到需求卡和 README |

## 使用命令初始化

打开已经安装并激活 Uni-Lab OS 的终端，先确认命令存在：

```bash
unilab workspace init --help
```

设置名称与路径。目标目录必须尚不存在；包名可以使用发布形式 `sample-lab`，也可以使用 Python 形式 `sample_lab`：

```bash
export DEVICE_PACKAGE_ROOT="/absolute/path/to/new-device-package"
export DEVICE_PACKAGE_NAME="sample-lab"

unilab workspace init \
  --output "$DEVICE_PACKAGE_ROOT" \
  --name "$DEVICE_PACKAGE_NAME"
```

`--name` 可以省略。省略时，命令会从输出目录名派生包身份，例如目录 `sample-lab` 会得到发布名称 `sample-lab` 和 Python 包名 `sample_lab`。如果目标目录已存在，命令返回 `workspace_exists`，已有内容保持不变。

普通输出会显示工作区路径、两种包名、首先要填写的需求卡和下一组检查命令；自动化脚本或 Agent 使用 `--json` 可取得相同信息和 `nextCommands` 数组。

## 检查生成结果

命令成功后会一次性生成：

```text
new-device-package/
├── .gitignore
├── DEVICE_PACKAGE_REQUIREMENTS.md
├── README.md
├── package.yaml
├── pyproject.toml
├── sample_lab/
│   ├── __init__.py
│   ├── devices/
│   │   ├── __init__.py
│   │   └── demo_device.py
│   ├── resources/
│   ├── experiment_operations/
│   └── workflows/
│       ├── __init__.py
│       └── demo_workflow.py
├── deployment/
│   ├── local_config.py
│   └── graphs/
│       └── dry-run.json
└── tests/
    └── test_workspace_contract.py
```

Python 包必须直接位于工作区根目录，并包含 `__init__.py`。不要把它放到额外的 `src/` 目录中。

`demo_device.py` 的 `echo` 动作只回显输入文本，不连接或控制任何硬件；`demo_workflow.py` 展示设备类型导入、设备实例选择、动作调用和结果返回。它们是可编译的教学示例，不代表真实设备能力。

生成的 `package.yaml` 已登记示例工作流，工作流 UUID 会根据包名稳定派生，并与源码中的 `@workflow` 一致。删除教学工作流时，应同时删除清单中的对应登记；没有工作流时使用 `workflows: []`，不能写 `null`。

`deployment/graphs/dry-run.json` 只装载无硬件副作用的 `demo_device_01`，不包含生产设备地址、账号或密钥。工作流中的设备选择必须与启动图实例 ID 一致。接入真实设备和物料时，应新建联调或生产启动图，不要直接把真实参数补进教学图。

## 安装并检查生成的设备包

```bash
cd "$DEVICE_PACKAGE_ROOT"
python -m pip install -e '.[dev]'
python -m pytest -q
unilab package inspect --path . --out dist/inspect
```

先确认示例可被加载，再根据 `DEVICE_PACKAGE_REQUIREMENTS.md` 中已经确认的设备资料、动作单位、物料位置和安全联锁替换示例。未知信息保持“待确认”，不能从其他项目复制设备实例 ID、地址、UUID 或安全阈值。

## 第一次安全启动

显式使用生成的教学启动图：

```bash
unilab workspace start \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --graph "$DEVICE_PACKAGE_ROOT/deployment/graphs/dry-run.json" \
  --runtime-mode dry-run \
  --startup-mode develop \
  --json
```

查看状态和页面地址：

```bash
unilab workspace status \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --json
```

停止工作区：

```bash
unilab workspace stop \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --json
```

## 命令参数与失败行为

| 参数 | 是否必填 | 作用 |
| --- | --- | --- |
| `--output PATH` | 是 | 创建新的设备包工作区；目标必须不存在 |
| `--name NAME` | 否 | 指定包名；接受 `sample-lab` 或 `sample_lab`，省略时从输出目录名派生 |
| `--json` | 否 | 使用稳定 JSON 结果，便于脚本和 Agent 读取 |

命令在写文件前完成路径和名称校验。目标已经存在时返回 `workspace_exists`；包名不符合规范时返回 `invalid_package_name`；写入中途失败时会清理本次新建的目标，不留下半个工作区。

如果 `unilab workspace init --help` 不可用，说明安装的 OS 版本早于该能力。先按[系统安装](installation.md)更新 OS，再回来创建；不要在未知版本上手工猜测命令参数。

## 完成标准

- [ ] OS 已独立安装并能执行 `unilab --help`；
- [ ] `workspace init` 执行成功，目标目录原先不存在；
- [ ] `pyproject.toml`、`package.yaml` 与 Python 包名一致；
- [ ] 示例驱动、示例工作流、测试和 `dry-run` 启动图均已生成；
- [ ] `pytest` 和 `package inspect` 通过；
- [ ] `workspace start` 能以 `dry-run + develop` 启动，状态命令能返回页面地址；
- [ ] 初始启动图不包含生产地址、账号或密钥；
- [ ] 可以继续执行[工作区的四道验证门](workspace.md#步骤五通过四道验证门)。
