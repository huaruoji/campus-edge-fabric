"""Persistent team workspace and auditable, finite-batch GPU comparisons."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import statistics
import threading
import time

from cef_protocol import new_id
from cef_protocol.model_manifest import expected_sha
from cef_protocol.models import ConversationCreate, ConversationPatch, ExperimentRequest, JobSpec
from cef_protocol.workloads import validate_params
from fastapi import APIRouter, Request

from . import db

router = APIRouter(prefix="/api/v1")
chat_locks: dict[str, threading.Lock] = {}


def error(status: int, message: str):
    from .api import _err

    return _err(status, "workspace_error", message)


def conversation_open(s, room):
    conn = db.reading(s.db_path)
    try:
        row = db.one(conn, "SELECT * FROM conversations WHERE id=? AND archived=0", (room,))
    finally:
        conn.close()
    if not row:
        raise error(409, "对话不存在或已归档，请新建对话")


@router.get("/conversations")
def conversations(request: Request):
    conn = db.reading(request.app.state.settings.db_path)
    try:
        return {"conversations": [dict(r) for r in db.rows(conn, "SELECT * FROM conversations ORDER BY updated_at DESC")]}
    finally:
        conn.close()


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, body: ConversationCreate):
    now, cid = time.time(), new_id("chat")
    title = body.title.strip()
    if not title:
        raise error(400, "对话标题不能为空")
    with db.transaction(request.app.state.settings.db_path) as conn:
        conn.execute("INSERT INTO conversations VALUES (?,?,0,?,?)", (cid, title, now, now))
    return {"id": cid, "title": title, "archived": 0, "created_at": now, "updated_at": now}


@router.patch("/conversations/{cid}")
def patch_conversation(request: Request, cid: str, body: ConversationPatch):
    with db.transaction(request.app.state.settings.db_path) as conn:
        row = db.one(conn, "SELECT * FROM conversations WHERE id=?", (cid,))
        if not row:
            raise error(404, "对话不存在")
        if body.title is not None and not body.title.strip():
            raise error(400, "对话标题不能为空")
        conn.execute(
            "UPDATE conversations SET title=?,archived=?,updated_at=? WHERE id=?",
            (
                body.title.strip() if body.title is not None else row["title"],
                int(body.archived) if body.archived is not None else row["archived"],
                time.time(),
                cid,
            ),
        )
        return dict(db.one(conn, "SELECT * FROM conversations WHERE id=?", (cid,)))


def read_experiment(s, eid):
    conn = db.reading(s.db_path)
    try:
        row = db.one(conn, "SELECT * FROM experiments WHERE id=?", (eid,))
        if not row:
            raise error(404, "评测不存在")
        return {"id": eid, "state": row["state"], "created_at": row["created_at"], "updated_at": row["updated_at"], **json.loads(row["report"])}
    finally:
        conn.close()


def save(s, eid, state, report):
    with db.transaction(s.db_path) as conn:
        conn.execute(
            "UPDATE experiments SET state=?,report=?,updated_at=? WHERE id=? AND (state!='canceled' OR ?='canceled')",
            (state, json.dumps(report, ensure_ascii=False, allow_nan=False), time.time(), eid, state),
        )


@router.get("/experiments")
def experiments(request: Request):
    s = request.app.state.settings
    conn = db.reading(s.db_path)
    try:
        ids = [r["id"] for r in db.rows(conn, "SELECT id FROM experiments ORDER BY created_at DESC LIMIT 100")]
    finally:
        conn.close()
    return {"experiments": [read_experiment(s, eid) for eid in ids]}


@router.get("/experiments/{eid}")
def experiment(request: Request, eid: str):
    return read_experiment(request.app.state.settings, eid)


@router.post("/experiments/{eid}/cancel")
def cancel_experiment(request: Request, eid: str):
    s = request.app.state.settings
    current = read_experiment(s, eid)
    if current["state"] in ("queued", "running"):
        report = {k: v for k, v in current.items() if k not in ("id", "state", "created_at", "updated_at")}
        save(s, eid, "canceled", report)
        stop_jobs(request, eid)
    return read_experiment(s, eid)


def stop_jobs(request, eid):
    from .api import cancel_job

    s = request.app.state.settings
    conn = db.reading(s.db_path)
    try:
        ids = [r["id"] for r in db.rows(conn, "SELECT id FROM jobs WHERE experiment_id=? AND state IN ('queued','running')", (eid,))]
    finally:
        conn.close()
    for jid in ids:
        cancel_job(request, jid)
    with db.transaction(s.db_path) as conn:
        conn.execute("DELETE FROM experiment_workers WHERE experiment_id=?", (eid,))


@router.post("/experiments", status_code=202)
async def create_experiment(request: Request, body: ExperimentRequest):
    from .api import _dataset_files

    s = request.app.state.settings
    if len(set(body.worker_ids)) != len(body.worker_ids):
        raise error(400, "设备不能重复")
    try:
        params = validate_params("infer", {**body.params, "device": "cuda"}).model_dump()
    except ValueError as exc:
        raise error(400, str(exc)) from exc
    sha = expected_sha(params["model"])
    if not sha:
        raise error(400, "正式评测只接受清单中已固定哈希的模型")
    refs = _dataset_files(s, body.dataset)
    if not refs or any(r.get("size", 0) > 500000 for r in refs) or len(refs) > 10000 or len({r["sha256"] for r in refs}) != len(refs):
        raise error(400, "评测需要 1–10000 个不重复的真实输入，请通过 CLI 整理数据集")
    fingerprint = hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).hexdigest()
    eid, now = new_id("exp"), time.time()
    with db.transaction(s.db_path) as conn:
        snapshots = []
        for wid in body.worker_ids:
            row = db.one(conn, "SELECT * FROM workers WHERE id=?", (wid,))
            if not row or row["state"] != "online" or now - (row["last_seen"] or 0) > s.offline_after_s:
                raise error(409, "选中的设备必须全部在线")
            supports = json.loads(row["workloads"])
            compatible = any(
                w["kind"] == "infer"
                and w.get("available", True)
                and w.get("engine") == "cuda"
                and any(m.get("sha256") == sha for m in w.get("models", []))
                for w in supports
            )
            if not compatible:
                raise error(409, "所有参与设备必须具有可用 CUDA 推理能力和相同的标准模型")
            if db.one(conn, "SELECT 1 FROM tasks WHERE worker_id=? AND state='leased'", (wid,)) or db.one(
                conn, "SELECT 1 FROM experiment_workers WHERE worker_id=?", (wid,)
            ):
                raise error(409, "设备正在计算或被另一场评测占用，请稍后重试")
            snapshots.append({**dict(row), "tags": json.loads(row["tags"]), "workloads": supports, "meta": json.loads(row["meta"])})
        report = {
            "config": {
                **body.model_dump(),
                "params": params,
                "model_sha256": sha,
                "inputs": refs,
                "dataset_sha256": fingerprint,
                "devices": snapshots,
                "timing": "coordinator created_at to finished_at; excludes CPU reference, warmup and correctness verification",
                "validation": (
                    "CPU numerical reference; one-to-one same class, box tolerance 0.5px, confidence tolerance 0.003; not an accuracy benchmark"
                ),
                "sampling_interval_s": s.heartbeat_interval_s,
                "measurement": "finite registered-dataset batch",
                "concurrency": "sum of selected worker slots; per-device slots/max_batch held constant",
            },
            "phases": [],
            "metrics": None,
            "warnings": [],
        }
        conn.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?)", (eid, "queued", body.model_dump_json(), json.dumps(report), now, now))
        for wid in body.worker_ids:
            conn.execute("INSERT INTO experiment_workers VALUES (?,?)", (wid, eid))
    task = asyncio.create_task(run_experiment(request.app, eid, body, refs, report))
    request.app.state.experiment_tasks.add(task)
    task.add_done_callback(request.app.state.experiment_tasks.discard)
    return read_experiment(s, eid)


def read_payload(app, task):
    output = json.loads(task["output"] or "{}")
    files = output.get("files", [])
    if len(files) != 1:
        raise ValueError("推理结果必须包含一个 JSON 文件")
    path = app.state.blobs.path_for(files[0]["sha256"])
    raw = path.read_bytes()
    if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != files[0]["sha256"]:
        raise ValueError("输出哈希或大小不合法")
    value = json.loads(raw)
    detections = value.get("detections")
    if not isinstance(detections, list):
        raise ValueError("缺少 detections")
    for d in detections:
        if (
            not isinstance(d.get("class_id"), int)
            or not isinstance(d.get("xyxy"), list)
            or len(d["xyxy"]) != 4
            or not all(math.isfinite(float(x)) for x in [*d["xyxy"], d["conf"]])
        ):
            raise ValueError("检测框或置信度无效")
    return value


def matches(actual, reference):
    if actual.get("size") != reference.get("size") or actual.get("model") != reference.get("model"):
        return False
    left = list(reference["detections"])
    for det in actual["detections"]:
        match = next(
            (
                i
                for i, ref in enumerate(left)
                if ref["class_id"] == det["class_id"]
                and abs(ref["conf"] - det["conf"]) <= 0.003
                and max(abs(a - b) for a, b in zip(ref["xyxy"], det["xyxy"], strict=True)) <= 0.5
            ),
            None,
        )
        if match is None:
            return False
        left.pop(match)
    return not left


def summarize_samples(s, ids, start, end):
    conn = db.reading(s.db_path)
    try:
        out = {}
        for wid in ids:
            samples = [
                dict(r)
                for r in db.rows(
                    conn, "SELECT ts,cpu_pct,gpu_pct FROM worker_stats WHERE worker_id=? AND ts>=? AND ts<=? ORDER BY ts", (wid, start, end)
                )
            ]
            out[wid] = {"samples": samples, "count": len(samples)}
            for field in ("cpu_pct", "gpu_pct"):
                values = [r[field] for r in samples if r[field] is not None and math.isfinite(r[field])]
                out[wid]["mean_" + field] = statistics.mean(values) if values else None
        return out
    finally:
        conn.close()


async def run_experiment(app, eid, spec, refs, report):
    from .api import create_job

    s = app.state.settings
    request = Request({"type": "http", "app": app, "headers": [], "cef.experiment": eid})
    reference = {}

    async def phase(label, ids, inputs, params, measured):
        if read_experiment(s, eid)["state"] == "canceled":
            raise asyncio.CancelledError
        request.scope["cef.inputs"] = inputs
        job = await create_job(request, JobSpec(workload="infer", params=params, name=f"{eid} {label}", worker_ids=ids).model_dump_json(), [])
        item = {"label": label, "worker_ids": ids, "job_id": job.job_id, "measured": measured, "state": "running"}
        report["phases"].append(item)
        save(s, eid, "running", report)
        deadline = time.monotonic() + spec.timeout_s
        while True:
            if read_experiment(s, eid)["state"] == "canceled":
                raise asyncio.CancelledError
            conn = db.reading(s.db_path)
            try:
                row = dict(db.one(conn, "SELECT * FROM jobs WHERE id=?", (job.job_id,)))
                tasks = [dict(t) for t in db.rows(conn, "SELECT * FROM tasks WHERE job_id=? ORDER BY idx", (job.job_id,))]
            finally:
                conn.close()
            if row["state"] not in ("queued", "running"):
                break
            if time.monotonic() > deadline:
                raise ValueError(f"阶段超时：{label}，请检查设备和 Worker 版本")
            await asyncio.sleep(0.25)
        item.update(
            state=row["state"],
            start=row["created_at"],
            end=row["finished_at"],
            total=len(tasks),
            retries=sum(max(0, t["attempt"] - 1) for t in tasks),
        )
        if row["state"] != "succeeded" or len(tasks) != len(inputs):
            raise ValueError(f"阶段未全部成功：{label}")
        valid = 0
        contribution = {wid: 0 for wid in spec.worker_ids}
        for task in tasks:
            value = await asyncio.to_thread(read_payload, app, task)
            expected_provider = "CPUExecutionProvider" if label == "reference" else "CUDAExecutionProvider"
            if value.get("provider") != expected_provider or value.get("model") != params["model"]:
                raise ValueError("执行引擎或模型不符；请升级所选 Worker，参考阶段必须实际使用 CPU")
            key = json.loads(task["inputs"])[0]["sha256"]
            if label == "reference":
                reference[key] = value
            elif not matches(value, reference[key]):
                raise ValueError(f"输出未通过 CPU 参考校验：{task['id']}")
            if task["worker_id"] not in ids:
                raise ValueError("结果来自未选中的设备")
            contribution[task["worker_id"]] += 1
            valid += 1
        elapsed = row["finished_at"] - row["created_at"]
        item.update(
            valid=valid,
            contribution=contribution,
            seconds=elapsed,
            throughput=valid / elapsed if elapsed > 0 else 0,
            resources=summarize_samples(s, spec.worker_ids, row["created_at"], row["finished_at"]),
        )
        if measured and any(contribution[wid] == 0 for wid in ids):
            raise ValueError("本轮未使全部参与设备产生有效结果，请增加真实输入数量")
        if measured and (elapsed < 30 or any(item["resources"][wid]["count"] < 5 for wid in ids)):
            report["warnings"].append(f"{label}：窗口少于30秒或资源采样少于5点；仅作有限批次结果，尚未证明稳定饱和吞吐")
        item["state"] = "verified"
        save(s, eid, "running", report)

    try:
        await phase("reference", [spec.worker_ids[0]], refs, {**report["config"]["params"], "device": "cpu"}, False)
        conditions = [[wid] for wid in spec.worker_ids] + [spec.worker_ids]
        for repeat in range(spec.repeats):
            ordered = conditions[repeat % len(conditions) :] + conditions[: repeat % len(conditions)]
            for ids in ordered:
                label = "pool" if len(ids) > 1 else ids[0]
                await phase(f"warmup-{repeat + 1}-{label}", ids, refs[: min(4, len(refs))], report["config"]["params"], False)
                await phase(f"r{repeat + 1}-{label}", ids, refs, report["config"]["params"], True)
                # Configuration changes invalidate the comparison rather than mixing two baselines.
                conn = db.reading(s.db_path)
                try:
                    for old in report["config"]["devices"]:
                        current = db.one(conn, "SELECT * FROM workers WHERE id=?", (old["id"],))
                        if (
                            not current
                            or any(current[k] != old[k] for k in ("slots", "max_batch", "agent_version"))
                            or json.loads(current["workloads"]) != old["workloads"]
                        ):
                            raise ValueError("测试期间设备配置或能力变化，请重新评测")
                finally:
                    conn.close()
        baselines = {
            wid: statistics.median(p["throughput"] for p in report["phases"] if p["measured"] and p["worker_ids"] == [wid]) for wid in spec.worker_ids
        }
        pool = statistics.median(p["throughput"] for p in report["phases"] if p["measured"] and len(p["worker_ids"]) > 1)
        report["metrics"] = {
            "baselines": baselines,
            "pool": pool,
            "eta_pct": pool / sum(baselines.values()) * 100,
            "speedup": pool / max(baselines.values()),
            "unit": "images/s",
        }
        if report["metrics"]["eta_pct"] > 100:
            report["warnings"].append("聚合效率超过100%，须复核批处理、输入和负载条件，不代表超过硬件能力")
        if spec.repeats < 3:
            report["warnings"].append("不足三轮，仅作流程检查，请增加重复轮数复测")
        report["warnings"].append("CPU参考只验证数值一致性，不代表模型准确率；设备所有者仍须确认物理设备身份及无外部竞争负载")
        save(s, eid, "completed", report)
    except asyncio.CancelledError:
        state = read_experiment(s, eid)["state"]
        save(s, eid, "canceled" if state == "canceled" else "interrupted", report)
    except Exception as exc:
        report["error"] = str(exc)[:1000]
        report["metrics"] = None
        save(s, eid, "failed", report)
    finally:
        stop_jobs(request, eid)
