# 物料页实时设备、库位与 Task 使用情况

## 交付范围

在 `frontend` 现有物料页增加实时使用信息，沿用设备 → 仓体 → 库位 → 物料的树结构，不新增主导航。Task 矩阵增加「占用资源」入口，跳到按该 Task 筛选的物料页；资源详情中的 Task 入口返回任务详情和现有执行锁面板。

- 库位的实际物料来自物料关系图。空库位、已放料、计划转入分别展示，计划目的地不提前改变物料位置。
- 设备执行锁、库位执行锁、任务设备占用、在途动作和 Task 物料绑定分别读取并展示。设备锁不会被解释为该设备所有库位均已锁定。
- 初始库存可以没有 Task 绑定，显示中性色及未绑定说明。有效 Task 物料绑定跟随物料当前位置；不会将历史 Task 永久附着在库位上。
- 同一 Task 在任务矩阵、物料树、资源卡片使用相同颜色。遗留执行锁和待确认状态增加文字及警示背景，颜色不是唯一提示。
- 通用或无法映射到当前物料树的资源锁保留在页面下方，不静默丢弃。
- 运行事件触发刷新，另有 5 秒轮询。读请求失败保留上次完整快照并提示，库存写操作受连接状态约束。

## 数据与接口

新增只读接口 `GET /api/v1/stations/{station_id}/resource-usage`，由 Edge 当前服务提供。读取已有 `execution_lock_lease`、`task_device_tenancy`、`workflow_task_material_claim`、Job 和 Task 数据，无新表、无迁移、无新后台服务。

返回活动占用、有效物料绑定，以及活动或仍被占用引用的 Job 上下文（Task、节点、设备、物料、预期变化）。物料图与运行信息都读取成功才发布新的前端快照；两类数据来自不同请求，不宣称跨存储原子快照。

## 自动化验证

- `npm test -- --maxWorkers=2`：29 个测试文件，305 个测试通过。
- `npm run build`：TypeScript 检查及 Vite 构建通过；存在既有的大包体积提示。
- `python -m pytest tests/workflow/test_resource_usage.py tests/workflow/test_error_control.py -q`：6 个通过。
- `git diff --check`：通过。

新增用例覆盖：初始未绑定库存、物料移动、计划转入与实际到位分离、设备/库位锁范围、取消后遗留占用、无锁在途动作、未匹配资源锁、接口只读与工站隔离、断线保留快照及事件恢复、现有物料树与库位详情导航。

## 本地部署

目标：Docker Desktop Kubernetes，`edge` namespace，`edge-workspace` Deployment。

镜像：`localhost:5050/unilabos-szlab:ubuntu24-arm64-k8s-20260910-material-usage-v14`。

固定 ARM64 镜像摘要：`sha256:055dea382e89b94b3806bec1136db572afd7dc19f3f51eeee3433f6070ed2840`。

镜像基于当前 v12，更新三个 Python 文件与 `frontend` 构建产物。Deployment 的初始化容器、调度容器和 Runtime 统一更新，同步保存到 SZLab 的 `deployment/kubernetes-docker-desktop/edge-namespace/stack.yaml`。

部署前任务共 18 个（成功 8、取消 7、失败 3），无运行中任务。本次验证不启动设备动作、不移动库存、不解除执行锁。

访问入口：

- 物料：<http://127.0.0.1:4173/console/?page=materials>
- 任务：<http://127.0.0.1:4173/console/?page=tasks>
- PLC Sim：<http://127.0.0.1:18766/>

部署过程遇到 Docker Desktop 镜像代理对尚未登记到本机镜像存储的摘要返回 500。将 ARM64 平台摘要通过本地 registry 拉入 Docker 镜像存储后，Kubernetes 成功获取该镜像；不是从外部下载基础镜像。后续更新应先执行这一步，再更新 Deployment。

## 部署后验证结果

最终 Pod：`edge-workspace-5fc8ffd6d7-29w8s`，2/2 Running，检查时重启 0 次。

- Runtime readiness 返回 ready、connected=true，设备 13 个；调度状态 running。
- 页面服务返回最新 `index-BNdjxndv.js`，资源接口及物料图接口返回 200，物料图 134 个对象。
- 任务仍为 18 个，均已结束；当前没有活动资源占用、Task 物料绑定或在途 Job。
- 浏览器验证 S04 设备显示 6 个下属库位；点击 S043 定位到仓体及对应库位详情。
- 浏览器验证初始库存未绑定 Task 的说明；任务行「占用资源」带入正确 Task；无匹配资源显示空状态，清除筛选恢复完整物料树。
- 修复实测发现的边界：筛选无结果时不能继续展示无关物料详情；资源请求超时使用中文提示。

本轮没有新增硬件运行任务。任务颜色、绑定随物料移动和计划转入的逻辑由自动化用例验证，不能替代现场多 Task 动作验收。

## 已观察到的环境限制

中间 v13 部署曾因健康检查超时重启，随后工作区候选编译超时；期间 Kubernetes 控制接口也出现 TLS 握手超时。最终 v14 重新部署成功，浏览器恢复在线。未修改全局 Docker 配置或放宽健康检查。

最终连续 4 组只读抽查均为 HTTP 200，13 个设备在线、物料图 134 个对象，但部分请求响应仍有波动（物料图最长约 12.42 秒、资源接口约 9.74 秒）。前端 10 秒超时可能触发保留快照，后续刷新能够恢复。这一环境响应稳定性问题尚未彻底定位，不能据此宣称长时间稳定性验收完成。

截图：`2026-09-10-material-resource-usage.png`。
