import unittest

from survey.batches import INITIAL_BATCH, FollowUpError, draw_followup, phase_base_weight
from survey.fieldwork import Fieldwork
from survey.sampling import SampleDesign


def _rows() -> list[dict]:
    frame = [
        {"unit_id": f"e{i}", "stratum": "east", "cluster": f"e-p{i % 2}"} for i in range(6)
    ] + [
        {"unit_id": f"w{i}", "stratum": "west", "cluster": f"w-p{i % 2}"} for i in range(4)
    ]
    design = SampleDesign(frame)
    drawn = design.draw_clustered(
        "d1", {"east": 2, "west": 2}, {"east": 3, "west": 2}, seed=3
    )
    return drawn["sample"]


class FollowUpBatchTests(unittest.TestCase):
    def setUp(self):
        self.rows = _rows()
        self.fieldwork = Fieldwork(self.rows)
        # 回收一部分，留下未应答池。
        for row in self.rows[:4]:
            self.fieldwork.respond(row["unit_id"], {"y": 1.0})

    def test_stratified_followup_records_phase_probability(self):
        plan = self.fieldwork.draw_followup("fu1", seed=5, quotas={"east": 1, "west": 1})
        self.assertEqual(plan["mechanism"]["kind"], "stratified_srs")
        self.assertEqual(len(plan["roster"]), 2)
        east_roster = [row for row in plan["roster"] if row["stratum"] == "east"]
        # east 初始抽 6 人、应答 4 人，未应答池 2 人，抽 1 人 => 0.5
        self.assertAlmostEqual(east_roster[0]["phase_probability"], 0.5)
        for row in plan["roster"]:
            self.assertIn(row["unit_id"], {item["unit_id"] for item in self.rows})

    def test_poisson_followup_needs_probability_for_every_nonrespondent(self):
        missing = [row["unit_id"] for row in self.fieldwork.response_rows() if not row["responded"]]
        probs = {unit_id: 0.7 for unit_id in missing}
        plan = self.fieldwork.draw_followup("fu2", seed=6, probs=probs)
        self.assertEqual(plan["mechanism"]["kind"], "poisson_bernoulli")
        self.assertTrue(plan["roster"])
        for row in plan["roster"]:
            self.assertAlmostEqual(row["phase_probability"], 0.7)
        with self.assertRaises(FollowUpError):
            self.fieldwork.draw_followup("fu3", seed=7, probs={missing[0]: 0.5})

    def test_mechanisms_are_mutually_exclusive_and_batch_id_protected(self):
        with self.assertRaises(FollowUpError):
            self.fieldwork.draw_followup("fu4", seed=8)
        with self.assertRaises(FollowUpError):
            self.fieldwork.draw_followup(
                "fu5", seed=8, quotas={"east": 1, "west": 1},
                probs={row["unit_id"]: 0.5 for row in self.rows},
            )
        with self.assertRaises(FollowUpError):
            self.fieldwork.draw_followup(INITIAL_BATCH, seed=8, quotas={"east": 1, "west": 1})

    def test_followup_response_must_match_roster_and_carries_batch_weight(self):
        plan = self.fieldwork.draw_followup("fu6", seed=9, quotas={"east": 1, "west": 1})
        outsider = next(
            row["unit_id"] for row in self.fieldwork.response_rows()
            if not row["responded"] and row["unit_id"] not in {r["unit_id"] for r in plan["roster"]}
        )
        with self.assertRaises(FollowUpError):
            self.fieldwork.respond(outsider, {"y": 2.0}, batch_id="fu6")
        target = plan["roster"][0]
        self.fieldwork.respond(target["unit_id"], {"y": 2.0}, batch_id="fu6")
        row = next(item for item in self.fieldwork.response_rows() if item["unit_id"] == target["unit_id"])
        self.assertEqual(row["response_batch"], "fu6")
        self.assertAlmostEqual(
            phase_base_weight(row),
            row["base_weight"] / target["phase_probability"],
        )
        # 初始批次权重不被二阶段概率调整。
        initial_row = next(item for item in self.fieldwork.response_rows() if item["response_batch"] == INITIAL_BATCH)
        self.assertEqual(phase_base_weight(initial_row), initial_row["base_weight"])

    def test_response_cannot_be_overwritten_and_unknown_batch_rejected(self):
        unit = self.rows[0]["unit_id"]
        with self.assertRaises(Exception):
            self.fieldwork.respond(unit, {"y": 9.0})
        with self.assertRaises(FollowUpError):
            self.fieldwork.respond(self.rows[-1]["unit_id"], {"y": 1.0}, batch_id="nope")

    def test_no_nonrespondents_means_no_followup(self):
        for row in self.rows:
            if not any(item["unit_id"] == row["unit_id"] for item in self.fieldwork.response_rows() if item["responded"]):
                self.fieldwork.respond(row["unit_id"], {"y": 1.0})
        with self.assertRaises(FollowUpError):
            self.fieldwork.draw_followup("fu7", seed=10, quotas={"east": 1, "west": 1})


if __name__ == "__main__":
    unittest.main()
