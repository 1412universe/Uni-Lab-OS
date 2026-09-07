# 工作流编排特性

本页是工作流作者和 AI 代码审查者的特性地图。每一节都说明“解决什么问题、怎样写、运行时怎样解释、有哪些边界”，示例来自当前 SZLab 驱动或已登记工作流。使用 AI 创作时，把相关章节作为提示词约束，并用本页检查生成结果；完整闭环见[用 AI 编写工作流（推荐）](ai-workflow-authoring.md)。

## 特性总览

| 特性 | 作者写法 | 运行含义 | SZLab 参考 |
| --- | --- | --- | --- |
| 类型化输入 | `Annotated`、`Literal`、`ResourceSlot` | 生成表单并在提交前校验 | `control_flow_repeat.py` |
| 动作与数据边 | `result = device.action(...)` | 创建 Job；结果引用形成依赖 | `control_flow_condition.py` |
| 展示分组 | `with group(name=...)` | 组织画布，不是屏障 | 单样品原子流程 |
| 条件 | 原生 `if / elif / else` | 只物化命中分支，其他分支跳过 | `control_flow_condition.py` |
| 有界循环 | `repeat_until` + `loop.next` + `until` | 按轮惰性创建 Job，达到条件或上限退出 | `control_flow_repeat.py` |
| 并行 | `parallel()` 下至少两个 group | DAG 提供并行机会；资源锁仍可串行化 | 拍照与开盖并行 |
| 结构化资源 | `resources("alias")` | 一段节点共同持有资源区间 | 编译器合同与测试 |
| 物料来源 | `material_source(...)` | 创建/选择物料并声明保管语义 | 单样品原子流程 |
| 动态库位 | `site_group(...)` | Task 创建时冻结候选站点 | S07/S08 工位选择 |
| 数量要求 | `quantity_requirement(...)` | Task 接纳时预留数量，消费后记账 | 加液体积 |
| 人工确认 | 画布包装设备动作 | 取得资源后等待批准，再下发同一 Job | 实验室操作编辑器 |
| 子工作流 | 调用已发布 `experiment_operation` | 按固定合同展开并冻结版本 | `material_transfer.py` |

## 类型化输入

参数必须位于 `*` 之后且有类型。`Annotated` 和 `Field` 可以把产品约束写进合同：

```python
from typing import Annotated, Literal

from pydantic import Field


def control_flow(
    *,
    stop_after: Annotated[
        int,
        Field(
            title="退出轮次",
            description="到达该轮次后结束",
            ge=1,
            le=10,
        ),
    ] = 3,
    route: Literal["sample", "blank"] = "sample",
):
    ...
```

当前 `Field` 支持 `title`、`description`、`unit`、`ge`、`le`、`min_length` 和 `max_length`。Console 会把整数、数值、字符串和枚举约束映射为表单校验；对象和数组使用 JSON 输入。

使用 `ResourceSlot` 时，用户选择的是一件完整 PLR Resource 引用，不是随意字符串：

```python
from typing import Annotated

from unilabos.registry.annotations import AllowedResourceTemplates
from unilabos.registry.placeholder_type import ResourceSlot
from szlab_poly_studio.resources.materials import beaker_500ml


def transfer(
    *,
    sample: Annotated[
        ResourceSlot,
        AllowedResourceTemplates(beaker_500ml),
    ],
):
    ...
```

允许模板应引用当前 Catalog 中的真实资源模板对象；不要用随手编写的字符串代替。

## 设备动作与结果引用

规范动作形状固定为：

```python
# unilab:node_uuid=<唯一 UUID>
result = selector.action_name(
    parameter_a=workflow_input,
    parameter_b=previous.output,
)
```

规则：

- selector 在模块级用 `device(...)` 声明；
- 动作必须赋给一个简单变量；
- 只接受命名参数；
- 参数只能来自 JSON 字面量、工作流输入、前序输出、循环 carry 或受支持的资源标记；
- 读取 `previous.output` 会建立真实数据边；
- 未知动作、缺参数或类型不兼容按失败关闭处理。

同一输出可以供多个后继读取；后继只有在所有数据和控制前提满足后才进入可调度集合。

## 展示分组 `group`

