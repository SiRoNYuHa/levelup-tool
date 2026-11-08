import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluator import FAIL, allowed_up_to, find_runs, judge_row, judge_table
from models import Preset, RankCondition, Stat
from simulator import simulate_greedy, simulate_limited
from storage import load_table

S = Stat
PHYSICAL = Preset("물리 딜러", (
    RankCondition(required=frozenset({Stat.SPD, Stat.STR}), min_count=2),
    RankCondition(required=frozenset({Stat.STR, Stat.DEF}), optional=frozenset({Stat.SKL, Stat.HP}),
                  min_count=3, max_count=4),
))
SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_table.csv"


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.physical = PHYSICAL

    def test_first_rank(self):
        self.assertEqual(judge_row(frozenset({S.SPD, S.STR, S.LUK}), self.physical), 1)

    def test_second_rank(self):
        self.assertEqual(judge_row(frozenset({S.STR, S.DEF, S.HP}), self.physical), 2)

    def test_fail(self):
        self.assertEqual(judge_row(frozenset({S.LUK, S.CHM}), self.physical), FAIL)

    def test_empty_condition_never_matches(self):
        self.assertFalse(RankCondition().matches(frozenset({S.HP})))

    def test_count_range(self):
        cond = RankCondition(required=frozenset({S.HP}), min_count=2, max_count=2)
        self.assertTrue(cond.matches(frozenset({S.HP, S.STR})))
        self.assertFalse(cond.matches(frozenset({S.HP, S.STR, S.DEF})))

    def test_find_runs(self):
        self.assertEqual(find_runs([False, True, True, False, True]), [(1, 2), (4, 4)])


class SimulatorTest(unittest.TestCase):
    ranks = [0, 0, 1, 0, 1, 1]

    def test_greedy(self):
        r = simulate_greedy(self.ranks, 0, 6, 3, allowed_up_to(1))
        self.assertEqual((r.deletions, r.accepted, r.truncated), (3, 3, False))
        self.assertEqual(r.deletions_by_level(), {6: 2, 7: 1})

    def test_limited_respects_cap(self):
        r = simulate_limited(self.ranks, 0, 6, 3, allowed_up_to(1), 2)
        self.assertEqual((r.accepted, r.deletions), (2, 2))

    def test_limited_large_cap_matches_greedy(self):
        r = simulate_limited(self.ranks, 0, 6, 3, allowed_up_to(1), 50)
        self.assertEqual((r.accepted, r.deletions), (3, 3))

    def test_limited_zero_cap_deletes_nothing(self):
        r = simulate_limited(self.ranks, 0, 6, 3, allowed_up_to(1), 0)
        self.assertEqual(r.deletions, 0)
        self.assertEqual(r.levels_covered, 3)

    def test_truncated_when_table_runs_out(self):
        r = simulate_greedy([0, 0], 0, 1, 2, allowed_up_to(1))
        self.assertTrue(r.truncated)


class SampleTableTest(unittest.TestCase):
    def test_load_and_judge(self):
        table = load_table(SAMPLE)
        self.assertEqual(len(table), 25)
        self.assertEqual(table.rows[0], frozenset({S.HP, S.MAG, S.SPD, S.DEF, S.LUK}))
        ranks = judge_table(table.rows, PHYSICAL)
        self.assertEqual(len(ranks), 25)


if __name__ == "__main__":
    unittest.main()
