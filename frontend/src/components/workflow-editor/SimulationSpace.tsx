/**
 * 工作流 3D 仿真空间（Three.js）。
 *
 * 与上一版 SVG 等轴测绘图的差别：这里是**真的 3D**——透视相机、方向光、阴影、
 * 材质、OrbitControls 自由旋转。设备按类型给出体量比例与颜色，机械手建模成
 * 底座 + 两段臂 + 夹爪，当前执行节点在其设备上方发光。
 *
 * 坐标说明：设备图里的 `pose` 只有 size 没有 position（实测全部为 0），所以布局
 * 由「设备类型 + 工站顺序」确定性地生成：机械手居中，其余按固定环排布。这与
 * 真实车间布置不完全一致，但能稳定表达「哪台设备在哪个位置、路径怎么走」。
 *
 * 依赖是惰性加载的：本文件只被 `React.lazy` 引用，首屏不会下载 three。
 */
import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { ContactShadows, Environment, Grid, Html, OrbitControls, RoundedBox } from '@react-three/drei'
import { ChevronLeft, ChevronRight, Layers, Pause, Play, RotateCcw, X } from 'lucide-react'
import * as THREE from 'three'
import { topologicalOrder, type EditorEdge, type EditorNode } from '../../lib/workflowEditorModel'
import { nodeDeviceKey, deviceDisplayName } from '../../lib/workflowDeviceBinding'

// ── 设备外观 ────────────────────────────────────────────────────────────

interface DeviceStyle {
  /** 平面尺寸（相对毫米量纲，纯视觉比例）。 */
  width: number
  depth: number
  height: number
  color: string
}

const DEVICE_STYLES: Array<{ pattern: RegExp; style: DeviceStyle }> = [
  { pattern: /robot|arm/i, style: { width: 90, depth: 90, height: 120, color: '#4f6ef7' } },
  { pattern: /prcxi|liquid/i, style: { width: 190, depth: 130, height: 62, color: '#0f9db8' } },
  { pattern: /centrifuge(?!.*tube)/i, style: { width: 120, depth: 120, height: 82, color: '#b96b10' } },
  { pattern: /centrifuge_tube/i, style: { width: 150, depth: 120, height: 78, color: '#0891b2' } },
  { pattern: /oven/i, style: { width: 130, depth: 110, height: 130, color: '#7657d8' } },
  { pattern: /stack|rotary/i, style: { width: 100, depth: 100, height: 160, color: '#65a30d' } },
  { pattern: /weigh/i, style: { width: 110, depth: 105, height: 80, color: '#d97706' } },
  { pattern: /carrier|exchange/i, style: { width: 95, depth: 130, height: 55, color: '#db2777' } },
  { pattern: /lock/i, style: { width: 95, depth: 125, height: 52, color: '#7c3aed' } },
]

const FALLBACK_STYLE: DeviceStyle = { width: 105, depth: 105, height: 85, color: '#64748b' }

function styleFor(deviceKey: string): DeviceStyle {
  for (const entry of DEVICE_STYLES) {
    if (entry.pattern.test(deviceKey)) return entry.style
  }
  return FALLBACK_STYLE
}

const IS_ARM = /robot|arm/i

interface PlacedDevice {
  key: string
  label: string
  style: DeviceStyle
  x: number
  z: number
  steps: number[]
  isArm: boolean
}

/**
 * 工位布局：机械手居中，其余按固定顺序沿椭圆环排布。
 *
 * 不用真实坐标是因为图里没有（`pose` 只有 size）。环排布至少能保证：不重叠、
 * 机械手在中心（真实车间就是它的行程覆盖全部工位）、路径连线清晰。
 */
function layoutDevices(keys: string[], stepsByDevice: Map<string, number[]>): PlacedDevice[] {
  const armKey = keys.find((key) => IS_ARM.test(key))
  const others = keys.filter((key) => key !== armKey)
  const placed: PlacedDevice[] = []
  const radiusX = others.length > 4 ? 330 : 265
  const radiusZ = others.length > 4 ? 250 : 205

  const place = (key: string, x: number, z: number) => {
    placed.push({
      key,
      label: deviceDisplayName(key),
      style: styleFor(key),
      x,
      z,
      steps: stepsByDevice.get(key) || [],
      isArm: IS_ARM.test(key),
    })
  }

  if (armKey) place(armKey, 0, 0)
  others.forEach((key, index) => {
    const angle = (-90 + (360 / Math.max(others.length, 1)) * index) * (Math.PI / 180)
    // 没有机械手时第一台设备占中心，避免整站悬空。
    if (!armKey && index === 0) {
      place(key, 0, 0)
      return
    }
    place(key, Math.cos(angle) * radiusX, Math.sin(angle) * radiusZ)
  })
  return placed
}

