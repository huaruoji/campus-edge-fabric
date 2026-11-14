"""Recheck a completed infer run and export compact evidence, a Chinese report and charts."""
from __future__ import annotations

import argparse
import math
import statistics
from pathlib import Path

from common import REPO, aggregate, config, digest, load_inputs, read_json, verify_archive, write_json
from run_gpu_benchmark import CONDITIONS, make_plan


def audit_run(run, dataset_root, reference_path, partial=False):
    progress = read_json(run / "progress.json")
    if not partial and (progress.get("state") != "complete" or progress.get("measured_completed") != 27):
        raise ValueError("The full experiment is not complete; no performance report generated")
    experiment = read_json(run / "experiment.json")
    diagnostic = experiment.get("record_numerical_mismatches", False)
    if digest(reference_path) != experiment["reference_sha256"] or experiment["plan"] != make_plan():
        raise ValueError("Reference hash or experiment plan changed")
    reference = read_json(reference_path)
    groups = load_inputs(dataset_root)
    ids = list(experiment["worker_configs"])
    if len(ids) != 2:
        raise ValueError("Two selected Workers are required")
    providers = experiment["expected_providers"]
    if set(providers) != set(ids):
        raise ValueError("Missing final backend mapping")
    recorded_inputs = read_json(run / "inputs.json")
    for group, paths in groups.items():
        if recorded_inputs[group] != [{"path": p.relative_to(dataset_root.resolve()).as_posix(), "sha256": digest(p)} for p in paths]:
            raise ValueError("Input inventory changed")
    expected_rounds = [("smoke-" + c, c, "smoke", 0, groups["smoke"]) for c in CONDITIONS[:2]]
    for trial in make_plan():
        condition, repeat, size = trial["condition"], trial["repeat"], trial["size"]
        label = f"r{repeat}-n{size}-{condition}"
        for warm in (CONDITIONS[:2] if condition == "pool" else [condition]):
            expected_rounds.append((label + "-warm-" + warm, warm, "warmup", 0, groups["warmup"]))
        expected_rounds.append((label, condition, "measured", repeat, groups["benchmark"][:size]))
    present = {p.parent.name for p in run.glob("*/round.json")}
    if partial:
        expected_rounds = expected_rounds[:len(present)]
    if present != {r[0] for r in expected_rounds}:
        raise ValueError("Missing or extra recorded rounds")
    records = []
    for label, condition, phase, repeat, files in expected_rounds:
        folder = run / label
        row = read_json(folder / "round.json")
        if (row["label"], row["condition"], row["phase"], row["repeat"], row["size"]) != (
            label, condition, phase, repeat, len(files)
        ):
            raise ValueError("Round identity or validity mismatch")
        if not diagnostic and not row["valid"]:
            raise ValueError("Numerical mismatch in strict acceptance mode")
        if diagnostic and (not row.get("integrity_passed") or not row.get("configuration_stable")):
            raise ValueError("Integrity or configuration failed")
        for field in ("end_to_end_s", "server_job_s", "submit_to_terminal_s", "submit_request_s", "download_s"):
            if not math.isfinite(row[field]) or row[field] < 0 or (field == "end_to_end_s" and row[field] == 0):
                raise ValueError("Invalid timing")
        archive = folder / "result.zip"
        if digest(archive) != row["result_zip_sha256"]:
            raise ValueError("Saved ZIP hash changed")
        allowed = set(ids) if condition == "pool" else {ids[int(condition[-1]) - 1]}
        checked = verify_archive(archive, files, row["job_id"], reference, allowed, providers)
        for field in ("valid", "verified_images", "per_worker", "per_provider", "both_workers_contributed", "retries_in_final_attempts"):
            if checked[field] != row[field]:
                raise ValueError("Independent recheck differs: " + label + ": " + field)
        for field in ("numerical_mismatches", "returned_per_worker"):
            if field in row and row[field] != checked[field]:
                raise ValueError("Numerical/returned counts differ")
        for snapshot in ("workers-before.json", "workers-after.json"):
            workers = {w["worker_id"]: w for w in read_json(folder / snapshot)}
            if any(workers[wid]["state"] != "online" or config(workers[wid]) != experiment["worker_configs"][wid] for wid in ids):
                raise ValueError("Worker identity/configuration changed")
        records.append(row)
    if len({r["job_id"] for r in records}) != len(records):
        raise ValueError("Repeated Job ID across rounds")
    measured = [r for r in records if r["phase"] == "measured"]
    if measured != read_json(run / "measured-rounds.json"):
        raise ValueError("Measured-round inventory differs")
    if partial and [{k: r[k] for k in ("repeat", "size", "condition")} for r in measured] != make_plan()[:len(measured)]:
        raise ValueError("Partial measured rounds must be the exact planned prefix")
    summary = partial_summary(measured) if partial else aggregate(measured, diagnostic)
    if not partial and summary != read_json(run / "summary.json"):
        raise ValueError("Recomputed summary differs")
    for size, data in summary.items():
        for condition, values in data["conditions"].items():
            times = [r["end_to_end_s"] for r in measured if r["size"] == int(size) and r["condition"] == condition]
            values.update(trials=len(times), min_end_to_end_s=min(times), max_end_to_end_s=max(times))
    if partial:
        experiment = {**experiment, "partial_report": True, "observed_progress": progress}
    supplemental = []
    for path in run.glob("continuations/**/round.json"):
        row = read_json(path)
        if row["phase"] not in ("smoke", "warmup"):
            raise ValueError("Unexpected excluded measured round; inspect continuation")
        files = groups["smoke"] if row["phase"] == "smoke" else groups["warmup"]
        allowed = {ids[int(row["condition"][-1]) - 1]}
        if digest(path.parent / "result.zip") != row["result_zip_sha256"]:
            raise ValueError("Supplemental ZIP changed")
        checked = verify_archive(path.parent / "result.zip", files, row["job_id"], reference, allowed, providers)
        if checked["verified_images"] != row["verified_images"]:
            raise ValueError("Supplemental verification changed")
        supplemental.append({"kind": "extra_smoke_or_excluded_warmup", "job_id": row["job_id"],
                             "total": len(files), "verified_images": checked["verified_images"], "included_in_performance": False})
    for path in run.glob("continuations/*/recovered-jobs.json"):
        for recovered in read_json(path):
            folder = path.parent / "excluded" / recovered["round"]
            files = groups["warmup"] if "-warm-" in recovered["round"] else groups["benchmark"][:recovered["total"]]
            allowed = set(ids) if recovered["round"].endswith("pool") else {ids[int(recovered["round"][-1]) - 1]}
            checked = verify_archive(folder / "recovered-result.zip", files, recovered["job_id"], reference, allowed, providers)
            if checked["verified_images"] != recovered["verified_images"] or recovered["included_in_performance"]:
                raise ValueError("Recovered job evidence changed")
            supplemental.append({"kind": "recovered_interrupted_job", **recovered})
    experiment["supplemental_audit"] = supplemental
    experiment["read_retries"] = [event for path in run.glob("continuations/*/read-retries.json") for event in read_json(path)]
    return experiment, records, summary


