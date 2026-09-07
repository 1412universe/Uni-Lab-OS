export function CapacityField({ value, unit, onChange, label = '最大装料量', defaultValue }: { value: string; unit: string; onChange: (value: string) => void; label?: string; defaultValue?: string }) {
  return <div className="capacity-field">
    <label className="form-field"><span>{label}（{unit}）</span><input aria-label={`${label}（${unit}）`} type="number" min="0" step="any" placeholder={defaultValue || '未配置'} value={value} onChange={(event) => onChange(event.target.value)} /></label>
    <small>{defaultValue ? `留空使用默认值 ${defaultValue} ${unit}。` : '可留空，已知最大装料量时填写。'}</small>
  </div>
}
