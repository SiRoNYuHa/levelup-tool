"""캐릭터 + 직종별 스탯 저장소 (현재 스탯 / 상한 스탯 공용).

JSON 구조: {"characters": {"캐릭터": {"직종": {"HP": 20, "힘": 15, ...}}}}
CSV 구조 : 캐릭터,직종,HP,힘,마력,속도,기술,방어,마방,행운,매력 (빈 칸 = 값 없음)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from models import ALL_STATS, Stat

StatValues = dict  # dict[Stat, int]


class StatBook:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict[str, StatValues]] = {}
        self.load()

    # ---------- 조회/수정
    def characters(self) -> list[str]:
        return list(self._data)

    def jobs(self, character: str) -> list[str]:
        return list(self._data.get(character, {}))

    def get(self, character: str, job: str) -> StatValues | None:
        values = self._data.get(character, {}).get(job)
        return dict(values) if values is not None else None

    def set(self, character: str, job: str, values: StatValues) -> None:
        self._data.setdefault(character, {})[job] = dict(values)

    def delete(self, character: str, job: str) -> None:
        jobs = self._data.get(character, {})
        jobs.pop(job, None)
        if not jobs:
            self._data.pop(character, None)

    # ---------- 파일 입출력
    def load(self) -> None:
        """파일이 없으면 빈 상태로 시작하고, 깨졌으면 .bak으로 백업한다."""
        if not self.path.exists():
            self._data = {}
            return
        try:
            self._data = self._parse_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.path.replace(self.path.with_suffix(".json.bak"))
            self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "characters": {
                char: {
                    job: {s.label: values[s] for s in ALL_STATS if s in values}
                    for job, values in jobs.items()
                }
                for char, jobs in self._data.items()
            }
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_file(self, path: Path) -> int:
        """JSON/CSV를 읽어 기존 데이터에 합친다(같은 캐릭터+직종은 덮어씀). 가져온 개수를 돌려준다."""
        if path.suffix.lower() == ".csv":
            incoming = self._parse_csv(path)
        else:
            incoming = self._parse_json(path.read_text(encoding="utf-8-sig"))
        count = 0
        for char, jobs in incoming.items():
            for job, values in jobs.items():
                self.set(char, job, values)
                count += 1
        return count

    # ---------- 파서
    @staticmethod
    def _parse_values(raw: dict) -> StatValues:
        values = {}
        for label, number in raw.items():
            if number in ("", None):
                continue
            values[Stat.from_label(label)] = int(number)
        return values

    @classmethod
    def _parse_json(cls, text: str) -> dict[str, dict[str, StatValues]]:
        data = json.loads(text)
        data = data.get("characters", data)
        return {
            char: {job: cls._parse_values(raw) for job, raw in jobs.items()}
            for char, jobs in data.items()
        }

    @classmethod
    def _parse_csv(cls, path: Path) -> dict[str, dict[str, StatValues]]:
        result: dict[str, dict[str, StatValues]] = {}
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                char, job = (row.get("캐릭터") or "").strip(), (row.get("직종") or "").strip()
                if not char or not job:
                    continue
                stat_cells = {k: v for k, v in row.items() if k in {s.label for s in ALL_STATS}}
                result.setdefault(char, {})[job] = cls._parse_values(
                    {k: (v or "").strip() for k, v in stat_cells.items()}
                )
        return result


class CharacterStatBook:
    """캐릭터별 스탯 저장소 (직종 없음). 현재 스탯용.

    StatBook과 같은 메서드 모양을 맞춰서 화면 코드를 공유한다(job 인자는 무시, jobs()는 항상 빈 목록).
    JSON 구조: {"characters": {"캐릭터": {"HP": 20, "힘": 15, ...}}}
    예전 버전(캐릭터→직종→스탯) 파일은 각 캐릭터의 첫 번째 직종 값을 가져와 이어서 쓴다.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, StatValues] = {}
        self.load()

    def characters(self) -> list[str]:
        return list(self._data)

    def jobs(self, character: str) -> list[str]:
        return []

    def get(self, character: str, job: str = "") -> StatValues | None:
        values = self._data.get(character)
        return dict(values) if values is not None else None

    def set(self, character: str, job: str, values: StatValues) -> None:
        self._data[character] = dict(values)

    def delete(self, character: str, job: str = "") -> None:
        self._data.pop(character, None)

    def load(self) -> None:
        """파일이 없으면 빈 상태로 시작하고, 깨졌으면 .bak으로 백업한다."""
        if not self.path.exists():
            self._data = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data = data.get("characters", data)
            self._data = {char: self._parse_entry(raw) for char, raw in data.items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.path.replace(self.path.with_suffix(".json.bak"))
            self._data = {}

    @staticmethod
    def _parse_entry(raw: dict) -> StatValues:
        if any(isinstance(value, dict) for value in raw.values()):  # 예전 형식: 직종별
            raw = next(iter(raw.values()), {})
        return StatBook._parse_values(raw)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "characters": {
                char: {s.label: values[s] for s in ALL_STATS if s in values}
                for char, values in self._data.items()
            }
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
