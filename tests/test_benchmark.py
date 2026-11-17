import unittest

from survey.benchmark import Benchmark, ContradictoryMargins, UnsupportedCell


class BenchmarkTests(unittest.TestCase):
    def test_consistent_margins_accepted_and_recorded(self):
        benchmark = Benchmark(
            "b1",
            {"地区": {"城区": 6, "郊县": 4}, "年龄": {"青年": 5, "老年": 5}},
            source="测试登记",
            as_of="2026-11-17",
        )
        self.assertEqual(benchmark.population_total, 10)
        record = benchmark.to_record()
        self.assertEqual(record["margins"]["地区"], {"城区": 6.0, "郊县": 4.0})
        self.assertEqual(record["source"], "测试登记")

    def test_contradictory_totals_are_rejected(self):
        with self.assertRaises(ContradictoryMargins):
            Benchmark("b2", {"地区": {"城区": 6, "郊县": 4}, "年龄": {"青年": 9, "老年": 3}})

    def test_nonpositive_or_nonfinite_margins_rejected(self):
        with self.assertRaises(ValueError):
            Benchmark("b3", {"地区": {"城区": 0, "郊县": 10}})
        with self.assertRaises(ValueError):
            Benchmark("b4", {"地区": {"城区": float("nan"), "郊县": 10}})

    def test_support_check_flags_missing_categories_and_values(self):
        benchmark = Benchmark(
            "b5",
            {"地区": {"城区": 5, "郊县": 5}, "年龄": {"青年": 4, "中年": 3, "老年": 3}},
        )
        rows = [
            {"unit_id": "1", "responded": True, "stratum": "城区", "age": "青年"},
            {"unit_id": "2", "responded": True, "stratum": "郊县", "age": "老年"},
        ]
        with self.assertRaises(UnsupportedCell):
            benchmark.check_support(rows, {"地区": "stratum", "年龄": "age"})

        supported = Benchmark("b6", {"地区": {"城区": 5, "郊县": 5}})
        with self.assertRaises(UnsupportedCell):
            supported.check_support(
                [{"unit_id": "1", "responded": True, "stratum": None}],
                {"地区": "stratum"},
            )

    def test_dimension_field_mapping_required(self):
        benchmark = Benchmark("b7", {"地区": {"城区": 5, "郊县": 5}})
        with self.assertRaises(ValueError):
            benchmark.check_support(
                [{"unit_id": "1", "responded": True, "stratum": "城区"}], {}
            )


if __name__ == "__main__":
    unittest.main()
