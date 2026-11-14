import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { ApiClient, ProvisionStatus, TagStat, Worker, WorkerState, WorkloadSupport } from "../api";
import {
  TAG_CATEGORY_LABELS,
  TAG_CATEGORY_ORDER,
  tagCategory,
} from "../api";
import type { PollState } from "../hooks/usePolling";
import { usePolling } from "../hooks/usePolling";
import { formatDuration, formatNumber, formatPct, formatRelativeSeconds } from "../format";
import { Bar, EmptyBox, ErrorBox, LoadingLine, Section, StateBadge, UpdatedInfo } from "./ui";
import { ProvisionPanel, provisionSupportedFromError } from "./ProvisionPanel";
import { workloadLabel } from "../presets";
import "../chat.css";

const TAG_FILTER_KEY = "cef.workers.tagFilter";

interface Props {
  workers: PollState<Worker[]>;
  api: ApiClient;
  onRefresh: () => void;
  /** 页面顶部已有全局错误横幅时，不再重复渲染同一条错误。 */
  hideError?: boolean;
}

interface CategorizedTag {
  tag: string;
  category: TagStat["category"];
}

function readTagFilter(): string[] {
  try {
    const raw = window.localStorage.getItem(TAG_FILTER_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function saveTagFilter(tags: string[]): void {
  try {
    window.localStorage.setItem(TAG_FILTER_KEY, JSON.stringify(tags));
  } catch {
    // 存储不可用时只在本次会话生效。
  }
}

function categorize(tags: string[]): CategorizedTag[] {
  return tags
    .map((tag) => ({ tag, category: tagCategory(tag) }))
    .sort((a, b) => {
      const order = TAG_CATEGORY_ORDER.indexOf(a.category) - TAG_CATEGORY_ORDER.indexOf(b.category);
      return order !== 0 ? order : a.tag.localeCompare(b.tag);
    });
}

/** GET /api/v1/tags 不可用时，用设备上已注册的标签自己算一份。 */
function buildTagStats(workers: Worker[], server: TagStat[] | null): TagStat[] {
  const hasWorkerTags = workers.some((worker) => worker.tags.length > 0);
  if (server && (server.length > 0 || !hasWorkerTags)) {
    return [...server].sort((a, b) => {
      const order = TAG_CATEGORY_ORDER.indexOf(a.category) - TAG_CATEGORY_ORDER.indexOf(b.category);
      if (order !== 0) return order;
      if (a.online !== b.online) return b.online - a.online;
      return a.tag.localeCompare(b.tag);
    });
  }
  const map = new Map<string, TagStat>();
  for (const worker of workers) {
    for (const tag of worker.tags) {
      const entry = map.get(tag) ?? { tag, category: tagCategory(tag), total: 0, online: 0 };
      entry.total += 1;
      if (worker.state === "online") entry.online += 1;
      map.set(tag, entry);
    }
  }
  return [...map.values()].sort((a, b) => {
    const order = TAG_CATEGORY_ORDER.indexOf(a.category) - TAG_CATEGORY_ORDER.indexOf(b.category);
    if (order !== 0) return order;
    if (a.online !== b.online) return b.online - a.online;
    return a.tag.localeCompare(b.tag);
  });
}

export function WorkerPanel({ workers, api, onRefresh, hideError = false }: Props) {
  const stateOrder: Record<WorkerState, number> = { online: 0, draining: 1, offline: 2 };
  const list = [...(workers.data ?? [])].sort((a, b) => stateOrder[a.state] - stateOrder[b.state]);
  const onlineCount = list.filter((worker) => worker.state === "online").length;

  const tagPoll = usePolling((signal) => api.getTags(signal), 15000, api.hasToken, [api]);
  const [selectedTags, setSelectedTags] = useState<string[]>(readTagFilter);

  useEffect(() => {
    saveTagFilter(selectedTags);
  }, [selectedTags]);

  const tagStats = useMemo(() => buildTagStats(list, tagPoll.data), [list, tagPoll.data]);
  const groupedTags = useMemo(
    () => TAG_CATEGORY_ORDER
      .map((category) => ({ category, items: tagStats.filter((item) => item.category === category) }))
      .filter((group) => group.items.length > 0),
    [tagStats],
  );
  const visible = selectedTags.length === 0
    ? list
    : list.filter((worker) => selectedTags.every((tag) => worker.tags.includes(tag)));
  const visibleOnline = visible.filter((worker) => worker.state === "online").length;

  function toggleTag(tag: string): void {
    setSelectedTags((previous) =>
      previous.includes(tag) ? previous.filter((item) => item !== tag) : [...previous, tag]
    );
  }

  return (
    <Section
      title="设备面板"
      subtitle={workers.data ? `${onlineCount} 台在线，共 ${list.length} 台已登记` : undefined}
      actions={
        <>
          <UpdatedInfo updatedAt={workers.updatedAt} />
          <button type="button" className="btn btn--sm btn--ghost" onClick={onRefresh}>
            刷新
          </button>
        </>
      }
    >
      {workers.error && !hideError ? (
        <ErrorBox error={workers.error} onRetry={onRefresh} compact={Boolean(workers.data)} />
      ) : null}

      {!workers.data && workers.loading ? <LoadingLine text="正在读取设备列表…" /> : null}
      {!workers.data && !workers.loading && !workers.error ? (
        <EmptyBox title="暂无设备数据" hint="点击「刷新」重新获取设备列表。" />
      ) : null}
      {!workers.data && workers.error && hideError ? (
        <EmptyBox title="设备数据暂不可用" hint="请按上方提示检查连接，然后重新检测。" />
      ) : null}

      {groupedTags.length > 0 ? (
        <div className="tag-filter" role="group" aria-label="按标签筛选设备">
          <div className="tag-filter-head">
            <span className="tag-filter-title">标签筛选</span>
            <span className="field-hint" aria-live="polite">
              {selectedTags.length > 0
                ? `已选 ${selectedTags.length} 个标签，匹配 ${visible.length} 台（${visibleOnline} 台在线）`
                : `共 ${tagStats.length} 个标签，点击可多选`}
            </span>
            {selectedTags.length > 0 ? (
              <button type="button" className="btn btn--sm btn--ghost" onClick={() => setSelectedTags([])}>
                清除筛选
              </button>
            ) : null}
          </div>
          {groupedTags.map((group) => (
            <div className="tag-filter-group" key={group.category}>
              <span className="tag-filter-cat">{TAG_CATEGORY_LABELS[group.category]}</span>
              {group.items.map((item) => {
                const on = selectedTags.includes(item.tag);
                return (
                  <button
                    key={item.tag}
                    type="button"
                    className={"tag-chip" + (on ? " tag-chip--on" : "")}
                    aria-pressed={on}
                    onClick={() => toggleTag(item.tag)}
                    title={`在线 ${item.online} 台，共 ${item.total} 台`}
                  >
                    {item.tag}
                    <span className="tag-chip-count">{item.online}/{item.total}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}

      {workers.data && list.length === 0 ? (
        <EmptyBox
          title="还没有设备加入"
          hint="在队员的电脑上用上方的地址和 Token 启动 Worker。设备连接后会自动出现在这里。"
        />
      ) : null}

      {list.length > 0 && visible.length === 0 ? (
        <EmptyBox
          title="没有设备带选中的标签"
          hint="取消部分筛选，或等带该标签的设备上线。"
          action={
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setSelectedTags([])}>
              清除筛选
            </button>
          }
        />
      ) : null}

      {visible.length > 0 ? (
        <>
          {visibleOnline === 0 ? <p className="notice notice--warn">当前没有在线设备。启动或重连一台 Worker 后，排队任务就可以继续处理。</p> : null}
          <div className="worker-grid">
            {visible.map((worker) => (
              <WorkerCard key={worker.worker_id} worker={worker} api={api} onRefresh={onRefresh} />
            ))}
          </div>
        </>
      ) : null}
    </Section>
  );
}

function WorkerTagList({ tags }: { tags: string[] }) {
  const sorted = categorize(tags);
  const shown = sorted.slice(0, 4);
  const hidden = sorted.slice(4);
  const groups = TAG_CATEGORY_ORDER
    .map((category) => ({ category, items: shown.filter((item) => item.category === category) }))
    .filter((group) => group.items.length > 0);
  const hiddenGroups = TAG_CATEGORY_ORDER
    .map((category) => ({ category, items: hidden.filter((item) => item.category === category) }))
    .filter((group) => group.items.length > 0);
  return (
    <div className="worker-tags">
      {groups.map((group) => (
        <div className="worker-tag-group" key={group.category}>
          <span className="worker-tag-cat">{TAG_CATEGORY_LABELS[group.category]}</span>
          {group.items.map(({ tag }) => (
            <span key={tag} className="tag">{tag}</span>
          ))}
        </div>
      ))}
      {hidden.length > 0 ? (
        <details className="worker-tag-more">
          <summary aria-label={`还有 ${hidden.length} 个标签`}>{`+${hidden.length}`}</summary>
          <div className="worker-tag-more-body">
            {hiddenGroups.map((group) => (
              <div className="worker-tag-group" key={group.category}>
                <span className="worker-tag-cat">{TAG_CATEGORY_LABELS[group.category]}</span>
                {group.items.map(({ tag }) => (
                  <span key={tag} className="tag">{tag}</span>
                ))}
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

/** tags 里的 engine:cuda / engine:cpu 是给人看的分类；能力矩阵优先用 workloads[].engine。 */
function engineFromTags(tags: string[]): string | null {
  const tag = tags.find((item) => item.startsWith("engine:"));
  return tag ? tag.slice("engine:".length) : null;
}

function modelText(models: WorkloadSupport["models"]): string {
  if (models.length === 0) return "—";
  return models.map((model) => model.name + " " + model.sha256.slice(0, 12)).join("、");
}

/**
 * 环境能力矩阵：这台设备声明能跑哪些 workload、版本、引擎、模型 sha，
 * 以及缺什么（available=false 时后端给出的 reason）。
 * 老版本后端不返回 workloads[] 时整块隐藏。
 */
function CapabilityMatrix({ worker }: { worker: Worker }) {
  const rows = worker.workloads ?? [];
  if (rows.length === 0) return null;
  const missing = rows.filter((row) => !row.available);
  return (
    <div className="worker-capabilities">
      <div className="worker-capabilities-head">
        <span className="worker-capabilities-title">环境能力</span>
        <span className="field-hint">
          {missing.length === 0
            ? "声明的 " + rows.length + " 个 workload 全部可用"
            : "缺 " + missing.length + " 项"}
        </span>
      </div>
      <div className="capability-wrap">
        <table className="capability-table" aria-label={worker.name + " 的环境能力"}>
          <thead>
            <tr>
              <th>工作负载</th>
              <th>版本</th>
              <th>引擎</th>
              <th>模型 sha</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.kind} className={row.available ? undefined : "capability-row--missing"}>
                <td>{workloadLabel(row.kind)}</td>
                <td className="mono">{row.version ?? "—"}</td>
                <td>{row.engine ?? engineFromTags(worker.tags) ?? "—"}</td>
                <td className="mono capability-model" title={row.models.map((model) => model.name + " " + model.sha256).join("\n")}>
                  {modelText(row.models)}
                </td>
                <td>
                  {row.available ? (
                    <span className="badge badge--ok">可用</span>
                  ) : (
                    <span className="badge badge--warn">缺失</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {missing.length > 0 ? (
        <ul className="worker-missing">
          {missing.map((row) => (
            <li key={row.kind}>
              <span className="mono">{row.kind}</span>
              {row.reason ? "：" + row.reason : "：设备没有说明原因"}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function WorkerCard({
  worker,
  api,
  onRefresh,
}: {
  worker: Worker;
  api: ApiClient;
  onRefresh: () => void;
}) {
  const offline = worker.state === "offline";
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<unknown>(null);
  const [editingTags, setEditingTags] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const [tagSaving, setTagSaving] = useState(false);
  const [tagError, setTagError] = useState<unknown>(null);
  const [tagNotice, setTagNotice] = useState<string | null>(null);
  const [provisionStatus, setProvisionStatus] = useState<ProvisionStatus | null>(null);
  /** null = 还在探测；false = 后端没有该接口（隐藏入口）；true = 有接口，可请求。 */
  const [provisionSupported, setProvisionSupported] = useState<boolean | null>(null);
  const [showProvision, setShowProvision] = useState(false);
  const probeRef = useRef(false);
  const tagInputId = useId();
  const provisionPanelId = useId();

  // 只在挂载后探测一次：GET /workers/{id}/provision。404 → 整个入口隐藏；
  // 403 → 接口存在但设备没授权，入口保留（请求时后端会再次拒绝）。
  useEffect(() => {
    if (probeRef.current || !api.hasToken || worker.state === "offline") return;
    probeRef.current = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const status = await api.getProvision(worker.worker_id, controller.signal);
        if (controller.signal.aborted) return;
        setProvisionStatus(status);
        setProvisionSupported(true);
      } catch (error) {
        if (controller.signal.aborted) return;
        setProvisionSupported(provisionSupportedFromError(error));
      }
    })();
    return () => controller.abort();
  }, [api, worker.worker_id, worker.state]);

  async function forget() {
    if (removing || !window.confirm(`移除离线设备「${worker.name}」的记录？`)) return;
    setRemoveError(null);
    setRemoving(true);
    try {
      await api.forgetWorker(worker.worker_id);
      onRefresh();
    } catch (error) {
      setRemoveError(error);
    } finally {
      setRemoving(false);
    }
  }

  function openTagEditor(): void {
    setTagDraft(worker.tags.join(", "));
    setTagError(null);
    setTagNotice(null);
    setEditingTags(true);
  }

  async function saveTags(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (tagSaving) return;
    const raw = tagDraft.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean);
    setTagSaving(true);
    setTagError(null);
    setTagNotice(null);
    try {
      const result = await api.setWorkerTags(worker.worker_id, raw);
      setTagNotice(
        result.rejected.length > 0
          ? "已保存；被服务端拒绝的标签：" + result.rejected.join("、")
          : "标签已保存：" + (result.tags.length > 0 ? result.tags.join("、") : "（无标签）")
      );
      setEditingTags(false);
      onRefresh();
    } catch (error) {
      setTagError(error);
    } finally {
      setTagSaving(false);
    }
  }

  return (
    <article className={"worker-card" + (offline ? " worker-card--offline" : "")}>
      <div className="worker-head">
        <span className={"dot dot--" + (offline ? "idle" : worker.state === "draining" ? "warn" : "ok")} aria-hidden="true" />
        <h3 className="worker-name" title={worker.worker_id}>
          {worker.name}
        </h3>
        <StateBadge state={worker.state} kind="worker" />
        <button
          type="button"
          className="btn btn--sm btn--ghost"
          onClick={() => (editingTags ? setEditingTags(false) : openTagEditor())}
          aria-expanded={editingTags}
          aria-label={`编辑设备 ${worker.name} 的标签`}
        >
          {editingTags ? "收起标签" : "编辑标签"}
        </button>
        {provisionSupported && !offline ? (
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => setShowProvision((value) => !value)}
            aria-expanded={showProvision}
            aria-controls={provisionPanelId}
          >
            {showProvision ? "收起配置" : "配置环境"}
          </button>
        ) : null}
        {offline ? (
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => void forget()}
            disabled={removing}
            aria-label={`移除离线设备 ${worker.name}`}
          >
            {removing ? "移除中…" : "移除"}
          </button>
        ) : null}
      </div>
      {removeError ? <ErrorBox error={removeError} compact /> : null}
      {tagError ? <ErrorBox error={tagError} compact /> : null}
      {tagNotice ? <div className="notice" role="status">{tagNotice}</div> : null}

      {editingTags ? (
        <form className="worker-tag-editor" onSubmit={(event) => void saveTags(event)}>
          <label htmlFor={tagInputId} className="field-label">设备标签（逗号分隔）</label>
          <input
            id={tagInputId}
            className="input"
            value={tagDraft}
            onChange={(event) => setTagDraft(event.target.value)}
            placeholder="role:gpu, hw:rtx5060, site:lab"
            disabled={tagSaving}
          />
          <div className="worker-tag-editor-actions">
            <span className="field-hint">小写字母、数字和 . _ : -，最多 8 个，保存后由服务端规范化</span>
            <button type="submit" className="btn btn--sm btn--primary" disabled={tagSaving}>
              {tagSaving ? "保存中…" : "保存标签"}
            </button>
          </div>
        </form>
      ) : null}

      <div className="worker-meta">
        <span>{worker.os ?? "未知系统"}</span>
        <span>{worker.cpu_count ? worker.cpu_count + " 核" : "— 核"}</span>
        <span>
          槽位 {worker.running ?? 0}/{worker.slots ?? "—"}
        </span>
      </div>

      {worker.tags.length > 0 ? <WorkerTagList tags={worker.tags} /> : null}

      <CapabilityMatrix worker={worker} />

      <div className="meters">
        <MeterRow label="CPU" value={worker.cpu_pct} tone="cpu" />
        <MeterRow label="内存" value={worker.mem_pct} tone="mem" />
        <MeterRow label="GPU" value={worker.gpu_pct} tone="gpu" />
      </div>

      <dl className="worker-stats">
        <div>
          <dt>已完成</dt>
          <dd>{formatNumber(worker.completed_total)}</dd>
        </div>
        <div>
          <dt>平均耗时</dt>
          <dd>{formatDuration(worker.mean_duration_s)}</dd>
        </div>
        <div>
          <dt>吞吐</dt>
          <dd>{formatNumber(worker.throughput_per_min, 2)} /min</dd>
        </div>
        <div>
          <dt>最后心跳</dt>
          <dd>{formatRelativeSeconds(worker.last_seen_s_ago)}</dd>
        </div>
      </dl>

      {provisionSupported && showProvision ? (
        <div id={provisionPanelId}>
          <ProvisionPanel
            api={api}
            worker={worker}
            initialStatus={provisionStatus}
            onRefresh={onRefresh}
          />
        </div>
      ) : null}
    </article>
  );
}

function MeterRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null;
  tone: "cpu" | "mem" | "gpu";
}) {
  return (
    <div className="meter-row">
      <span className="meter-label">{label}</span>
      <Bar value={value} tone={tone} label={label} />
      <span className="meter-value">{formatPct(value)}</span>
    </div>
  );
}
