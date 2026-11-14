"""Two infer Workers: each alone and together, 8/32/110 inputs, three repeats.

Default is offline validation. --run asks for a hidden Token; no automatic resubmission.
Local CPU reference generation is separate from remote Worker performance measurements.
"""
from __future__ import annotations

import argparse
import getpass
import math
import os
import platform
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from common import (
    MODEL_SHA,
    PARAMS,
    PROVIDERS,
    REPO,
    SIZES,
    TOLERANCE,
    aggregate,
    compatible_workers,
    config,
    digest,
    job_can_use,
    load_inputs,
    model_evidence,
    observed_providers,
    read_json,
    resource_summary,
    select_workers,
    verify_archive,
    write_json,
)

CONDITIONS = ["single-1", "single-2", "pool"]


def make_plan():
    plan = []
    for repeat in range(1, 4):
        offset = repeat - 1
        sizes = [*SIZES[offset:], *SIZES[:offset]]
        conditions = CONDITIONS[offset:] + CONDITIONS[:offset]
        for size in sizes:
            for condition in conditions:
                plan.append({"repeat": repeat, "size": size, "condition": condition})
    return plan


def check_previous_job(client, job_id, out):
    """Inspect an existing job without resubmitting or canceling it."""
    job = client.get_job(job_id)
    write_json(out / "previous-job.json", job.model_dump(mode="json"))
    print(f"Previous job: {job.job_id}; state={job.state}; succeeded={job.succeeded}/{job.total}", flush=True)
    if job.state not in ("succeeded", "partial", "failed", "canceled"):
        raise ValueError("Previous job is still queued/running; no new work submitted. Inspect it before restarting the benchmark.")
    return {"job_id": job.job_id, "state": job.state, "succeeded": job.succeeded, "total": job.total,
            "included_in_new_measurement": False}


def registered_dataset(client, files, name, out):
    from cef_protocol.client import CefError

    expected = [{"name": p.name, "sha256": digest(p), "size": p.stat().st_size} for p in files]
    try:
        dataset = client.get_dataset(name)
    except CefError as error:
        if error.status != 404:
            raise
        # A failed/ambiguous POST is never automatically retried.
        dataset = client.create_dataset(name, files, "D fixed infer inputs; numerical reference experiment")
    actual = [{k: f[k] for k in ("name", "sha256", "size")} for f in dataset["files"]]
    if actual != expected:
        raise ValueError("Registered dataset input order, identities or hashes differ")
    write_json(out / (name + ".json"), dataset)
    return dataset["id"]