// ── 场景元件 ────────────────────────────────────────────────────────────

function DeviceBlock({
  device,
  active,
  visited,
  onSelect,
}: {
  device: PlacedDevice
  active: boolean
  visited: boolean
  onSelect: () => void
}) {
  const { style } = device
  const [hovered, setHovered] = useState(false)
  const emissive = active ? 0.55 : hovered ? 0.22 : visited ? 0.1 : 0

  return (
    <group position={[device.x, 0, device.z]}>
      {/* 底座 */}
      <RoundedBox
        args={[style.width, style.height, style.depth]}
        radius={6}
        smoothness={4}
        position={[0, style.height / 2, 0]}
        castShadow
        receiveShadow
        onClick={(event) => { event.stopPropagation(); onSelect() }}
        onPointerOver={(event) => { event.stopPropagation(); setHovered(true) }}
        onPointerOut={() => setHovered(false)}
      >
        <meshStandardMaterial
          color={style.color}
          roughness={0.45}
          metalness={0.15}
          emissive={active ? '#3b67f2' : visited ? '#0d9488' : '#000000'}
          emissiveIntensity={emissive}
        />
      </RoundedBox>

      {/* 机械手：底座 + 两段臂 + 夹爪，缓慢摆动示意工作 */}
      {device.isArm ? <ArmModel active={active} /> : null}

      {/* 顶部状态灯 */}
      <mesh position={[style.width / 2 - 10, style.height + 6, style.depth / 2 - 10]}>
        <sphereGeometry args={[4.5, 16, 16]} />
        <meshStandardMaterial
          color={active ? '#22d3ee' : visited ? '#34d399' : '#64748b'}
          emissive={active ? '#22d3ee' : '#000000'}
          emissiveIntensity={active ? 1.6 : 0}
        />
      </mesh>

      <Html
        center
        distanceFactor={520}
        position={[0, style.height + 30, 0]}
        style={{ pointerEvents: 'none' }}
      >
        <div className={`sim3d-label${active ? ' is-active' : ''}`}>
          {device.label}
          <small>{device.steps.length} 步</small>
        </div>
      </Html>
    </group>
  )
}

function ArmModel({ active }: { active: boolean }) {
  const upper = useRef<THREE.Group>(null)
  const lower = useRef<THREE.Group>(null)
  useFrame((state) => {
    const t = state.clock.getElapsedTime()
    const swing = active ? Math.sin(t * 1.6) * 0.55 : Math.sin(t * 0.5) * 0.12
    if (upper.current) upper.current.rotation.y = swing
    if (lower.current) lower.current.rotation.x = active ? Math.abs(Math.cos(t * 1.6)) * -0.5 : -0.25
  })
  return (
    <group position={[0, 14, 0]}>
      <mesh position={[0, 4, 0]} castShadow>
        <cylinderGeometry args={[16, 20, 10, 24]} />
        <meshStandardMaterial color="#334155" metalness={0.5} roughness={0.35} />
      </mesh>
      <group ref={upper} position={[0, 10, 0]}>
        <mesh position={[0, 26, 0]} castShadow>
          <cylinderGeometry args={[6, 7, 52, 20]} />
          <meshStandardMaterial color="#94a3b8" metalness={0.65} roughness={0.28} />
        </mesh>
        <group ref={lower} position={[0, 52, 0]}>
          <mesh position={[0, 0, 22]} rotation={[Math.PI / 2, 0, 0]} castShadow>
            <cylinderGeometry args={[5, 5.5, 46, 18]} />
            <meshStandardMaterial color="#cbd5e1" metalness={0.6} roughness={0.3} />
          </mesh>
          <mesh position={[0, 0, 46]} castShadow>
            <boxGeometry args={[16, 8, 14]} />
            <meshStandardMaterial color="#475569" metalness={0.5} roughness={0.4} />
          </mesh>
        </group>
      </group>
    </group>
  )
}

/** 执行路径：沿设备中心的折线，用管道表现，比线段更立体。 */
function Route({ points }: { points: Array<[number, number, number]> }) {
  const curve = useMemo(() => {
    if (points.length < 2) return null
    return new THREE.CatmullRomCurve3(points.map(([x, y, z]) => new THREE.Vector3(x, y, z)))
  }, [points])
  if (!curve) return null
  return (
    <mesh>
      <tubeGeometry args={[curve, Math.max(24, points.length * 8), 2.4, 8, false]} />
      <meshStandardMaterial color="#3b67f2" emissive="#3b67f2" emissiveIntensity={0.7} roughness={0.4} />
    </mesh>
  )
}

