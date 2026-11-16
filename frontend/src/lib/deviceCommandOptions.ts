/**
 * 设备指令号（`cmd_type`）中文名表。
 *
 * 权威来源：CWM-Lab 的 `gn_node_library.js` → `actionsTable`（139 条），
 * 其上游是 `OPC_UA协议1.4.0(2).xlsx`。这里按**设备 id**索引，因为 id 是稳定
 * 标识；早期实现按中文 display_name 索引，只有「固体加样」恰好命中，导致其余
 * 设备的指令号只能手填数字。
 *
 * 取值含义见各设备的驱动文档；同一数字在不同设备上语义不同，不可跨设备套用。
 */
export const DEVICE_COMMAND_OPTIONS: Record<string, Record<number, string>> = {
  gn_centrifuge: {
    1: 'Y向左',
    2: 'Y向右',
    3: 'Z向左',
    4: 'Z向右',
    5: '放入物料',
    6: '运行离心机',
    7: '取出物料',
    8: '复位',
    9: '夹爪张开',
    10: '夹爪夹紧',
    11: '配重天平去皮',
  },
  gn_centrifuge_tube_liquid_handling: {
    1: 'X向左',
    2: 'X向右',
    3: 'Y向前',
    4: 'Y向后',
    5: '8通道Z向上',
    6: '8通道Z向下',
    7: '8通道移液吸',
    8: '8通道移液放',
    9: '大夹爪Z向上',
    10: '大夹爪Z向下',
    11: '单通道移液吸',
    12: '单通道移液放',
    13: '单通道向上',
    14: '单通道向下',
    15: '小夹爪向上',
    16: '小夹爪向下',
    17: '磁力架轴向上',
    18: '磁力架轴向下',
    19: '8通道装载',
    20: '8通道吸液',
    21: '8通道放液',
    22: '8通道卸载',
    23: '单通道装载',
    24: '单通道吸液',
    25: '单通道放液',
    26: '单通道卸载',
    27: '小夹爪开盖',
    28: '小夹爪关盖',
    29: '小夹爪抓取',
    30: '小夹爪放置',
    31: '大夹爪抓取',
    32: '大夹爪放置',
    33: '单通道混匀',
    34: '8通道混匀',
    35: '超声波混匀',
    36: '复位',
    37: 'xyz回原点',
    38: '模块1恒温打开',
    39: '模块1恒温关闭',
    40: '模块2恒温打开',
    41: '模块2恒温关闭',
    42: '模块3恒温打开',
    43: '模块3恒温关闭',
    44: '模块4恒温打开',
    45: '模块4恒温关闭',
    46: '模块4震荡打开',
    47: '模块4震荡关闭',
    48: '震荡模块夹爪开',
    49: '震荡夹爪关',
  },
  gn_liquid_cooling: {
    1: '温度设置',
    2: '液冷模块开启',
    3: '液冷模块关闭',
  },
  gn_locking_mechanism: {
    1: 'X向左',
    2: 'X向右',
    3: 'Y向左',
    4: 'Y向右',
    5: 'Z1向左',
    6: 'Z1向右',
    7: 'Z2向左',
    8: 'Z2向右',
    9: '夹爪夹取',
    10: '夹爪放置',
    11: '电批终锁(扭矩锁死)',
    12: '电批拧松',
    13: '夹爪夹紧',
    14: '夹爪松开',
    15: '电批预锁(按圈数,不锁死)',
    16: '复位',
  },
  gn_quick_carrier_exchange: {
    1: 'X向左',
    2: 'X向右',
    3: 'Z1向左',
    4: 'Z1向右',
    5: 'Z2向左',
    6: 'Z2向右',
    7: '推轴向左',
    8: '推轴向右',
    9: 'Z3向左',
    10: 'Z3向右',
    11: '物料顶出',
    12: '物料放置',
    13: '磁力搅拌运行',
    14: '复位',
  },
  gn_robotic_arm: {
    1: 'X向左',
    2: 'X向右',
    3: '去目标工站目标板位夹料',
    4: '去目标工站目标板位放料',
    6: '停止',
    7: '复位',
    8: '失能',
    9: '使能',
  },
  gn_rotary_stack: {
    1: '堆栈左旋转',
    2: '堆栈右旋转',
    3: '相机Z向上',
    4: '相机Z向下',
    5: '堆栈旋转至目标位置',
    6: '相机移动至目标位置并检测有无',
    7: '复位',
  },
  gn_solid_weighing: {
    1: 'X向左',
    2: 'X向右',
    3: 'Y向里',
    4: 'Y向外',
    5: '夹爪Z向上',
    6: '夹爪Z向下',
    7: 'D开门',
    8: 'D关门',
    9: '取料筒时Y轴向里',
    10: '取料筒时Y轴向外',
    11: '加料',
    12: '夹爪夹料',
    13: '夹爪放料',
    14: '天平去皮',
    15: '天平称重',
    16: '料筒Z向上',
    17: '料筒Z向下',
    18: '放料筒时Y轴向里',
    19: '放料筒时Y轴向外',
    20: '复位',
    21: '夹爪夹紧',
    22: '夹爪松开',
    23: 'xyz回原点',
    24: '静电消除开',
    25: '静电消除关',
  },
  gn_standard_oven: {
    1: '启动',
    2: '复位/停止',
  },
  gn_vacuum_oven: {
    1: '启动',
    2: '复位',
    100: '开门',
    101: '关门',
  },
}

/**
 * 中文显示名 → 设备 id。
 *
 * 只用于兼容「调用方只拿到 display_name」的旧路径；新代码应直接用设备 id，
 * 因为显示名可被本地化或改写，不是稳定标识。
 */
const DISPLAY_NAME_TO_DEVICE: Record<string, string> = {
  锁紧模块: 'gn_locking_mechanism',
  快换模块: 'gn_quick_carrier_exchange',
  离心机: 'gn_centrifuge',
  离心管液体处理: 'gn_centrifuge_tube_liquid_handling',
  旋转堆栈: 'gn_rotary_stack',
  机械手: 'gn_robotic_arm',
  固体加样: 'gn_solid_weighing',
  常规烘箱: 'gn_standard_oven',
  真空烘箱: 'gn_vacuum_oven',
  液冷模块: 'gn_liquid_cooling',
}

/**
 * 把资源模板标识解析成指令号表的设备键。
 *
 * 参数：``resourceTemplateName`` 是模板里的 `resource_template.name`，形如
 * `community.gn_lab.gn_robotic_arm`；``resourceDisplayName`` 是中文显示名，
 * 仅作旧数据兜底。返回：可用于查询 `DEVICE_COMMAND_OPTIONS` 的设备键，查不到
 * 时返回空串。异常：无。
 */
export function commandDeviceKey(
  resourceTemplateName?: string,
  resourceDisplayName?: string,
): string {
  const raw = (resourceTemplateName || '').trim()
  if (raw) {
    // `community.gn_lab.gn_robotic_arm` → `gn_robotic_arm`
    const tail = raw.split('.').pop() || raw
    if (DEVICE_COMMAND_OPTIONS[tail]) return tail
    if (DEVICE_COMMAND_OPTIONS[raw]) return raw
  }
  const display = (resourceDisplayName || '').trim()
  if (!display) return ''
  if (DEVICE_COMMAND_OPTIONS[display]) return display
  return DISPLAY_NAME_TO_DEVICE[display] || ''
}
