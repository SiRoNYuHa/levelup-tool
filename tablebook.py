"""캐릭터별 성장 테이블 저장소.

JSON 구조: {"characters": {"캐릭터": {"first_level": 1, "rows": [["HP", "마력"], ...]}}}
CSV 구조 : 캐릭터,레벨,HP,힘,마력,속도,기술,방어,마방,행운,매력
           - 한 파일에 여러 캐릭터를 넣을 수 있고, '레벨'(구간 시작 레벨) 열은 생략 가능(기본 1)
           - 같은 캐릭터의 줄은 위에서 아래로 레벨 순서
           - '캐릭터' 열이 없는 CSV/JSON(단일 테이블)은 가져올 때 이름을 따로 지정한다
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from models import ALL_STATS, GrowthTable, Stat
from storage import load_table

_TRUTHY = {"1", "+1", "o", "x", "y", "v", "true", "●", "✓"}
_STAT_LABELS = {s.label for s in ALL_STATS}


class NeedsName(ValueError):
    """캐릭터 구분이 없는 단일 테이블 파일이라 이름 지정이 필요한 경우."""


def _copy(table: GrowthTable) -> GrowthTable:
    return GrowthTable(rows=list(table.rows), first_level=table.first_level)


class TableBook:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, GrowthTable] = {}
        self.load()

    # ---------- 조회/수정
    def characters(self) -> list[str]:
        return list(self._data)

    def get(self, name: str) -> GrowthTable | None:
        table = self._data.get(name)
        return _copy(table) if table is not None else None

    def set(self, name: str, table: GrowthTable) -> None:
        self._data[name] = _copy(table)

    def delete(self, name: str) -> None:
        self._data.pop(name, None)

    # ---------- 저장/불러오기
    def load(self) -> None:
        """파일이 없으면 빈 상태로 시작하고, 깨졌으면 .bak으로 백업한다."""
        if not self.path.exists():
            self._data = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = self._parse_characters(data["characters"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.path.replace(self.path.with_suffix(".json.bak"))
            self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "characters": {
                name: {
                    "first_level": table.first_level,
                    "rows": [[s.label for s in ALL_STATS if s in row] for row in table.rows],
                }
                for name, table in self._data.items()
            }
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 가져오기/내보내기
    def import_file(self, path: Path, single_name: str | None = None) -> int:
        """CSV/JSON을 읽어 합친다(같은 이름은 덮어씀). 가져온 캐릭터 수를 돌려준다.

        캐릭터 구분이 없는 단일 테이블이면 single_name이 필요하고, 없으면 NeedsName을 낸다.
        """
        if path.suffix.lower() == ".csv":
            incoming = self._read_csv(path, single_name)
        else:
            incoming = self._read_json(path, single_name)
        self._data.update(incoming)
        return len(incoming)

    def export_csv(self, path: Path) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["캐릭터", "레벨", *[s.label for s in ALL_STATS]])
            for name, table in self._data.items():
                for index, row in enumerate(table.rows):
                    writer.writerow([name, table.level_of(index), *[1 if s in row else "" for s in ALL_STATS]])

    # ---------- 파서
    @staticmethod
    def _parse_characters(data: dict) -> dict[str, GrowthTable]:
        return {
            name: GrowthTable(
                rows=[frozenset(Stat.from_label(label) for label in row) for row in item["rows"]],
                first_level=int(item.get("first_level", 1)),
            )
            for name, item in data.items()
        }

    def _read_json(self, path: Path, single_name: str | None) -> dict[str, GrowthTable]:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and "characters" in data:
            return self._parse_characters(data["characters"])
        if not single_name:
            raise NeedsName("캐릭터 구분이 없는 파일이에요.")
        return {single_name: load_table(path)}

    @staticmethod
    def _read_csv(path: Path, single_name: str | None) -> dict[str, GrowthTable]:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            if "캐릭터" not in fields:
                if not single_name:
                    raise NeedsName("캐릭터 구분이 없는 파일이에요.")
                return {single_name: load_table(path)}
            if not _STAT_LABELS & set(fields):
                raise ValueError("헤더에 스탯 이름(HP, 힘, 마력 ...)이 없어요.")
            result: dict[str, GrowthTable] = {}
            for line in reader:
                name = (line.get("캐릭터") or "").strip()
                if not name:
                    continue
                table = result.get(name)
                if table is None:
                    level_text = (line.get("레벨") or "").strip()
                    table = result[name] = GrowthTable(first_level=int(level_text) if level_text else 1)
                table.rows.append(frozenset(
                    Stat.from_label(label) for label in _STAT_LABELS
                    if (line.get(label) or "").strip().lower() in _TRUTHY
                ))
        return result
