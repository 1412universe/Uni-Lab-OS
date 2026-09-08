# 堆栈／旋转堆栈

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页适用于板库、缓存架、多层料架和旋转仓。业务人员先画出实际层级、编号方向和取放顺序；开发人员再把每个可放置位置登记到系统中。这样操作员看到的编号、设备协议中的编号和库存位置能够保持一致。

本页说明怎样登记普通堆栈、分层仓库和旋转堆栈。核心原则是：**设备完成实际运动；载架保存有哪些位置；每个放置位记录是否被占用；库存记录每件物料属于哪个位置。**

:::{admonition} 本页完成条件
在 `resources/carriers.py` 中登记载架，为所有放置位填写固定编号、尺寸、坐标和允许的物料类型；再在启动图（Graph JSON）中建立现场仓库，并检查空位、已占用位置和不兼容物料。
:::

开发人员可使用 `BottleCarrier + ResourceHolder` 表示吸头盒、烧杯、粉桶和试剂瓶堆栈，使用 `PlateCarrier + PlateHolder` 表示旋转板库。所有可用位置和机械臂取放位置都必须有长期稳定的编号。

## 1. 先画清现场的层级和位置

建模前准备一张经过确认的表：

| 必填信息 | 示例 | 用途 |
| --- | --- | --- |
| 放置位（Site）名称 | `L1C1`、`cassette_01_layer_01` | 启动图、工作流和库存的稳定地址 |
| 相对坐标 | `x/y/z`，单位 mm | 画布布局和资源包络 |
| 槽位尺寸 | `width/height/depth` | 兼容性校验 |
| 允许类型 | `my_lab_plate_96` | `content_type` |
| 是否启用 | true/false | 排除损坏或未交付槽位 |
| 设备地址 | 层号、列号或协议位 | 由驱动映射，不能等同于视觉坐标 |

旋转堆栈还要确认零点、旋转方向、层号/列号的起始值、交互位、门联锁和断电恢复方式。资料不完整时只允许做布局模型和模拟验证。

## 2. 按模板登记普通堆栈

下面为自己的设备包建立一个 2 层 × 3 列容器架：

```python
from pylabrobot.resources import Coordinate, ResourceHolder
from unilabos.registry.decorators import resource
from unilabos.resources.itemized_carrier import BottleCarrier


@resource(
    id="my_lab_vial_stack",
    displayname="My Lab 样品瓶堆栈",
    category=["my_lab", "warehouse", "stack"],
    description="2 层 × 3 列；每个放置位 最多放一只 50 mL 样品瓶。",
    metadata={"site_count": 6},
)
def make_vial_stack(name: str = "my-lab-vial-stack") -> BottleCarrier:
    sites = {}
    for layer in range(2):
        for column in range(3):
            label = f"L{layer + 1}C{column + 1}"
            holder = ResourceHolder(
                name=f"{name}_{label}",
                size_x=40.0,
                size_y=40.0,
                size_z=80.0,
            )
            holder.location = Coordinate(
                x=20.0 + column * 60.0,
                y=20.0,
                z=20.0 + layer * 100.0,
            )
            holder.unilabos_extra = {
                "content_type": ["my_lab_sample_vial_50ml"],
            }
            sites[label] = holder

    return BottleCarrier(
        name=name,
        size_x=200.0,
        size_y=80.0,
        size_z=220.0,
        sites=sites,
        category="vial_stack",
    )
```

示例坐标必须替换为实测/CAD 数据。放置位 名称一旦进入 启动图、库存和工作流，就不要随意变更。

## 3. 按模板登记旋转堆栈

旋转堆栈同样先定义整个载架；每个实际位置对应一个固定放置位，并额外定义机械臂取放物料的交互位置 `interaction_site`：

```python
from pylabrobot.resources import Coordinate, PlateCarrier, PlateHolder


def make_rotating_plate_store(name: str) -> PlateCarrier:
    sites = {}
    for cassette in range(1, 11):
        for layer in range(1, 6):
            site_name = f"cassette_{cassette:02d}_layer_{layer:02d}"
            holder = PlateHolder(
                name=site_name,
                size_x=127.76,
                size_y=85.48,
                size_z=23.0,
                pedestal_size_z=0.0,
            ).at(Coordinate(x=0.0, y=0.0, z=layer * 25.0))
            holder.unilabos_extra = {
                "content_type": ["my_lab_96_well_plate"],
            }
            sites[site_name] = holder

    interaction = PlateHolder(
        name="interaction_site",
        size_x=127.76,
        size_y=85.48,
        size_z=44.0,
        pedestal_size_z=0.0,
    ).at(Coordinate(x=300.0, y=0.0, z=100.0))
    interaction.unilabos_extra = {
        "site_role": "interaction",
        "content_type": ["my_lab_96_well_plate"],
    }
    sites["interaction_site"] = interaction

    return PlateCarrier(
        name=name,
        size_x=560.0,
        size_y=560.0,
        size_z=750.0,
        sites=sites,
        category="rotating_plate_store",
    )
```

这里的循环只演示结构。真实旋转几何应由已确认的圆心、半径、层高和旋转角计算；启用 放置位 数量应增加断言，避免配置漏位。

## 4. 设备确认搬运成功后再更新库存

在设备类中定义 `inbound`、`outbound`、`home`、`get_status` 等动作。动作接收稳定 放置位 或设备层/列参数，由驱动转换为 PLC/协议地址；工作流不要传视觉 `x/y/z`。

一次出库应按以下顺序：

1. 校验来源放置位 有物料、交互位为空且类型兼容；
2. 锁定堆栈、目标放置位 和物料实例；
3. 调用旋转/出库设备动作；
4. 确认设备返回成功及必要在位信号；
5. 唯一一次调用库存转移，把物料挂到 `interaction_site` 或目标载架；
6. 失败或结果不确定时不改归属，进入人工核对。

## 5. 交付前必须检查

- 放置位 数量、名称、顺序和停用位与现场表一致；
- 每个放置位 的 `content_type` 能解析到已注册模板；
- 空位、占用位、错误类型、满栈和重复请求会被拒绝；
- 旋转堆栈的零点、方向、超时与联锁不由视觉模型推断；
- 离线试运行完成“出库→取放位置→入库”的完整过程，库存位置只更新一次；
- 真机测试前单独验证门、急停、掉电和动作结果不确定场景。
