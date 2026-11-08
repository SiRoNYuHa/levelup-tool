"""전직변경 시뮬레이션.

전직변경 1번 = 현재 레벨 구간의 줄이 삭제되고, 뒤의 줄이 한 칸씩 앞으로 당겨진다.
따라서 '삭제'는 원본 테이블에서 다음 줄로 넘어가는 것과 같다.
레벨 L에서 e번 삭제했다면 받는 줄은 원본의 (시작 인덱스 + 지금까지 받은 줄 + 지금까지 삭제한 줄 + e)번째다.

- max_per_level: 한 레벨에서 연속으로 전직변경할 수 있는 최대 횟수 (None = 제한 없음, 0 = 금지)
- headroom: 스탯별 '상한까지 남은 양'. 0이 되면 그 스탯은 상한에 도달한 것이고,
  이후 줄을 판정할 때 그 스탯은 오르지 않은 것으로 취급한다. 삭제한 줄은 능력치에 쌓이지 않는다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, Hashable, Mapping, Optional, Sequence

AcceptFn = Callable[[int], bool]  # 순위(0=Fail) -> 허용 여부
RankAtFn = Callable[[int, frozenset], int]  # (원본 줄 인덱스, 상한에 도달한 스탯 집합) -> 순위

_INF = 10**9  # '앞으로 남은 줄을 다 받아도 상한에 못 닿는' 여유
_END = -1
MAX_TRANSITIONS = 1_200_000  # 상한 모드 탐색량 제한 (약 3~4초)


class SearchTooLarge(Exception):
    """상한 모드 탐색량이 너무 커서 중단한 경우."""


@dataclass(frozen=True)
class PathProblem:
    row_stats: Sequence[frozenset]  # 원본 줄별로 오르는 스탯
    rank_at: RankAtFn
    is_accepted: AcceptFn
    start_index: int
    start_level: int
    level_count: int
    max_per_level: Optional[int] = None
    headroom: Mapping[Hashable, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RowOutcome:
    row_index: int  # 원본 테이블 인덱스
    rank: int  # 그 시점의 능력치 상한을 반영한 판정
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


class _CapModel:
    """상한 도달 상태(스탯별 남은 여유)를 튜플로 다루는 도우미."""

    def __init__(self, problem: PathProblem) -> None:
        self.keys = tuple(problem.headroom)
        self.start = tuple(problem.headroom[k] for k in self.keys)
        self.row_stats = problem.row_stats
        total = len(problem.row_stats)
        # suffix[n][i] = i번째 줄부터 끝까지 keys[n]이 오르는 줄 수
        self.suffix = []
        for key in self.keys:
            counts = [0] * (total + 2)
            for i in range(total - 1, -1, -1):
                counts[i] = counts[i + 1] + (key in problem.row_stats[i])
            self.suffix.append(counts)
        self.total = total

    def capped(self, state: tuple) -> frozenset:
        return frozenset(k for k, v in zip(self.keys, state) if v == 0)

    def advance(self, state: tuple, index: int) -> tuple:
        stats = self.row_stats[index]
        return tuple(
            v - 1 if (v > 0 and v != _INF and k in stats) else v
            for k, v in zip(self.keys, state)
        )

    def canon(self, state: tuple, next_index: int) -> tuple:
        """남은 줄을 전부 받아도 상한에 못 닿는 스탯은 같은 상태로 합쳐 탐색량을 줄인다."""
        i = min(next_index, self.total)
        return tuple(
            _INF if (v != _INF and v > self.suffix[n][i]) else v
            for n, v in enumerate(state)
        )


def _cached(rank_at: RankAtFn) -> RankAtFn:
    cache: dict[tuple, int] = {}

    def lookup(index: int, capped: frozenset) -> int:
        key = (index, capped)
        if key not in cache:
            cache[key] = rank_at(index, capped)
        return cache[key]

    return lookup


def _build_result(outcomes: list[RowOutcome], problem: PathProblem) -> PathResult:
    received = [o for o in outcomes if not o.deleted]
    return PathResult(
        outcomes=tuple(outcomes),
        accepted=sum(1 for o in received if problem.is_accepted(o.rank)),
        deletions=len(outcomes) - len(received),
        levels_covered=len(received),
        truncated=len(received) < problem.level_count,
    )


def simulate_greedy(problem: PathProblem) -> PathResult:
    """허용되는 줄이 나올 때까지 계속 삭제하는 기본 모드.

    한 레벨에서 max_per_level번을 써도 허용 줄이 안 나오면 그 줄을 그대로 받고 다음 레벨로 간다.
    """
    model, rank_at = _CapModel(problem), _cached(problem.rank_at)
    state = model.start
    outcomes: list[RowOutcome] = []
    index, level, received, level_deletions = problem.start_index, problem.start_level, 0, 0
    while received < problem.level_count and index < len(problem.row_stats):
        rank = rank_at(index, model.capped(state))
        cap = problem.max_per_level
        can_delete = cap is None or level_deletions < cap
        if problem.is_accepted(rank) or not can_delete:
            outcomes.append(RowOutcome(index, rank, False, level))
            state = model.advance(state, index)
            received += 1
            level += 1
            level_deletions = 0
        else:
            outcomes.append(RowOutcome(index, rank, True, level))
            level_deletions += 1
        index += 1
    return _build_result(outcomes, problem)


def simulate_limited(problem: PathProblem, max_deletions: int) -> PathResult:
    """전체 전직변경 상한(과 레벨당 상한) 안에서 허용 줄을 최대한 많이 받는 경로.

    상태 (j, d, 상한 여유): j번째 레벨을 처리 중이고 지금까지 총 d번 삭제함.
    점수는 (허용 줄 수 최대, 삭제 횟수 최소) 순으로 비교하고, 동점이면 삭제를 뒤로 미룬다(e가 작은 쪽).
    상한 스탯이 없으면 상태가 (j, d)뿐이라 빠르고, 있으면 탐색량이 늘 수 있다(SearchTooLarge).
    """
    n, total, start = problem.level_count, len(problem.row_stats), problem.start_index
    cap = max(0, min(max_deletions, total - start))
    per_level = cap if problem.max_per_level is None else min(problem.max_per_level, cap)
    model, rank_at = _CapModel(problem), _cached(problem.rank_at)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 2 * n + 1000))

    memo: dict[tuple, tuple[tuple[int, int], int]] = {}
    transitions = 0

    def solve(j: int, d: int, state: tuple) -> tuple[int, int]:
        nonlocal transitions
        if j == n:
            return (0, 0)
        key = (j, d, state)
        hit = memo.get(key)
        if hit is not None:
            return hit[0]
        capped = model.capped(state)
        best, picked = (0, 0), _END  # 줄이 없으면 여기서 끝(truncated)
        for e in range(min(per_level, cap - d) + 1):
            index = start + j + d + e
            if index >= total:
                break
            transitions += 1
            if transitions > MAX_TRANSITIONS:
                raise SearchTooLarge
            next_state = model.canon(model.advance(state, index), index + 1)
            gain, dels = solve(j + 1, d + e, next_state)
            candidate = (gain + int(problem.is_accepted(rank_at(index, capped))), dels + e)
            if picked == _END or (candidate[0], -candidate[1]) > (best[0], -best[1]):
                best, picked = candidate, e
        memo[key] = (best, picked)
        return best

    state = model.canon(model.start, start)
    solve(0, 0, state)

    outcomes: list[RowOutcome] = []
    j = d = 0
    while j < n:
        _, e = memo[(j, d, state)]
        if e == _END:
            break
        level, capped = problem.start_level + j, model.capped(state)
        for k in range(e):
            index = start + j + d + k
            outcomes.append(RowOutcome(index, rank_at(index, capped), True, level))
        index = start + j + d + e
        outcomes.append(RowOutcome(index, rank_at(index, capped), False, level))
        state = model.canon(model.advance(state, index), index + 1)
        j += 1
        d += e
    return _build_result(outcomes, problem)