// ── 主组件 ──────────────────────────────────────────────────────────────

export interface SimulationSpaceProps {
  nodes: EditorNode[]
  edges: EditorEdge[]
  selectedNodeId?: string
  onSelectNode: (nodeId: string) => void
  onClose: () => void
}

/** 探测浏览器是否提供 WebGL；不支持时 3D 场景无法渲染，应给出明确说明。 */
function hasWebGL(): boolean {
  try {
    const canvas = document.createElement('canvas')
    return Boolean(
      canvas.getContext('webgl2') || canvas.getContext('webgl') || canvas.getContext('experimental-webgl'),
    )
  } catch {
    return false
  }
}

export function SimulationSpace({ nodes, edges, selectedNodeId, onSelectNode, onClose }: SimulationSpaceProps) {
  // 只在挂载时探测一次；WebGL 可用性在会话内不会变化。
  const [webglReady] = useState(hasWebGL)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [index, setIndex] = useState(0)

  const steps = useMemo(() => topologicalOrder(nodes, edges), [nodes, edges])

  const stepsByDevice = useMemo(() => {
    const map = new Map<string, number[]>()
    steps.forEach((step, position) => {
      const key = nodeDeviceKey(step)
      const list = map.get(key) || []
      list.push(position)
      map.set(key, list)
    })
    return map
  }, [steps])

  const devices = useMemo(
    () => layoutDevices([...stepsByDevice.keys()], stepsByDevice),
    [stepsByDevice],
  )
  const deviceByKey = useMemo(() => new Map(devices.map((item) => [item.key, item])), [devices])

  const route = useMemo(() => {
    const points: Array<[number, number, number]> = []
    let previous = ''
    for (const step of steps) {
      const key = nodeDeviceKey(step)
      if (key === previous) continue
      previous = key
      const device = deviceByKey.get(key)
      if (!device) continue
      points.push([device.x, device.style.height * 0.5, device.z])
    }
    return points
  }, [deviceByKey, steps])

  useEffect(() => {
    if (!selectedNodeId) return
    const position = steps.findIndex((step) => step.id === selectedNodeId)
    if (position >= 0) setIndex(position)
  }, [selectedNodeId, steps])

  useEffect(() => {
    setIndex((current) => Math.min(current, Math.max(steps.length - 1, 0)))
  }, [steps.length])

  useEffect(() => {
    if (!playing || steps.length === 0) return
    const timer = window.setInterval(() => {
      setIndex((current) => {
        if (current >= steps.length - 1) {
          setPlaying(false)
          return current
        }
        return current + 1
      })
    }, Math.max(180, 900 / speed))
    return () => window.clearInterval(timer)
  }, [playing, speed, steps.length])

  const activeStep = steps[index]
  const activeKey = activeStep ? nodeDeviceKey(activeStep) : ''
  const activeDevice = deviceByKey.get(activeKey)

  return (
    <div className="sim-space" role="dialog" aria-label="工作流 3D 仿真空间">
      <header className="sim-head">
        <div className="sim-title">
          <Layers size={15} />
          <strong>3D 仿真空间</strong>
          <span>{steps.length} 步 · {devices.length} 个工位</span>
        </div>
        <div className="sim-head-tools">
          <button type="button" onClick={onClose} title="关闭" aria-label="关闭仿真空间"><X size={15} /></button>
        </div>
      </header>

      <div className="sim-body">
        <div className="sim-stage">
          {steps.length === 0 ? (
            <p className="sim-empty">画布为空，先拖入节点再来看执行路径。</p>
          ) : !webglReady ? (
            // 无 WebGL（远程桌面、禁用硬件加速、部分虚拟机）时不静默白屏，
            // 明确说明原因并保留右侧步骤清单可用。
            <p className="sim-empty">
              当前浏览器未启用 WebGL，无法渲染 3D 场景。右侧步骤清单仍可正常浏览执行顺序。
            </p>
          ) : (
            <Canvas
              shadows
              dpr={[1, 2]}
              camera={{ position: [430, 360, 470], fov: 42 }}
              gl={{ antialias: true }}
            >
              <color attach="background" args={['#eef2f7']} />
              <fog attach="fog" args={['#eef2f7', 900, 1900]} />
              <ambientLight intensity={0.7} />
              <directionalLight
                position={[340, 520, 260]}
                intensity={1.7}
                castShadow
                shadow-mapSize-width={2048}
                shadow-mapSize-height={2048}
                shadow-camera-left={-700}
                shadow-camera-right={700}
                shadow-camera-top={700}
                shadow-camera-bottom={-700}
              />
              <directionalLight position={[-320, 260, -280]} intensity={0.5} />

              <Suspense fallback={null}>
                <Environment preset="city" />
              </Suspense>

              <Grid
                args={[2200, 2200]}
                cellSize={50}
                cellThickness={0.6}
                cellColor="#cbd5e1"
                sectionSize={250}
                sectionThickness={1.1}
                sectionColor="#94a3b8"
                fadeDistance={1700}
                fadeStrength={1.4}
                infiniteGrid
                position={[0, -0.4, 0]}
              />

              {devices.map((device) => (
                <DeviceBlock
                  key={device.key}
                  device={device}
                  active={device.key === activeKey}
                  visited={device.steps.some((step) => step <= index)}
                  onSelect={() => {
                    // 点设备跳到它上面的第一个步骤，便于从空间回到执行序列。
                    const first = device.steps[0]
                    if (first !== undefined) {
                      setIndex(first)
                      const step = steps[first]
                      if (step) onSelectNode(step.id)
                    }
                  }}
                />
              ))}

              <Route points={route} />

              {/* 当前设备上方的执行指示 */}
              {activeDevice ? (
                <Html
                  center
                  distanceFactor={480}
                  position={[activeDevice.x, activeDevice.style.height + 66, activeDevice.z]}
                  style={{ pointerEvents: 'none' }}
                >
                  <div className="sim3d-badge">
                    <b>#{index + 1}</b>
                    {String(activeStep?.data.raw.name || '未命名')}
                  </div>
                </Html>
              ) : null}

              <ContactShadows
                position={[0, 0.2, 0]}
                opacity={0.28}
                scale={1500}
                blur={2.4}
                far={420}
              />

              <OrbitControls
                makeDefault
                enableDamping
                dampingFactor={0.08}
                maxPolarAngle={Math.PI / 2.15}
                minDistance={170}
                maxDistance={1400}
              />
            </Canvas>
          )}

          <div className="sim-help">拖动旋转 · 滚轮缩放 · 右键平移 · 点击设备跳到该工位首个步骤</div>
        </div>

        <aside className="sim-side">
          <div className="sim-player">
            <button type="button" onClick={() => { setIndex(0); setPlaying(false) }} title="回到第一步"><RotateCcw size={13} /></button>
            <button type="button" disabled={index <= 0} onClick={() => setIndex((value) => Math.max(0, value - 1))} title="上一步"><ChevronLeft size={14} /></button>
            <button
              type="button"
              className={`sim-play ${playing ? 'is-playing' : ''}`}
              onClick={() => setPlaying((value) => !value)}
              title={playing ? '暂停' : '播放执行过程'}
            >
              {playing ? <Pause size={13} /> : <Play size={13} />}
              {playing ? '暂停' : '播放'}
            </button>
            <button type="button" disabled={index >= steps.length - 1} onClick={() => setIndex((value) => Math.min(steps.length - 1, value + 1))} title="下一步"><ChevronRight size={14} /></button>
            <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} aria-label="播放速度">
              <option value={0.5}>0.5×</option>
              <option value={1}>1×</option>
              <option value={2}>2×</option>
              <option value={4}>4×</option>
            </select>
          </div>

          <div className="sim-progress">
            <div className="sim-progress-bar"><i style={{ width: `${steps.length ? ((index + 1) / steps.length) * 100 : 0}%` }} /></div>
            <span>{steps.length ? index + 1 : 0} / {steps.length}</span>
          </div>

          {activeStep ? (
            <article className="sim-current">
              <strong>{String(activeStep.data.raw.name || '未命名节点')}</strong>
              <span>{deviceDisplayName(activeKey)}</span>
              <p>{String(activeStep.data.raw.description || '无步骤说明')}</p>
            </article>
          ) : null}

          <ol className="sim-steps">
            {steps.map((step, position) => {
              const key = nodeDeviceKey(step)
              return (
                <li key={step.id}>
                  <button
                    type="button"
                    className={[
                      position === index ? 'is-current' : '',
                      position < index ? 'is-past' : '',
                      step.id === selectedNodeId ? 'is-selected' : '',
                    ].filter(Boolean).join(' ')}
                    onClick={() => { setIndex(position); onSelectNode(step.id) }}
                  >
                    <em>{position + 1}</em>
                    <div>
                      <strong>{String(step.data.raw.name || '未命名')}</strong>
                      <small>{deviceDisplayName(key)}</small>
                    </div>
                  </button>
                </li>
              )
            })}
          </ol>
        </aside>
      </div>
    </div>
  )
}

export default SimulationSpace
