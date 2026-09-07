# 事实依据与版本

本手册以运行代码为产品事实，以 SZLab 驱动和工作流作为示例。上游 README 用于说明公开的 Conda 环境分类与 Python 基线；历史测试报告和旧界面只用于发现线索。当任何说明与当前实现冲突时，以可执行代码、当前 Graph、发布目录和目标环境只读 API 为准。

## 本次核对范围

| 来源 | 版本/状态 | 用途 |
| --- | --- | --- |
| Uni-Lab-OS | `f2295f7c2de4890306a89dd22f1bb25fcf4fb7b4`，并核对当前工作树 | Runtime、Console、Inventory、Workflow、Scheduler、CLI 和 MCP。 |
| deepmodeling/Uni-Lab-OS README | 2026-09-07 在线读取 | `unilabos` / `unilabos-env` / `unilabos-full` 环境分类和 Python 3.11.14 基线；启动细节由当前产品代码复核。 |
| Uni-Lab-SZLab | `f0958f5b2d1ba0b145993778c2e1d22519819fba`，并区分未提交实验文件 | 设备、资源、Graph、动作、工艺工作流和真机边界。 |
| uni-lab-fe | 当前工作树 | Theia Workbench 入口与 Profile 能力矩阵。 |
| unilab-domain-repo-builder | 当前工作树，并核对 Workbench 内置副本 | 本地领域仓库 Agent 的操作合同；`evals` 只作为评测定义，不作为通过证据。 |
| uni-lab-backend | 当前工作树 | Go Backend 管理后台、Scheduler 和安全边界。 |
| PLC-Sim | `ff71ba2772af3e994f32fb7b6b2a9021afc2616b` | OPC UA/Modbus 仿真能力和边界。 |
| 目标 Kubernetes | 当前演示命名空间，2026-09-07 实测 | 当前镜像、模式、工作流、在线设备、Service 和公网入口；公开手册不记录人员命名信息。 |