def save_report(out, rows, summary, worker_labels=None, metric=None):
    worker_labels = worker_labels or ["single-1 (CUDA)", "single-2 (CUDA)"]
    lines = ["# Infer: single Workers and pool", "", "Local CPU reference generation is excluded from performance timing.", "",
             f"single-1: {worker_labels[0]}; single-2: {worker_labels[1]}.", "",
             "Metric: " + (metric or "verified outputs / client submit-to-result-ZIP-download wall time"), "",
             "| Images | Single 1 images/s | Single 2 images/s | Pool images/s | S | eta | Both Workers in all pool trials |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for size, group in summary.items():
        rates = [group["conditions"][c]["median_images_s"] for c in CONDITIONS]
        speedup = f"{group['speedup']:.3f}" if group["speedup"] is not None else "N/A"
        eta = f"{group['eta']:.2%}" if group["eta"] is not None else "N/A"
        lines.append(f"| {size} | {rates[0]:.4f} | {rates[1]:.4f} | {rates[2]:.4f} | "
                     f"{speedup} | {eta} | {group['both_workers_in_every_pool_trial']} |")
    passed, total = sum(r["verified_images"] for r in rows), sum(r["size"] for r in rows)
    lines += ["", f"Numerical reference agreement: {passed}/{total} outputs. Only passing outputs count toward throughput."]
    if passed != total:
        lines += ["", "DIAGNOSTIC COMPARISON: numerical mismatches remain unresolved; this is not a full correctness acceptance."]
    lines += ["", "Only the measured sizes are characterized; no interpolated or guaranteed break-even threshold.",
              "Each median uses three trials. A small pool batch handled by one Worker is retained, not rerun to select a better result.",
              "Every output matches its Worker's predefined CUDA/CPU provider; numerical agreement counts are reported above. "
              "Provider metadata does not replace Worker-side kernel traces.",
              "Model hashes and engine are checked when reported by Worker capabilities; see experiment.json. "
              "Physical GPU identities, profiling state and deployed commits still require operator evidence.",
              "COCO128 is a training subset; reference agreement is not model accuracy or held-out generalization.",
              "", "| Trial | Job | Seconds | images/s | Worker contribution |", "| --- | --- | ---: | ---: | --- |"]
    for r in rows:
        lines.append(f"| {r['label']} | {r['job_id']} | {r['end_to_end_s']:.4f} | "
                     f"{r['verified_images'] / r['end_to_end_s']:.4f} | {r['per_worker']} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--smoke-only", action="store_true", help="with --run, validate 8 images on each selected Worker and stop")
    parser.add_argument("--single-smoke-tag", help="validate only this one Worker with 8 images; never measure performance")
    parser.add_argument("--tags", nargs=2)
    parser.add_argument("--providers", nargs="+", choices=tuple(PROVIDERS),
                        help="expected backend for each tag, in order; default cuda cuda; every output is checked")
    parser.add_argument("--previous-job", help="read this existing job first; submit nothing new while it is still active")
    parser.add_argument("--auto-workers", action="store_true",
                        help="select up to two compatible Workers; discover CPU/CUDA with strict smoke checks; reuse datasets")
    parser.add_argument("--record-numerical-mismatches", action="store_true",
                        help="diagnostic comparison: keep numerical failures visible and count only passing outputs; thresholds stay unchanged")
    parser.add_argument("--resume", type=Path, help="resume an interrupted auto experiment; retain completed rounds and recover in-flight jobs first")
    parser.add_argument("--dataset-root", type=Path, default=REPO.parent / "test-data")
    parser.add_argument("--reference", type=Path, default=REPO / "data/d-acceptance/infer-reference/reference.json")
    parser.add_argument("--output-root", type=Path, default=REPO / "data/d-acceptance")
    parser.add_argument("--server", default=os.environ.get("CEF_SERVER") or "https://cef.huaruoji.com")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    resumed_experiment, resumed_rows, prior_progress = None, [], None
    if args.resume:
        from curate_report import audit_run
        from recovery import remaining_plan
        args.resume = args.resume.resolve()
        if not args.resume.is_relative_to(args.output_root.resolve()):
            parser.error("Resume directory must be under output-root")
        if args.tags or args.providers or args.single_smoke_tag or args.smoke_only or args.previous_job:
            parser.error("Resume uses the saved workers/backends and discovers pending jobs; omit tags/providers/smoke/previous-job")
        prior_progress = read_json(args.resume / "progress.json")
        if prior_progress.get("state") != "stopped" or prior_progress.get("error_type") not in (
            "RemoteProtocolError", "ReadTimeout", "ConnectTimeout", "ReadError", "WriteError", "ConnectError", "TimeoutError"
        ):
            parser.error("Resume is only for a recorded transport interruption; investigate other failures separately")
        _, records, _ = audit_run(args.resume, args.dataset_root, args.reference, partial=True)
        resumed_experiment = read_json(args.resume / "experiment.json")
        if not resumed_experiment.get("backend_discovery"):
            parser.error("Resume requires an auto-workers experiment")
        resumed_rows = [r for r in records if r["phase"] == "measured"]
        remaining_plan(resumed_experiment["plan"], resumed_rows)
        args.auto_workers = True
        args.record_numerical_mismatches = resumed_experiment.get("record_numerical_mismatches", False)
        args.server = resumed_experiment["server"]
    if args.auto_workers and (args.tags or args.single_smoke_tag or args.providers):
        parser.error("--auto-workers selects the devices and discovers backends; omit tags/providers/single-smoke-tag")
    args.providers = args.providers or (["cuda"] if args.single_smoke_tag else ["cuda", "cuda"])
    if len(args.providers) != (1 if args.single_smoke_tag else 2):
        parser.error("Provide one backend per selected Worker: --providers cpu cuda, for example")
    run_kind = ("auto-infer" if args.auto_workers else "gpu-infer" if set(args.providers) == {"cuda"}
                else "mixed-infer" if len(set(args.providers)) > 1 else "cpu-infer")
    if args.record_numerical_mismatches:
        run_kind = "diagnostic-" + run_kind
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 86400:
        parser.error("Timeout must be finite and between 1 and 86400 seconds")
    groups = load_inputs(args.dataset_root)
    reference = read_json(args.reference)
    if reference["model_sha256"] != MODEL_SHA or reference["params"] != PARAMS or reference["tolerance"] != TOLERANCE:
        raise ValueError("Reference model, parameters or tolerance differ from the fixed experiment")
    for files in groups.values():
        for p in files:
            if reference["images"][p.name]["input_sha256"] != digest(p):
                raise ValueError(f"Reference source hash mismatch: {p.name}")
    plan = make_plan()
    if args.resume:
        print(f"Resume: {len(resumed_rows)}/27 measured rounds retained; only {27-len(resumed_rows)} remain.", flush=True)
    n_outputs = sum(p["size"] for p in plan)
    total_bytes = sum(p.stat().st_size for p in groups["smoke"]) * 2
    total_bytes += sum(sum(p.stat().st_size for p in groups["benchmark"][:r["size"]]) for r in plan)
    total_bytes += sum(p.stat().st_size for p in groups["warmup"]) * 36
    if args.single_smoke_tag:
        print("Plan: one targeted correctness smoke job, 8 distinct COCO images; no performance measurement.", flush=True)
    elif args.smoke_only:
        print("Plan: two targeted correctness smoke jobs, 8 distinct COCO images each; no performance measurement.", flush=True)
    else:
        print(f"Infer plan: 8/32/110 images x 3 conditions x 3 repetitions = {len(plan)} measured jobs, {n_outputs} outputs.", flush=True)
        print(f"Separate smoke: 2 x 8 images; warmups: 36 x 8 images. Legacy upload estimate {total_bytes / 1e6:.2f} MB.", flush=True)
        print("Both Workers are warmed individually before each pool trial. Reference generation is excluded from timing.", flush=True)
    if args.auto_workers:
        print("Auto: discover actual CPU/CUDA per Worker, then freeze; registered inputs are reused; no remote configuration changes.", flush=True)
    else:
        print("Expected backends in tag order:", args.providers, "(checked on EVERY output; remote backend configuration is not changed)", flush=True)
    if args.record_numerical_mismatches:
        print("Diagnostic mode: numerical mismatches are recorded, never counted as passed; integrity/backend/configuration errors still stop.",
              flush=True)
    if not args.run:
        print("Offline checks passed; no network request. Add --run with --auto-workers or explicit --tags TAG1 TAG2.")
        return 0
    if args.single_smoke_tag:
        if args.tags:
            parser.error("Use either --tags or --single-smoke-tag")
        args.tags = [args.single_smoke_tag]
    elif not args.auto_workers and (not args.tags or args.tags[0] == args.tags[1]):
        parser.error("--run requires two distinct unique tags")
    from recovery import ReadRetryClient, archive_round, recover_inflight

    token = os.environ.get("CEF_TOKEN") or getpass.getpass("CEF Token (hidden; not saved): ")
    if not token.strip():
        raise ValueError("A Token is required")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out = args.resume or args.output_root.resolve() / (run_kind + "-" + stamp)
    out.mkdir(parents=True, exist_ok=bool(args.resume))
    segment = out / "continuations" / stamp
    if args.resume:
        write_json(segment / "prior-progress.json", prior_progress)
    print("Evidence directory:", out, flush=True)
    write_json(out / "progress.json", {"state": "preflight", "measured_completed": len(resumed_rows)})
    with ReadRetryClient(args.server, token.strip(), timeout=60, retry_log=segment / "read-retries.json") as client:
        chosen_ids = list(resumed_experiment["worker_configs"]) if args.resume else None
        def workers():
            return [w.model_dump(mode="json") for w in client.workers().workers]

        def choose(current):
            if args.auto_workers:
                eligible = compatible_workers(current)
                if chosen_ids is None:
                    if not eligible:
                        raise ValueError("No online infer Worker reports the canonical model hash")
                    return eligible[:2]
                by_id = {w["worker_id"]: w for w in eligible}
                if not set(chosen_ids) <= by_id.keys():
                    raise ValueError("A selected Worker is no longer online/model-compatible")
                return [by_id[wid] for wid in chosen_ids]
            if not args.single_smoke_tag:
                return select_workers(current, args.tags)
            matches = [w for w in current if args.tags[0] in w["tags"]]
            if len(matches) != 1 or matches[0]["state"] != "online" or not any(
                c["kind"] == "infer" and c["available"] for c in matches[0]["workloads"]
            ):
                raise ValueError("The smoke tag must identify exactly one registered, online infer Worker")
            return matches

        try:
            initial = workers()
            write_json((segment if args.resume else out) / "workers-initial.json", initial)
            selected = choose(initial)
            if args.resume and {w["worker_id"]: config(w) for w in selected} != resumed_experiment["worker_configs"]:
                raise ValueError("Worker configuration differs from the original segment; cannot combine measurements")
            chosen_ids = [w["worker_id"] for w in selected]
            if args.auto_workers:
                args.providers = ["cpu"] * len(selected)  # capability metadata is not an actual backend measurement
                args.smoke_only = args.smoke_only or len(selected) < 2
                if len(selected) < 2:
                    print("Only one compatible Worker: running correctness smoke only; no two-device performance claim.", flush=True)
                for w in initial:
                    if w["worker_id"] not in chosen_ids:
                        print("Not selected:", w["name"], "(offline, model-incompatible, or outside the selected pair)", flush=True)
            previous_job = None
            if args.previous_job:
                if args.auto_workers:
                    previous_job = client.request("GET", f"/api/v1/jobs/{args.previous_job}").json()
                    write_json(out / "previous-job.json", previous_job)
                    if previous_job["state"] in ("queued", "running") and job_can_use(previous_job, selected):
                        print("Previous job uses the selected pair; waiting up to 90 seconds without submitting new work.", flush=True)
                        until = time.monotonic() + 90
                        while previous_job["state"] in ("queued", "running") and time.monotonic() < until:
                            time.sleep(5)
                            previous_job = client.request("GET", f"/api/v1/jobs/{args.previous_job}").json()
                            write_json(out / "previous-job.json", previous_job)
                        if previous_job["state"] in ("queued", "running"):
                            raise ValueError("Previous job still active after 90s; no new task submitted. "
                                             "See previous-job.json and workers-initial.json")
                    print("Previous job:", previous_job["state"], "can use selected pair:", job_can_use(previous_job, selected), flush=True)
                    previous_job = {k: previous_job.get(k) for k in ("job_id", "state", "total", "succeeded", "required_tag")}
                else:
                    previous_job = check_previous_job(client, args.previous_job, out)
            expected_providers = (dict(resumed_experiment["expected_providers"]) if args.resume else
                                  {w["worker_id"]: PROVIDERS[p] for w, p in zip(selected, args.providers, strict=True)})
            reported_models = model_evidence(selected, expected_providers)
        except Exception as error:
            write_json(out / "progress.json", {"state": "stopped", "stage": "preflight", "measured_completed": len(resumed_rows),
                                               "error_type": type(error).__name__, "reason": str(error), "jobs_submitted": 0})
            raise
        for worker in selected:
            print("Selected:", worker["name"], worker["worker_id"], "slots=", worker["slots"],
                  "model evidence=", reported_models[worker["worker_id"]], flush=True)
        ids = [w["worker_id"] for w in selected]
        worker_labels = [f"{w['name']} ({p.upper()})" for w, p in zip(selected, args.providers, strict=True)]
        fingerprints = {w["worker_id"]: config(w) for w in selected}
        experiment = {
            "client_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
            "script_sha256": {name: digest(Path(__file__).with_name(name)) for name in ("run_gpu_benchmark.py", "common.py")},
            "reference_sha256": digest(args.reference), "reference_environment": reference["environment"],
            "python": sys.version, "client_os": platform.platform(), "server": args.server,
            "health": client.health().model_dump(mode="json"), "tags": args.tags, "worker_configs": fingerprints,
            "model_sha256_reference": MODEL_SHA, "remote_model_evidence": reported_models,
            "experiment_kind": run_kind, "expected_providers": expected_providers, "worker_labels": worker_labels,
            "backend_discovery": args.auto_workers, "datasets_reused": args.auto_workers,
            "record_numerical_mismatches": args.record_numerical_mismatches,
            "previous_job_checked": previous_job,
            "backend_control": "Per-output validation only; the client does not force or configure remote execution providers",
            "params": PARAMS, "tolerance": TOLERANCE, "sizes": SIZES,
            "plan": [] if args.single_smoke_tag or args.smoke_only else plan, "warmup_per_worker": 8,
            "smoke_only": bool(args.single_smoke_tag or args.smoke_only),
            "metric": "verified images / client submit-to-result-download wall time", "reference_is_performance_baseline": False,
            "gpu_evidence_limit": ("Output provider and available capability model hashes are checked; "
                                   "remote kernel traces remain separate operator evidence."),
        }
        if args.auto_workers:
            experiment["expected_providers"] = {}
            experiment["worker_labels"] = [w["name"] + " (pending smoke)" for w in selected]
            experiment["metric"] = "verified images / registered-dataset job submit-to-result-download time; initial registration excluded"
        if args.resume:
            experiment = resumed_experiment
            worker_labels = experiment["worker_labels"]
            experiment.setdefault("continuations", []).append({"segment": stamp, "retained_measured_rounds": len(resumed_rows),
                "prior_stop": prior_progress, "client_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                "script_sha256": {name: digest(Path(__file__).with_name(name)) for name in ("run_gpu_benchmark.py", "common.py", "recovery.py")}})
        write_json(out / "experiment.json", experiment)
        write_json(out / "inputs.json", {name: [{"path": p.relative_to(args.dataset_root.resolve()).as_posix(), "sha256": digest(p)}
                                                for p in files] for name, files in groups.items()})
        rows = list(resumed_rows)
        datasets = {}

        def check_idle():
            for state in ("queued", "running"):
                jobs = client.list_jobs(limit=500 if args.auto_workers else 1, state=state).jobs
                for other in jobs:
                    if not args.auto_workers or job_can_use(
                        client.request("GET", f"/api/v1/jobs/{other.job_id}").json(), selected
                    ):
                        raise ValueError(f"Competing job {other.job_id} may use a selected Worker; no new task submitted")
                if args.auto_workers and len(jobs) >= 500:
                    raise ValueError("Too many active jobs to establish an isolated comparison")

        def run_round(label, condition, files, repeat=0, phase="warmup"):
            folder = out / label
            folder.mkdir(parents=True)
            before = workers()
            write_json(folder / "workers-before.json", before)
            if {w["worker_id"]: config(w) for w in choose(before)} != fingerprints:
                raise ValueError("Worker identity/configuration changed")
            check_idle()
            index = int(condition[-1]) - 1 if condition != "pool" else None
            tag = args.tags[index] if index is not None and not args.auto_workers else None
            allowed_ids = {ids[index]} if index is not None else set(ids)
            dataset_id = None
            if args.auto_workers:
                group_name = "smoke" if phase == "smoke" else "warmup" if phase == "warmup" else f"n{len(files)}"
                if group_name not in datasets:
                    name = "d-infer-" + group_name + "-" + digest(files[0])[:12]
                    datasets[group_name] = registered_dataset(client, files, name, out / "datasets")
                dataset_id = datasets[group_name]
            write_json(out / "progress.json", {"state": phase, "label": label, "measured_completed": len(rows)})
            snapshots, sample_errors = [], []

            def sample():
                try:
                    snapshots.append(client.stats(window=60).model_dump(mode="json"))
                except Exception as error:
                    sample_errors.append({"type": type(error).__name__})

            t0 = time.perf_counter()
            created = client.submit_job([] if dataset_id else files, "infer", PARAMS, name="D-" + run_kind + "-" + label,
                                        required_tag=tag, dataset=dataset_id,
                                        extra_spec={"worker_ids": sorted(allowed_ids)} if args.auto_workers else None)
            t_submitted = time.perf_counter()
            write_json(folder / "created.json", created.model_dump(mode="json"))
            print(label, created.job_id, flush=True)
            next_sample = 0.0
            while True:
                job = client.get_job(created.job_id)
                write_json(folder / "last-job.json", job.model_dump(mode="json"))
                if job.state in ("succeeded", "partial", "failed", "canceled"):
                    break
                if time.perf_counter() - t0 > args.timeout:
                    write_json(folder / "last-job.json", job.model_dump(mode="json"))
                    raise TimeoutError("Job still running; inspect saved Job ID before another run. No automatic resubmission/cancellation.")
                if time.perf_counter() >= next_sample:
                    sample()
                    next_sample = time.perf_counter() + 5
                time.sleep(0.5)
            t_terminal = time.perf_counter()
            write_json(folder / "job-detail.json", job.model_dump(mode="json"))
            archive = client.download_result(created.job_id, folder / "result.zip")
            t_downloaded = time.perf_counter()
            sample()
            write_json(folder / "resource-snapshots.json", snapshots)
            write_json(folder / "resource-sampling-errors.json", sample_errors)
            if job.finished_at is None:
                raise ValueError("Terminal job has no finished_at timestamp")
            with zipfile.ZipFile(archive) as z:
                (folder / "manifest.json").write_bytes(z.read("manifest.json"))
            round_providers = observed_providers(archive, allowed_ids) if args.auto_workers and phase == "smoke" else expected_providers
            if args.resume and phase == "smoke" and round_providers != {wid: expected_providers[wid] for wid in allowed_ids}:
                raise ValueError("Backend changed since original segment; cannot combine measurements")
            verification = verify_archive(archive, files, created.job_id, reference, allowed_ids, round_providers)
            if args.auto_workers and phase == "smoke":
                expected_providers.update(round_providers)
            write_json(folder / "verification.json", verification)
            after = workers()
            write_json(folder / "workers-after.json", after)
            stable = {w["worker_id"]: config(w) for w in choose(after)} == fingerprints
            record = {"label": label, "condition": condition, "repeat": repeat, "phase": phase, "size": len(files),
                      "job_id": created.job_id, "valid": verification["valid"] and stable,
                      "integrity_passed": True, "configuration_stable": stable,
                      "numerical_mismatches": verification["numerical_mismatches"],
                      "returned_per_worker": verification["returned_per_worker"],
                      "verified_images": verification["verified_images"], "per_worker": verification["per_worker"],
                      "per_provider": verification["per_provider"],
                      "both_workers_contributed": verification["both_workers_contributed"],
                      "retries_in_final_attempts": verification["retries_in_final_attempts"],
                      "submit_request_s": t_submitted - t0, "submit_to_terminal_s": t_terminal - t0,
                      "download_s": t_downloaded - t_terminal, "end_to_end_s": t_downloaded - t0,
                      "server_job_s": job.finished_at - job.created_at,
                      "resources": resource_summary(snapshots, job.created_at, job.finished_at, ids),
                      "sampling_error_count": len(sample_errors), "result_zip_sha256": digest(archive)}
            write_json(folder / "round.json", record)
            print(f"  {record['verified_images']}/{len(files)} verified; {record['end_to_end_s']:.3f}s; {record['per_worker']}", flush=True)
            if not stable or (not verification["valid"] and not args.record_numerical_mismatches):
                raise ValueError("Correctness or configuration check failed; all evidence retained, experiment stopped")
            if not verification["valid"]:
                print(f"  DIAGNOSTIC: {record['numerical_mismatches']} outputs outside fixed numerical tolerance; not counted as passed.", flush=True)
            return record

        try:
            if args.resume:
                recover_inflight(client, out, segment, ids, expected_providers, groups, reference)
                for trial in plan[len(rows):]:
                    base = f"r{trial['repeat']}-n{trial['size']}-{trial['condition']}"
                    for name in (base, base + "-warm-single-1", base + "-warm-single-2"):
                        if (out / name).exists():
                            archive_round(out, out / name, segment)
            for condition in CONDITIONS[:len(ids)]:
                label = f"continuations/{stamp}/smoke-{condition}" if args.resume else "smoke-" + condition
                run_round(label, condition, groups["smoke"], phase="smoke")
            if args.auto_workers:
                worker_labels = [w["name"] + " (" + expected_providers[w["worker_id"]].replace("ExecutionProvider", "") + ")" for w in selected]
                experiment["expected_providers"] = expected_providers
                experiment["worker_labels"] = worker_labels
                experiment["resolved_experiment_kind"] = "+".join(expected_providers[wid] for wid in ids)
                for wid, evidence in experiment["remote_model_evidence"].items():
                    evidence["expected_output_provider"] = expected_providers[wid]
                    evidence["capability_engine_may_differ_from_session"] = (
                        evidence.get("engine") == "cuda" and expected_providers[wid] == PROVIDERS["cpu"]
                    )
                write_json(out / "experiment.json", experiment)
                print("Smoke finished; locked backends (numerical agreement recorded separately):", worker_labels, flush=True)
            if args.smoke_only or args.single_smoke_tag:
                smoke_ok = all(read_json(out / ("smoke-" + c) / "round.json")["valid"] for c in CONDITIONS[:len(ids)])
                write_json(out / "progress.json", {"state": "smoke_passed" if smoke_ok else "smoke_numerical_mismatches", "measured_completed": 0})
                print(f"{len(ids)} selected Worker(s) finished the 8-image smoke group; numerical checks all passed: {smoke_ok}.")
                return 0
            for trial in plan[len(rows):]:
                condition, repeat, size = trial["condition"], trial["repeat"], trial["size"]
                label = f"r{repeat}-n{size}-{condition}"
                for warm_condition in (CONDITIONS[:2] if condition == "pool" else [condition]):
                    run_round(label + "-warm-" + warm_condition, warm_condition, groups["warmup"])
                rows.append(run_round(label, condition, groups["benchmark"][:size], repeat, "measured"))
                write_json(out / "measured-rounds.json", rows)
            summary = aggregate(rows, args.record_numerical_mismatches)
            write_json(out / "summary.json", summary)
            save_report(out, rows, summary, worker_labels, experiment["metric"])
            write_json(out / "progress.json", {"state": "complete", "measured_completed": len(rows),
                                               "all_measured_numerical_checks_passed": all(r["valid"] for r in rows)})
            print("Report:", out / "report.md")
        except Exception as error:
            write_json(out / "progress.json", {"state": "stopped", "measured_completed": len(rows),
                                               "error_type": type(error).__name__, "reason": str(error)})
            raise
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("ERROR:", error, file=sys.stderr)
        print("Saved evidence remains. Check the recorded Job IDs before resubmitting.", file=sys.stderr)
        raise SystemExit(1) from error
