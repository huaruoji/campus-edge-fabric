import type { Worker } from "../api";

export function DevicePicker({ workers, value, onChange, gpuOnly = false, disabled = false }: {
  workers: Worker[]; value: string[] | null; onChange: (value: string[] | null) => void; gpuOnly?: boolean; disabled?: boolean;
}) {
  return <fieldset className="device-picker" disabled={disabled}>
    <legend>参与设备{value ? `（已选 ${value.length} 台）` : "（自动）"}</legend>
    {!gpuOnly ? <label><input type="checkbox" checked={value === null} onChange={(event) => onChange(event.target.checked ? null : [])} /> 自动使用兼容设备</label> : null}
    {(value !== null || gpuOnly) ? <div className="device-choices">{workers.map((worker) => {
      const support = worker.workloads?.find((w) => w.kind === "infer" && w.available);
      const eligible = worker.state === "online" && !!support && (!gpuOnly || support.engine === "cuda");
      return <label className={eligible ? "device-choice" : "device-choice is-muted"} key={worker.worker_id}>
        <input type="checkbox" checked={value?.includes(worker.worker_id) ?? false} disabled={!eligible} onChange={(event) => onChange(event.target.checked ? [...(value ?? []), worker.worker_id] : (value ?? []).filter((id) => id !== worker.worker_id))} />
        <span><strong>{worker.name}</strong><small>{worker.tags.filter((tag) => tag.startsWith("hw:")).join(" ") || worker.os || "硬件信息未登记"} / {support?.engine ?? "无推理能力"} / {worker.state === "online" ? "在线" : "离线"}</small></span>
      </label>;
    })}</div> : null}
    {workers.length === 0 ? <p className="field-hint">暂无已登记设备。</p> : null}
    {value?.length === 0 ? <p className="field-hint">请选择设备。不会转给未选中的机器。</p> : null}
  </fieldset>;
}
