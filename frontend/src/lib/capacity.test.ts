import { describe, expect, it } from 'vitest'
import { capacityValue, convertMaximum, maximumError, parseMaximum } from './capacity'

describe('最大装料量单位与校验', () => {
  const current = { max_volume_ul: 80000 }
  const rated = { max_volume_ul: 100000 }

  it('按数量单位读取容积和质量，不跨维度推算', () => {
    expect(capacityValue(rated, 'mL')).toBe('100')
    expect(capacityValue(rated, 'L')).toBe('0.1')
    expect(capacityValue({ max_mass_g: 2 }, 'mg')).toBe('2000')
    expect(capacityValue(rated, 'g')).toBe('')
    expect(capacityValue({ max_mass_g: 2 }, 'mL')).toBe('')
    expect(capacityValue(undefined, 'mL')).toBe('')
  })

  it.each([
    ['100', 'mL', { max_volume_ul: 100000 }],
    ['0.1', 'L', { max_volume_ul: 100000 }],
    ['100', 'μL', { max_volume_ul: 100 }],
    ['100', 'µL', { max_volume_ul: 100 }],
    ['50', 'g', { max_mass_g: 50 }],
    ['1000', 'mg', { max_mass_g: 1 }],
    ['0.1', 'kg', { max_mass_g: 100 }],
  ])('把 %s %s 转为同维度容量', (value, unit, expected) => {
    expect(parseMaximum(value, unit)).toEqual(expected)
  })

  it.each(['0', '-1', 'Infinity', 'NaN', '1e309', 'invalid'])('拒绝非法最大装料量 %s', (value) => {
    expect(() => parseMaximum(value, 'mL')).toThrow()
    expect(maximumError(1, 'mL', current, rated, value)).not.toBe('')
  })

  it('未修改时保留现有限制，空白恢复领域包默认容量', () => {
    expect(maximumError(90, 'mL', current, rated)).not.toBe('')
    expect(maximumError(101, 'mL', undefined, rated)).not.toBe('')
    expect(parseMaximum('  ', 'mL')).toEqual({})
    expect(maximumError(90, 'mL', current, rated, '')).toBe('')
    expect(maximumError(101, 'mL', current, rated, '')).not.toBe('')
    expect(maximumError(100, 'mL', current, rated, '100')).toBe('')
    expect(maximumError(50, 'mL', current, rated, '101')).not.toBe('')
  })

  it('在同一维度内换算输入，切换到另一维度时恢复对应默认值', () => {
    expect(convertMaximum('80', 'mL', 'L')).toBe('0.08')
    expect(convertMaximum('2', 'g', 'mg')).toBe('2000')
    expect(convertMaximum('0.5', 'L', 'μL')).toBe('500000')
    expect(convertMaximum('80', 'mL', 'g')).toBeUndefined()
    expect(convertMaximum('2', 'g', 'mL')).toBeUndefined()
    expect(convertMaximum('', 'mL', 'L')).toBe('')
  })

  it('粉体质量只受质量上限约束，不能把体积当成质量上限', () => {
    expect(maximumError(200, 'g', rated, rated)).toBe('')
    expect(maximumError(50, 'g', rated, rated, '50')).toBe('')
    expect(maximumError(51, 'g', rated, rated, '50')).not.toBe('')
    expect(maximumError(1000, 'mg', { max_mass_g: 1 }, { max_mass_g: 1 })).toBe('')
    expect(maximumError(1001, 'mg', { max_mass_g: 1 }, { max_mass_g: 1 })).not.toBe('')
  })

  it('不能给不支持的计量单位设置容量上限', () => {
    expect(() => parseMaximum('10', 'mmol')).toThrow()
    expect(maximumError(1, 'mmol', undefined, undefined, '10')).not.toBe('')
    expect(maximumError(1, 'mmol', rated, rated)).not.toBe('')
  })
})
