import math
import unittest


def history_api(testcase: unittest.TestCase):
    try:
        from tools.robot_cpg_gui.history import RollingHistory
    except ImportError as exc:
        testcase.fail(f"history API is not implemented: {exc}")
    return RollingHistory


class RollingHistoryTests(unittest.TestCase):
    def test_series_evicts_points_older_than_window(self):
        RollingHistory = history_api(self)
        history = RollingHistory(("J1", "J2"), window_seconds=10.0)
        history.append(0.0, {"J1": 1.0, "J2": 2.0})
        history.append(5.0, {"J1": 3.0, "J2": 4.0})
        history.append(11.0, {"J1": 5.0, "J2": 6.0})

        series = history.series(11.0)

        self.assertEqual(series["J1"], [(5.0, 3.0), (11.0, 5.0)])
        self.assertEqual(series["J2"], [(5.0, 4.0), (11.0, 6.0)])

    def test_values_must_match_names_and_be_finite(self):
        RollingHistory = history_api(self)
        history = RollingHistory(("J1", "J2"), window_seconds=10.0)

        with self.assertRaises(ValueError):
            history.append(1.0, {"J1": 1.0})
        with self.assertRaises(ValueError):
            history.append(1.0, {"J1": math.nan, "J2": 2.0})

    def test_timestamps_must_be_monotonic(self):
        RollingHistory = history_api(self)
        history = RollingHistory(("J1",), window_seconds=10.0)
        history.append(2.0, {"J1": 1.0})

        with self.assertRaises(ValueError):
            history.append(1.9, {"J1": 2.0})

    def test_series_returns_a_copy(self):
        RollingHistory = history_api(self)
        history = RollingHistory(("J1",), window_seconds=10.0)
        history.append(1.0, {"J1": 2.0})
        first = history.series(1.0)
        first["J1"].append((2.0, 99.0))

        self.assertEqual(history.series(1.0)["J1"], [(1.0, 2.0)])


if __name__ == "__main__":
    unittest.main()
