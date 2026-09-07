# 能力状态表

本页把代码中已有能力映射到当前演示环境，避免把不同前端、API 和实验代码混写为一个产品入口。

## 当前 Console 与 Runtime

| 功能 | Console | API / Runtime | 当前结论 |
| --- | --- | --- | --- |
| 总览 KPI、活动和关注事项 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 物料层级、位置、站点、搜索 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 创建物料并原子放置 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 物料装载、移动、卸载 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 修改/删除物料 | 无按钮 | 有 | <span class="status status-limited">API 可用</span> |
| 物料 CSV 导出与条码核对 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 试剂目录 CRUD、CAS 查询 | 有 | 有 | <span class="status status-config">可用，CAS 依赖网络</span> |
| 试剂库存 CRUD、搜索和历史 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 多目标原子分装 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 试剂批量/文件导入 | 无 | 有 | <span class="status status-limited">API 可用</span> |
| 实验操作画布与发布 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| Action、condition、repeat | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 设备动作的 manual-confirm 包装 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 已发布子操作/组合工作流 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 工作流搜索、详情、拓扑、合同、源码 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| Python/JSON 导入和发布 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 从空白“新建工作流” | 入口占位 | API/authoring 有 | <span class="status status-limited">当前 Console 不可用</span> |
| 零写入预检与任务创建 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| normal / step Task | 有 | 有 | <span class="status status-ready">受运行模式约束</span> |
| 并行调度 | 任务矩阵可见 | 有 | <span class="status status-config">需 product 模式</span> |
| 节点参数、feedback、返回值、错误 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| 人工确认 | 有 | 有 | <span class="status status-ready">当前可用</span> |
| pause / resume / step / continue | 有 | 有 | <span class="status status-config">需 develop 模式</span> |
| product 模式取消任务 | 无按钮 | 有 | <span class="status status-limited">API 可用</span> |
| 工作流干预 | 有 | 有 | <span class="status status-ready">按任务触发</span> |
| 执行锁查看和强制释放 | 有 | 有 | <span class="status status-ready">高风险操作</span> |
| 失败物料转移结算 | 有 | 有 | <span class="status status-ready">高风险操作</span> |
| 未知执行结果解决 | 有 | 有 | <span class="status status-ready">按异常触发</span> |
| SigNoZ Trace 跳转 | 有 | 有 | <span class="status status-ready">当前已配置</span> |
| Scheduler 快照、历史、时间线、drain | 无专页 | 有 | <span class="status status-limited">管理员 API</span> |
| 设备目录和单动作 Task | 无专页 | 有 | <span class="status status-limited">API / Workbench</span> |
| 设备 2D/3D 实时遥测 | 布局展示 | 模型读取 | <span class="status status-limited">不是实时遥测</span> |
| PLC-Sim Web GUI、OPC UA 与 SZLab 握手代理 | 独立 GUI | 有 | <span class="status status-ready">当前已部署并连接 Edge</span> |

## 明确未接入或已退役

| 功能 | 代码事实 | 状态 |
| --- | --- | --- |
| 全局搜索 | 只提示使用页面内搜索，没有统一后端搜索。 | <span class="status status-unavailable">当前不可用</span> |
| 通知中心 | UI 未连接通知服务。 | <span class="status status-unavailable">当前不可用</span> |
| 用户/权限菜单 | UI 未连接身份和权限服务。 | <span class="status status-unavailable">当前不可用</span> |
| 运行报告导出 | 总览按钮为后续入口。 | <span class="status status-unavailable">当前不可用</span> |
| 控制台安全联锁 | 总览未接入真实安全系统。 | <span class="status status-unavailable">以设备安全系统为准</span> |
| `/api/v1/debug/*` | 后端固定返回 HTTP 410 `debug_api_retired`。 | <span class="status status-unavailable">已退役</span> |
| Breakpoint debugger | 部分旧 Workbench/CLI/MCP 仍调用退役接口。 | <span class="status status-unavailable">改用 step Task</span> |
| Robot Points | Workbench 明确显示 unavailable，OS/Backend 无点位目录。 | <span class="status status-unavailable">当前不可用</span> |
| `script` 节点执行 | 模型可表达，但标准 Scheduler 没有注册执行器。 | <span class="status status-unavailable">不可执行</span> |
| Cloud Web | 仅未来占位。 | <span class="status status-experimental">非正式产品</span> |
| 旧任务台 prototype | 多个按钮和 SSE 为占位/轮询。 | <span class="status status-experimental">历史参考</span> |

## SZLab 工艺边界

| 功能 | 已实现部分 | 当前未承诺部分 |
| --- | --- | --- |
| S05 拍照 | 工位动作与工作流节点 | 真实相机 URL、图片采集结果、算法检测、双视图 |
| S06 加液 | 原始/完整/物料感知单/双溶剂动作 | `skip_level_check`、`skip_robot` 等字段作为有效开关 |
| S07 投粉 | 扫码、读秤、转桶、投粉、历史附件 | 用 `commanded_powder_mass_g` 代表实测质量 |
| S08 开合盖 | 原始、样品瓶、试剂瓶与物料感知动作 | 工位状态与瓶盖约束自动校验 |
| S09 移液 | 移液、可复用吸头、密度、多步和吸头库存 | 工位 bind/release、可靠 stable bit |
| S1 | 驱动和危险动作双开关 | 当前 Graph 实例和在线设备 |
| Embedded simulator | 七设备动作握手、故障注入 | 真机安全、运动和电气验收 |

## “已存在”的四个层级

对任何新功能，都应依次回答：

1. 源码是否定义并能编译？
2. 当前运行镜像是否包含？
3. 当前发布目录和客户端是否提供入口？
4. 当前目标设备/仿真是否完成对应级别的验证？

只有四个答案都满足时，才应写成“本环境当前可直接使用”。

<div class="evidence">
<strong>实现依据</strong>：Uni-Lab OS 内置 Console 六个页面、<code>workflow_api.py</code> 和 Inventory API；<code>uni-lab-fe/packages/services/src/capabilities.ts</code>（Workbench Profile 能力矩阵）；<code>uni-lab-backend/internal/app/scheduler/application.go</code>（执行器注册）；SZLab 设备驱动与活动 Graph；目标环境 API 实测。
</div>
