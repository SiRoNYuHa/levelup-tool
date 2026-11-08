"""경로 결과를 능력치 관점에서 다시 훑어 줄마다 주석 정보를 만든다 (GUI 표시용)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Mapping, Sequence

from simulator import RowOutcome


@dataclass(frozen=True)
class RowInfo:
    excluded: frozenset  # 이 줄에서 상한에 이미 도달해 있어서 제외된 스탯
    reached: frozenset  # 이 줄을 받아서 새로 상한에 도달한 스탯
    stats_after: Mapping[Hashable, int]  # 이 줄 처리 뒤의 능력치


def annotate(
    outcomes: Sequence[RowOutcome],
    row_stats: Sequence[frozenset],
    current: Mapping[Hashable, int],
    caps: Mapping[Hashable, int],
) -> list[RowInfo]:
    values = dict(current)
    infos: list[RowInfo] = []
    for outcome in outcomes:
        stats = row_stats[outcome.row_index]
        excluded = frozenset(s for s in stats if s in caps and values.get(s, 0) >= caps[s])
        reached = set()
        if not outcome.deleted:
            for stat in stats - excluded:
                values[stat] = values.get(stat, 0) + 1
                if stat in caps and values[stat] >= caps[stat]:
                    reached.add(stat)
        infos.append(RowInfo(excluded, frozenset(reached), dict(values)))
    return infos
