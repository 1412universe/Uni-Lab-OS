# Kubernetes 部署与上线

本页带你把已经通过领域仓库验证门的 Uni-Lab OS 部署到 Kubernetes，并完成公网入口、业务就绪、升级和回滚验收。先在本地完成[安装与安全加载](installation.md)，再开始本页。

:::{important}
仓库目前没有 Helm Chart，也没有可直接套用到任意集群的完整生产清单。

仓库随附的 `szlab-local-debug/` 是 SZLab 单节点临时调试样例；它在不同发布包中的父目录可能不同。本页标为“模板”的片段必须按你的集群改造。
:::

## 先认识现有两套参考

| 参考 | 已实现的事实 | 不能据此声称什么 |
| --- | --- | --- |
| 仓库随附的 `szlab-local-debug/` | 两个 Deployment、两个 Backend PVC、Secret、NodePort、PLC-Sim 等待脚本 | 不是通用生产清单；Edge 状态不持久，也没有自动 drain 生命周期 |
| `Uni-Lab-SZLab/deployment/kubernetes-docker-desktop/edge-namespace/stack.yaml` | 同 Pod 双进程、三个权威 PVC、一个本地覆盖 PVC、业务探针及 60/90 秒终止预算 | Docker Desktop 专用；覆盖层会改变运行字节，不可原样部署到服务器 |

本教程以第一套单节点清单讲解部署路径，并引用第二套清单说明生产改造方向。不要拼接两套 YAML；应复制一套作为自己的受审查基线，再逐项迁移。

## 上线完成的判据

“Pod 为 Running”不是部署成功。完成时应同时满足：

- Backend 与 Edge 使用同一发布批次的 OS、领域包、Console、Graph 和依赖；
- Backend 业务 Readiness 为 `ready`，且工作流加载数等于总数；
- Edge Readiness 为 `ready`、`connected: true`，并登记预期设备；
- Scheduler 处于 `running`，目标工作流零写入预检通过；
- 入口具有预期的 TLS、身份认证和网络限制，或已记录仅限临时测试的例外；
- PVC、Secret、备份、排空与回滚都经过演练。

如果目标只是学习，请先部署 PLC-Sim。当前 SZLab 样例虽使用 `--action_mode real`，Graph 实际指向集群内 `plc-sim:4855`；它会构造真实 Driver，但控制的是模拟 PLC，不等于真机安全验收。

## 1. 部署前检查集群

准备一个有权限的 `kubectl` 上下文、可用 StorageClass、可从节点取得的镜像，以及组织批准的域名、证书和认证入口。先做只读检查：

```bash
kubectl version --client
kubectl config current-context
kubectl get nodes -o wide
kubectl get storageclass
```

确认当前上下文就是目标集群。新建命名空间、Secret、PVC、Deployment、Service 和 Ingress 所需权限应由集群管理员授予；不要为了通过教程给运行 Pod 绑定集群管理员权限。

当前单节点样例的 PVC 固定使用 `local-path` 和 `ReadWriteOnce`。其他集群必须把 `storageClassName` 改成实际存在的 StorageClass，并确认节点故障、迁移、快照和恢复能力。

## 2. 冻结一个可追踪发布

上线前固定 Uni-Lab-OS 与领域仓库的完整 commit。两个仓库必须是干净工作树；否则镜像标签与实际内容可能不一致。

```bash
git -C /path/to/Uni-Lab-OS rev-parse HEAD
git -C /path/to/Uni-Lab-OS status --short
git -C /path/to/Uni-Lab-SZLab rev-parse HEAD
git -C /path/to/Uni-Lab-SZLab status --short
```

