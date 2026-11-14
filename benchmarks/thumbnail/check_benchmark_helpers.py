"""Offline regression checks for benchmark selection, time windows and aggregation."""
import copy
import unittest

from run_thumbnail_benchmark import aggregate, resource_summary, select_workers


def worker(identifier, tag):
    return {"worker_id": identifier, "state": "online", "tags": [tag],
            "workloads": [{"kind": "thumbnail", "available": True}]}


class BenchmarkChecks(unittest.TestCase):
    def test_two_unique_tags_select_two_workers(self):
        selected = select_workers([worker("a", "a-tag"), worker("b", "b-tag")], ["a-tag", "b-tag"])
        self.assertEqual([w["worker_id"] for w in selected], ["a", "b"])

    def test_missing_or_shared_tags_do_not_silently_skip_baselines(self):
        for workers in ([worker("a", "a-tag"), worker("b", "other")],
                        [worker("a", "a-tag"), worker("b", "a-tag")]):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                select_workers(workers, ["a-tag", "b-tag"])

    def test_extra_pool_worker_or_offline_baseline_is_rejected(self):
        base = [worker("a", "a-tag"), worker("b", "b-tag")]
        with self.assertRaises(ValueError):
            select_workers([*base, worker("c", "c-tag")], ["a-tag", "b-tag"])
        base[1]["state"] = "offline"
        with self.assertRaises(ValueError):
            select_workers(base, ["a-tag", "b-tag"])

    def test_resource_window_deduplicates_and_excludes_earlier_jobs(self):
        points = [{"ts": t, "cpu_pct": v, "gpu_pct": None} for t, v in [(9, 99), (10, 20), (12, 40), (16, 99)]]
        snapshots = [{"workers": [{"worker_id": "a", "util_series": points}]}] * 2
        result = resource_summary(snapshots, 10, 15, {"a", "b"})
        self.assertEqual(result["a"]["sample_count"], 2)
        self.assertEqual(result["a"]["mean_cpu_pct"], 30)
        self.assertIsNone(result["a"]["mean_gpu_pct"])
        self.assertEqual(result["b"]["sample_count"], 0)
        self.assertIsNone(result["b"]["mean_cpu_pct"])

    def test_summary_uses_all_three_repetitions(self):
        rounds = [{"condition": name, "valid": True, "verified_throughput_images_s": rate}
                  for name, rates in (("single-1", [1, 2, 3]), ("single-2", [3, 4, 5]), ("pool", [4, 5, 6]))
                  for rate in rates]
        result = aggregate(rounds)
        self.assertAlmostEqual(result["eta"], 5 / 6)
        self.assertAlmostEqual(result["speedup"], 5 / 4)
        self.assertEqual(result["groups"]["pool"]["median_images_s"], 5)
        with self.assertRaises(ValueError):
            aggregate(rounds[:-1])
        failed = copy.deepcopy(rounds)
        failed[0]["valid"] = False
        with self.assertRaises(ValueError):
            aggregate(failed)


if __name__ == "__main__":
    unittest.main()
