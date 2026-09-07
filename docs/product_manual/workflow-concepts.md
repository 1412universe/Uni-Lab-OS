# 先理解工作流

Uni-Lab 工作流不是一段由 Python 解释器顺序执行的脚本。它是一份静态 DSL 源码：系统解析 AST，把设备动作、数据依赖、控制结构和资源要求编译成可验证的 DAG，再由 Scheduler 创建 Task 和 Job。

日常创作推荐使用[AI 辅助方式](ai-workflow-authoring.md)：让 AI 读取当前代码和 SZLab 相似流程后生成草稿，再由人审查并交给产品编译。本页的概念仍是必修内容，因为只有理解这些合同，才能判断 AI 是否误用了设备动作、资源或控制结构。

## 从源码到一次运行

```text
Python DSL 源码
      │ 静态编译，不执行作者代码
      ▼
工作流定义与修订
      │ 发布
      ▼
不可变发布合同
      │ 运行前检查
      ▼
Task 冻结执行计划
      │ 调度
      ▼
WorkflowNodeJob ──► Edge 设备动作 ──► 结果 / 事件 / Trace
```

这条链路带来三个重要结果：

1. 上传工作流时不会执行文件里的任意 Python 代码；
2. 发布后的合同不会因源文件随后修改而悄悄变化；
3. 每个 Task 都冻结自己的图、输入、绑定和源码快照，便于恢复与审计。

:::{warning}
不要运行 `python my_workflow.py`。`device()`、动作调用、`resource_ref()`、`material_source()` 和 `until()` 都是给静态编译器识别的标记，在普通 Python 运行时会拒绝执行。
:::

## 六个核心对象

| 对象 | 它回答的问题 | 例子 |
| --- | --- | --- |
| Workflow | 要完成什么实验目标？ | “单样品原子流程” |
| Experiment Operation | 哪段局部工艺可复用？ | “SZLab 标准物料转运” |
| Node | 这一步做什么？ | 机械臂转运、条件、循环 |
| Edge | 哪个结果或完成事实是下一步的前提？ | `observed.value` 进入条件 |
| Task | 这一次运行的冻结实例是什么？ | 输入、优先级、绑定、发布修订 |
| Job | 某个真实执行节点这一次怎样运行？ | 固定设备上的一次 action |

工作流和实验操作使用同一编译模型，但用途不同：普通 `normal` 工作流可以创建 Task；`experiment_operation` 作为上层流程的子工作流使用，不能在产品模式下独立当作完整实验运行。

可编辑的 `develop` Workspace 启动时，会按依赖从子到父应用已登记源码，直到组合目录达到固定点；作者不应依赖“先手工打开一次子流程”才能加载。进入产品发布和运行合同后，子流程仍必须是已发布的 `experiment_operation`。

## 节点不等于每一行代码

常见的图结构包括：

- 设备动作：形成可执行 Job；
- 物料来源：形成非 Action 的供应边界；
- 条件和循环：形成 Scheduler 解释的控制结构；
- 子工作流调用：按已发布合同确定性展开；
- 展示分组：保存画布组织信息，但不形成执行屏障。

数据引用会建立依赖。例如：

```python
# unilab:node_uuid=<第一个节点 UUID>
observed = probe.observe_boolean(value=value)

# unilab:node_uuid=<第二个节点 UUID>
recorded = probe.record_branch(
    branch=observed.message,
    iteration=0,
)
```

第二个动作读取第一个动作的输出，因此编译器会创建数据依赖。没有数据依赖并不一定表示可以物理并发；Scheduler 还会检查设备锁、资源区间和库存预留。

## 设备选择器

模块级设备声明把一个可读变量绑定到设备类型和选择方式：

```python
probe: SZLabControlFlowProbeDevice = device("szlab_control_flow_probe")
```

- `device("设备 ID")`：固定绑定当前 Graph 中的设备实例；
- `device()`：运行时从兼容实例中分配，是否可用取决于定义和部署绑定。

设备类型必须显式导入并作为变量注解。动作写成 `result = selector.action(keyword=value)`；只允许命名参数，不允许位置参数、`*args` 或 `**kwargs`。

## 公开输入与输出合同

所有工作流参数都必须是带类型的关键字专用参数：

```python
def my_workflow(*, value: bool = True):
    ...
```

类型会生成 Console 表单和运行校验。常用类型包括：

