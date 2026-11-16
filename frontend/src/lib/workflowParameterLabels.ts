/**
 * 工作流编辑器参数文案表。
 *
 * 为什么需要它：Uni-Lab 的 `@action` 只有动作级中文名，参数直接来自 Python
 * 参数名（`cmd_type`、`x_pos`），模板句柄里也没有单位与范围——341 个句柄中
 * 只有 5 个带中文名。CWM 那套属性面板的「中文名 + 单位 + 范围」在 Uni-Lab
 * 侧没有数据源，因此由这张表补齐。
 *
 * 维护约定：键为句柄的 `data_key`；找不到时退回后缀规则，再退回原始字段名。
 * 新增驱动参数时在这里补一行即可，不需要改组件。
 */

import { DEVICE_COMMAND_OPTIONS, commandDeviceKey } from './deviceCommandOptions'

export interface ParameterLabel {
  /** 面板显示的中文名。 */
  label: string
  /** 物理单位；不确定时留空，避免编造。 */
  unit?: string
  /** 数值范围提示，来自协议经验而非 schema。 */
  min?: number
  max?: number
  /** 一句话说明参数含义。 */
  hint?: string
  /** 枚举可选项：数值 → 中文。 */
  options?: Record<number, string>
}

/** 精确文案表（按 data_key）。 */
const PARAMETER_LABELS: Record<string, ParameterLabel> = {
  cmd_type: { label: '指令号', hint: '对应 OPC UA 协议的 CmdType，决定该节点执行哪个动作' },
  x_pos: { label: 'X 轴位置', hint: '设备行程坐标，单位以现场标定为准' },
  y_pos: { label: 'Y 轴位置' },
  z_pos: { label: 'Z 轴位置' },
  r_pos: { label: '旋转位置' },
  m_pos: { label: '模组位置' },
  p1_pos: { label: '第 1 工位位置' },
  p2_pos: { label: '第 2 工位位置' },
  p3_pos: { label: '第 3 工位位置' },
  p4_pos: { label: '第 4 工位位置' },
  x_speed: { label: 'X 轴速度' },
  y_speed: { label: 'Y 轴速度' },
  z_speed: { label: 'Z 轴速度' },
  r_speed: { label: '旋转速度' },
  p2_speed: { label: '第 2 工位速度' },
  z1_speed: { label: 'Z1 速度' },
  timeout: { label: '超时', unit: 's', min: 1, max: 3600, hint: '等待设备完成位的最长时间' },
  weight: { label: '加料重量', hint: '协议内部以 0.1mg 为计数单位' },
  material_z_pos: { label: '料筒 Z 位置' },
  material_z_speed: { label: '料筒 Z 速度' },
  door_pos: { label: '门位置' },
  door_speed: { label: '门速度' },
  shaking_speed: { label: '振荡转速' },
  mix_counts: { label: '混匀次数', min: 1 },
  open_lid_speed: { label: '开盖速度' },
  open_lid_start: { label: '开盖起点' },
  open_lid_end: { label: '开盖终点' },
  open_lid_rotation_speed: { label: '开盖旋转速度' },
  close_lid_speed: { label: '关盖速度' },
  close_lid_start: { label: '关盖起点' },
  close_lid_end: { label: '关盖终点' },
  close_lid_rotation_speed: { label: '关盖旋转速度' },
  ch1_blow: { label: '1 通道吹液' },
  ch8_blow: { label: '8 通道吹液' },
  ch1_reverse_aspirate: { label: '1 通道反向吸液' },
  ch8_reverse_aspirate: { label: '8 通道反向吸液' },
  ultrasound_stop: { label: '超声停止' },
  thermal1_temperature: { label: '温区 1 温度', unit: '℃' },
  thermo1_temperature: { label: '温区 1 温度', unit: '℃' },
  thermo2_temperature: { label: '温区 2 温度', unit: '℃' },
  thermo3_temperature: { label: '温区 3 温度', unit: '℃' },
  unilabos_device_id: { label: '目标设备', hint: '留空表示由调度器按物料绑定推导' },
}