```python
from unilabos.workflow.authoring import group


# [样品预处理]: 把两步工艺显示为一个区域
# unilab:node_uuid=<分组 UUID>
with group(name="Preparation"):
    # unilab:node_uuid=<动作 UUID>
    prepared = reactor.prepare(sample=sample)
```

`group` 只保存展示和源码组织信息，不创建执行节点，也不是串并行屏障。名称必须是非空字符串；分组至少包含一个可执行节点；当前不支持展示 group 直接嵌套另一个展示 group。

## 条件 `if / elif / else`

SZLab 的无硬件副作用示例：

下面的 parallel 片段用于说明结构；设备和动作名应替换为当前 Catalog 中的真实定义。SZLab 的已登记流程使用同一结构表达“拍照”和“样品瓶开盖”的并行机会。

```python
# unilab:node_uuid=07880b05-d9a4-4290-8614-00554cac071a
if observed.value:
    # unilab:node_uuid=5b867034-42c0-4442-a9ac-fa22989934c6
    true_branch = control_flow_probe.record_branch(
        branch="true",
        iteration=0,
    )
else:
    # unilab:node_uuid=9f2d1466-db25-421c-a0a0-d3c2f63718f3
    false_branch = control_flow_probe.record_branch(
        branch="false",
        iteration=0,
    )
```

运行时严格求值得到布尔结果，只创建命中分支的动作；未命中分支不会偷偷执行。支持：

- `and`、`or`、`not`；
- `==`、`!=`、`>`、`>=`、`<`、`<=`；
- `+`、`-`、`*`、`/`、`//`、`%`；
- 输入、前序结果的字段与索引；
- `len`、`min`、`max`、`abs`、`round`、`contains`、`get`。

每个实际分支至少包含一个工作流节点。分支内部变量不能在条件外引用；需要汇总结果时，让各分支写入后续动作可统一读取的显式资源，或重构合同，不能依赖普通 Python 的局部变量逃逸。

## 有界循环 `repeat_until`

当前循环是 do-while 语义：先执行一轮，再在循环尾判断。SZLab 示例：

```python
from unilabos.workflow.authoring import repeat_until, until


# unilab:node_uuid=d75de496-24b9-54be-b719-2f3f5a185875
with repeat_until(max_iterations=10, carry={"iteration": 1}) as loop:
    # unilab:node_uuid=c00fee26-22b7-549e-934f-97d22b76e10e
    prepared = control_flow_probe.prepare_iteration(
        iteration=loop.carry["iteration"],
    )
    # unilab:node_uuid=0ef93d91-e6bc-5be1-9b2f-2b4d3f7f7d0c
    executed = control_flow_probe.execute_iteration(
        iteration=prepared.iteration,
    )
    # unilab:node_uuid=f29d242f-ebf6-5211-b2f6-b24f7447c559
    decision = control_flow_probe.evaluate_iteration(
        iteration=executed.iteration,
        stop_after=stop_after,
    )
    loop.next(iteration=decision.next_iteration)
    until(decision.done)
```

运行时不会提前展开最大轮数，而是每轮惰性物化 Job。约束：

- `max_iterations` 是正整数字面量；
- `carry` 是静态字符串键字典；
- 非空 carry 必须且只能调用一次 `loop.next(...)`，键集合完全一致；
- `until(...)` 是循环体最后一条语句，结果必须是严格布尔；
- 循环体至少有一个节点；
- 循环内动作变量不能在循环外引用；
- 控制结构最大嵌套深度为 8。

达到 `max_iterations` 仍未满足退出条件时，系统按有界循环语义停止继续物化，而不是无限运行。作者应把上限设为工艺允许的安全值，而不是一个“几乎无限”的数字。

## 并行 `parallel`

```python
from unilabos.workflow.authoring import group, parallel


with parallel():
    # unilab:node_uuid=<分支 A group UUID>
    with group(name="拍照"):
        # unilab:node_uuid=<拍照动作 UUID>
        image = camera.capture(sample=sample)

    # unilab:node_uuid=<分支 B group UUID>
    with group(name="开盖"):
        # unilab:node_uuid=<开盖动作 UUID>
        opened = robot.open_cap(sample=sample)

# unilab:node_uuid=<汇合动作 UUID>
joined = analyser.consume(
    image=image.result,
    opened=opened.result,
)
```

