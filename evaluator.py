"""한 줄/테이블의 순위 판정 (GUI와 무관한 순수 로직)."""
from __future__ import annotations

from typing import Callable, Sequence

from models import Preset, StatSet

FAIL = 0


def judge_row(stats: StatSet, preset: Preset) -> int:
    """1~5순위 중 처음 맞는 순위를 돌려주고, 하나도 안 맞으면 FAIL(0)."""
    for rank, condition in enumerate(preset.ranks, start=1):
        if condition.matches(stats):
            return rank
    return FAIL


def judge_table(rows: Sequence[StatSet], preset: Preset) -> list[int]:
    return [judge_row(stats, preset) for stats in rows]


def rank_text(rank: int) -> str:
    return "Fail" if rank == FAIL else f"{rank}순위"


def allowed_up_to(max_rank: int) -> Callable[[int], bool]:
    """1~max_rank순위만 허용하는 판정 함수."""
    return lambda rank: 1 <= rank <= max_rank


def find_runs(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """flags가 True인 연속 구간을 (시작 인덱스, 끝 인덱스) 포함 범위로 돌려준다."""
    runs: list[tuple[int, int]] = []
    start = None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs
