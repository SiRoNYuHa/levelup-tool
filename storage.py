"""프리셋(JSON)과 성장 테이블(CSV/JSON) 저장·불러오기."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from models import ALL_STATS, GrowthTable, Preset, RankCondition, Stat

_TRUTHY = {"1", "+1", "o", "x", "y", "v", "true", "●", "✓"}


# ---------- 프리셋 ----------

def _condition_to_dict(c: RankCondition) -> dict:
    labels = lambda stats: [s.label for s in ALL_STATS if s in stats]
    return {
        "required": labels(c.required),
        "optional": labels(c.optional),
        "optional_min": c.optional_min,
        "min_count": c.min_count,
        "max_count": c.max_count,
    }


def _condition_from_dict(d: dict) -> RankCondition:
    return RankCondition(
        required=frozenset(Stat.from_label(x) for x in d.get("required", [])),
        optional=frozenset(Stat.from_label(x) for x in d.get("optional", [])),
        optional_min=int(d.get("optional_min", 1)),
        min_count=int(d.get("min_count", 0)),
        max_count=int(d.get("max_count", len(ALL_STATS))),
    )


def save_presets(path: Path, presets: dict[str, Preset]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "presets": [
            {"name": p.name, "ranks": [_condition_to_dict(c) for c in p.ranks]}
            for p in presets.values()
        ]
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_presets(path: Path) -> dict[str, Preset]:
    """저장된 프리셋을 읽는다. 파일이 없으면 빈 상태로 시작하고, 깨졌으면 .bak으로 백업한다."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            item["name"]: Preset(item["name"], tuple(_condition_from_dict(r) for r in item["ranks"]))
            for item in data["presets"]
        }
    except (OSError, ValueError, KeyError, TypeError):
        path.replace(path.with_suffix(".json.bak"))
        return {}


# ---------- 테이블 ----------

def _load_csv(path: Path) -> GrowthTable:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            raise ValueError("CSV가 비어 있어요.")
        columns: list[tuple[int, Stat]] = []
        for i, name in enumerate(header):
            try:
                columns.append((i, Stat.from_label(name)))
            except ValueError:
                continue  # '구간' 같은 부가 열은 무시
        if not columns:
            raise ValueError("헤더에 스탯 이름(HP, 힘, 마력 ...)이 없어요.")
        rows = []
        for line in reader:
            if not line:
                continue
            rows.append(frozenset(
                stat for i, stat in columns
                if i < len(line) and line[i].strip().lower() in _TRUTHY
            ))
    return GrowthTable(rows=rows)


def _load_json(path: Path) -> GrowthTable:
    data = json.loads(path.read_text(encoding="utf-8"))
    first_level = 1
    if isinstance(data, dict):
        first_level = int(data.get("first_level", 1))
        data = data["rows"]
    rows = [frozenset(Stat.from_label(label) for label in row) for row in data]
    return GrowthTable(rows=rows, first_level=first_level)


def load_table(path: Path) -> GrowthTable:
    return _load_json(path) if path.suffix.lower() == ".json" else _load_csv(path)


def save_table(path: Path, table: GrowthTable) -> None:
    if path.suffix.lower() == ".json":
        data = {
            "first_level": table.first_level,
            "rows": [[s.label for s in ALL_STATS if s in row] for row in table.rows],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([s.label for s in ALL_STATS])
        for row in table.rows:
            writer.writerow([1 if s in row else "" for s in ALL_STATS])