结构要求：`parallel()` 不接受参数，直接子项只能是至少两个 group。分支不能读取同级分支的中间结果；离开 parallel 后才能汇合。

编译器不会制造虚假的 fork/join 设备节点。并行只说明 DAG 允许同时就绪，不保证物理同时执行：如果两个分支绑定同一台设备、同一工位或同一结构化资源，Scheduler 的锁仍会把它们串行化。SZLab 的真实单样品流程用它表达“拍照”和“样品瓶开盖”的并行机会。

## 结构化资源区间 `resources`

```python
from unilabos.workflow.authoring import resources


with resources("sample_preparation_region"):
    # unilab:node_uuid=<动作 1 UUID>
    first = device_a.action(...)
    # unilab:node_uuid=<动作 2 UUID>
    second = device_b.action(input=first.output)
```

`resources(...)` 声明整个区间共同持有的调度资源。别名必须是非空、唯一的字符串字面量；动态变量会被拒绝。它可以嵌套，但不会产生可执行节点。根工作流也可以在装饰器元数据中声明结构化资源。

它和 group 的区别：group 管展示，resources 管锁；需要二者时应分别表达。

## 物料来源 `material_source`

物料来源把“这件实验输入从哪里来”写进工作流：

```python
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    material_source,
    resource_ref,
)


# unilab:node_uuid=<物料来源 UUID>
source_solvent = material_source(
    resource_template=liquid_reagent_bottle_100ml,
    mode="existing",
    mount=resource_ref("s10_liquid_reagent"),
    material_uuid=None,
    site="S101",
    slot_range=None,
    flow_role=MaterialFlowRole.REAGENT,
    custody_policy=MaterialCustodyPolicy.SHARED_SOURCE,
)
```

- `mode="existing"` 选择现有资源；`create_new` 按模板建立新资源；
- `resource_ref("s10_liquid_reagent")` 使用部署资源业务 ID，由库存权威解析实例；
- `PRIMARY_SAMPLE`、`REAGENT`、`CONSUMABLE` 表达流程角色；
- `TASK_EXCLUSIVE` 表示 Task 独占保管，`SHARED_SOURCE` 适合受数量预留保护的共享来源。

不要在源码中硬猜当前库存实例 UUID；稳定部署身份交给 `resource_ref`，本次具体材料交给输入绑定和库存权威。

## 动态库位 `site_group`

```python
from unilabos.workflow.authoring import site_group


placed = robot.transfer_material_atomic(
    resource=sample,
    target_warehouse=resource_ref("s07_process_warehouse"),
    target_site=site_group(
        "s07_beaker_process",
        exact=preferred_site,
    ),
)
```

`site_group` 引用部署中的命名站点组。创建 Task 时，系统按当前部署代际解析并冻结候选 UUID；这避免运行中因为布局变化悄悄换位。`exact` 只能直接引用字符串工作流输入或留空，不能引用任意运行表达式。

## 数量预留 `quantity_requirement`

```python
from unilabos.workflow.authoring import quantity_requirement


# unilab:node_uuid=<加液动作 UUID>
added = pump.add_solvent_with_materials(
    solvent_pump_1=source_solvent,
    volume_pump_1=volume_ml,
)

quantity_requirement(
    requirement_key="solvent",
    source=source_solvent,
    consume=added,
    quantity=volume_ml,
    quantity_unit="mL",
    scale=1.0,
    description="本次加液量",
)
```

Task 接纳时会按当前库存建立数量预留，动作结果收敛后再进入消费记账。约束：

- `requirement_key` 在工作流中唯一；
- `source` 直接引用一个 `material_source` 结果；
- `consume` 直接引用一个普通或组合动作结果；
- `quantity` 是有限正数字面量或工作流输入；动态输入为 `0` 时本次不生成要求；
- `scale` 是有限正数字面量；
- 当前不能在条件或循环内部声明数量要求。

数量合同只负责调度与账本，不替代设备测量。SZLab 的指令加液量、泵回执与真实称量应分别保留。

## 人工确认

当前 Python DSL **没有** `manual_confirm()` 或 `with manual_confirm()`。不要在源码中发明这种语法。

正确做法是在“实验室操作”画布中选中一个已绑定的真实设备动作，打开人工确认包装，并设置 `1..86400` 秒的超时。运行语义是：

