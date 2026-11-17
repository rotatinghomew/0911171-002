import unittest

from survey.estimate import weighted_mean
from survey.weights import calibrate_weights


class SurveyTests(unittest.TestCase):
    def test_nonresponse_is_not_imputed_as_zero(self):
        rows = [{"stratum": "a", "base_weight": 1, "responded": True, "hours": 5}, {"stratum": "a", "base_weight": 1, "responded": False, "hours": None}]
        adjusted = calibrate_weights(rows, {"a": 4})
        self.assertEqual(weighted_mean(adjusted, "hours")["estimate"], 5)

    def test_empty_answer_group_has_no_estimate(self):
        self.assertIsNone(weighted_mean([], "x")["estimate"])


if __name__ == "__main__":
    unittest.main()
