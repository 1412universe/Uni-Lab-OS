/**
 * 画布节点角标按钮的操作通道。
 *
 * 节点卡片是 React Flow 的 `nodeTypes` 组件，拿不到页面状态；又不想把回调塞进
 * `node.data`（那会把函数混进图数据、每次重建都换引用），所以用一层 context：
 * 页面提供，卡片消费。默认实现是空操作，卡片单独渲染时不会炸。
 */
import { createContext, useContext } from 'react'

export interface EditorNodeActions {
  /** 切换启用/停用；停用的节点保留在图上但运行时跳过。 */
  toggleDisabled: (nodeId: string) => void
  /** 删除节点及其全部连线。 */
  removeNode: (nodeId: string) => void
}

const fallback: EditorNodeActions = {
  toggleDisabled: () => {},
  removeNode: () => {},
}

export const NodeActionsContext = createContext<EditorNodeActions>(fallback)

export function useNodeActions(): EditorNodeActions {
  return useContext(NodeActionsContext)
}
