# gn-lab — GN 有机合成工作站设备包

真机驱动代码仍在上一级目录 `../`（`gn_*.py`、`robotic_arm.py`、`rotary_stack.py`、`liquid_handling/` 等）。  
本包是 **kernel-v2 工作区** 用的注册与启动图壳层：AST 扫描 `gn_lab/devices/gn_station_devices.py`，图里引用 `community.gn_lab.<device_id>`。

## 安装与自检

```bash
cd /path/to/Uni-Lab-OS
pip install -e .

cd unilabos/devices/workstation/GN/gn-lab
pip install -e .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q
unilab package inspect --path . --out ./dist/inspect
```

## 先仿真

```bash
unilab workspace start \
  --workspace . \
  --graph deployment/graphs/dry-run.json \
  --runtime-mode dry-run \
  --startup-mode develop \
  --wait 300
```

控制台应出现 5 条工作流（快换单点调试 + 3 条金四面体 + 9320移液）。仿真不连 PLC。

## 再真机

将 `deployment/graphs/gn-station.json` 里的 `url` / `host` 改成现场地址后：

```bash
unilab workspace start \
  --workspace . \
  --graph deployment/graphs/gn-station.json \
  --runtime-mode normal \
  --startup-mode develop \
  --wait 300
```

OPC 节点表 `opcua_gn1.3.7.csv` 等仍放在 `../`（GN 目录）；驱动里相对路径会按 GN 驱动文件所在目录解析。

## 更新启动图 / 工作流

```bash
python scripts/convert_gn_station_graph.py
python scripts/convert_legacy_workflows.py
```

## 与 legacy `unilab -g GN_station.json` 的关系

旧入口仍可用内置注册表 id（`gn_workstation` 等）。工作区模式请用本包图里的 `community.gn_lab.*` class。
