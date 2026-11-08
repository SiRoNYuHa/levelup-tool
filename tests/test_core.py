import random
import sys
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluator import FAIL, allowed_up_to, find_runs, judge_row
from models import GrowthTable, Preset, RankCondition, Stat
from report import annotate
from simulator import PathProblem, SearchTooLarge, simulate_greedy, simulate_limited
import simulator
from statbook import CharacterStatBook, StatBook
from storage import load_table
from tablebook import NeedsName, TableBook

S = Stat
ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_table.csv"
PHYSICAL = Preset("물리 딜러", (
    RankCondition(required=frozenset({S.SPD, S.STR}), min_count=2),
    RankCondition(required=frozenset({S.STR, S.DEF}), optional=frozenset({S.SKL, S.HP}),
                  min_count=3, max_count=4),
))


def problem_from_ranks(ranks, start_index, start_level, level_count, allowed, per_level=None):
    """상한 스탯 없이 '순위 목록'만으로 만든 문제."""
    return PathProblem(
        row_stats=[frozenset()] * len(ranks),
        rank_at=lambda index, capped: ranks[index],
        is_accepted=allowed_up_to(allowed),
        start_index=start_index, start_level=start_level, level_count=level_count,
        max_per_level=per_level,
    )


class JudgeTest(unittest.TestCase):
    def test_first_rank(self):
        self.assertEqual(judge_row(frozenset({S.SPD, S.STR, S.LUK}), PHYSICAL), 1)

    def test_second_rank(self):
        self.assertEqual(judge_row(frozenset({S.STR, S.DEF, S.HP}), PHYSICAL), 2)

    def test_fail(self):
        self.assertEqual(judge_row(frozenset({S.LUK, S.CHM}), PHYSICAL), FAIL)

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
        r = simulate_greedy(problem_from_ranks(self.ranks, 0, 6, 3, 1))
        self.assertEqual((r.deletions, r.accepted, r.truncated), (3, 3, False))
        self.assertEqual(r.deletions_by_level(), {6: 2, 7: 1})

    def test_limited_respects_cap(self):
        r = simulate_limited(problem_from_ranks(self.ranks, 0, 6, 3, 1), 2)
        self.assertEqual((r.accepted, r.deletions), (2, 2))

    def test_limited_large_cap_matches_greedy(self):
        r = simulate_limited(problem_from_ranks(self.ranks, 0, 6, 3, 1), 50)
        self.assertEqual((r.accepted, r.deletions), (3, 3))

    def test_limited_zero_cap_deletes_nothing(self):
        r = simulate_limited(problem_from_ranks(self.ranks, 0, 6, 3, 1), 0)
        self.assertEqual((r.deletions, r.levels_covered), (0, 3))

    def test_truncated_when_table_runs_out(self):
        r = simulate_greedy(problem_from_ranks([0, 0], 0, 1, 2, 1))
        self.assertTrue(r.truncated)


class PerLevelTest(unittest.TestCase):
    ranks = [0, 0, 0, 1]

    def test_greedy_unlimited(self):
        r = simulate_greedy(problem_from_ranks(self.ranks, 0, 1, 1, 1))
        self.assertEqual((r.deletions, r.accepted), (3, 1))

    def test_greedy_per_level_gives_up_after_cap(self):
        r = simulate_greedy(problem_from_ranks(self.ranks, 0, 1, 1, 1, per_level=2))
        self.assertEqual((r.deletions, r.accepted), (2, 0))
        self.assertFalse(r.outcomes[-1].deleted)

    def test_zero_forbids_deletion(self):
        self.assertEqual(simulate_greedy(problem_from_ranks(self.ranks, 0, 1, 2, 1, per_level=0)).deletions, 0)
        self.assertEqual(simulate_limited(problem_from_ranks(self.ranks, 0, 1, 2, 1, per_level=0), 10).deletions, 0)

    def test_limited_never_exceeds_per_level(self):
        ranks = [0, 0, 1, 0, 0, 1, 0, 1]
        r = simulate_limited(problem_from_ranks(ranks, 0, 1, 3, 1, per_level=1), 10)
        self.assertTrue(all(c <= 1 for c in r.deletions_by_level().values()))


def brute_force(rows, preset, accept, start, n, cap, per_level, keys, headroom):
    """DP 검증용 완전탐색(상태 압축 없이): (허용 줄 수, -삭제 횟수)의 최댓값."""

    @lru_cache(maxsize=None)
    def go(j, d, state):
        if j == n:
            return (0, 0)
        capped = frozenset(k for k, v in zip(keys, state) if v == 0)
        best, first = (0, 0), True
        for e in range(0, min(per_level, cap - d) + 1):
            idx = start + j + d + e
            if idx >= len(rows):
                break
            nxt = tuple(max(v - 1, 0) if k in rows[idx] else v for k, v in zip(keys, state))
            gain, neg = go(j + 1, d + e, nxt)
            cand = (gain + int(accept(judge_row(rows[idx] - capped, preset))), neg - e)
            if first or cand > best:
                best, first = cand, False
        return best

    return go(0, 0, tuple(headroom[k] for k in keys))


