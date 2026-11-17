import unittest

from survey.batches import INITIAL_BATCH
from survey.benchmark import Benchmark, ContradictoryMargins, UnsupportedCell
from survey.fieldwork import Fieldwork, fingerprint
from survey.sampling import SampleDesign


def _frame() -> list[dict]:
    frame = []
    for region in ("城区", "郊县"):
        for point in range(4):
            for unit in range(6):
                frame.append({
                    "unit_id": f"{region}-{point}-{unit}",
                    "stratum": region,
                    "cluster": f"{region}-p{point}",
                    "age_group": "青年" if unit % 2 == 0 else "中年",
                })
    return frame


class ReleaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        design = SampleDesign(_frame()).draw_clustered(
            "d1", {"城区": 3, "郊县": 2}, {"城区": 3, "郊县": 3}, seed=21
        )
        self.design = design
        self.fieldwork = Fieldwork.from_design(design)
        for index, row in enumerate(design["sample"]):
            # 郊县低应答制造城区偏向，但保证每个 地区×年龄 联合单元格都有支持。
            respond = row["stratum"] == "城区" or (index % 3 != 0)
            if respond:
                hours = (1.0 if row["age_group"] == "青年" else 3.0) + (index % 2) * 0.4
                self.fieldwork.respond(row["unit_id"], {"hours": hours})
        self.benchmark = Benchmark(
            "b1",
            {"地区": {"城区": 60.0, "郊县": 40.0}, "年龄": {"青年": 55.0, "中年": 45.0}},
        )
        self.fields = {"地区": "stratum", "年龄": "age_group"}

    def test_release_keeps_basis_batches_settings_and_interval(self):
        record = self.fieldwork.publish(
            "r1", self.benchmark, ["hours"], self.fields, domain_field="stratum"
        )
        self.assertEqual(record["release_kind"], "calibrated_population_estimate")
        self.assertEqual(record["design"]["design_id"], "d1")
        self.assertEqual(record["benchmark"]["name"], "b1")
        self.assertEqual(
            record["calibration_settings"]["method"],
            "iterative_proportional_fitting_raking",
        )
        block = record["estimates"]["after_calibration"]["hours"]
        self.assertIn("城区", block["domains"])
        self.assertIn("郊县", block["domains"])
        overall = block["overall"]["mean"]
        self.assertIsNotNone(overall["confidence_interval"])
        self.assertIn("t 分布", overall["interval_basis"])
        self.assertTrue(record["fingerprint"])
        self.assertTrue(record["batches"]["unit_nonresponse_count"] > 0)

    def test_before_and_after_calibration_are_both_reported(self):
        record = self.fieldwork.publish("r2", self.benchmark, ["hours"], self.fields)
        before = record["estimates"]["before_calibration"]["hours"]["overall"]["mean"]
        after = record["estimates"]["after_calibration"]["hours"]["overall"]["mean"]
        self.assertIsNotNone(before["estimate"])
        self.assertIsNotNone(after["estimate"])
        # 校准前后边际对齐不同，估计应当发生变化。
        self.assertNotAlmostEqual(before["estimate"], after["estimate"])

    def test_release_ids_are_immutable_but_new_benchmark_gets_new_release(self):
        first = self.fieldwork.publish("r3", self.benchmark, ["hours"], self.fields)
        with self.assertRaises(Exception):
            self.fieldwork.publish("r3", self.benchmark, ["hours"], self.fields)
        # 旧记录不被后续动作改动。
        alternative = Benchmark(
            "b2", {"地区": {"城区": 50.0, "郊县": 50.0}, "年龄": {"青年": 50.0, "中年": 50.0}}
        )
        second = self.fieldwork.publish("r4", alternative, ["hours"], self.fields)
        self.assertEqual(self.fieldwork.get_release("r3")["fingerprint"], first["fingerprint"])
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertTrue(self.fieldwork.republish_check("r3", first))

    def test_followup_batches_flow_into_release_and_remain_distinct(self):
        pool_sizes = {
            region: sum(
                1 for row in self.fieldwork.response_rows()
                if not row["responded"] and row["stratum"] == region
            )
            for region in ("城区", "郊县")
        }
        # 每层只抽部分未应答者，保证条件入选概率严格小于 1。
        plan = self.fieldwork.draw_followup(
            "fu1",
            seed=3,
            quotas={
                region: max(1, pool_sizes[region] - 1)
                for region in pool_sizes
                if pool_sizes[region] > 0
            },
        )
        for row in plan["roster"]:
            self.fieldwork.respond(row["unit_id"], {"hours": 5.0}, batch_id="fu1")
        record = self.fieldwork.publish("r5", self.benchmark, ["hours"], self.fields)
        batches = record["batches"]["respondents_by_batch"]
        self.assertIn(INITIAL_BATCH, batches)
        self.assertIn("fu1", batches)
        self.assertEqual(
            record["batches"]["followup_plans"]["fu1"]["mechanism"]["kind"],
            "stratified_srs",
        )
        weighted = {row["unit_id"]: row for row in record["weighted_rows"]}
        followed = next(
            row for row in record["weighted_rows"] if row["response_batch"] == "fu1"
        )
        self.assertLess(followed["phase_probability"], 1.0)
        self.assertGreater(followed["weight"], 0)
        self.assertEqual(weighted[followed["unit_id"]]["responded"], True)

    def test_contradiction_and_unsupported_cell_fail_hard(self):
        with self.assertRaises(ContradictoryMargins):
            Benchmark("bad", {"地区": {"城区": 60.0, "郊县": 40.0}, "年龄": {"青年": 90.0, "中年": 30.0}})
        narrow = Benchmark("narrow", {"地区": {"城区": 50.0, "郊县": 50.0}, "年龄": {"青年": 50.0, "中年": 30.0, "老年": 20.0}})
        with self.assertRaises(UnsupportedCell):
            self.fieldwork.publish("r6", narrow, ["hours"], self.fields)
        self.assertNotIn("r6", self.fieldwork.releases)

    def test_sample_only_fallback_carries_limitations_and_no_benchmark(self):
        record = self.fieldwork.publish_sample_only(
            "r7", ["hours"], reason="年龄边际缺乏样本支持", domain_field="stratum"
        )
        self.assertEqual(record["release_kind"], "sample_description_only")
        self.assertIsNone(record["benchmark"])
        self.assertTrue(any("不做总体外推" in note for note in record["limitations"]))
        estimate = record["estimates"]["sample_description"]["hours"]["overall"]["mean"]
        self.assertIsNotNone(estimate["estimate"])

    def test_fingerprint_stable_under_deepcopy_and_sensitive_to_change(self):
        record = self.fieldwork.publish("r8", self.benchmark, ["hours"], self.fields)
        payload = {key: value for key, value in record.items() if key != "fingerprint"}
        self.assertEqual(record["fingerprint"], fingerprint(payload))
        tampered = dict(payload)
        tampered["note"] = "被篡改"
        self.assertNotEqual(record["fingerprint"], fingerprint(tampered))

    def test_legacy_release_still_available_and_recorded(self):
        result = self.fieldwork.release({"城区": 60.0, "郊县": 40.0}, "hours")
        self.assertEqual(result["release_kind"], "legacy_stratum_poststratification")
        self.assertIn("旧口径", result["limitations"][0])


if __name__ == "__main__":
    unittest.main()
