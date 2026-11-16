/**
 * 画布节点 → 设备身份的解析。
 *
 * 背景：Uni-Lab 有两种执行器绑定
 *
 * 1. **固定绑定**：`meta_data.unilab.executor_binding = { mode: 'fixed', device_id }`
 * 2. **动态选择**：`DeviceSelector(mode='resource_template', resource_template_uuid=...)`
 *    —— 具体设备在派发那一刻才由调度器按类型选出来。
 *
 * 早先的实现只认第 1 种，于是所有动态节点在 3D 视图里都显示「未绑定设备」。
 * 这里统一解析出**最具体的可用身份**：优先具体设备，其次设备类型，最后空。
 *
 * 另外集中处理「框架内部设备」的隐藏：`host_node` 是注册表为控制流模板注入的
 * 宿主执行器，用户不该在设备/物料/3D 里看到它。
 */
import type { EditorNode } from './workflowEditorModel'

/** 不向用户展示的框架内部执行器。 */
const HIDDEN_DEVICE_IDS = new Set(['host_node'])

/** 设备 id / 类型 → 中文显示名；键与设备注册表里的 id 对应。 */
const DEVICE_DISPLAY: Record<string, string> = {
  gn_robotic_arm: '机械手',
  gn_workstation: '有机合成工作站',
  gn_solid_weighing: '固体加样',
  gn_centrifuge: '离心机',
  gn_centrifuge_tube_liquid_handling: '离心管液体处理',
  gn_rotary_stack: '旋转堆栈',
  gn_stack: '旋转堆栈',
  gn_locking_mechanism: '锁紧模块',
  gn_quick_carrier_exchange: '快速载具交换',
  gn_vacuum_oven: '真空烘箱',
  gn_standard_oven: '常规烘箱',
  gn_prcxi: 'PRCXI 移液站',
  PRCXI: 'PRCXI 移液站',
}

/**
 * 判断一个设备标识是否属于框架内部设备（应从用户界面隐藏）。
 *
 * 参数：``deviceId`` 是 `local_id` 或资源模板名（可带命名空间前缀）。
 * 返回：需要隐藏时为真。异常：无。
 */
export function isHiddenDevice(deviceId: string | undefined, name?: string): boolean {
  const candidates = [deviceId, name].filter(Boolean).map((value) => String(value))
  for (const value of candidates) {
    // 兼容 `community.gn_lab.host_node` 这类带命名空间写法。
    const tail = value.split('.').pop() || value
    if (HIDDEN_DEVICE_IDS.has(value) || HIDDEN_DEVICE_IDS.has(tail)) return true
  }
  return false
}

/** 从资源模板标识里取出设备短名（`community.gn_lab.gn_robotic_arm` → `gn_robotic_arm`）。 */
function deviceShortName(templateName: string | undefined): string {
  const raw = (templateName || '').trim()
  if (!raw) return ''
  return raw.split('.').pop() || raw
}

/**
 * 解析节点对应的设备键（用于 3D 布局分组与显示）。
 *
 * 参数：``node`` 是画布节点。返回：设备 id 或设备类型的短名；都拿不到时返回
 * 空串（调用方据此显示「未指定设备」）。异常：无。
 *
 * 优先顺序：固定绑定的 `device_id` → 模板的资源类型 → 复合节点的 device_id。
 * 这样同一个设备类型的所有节点会落到同一个工位上，而不是各显示「未绑定」。
 */
export function nodeDeviceKey(node: EditorNode): string {
  const raw = node.data.raw
  const unilab = raw.meta_data?.unilab
  const binding = unilab?.executor_binding
  const fixedId = binding && typeof binding === 'object' ? String(binding.device_id || '').trim() : ''
  if (fixedId) return fixedId

  const compositeId = unilab?.composite?.device_id
  if (compositeId) return String(compositeId)

  // 动态选择器：用设备类型代表该节点所在的工位。
  const template = node.data.template
  const typeName = deviceShortName(template?.resourceName)
  if (typeName) return typeName

  return ''
}

/**
 * 把设备键翻成用户可读的中文名。
 *
 * 参数：``deviceKey`` 来自 `nodeDeviceKey`。返回：中文名；未知时原样返回；
 * 空值返回「未指定设备」。异常：无。
 */
export function deviceDisplayName(deviceKey: string): string {
  if (!deviceKey) return '未指定设备'
  return DEVICE_DISPLAY[deviceKey] || deviceKey
}

export { HIDDEN_DEVICE_IDS }