`status --short` 应没有输出。再按[开发一个可加载的实验室仓库](lab-repository.md#7-按四道门验证仓库)完成 inspect、Registry 导入检查、wheel 自审计和 `dry-run` 加载。

发布记录至少保存以下事实：

| 事实 | 用途 |
| --- | --- |
| OS 与领域仓库完整 commit | 定位实际源码 |
| 镜像不可变 digest 与目标平台 | 保证所有节点拉到同一字节 |
| Graph 文件及摘要 | 证明端点、设备实例和资源拓扑 |
| Package Catalog 摘要 | 证明设备、动作、资源和工作流合同 |
| Console 构建版本 | 避免页面与 API 跨代 |
| 清单版本与数据迁移版本 | 支撑审计和回滚 |

当前 SZLab Dockerfile 会把 `io.unilabos.commit` 和 `io.unilabos.szlab.commit` 写入镜像标签。它同时复制 OS 源码、`pyproject.toml`、`package.yaml`、规范 Python 包和 `deployment/`。

wheel 是发布产物与自审计证据，但当前 Runtime 仍要求 `--workspace` 指向完整源码工作区。不要只把 wheel 装进镜像，却遗漏 `package.yaml`、Graph 或工作流源码。

## 3. 构建并分发镜像

先确认 Console 已编译。当前 SZLab Dockerfile 直接复制 OS 的 `unilabos/` 目录，不会在这一阶段重新构建前端。

```bash
test -f /path/to/Uni-Lab-OS/unilabos/app/web/static/console/index.html
```

当前单节点样例的构建形状如下。它是已有 Dockerfile 的调用方式，不是通用发布脚本；其中基础镜像和 OTel 运行时镜像必须已在目标节点可用。

```bash
export OS_SOURCE="/path/to/Uni-Lab-OS"
export SZLAB_SOURCE="/path/to/Uni-Lab-SZLab"
export DEPLOY_SOURCE="/path/to/your-reviewed/szlab-local-debug"
export OS_REVISION="$(git -C "$OS_SOURCE" rev-parse HEAD)"
export SZLAB_REVISION="$(git -C "$SZLAB_SOURCE" rev-parse HEAD)"
export RUNTIME_IMAGE="unilabos-szlab-local-debug:${OS_REVISION:0:8}-${SZLAB_REVISION:0:8}"

cd "$OS_SOURCE"
nerdctl -n k8s.io build \
  --build-context szlab="$SZLAB_SOURCE" \
  --build-arg OS_REVISION="$OS_REVISION" \
  --build-arg SZLAB_REVISION="$SZLAB_REVISION" \
  -f "$DEPLOY_SOURCE/Dockerfile" \
  -t "$RUNTIME_IMAGE" \
  .
```

同目录的 `README.md` 给出了干净 detached worktree 样例。将 `DEPLOY_SOURCE` 指向已审核的 `szlab-local-debug/` 目录，并用本次固定提交替换过期 SHA 和镜像标签。

该 Dockerfile 还假定基础镜像已包含 SZLab 运行依赖，只复制领域源码。新领域仓库不能原样套用；它的发布镜像必须按 `pyproject.toml` 安装固定依赖并通过 `pip check`。

`imagePullPolicy: Never` 只适用于镜像已经导入同一 Kubernetes 节点的单节点环境。多节点部署应推送到所有节点可访问的可信 Registry，并在清单中使用不可变 digest。

私有 Registry 的拉取凭证应使用组织既有的 Secret 或工作负载身份。仓库没有提供通用 Registry 登录脚本；不要把用户名、密码或长期 token 写进 Dockerfile、Graph、YAML 或 Git。

## 4. 让 Backend 与 Edge 使用同一代 Workspace

当前拓扑把产品拆成两个职责：

| 项目 | Backend | Edge |
| --- | --- | --- |
| `--process_role` | `workspace_backend` | `edge_runtime` |
| `--app_bridges` | `fastapi` | `edge_control` |
| 服务端口 | `18003` | `0`，不启动 HTTP |
| 职责 | Authoring、Inventory、Scheduler、本地 Authority | 加载 Driver、登记动作、执行设备 Job |

两边都应显式使用 `--control_plane local`、`--backend ros`、同一个 `--workspace` 合同、同一 Graph 内容和同一启动模式。Edge 通过 Backend Service 地址连接本站 Authority 和 Scheduler。

当前单节点样例没有传 `--run_mode`，因此沿用代码默认的 `develop`。若上线仅展示已发布普通工作流，应在两个进程中同时显式设置 `--run_mode product`；它不提供 TLS 或登录能力。

当前样例在 Backend 上把作者工作区 PVC 挂载到 `/opt/runtime-src/szlab`，Edge 则从不可变镜像读取同一路径。首次挂载时 init container 会播种源码，之后检测到标记便不再覆盖。

因此只换镜像不能升级已有作者工作区。升级领域包时，必须先备份 PVC，再显式合并或迁移 Python 源、`package.yaml` 和 `deployment/`。

迁移后重新通过四道门，并确认 Backend 与 Edge 的 Catalog、Graph 和驱动合同一致。

不得长期运行 Backend 新版、Edge 旧版或反向组合。当前代码尚未把混合版本声明为受支持升级方式；维护期间应阻止新任务，成组更新并成组验收。

## 5. 创建命名空间和控制 Secret

先为自己的环境选择不含人员姓名的命名空间。后续命令复用 `TARGET_NAMESPACE`；如果换了终端，应重新导出该变量。下面命令只创建或复用命名空间，不删除已有资源：

```bash
export TARGET_NAMESPACE="unilabos-demo"
kubectl create namespace "$TARGET_NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Backend 和 Edge 必须读取同一个非空 Edge 控制密钥。当前清单引用 Secret `unilabos-local-control` 的 `api-key`：

```bash
kubectl create secret generic unilabos-local-control \
  -n "$TARGET_NAMESPACE" \
  --from-literal=api-key="$(openssl rand -hex 32)"
```

首次创建后不要把 Secret 导出到工单或日志。生产环境优先使用组织的 Secret 管理器。`secretKeyRef` 在进程启动时进入环境变量；更新 Secret 不会让现有 Pod 自动读取新值。

轮换时先排空，再在同一维护窗口重启 Backend 与 Edge，并等待两者完成业务验收。不要只重启一侧，否则控制密钥会暂时错代。

`UNILABOS_EDGECONTROLCONFIG_EDGE_KEY` 是稳定身份，不是密码。每个逻辑 Edge 使用唯一、稳定的值；不要用它替代 `api-key`，也不要让两个活动 Edge 争用同一身份。

## 6. 规划 PVC 与副本数

当前调试清单创建：

- `unilabos-szlab-authoring-workspace`：2 GiB，保存可编辑领域源码和发布记录；
- `unilabos-local-debug-runtime`：5 GiB，保存 Backend 的 Workflow、Inventory 与本地 Authority 数据；
- Edge `/runtime`：`emptyDir`，Pod 重建后协议状态重新登记，不提供生产级恢复保证。

生产模板应另外为 Edge 的 `edge_control.db`、命令、反馈和结果发件箱提供专用持久卷。Docker Desktop 参考清单中的 `edge-runtime` PVC 展示了这一边界，但其名称和 StorageClass 不能直接照搬。

该参考清单还有 `edge-local-overrides` PVC，会覆盖 Scheduler、Inventory、Console 和工作流源码。它是开发覆盖层，不是生产发布方式；若测试环境保留它，必须记录摘要，并纳入备份、升级和完整清单回滚。

不要让多个 Pod 同时写同一 SQLite 目录。现有清单使用 `replicas: 1`、`ReadWriteOnce` 和 `strategy: Recreate`；在系统没有外部化状态与多副本协议前，不要把 Backend 或同一 Edge 水平扩容。

部署前确定备份和恢复方案。`local-path` 是节点本地存储，节点丢失可能同时丢失数据；应使用集群批准的 VolumeSnapshot、存储备份或停机一致性备份，并实际演练恢复。

## 7. 准备 Graph 与依赖服务

容器内不能继续使用开发机的 `127.0.0.1`。当前 ConfigMap 会在 init 阶段复制 SZLab Graph，把 PLC URL 改为 `opc.tcp://plc-sim:4855`，并把机械臂 journal 改到可写运行目录。

该初始化脚本尝试 150 次。每次连接最多等待 2 秒，失败后再等待 2 秒，因此最坏情况接近 600 秒。PLC-Sim Server 未启动时，Backend 和 Edge 会停留在 init，而不是错误进入 Ready。

按[PLC-Sim 仿真器](plc-sim.md)先准备模拟器。仓库参考 Service 会把 GUI 和 OPC UA 分别暴露为 NodePort `30160`、`30161`，不能直接作为生产清单应用。

复制清单后，默认把 Service 改成 `ClusterIP` 并删除两个 `nodePort`。管理员可临时 port-forward GUI；Edge 仍通过集群内 `plc-sim:4855` 连接。只有经过审批和来源限制的测试网络才能保留 NodePort。

```bash
export PLC_SIM_SOURCE="/path/to/your-reviewed/plc-sim.yaml"
export PLC_SIM_RELEASE="/path/to/release/plc-sim.yaml"

cp "$PLC_SIM_SOURCE" "$PLC_SIM_RELEASE"
rg -n 'type:|nodePort:' "$PLC_SIM_RELEASE"
# 人工改成 ClusterIP 并删除 nodePort 后再继续。
kubectl diff -f "$PLC_SIM_RELEASE"
kubectl apply -f "$PLC_SIM_RELEASE"
kubectl rollout status deployment/plc-sim \
  -n "$TARGET_NAMESPACE" --timeout=5m
kubectl -n "$TARGET_NAMESPACE" port-forward service/plc-sim 18765:18765
```

最后一条命令会保持前台运行。另开浏览器访问 `http://127.0.0.1:18765/`，完成检查后用 `Ctrl+C` 结束端口转发；这不会停止集群中的 PLC-Sim。

PLC-Sim Pod 的 HTTP Probe 只证明 GUI 进程存活。必须在 GUI 中确认 Server 和 Agent 均为 running；再从集群内确认 `plc-sim:4855` 可达。

真机部署时，删除对 PLC-Sim 的隐式依赖，逐个审核 Graph 中的主机、端口、证书、凭证和 `auto_connect`。Graph 一旦指向真实设备，`--action_mode real` 就可能产生物理动作。

## 8. 审查并应用现有清单

不要直接修改仓库里的参考文件。复制为本次发布清单，然后逐项审查：

```bash
cp "$DEPLOY_SOURCE/unilabos-local-debug.yaml" \
  /path/to/release/unilabos-szlab.yaml
rg -n 'namespace:|image:|imagePullPolicy:|storageClassName:|nodePort:' \
  /path/to/release/unilabos-szlab.yaml
```

必须把两个 Deployment 及其 init container 的所有 `image:` 改为同一发布镜像。单节点本地镜像保留 `Never`；Registry 镜像使用集群规定的拉取策略和不可变 digest。

同时核对 Namespace、StorageClass、PVC 容量、Secret 名与键、Backend Service 地址、Edge 身份、Graph、运行模式、ROS 隔离参数、OTLP 端点、资源请求和 NodePort 冲突。

先让 Kubernetes 展示差异。`kubectl diff` 在存在预期差异时通常返回状态码 1，应阅读输出，而不是把它当作清单校验失败。

```bash
kubectl diff -f /path/to/release/unilabos-szlab.yaml
kubectl apply -f /path/to/release/unilabos-szlab.yaml
```

当前参考 README 包含删除整个命名空间的重置命令。它会删除该命名空间内所有资源和 PVC 数据，不属于正常安装、升级或回滚。

除非已有核验过的备份并获得明确授权，否则不要执行。

## 9. 配置 Kubernetes Probe

代码把存活与业务就绪分开：`/api/v1/health` 证明 HTTP/Scheduler 进程存活；`/api/v1/readiness` 证明工作流运行时完成加载；`/api/v1/edge/readiness` 证明 Edge 当前连接且设备目录有效。

当前单节点样例中 Backend 的 startup、readiness 和 liveness Probe 都指向 `/api/v1/health`。所以其 Pod Ready 仍不足以证明工作流或 Edge 已就绪，必须执行下一节的业务检查。

新清单可按下列**示例模板**拆分 Backend 探针。阈值必须按实际 Catalog 加载时间和集群策略调整：

```yaml
startupProbe:
  httpGet:
    path: /api/v1/readiness
    port: http
    httpHeaders:
      - name: Host
        value: unilabos-runtime.lab-prod.svc.cluster.local
  periodSeconds: 5
  failureThreshold: 120
readinessProbe:
  httpGet:
    path: /api/v1/readiness
    port: http
    httpHeaders:
      - name: Host
        value: unilabos-runtime.lab-prod.svc.cluster.local
  periodSeconds: 5
  failureThreshold: 3
livenessProbe:
  httpGet:
    path: /api/v1/health
    port: http
    httpHeaders:
      - name: Host
        value: unilabos-runtime.lab-prod.svc.cluster.local
  periodSeconds: 30
  failureThreshold: 5
```

这里显式设置 `Host` 是为了兼容产品的主机白名单。该值必须与 Service DNS 及后文 `frontend_allowed_hosts` 一致；如果改了 Service 名或 Namespace，要同步修改三项 Probe。

分离的 Edge 不启动 HTTP。当前样例以 `ready.json` 检查初始化完成、以 `kill -0 1` 检查进程存活；文件不会单独证明 WebSocket 此刻仍连接，因此还要从 Backend 查询实时 Edge Readiness。

同 Pod 参考清单会让 Edge Probe 查询 Backend 的 `/api/v1/edge/readiness`，同时要求 `connected: true` 和 `device_count >= 1`。若迁移该做法，应保留单 Edge 身份，并避免探针误读其他 Edge 的会话。

## 10. 等待 Rollout 并做业务验收

先等待 Kubernetes 控制器完成：

```bash
kubectl rollout status deployment/unilabos-local-debug \
  -n "$TARGET_NAMESPACE" --timeout=10m
kubectl rollout status deployment/unilabos-local-edge \
  -n "$TARGET_NAMESPACE" --timeout=10m
kubectl get pods,pvc,service -n "$TARGET_NAMESPACE" -o wide
```

如果超时，先看 Pod 事件与两个容器日志，不要反复删除 Pod：

```bash
kubectl get pod -n "$TARGET_NAMESPACE" \
  -l app.kubernetes.io/part-of=uni-lab-szlab
export FAILED_POD="replace-with-pod-name"
kubectl describe pod -n "$TARGET_NAMESPACE" "$FAILED_POD"
kubectl logs -n "$TARGET_NAMESPACE" deployment/unilabos-local-debug \
  -c workspace-backend --tail=200
kubectl logs -n "$TARGET_NAMESPACE" deployment/unilabos-local-edge \
  -c edge-runtime --tail=200
```

先用端口转发做不暴露公网的验收：

```bash
kubectl -n "$TARGET_NAMESPACE" port-forward \
  service/unilabos-local-debug 8002:18003
```

保持端口转发，在另一个终端执行：

```bash
curl -fsS http://127.0.0.1:8002/api/v1/health
curl -fsS http://127.0.0.1:8002/api/v1/readiness
curl -fsS http://127.0.0.1:8002/api/v1/edge/readiness
curl -fsS http://127.0.0.1:8002/api/v1/scheduler/drain
```

依次确认 `health.status=ok`、`readiness.status=ready`、工作流 `loaded=total`、Edge `connected=true`、设备数量符合 Graph，以及 drain `phase=running`。

再打开 `/console/`，核对设备在线、工作流修订和发布状态。选择一个目标工作流运行预检；预检应为零写入，不要用创建真实 Task 代替部署验收。

最后核对运行 Pod 的镜像引用与 `imageID`，并把结果写入发布记录。标签相同不能证明镜像内容相同；多节点环境以 Registry digest 和 Pod 实际 `imageID` 为准。

## 11. 选择访问方式

| 方式 | 适用场景 | 边界 |
| --- | --- | --- |
| `port-forward` | 首次验收和管理员临时排错 | 仅当前终端和授权主机可达，不是长期入口 |
| `ClusterIP` | 集群内部 API，或由统一网关转发 | 推荐默认；不会自行提供公网访问 |
| `NodePort` | 明确授权的临时测试 | 直接暴露节点端口；当前产品没有通用登录与 TLS |
| Ingress/Gateway | 受管公网入口 | 必须由实际控制器提供 TLS、身份认证、授权、限流与审计 |

当前样例 Service 使用 NodePort `30183`，直接转发到 Backend `18003`。它只适合已接受明文 HTTP 和无通用登录风险的临时环境；生产环境不要照搬。

访问格式为 `http://<节点公网地址>:30183/console/`。还需由云防火墙或安全组放行该端口；当前目标环境的实际入口见本手册首页。

生产入口推荐把 Runtime Service 改成 `ClusterIP`，再接入组织已有的 Ingress 或 Gateway。仓库没有绑定具体控制器；下面只是路由与 TLS 的**示例骨架**，不能单独应用到生产：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: unilabos-runtime
  namespace: lab-prod
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: unilabos-local-debug
    app.kubernetes.io/component: workspace-backend
  ports:
    - name: http
      port: 18003
      targetPort: http
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: unilabos-runtime
  namespace: lab-prod
spec:
  ingressClassName: your-ingress-class
  tls:
    - hosts: [unilab.example.invalid]
      secretName: unilabos-tls
  rules:
    - host: unilab.example.invalid
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: unilabos-runtime
                port:
                  name: http
```

这个 `/` 规则会转发 Backend 整个端口，其中也包含 Edge 控制、Scheduler drain/resume、恢复和强制操作接口。必须在网关实施身份认证与按角色的路由授权，显式阻断公网管理接口；没有策略和测试时不要创建该路由。

把域名、IngressClass、证书 Secret 和 Namespace 换成实际值。TLS 终止后还要让受信代理把外部 scheme、host 和 port 正确传给 ASGI，并限制可信代理来源；否则 HTTPS Origin 可能与 Backend 所见的 HTTP 请求不一致而被拒绝。

Backend 还应设置 `UNILABOS_BASICCONFIG_FRONTEND_ALLOWED_HOSTS`，列出公网域名和 Edge 使用的集群内 Service DNS，并设置 `UNILABOS_BASICCONFIG_FRONTEND_SAME_ORIGIN=true`。

Probe 的 `Host` header 必须使用同一个已允许的 Service DNS。若 Service 名或 Namespace 改变，应同时修改 Probe 和白名单，否则 Probe 会收到 403。这两个浏览器边界不能替代用户认证。

公网只应通过受控网关到达按角色批准的 Console 和用户 API。Edge 控制、Scheduler 管理、PLC、OPC UA、数据库和 OTLP 应保持集群内可达；用路由策略、NetworkPolicy、防火墙和安全组限制方向。当前参考清单没有通用 NetworkPolicy。

## 12. 安全升级

升级前先在入口层停止新写请求，并通知操作员。然后调用 Scheduler 正式排空接口；它会停止新设备 Job 派发，并等待在途 Job 经原结果通道收敛。

```bash
curl -fsS -X POST \
  http://127.0.0.1:8002/api/v1/scheduler/drain
curl -fsS \
  http://127.0.0.1:8002/api/v1/scheduler/drain
```

重复 GET，直到 `phase=drained`、`active_device_job_count=0` 且列表为空。

还要人工核对没有待处理 confirmation、`UNKNOWN`、物料转移结算或现场动作；详见[运行模式、安全与恢复](runtime-safety.md#安全停机与排空)。

当前单节点双 Deployment 样例没有 `preStop` drain，也没有设置 90 秒终止预算。不要假定 `kubectl apply`、删 Pod 或节点驱逐会自动安全排空。

更完整的 Docker Desktop 参考清单在 Edge `preStop` 中最多等待约 60 秒，并给 Pod 90 秒终止预算。迁移这一模式时仍需根据设备最长安全动作设置预算，并验证超时后的持久恢复。

排空后备份作者工作区、Backend Runtime 和生产 Edge Runtime。生成唯一新镜像，完成领域源码迁移，审核清单差异，再应用整组 Backend/Edge 变更：

```bash
kubectl apply -f /path/to/release/unilabos-szlab.yaml
kubectl rollout status deployment/unilabos-local-debug \
  -n "$TARGET_NAMESPACE" --timeout=10m
kubectl rollout status deployment/unilabos-local-edge \
  -n "$TARGET_NAMESPACE" --timeout=10m
```

保持外部写入口关闭，重复完整业务验收。确认新旧数据合同、设备状态和工作流预检后，再恢复访问和派发。

当前分离清单重启后的 drain 状态不能充当跨进程维护门；入口层的写阻断必须持续到验收结束。

## 13. 回滚

升级前查看 Deployment 历史，并确认上一版镜像仍可取得：

```bash
kubectl rollout history deployment/unilabos-local-debug \
  -n "$TARGET_NAMESPACE"
kubectl rollout history deployment/unilabos-local-edge \
  -n "$TARGET_NAMESPACE"
```

回滚应先恢复上一版完整受审查清单，其中包括 ConfigMap、Service、Ingress、Secret 引用和两个工作负载。ConfigMap 会改写 PLC 端点与机械臂 journal；只回滚镜像可能让旧代码连接新端点。

```bash
kubectl diff -f /path/to/releases/unilabos-szlab.previous.yaml
kubectl apply -f /path/to/releases/unilabos-szlab.previous.yaml
```

`kubectl apply` 不会删除只存在于新版的对象。应比较两版资源清单，对新版独有对象逐个确认后处理；不要对整个命名空间使用无边界的 `--prune`。

只有确认本次变更仅涉及 Deployment Pod 模板、其他对象完全未变，而且数据格式兼容时，才可在写入口关闭期间使用 `rollout undo`：

```bash
kubectl rollout undo deployment/unilabos-local-debug \
  -n "$TARGET_NAMESPACE"
kubectl rollout undo deployment/unilabos-local-edge \
  -n "$TARGET_NAMESPACE"
kubectl rollout status deployment/unilabos-local-debug \
  -n "$TARGET_NAMESPACE" --timeout=10m
kubectl rollout status deployment/unilabos-local-edge \
  -n "$TARGET_NAMESPACE" --timeout=10m
```

`rollout undo` 不会恢复 ConfigMap、Service、Ingress、PVC、Secret、领域源码合并或外部设备事实。若升级已经改变数据格式，必须执行该版本审核过的数据恢复或前向修复方案，不能只回滚镜像。

作者工作区的播种标记也不会因镜像回滚而重置。恢复旧领域源码前先保留现场数据和当前 PVC 副本，再按发布记录执行显式迁移。

如果已有物理 Job 进入执行或结果未知，先保持 drain，核对设备与现场事实。不要通过回滚、重建 Task 或重复下发来“试一次”。

## 14. 上线安全边界

- `product` 控制定义可见范围、写入能力和 Task 准入，不会切换 Scheduler 算法，也不是生产安全认证；
- 当前 Backend/Console 没有通用用户登录中间件；
- `edge_key` 是身份，Edge 控制 `api-key` 才是协议密钥，两者都不能替代用户授权；
- NodePort 和 PLC-Sim 样例没有 TLS，也没有默认 NetworkPolicy；
- `readOnlyRootFilesystem`、非 root UID、drop capabilities、seccomp 和关闭 ServiceAccount token 应继续保留；
- 真机必须另做联锁、急停、超时、断线、幂等、恢复和现场验收；
- 观测系统是 fail-open，Trace 缺失不能证明任务没有运行。

## 部署核对清单

- [ ] 集群上下文、权限、节点架构和 StorageClass 已确认；
- [ ] OS、领域仓库、Graph、Catalog、Console、镜像 digest 和清单已归档；
- [ ] Backend 与 Edge 的 Workspace、Graph、运行模式和 Secret 同代；
- [ ] 作者、Backend Runtime 与 Edge Runtime 的 PVC 边界和备份已验证；
- [ ] PLC-Sim 或真实依赖端点已按环境审查；
- [ ] Kubernetes Rollout 与三项业务 Readiness 均通过；
- [ ] 工作流加载完整、设备数量正确、Scheduler 为 `running`；
- [ ] 目标工作流零写入预检通过；
- [ ] 公网入口已配置 TLS、认证、授权、限流与审计；
- [ ] drain、终止超时、恢复和双 Deployment 回滚已演练。

<div class="evidence">
<strong>实现依据</strong>
<p>仓库随附的 <code>szlab-local-debug/{Dockerfile,unilabos-local-debug.yaml,README.md}</code>（当前单节点镜像、双 Deployment、Secret、PVC、Probe、NodePort 与 PLC-Sim 顺序）。</p>
<p><code>unilabos/app/{main,runtime_topology}.py</code>、<code>workspace_host/launch.py</code> 与 <code>config/config.py</code>（进程角色、Workspace、模式、Edge 控制参数与启动合同）。</p>
<p><code>unilabos/app/web/server.py</code>、<code>edge_control/local_{authority,edge_session}.py</code> 与 <code>scheduler/{api,service}.py</code>（Health、Readiness、Edge 实时注册和 drain）。</p>
<p><code>Uni-Lab-SZLab/deployment/kubernetes-docker-desktop/edge-namespace/stack.yaml</code>（持久权威边界、业务探针和 60/90 秒终止参考）。</p>
<p>通用 Ingress 与生产安全项均为需按集群实现的模板，不是仓库现成 Chart。</p>
</div>
