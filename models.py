"""도메인 모델: 스탯, 순위 조건, 프리셋, 성장 테이블."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stat(Enum):
    HP = "HP"
    STR = "힘"
    MAG = "마력"
    SPD = "속도"
    SKL = "기술"
    DEF = "방어"
    RES = "마방"
    LUK = "행운"
    CHM = "매력"

    @property
    def label(self) -> str:
        return self.value

    @classmethod
    def from_label(cls, label: str) -> "Stat":
        for stat in cls:
            if stat.value == label.strip():
                return stat
        raise ValueError(f"알 수 없는 스탯: {label!r}")


ALL_STATS: tuple[Stat, ...] = tuple(Stat)
MAX_RANKS = 5
MAX_STAT_COUNT = len(ALL_STATS)

StatSet = frozenset  # frozenset[Stat]


def format_stats(stats: StatSet) -> str:
    return " ".join(s.label for s in ALL_STATS if s in stats)


@dataclass(frozen=True)
class RankCondition:
    """한 순위의 판정 조건.

    - required: 전부 올라야 하는 스탯
    - optional: 이 중 optional_min개 이상 올라야 하는 스탯 그룹
    - min_count / max_count: 한 줄에서 오른 스탯 총 개수(띵) 범위
    """

    required: StatSet = frozenset()
    optional: StatSet = frozenset()
    optional_min: int = 1
    min_count: int = 0
    max_count: int = MAX_STAT_COUNT

    @property
    def is_active(self) -> bool:
        """아무 것도 설정하지 않은 순위는 판정에서 제외한다."""
        return bool(self.required or self.optional or self.min_count > 0)

    def matches(self, stats: StatSet) -> bool:
        if not self.is_active:
            return False
        if not self.required <= stats:
            return False
        if self.optional and len(self.optional & stats) < self.optional_min:
            return False
        return self.min_count <= len(stats) <= self.max_count


@dataclass(frozen=True)
class Preset:
    name: str
    ranks: tuple[RankCondition, ...] = field(
        default_factory=lambda: (RankCondition(),) * MAX_RANKS
    )

    def __post_init__(self) -> None:
        padded = tuple(self.ranks[:MAX_RANKS])
        padded += (RankCondition(),) * (MAX_RANKS - len(padded))
        object.__setattr__(self, "ranks", padded)


@dataclass
class GrowthTable:
    """레벨업 성장 테이블. rows[i]는 (first_level + i) 레벨 구간에서 오르는 스탯."""

    rows: list[StatSet] = field(default_factory=list)
    first_level: int = 1

    def __len__(self) -> int:
        return len(self.rows)

    def level_of(self, index: int) -> int:
        return self.first_level + index

    def label_of(self, index: int) -> str:
        level = self.level_of(index)
        return f"{level}→{level + 1}"

    def index_of_level(self, level: int) -> int:
        return level - self.first_level
