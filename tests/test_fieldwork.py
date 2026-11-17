import unittest

from survey.fieldwork import Fieldwork


class FieldworkTests(unittest.TestCase):
    def setUp(self):
        self.fieldwork = Fieldwork([
            {"unit_id": "u1", "stratum": "east", "base_weight": 2},
            {"unit_id": "u2", "stratum": "east", "base_weight": 2},
        ])

    def test_release_uses_answers_without_modifying_sample_design(self):
        self.fieldwork.respond("u1", {"satisfaction": 4})
        result = self.fieldwork.release({"east": 100}, "satisfaction")
        self.assertEqual(result["summary"]["overall"]["estimate"], 4)
        self.assertEqual(result["summary"]["response_count"], 1)
        self.assertEqual(result["summary"]["unit_count"], 2)
        self.assertEqual(self.fieldwork.sample["u1"]["base_weight"], 2)

    def test_answers_cannot_overwrite_design_or_add_an_unknown_unit(self):
        with self.assertRaises(ValueError):
            self.fieldwork.respond("u1", {"stratum": "west"})
        with self.assertRaises(ValueError):
            self.fieldwork.respond("u3", {"satisfaction": 4})