1. Scheduler 先取得该动作需要的设备、物料和资源；
2. Job 进入等待人工确认，并在等待期间持有资源；
3. 批准后，同一个 Job 恢复成原设备动作且只下发一次；
4. 拒绝或超时取消整个 Task；
5. 不回应绝不会自动批准。

人工确认是“动作下发前复核”，不是一个无设备的纯人工停顿节点。超时默认 3600 秒。运行时重启不会自动恢复未决确认，因此恢复后要根据 Task/Job 状态重新对账。

## 子工作流与实验操作

可复用子流程先声明并发布为实验操作：

```python
@workflow(
    workflow_uuid="e7c53119-9fde-5250-9bf5-264f23d157a8",
    displayname="SZLab 标准物料转运",
    workflow_type="experiment_operation",
)
def s_z_lab_标准物料转运(*, resource: ResourceSlot):
    ...
```

父工作流用绝对导入和命名参数调用：

```python
from szlab_poly_studio.workflows.material_transfer import (
    s_z_lab_标准物料转运,
)


# unilab:node_uuid=<本次调用 UUID>
moved = s_z_lab_标准物料转运(
    resource=source_beaker,
    source_warehouse=resource_ref("s3_unused_beaker"),
    target_device="szlab_s07_solid_addition",
    target_warehouse=resource_ref("s07_process_warehouse"),
    source_site="L1B1",
    target_site="S0721",
)
```

编译器不会真的 import 并运行子模块，而是从已发布目录解析合同。每次调用有独立 invocation 身份；父图冻结子合同 UUID 和摘要。未发布、合同损坏、pin 过期或普通 `normal` 工作流被当作子流程时都会失败关闭。

## 常见错误写法

| 不要这样写 | 原因 | 正确方向 |
| --- | --- | --- |
| `python workflow.py` | DSL marker 在普通运行时会报错 | 通过 Console/API/Workspace 编译 |
| `device.action(1, 2)` | 动作不接受位置参数 | `device.action(a=1, b=2)` |
| `result = device.action(**params)` | 不允许动态展开 | 逐个写出命名参数 |
| 在源码中调用 `uuid.uuid4()` | 身份必须可重复解析 | 预先生成并粘贴 UUID 字面量 |
| 分支外读取 `true_branch` | 条件局部结果不逃逸 | 重构显式后继/资源合同 |
| 循环外读取 `decision` | 每轮结果身份不稳定 | 把输出写入允许的资源或重构流程 |
| 用 group 期待加锁 | group 只控制展示 | 使用 `resources(...)` |
| 用 parallel 保证同时动作 | 资源锁仍可串行 | 把它理解为并行机会 |
| `manual_confirm()` | 当前没有这种 Python marker | 在实验操作画布包装真实动作 |
| 调用未发布子操作 | 没有可冻结合同 | 先发布 `experiment_operation` |

## 作者提交前检查表

这份清单同样适用于 AI 生成的源码；AI 自己报告“已检查”不能代替人工复核和产品编译。

- 模块级只包含 import、可选 docstring、设备声明、至多一个输出 `TypedDict` 和一个工作流函数；
- 只使用绝对 import，不使用星号 import；
- 每个可识别结构都有唯一、紧邻的节点 UUID；
- 所有参数是关键字专用且有类型；
- 所有动作与子工作流调用只用命名参数并赋给简单变量；
- 输入、输出和 `ResourceSlot` 合同清楚且不重名；
- 条件、循环、并行和资源区间满足本页结构限制；
- 物料来源、保管策略、站点和数量单位与现场一致；
- 子工作流已发布，父流程重新编译并发布；
- 先在 `dry-run` 编译、预检和运行，再进入真机验收。

<div class="evidence">
<strong>实现依据</strong>：<code>unilabos/workflow/authoring.py</code>、<code>authoring_ast.py</code> 与 <code>registry/annotation_schema.py</code>（全部静态语法与类型）；<code>workflow/manual_confirmation.py</code> 和 Scheduler manual-confirmation 测试（人工确认）；<code>workflow/service.py</code>（子工作流发布合同）；<code>Uni-Lab-SZLab/szlab_poly_studio/workflows/control_flow_{condition,repeat}.py</code>、<code>material_transfer.py</code> 与 <code>single_sample_atomic_attachment_robot_atomic.py</code>（当前真实示例）。
</div>
