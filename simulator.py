"""전직변경 시뮬레이션.

전직변경 1번 = 현재 레벨 구간의 줄이 삭제되고, 뒤의 줄이 한 칸씩 앞으로 당겨진다.
따라서 '삭제'는 원본 테이블에서 다음 줄로 넘어가는 것과 같다.
레벨 L에서 d번 삭제했다면 받는 줄은 원본의 (시작 인덱스 + 지금까지 받은 줄 + 지금까지 삭제한 줄)번째다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

AcceptFn = Callable[[int], bool]  # 순위(0=Fail) -> 허용 여부


@dataclass(frozen=True)
class RowOutcome:
    row_index: int  # 원본 테이블 인덱스
    rank: int
    deleted: bool
    level: int  # 삭제된 레벨 / 수령한 레벨 (구간 시작 레벨)


@dataclass(frozen=True)
class PathResult:
    outcomes: tuple[RowOutcome, ...]
    accepted: int  # 허용 순위로 받은 줄 수
    deletions: int  # 전직변경 총 횟수
    levels_covered: int  # 실제로 줄을 받은 레벨 수
    truncated: bool  # 테이블이 모자라 목표 레벨까지 못 간 경우

    def deletions_by_level(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for outcome in self.outcomes:
            if outcome.deleted:
                counts[outcome.level] = counts.get(outcome.level, 0) + 1
        return counts


def _build_result(outcomes: list[RowOutcome], accepted: int, level_count: int) -> PathResult:
    received = sum(1 for o in outcomes if not o.deleted)
    return PathResult(
        outcomes=tuple(outcomes),
        accepted=accepted,
        deletions=len(outcomes) - received,
        levels_covered=received,
        truncated=received < level_count,
    )


def simulate_greedy(
    ranks: Sequence[int],
    start_index: int,
    start_level: int,
    level_count: int,
    is_accepted: AcceptFn,
) -> PathResult:
    """허용되는 줄이 나올 때까지 계속 삭제하는 기본 모드."""
    outcomes: list[RowOutcome] = []
    index, level, received = start_index, start_level, 0
    while received < level_count and index < len(ranks):
        accepted = is_accepted(ranks[index])
        outcomes.append(RowOutcome(index, ranks[index], not accepted, level))
        if accepted:
            received += 1
            level += 1
        index += 1
    return _build_result(outcomes, received if outcomes else 0, level_count)


_RECEIVE, _DELETE, _END = "R", "D", "E"


def simulate_limited(
    ranks: Sequence[int],
    start_index: int,
    start_level: int,
    level_count: int,
    is_accepted: AcceptFn,
    max_deletions: int,
) -> PathResult:
    """전직변경 횟수 상한 안에서 허용 줄을 최대한 많이 받는 경로 (DP).

    상태 (j, d): j번째 레벨을 처리 중이고 지금까지 d번 삭제함.
    이때 보고 있는 원본 줄은 start_index + j + d 로 정해진다.
    점수는 (허용 줄 수 최대, 삭제 횟수 최소) 순으로 비교한다.
    """
    n, cap, total = level_count, max_deletions, len(ranks)
    best = [[(0, 0)] * (cap + 1) for _ in range(n + 1)]
    choice = [[_END] * (cap + 1) for _ in range(n + 1)]

    def better(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return (a[0], -a[1]) > (b[0], -b[1])

    for j in range(n - 1, -1, -1):
        for d in range(cap, -1, -1):
            index = start_index + j + d
            if index >= total:
                continue  # 줄이 없음: (0, 0), _END
            gain, dels = best[j + 1][d]
            current = (gain + int(is_accepted(ranks[index])), dels)
            picked = _RECEIVE
            if d < cap:
                gain_d, dels_d = best[j][d + 1]
                if better((gain_d, dels_d + 1), current):
                    current, picked = (gain_d, dels_d + 1), _DELETE
            best[j][d] = current
            choice[j][d] = picked

    outcomes: list[RowOutcome] = []
    j = d = accepted = 0
    while j < n:
        step = choice[j][d]
        if step == _END:
            break
        index = start_index + j + d
        if step == _RECEIVE:
            ok = is_accepted(ranks[index])
            accepted += int(ok)
            outcomes.append(RowOutcome(index, ranks[index], False, start_level + j))
            j += 1
        else:
            outcomes.append(RowOutcome(index, ranks[index], True, start_level + j))
            d += 1
    return _build_result(outcomes, accepted, level_count)
