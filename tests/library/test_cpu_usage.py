import math
import unittest

from library.sensors.sensors_python import core_utilization, fastest_frequency, _parse_cpu_list


# 4 physical cores, 8 logical CPUs. Sibling pairs are (0, 4), (1, 5), (2, 6), (3, 7).
PAIRS = [(0, 4), (1, 5), (2, 6), (3, 7)]


class TestCoreUtilization(unittest.TestCase):
    def test_one_thread_per_core_reads_as_full(self):
        per_cpu = [100, 100, 100, 100, 0, 0, 0, 0]
        self.assertAlmostEqual(core_utilization(per_cpu, PAIRS), 100.0)

    def test_both_threads_busy_stays_at_100(self):
        per_cpu = [100] * 8
        self.assertAlmostEqual(core_utilization(per_cpu, PAIRS), 100.0)

    def test_one_socket_worth_of_cores_reads_as_half(self):
        per_cpu = [100, 100, 0, 0, 0, 0, 0, 0]
        self.assertAlmostEqual(core_utilization(per_cpu, PAIRS), 50.0)

    def test_work_split_across_a_hyperthread_pair_still_fills_the_core(self):
        per_cpu = [60, 40, 0, 0, 40, 0, 0, 0]
        # Core 0 is 60+40, core 1 is 40, cores 2 and 3 are idle.
        self.assertAlmostEqual(core_utilization(per_cpu, PAIRS), 35.0)

    def test_idle(self):
        self.assertAlmostEqual(core_utilization([0] * 8, PAIRS), 0.0)

    def test_missing_topology_falls_back_to_the_thread_average(self):
        self.assertAlmostEqual(core_utilization([100, 0, 0, 0], []), 25.0)

    def test_parse_cpu_list(self):
        self.assertEqual(_parse_cpu_list("0,44\n"), [0, 44])
        self.assertEqual(_parse_cpu_list("0-2,8"), [0, 1, 2, 8])


class TestFastestFrequency(unittest.TestCase):
    def test_boosting_core_is_not_diluted_by_idle_cores(self):
        currents = [1200.0] * 86 + [3600.0, 3700.0]
        self.assertEqual(fastest_frequency(currents), 3700.0)

    def test_even_load_reports_the_shared_clock(self):
        self.assertEqual(fastest_frequency([2800.0] * 88), 2800.0)

    def test_offline_zeros_are_ignored(self):
        self.assertEqual(fastest_frequency([0.0, 0.0, 2400.0]), 2400.0)

    def test_empty_or_unreadable_is_nan(self):
        self.assertTrue(math.isnan(fastest_frequency([])))
        self.assertTrue(math.isnan(fastest_frequency([0.0, None, float("nan"), "n/a"])))


if __name__ == "__main__":
    unittest.main()
