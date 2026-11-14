import { useEffect, useState } from "react";
import type { ApiClient, Experiment } from "../api";
import { usePolling } from "../hooks/usePolling";
import { formatUnix } from "../format";
import { DevicePicker } from "./DevicePicker";
import { ErrorBox, LoadingLine } from "./ui";
import "../workspace.css";

const stateLabel: Record<string, string> = {queued:"排队中", running:"评测中", completed:"已完成", failed:"失败", canceled:"已取消", interrupted:"服务重启中断"};
const num = (value: number | null | undefined, digits=2) => value == null ? "—" : value.toFixed(digits);

export function ExperimentPanel({api}: {api: ApiClient}) {
  const reports = usePolling((signal) => api.getExperiments(signal), 4000, api.hasToken, [api]);
  const datasets = usePolling((signal) => api.listDatasets(signal), 30000, api.hasToken, [api]);
  const workers = usePolling((signal) => api.getWorkers(signal), 5000, api.hasToken, [api]);
  const [dataset, setDataset] = useState("");
  const [selected, setSelected] = useState<string[] | null>([]);
  const [repeats, setRepeats] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [reportId, setReportId] = useState(() => window.location.hash.split("/")[2] ?? "");
  useEffect(() => {
    const change = () => setReportId(window.location.hash.split("/")[2] ?? "");
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  const report = reports.data?.find((r) => r.id === reportId) ?? (!reportId ? reports.data?.[0] : undefined);
  async function start() {
    if (busy || !dataset || (selected?.length ?? 0) < 2) return;
    setBusy(true); setError(null);
    try {
      const run = await api.createExperiment({dataset, worker_ids: selected!, params: {model:"yolo11n", imgsz:640, conf:.25}, repeats, timeout_s:900});
      setReportId(run.id); window.location.hash = "#/experiments/" + run.id; reports.refresh();
    } catch (cause) {setError(cause);}
    finally {setBusy(false);}
  }
  async function cancel() {
    if (!report || busy) return;
    setBusy(true); setError(null);
    try {await api.cancelExperiment(report.id); reports.refresh();}
    catch (cause) {setError(cause);}
    finally {setBusy(false);}
  }
  return <div className="experiment-workspace">
    <section className="experiment-setup" aria-label="创建对照评测"><h2>一次操作，完成公平对照</h2><p>CPU 参考校验 → 各条件预热 → 单机 A / 单机 B / 多机。系统自动执行，参考与预热不计成绩。</p>
      <label>评测数据集<select className="select" aria-label="评测数据集" value={dataset} onChange={(e) => setDataset(e.target.value)}><option value="">请选择已登记数据集</option>{datasets.data?.map((d) => <option key={d.id} value={d.name}>{d.name}（{d.count} 张）</option>)}</select></label>
      <p className="field-hint">yolo11n，输入 640，置信度 0.25。采用固定模型哈希和完整相同输入；请准备足够多的不重复图片，测量持续时间以实际完成为准。</p>
      <DevicePicker workers={workers.data ?? []} value={selected} onChange={setSelected} gpuOnly disabled={busy} />
      <label>重复轮数<select className="select" aria-label="重复轮数" value={repeats} onChange={(e) => setRepeats(Number(e.target.value))}><option value={1}>1 轮（流程检查）</option><option value={3}>3 轮（默认）</option><option value={5}>5 轮</option></select></label>
      <p className="field-hint">评测期间选中设备暂不接其他 CEF 作业。每阶段最多等待 15 分钟；请确保设备所有者知情并关闭竞争计算负载。</p>
      <button className="btn btn--primary" onClick={() => void start()} disabled={busy || !dataset || (selected?.length ?? 0)<2}>{busy ? "请求中…" : "开始一键对照评测"}</button>
      {datasets.error ? <ErrorBox error={datasets.error} onRetry={datasets.refresh} /> : null}{workers.error ? <ErrorBox error={workers.error} onRetry={workers.refresh} /> : null}{error ? <ErrorBox error={error} compact /> : null}
    </section>
    <section className="experiment-results" aria-label="评测记录"><header className="experiment-result-head"><h2>评测记录</h2><button className="btn btn--sm" onClick={reports.refresh}>刷新评测</button></header>
      <nav className="experiment-list" aria-label="历史评测">{reports.data?.map((r) => <a key={r.id} className={report?.id === r.id ? "selected" : ""} href={"#/experiments/"+r.id}>{r.config.dataset} / {stateLabel[r.state] ?? r.state} / {formatUnix(r.created_at)}</a>)}</nav>
      {reports.error ? <ErrorBox error={reports.error} onRetry={reports.refresh} /> : null}
      {reports.loading && !reports.data ? <LoadingLine /> : null}
      {report ? <ExperimentReport report={report} onCancel={() => void cancel()} busy={busy} /> : <p className="empty-hint">{reportId ? "找不到这份评测记录。" : "暂无评测。普通任务的完成数不能替代单机基线。"}</p>}
    </section>
  </div>;
}

function ExperimentReport({report, onCancel, busy}: {report: Experiment; onCancel: () => void; busy: boolean}) {
  const metrics = report.metrics;
  const names = new Map(report.config.devices.map((d) => [d.id,d.name]));
  function exportReport() {
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"}));
    const a=document.createElement("a"); a.href=url; a.download=report.id+".json"; a.click(); window.setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const bars = metrics ? [...Object.entries(metrics.baselines).map(([id,p])=>({name:names.get(id)??id,p})), {name:"多机算力池",p:metrics.pool}] : [];
  const maximum=Math.max(1,...bars.map((b)=>b.p));
  return <article className="experiment-report"><div className="experiment-result-head"><div><h3>{stateLabel[report.state] ?? report.state}</h3><span className="mono">{report.id}</span></div><div><button className="btn btn--sm" onClick={exportReport}>导出完整报告</button>{["running","queued"].includes(report.state) ? <button className="btn btn--sm" disabled={busy} onClick={onCancel}>取消评测</button> : null}</div></div>
    {report.error ? <p role="alert" className="notice notice--warn">{report.error}</p> : null}
    {metrics ? <><div className="metric-summary"><div><span>算力池有效吞吐</span><strong>{num(metrics.pool)} <small>images/s</small></strong></div><div><span>聚合效率 η</span><strong>{num(metrics.eta_pct)}%</strong></div><div><span>相对最强单机 S</span><strong>{num(metrics.speedup)}×</strong></div></div>
      <div className="throughput-comparison">{bars.map((bar)=><div key={bar.name}><span>{bar.name}</span><div className="throughput-track"><span style={{width:bar.p/maximum*100+"%"}} /></div><strong>{num(bar.p)} images/s</strong></div>)}</div>
      <p className="field-hint">各条件多轮吞吐中位数。η = Ppool / ΣPi；S = Ppool / max(Pi)。GPU 利用率只表示忙碌程度，不是聚合效率。</p></> : <p className="notice">{report.state === "running" ? "参考、预热或正式测量正在进行；所有条件完成且校验通过后才计算 η 和 S。" : "暂无有效的完整对照指标。"}</p>}
    {report.warnings.length ? <details open className="measurement-warnings"><summary>测量边界与需要复核的条件</summary><ul>{report.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul></details> : null}
    <div className="table-wrap"><table className="data-table"><caption>阶段与设备贡献（参考及预热不计入吞吐成绩）</caption><thead><tr><th>阶段</th><th>状态</th><th>有效数</th><th>窗口</th><th>有效吞吐</th><th>各设备有效贡献</th><th>重试</th></tr></thead><tbody>{report.phases.map((p)=><tr key={p.job_id}><td><a href={"#/jobs/"+p.job_id}>{p.label}</a>{!p.measured ? "（不计分）" : ""}</td><td>{p.state === "verified" ? "已校验" : p.state === "running" ? "执行中" : p.state}</td><td>{p.valid ?? "—"} / {p.total ?? "—"}</td><td>{num(p.seconds)} s</td><td>{p.measured ? num(p.throughput)+" images/s" : "—"}</td><td>{report.config.devices.map((d)=>`${d.name}: ${p.contribution?.[d.id] ?? 0}`).join("；")}</td><td>{p.retries ?? "—"}</td></tr>)}</tbody></table></div>
    <details className="report-details"><summary>资源采样、设备配置与计量方法</summary><p>计时：Coordinator 创建作业至全部结果回传完成；包含排队及 Worker 传输，排除 CPU 参考、预热与事后校验。已登记数据集不重复上传。</p><p>固定间隔心跳采样：配置 {report.config.sampling_interval_s}s；缺失值保留为空。输出与 CPU 参考逐项比对（同类别、框坐标容差 0.5px、置信度 0.003），不代表模型准确率评测。</p>
      <p className="mono">数据指纹：{report.config.dataset_sha256}<br/>模型：{report.config.model_sha256}</p>
      <div className="table-wrap"><table className="data-table"><thead><tr><th>登记设备</th><th>系统 / CPU线程</th><th>槽位 / 最大领取批次</th><th>Worker 版本</th></tr></thead><tbody>{report.config.devices.map((d)=><tr key={d.id}><td>{d.name}<br/><small>{d.id}</small></td><td>{d.os} / {d.cpu_count}</td><td>{d.slots} / {d.max_batch}</td><td>{d.agent_version ?? "未上报"}</td></tr>)}</tbody></table></div>
      {report.phases.filter((p)=>p.measured && p.resources).map((p)=><div key={p.job_id}><h4>{p.label} / {p.start ? formatUnix(p.start) : "—"} — {p.end ? formatUnix(p.end) : "—"}</h4><div className="table-wrap"><table className="data-table"><thead><tr><th>设备</th><th>CPU 均值</th><th>GPU 均值</th><th>样本数</th></tr></thead><tbody>{report.config.devices.map((d)=>{const resource=p.resources?.[d.id];return <tr key={d.id}><td>{d.name}</td><td>{num(resource?.mean_cpu_pct)}%</td><td>{num(resource?.mean_gpu_pct)}%</td><td>{resource?.count ?? 0}</td></tr>;})}</tbody></table></div></div>)}
      <p className="field-hint">完整输入哈希、设备快照、各阶段时间与逐点资源采样可通过报告 JSON 导出。主机身份及无外部竞争负载仍需队伍确认。</p>
    </details>
  </article>;
}
