import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient, type ApiClient, type ProvisionStatus, type Worker } from "../src/api";
import { TaskPreview } from "../src/components/TaskPreview";
import { ProvisionPanel } from "../src/components/ProvisionPanel";
import { WorkerPanel } from "../src/components/WorkerPanel";
import { JobChat } from "../src/components/JobChat";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); localStorage.clear(); sessionStorage.clear(); });

const preview = { workload: "thumbnail", params: { size: 128 }, task_count: 1, dataset: null, note: "" };
const worker: Worker = { worker_id: "w-test", name: "Test worker", state: "online", tags: [], last_seen_s_ago: 0, cpu_pct: 0, mem_pct: 0, gpu_pct: null, mean_duration_s: null };
const queued: ProvisionStatus = { id: "prov-1", worker_id: worker.worker_id, state: "queued", recipe: "infer", dry_run: true, log: [], created_at: 1, updated_at: 1 };
const done = { ...queued, state: "succeeded" as const, log: ["dry-run complete"] };
const dataset = { id: "ds-1", name: "bsds-smoke", description: null, count: 16, bytes: 1000, created_at: 1, updated_at: 1 };

function client(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    ...createApiClient("http://localhost", "test-only"),
    getConversations: vi.fn().mockResolvedValue([{id:"pool",title:"团队对话",archived:0,created_at:1,updated_at:1}]),
    getWorkers: vi.fn().mockResolvedValue([{...worker, workloads:[{kind:"infer",available:true,version:"test",engine:"cuda",models:[]}]}]),
    getTags: vi.fn().mockResolvedValue([]),
    getWorkloads: vi.fn().mockResolvedValue([{ kind: "thumbnail", available_on: [worker.worker_id] }]),
    listDatasets: vi.fn().mockResolvedValue([dataset]),
    getChatMessages: vi.fn().mockResolvedValue({ messages: [], latest: 0 }),
    ...overrides,
  };
}

describe("API wire contract", () => {
  it("reads provision history instead of inventing queued state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ requests: [done] }))));
    expect(await client().getProvision(worker.worker_id)).toEqual(done);
  });
  it("returns no status for empty history", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"requests":[]}')));
    expect(await client().getProvision(worker.worker_id)).toBeNull();
  });
  it("tracks the submitted request even when a newer one exists", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ requests: [{ ...queued, id: "prov-2" }, done] }))));
    expect((await client().getProvision(worker.worker_id, undefined, "prov-1"))?.state).toBe("succeeded");
  });
  it("rejects unknown states and missing tracked requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{"requests":[{"state":"unknown"}]}'))
      .mockResolvedValueOnce(new Response('{"requests":[]}'));
    vi.stubGlobal("fetch", fetchMock);
    await expect(client().getProvision("w")).rejects.toMatchObject({ kind: "parse" });
    await expect(client().getProvision("w", undefined, "missing")).rejects.toMatchObject({ kind: "parse" });
  });
  it("does not display rejected tags as saved", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"tags":[],"rejected_tags":["invalid"]}')));
    expect(await client().setWorkerTags("w", ["invalid"])).toEqual({ tags: [], rejected: ["invalid"] });
  });
  it("uploads multipart files, spec, and device tag together", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"job_id":"job-test"}'));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["image"], "test.jpg");
    await client().submitJob({ workload: "thumbnail", params: { size: 128 }, inputs: [{ name: file.name }], required_tag: "bench-test" }, [file]);
    const form = fetchMock.mock.calls[0][1].body as FormData;
    expect(JSON.parse(String(form.get("spec"))).required_tag).toBe("bench-test");
    expect((form.get("files") as File).name).toBe(file.name);
  });
});

