"""Built-in chat agent: natural language -> a validated job spec.

Why it lives on the server and not in the browser:
  * the OpenAI key must never be shipped to a judge's laptop,
  * the model gets the *same* token-scoped tools a human has — no special powers,
  * every proposal is validated against the same pydantic schemas as a CLI submit,
    so the agent cannot invent a parameter that the worker would not understand.

The agent never submits by itself: it returns a preview, the UI shows it, the
human clicks "confirm". A wrong parameter at demo time wastes a real render.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from cef_protocol.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentPreview,
    AgentToolTrace,
)
from cef_protocol.workloads import WORKLOAD_UNITS, WORKLOADS, InvalidParams, parse_frames, validate_params

log = logging.getLogger("cef.agent")

SYSTEM_PROMPT = """你是 Campus Edge Fabric 的算力调度助手。这个服务把队友各自的电脑组成一个临时算力池。

你能做的事：
1. 判断用户想跑什么 workload（thumbnail=批量图片缩放/转码，infer=ONNX 目标检测，blender=逐帧渲染），
   并在参数不确定时给出合理默认值。用户通常不懂技术参数，别把参数清单甩给用户，直接替他决定。
2. 用工具查看真实状态：在线设备、负载、任务进度、最近的调度事件。
3. 解释调度行为：为什么任务排队、为什么某台设备没接到活、任务失败/重试意味着什么。

硬规则：
- 绝不编造数字。设备数、吞吐、耗时都必须来自工具返回。
- 提交任务只能用 preview_job，它只做校验和预览，真正提交由用户点确认。
- 输入来自通过 CEF CLI 登记的数据集；网页不上传或管理文件。缺少数据集时提示用户让本地 Agent 调用 cef dataset add。
- 正式对照评测有独立入口；普通作业没有单机基线时，不得编造聚合效率。
- 如果用户要的东西这个池子做不了（比如跑任意脚本、训练模型），直接说明不支持并给替代方案。
- 回答用中文，简洁，最多 4 句话。不要输出 JSON。
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_workloads",
            "description": "列出支持的 workload 种类、参数的 JSON Schema，以及哪些设备能运行。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workers",
            "description": "列出算力池里的设备：在线状态、CPU/GPU 负载、槽位、已完成任务数。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pool_stats",
            "description": "整池统计：每分钟完成任务数、p50/p95 耗时、各设备利用率、瓶颈提示。",
            "parameters": {
                "type": "object",
                "properties": {"window_s": {"type": "integer", "description": "统计窗口秒数，默认 300"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_job",
            "description": "查询某个任务的进度、ETA、按设备分解（谁做了多少个）。",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_events",
            "description": "最近的调度事件（租约过期、任务重排、设备断线等），用于解释任务为什么卡住或重跑。",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_job",
            "description": (
                "校验一组 workload 参数并生成提交预览（不会真的提交）。"
                "输入文件由界面已上传，不需要你传路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workload": {"type": "string", "enum": [str(k) for k in WORKLOADS]},
                    "params": {"type": "object", "description": "workload 参数，见 list_workloads 的 schema"},
                },
                "required": ["workload", "params"],
            },
        },
    },
]


class AgentError(RuntimeError):
    pass


class AgentUnavailable(AgentError):
    """No API key configured - the endpoint answers 503 with a clear message."""


def _client(port: int, token: str):
    from cef_protocol import CefClient

    return CefClient(f"http://127.0.0.1:{port}", token, timeout=20.0)


def _summarize(name: str, result: Any) -> str:
    try:
        if name == "list_workloads":
            return ", ".join(f"{w['kind']}({len(w.get('available_on') or [])} 台)" for w in result)
        if name == "list_workers":
            online = [w["name"] for w in result if w.get("state") == "online"]
            return f"{len(result)} 台设备，在线 {len(online)}: {', '.join(online[:5]) or '-'}"
        if name == "pool_stats":
            return f"{result.get('tasks_per_min')} tasks/min, 排队 {result.get('tasks_queued')}, 重试 {result.get('retries')}"
        if name == "get_job":
            return f"{result.get('state')} {result.get('succeeded')}/{result.get('total')} eta={result.get('eta_s')}"
        if name == "recent_events":
            return f"{len(result)} 条事件: " + "; ".join(sorted({e.get('kind', '') for e in result})[:4])
        if name == "preview_job":
            return f"{result.get('workload')} params={json.dumps(result.get('params'), ensure_ascii=False)}"
    except Exception:  # noqa: BLE001
        pass
    return "ok"


