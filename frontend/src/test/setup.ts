import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)

// jsdom 不实现 ResizeObserver，而 @react-three/fiber 的 Canvas 需要它来测量容器。
// 用一个最小实现顶上：回调立即以零尺寸触发一次，让组件完成首帧测量而不抛错。
if (!('ResizeObserver' in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, 'ResizeObserver', {
    value: ResizeObserverStub,
    configurable: true,
  })
}

// jsdom 也没有 WebGL 上下文。3D 场景在测试里只需验证外壳与步骤清单，因此让
// canvas.getContext 返回 null（three 会走「无法创建渲染器」分支并安静降级），
// 而不是抛出一个会中断整个组件的异常。
if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext
}