参考站点 [Automata Topics](https://docs.automata.tech/topics) 只用于信息架构和阅读样式：左侧分组目录、中心正文、右侧页内目录，以及“Getting started / Topics / Concepts / Tutorials / Reference”式内容分层；不作为 Uni-Lab 功能事实来源。

## 当前部署快照

| 项目 | 实测值 |
| --- | --- |
| Workspace Deployment | `unilabos-local-debug` |
| Edge Deployment | `unilabos-local-edge` |
| Runtime 镜像 | `unilabos-szlab-local-debug:e237fb99-81215bcb-all-workflows-v10-20260906` |
| 进程角色 | `workspace_backend` + `edge_runtime` |
| 启动模式 | `develop` |
| 动作模式 | `real`，活动 Graph 的 PLC URL 指向 `plc-sim:4855` |
| 工作流加载 | `23/23` |
| Edge | `connected: true`，10 个在线节点 |
| Console/API | NodePort `30183` |
| SigNoZ | NodePort `30151` |
| PLC-Sim Web GUI / OPC UA | NodePort `30160` / `30161`；Server、SZLab Agent 与 Edge Session 均已连接 |
| 产品说明书 | NodePort `30184` |

这些值描述检查时刻，不是永久配置。运行前应重新查看 Readiness。

## 功能到代码的追踪

| 功能域 | 主要事实文件 |
| --- | --- |
| Console 导航与连接 | `frontend/src/components/AppShell.tsx`、`frontend/src/App.tsx` |
| 安装与环境 | `README.md`、`.conda/*/recipe.yaml`、`scripts/dev_install.py`、`frontend/package.json` |
| Workspace 启停与配置 | `unilabos/workspace_host/{cli,launch,host}.py`、`unilabos/config/config.py` |
| Python 工作流 DSL | `unilabos/workflow/{authoring,authoring_ast,python_workflow_import}.py` |
| 总监控 | `frontend/src/pages/OverviewPage.tsx` |
| 物料 | `frontend/src/pages/MaterialsPage.tsx`、`unilabos/app/scheduler/inventory/backend_api.py` |
| 试剂 | `frontend/src/pages/ReagentsPage.tsx`、`unilabos/app/scheduler/inventory/reagent_api.py` |
| 实验操作 | `frontend/src/pages/OperationsPage.tsx`、参数编辑组件 |
| 工作流 | `frontend/src/pages/WorkflowsPage.tsx`、`unilabos/app/workflow_api.py` |
| 任务与恢复 | `frontend/src/pages/TasksPage.tsx`、`unilabos/workflow/` |
| 调度与库存准入 | `unilabos/app/scheduler/service.py`、`inventory/dispatch_admission.py` |
| 设备与扩展 | `unilabos/registry/`、`unilabos/devices/`、`unilabos/resources/` |
| SZLab 工作流 | `Uni-Lab-SZLab/package.yaml`、`szlab_poly_studio/workflows/` |
| SZLab 设备 | `Uni-Lab-SZLab/szlab_poly_studio/devices/`、`common/plc_gateway.py` |
| 实验室仓库加载合同 | `unilabos/package_manager/`、`unilabos/registry/`、`unilabos/workflow/source_manifest.py`、`workspace_host/launch.py` |
| Kubernetes 部署与上线 | 仓库随附的 `szlab-local-debug/` 部署样例、`Uni-Lab-SZLab/deployment/kubernetes-docker-desktop/edge-namespace/stack.yaml` |
| 活动实验室布局 | `Uni-Lab-SZLab/deployment/graphs/szlab-local-debug.json` |
| Workbench 能力 | `uni-lab-fe/packages/services/src/capabilities.ts`、`packages/workbench-theia/src/browser/` 与 `packages/workbench-session/src/{agent-sidecar,workspace-skills}.ts` |
| Workbench 托管 Skill | `uni-lab-fe/apps/workbench/resources/workspace-skills/manifest.json`、`unilab-domain-repo-builder/` |
| Go Backend | `uni-lab-backend/internal/`、`uni-lab-backend/frontend/src/` |
| PLC 仿真 | `PLC-Sim/PLC-Sim/`、`PLC-Sim/Modbus-Sim/` |

## 发现并纠正的口径冲突

### 工作流数量

SZLab README 仍写 13 个工作流；较早测试和迁移清单使用 23；当前已提交 Catalog 有 24；本地未提交工作树再增加 2 个调度实验夹具。目标运行环境实测只加载 23 个。因此本手册的用户清单以目标环境 23 个为准，其他内容按源码/实验状态单列。

### 多套界面

Uni-Lab OS 内置 Console、Go Backend 管理后台、Theia Workbench 和旧任务台原型有不同导航与能力。主操作步骤只使用当前部署的内置 Console；其他入口放在[产品入口与 Workbench](interfaces.md)。

### Debug 与 step

部分 Workbench、CLI 和 MCP 代码仍提供旧 debug 命令，但 Uni-Lab-OS API 已固定返回 410。手册只推荐标准 step Task。

### 物料写入

Theia Workbench 内存在物料写组件，但当前静态 Profile 对所有 Material 写能力均为 false。目标内置 Console 则实际提供创建、放置和卸载。因此能力按“客户端 + Profile”描述，不笼统写成“前端支持”。

### 活动 Graph 与旧 README

SZLab README 写默认 PLC `auto_connect: false`，当前 `szlab-local-debug.json` 为 `auto_connect: true` 并连接集群内 PLC-Sim。手册以活动 Graph 为准。

### 仿真与真机

历史 E2E 只证明当时版本连接远程 OPC UA 模拟器的结果；当前优化镜像验证了 Catalog/import/ROS 冒烟，但没有重新完成全套 PLC-Sim A/B 或物理硬件回归。手册不会把这些证据表述为“真机生产就绪”。

## 如何维护这本手册

每次产品能力变更后，按以下顺序更新：

1. 扫描实现、测试、Graph 和发布目录；
2. 在目标环境读取 OpenAPI、Readiness、在线设备和工作流目录；
3. 先更新能力状态表，再更新操作步骤；
4. 对新增写操作补充前置条件、成功信号、失败语义和恢复方法；
5. 对源码存在但未部署的能力明确标记，不提前写入用户主路径；
6. 用 Sphinx `-W --keep-going` 构建，并检查站内链接和公开入口。

<div class="evidence">
<strong>核对原则</strong>：代码与部署事实优先；SZLab 只作为实际驱动/工作流示例；外部参考站点只决定文档组织和视觉风格，不决定产品能力。
</div>