def _dispatch(name: str, args: dict[str, Any], ctx: AgentChatRequest, port: int, token: str) -> Any:
    api = _client(port, token)
    try:
        if name == "list_workloads":
            return [
                {
                    "kind": w.kind,
                    "unit": w.unit,
                    "params_schema": w.params_schema,
                    "available_on": w.available_on,
                }
                for w in api.workloads().workloads
            ]
        if name == "list_workers":
            return [w.model_dump() for w in api.workers().workers]
        if name == "pool_stats":
            return api.stats(int(args.get("window_s") or 300)).model_dump()
        if name == "get_job":
            return api.get_job(str(args["job_id"])).model_dump()
        if name == "recent_events":
            resp = api.request("GET", "/api/v1/events", params={"limit": int(args.get("limit") or 30)})
            return resp.json().get("events", [])
        if name == "preview_job":
            workload = str(args.get("workload") or "")
            raw_params = args.get("params") or {}
            model = validate_params(workload, raw_params)  # raises InvalidParams
            params = model.model_dump()
            count = None
            if workload == "blender" and "frames" in params:
                count = len(parse_frames(str(params["frames"])))
            elif ctx.context.files:
                count = len(ctx.context.files)
            dataset = (ctx.context.dataset or "").strip() or None
            if not dataset:
                raise InvalidParams("请先选择已登记的数据集；由本地 Agent 通过 cef dataset add 登记")
            detail = api.request("GET", "/api/v1/datasets/" + __import__("urllib.parse", fromlist=["quote"]).quote(dataset, safe="")).json()
            if workload != "blender":
                count = detail["count"]
            note = f"输入来自数据集 {dataset}，共 {detail['count']} 个文件；确认后提交"
            return {
                "workload": workload,
                "params": params,
                "task_count": count,
                "note": note,
                "unit": WORKLOAD_UNITS.get(workload, "tasks"),
                "dataset": dataset,
            }
        raise AgentError(f"unknown tool: {name}")
    finally:
        api.close()


def _post(base_url: str, key: str, payload: dict[str, Any], timeout: float = 90.0) -> dict[str, Any]:
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise AgentError(f"无法连接模型服务: {exc}") from exc
    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            detail = resp.json().get("error", {}).get("message", detail)
        except Exception:  # noqa: BLE001
            pass
        raise AgentError(f"模型服务返回 {resp.status_code}: {detail}")
    return resp.json()  # type: ignore[no-any-return]


def chat(req: AgentChatRequest, port: int, token: str) -> AgentChatResponse:
    key = os.environ.get("CEF_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("CEF_AGENT_MODEL", "gpt-5.4-mini")
    base_url = os.environ.get("CEF_AGENT_BASE_URL", "https://api.openai.com/v1")
    if not key:
        raise AgentUnavailable("未配置 CEF_AGENT_API_KEY（写进 /etc/cef/agent.env 后重启服务）")

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in req.messages[-12:]:
        if m.role in ("user", "assistant"):
            messages.append({"role": m.role, "content": m.content[:4000]})
    if req.context.files:
        names = ", ".join(f.name for f in req.context.files[:5])
        messages.append(
            {
                "role": "system",
                "content": f"本次界面上传了 {len(req.context.files)} 个文件: {names}",
            }
        )

    trace: list[AgentToolTrace] = []
    preview: AgentPreview | None = None
    usage: dict[str, Any] | None = None

    for _ in range(5):
        data = _post(
            base_url,
            key,
            {"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto"},
        )
        usage = data.get("usage") or usage
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls:
            return AgentChatResponse(
                reply=(msg.get("content") or "").strip() or "（模型没有返回内容）",
                preview=preview,
                tool_trace=trace,
                model=model,
                usage=usage,
            )
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result = _dispatch(name, args, req, port, token)
                ok, summary = True, _summarize(name, result)
                if name == "preview_job":
                    preview = AgentPreview(**result)
            except InvalidParams as exc:
                ok, summary = False, f"参数校验失败: {exc}"
                result = {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                ok, summary = False, f"{type(exc).__name__}: {exc}"
                result = {"error": str(exc)}
            trace.append(AgentToolTrace(name=name, args=args, ok=ok, summary=summary))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False)[:6000],
                }
            )

    # ran out of tool budget: ask for a plain answer
    data = _post(
        base_url,
        key,
        {"model": model, "messages": messages, "tool_choice": "none"},
    )
    reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "（工具调用次数超限）"
    return AgentChatResponse(reply=reply.strip(), preview=preview, tool_trace=trace, model=model, usage=usage)
