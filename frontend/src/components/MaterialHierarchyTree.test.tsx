import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { MaterialRecord } from '../types'
import { MaterialHierarchyTree } from './MaterialHierarchyTree'

/**
 * 验证按库位名称搜索时只展示命中的库位，并保留该库位上真实的占用物料。
 *
 * 参数：无。
 * 返回值：无；断言物料树的搜索结果。
 * 状态约束：测试数据中的 R1C1 已占用、R1C2 空闲，搜索不得改变占用事实。
 */
function keepsTheOccupiedMaterialWhenFilteringBySite(): void {
  const owner: MaterialRecord = {
    uuid: 'rack-1',
    name: '试剂瓶堆栈',
    category: 'reagent_stack',
    currentLocation: { kind: 'structural', label: '结构资源', siteCount: 2 },
    configuredSource: 'szlab',
    taskReferences: [],
    barcode: 'RACK-1',
    className: 'community.szlab.reagent_stack',
    updatedAt: '2026-09-05T10:00:00+08:00',
    isStructural: true,
    siteCount: 2,
    sites: [
      {
        uuid: 'site-r1c1',
        name: 'R1C1',
        occupiedMaterialUuid: 'vial-1',
        occupiedMaterialName: '试剂瓶 100 mL',
      },
      { uuid: 'site-r1c2', name: 'R1C2' },
    ],
    revision: 1,
    position: [0, 0, 0],
    size: [100, 100, 100],
  }
  const occupant: MaterialRecord = {
    uuid: 'vial-1',
    name: '试剂瓶 100 mL',
    category: 'liquid_reagent',
    currentLocation: {
      kind: 'site',
      label: 'R1C1',
      siteUuid: 'site-r1c1',
      ownerMaterialUuid: owner.uuid,
    },
    configuredSource: 'szlab',
    taskReferences: [],
    barcode: 'VIAL-1',
    parentUuid: owner.uuid,
    className: 'community.szlab.liquid_reagent',
    updatedAt: '2026-09-05T10:00:00+08:00',
    isStructural: false,
    siteCount: 0,
    sites: [],
    revision: 1,
    position: [0, 0, 0],
    size: [10, 10, 20],
  }

  render(<MaterialHierarchyTree materials={[owner, occupant]} onSelect={vi.fn()} />)
  const tree = screen.getByRole('tree')
  expect(within(tree).getByRole('button', { name: '试剂瓶 100 mL' })).toBeInTheDocument()
  expect(within(tree).getByText('R1C2')).toBeInTheDocument()

  fireEvent.change(screen.getByPlaceholderText('检索物料、设备或库位'), { target: { value: 'R1C1' } })

  expect(within(tree).getByRole('button', { name: '试剂瓶 100 mL' })).toBeInTheDocument()
  expect(within(tree).queryByLabelText('R1C1，未占用')).not.toBeInTheDocument()
  expect(within(tree).queryByText('R1C2')).not.toBeInTheDocument()
}

it('按已占用库位搜索时保留物料名称并隐藏无关库位', keepsTheOccupiedMaterialWhenFilteringBySite)