/** 后缀规则：覆盖 341 个句柄里绝大多数 *_pos / *_speed 组合。 */
const SUFFIX_RULES: Array<{ suffix: string; decorate: (head: string) => ParameterLabel }> = [
  { suffix: '_temperature', decorate: (head) => ({ label: `${head}温度`, unit: '℃' }) },
  { suffix: '_rotation_speed', decorate: (head) => ({ label: `${head}旋转速度` }) },
  { suffix: '_speed', decorate: (head) => ({ label: `${head}速度` }) },
  { suffix: '_pos', decorate: (head) => ({ label: `${head}位置` }) },
  { suffix: '_counts', decorate: (head) => ({ label: `${head}次数`, min: 1 }) },
  { suffix: '_count', decorate: (head) => ({ label: `${head}次数`, min: 1 }) },
  { suffix: '_end', decorate: (head) => ({ label: `${head}终点` }) },
  { suffix: '_start', decorate: (head) => ({ label: `${head}起点` }) },
  { suffix: '_blow', decorate: (head) => ({ label: `${head}吹液` }) },
  { suffix: '_aspirate', decorate: (head) => ({ label: `${head}吸液` }) },
  { suffix: '_volume', decorate: (head) => ({ label: `${head}体积`, unit: 'µL' }) },
  { suffix: '_weight', decorate: (head) => ({ label: `${head}重量` }) },
  { suffix: '_timeout', decorate: (head) => ({ label: `${head}超时`, unit: 's' }) },
]

/** 通道前缀：ch1 / p2 / z3 / thermo1 → 1 通道 / 2 工位 / Z3 / 温区 1。 */
const PREFIX_RULES: Array<{ pattern: RegExp; render: (matched: string) => string }> = [
  { pattern: /^ch(\d+)$/, render: (m) => `${m.replace('ch', '')} 通道` },
  { pattern: /^p(\d+)$/, render: (m) => `第 ${m.replace('p', '')} 工位` },
  { pattern: /^z(\d+)$/, render: (m) => `Z${m.replace('z', '')}` },
  { pattern: /^x(\d+)$/, render: (m) => `X${m.replace('x', '')}` },
  { pattern: /^y(\d+)$/, render: (m) => `Y${m.replace('y', '')}` },
  { pattern: /^r(\d+)$/, render: (m) => `R${m.replace('r', '')}` },
  { pattern: /^thermo(\d+)$/, render: (m) => `温区 ${m.replace('thermo', '')}` },
  { pattern: /^thermal(\d+)$/, render: (m) => `温区 ${m.replace('thermal', '')}` },
]

/** 指令号中文表（目前覆盖固体加样；其它设备退化为数值输入）。 */
function humaniseKey(rawKey: string): string {
  return rawKey
    .split('_')
    .filter(Boolean)
    .map((part) => (/^[a-z]/.test(part) ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}

/**
 * 把句柄字段名翻成面板文案。
 *
 * 参数：`dataKey` 是句柄的 `data_key`；`deviceLabel` 用于挑选指令号枚举表。
 * 返回：含中文名、可选单位与范围的展示对象。异常：无；未知字段退化为
 * 人类可读的英文原名。
 */
export function describeParameter(dataKey: string, deviceLabel?: string): ParameterLabel {
  const exact = PARAMETER_LABELS[dataKey]
  if (exact) {
    if (dataKey === 'cmd_type') {
      // deviceLabel 现在传设备 id（如 community.gn_lab.gn_robotic_arm）；
      // 兼容旧调用仍可能传中文显示名，两者都由 commandDeviceKey 解析。
      const key = commandDeviceKey(deviceLabel, deviceLabel)
      if (key && DEVICE_COMMAND_OPTIONS[key]) {
        return { ...exact, options: DEVICE_COMMAND_OPTIONS[key] }
      }
    }
    return exact
  }

  for (const rule of SUFFIX_RULES) {
    if (!dataKey.endsWith(rule.suffix)) continue
    const head = dataKey.slice(0, -rule.suffix.length)
    if (!head) continue
    const prefix = PREFIX_RULES.find((item) => item.pattern.test(head))
    const headLabel = prefix ? prefix.render(head) : humaniseKey(head)
    return rule.decorate(headLabel)
  }

  return { label: humaniseKey(dataKey) }
}

/** 参数面板用的顺序：`ready` 固定在前，其余遵循后端 contract_order。 */
export function sortHandlesForPanel<T extends { handleKey: string; contractOrder?: number }>(handles: T[]): T[] {
  return [...handles].sort((left, right) => {
    if (left.handleKey === 'ready' && right.handleKey !== 'ready') return -1
    if (right.handleKey === 'ready' && left.handleKey !== 'ready') return 1
    const leftOrder = left.contractOrder ?? Number.MAX_SAFE_INTEGER
    const rightOrder = right.contractOrder ?? Number.MAX_SAFE_INTEGER
    return leftOrder - rightOrder
  })
}
