# 两个工作流的资源范围并行实测

2026-09-06，本地 mamba `unilab` 环境，连接 Kubernetes `edge/plc-sim`。四种组合全部通过：8 个任务、16 次真实 OPC UA 模拟工艺命令，所有任务 succeeded；每种组合结束后活动 Claim 和软件 Lease 均为 0。

## 两个工作流

- [工作流 A](workflow-a.json)：申请根范围 `region` → S06 动作 A1 → S06 动作 A2 → 释放范围。
- [工作流 B](workflow-b.json)：申请根范围 `region` → S07 动作 B1 → S07 动作 B2 → 释放范围。

两个文件是资源计划编译器使用的作者图，加上验证运行器的设备映射信息；可由配套脚本直接编译、绑定并执行，**不是网页编辑器的导入格式，也没有发布到现有业务工作流列表**。本次采用的根范围覆盖两个动作，验证整个范围连续持有，而不仅是单个动作上的设备互斥。

`region` 是作者别名。同范围场景中两个工作流均绑定 `region-a`，不同范围场景分别绑定 `region-a` / `region-b`。这些身份由独立测试库存中的设备类资源记录承载，仅用于测试工作流软件资源范围；不对应 PLC 物理区域互锁。S06 / S07 始终使用不同执行设备，避免把设备互斥误当成区域互斥。

## 实测结果

| 绑定 | 提交顺序 | 实际行为 | 最大在途命令数 | 结果 |
|---|---|---|---:|---|
| 同一范围 | A、B | A1 → A2 → B1 → B2 | 1 | 通过 |
| 同一范围 | B、A | B1 → B2 → A1 → A2 | 1 | 通过 |
| 不同范围 | A、B | A、B 在 S06/S07 并行，各自内部串行 | 2 | 通过 |
| 不同范围 | B、A | B、A 在 S07/S06 并行，各自内部串行 | 2 | 通过 |

同范围 A 先提交时，A2 完成于相对时间 1.107 秒，B1 才于 1.180 秒派发。不同范围 A 先提交时，A1 在 0 秒派发，B1 在 0.014 秒派发；A1 在 0.554 秒才确认完成。另有 26 次双通道在途状态观测。反向提交场景有 28 次双通道在途观测。

这里“在途”指 PLC 的参数写入完成为 true、工艺选择为 1、完成位为 false；这证明模拟器命令窗口重叠，不等价于真实机械运动的传感器证据。读取到实际完成位后，脚本复位该通道，再向调度器提交完成回执。

## 证据与边界

- [实测结果及完整时序](run-01/result.json)
- [版本与文件摘要](run-01/manifest.json)
- `run-01/{same,different}-{AB,BA}/`：每次运行的两份冻结工作流、逐命令事件以及独立工作流/库存数据库。
- [相关回归日志](regression.log)
- [验证运行器](../../../scripts/validation/workflow_region_parallel.py)

本次链路是资源图编译/绑定 → 真实持久化调度核心与库存 → CallbackDispatcher → OPC UA PLC Sim。没有通过产品 HTTP API、simple/ROS 实际驱动或网页工作流编辑器，不代表这些入口已完成全链路验收；也不涉及物料搬运或故障长稳。本次未配置 SigNoz 导出，以本地事件和持久化证据为准。

## 复跑

从 Uni-Lab-OS 仓库根目录执行。确保 S06/S07 模拟工站空闲且没有其他运行器控制；输出目录必须是新的目录，避免覆盖历史证据。

```bash
kubectl -n edge port-forward svc/plc-sim 24855:4855
```

在另一个终端执行：

```bash
/Users/xiongyanfei/.local/share/mamba/envs/unilab/bin/python \
  -m scripts.validation.workflow_region_parallel \
  --endpoint opc.tcp://127.0.0.1:24855/xuse_sim/ \
  --output docs/validation/workflow-region-parallel/run-02 \
  > /tmp/unilab-region-run-02.log 2>&1
```

成功要求进程退出码为 0、四个 case 全部 passed、任务全部 succeeded、占用和租约归零。任何断言失败均保留证据，不强行清锁后改成成功。完成后停止自己创建的端口转发。