class CapTest(unittest.TestCase):
    SPEED_ONLY = Preset("속도", (RankCondition(required=frozenset({S.SPD})),))

    def make(self, rows, headroom, count=2, allowed=1, per_level=None, start=0):
        return PathProblem(
            row_stats=rows,
            rank_at=lambda i, capped: judge_row(rows[i] - capped, self.SPEED_ONLY),
            is_accepted=allowed_up_to(allowed),
            start_index=start, start_level=1, level_count=count,
            max_per_level=per_level, headroom=headroom,
        )

    def test_capped_stat_is_excluded_from_judgement(self):
        rows = [frozenset({S.SPD}), frozenset({S.SPD}), frozenset({S.HP}), frozenset({S.SPD})]
        r = simulate_greedy(self.make(rows, {S.SPD: 1}))
        # 첫 줄로 속도가 상한에 도달 -> 이후 속도 줄은 Fail 취급이라 전부 삭제되고 줄이 모자람
        self.assertEqual([o.deleted for o in r.outcomes], [False, True, True, True])
        self.assertTrue(r.truncated)

    def test_deleted_rows_do_not_add_to_stats(self):
        rows = [frozenset({S.HP}), frozenset({S.SPD}), frozenset({S.SPD})]
        # 속도 여유 2: 삭제된 첫 줄(HP)은 쌓이지 않고, 속도 줄 둘을 받으면 상한 도달
        r = simulate_greedy(self.make(rows, {S.SPD: 2}, count=2))
        self.assertEqual([o.deleted for o in r.outcomes], [True, False, False])
        info = annotate(r.outcomes, rows, {}, {S.SPD: 2})
        self.assertEqual(info[-1].reached, frozenset({S.SPD}))
        self.assertEqual(info[-1].stats_after[S.SPD], 2)

    def test_no_caps_equals_plain_dp(self):
        rows = [frozenset({S.SPD}), frozenset({S.HP}), frozenset({S.SPD})]
        r = simulate_limited(self.make(rows, {}, count=2), 5)
        self.assertEqual((r.accepted, r.deletions), (2, 1))

    def test_dp_matches_brute_force_with_caps(self):
        rng = random.Random(11)
        pool = [S.SPD, S.STR, S.HP]
        preset = Preset("t", (
            RankCondition(required=frozenset({S.SPD})),
            RankCondition(required=frozenset({S.STR})),
        ))
        for _ in range(250):
            rows = [frozenset(s for s in pool if rng.random() < 0.5) for _ in range(rng.randint(3, 9))]
            headroom = {s: rng.randint(0, 3) for s in (S.SPD, S.STR) if rng.random() < 0.8}
            start, n = rng.randint(0, 2), rng.randint(1, 5)
            cap, per = rng.randint(0, 5), rng.choice([None, 0, 1, 2, 3])
            accept = allowed_up_to(rng.randint(1, 2))
            problem = PathProblem(
                rows, lambda i, capped: judge_row(rows[i] - capped, preset), accept,
                start, 1, n, per, headroom,
            )
            result = simulate_limited(problem, cap)
            eff_cap = max(0, min(cap, len(rows) - start))
            eff_per = eff_cap if per is None else min(per, eff_cap)
            keys = tuple(headroom)
            expected = brute_force(rows, preset, accept, start, n, eff_cap, eff_per, keys, headroom)
            case = (rows, headroom, start, n, cap, per)
            self.assertEqual((result.accepted, -result.deletions), expected, case)
            self.assertLessEqual(result.deletions, cap)
            if per is not None:
                self.assertTrue(all(c <= per for c in result.deletions_by_level().values()), case)
            # annotate가 재현한 '제외된 스탯'으로 다시 판정해도 시뮬레이터의 판정과 같아야 한다
            infos = annotate(result.outcomes, rows, {}, {k: v for k, v in headroom.items()})
            for outcome, info in zip(result.outcomes, infos):
                self.assertEqual(judge_row(rows[outcome.row_index] - info.excluded, preset), outcome.rank, case)

    def test_search_limit_raises(self):
        old = simulator.MAX_TRANSITIONS
        simulator.MAX_TRANSITIONS = 5
        try:
            rows = [frozenset({S.SPD})] * 12
            with self.assertRaises(SearchTooLarge):
                simulate_limited(self.make(rows, {S.SPD: 3}, count=8), 10)
        finally:
            simulator.MAX_TRANSITIONS = old


