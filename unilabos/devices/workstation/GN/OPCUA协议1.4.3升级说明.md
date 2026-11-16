# GN 工站 OPC UA 1.4.3 协议升级说明

节点表来源：`OPC_UA协议1.4.3.xlsx`（两个 sheet：`点位表`、`锁紧预锁说明`）。
生成产物：`unilabos/devices/workstation/GN/opcua_gn1.4.3.csv`（236 个点位）。

## 1. 与 1.3.9 的差异

| 类别 | 点位 | 说明 |
|---|---|---|
| 新增 | `Lock_PreTurnsSet` | 预锁圈数，Int16 写，单位 0.1 圈（20 = 2.0 圈） |
| 新增 | `Tare_the_scale` | 称重天平去皮，Int16 写 |
| 新增 | `scale_weight` | 称重天平重量反馈，协议写 REAL → 驱动按 FLOAT 读写 |
| 新增 | `Centrifuge_Time_Hour` | 离心机时间（小时），与 `Centrifuge_Time`（分钟）并存 |
| 删除 | `Centrifuge_Temperature_feedback` | 协议已移除，驱动不再引用 |
| 删除 | `Solid_Remove_static` | 协议已移除；`Remove_static` 节点本身也已不在表中 |

共享点位的 **NodeId 一字未变**（回归测试逐个比对），因此只换表不会造成读写错位。

xlsx 的 `点位表` 尾部有 3 行无名的重复/草稿行（`Robot_Operating_status` ×2、
`Robot_Protective_stop_st`），生成表格时按「无名称即丢弃」处理——它们的 NodeId
与上一行重复，属于表格残留，不是新点位。

## 2. 指令表语义变化（驱动已跟进）

### 2.1 锁紧模块：11 由「拧紧」明确为「终锁」，新增 15=预锁

1.3.9 只有 `11=电批拧紧`。1.4.3 拆成两步：

- `11=电批终锁(扭矩锁死)` — 扭矩模式，必须扭矩到达才算成功
- `15=电批预锁(按圈数,不锁死)` — 角度模式，转够圈数即成功

工艺原因（见 xlsx 附表）：四角若逐颗直接打满扭矩，第一颗锁死后盖板会翘，
后面几颗对不上孔。正确顺序是**先对角预锁四角，再逐颗终锁**，且禁止同一颗
`15` 完立刻 `11`。

驱动侧改动（`gn_locking_mechanism.py`）：

- `LockCommand.SCREW_FINAL_TIGHTEN = 11`，新增 `SCREW_PRE_TIGHTEN = 15`
- `SCREW_TIGHTEN = 11` 保留为枚举别名，旧脚本按旧名引用不会 `AttributeError`
- `execute_command(..., pre_turns=)` 新增参数，写 `Lock_PreTurnsSet`
- 预锁缺参或 `pre_turns <= 0` **在下发前就拒绝**：协议要求「Type=15 必写，≤0 拒收」，
  交给 PLC 静默拒收只会白等到超时

### 2.2 离心机：新增 11=配重天平去皮，新增小时级时长

- `CentrifugeCommand.TARE_SCALE = 11`，新增 `tare_scale()` 动作（走完整 CmdType +
  CompleteFB 握手，不再需要外部直接写 `Tare_the_scale` 节点）
- `execute_command(..., time_hours=)` 写 `Centrifuge_Time_Hour`
- `run(hours=)` 同步暴露；RUN 的超时预算按 `小时×3600 + 分钟×60 + 缓冲` 计算
- 去皮**不进** `_INTERNAL_CMDS`：那条路径会用 Y/Z 轴反馈稳定做兜底，而去皮不产生
  任何轴运动，兜底只会把失败误报成成功。去皮只承认 `CompleteFB=1`。

### 2.3 离心管液体处理：48/49 编号明确

1.3.9 的指令表把 48/49 挤在 47 后面没写编号（`47=模块4震荡关闭 48震荡模块夹爪开，49震荡夹爪关`）。
1.4.3 明确为 `48=震荡模块夹爪开`、`49=震荡夹爪关`。驱动里本就是 48/49，无需改动，
只在节点表的指令列补全了编号。

## 3. 统一指向 1.4.3

原先各驱动分别指向 `opcua_gn1.3.6.csv` / `1.3.7` / `1.3.9`，其中 **1.3.6 这个文件
从来不存在**——机械手、堆栈、真空烘箱之所以能跑，只是因为 `GNWorkstation` 预热了
按 URL 单例的 `GnPlcClient` 并加载了工站自己的那份表，子设备的 `csv_path` 被
`load_nodes_from_csv_once` 的「已加载」判断跳过了。这属于隐性依赖，一旦单独挂载
子设备就会加载失败。

本次统一为：

- 所有 GN 驱动 `DEFAULT_CSV_PATH` / 构造函数默认值 → `opcua_gn1.4.3.csv`
- `deployment/graphs/*.json`、`GN_station*.json` 里 10 处设备 `csv_path`/`xlsx_path` → 1.4.3
- 回归测试断言「仓库里不再有任何 `.py`/`.json` 引用 1.3.6/1.3.7/1.3.9」

## 4. 未做的事

- **没有现场验证**：`192.168.6.6:4840` 在本次改动时不可达（TCP 超时），因此
  `scale_weight` 的 REAL→FLOAT 写入、`Lock_PreTurnsSet` 的实际取值都只在
  节点表与驱动层面验证过，未做真机读写。上机前建议先跑一次
  `gn_workstation.get_plc_stats()` 确认节点解析数，再单点试 `Tare_the_scale`。
- **xlsx 附表提到的 5 个点位未在点位表中出现**：`Lock_PreTorqueLimit`、
  `Lock_FinalTorqueSet`、`Lock_ScrewResult`、`Lock_TorqueFB`、`Lock_AngleFB`。
  `锁紧预锁说明` 把它们列为「新增点位」，但 `点位表` 里没有对应的 NodeId。
  缺 NodeId 无法构造 OPC 地址，因此**没有**生成这 5 个点位，也未在驱动里引用。
  如果 PLC 侧确实已实现，需要补一份带 NodeId 的协议表再生成。

## 5. 测试

`tests/devices/test_gn_opcua_143_protocol.py`（8 例）：

- 节点表存在且规模合理
- 4 个新增点位的类型正确
- 已删除点位确实不在表中
- 200+ 共享点位的 NodeId 与 1.3.9 逐一致
- 指令表包含 `11=电批终锁` / `15=电批预锁` / `11=配重天平去皮` / `48`/`49`
- 锁紧：预锁枚举值、旧名别名、`pre_turns` 写入、缺参与 ≤0 拒绝
- 离心机：`TARE_SCALE` 值、`TIME_HOUR_NODE`、RUN 同时写分钟与小时、
  去皮 setpoint 为空且不在 `_INTERNAL_CMDS`
- 所有 GN 驱动不再引用 1.3.x 节点表文本
