import unittest

from survey.batches import INITIAL_BATCH
from survey.benchmark import Benchmark, UnsupportedCell
from survey.weights import phase_base_weights, rake_weights, weight_diagnostics


def _rows() -> list[dict]:
    # 回收偏向城区、缺少老年类别的年龄维度支持。
    data = [
        ("u1", "城区", "青年", 1.0),
        ("u2", "城区", "青年", 2.0),
        ("u3", "城区", "中年", 3.0),
        ("u4", "郊县", "中年", 4.0),
        ("u5", "郊县", "青年", 5.0),
    ]
    return [
        {
            "unit_id": unit_id,
            "stratum": region,
            "cluster": f"{region}-p1",
            "age_group": age,
            "base_weight": 2.0,
            "responded": True,
            "response_batch": INITIAL_BATCH,
            "y": value,
        }
        for unit_id, region, age, value in data
    ]


class RakingTests(unittest.TestCase):
    def setUp(self):
        self.benchmark = Benchmark(
            "b1",
            {"地区": {"城区": 12.0, "郊县": 8.0}, "年龄": {"青年": 11.0, "中年": 9.0}},
        )
        self.fields = {"地区": "stratum", "年龄": "age_group"}

    def test_raking_hits_every_margin_and_keeps_total(self):
        result = rake_weights(phase_base_weights(_rows()), self.benchmark, self.fields)
        rows = result["rows"]
        for dimension, field in (("地区", "stratum"), ("年龄", "age_group")):
            for category, target in self.benchmark.margins[dimension].items():
                total = sum(r["weight"] for r in rows if r.get(field) == category)
                self.assertAlmostEqual(total, target, places=6)
        self.assertAlmostEqual(
            sum(r["weight"] for r in rows), self.benchmark.population_total, places=6
        )
        self.assertLessEqual(result["diagnostics"]["max_relative_gap"], 1e-6)

    def test_nonrespondents_keep_zero_weight_and_stay_in_rows(self):
        rows = _rows()
        rows[0]["responded"] = False
        result = rake_weights(phase_base_weights(rows), self.benchmark, self.fields)
        self.assertEqual(result["rows"][0]["weight"], 0.0)
        self.assertEqual(len(result["rows"]), len(rows))

    def test_unsupported_cell_raises_instead_of_smoothing(self):
        benchmark = Benchmark(
            "b2",
            {"地区": {"城区": 10.0, "郊县": 10.0}, "年龄": {"青年": 8.0, "中年": 6.0, "老年": 6.0}},
        )
        with self.assertRaises(UnsupportedCell):
            rake_weights(phase_base_weights(_rows()), benchmark, self.fields)

    def test_structural_zero_joint_cell_is_reported_not_cycled(self):
        # 单维边际都有样本支持，但联合结构使边际数学上不可行：医疗服务
        # 只在城区单位中出现，而医疗边际总量（14）超过城区地区总量（10）。
        # IPF 必然进入极限环，必须显式报错并指出空联合单元格，禁止空转。
        rows = []
        for unit_id, region, age, service, weight in (
            ("u1", "城区", "青年", "政务", 2.0),
            ("u2", "城区", "中年", "医疗", 2.0),
            ("u3", "郊县", "青年", "政务", 2.0),
            ("u4", "郊县", "中年", "政务", 2.0),
        ):
            rows.append({
                "unit_id": unit_id, "stratum": region, "cluster": f"{region}-p1",
                "age_group": age, "service": service, "base_weight": weight,
                "responded": True, "response_batch": INITIAL_BATCH, "y": 1.0,
            })
        benchmark = Benchmark(
            "b3",
            {
                "地区": {"城区": 10.0, "郊县": 10.0},
                "年龄": {"青年": 10.0, "中年": 10.0},
                "服务": {"政务": 6.0, "医疗": 14.0},
            },
        )
        fields = {"地区": "stratum", "年龄": "age_group", "服务": "service"}
        with self.assertRaisesRegex(Exception, "结构零单元格"):
            rake_weights(phase_base_weights(rows), benchmark, fields, max_iterations=500)

    def test_extreme_weights_are_flagged_never_trimmed(self):
        rows = _rows()
        rows[-1]["base_weight"] = 200.0  # 制造极端权重
        result = rake_weights(phase_base_weights(rows), self.benchmark, self.fields, extreme_ratio=5.0)
        flagged = result["diagnostics"]["extreme_weights"]
        self.assertTrue(any(item["unit_id"] == "u5" for item in flagged))
        # 标记后权重仍然保留，未被修剪或缩尾。
        u5 = next(row for row in result["rows"] if row["unit_id"] == "u5")
        self.assertGreaterEqual(u5["weight"], result["diagnostics"]["min_weight"] * 5.0 - 1e-9)

    def test_diagnostics_report_concentration_and_effective_n(self):
        diagnostics = weight_diagnostics([
            {"unit_id": "1", "stratum": "a", "responded": True, "weight": 1.0},
            {"unit_id": "2", "stratum": "a", "responded": True, "weight": 3.0},
        ])
        self.assertAlmostEqual(diagnostics["weight_ratio"], 3.0)
        self.assertAlmostEqual(diagnostics["effective_sample_size"], 16 / 10)
        self.assertAlmostEqual(diagnostics["largest_weight_share"], 0.75)

    def test_followup_phase_weight_enters_raking_base(self):
        rows = _rows()
        rows[0]["response_batch"] = "fu1"
        rows[0]["phase_probability"] = 0.5
        phased = phase_base_weights(rows)
        self.assertAlmostEqual(phased[0]["phase_weight"], 4.0)
        result = rake_weights(phased, self.benchmark, self.fields)
        self.assertTrue(all(row["weight"] >= 0 for row in result["rows"]))


if __name__ == "__main__":
    unittest.main()