def partial_summary(rows):
    """Describe available balanced conditions only; never impute missing repeats."""
    summary = {}
    for n in (8, 32, 110):
        conditions = {}
        for condition in CONDITIONS:
            group = [r for r in rows if r["size"] == n and r["condition"] == condition]
            if not group:
                break
            rates = [r["verified_images"] / r["end_to_end_s"] for r in group]
            times = [r["end_to_end_s"] for r in group]
            conditions[condition] = {"trials": len(group), "median_images_s": statistics.median(rates),
                                     "min_images_s": min(rates), "max_images_s": max(rates),
                                     "median_end_to_end_s": statistics.median(times), "min_end_to_end_s": min(times),
                                     "max_end_to_end_s": max(times), "verified_images": sum(r["verified_images"] for r in group),
                                     "outputs_total": n * len(group)}
        if len(conditions) != 3:
            continue
        p1, p2, pool = [conditions[c]["median_images_s"] for c in CONDITIONS]
        summary[str(n)] = {"conditions": conditions, "speedup": pool / max(p1, p2) if max(p1, p2) else None,
                           "eta": pool / (p1 + p2) if p1 + p2 else None,
                           "both_workers_in_every_pool_trial": all(r["both_workers_contributed"] for r in rows
                                                                   if r["size"] == n and r["condition"] == "pool")}
    return summary