- `str`、`int`、`float`、`bool`；
- `Literal[...]` 枚举；
- `Optional[T]` / `T | None`；
- 一层 `list[T]`、`dict[str, JSONValue]`；
- `Annotated[T, Field(...)]` 的范围、长度、标题、说明和单位；
- `ResourceSlot` 资源引用。

工作流输出可以用一个普通 `typing.TypedDict` 声明正式合同，也可以使用 `workflow_output(...)`：

```python
class ProbeOutput(TypedDict):
    observed: bool


def my_workflow(*, value: bool = True) -> ProbeOutput:
    # unilab:node_uuid=<节点 UUID>
    result = probe.observe_boolean(value=value)
    return {"observed": result.value}
```

输出值必须来自工作流输入、物料来源或节点结果，不能用一个常量冒充运行结果。

不要把 Action 结果与 Workflow 输出混为一谈。Action 结果可按当前 Action Catalog 合同使用 `TypedDict` 或合规的 frozen dataclass；Workflow 作者源码当前只接受普通 `TypedDict` 结果记录，不接受 dataclass 或 Pydantic `BaseModel`。

## 节点 UUID 是稳定身份

每个动作、物料来源、条件、循环、展示分组和子工作流调用前都要有紧邻的唯一 UUID：

```python
# [观察输入]: 将公开输入送到软件探针
# unilab:node_uuid=966d4242-af1f-400f-93c9-268927de1451
observed = probe.observe_boolean(value=value)
```

节点 UUID 用于画布 round-trip、修订差异、Task 矩阵、Job 归属和恢复，不能因格式化代码随意改变。可选标题必须在 UUID 注释正上方并保持相同缩进。`parallel()` 和 `quantity_requirement()` 本身不是持久节点，不单独添加节点 UUID。

工作流函数名、包内源码路径和 `@workflow` UUID 共同参与来源身份。移动文件或重命名函数不是普通排版修改；操作前应检查 `package.yaml`、既有修订和引用方，并按身份迁移处理。

需要临时禁用节点时，可写：

```python
# unilab:node_uuid=<节点 UUID> disabled=true
```

禁用会改变编译图；应用后仍要重新检查数据依赖和发布修订。

## 修订、发布与 Task 快照

- **保存/应用**：把通过编译的候选图变成当前定义修订；
- **发布**：冻结当前修订及合同；
- **预检**：对当前输入、绑定、设备和库存做零写入检查；
- **创建 Task**：重新检查，并原子建立任务、Job、锁和需要的预留；
- **执行**：Scheduler 只按该 Task 的冻结计划推进。

预检通过不等于之后必然立即运行。预检与提交之间，另一任务可能占用设备或库存；以创建 Task 时的结果和任务等待原因作为事实。

## 开发模式与产品模式

| 模式 | 看得到什么 | 能做什么 |
| --- | --- | --- |
| `develop` | 已发布和未发布定义 | 导入、创作、发布、普通/单步运行；同一时间一个开发 Task |
| `product` | 仅已发布普通工作流 | 多任务运行；定义写操作和单步调试关闭 |

先在 `dry-run + develop` 完成学习，再在受控环境中发布和验收。不要把模式名理解成设备安全等级：是否接触真实硬件由 `runtimeMode`/`action_mode` 决定。

## 继续学习

1. 按[手写并运行第一个工作流（SZLab 教学）](first-workflow.md)掌握最小 DSL；
2. 在[物料管理](materials.md)和[试剂管理](reagents.md)中理解工作流绑定的运行资源；
3. 在[工作流编排特性](workflow-features.md)学习条件、循环、并行、资源、人工确认和子工作流；
4. 在[可复用实验操作](operations.md)理解子流程的创作与发布；
5. 按[用 AI 编写工作流（推荐）](ai-workflow-authoring.md)建立日常取证、生成、审查和验证闭环；
6. 完成[运行安全](runtime-safety.md)和[部署](deployment.md)后，再管理工作流和任务。

<div class="evidence">
<strong>实现依据</strong>：<code>unilabos/workflow/authoring.py</code>（静态 DSL 标记）；<code>unilabos/workflow/authoring_ast.py</code>（AST 编译、语法和类型边界）；<code>unilabos/workflow/service.py</code>（修订、候选、发布与组合合同）；<code>unilabos/workflow/execution_plan.py</code>（Task 冻结计划）；<code>unilabos/app/workflow_api.py</code>（预检、Task 和 Job）。
</div>