class StorageTest(unittest.TestCase):
    def test_sample_table(self):
        table = load_table(SAMPLE)
        self.assertEqual(len(table), 25)
        self.assertEqual(table.rows[0], frozenset({S.HP, S.MAG, S.SPD, S.DEF, S.LUK}))

    def test_statbook_roundtrip_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "book.json"
            book = StatBook(path)
            self.assertEqual(book.characters(), [])
            book.set("A", "기사", {S.HP: 20, S.SPD: 15})
            book.set("A", "궁수", {S.HP: 18})
            book.save()
            again = StatBook(path)
            self.assertEqual(again.jobs("A"), ["기사", "궁수"])
            self.assertEqual(again.get("A", "기사"), {S.HP: 20, S.SPD: 15})
            again.delete("A", "기사"); again.delete("A", "궁수")
            self.assertEqual(again.characters(), [])

    def test_statbook_csv_import_and_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "caps.csv"
            csv_path.write_text("캐릭터,직종,HP,힘,마력,속도,기술,방어,마방,행운,매력\nA,기사,30,,,25,,,,,\n", encoding="utf-8-sig")
            book = StatBook(Path(tmp) / "book.json")
            self.assertEqual(book.import_file(csv_path), 1)
            self.assertEqual(book.get("A", "기사"), {S.HP: 30, S.SPD: 25})  # 빈 칸 = 상한 없음
            bad = Path(tmp) / "bad.json"
            bad.write_text("{broken", encoding="utf-8")
            self.assertEqual(StatBook(bad).characters(), [])
            self.assertTrue(bad.with_suffix(".json.bak").exists())

    def test_character_statbook_roundtrip_and_old_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "characters.json"
            book = CharacterStatBook(path)
            book.set("A", "", {S.HP: 20, S.SPD: 15})
            book.set("B", "무시되는직종", {S.STR: 5})
            book.save()
            again = CharacterStatBook(path)
            self.assertEqual(again.characters(), ["A", "B"])
            self.assertEqual(again.get("A"), {S.HP: 20, S.SPD: 15})
            self.assertEqual(again.jobs("A"), [])
            again.delete("A")
            self.assertIsNone(again.get("A"))
            # 예전 형식(캐릭터 -> 직종 -> 스탯)은 첫 직종 값으로 이어받는다
            old = Path(tmp) / "old.json"
            old.write_text('{"characters": {"A": {"기사": {"HP": 7}, "궁수": {"HP": 9}}}}', encoding="utf-8")
            self.assertEqual(CharacterStatBook(old).get("A"), {S.HP: 7})
            bad = Path(tmp) / "bad.json"
            bad.write_text("{broken", encoding="utf-8")
            self.assertEqual(CharacterStatBook(bad).characters(), [])
            self.assertTrue(bad.with_suffix(".json.bak").exists())

    def test_tablebook_roundtrip_and_csv_with_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            csv_path = tmp / "tables.csv"
            csv_path.write_text(
                "캐릭터,레벨,HP,힘,마력,속도,기술,방어,마방,행운,매력\n"
                "A,3,1,,,1,,,,,\nA,4,,1,,,,,,,\nB,,,,1,,,,,,\n", encoding="utf-8-sig")
            book = TableBook(tmp / "tables.json")
            self.assertEqual(book.import_file(csv_path), 2)
            a = book.get("A")
            self.assertEqual((a.first_level, a.rows), (3, [frozenset({S.HP, S.SPD}), frozenset({S.STR})]))
            self.assertEqual(book.get("B").first_level, 1)
            book.save()
            self.assertEqual(TableBook(tmp / "tables.json").get("A").rows, a.rows)
            out = tmp / "export.csv"
            book.export_csv(out)
            again = TableBook(tmp / "other.json")
            again.import_file(out)
            self.assertEqual(again.get("A").rows, a.rows)
            self.assertEqual(again.get("A").first_level, 3)

    def test_tablebook_single_table_needs_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = TableBook(Path(tmp) / "t.json")
            with self.assertRaises(NeedsName):
                book.import_file(SAMPLE)
            self.assertEqual(book.import_file(SAMPLE, single_name="샘플"), 1)
            self.assertEqual(len(book.get("샘플")), 25)

    def test_tablebook_get_returns_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = TableBook(Path(tmp) / "t.json")
            book.set("A", GrowthTable(rows=[frozenset({S.HP})]))
            book.get("A").rows.clear()
            self.assertEqual(len(book.get("A")), 1)


if __name__ == "__main__":
    unittest.main()