def chart(out, experiment, summary, elapsed=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [*experiment["worker_labels"], "Together"]
    colors = ["#5470a8", "#d89136", "#278878"]
    fig, axes = plt.subplots(1, len(summary), figsize=(12, 4.3), sharey=not elapsed, layout="constrained", squeeze=False)
    for ax, (size, data) in zip(axes[0], summary.items(), strict=True):
        for i, (condition, color) in enumerate(zip(CONDITIONS, colors, strict=True)):
            values = data["conditions"][condition]
            unit = "end_to_end_s" if elapsed else "images_s"
            median = values["median_" + unit]
            ax.bar(i, median, color=color, width=.62,
                   yerr=[[median - values.get("min_" + unit, median)], [values.get("max_" + unit, median) - median]], capsize=4)
            if elapsed:
                ax.text(i, median + .03 * max(v["median_end_to_end_s"] for v in data["conditions"].values()),
                        f"{median:.2f}s", ha="center", fontsize=9)
        ax.set_xticks(range(3), labels, rotation=18, ha="right", fontsize=8)
        ratio = f"{data['speedup']:.2f}x" if data["speedup"] is not None else "N/A"
        trials = "/".join(str(data["conditions"][c].get("trials", 3)) for c in CONDITIONS)
        ax.set_title(f"{size} images | trials: {trials}" if elapsed else f"{size} images | {ratio} | trials: {trials}")
        ax.set_axisbelow(True)
        ax.grid(axis="y", alpha=.2)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0][0].set_ylabel("Seconds to retrieve all outputs" if elapsed else "CPU-reference-matching images / second")
    title = "Diagnostic comparison: only CPU-reference-matching outputs count" if experiment.get("record_numerical_mismatches") else (
        "Infer service throughput: medians of 3 runs; whiskers show min-max"
    )
    if elapsed:
        title = "Batch completion time; numerical agreement reported separately"
    if experiment.get("partial_report"):
        completed = experiment["observed_progress"]["measured_completed"]
        title = f"PARTIAL RUN ({completed}/27 trials) | " + title
    fig.suptitle(title, fontsize=11)
    for extension in ("png", "svg"):
        name = "elapsed" if elapsed else "throughput"
        artifact = out / f"{name}.{extension}"
        fig.savefig(artifact, dpi=180, bbox_inches="tight")
        if extension == "svg":
            artifact.write_text("\n".join(line.rstrip() for line in artifact.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    plt.close(fig)


def export(run, out, experiment, records, summary):
    out.mkdir(parents=True, exist_ok=True)
    ids = list(experiment["worker_configs"])
    measured = [r for r in records if r["phase"] == "measured"]
    compact_rows = [{k: v for k, v in r.items() if k != "resources"} | {
        "resources": {wid: {k: v for k, v in value.items() if k != "points"} for wid, value in r["resources"].items()}
    } for r in records]
    numerical_by_worker = {}
    for wid in ids:
        returned = sum(r.get("returned_per_worker", r["per_worker"]).get(wid, 0) for r in measured)
        matched = sum(r["per_worker"].get(wid, 0) for r in measured)
        failed_inputs = sorted({c["input"] for r in measured for c in read_json(run / r["label"] / "verification.json")["checks"]
                                if c["worker_id"] == wid and not c["passed"]})
        numerical_by_worker[wid] = {"returned": returned, "matched_cpu_reference": matched,
                                    "numerical_mismatches": returned - matched, "distinct_mismatching_inputs": failed_inputs}
    write_json(out / "evidence.json", {
        "source_run": run.name, "audit": f"{len(records)} saved result ZIPs revalidated against fixed inputs and CPU reference",
        "partial_report": experiment.get("partial_report", False), "observed_progress": experiment.get("observed_progress"),
        "continuations": experiment.get("continuations", []),
        "supplemental_audit": experiment.get("supplemental_audit", []), "read_retries": experiment.get("read_retries", []),
        "client_commit": experiment["client_commit"], "script_sha256": experiment["script_sha256"],
        "client_environment": {"python": experiment["python"], "os": experiment["client_os"],
                               "reference": experiment["reference_environment"]},
        "reference_sha256": experiment["reference_sha256"], "params": experiment["params"], "tolerance": experiment["tolerance"],
        "metric": experiment["metric"], "datasets_reused": experiment.get("datasets_reused", False),
        "record_numerical_mismatches": experiment.get("record_numerical_mismatches", False),
        "worker_configs": experiment["worker_configs"], "expected_providers": experiment["expected_providers"],
        "numerical_by_worker": numerical_by_worker,
        "summary": summary, "rounds": compact_rows,
    })
    passed = sum(r["verified_images"] for r in measured)
    total = sum(r["size"] for r in measured)
    auxiliary = [r for r in records if r["phase"] != "measured"]
    lines = ["# Infer 单机与双机对照报告", "", f"来源运行：`{run.name}`。", "",
             f"已保存 {len(measured)}/27 轮正式测量，返回 {total} 个输出，其中 **{passed}/{total}** 个通过固定 CPU 数值参考校验。",
             f"另有 {sum(r['verified_images'] for r in auxiliary)}/{sum(r['size'] for r in auxiliary)} 个冒烟和预热输出通过同一数值校验。",
             "固定 110 张测量图片，以嵌套的 8、32、110 张子集复用；输出数包含跨轮重复，不是不同原图数。", "",
             f"single-1：{experiment['worker_labels'][0]}；single-2：{experiment['worker_labels'][1]}。", "",
             "| 图片数 | 单机 1 张/秒 | 单机 2 张/秒 | 一起跑 张/秒 | 相对最快单机 | 两台均参与的 pool 轮次 |",
             "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    if summary:
        largest = max(summary, key=int)
        group = summary[largest]["conditions"]
        times = [group[c]["median_end_to_end_s"] for c in CONDITIONS]
        saving = (1 - times[2] / min(times[:2])) * 100
        lines[4:4] = [f"最大实测批次 {largest} 张：jochi、liusic、两台共同处理的整批耗时中位数分别为 "
                      f"**{times[0]:.2f}、{times[1]:.2f}、{times[2]:.2f} 秒**，双机相对耗时较短的单机减少 {saving:.2f}%。"
                      "这里统计取得全部输出的时间；输出是否通过数值参考需结合下文通过率判断。", ""]
    for size, data in summary.items():
        rates = [data["conditions"][c]["median_images_s"] for c in CONDITIONS]
        contributed = sum(r["both_workers_contributed"] for r in measured if r["condition"] == "pool" and r["size"] == int(size))
        gain = f"{(data['speedup']-1)*100:+.2f}%" if data["speedup"] is not None else "N/A"
        trials = sum(r["condition"] == "pool" and r["size"] == int(size) for r in measured)
        lines.append(f"| {size} | {rates[0]:.4f} | {rates[1]:.4f} | {rates[2]:.4f} | {gain} | {contributed}/{trials} |")
    notes = []
    if passed != total or len(measured) != 27:
        notes += ["**本报告是诊断性对照，数值一致性尚未全部通过，不能作为完整正确性验收或无条件加速宣传。**", ""]
    if experiment.get("partial_report"):
        notes += ["**运行因连接中断停止。本报告仅分析已保存轮次，重复次数不足，不补齐或假定缺失结果。**", ""]
    if experiment.get("continuations"):
        notes += ["本实验跨网络中断分段完成。恢复前重验原有证据与设备配置，重新冒烟和预热；中断轮次的旧计时不纳入，"
                  "旧任务先查询并取回结果，再补做该轮。恢复记录及排除证据保留在 continuations 中。", ""]
        supplemental = experiment["supplemental_audit"]
        notes += [f"另独立复核 {len(supplemental)} 个恢复/排除作业，"
                  f"{sum(r['verified_images'] for r in supplemental)}/{sum(r['total'] for r in supplemental)} 个输出通过数值参考；"
                  "这些输出与旧计时均不计入正式吞吐。", ""]
    details_at = next(i for i, line in enumerate(lines) if line.startswith("single-1："))
    lines[details_at:details_at] = notes
    lines += ["", "吞吐只计通过数值校验的输出，未通过项计为零有效工作；耗时保留整轮全部执行时间。",
              "两台贡献统计也是有效输出贡献。原始返回贡献单列在 JSON 的 returned_per_worker 中。", "",
              "| 图片数 | 条件 | 已保存轮数 | CPU 参考通过 / 总数 | 整轮耗时中位数（秒） |",
              "| ---: | --- | ---: | ---: | ---: |"]
    for size, data in summary.items():
        for condition in CONDITIONS:
            group = [r for r in measured if r["size"] == int(size) and r["condition"] == condition]
            count = sum(r["verified_images"] for r in group)
            seconds = data["conditions"][condition]["median_end_to_end_s"]
            lines.append(f"| {size} | {condition} | {len(group)} | {count}/{len(group)*int(size)} | {seconds:.3f} |")
    lines += ["", "![整批耗时](elapsed.png)", "", "![有效吞吐对比](throughput.png)", "",
              "图表使用已保存轮次的中位数，具体轮数见表及图；误差线为最小至最大值，不是置信区间。只有一轮的值没有重复性证据。",
              "计时口径：`" + experiment["metric"] + "`。本地 CPU 参考生成和结果数值校验不计时。",
              "速度是两台电脑及其部署、网络、Coordinator 的整体服务表现。未达到双机加速的规模也保留，不挑选最好一轮。",
              "各自吞吐之和仅是经验参照，不能解释为调度的理论最优上界。", "",
              "## 数值差异分布", "", "| Worker | 返回输出 | 通过 CPU 参考 | 未通过 | 涉及不同输入数 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for wid, values in numerical_by_worker.items():
        lines.append(f"| {experiment['worker_configs'][wid]['name']} | {values['returned']} | {values['matched_cpu_reference']} | "
                     f"{values['numerical_mismatches']} | {len(values['distinct_mismatching_inputs'])} |")
    lines += ["", "未通过数是跨轮输出数，不能等同于不同原图数；具体图片列表见精简证据。"
              "输出未满足固定数值容差不直接等同于模型语义判断错误，也不自动视为无害。", "",
              "## 复现与有效性", "",
              "输入为固定 COCO128 子集，每张不超过 500,000 字节；同一规模三条件输入相同。",
              "模型为固定 YOLO11n，imgsz=640，conf=0.25，max_det=100。冒烟与预热图片和正式集互不重叠。",
              "每个正式条件前预热；双机条件先分别预热两台。规模和条件顺序按重复轮换。",
              "复核所有结果 ZIP 的完整性、文件哈希、任务/输入/Worker 对应关系、逐份 CPU/CUDA 后端和数值参考。",
              "置信度容差 0.002、框坐标容差 0.3 像素，检测数量与类别一致；这不代表 mAP 或泛化精度。",
              "输出 provider 和能力自报不能代替远端算子跟踪；物理设备身份、完整硬件和部署提交需由设备操作者补充。",
              "资源点来自整机心跳，可能包含其他程序，短作业采样有限；缺失值没有按零填补。", "",
              f"正式轮次最终 attempt 中的重试合计：{sum(r['retries_in_final_attempts'] for r in measured)}。",
              "详细时间、设备配置和资源摘要见 [精简证据](evidence.json)。原图、模型、原始 ZIP 留在本地。", "",
              "## 服务开销观察", "",
              "下表保留 110 张共同运行时的服务端作业窗口、适配器处理时间之和与整机 GPU 心跳均值。"
              "处理时间之和不是并行执行墙钟，资源均值也不是本进程的专属占用。", "",
              "| pool 轮次 | 服务端秒 | 适配器时间之和（秒） | jochi GPU 心跳均值 / 点数 | liusic GPU 心跳均值 / 点数 |",
              "| --- | ---: | ---: | --- | --- |"]
    for r in measured:
        if r["condition"] != "pool" or r["size"] != 110:
            continue
        detail = read_json(run / r["label"] / "job-detail.json")
        adapter_seconds = sum(task.get("duration_s") or 0 for task in detail["tasks"])
        utilization = []
        for wid in ids:
            resources = r["resources"][wid]
            value = resources["mean_gpu_pct"]
            utilization.append((f"{value:.2f}%" if value is not None else "缺失") + f" / {resources['sample_count']}")
        lines.append(f"| {r['label']} | {r['server_job_s']:.3f} | {adapter_seconds:.3f} | {utilization[0]} | {utilization[1]} |")
    lines += ["", "这些观察只能说明应继续细分领取任务、输入传输、推理、结果上传与服务端处理等阶段，"
              "不能将全部差额归为网络耗时或把本次收益解释成 GPU 已充分利用。", "",
              "## 正式轮次", "", "| 轮次 | Job ID | 端到端秒 | 单机 1 / 单机 2 有效输出 |",
              "| --- | --- | ---: | ---: |"]
    for r in measured:
        lines.append(f"| {r['label']} | `{r['job_id']}` | {r['end_to_end_s']:.3f} | "
                     f"{r['per_worker'].get(ids[0], 0)} / {r['per_worker'].get(ids[1], 0)} |")
    chart(out, experiment, summary)
    chart(out, experiment, summary, elapsed=True)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--partial", action="store_true", help="explicitly export a partial diagnostic report; never impute missing trials")
    parser.add_argument("--dataset-root", type=Path, default=REPO.parent / "test-data")
    parser.add_argument("--reference", type=Path, default=REPO / "data/d-acceptance/infer-reference/reference.json")
    args = parser.parse_args()
    experiment, records, summary = audit_run(args.run.resolve(), args.dataset_root, args.reference, args.partial)
    output = args.output or args.run / "review"
    export(args.run, output, experiment, records, summary)
    print("Verified report:", (output / "report.md").resolve())


if __name__ == "__main__":
    main()