describe("task confirmation", () => {
  it("preserves the submitted link when returning to the same preview", async () => {
    const submitJob = vi.fn().mockResolvedValue({ job_id: "job-saved" });
    const props = { api: client({ submitJob }), preview: { ...preview, dataset: "data" }, files: [], requiredTag: "", existingJobId: null, messageId: "message-1" };
    const view = render(<TaskPreview {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "确认创建任务" }));
    await screen.findByRole("link", { name: "查看新任务详情" });
    view.unmount();
    render(<TaskPreview {...props} />);
    expect(screen.getByRole("link", { name: "查看新任务详情" }).getAttribute("href")).toBe("#/jobs/job-saved");
    expect(screen.queryByRole("button", { name: "确认创建任务" })).toBeNull();
    expect(submitJob).toHaveBeenCalledTimes(1);
  });
  it("requires real files, then submits once and provides a details link", async () => {
    let resolve!: (value: { job_id: string }) => void;
    const submitJob = vi.fn().mockImplementation(() => new Promise((r) => { resolve = r; }));
    const api = client({ submitJob });
    const props = { api, preview, requiredTag: "bench-test", existingJobId: null };
    const view = render(<TaskPreview {...props} files={[]} />);
    expect((screen.getByRole("button", { name: "确认创建任务" }) as HTMLButtonElement).disabled).toBe(true);
    const file = new File(["image"], "sample.jpg");
    view.rerender(<TaskPreview {...props} files={[file]} />);
    fireEvent.click(screen.getByRole("button", { name: "确认创建任务" }));
    fireEvent.click(screen.getByRole("button", { name: "正在提交…" }));
    expect(submitJob).toHaveBeenCalledTimes(1);
    expect(submitJob).toHaveBeenCalledWith({ workload: "thumbnail", params: { size: 128 }, inputs: [{ name: "sample.jpg" }], required_tag: "bench-test" }, [file]);
    await act(async () => resolve({ job_id: "job-test" }));
    expect(screen.getByRole("link", { name: "查看新任务详情" }).getAttribute("href")).toBe("#/jobs/job-test");
  });
  it("pins the preview dataset and never uploads unrelated local files", async () => {
    const submitJob = vi.fn().mockResolvedValue({ job_id: "job-ds" });
    render(<TaskPreview api={client({ submitJob })} preview={{ ...preview, dataset: "original" }} files={[new File(["x"], "other.jpg")]} dataset={dataset} requiredTag="" existingJobId={null} />);
    fireEvent.click(screen.getByRole("button", { name: "确认创建任务" }));
    await screen.findByRole("link", { name: "查看新任务详情" });
    expect(submitJob).toHaveBeenCalledWith(expect.objectContaining({ dataset: "original", inputs: [], required_tag: null }), []);
  });
  it("shows submission errors and allows retry", async () => {
    const submitJob = vi.fn().mockRejectedValueOnce(new Error("failed")).mockResolvedValueOnce({ job_id: "retry" });
    render(<TaskPreview api={client({ submitJob })} preview={{ ...preview, dataset: "data" }} files={[]} requiredTag="" existingJobId={null} />);
    fireEvent.click(screen.getByRole("button", { name: "确认创建任务" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "确认创建任务" }));
    await screen.findByRole("link", { name: "查看新任务详情" });
    expect(submitJob).toHaveBeenCalledTimes(2);
  });
  it("submits registered data and selected machines from a persistent conversation without file controls", async () => {
    const messages: unknown[] = [];
    const getChatMessages = vi.fn().mockImplementation(async () => ({messages:[...messages],latest:1}));
    const chat = vi.fn().mockImplementation(async () => { messages.push({id:"msg-1",room:"pool",role:"assistant",author:"",content:"准备好了",preview:{...preview,dataset:dataset.name},job_id:null,trace:null,created_at:1}); return {reply:"准备好了",tool_trace:[]}; });
    const submitJob = vi.fn().mockResolvedValue({job_id:"job-integrated"});
    render(<JobChat api={client({chat,submitJob,getChatMessages})} token="test-only" />);
    const input = await screen.findByRole("textbox",{name:"给算力助手发消息"});
    expect(document.querySelector('input[type="file"]')).toBeNull();
    fireEvent.change(screen.getByLabelText("使用的数据集"),{target:{value:dataset.name}});
    fireEvent.change(input,{target:{value:"处理这些图片"}});
    expect(submitJob).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
    await screen.findByRole("button",{name:"确认创建任务"});
    expect(chat.mock.calls[0][0]).toMatchObject({room:"pool",context:{dataset:dataset.name,files:[]}});
    expect(submitJob).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("自动使用兼容设备"));
    fireEvent.click(screen.getByRole("checkbox",{name:/Test worker/}));
    fireEvent.click(screen.getByRole("button",{name:"确认创建任务"}));
    await screen.findByRole("link",{name:"查看新任务详情"});
    expect(submitJob).toHaveBeenCalledWith(expect.objectContaining({dataset:dataset.name,worker_ids:[worker.worker_id],conversation_id:"pool",message_id:"msg-1",inputs:[]}),[]);
  });
});

describe("worker provisioning", () => {
  it("preserves the upstream device consent rejection message", async () => {
    const getProvision = vi.fn().mockResolvedValue({ ...queued, state: "failed", log: ["device not authorised"] });
    render(<ProvisionPanel api={client({ getProvision })} worker={worker} initialStatus={queued} onRefresh={() => {}} />);
    await screen.findByText("设备拒绝执行：没有授权远程配置。");
    expect(screen.getByText(/这台设备没有授权远程配置/)).toBeTruthy();
  });
  it("keeps the entry available under StrictMode with no prior configuration", async () => {
    const getProvision = vi.fn().mockResolvedValue(null);
    render(<StrictMode><WorkerPanel api={client({ getProvision })} workers={{ data: [worker], error: null, loading: false, updatedAt: Date.now() }} onRefresh={() => {}} /></StrictMode>);
    expect(await screen.findByRole("button", { name: "配置环境" })).toBeTruthy();
    expect(getProvision).toHaveBeenCalledTimes(2);
  });
  it("resumes an existing request and labels dry-run completion accurately", async () => {
    const getProvision = vi.fn().mockResolvedValue(done);
    render(<ProvisionPanel api={client({ getProvision })} worker={worker} initialStatus={queued} onRefresh={() => {}} />);
    await screen.findByText("预演已完成，尚未安装或修改设备环境。");
    expect(getProvision).toHaveBeenCalledWith(worker.worker_id, expect.any(AbortSignal), queued.id);
    expect(screen.getByText("dry-run complete")).toBeTruthy();
  });
  it("does not restart polling when unmounted during a request", async () => {
    vi.useFakeTimers();
    let resolve!: (value: ProvisionStatus) => void;
    const getProvision = vi.fn().mockImplementation(() => new Promise((r) => { resolve = r; }));
    const view = render(<ProvisionPanel api={client({ getProvision })} worker={worker} initialStatus={queued} onRefresh={() => {}} />);
    const signal = getProvision.mock.calls[0][1] as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { resolve(queued); });
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(getProvision).toHaveBeenCalledTimes(1);
  });
});
