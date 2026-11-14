"""Offline tests of correctness gates and the experimental design."""
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common import (
    MODEL_SHA,
    PARAMS,
    PROVIDERS,
    aggregate,
    compatible_workers,
    detections_match,
    job_can_use,
    model_evidence,
    observed_providers,
    resource_summary,
    select_workers,
    validate_payload,
    verify_archive,
)
from recovery import ReadRetryClient, archive_round, remaining_plan
from run_gpu_benchmark import check_previous_job, make_plan, registered_dataset


def worker(wid, tag, infer=True):
    return {"worker_id": wid, "state": "online", "tags": [tag],
            "workloads": [{"kind": "infer" if infer else "thumbnail", "available": True}]}


class Checks(unittest.TestCase):
    def test_resume_requires_exact_prefix_and_only_moves_inside_run(self):
        plan = make_plan()
        self.assertEqual(remaining_plan(plan, plan[:12]), plan[12:])
        for bad in (plan, plan[1:12], list(reversed(plan[:12]))):
            with self.assertRaises(ValueError):
                remaining_plan(plan, bad)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            old = root / "interrupted-round"
            old.mkdir(parents=True)
            (old / "evidence.json").write_text("original")
            segment = root / "continuations" / "segment"
            archive_round(root, old, segment)
            self.assertEqual((segment / "excluded/interrupted-round/evidence.json").read_text(), "original")
            outside = Path(tmp) / "outside"
            outside.mkdir()
            with self.assertRaises(ValueError):
                archive_round(root, outside, segment)
            self.assertTrue(outside.exists())

    def test_read_retry_never_retries_post_and_retains_only_safe_metadata(self):
        import httpx
        with ReadRetryClient("https://example.invalid", "test-token") as client:
            client._client.close()
            attempts = []
            def handler(request):
                attempts.append(request.method)
                if len(attempts) < 3 or request.method == "POST":
                    raise httpx.RemoteProtocolError("disconnected")
                return httpx.Response(200, json={"ok": True})
            client._client = httpx.Client(base_url="https://example.invalid", transport=httpx.MockTransport(handler))
            with patch("recovery.time.sleep"):
                self.assertTrue(client.request("GET", "/health").json()["ok"])
                with self.assertRaises(httpx.RemoteProtocolError):
                    client.request("POST", "/jobs")
            self.assertEqual(attempts, ["GET", "GET", "GET", "POST"])
            self.assertNotIn("test-token", json.dumps(client.retries))

    def test_auto_selection_and_routing_exclude_unrelated_blocked_worker(self):
        a, b, legacy = worker("gpu", "gpu"), worker("cpu", "cpu"), worker("old", "old")
        for w, engine in ((a, "cuda"), (b, "cpu")):
            w["workloads"][0].update(engine=engine, models=[{"name": "yolo11n", "sha256": MODEL_SHA}])
        selected = compatible_workers([legacy, b, a])
        self.assertEqual([w["worker_id"] for w in selected], ["gpu", "cpu"])
        job = {"workload": "infer", "required_tag": "old", "required_model_sha": MODEL_SHA}
        self.assertFalse(job_can_use(job, selected))
        job["required_tag"] = "gpu"
        self.assertTrue(job_can_use(job, selected))
        job["required_model_sha"] = "wrong"
        self.assertFalse(job_can_use(job, selected))
        job.update(required_tag=None, required_model_sha=MODEL_SHA, worker_ids=["old"])
        self.assertFalse(job_can_use(job, selected))
        job["worker_ids"] = None
        self.assertTrue(job_can_use(job, selected))

    def test_dataset_reuse_checks_order_and_does_not_retry_mutation(self):
        from cef_protocol.client import CefError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [root / "a.jpg", root / "b.jpg"]
            for i, p in enumerate(files):
                p.write_bytes(bytes([i]))
            entries = [{"name": p.name, "size": 1, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
            client = Mock()
            client.get_dataset.return_value = {"id": "existing", "files": entries}
            self.assertEqual(registered_dataset(client, files, "fixed", root), "existing")
            client.create_dataset.assert_not_called()
            client.get_dataset.return_value = {"id": "existing", "files": list(reversed(entries))}
            with self.assertRaises(ValueError):
                registered_dataset(client, files, "fixed", root)
            client.get_dataset.side_effect = CefError(404, "not_found", "missing")
            client.create_dataset.side_effect = TimeoutError("ambiguous creation")
            with self.assertRaises(TimeoutError):
                registered_dataset(client, files, "new", root)
            self.assertEqual(client.create_dataset.call_count, 1)

    def test_previous_job_is_checked_without_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock()
            for state in ("queued", "running", "succeeded", "failed"):
                job = SimpleNamespace(job_id="old", state=state, succeeded=8 if state == "succeeded" else 0, total=8)
                job.model_dump = lambda captured_state=state, **kwargs: {"job_id": "old", "state": captured_state}
                client.get_job.return_value = job
                if state in ("queued", "running"):
                    with self.assertRaises(ValueError):
                        check_previous_job(client, "old", Path(tmp))
                else:
                    self.assertFalse(check_previous_job(client, "old", Path(tmp))["included_in_new_measurement"])
            client.submit_job.assert_not_called()
            client.cancel_job.assert_not_called()

    def test_reported_model_mismatch_and_cpu_engine_reject(self):
        w = {**worker("a", "a"), "name": "GPU A"}
        self.assertFalse(model_evidence([w])["a"]["matches_reference_hash"])
        w["workloads"][0].update(engine="cuda", models=[{"name": "yolo11n", "sha256": MODEL_SHA}])
        self.assertTrue(model_evidence([w])["a"]["matches_reference_hash"])
        w["workloads"][0]["models"][0]["sha256"] = "wrong"
        with self.assertRaises(ValueError):
            model_evidence([w])
        w["workloads"][0].update(engine="cpu", models=[])
        with self.assertRaises(ValueError):
            model_evidence([w])

    def test_missing_infer_extra_worker_and_shared_tag_reject(self):
        good = [worker("a", "a"), worker("b", "b")]
        self.assertEqual(len(select_workers(good, ["a", "b"])), 2)
        for rows in ([good[0], worker("b", "b", False)], [*good, worker("c", "c")], [good[0], worker("b", "a")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                select_workers(rows, ["a", "b"])

    def test_cpu_expectation_keeps_capability_mismatch_visible_and_checks_hash(self):
        w = {**worker("a", "a"), "name": "CPU Worker"}
        w["workloads"][0].update(engine="cuda", models=[{"name": "yolo11n", "sha256": MODEL_SHA}])
        evidence = model_evidence([w], {"a": PROVIDERS["cpu"]})["a"]
        self.assertTrue(evidence["capability_engine_may_differ_from_session"])
        self.assertEqual(evidence["expected_output_provider"], PROVIDERS["cpu"])
        w["workloads"][0]["models"][0]["sha256"] = "wrong"
        with self.assertRaises(ValueError):
            model_evidence([w], {"a": PROVIDERS["cpu"]})

    def test_mixed_pool_requires_correct_backend_for_each_worker(self):
        expected = {"a": PROVIDERS["cpu"], "b": PROVIDERS["cuda"]}
        payloads = [{"model": "yolo11n", "image": name + ".jpg", "size": [10, 10],
                     "detections": [], "provider": provider} for name, provider in expected.items()]
        blobs = [json.dumps(p).encode() for p in payloads]
        entries = [{"task_id": wid, "idx": i, "name": wid + ".jpg", "worker_id": wid, "attempt": 1,
                    "error": None, "output_files": [wid + ".json"], "output_sha256": hashlib.sha256(blobs[i]).hexdigest()}
                   for i, wid in enumerate(expected)]
        manifest = {"job_id": "mixed", "workload": "infer", "state": "succeeded", "total": 2, "succeeded": 2,
                    "failed": 0, "params": PARAMS, "entries": entries}
        reference = {"images": {p["image"]: {"output": p} for p in payloads}}
        files = [Path(p["image"]) for p in payloads]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                for i, wid in enumerate(expected):
                    archive.writestr(f"outputs/{i:06d}_{wid}.json", blobs[i])
            result = verify_archive(path, files, "mixed", reference, set(expected), expected)
            self.assertEqual(observed_providers(path, set(expected)), expected)
            self.assertEqual(result["per_provider"], {PROVIDERS["cpu"]: 1, PROVIDERS["cuda"]: 1})
            self.assertTrue(result["both_workers_contributed"])
            for mapping in (None, {"a": PROVIDERS["cuda"], "b": PROVIDERS["cpu"]}, {"a": PROVIDERS["cpu"]}):
                with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                    verify_archive(path, files, "mixed", reference, set(expected), mapping)
            with self.assertRaises(ValueError):
                observed_providers(path, {"a"})

    def test_matching_is_order_independent_and_not_greedy(self):
        def det(x):
            return {"class_id": 0, "conf": 0.9, "xyxy": [x, 0, x + 10, 10]}
        self.assertTrue(detections_match([det(0.2), det(0)], [det(0.1), det(0.4)]))
        self.assertFalse(detections_match([det(0)], [det(1)]))
        self.assertFalse(detections_match([det(0)], []))
        wrong = det(0)
        wrong["class_id"] = 1
        self.assertFalse(detections_match([det(0)], [wrong]))
        self.assertTrue(detections_match([], []))

    def test_cuda_and_finite_boxes_are_required(self):
        payload = {"model": "yolo11n", "image": "a.jpg", "size": [100, 100], "detections": [], "provider": "CPUExecutionProvider"}
        with self.assertRaises(ValueError):
            validate_payload(payload, "a.jpg", [100, 100], True)
        payload["provider"] = "CUDAExecutionProvider"
        validate_payload(payload, "a.jpg", [100, 100], True)
        payload["detections"] = [{"class_id": 0, "conf": float("nan"), "xyxy": [0, 0, 10, 10]}]
        with self.assertRaises(ValueError):
            validate_payload(payload, "a.jpg", [100, 100], True)

    def test_plan_rotates_conditions_at_every_size(self):
        plan = make_plan()
        self.assertEqual(len(plan), 27)
        self.assertEqual(sum(r["size"] for r in plan), 1350)
        for size in (8, 32, 110):
            orders = [tuple(r["condition"] for r in plan if r["size"] == size and r["repeat"] == rep) for rep in (1, 2, 3)]
            self.assertEqual(len(set(orders)), 3)

    def test_summary_cannot_discard_failed_or_missing_trials(self):
        rows = [{**r, "verified_images": r["size"], "end_to_end_s": r["size"] / {"single-1": 1, "single-2": 2, "pool": 2.5}[r["condition"]],
                 "valid": True, "both_workers_contributed": r["size"] != 8} for r in make_plan()]
        result = aggregate(rows)
        self.assertAlmostEqual(result["110"]["speedup"], 1.25)
        self.assertFalse(result["8"]["both_workers_in_every_pool_trial"])
        with self.assertRaises(ValueError):
            aggregate(rows[:-1])
        rows[0]["valid"] = False
        with self.assertRaises(ValueError):
            aggregate(rows)

    def test_resources_use_job_window_and_keep_missing_gpu_null(self):
        points = [{"ts": t, "cpu_pct": v, "gpu_pct": None} for t, v in [(0, 99), (1, 20), (2, 40), (3, 99)]]
        snapshots = [{"workers": [{"worker_id": "a", "util_series": points}]}] * 2
        result = resource_summary(snapshots, 1, 2, ["a", "b"])
        self.assertEqual(result["a"]["sample_count"], 2)
        self.assertEqual(result["a"]["mean_cpu_pct"], 30)
        self.assertIsNone(result["a"]["mean_gpu_pct"])
        self.assertEqual(result["b"]["sample_count"], 0)

    def test_diagnostic_counts_only_matching_outputs_and_keeps_failures_visible(self):
        rows = [{**r, "verified_images": r["size"] - 1, "numerical_mismatches": 1,
                 "end_to_end_s": r["size"], "valid": False, "integrity_passed": True,
                 "configuration_stable": True, "both_workers_contributed": False} for r in make_plan()]
        with self.assertRaises(ValueError):
            aggregate(rows)
        result = aggregate(rows, True)
        self.assertEqual(result["8"]["conditions"]["single-1"]["median_images_s"], 7 / 8)
        self.assertEqual(result["8"]["conditions"]["single-1"]["verified_images"], 21)
        self.assertFalse(result["8"]["conditions"]["single-1"]["all_numerical_checks_passed"])
        for field, value in (("integrity_passed", False), ("configuration_stable", False), ("valid", True), ("numerical_mismatches", 0)):
            bad = copy.deepcopy(rows)
            bad[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                aggregate(bad, True)
        with self.assertRaises(ValueError):
            aggregate(rows[:-1], True)
        for row in rows:
            row.update(verified_images=0, numerical_mismatches=row["size"])
        empty = aggregate(rows, True)
        self.assertIsNone(empty["8"]["speedup"])
        self.assertEqual(empty["8"]["conditions"]["pool"]["median_images_s"], 0)

    def test_archive_gates_hash_identity_placement_inventory_and_reference(self):
        payload = {"model": "yolo11n", "image": "a.jpg", "size": [10, 10], "detections": [], "provider": "CUDAExecutionProvider"}
        raw = json.dumps(payload).encode()
        base = {"job_id": "job", "workload": "infer", "state": "succeeded", "total": 1, "succeeded": 1, "failed": 0,
                "params": PARAMS, "entries": [{"task_id": "task", "idx": 0, "name": "a.jpg", "worker_id": "a", "attempt": 1,
                                              "error": None, "output_files": ["a.json"], "output_sha256": hashlib.sha256(raw).hexdigest()}]}
        ref = {"images": {"a.jpg": {"output": payload}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.zip"

            def archive(manifest, extra=False):
                with zipfile.ZipFile(path, "w") as z:
                    z.writestr("manifest.json", json.dumps(manifest))
                    z.writestr("outputs/000000_a.json", raw)
                    if extra:
                        z.writestr("outputs/unexpected.json", raw)

            archive(base)
            self.assertTrue(verify_archive(path, [Path("a.jpg")], "job", ref, {"a"})["valid"])
            with self.assertRaises(ValueError):
                verify_archive(path, [Path("a.jpg")], "job", ref, {"b"})
            for key, value in (("output_sha256", "wrong"), ("name", "other.jpg")):
                bad = copy.deepcopy(base)
                bad["entries"][0][key] = value
                archive(bad)
                with self.assertRaises(ValueError):
                    verify_archive(path, [Path("a.jpg")], "job", ref, {"a"})
            archive(base, extra=True)
            with self.assertRaises(ValueError):
                verify_archive(path, [Path("a.jpg")], "job", ref, {"a"})


if __name__ == "__main__":
    unittest.main()
