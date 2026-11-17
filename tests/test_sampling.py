import unittest

from survey.fieldwork import Fieldwork
from survey.sampling import SampleDesign


class SamplingTests(unittest.TestCase):
    def test_design_probability_flows_to_fieldwork_and_estimate(self):
        design = SampleDesign([{'unit_id': str(number), 'stratum': 'east'} for number in range(4)])
        selected = design.draw('wave1', {'east': 2}, 5)
        fieldwork = Fieldwork.from_design(selected)
        with self.assertRaises(ValueError):
            fieldwork.respond(selected['sample'][0]['unit_id'], {'inclusion_probability': 1})
        for unit in selected['sample']:
            self.assertEqual(unit['inclusion_probability'], 0.5)
            self.assertEqual(unit['base_weight'], 2)
            fieldwork.respond(unit['unit_id'], {'hours': 3})
        report = fieldwork.release({'east': 4}, 'hours')
        self.assertEqual(report['design']['design_id'], 'wave1')
        self.assertEqual(report['summary']['overall']['estimate'], 3)
        self.assertEqual(design.draw('wave1', {'east': 2}, 5), selected)
        with self.assertRaises(ValueError):
            design.draw('wave1', {'east': 3}, 5)

    def test_zero_sample_is_not_claimed_to_cover_a_stratum(self):
        with self.assertRaises(ValueError):
            SampleDesign([{'unit_id': 'a', 'stratum': 'east'}]).draw('wave1', {'east': 0}, 1)
