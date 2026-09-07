# 试剂最大装料量

控制台只提供一个可编辑的“最大装料量”。录入、编辑和每个分装目标都以试剂数量的单位填写，例如液体 100 mL、粉体 50 g。领域包的额定容量提供默认值和后台约束；不再另设“本次装料上限”。库存列表和试剂历史显示实际生效的最大装料量。

## 存储与默认值

复用现有 JSON 字段，不增加数据库表或列，不新增 ResourceDict 根字段：

| 位置 | 含义 | 示例 |
| --- | --- | --- |
| `resource_template.meta_data.capacity` | 领域包声明的额定容量，由 `@resource(metadata={"capacity": ...})` 同步 | `{"max_volume_ul": 100000}` |
| `material.config.capacity` | 用户设置的这一个容器的最大装料量 | `{"max_mass_g": 50}` |
| `reagent.meta_data.loading_limits` | 仅兼容旧记录；显式保存新的最大装料量时移除 | 不再由控制台生成 |

体积统一存 µL，质量统一存 g。旧图中 `config.max_volume` / `data.max_volume` 按 PLR 的 µL 单位读取，并作为额定约束。不从名称、尺寸、化学品密度推算容量。

领域包的容量声明是可选扩展，本次只修改 Uni-Lab-OS，不要求 SZLab 或其他领域包同步修改。未声明容量且旧图也未提供容量时，界面显示未配置，用户仍可手工设置最大装料量；领域包以后统一适配时再提供默认值。模板同步省略容量不会删除数据库中已有的容量元数据。

用户最大装料量不能超过已知的同维度额定容量。缺省时采用领域包或旧图的额定容量；没有额定值时可自行设置。体积与质量分别校验：300 mL 注粉瓶用 g 计量时，需要填写质量上限才能校验装料质量，不做密度换算。

## 写入与兼容规则

- `container_capacity` 是录入、编辑和分装的唯一最大装料量请求字段，写入 `material.config.capacity`；可选的 `expected_material_revision` 防止覆盖并发容器修改。试剂编辑仍支持 `expected_revision`，同时修改数量和最大装料量必须在同一事务中成功。
- 空对象 `{}` 清除用户配置，恢复领域包/旧图默认值；正有限数值才是有效上限。0、负数、NaN、Infinity、布尔值及未知键均拒绝。容量对象内某个维度为 `null` 表示该维度未配置。请求中的 `container_capacity:null` 按旧 DTO 兼容行为视为未修改。
- 同维度支持 `µL` / `μL` / `uL`、`mL`、`L` 与 `mg`、`g`、`kg`。已配置容量的记录使用其他数量单位会被拒绝。没有任何上限的旧记录保持原行为。
- 创建物料并内联试剂、单条录入、JSON/CSV/XLSX 导入、编辑、分装均在原有事务内校验。新建数量必须大于 0；编辑允许降至 0。既有活动锁、数量预留与修订检查继续生效。任一检查失败，原子批次或分装不保留前面的试剂、数量扣减、容量设置、台账或发件箱事件。
- 普通数量编辑省略 `container_capacity` 时，旧 `loading_limits` 继续生效；读取时将其与容器容量取同维度较小值。`meta_data` 合并未指定的键，因此省略元数据或传 `{}` 也不会清掉旧限制、批次信息或分装来源。
- 显式提交 `container_capacity`（包括 `{}`）后，新值替换旧限制，移除该试剂的 `loading_limits`，不保留隐藏的第二个上限。其他元数据及容器配置保持不变。旧客户端仍可提交 `meta_data.loading_limits` 或分装目标 `loading_limits`；若同时提供新最大装料量，以新字段为准。
- 物料详情 `PUT /api/v1/materials/{uuid}` 显式提交 `config.capacity` 时同样替换旧限制、保留其他配置，并检查当前试剂余量。即使保存值与已有容器配置相同，也会折叠旧限制。省略 `capacity` 则保留已有设置与旧限制；`capacity:null` 或 `{}` 恢复默认值。
- 最大装料量单独变化也追加数量增量为 0 的 `reagent.adjust` 台账与事务发件箱事件，并增加试剂修订。`changes.previous.maximum_capacity` / `changes.result.maximum_capacity` 保存修改前后的实际值；清除后记录恢复的额定值，或没有上限时的 `{}`。历史保留旧 `loading_limits` / `container_capacity` 快照键以兼容老客户端。
- 分装继承化学身份、浓度及来源血缘，不继承源瓶的最大装料量或旧装料限制。目标使用自身配置或目标请求中的最大装料量。

旧记录仅在显式保存最大装料量时转换；不会启动时批量修改库存。模板额定容量同步也不改写已有数量，后续写入按最新容量校验。本合同覆盖试剂人工库存入口；工作流动作的物理加料预检、样品与当前物质的数量结算不在本次改动范围内。

## API 示例

录入时设置 50 g 最大装料量，不另存每次装料配置：

```json
{
  "material_uuid": "<容器 UUID>",
  "reagent_info_uuid": "<化学品身份 UUID>",
  "quantity": 30,
  "quantity_unit": "g",
  "container_capacity": {"max_mass_g": 50},
  "expected_material_revision": 1,
  "meta_data": {"batch": "A"}
}
```

这些字段适用于 `POST /api/v1/reagents`、导入的每一行和 `PUT /api/v1/reagents/{uuid}`（编辑无需重复容器和试剂身份，可增加 `expected_revision`）。CSV/XLSX 的 `meta_data` 与 `container_capacity` 单元格使用 JSON 对象文本；导入沿用 `atomic` 参数。

`reagent.dispense` 的每个 `targets` 项支持 `container_capacity` / `expected_material_revision`。物料读取保留 `capacity`（容器有效容量）与 `rated_capacity`（领域包/旧图额定容量）。试剂读取返回 `maximum_capacity`（含尚未替换的旧限制）、`rated_capacity` 和 `material_revision`，同时保留原 `container_capacity` 与 `meta_data` 字段。
