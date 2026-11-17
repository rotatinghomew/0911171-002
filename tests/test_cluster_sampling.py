import unittest

from survey.sampling import SampleDesign


def _frame() -> list[dict]:
    frame = []
    for region, points in (("east", 3), ("west", 2)):
        for point in range(points):
            for unit in range(4):
                frame.append({
                    "unit_id": f"{region}-{point}-{unit}",
                    "stratum": region,
                    "cluster": f"{region}-p{point}",
                })
    return frame


class ClusterSamplingTests(unittest.TestCase):
    def test_two_stage_probabilities_and_frame_snapshot(self):
        design = SampleDesign(_frame())
        drawn = design.draw_clustered(
            "c1", {"east": 2, "west": 1}, {"east": 2, "west": 2}, seed=11
        )
        self.assertEqual(drawn["design_kind"], "stratified_two_stage_cluster")
        east = [row for row in drawn["sample"] if row["stratum"] == "east"]
        west = [row for row in drawn["sample"] if row["stratum"] == "west"]
        self.assertEqual(len(east), 4)
        self.assertEqual(len(west), 2)
        for row in east:
            self.assertAlmostEqual(row["cluster_probability"], 2 / 3)
            self.assertAlmostEqual(row["unit_probability"], 2 / 4)
            self.assertAlmostEqual(row["inclusion_probability"], (2 / 3) * (2 / 4))
            self.assertAlmostEqual(row["base_weight"], 1 / row["inclusion_probability"])
        snapshot = drawn["frame_snapshot"]["west"]
        self.assertEqual(snapshot["cluster_population"], 2)
        self.assertEqual(snapshot["cluster_sampled"], 1)
        self.assertEqual(snapshot["unit_population"], 8)

    def test_cluster_draw_is_idempotent_and_configuration_locked(self):
        design = SampleDesign(_frame())
        first = design.draw_clustered("c1", {"east": 2, "west": 1}, {"east": 2, "west": 2}, seed=11)
        second = design.draw_clustered("c1", {"east": 2, "west": 1}, {"east": 2, "west": 2}, seed=11)
        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            design.draw_clustered("c1", {"east": 3, "west": 1}, {"east": 2, "west": 2}, seed=11)

    def test_quota_limits_and_missing_cluster_field_are_rejected(self):
        design = SampleDesign(_frame())
        with self.assertRaises(ValueError):
            design.draw_clustered("c2", {"east": 9, "west": 1}, {"east": 2, "west": 2}, seed=11)
        with self.assertRaises(ValueError):
            design.draw_clustered("c3", {"east": 2}, {"east": 2, "west": 2}, seed=11)
        flat = SampleDesign([{"unit_id": "a", "stratum": "east"}])
        with self.assertRaises(ValueError):
            flat.draw_clustered("c4", {"east": 1}, {"east": 1}, seed=11)

    def test_same_seed_reproduces_identical_roster(self):
        left = SampleDesign(_frame()).draw_clustered(
            "c1", {"east": 2, "west": 1}, {"east": 2, "west": 2}, seed=99
        )
        right = SampleDesign(_frame()).draw_clustered(
            "c1", {"east": 2, "west": 1}, {"east": 2, "west": 2}, seed=99
        )
        self.assertEqual(
            [row["unit_id"] for row in left["sample"]],
            [row["unit_id"] for row in right["sample"]],
        )


if __name__ == "__main__":
    unittest.main()
