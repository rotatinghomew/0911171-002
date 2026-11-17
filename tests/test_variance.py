import math
import unittest

from survey.estimate import estimate_field, weighted_mean


def _cluster_rows() -> list[dict]:
    rows = []
    for stratum, clusters in (("east", ("e1", "e2")), ("west", ("w1", "w2"))):
        for cluster in clusters:
            for unit in range(4):
                rows.append({
                    "unit_id": f"{cluster}-{unit}",
                    "stratum": stratum,
                    "cluster": cluster,
                    "responded": True,
                    "weight": 2.0,
                    "y": 1.0 if cluster.startswith("e") else 3.0,
                })
    return rows


class ClusterVarianceTests(unittest.TestCase):
    def test_psu_grouping_dominates_independent_unit_assumption(self):
        rows = _cluster_rows()
        clustered = estimate_field(rows, "y")
        # 朴素口径把 16 人当独立单位；整群口径只有 4 个 PSU，SE 必须更大。
        naive = weighted_mean(rows, "y")
        self.assertIsNotNone(clustered["confidence_interval"])
        self.assertEqual(clustered["degrees_of_freedom"], 2)
        self.assertGreater(clustered["standard_error"], naive["standard_error"])
        self.assertIn("ultimate cluster", clustered["interval_basis"])
        self.assertIn("PSU 字段 cluster", clustered["interval_basis"])

    def test_within_psu_correlation_inflates_variance(self):
        # 同一 PSU 内完全一致、PSU 间差异大：整群方差远大于把人打散的情形。
        clustered_rows = _cluster_rows()
        clustered_se = estimate_field(clustered_rows, "y")["standard_error"]

        shuffled = []
        for index, row in enumerate(clustered_rows):
            rewritten = dict(row)
            rewritten["cluster"] = f"p{index % 4}"  # 人为打散到更多 PSU
            shuffled.append(rewritten)
        spread_se = estimate_field(shuffled, "y")["standard_error"]
        self.assertGreater(clustered_se, spread_se)

    def test_singleton_psu_stratum_has_no_false_precision(self):
        rows = _cluster_rows()
        for row in rows:
            if row["stratum"] == "west" and row["cluster"] == "w2":
                row["cluster"] = "w1"  # west 只剩一个 PSU
        result = estimate_field(rows, "y")
        self.assertTrue(any("仅一个抽中服务点" in note for note in result["limitations"]))
        self.assertEqual(result["degrees_of_freedom"], 1)
        self.assertIsNotNone(result["confidence_interval"])

    def test_all_singleton_strata_cannot_build_interval(self):
        rows = _cluster_rows()
        for row in rows:
            row["cluster"] = f"{row['stratum']}-only"
        result = estimate_field(rows, "y")
        self.assertIsNone(result["confidence_interval"])
        self.assertIsNotNone(result["standard_error"])
        self.assertIn("无法估计抽样方差", result["interval_basis"])

    def test_item_nonresponse_excluded_not_zeroed(self):
        rows = _cluster_rows()
        for row in rows[:4]:
            row["y"] = None
        result = estimate_field(rows, "y")
        self.assertEqual(result["sample_n"], 12)
        self.assertEqual(result["item_nonresponse_count"], 4)
        self.assertTrue(any("单项未答" in note for note in result["limitations"]))
        # 若错误填零，估计会被明显拉低；这里应只基于 12 个有效值。
        self.assertGreater(result["estimate"], 1.5)

    def test_unsupported_domain_returns_none_without_extrapolation(self):
        rows = _cluster_rows()
        result = estimate_field([row for row in rows if row["stratum"] == "east"], "z")
        self.assertIsNone(result["estimate"])
        self.assertIsNone(result["confidence_interval"])
        self.assertTrue(result["limitations"])

    def test_total_and_mean_are_consistent(self):
        rows = _cluster_rows()
        mean = estimate_field(rows, "y", kind="mean")
        total = estimate_field(rows, "y", kind="total")
        total_weight = sum(row["weight"] for row in rows)
        self.assertAlmostEqual(total["estimate"], mean["estimate"] * total_weight, places=9)
        # V(total) = W²·V(mean)，同一套 PSU 残差，FPC 只施加一次。
        self.assertAlmostEqual(
            total["standard_error"] ** 2,
            (mean["standard_error"] * total_weight) ** 2,
            places=9,
        )
        self.assertAlmostEqual(
            total["confidence_interval"][0],
            mean["confidence_interval"][0] * total_weight,
            places=9,
        )

    def test_fpc_reduces_standard_error(self):
        rows = _cluster_rows()
        snapshot = {
            "east": {"cluster_population": 100, "cluster_sampled": 2},
            "west": {"cluster_population": 100, "cluster_sampled": 2},
        }
        without = estimate_field(rows, "y")
        with_fpc = estimate_field(rows, "y", frame_snapshot=snapshot)
        self.assertLess(with_fpc["standard_error"], without["standard_error"])
        self.assertIn("FPC", with_fpc["interval_basis"])

    def test_confidence_level_validated(self):
        with self.assertRaises(ValueError):
            estimate_field(_cluster_rows(), "y", confidence=1.5)


if __name__ == "__main__":
    unittest.main()
