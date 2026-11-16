import { describe, expect, it } from 'vitest'
import { DEVICE_COMMAND_OPTIONS, commandDeviceKey } from './deviceCommandOptions'
import { describeParameter } from './workflowParameterLabels'

describe('DEVICE_COMMAND_OPTIONS', () => {
  it('覆盖 GN 工作站的十台设备', () => {
    expect(Object.keys(DEVICE_COMMAND_OPTIONS).sort()).toEqual([
      'gn_centrifuge',
      'gn_centrifuge_tube_liquid_handling',
      'gn_liquid_cooling',
      'gn_locking_mechanism',
      'gn_quick_carrier_exchange',
      'gn_robotic_arm',
      'gn_rotary_stack',
      'gn_solid_weighing',
      'gn_standard_oven',
      'gn_vacuum_oven',
    ])
  })

  it('离心管液体处理有 49 条指令且含关键动作', () => {
    const table = DEVICE_COMMAND_OPTIONS.gn_centrifuge_tube_liquid_handling
    expect(Object.keys(table)).toHaveLength(49)
    expect(table[36]).toBe('复位')
    expect(table[35]).toBe('超声波混匀')
  })

  it('固体加样的天平错别字已修正', () => {
    const table = DEVICE_COMMAND_OPTIONS.gn_solid_weighing
    expect(table[14]).toBe('天平去皮')
    expect(table[15]).toBe('天平称重')
    expect(table[11]).toBe('加料')
  })
})

describe('commandDeviceKey', () => {
  it('从带命名空间的资源模板名解析出设备键', () => {
    expect(commandDeviceKey('community.gn_lab.gn_robotic_arm')).toBe('gn_robotic_arm')
    expect(commandDeviceKey('community.gn_lab.gn_solid_weighing')).toBe('gn_solid_weighing')
  })

  it('资源模板 id 直接命中时原样返回', () => {
    expect(commandDeviceKey('gn_centrifuge')).toBe('gn_centrifuge')
  })

  it('没有资源 id 时回落到中文显示名', () => {
    expect(commandDeviceKey(undefined, '锁紧模块')).toBe('gn_locking_mechanism')
  })

  it('未知设备返回空串而不是猜测', () => {
    expect(commandDeviceKey('community.gn_lab.gn_unknown_device')).toBe('')
    expect(commandDeviceKey(undefined, undefined)).toBe('')
  })
})

describe('describeParameter 的指令号下拉', () => {
  it('按设备 id 给出该设备的指令枚举', () => {
    const meta = describeParameter('cmd_type', 'community.gn_lab.gn_rotary_stack')
    expect(meta.label).toBe('指令号')
    expect(meta.options?.[7]).toBe('复位')
  })

  it('离心机与机械手各自拿到自己的语义，不串表', () => {
    const centrifuge = describeParameter('cmd_type', 'community.gn_lab.gn_centrifuge')
    const arm = describeParameter('cmd_type', 'community.gn_lab.gn_robotic_arm')
    expect(centrifuge.options?.[6]).toBe('运行离心机')
    expect(arm.options?.[6]).toBe('停止')
  })

  it('旧调用传中文显示名仍可用', () => {
    const meta = describeParameter('cmd_type', '固体加样')
    expect(meta.options?.[11]).toBe('加料')
  })

  it('未匹配到设备时不给 options，退回数字输入', () => {
    const meta = describeParameter('cmd_type', '未知设备')
    expect(meta.options).toBeUndefined()
  })
})
