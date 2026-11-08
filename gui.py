"""tkinter GUI: 조건 설정 / 테이블 / 판정 결과 / 최적 경로 탭."""
from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from typing import Callable

from evaluator import FAIL, allowed_up_to, find_runs, judge_table, rank_text
from models import (
    ALL_STATS, MAX_RANKS, MAX_STAT_COUNT, GrowthTable, Preset, RankCondition,
)
from simulator import PathResult, simulate_greedy, simulate_limited
from storage import load_presets, load_table, save_presets, save_table

BASE_DIR = Path(__file__).resolve().parent
# exe(PyInstaller)로 묶이면 __file__이 임시 폴더라서, 저장 파일은 exe 옆에 둔다.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE_DIR
PRESET_PATH = APP_DIR / "data" / "presets.json"
EDITING_LABEL = "(편집 중인 조건)"

RANK_COLORS = {1: "#1a7f37", 2: "#0969da", 3: "#8250df", 4: "#0a7f8c", 5: "#b35900", FAIL: "#cf222e"}

# 조건 칸 상태: 0 비어있음 / 1 필수 / 2 선택
CELL_STYLES = [("", "#f3f3f3"), ("필수", "#ffc9c9"), ("선택", "#c9dcff")]


class AppContext:
    """탭들이 공유하는 상태."""

    def __init__(self) -> None:
        self.table = GrowthTable()
        self.presets: dict[str, Preset] = load_presets(PRESET_PATH)
        self.editor_provider: Callable[[], Preset] | None = None

    def resolve_preset(self, choice: str) -> Preset:
        if choice == EDITING_LABEL and self.editor_provider:
            return self.editor_provider()
        return self.presets[choice]

    def persist_presets(self) -> None:
        save_presets(PRESET_PATH, self.presets)


def _int_of(var: tk.Variable, default: int = 0) -> int:
    try:
        return int(var.get())
    except (tk.TclError, ValueError):
        return default


# ---------------------------------------------------------------- 공용 위젯

class PresetPicker(ttk.Frame):
    """프리셋 선택 콤보박스 (저장된 프리셋 + 편집 중인 조건)."""

    def __init__(self, master, ctx: AppContext) -> None:
        super().__init__(master)
        self.ctx = ctx
        ttk.Label(self, text="프리셋").pack(side="left")
        self.var = tk.StringVar(value=EDITING_LABEL)
        self.combo = ttk.Combobox(self, textvariable=self.var, state="readonly", width=24)
        self.combo.pack(side="left", padx=4)
        self.refresh()

    def refresh(self) -> None:
        values = [EDITING_LABEL, *self.ctx.presets]
        self.combo["values"] = values
        if self.var.get() not in values:
            self.var.set(EDITING_LABEL)

    def get_preset(self) -> Preset:
        return self.ctx.resolve_preset(self.var.get())


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    width: int
    anchor: str = "center"


@dataclass
class ResultRow:
    values: tuple
    rank: int
    deleted: bool = False


RANK_BG = {1: "#e6f6ea", 2: "#e7f0fd", 3: "#f1eafc", 4: "#e2f5f6", 5: "#fdf0e0", FAIL: "#fde8e8"}
STAT_MARK = "●"
STAT_COLUMNS = [Column(s.label, s.label, 52) for s in ALL_STATS]


def stat_cells(stats) -> list[str]:
    return [STAT_MARK if s in stats else "" for s in ALL_STATS]


class ResultTable(ttk.Frame):
    """판정/경로 결과 표. 순위별로 색을 입히고, 삭제된 줄은 회색 취소선으로 보여준다."""

    def __init__(self, master, columns: list[Column]) -> None:
        super().__init__(master)
        self.tree = ttk.Treeview(self, columns=[c.key for c in columns], show="headings", selectmode="browse")
        for column in columns:
            self.tree.heading(column.key, text=column.title)
            self.tree.column(column.key, width=column.width, anchor=column.anchor, stretch=False)
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        for rank, color in RANK_COLORS.items():
            self.tree.tag_configure(f"rank{rank}", foreground=color, background=RANK_BG[rank])
        self._strike_font = tkfont.nametofont("TkDefaultFont").copy()
        self._strike_font.configure(overstrike=True)
        self.tree.tag_configure("deleted", foreground="#8c8c8c", background="#f2f2f2", font=self._strike_font)

    def show(self, rows: list[ResultRow]) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            tag = "deleted" if row.deleted else f"rank{row.rank}"  # 태그는 하나만: 우선순위 충돌 방지
            self.tree.insert("", "end", values=row.values, tags=(tag,))


# ---------------------------------------------------------------- 조건 설정 탭

class ConditionTab(ttk.Frame):
    def __init__(self, master, ctx: AppContext, on_presets_changed: Callable[[], None]) -> None:
        super().__init__(master, padding=8)
        self.ctx = ctx
        self.on_presets_changed = on_presets_changed
        self.states = [[0] * len(ALL_STATS) for _ in range(MAX_RANKS)]
        self.buttons: list[list[tk.Button]] = []
        self.optional_min = [tk.IntVar(value=1) for _ in range(MAX_RANKS)]
        self.min_count = [tk.IntVar(value=0) for _ in range(MAX_RANKS)]
        self.max_count = [tk.IntVar(value=MAX_STAT_COUNT) for _ in range(MAX_RANKS)]
        self.name_var = tk.StringVar()
        self._build_toolbar()
        self._build_grid()
        ctx.editor_provider = lambda: self.to_preset(EDITING_LABEL)
        first = next(iter(ctx.presets), None)
        if first:
            self.name_var.set(first)
            self.load_preset(ctx.presets[first])

    # --- 화면 구성
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="프리셋").pack(side="left")
        self.combo = ttk.Combobox(bar, textvariable=self.name_var, width=22)
        self.combo.pack(side="left", padx=4)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._on_select())
        for text, command in (
            ("저장", self.save),
            ("다른 이름으로 저장", self.save_as),
            ("새 조건", self.new),
            ("삭제", self.delete),
        ):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=2)
        self._refresh_names()

    def _build_grid(self) -> None:
        grid = ttk.Frame(self)
        grid.pack(anchor="w")
        headers = ["순위", *[s.label for s in ALL_STATS], "선택 최소", "띵 최소", "띵 최대"]
        for col, text in enumerate(headers):
            ttk.Label(grid, text=text, anchor="center").grid(row=0, column=col, padx=2, pady=2)
        for rank in range(MAX_RANKS):
            ttk.Label(grid, text=f"{rank + 1}순위").grid(row=rank + 1, column=0, padx=4)
            row_buttons = []
            for col in range(len(ALL_STATS)):
                button = tk.Button(grid, width=5, command=lambda r=rank, c=col: self._cycle(r, c))
                button.grid(row=rank + 1, column=col + 1, padx=1, pady=2)
                row_buttons.append(button)
            self.buttons.append(row_buttons)
            base = len(ALL_STATS) + 1
            for offset, var in enumerate(
                (self.optional_min[rank], self.min_count[rank], self.max_count[rank])
            ):
                ttk.Spinbox(grid, from_=0, to=MAX_STAT_COUNT, width=4, textvariable=var).grid(
                    row=rank + 1, column=base + offset, padx=4
                )
        ttk.Label(
            self,
            justify="left",
            foreground="#555",
            text=(
                "칸을 누르면 [비어있음 → 필수 → 선택] 순으로 바뀝니다.\n"
                "필수: 전부 올라야 함 / 선택: 선택 칸 중 '선택 최소'개 이상 올라야 함 / "
                "띵: 한 줄에서 오른 스탯 총 개수 범위\n"
                "아무 것도 설정하지 않은 순위는 판정에서 제외됩니다. 위 순위부터 차례로 검사해 처음 맞는 순위가 됩니다."
            ),
        ).pack(anchor="w", pady=10)

    # --- 상태 <-> 화면
    def _cycle(self, rank: int, col: int) -> None:
        self.states[rank][col] = (self.states[rank][col] + 1) % len(CELL_STYLES)
        self._paint(rank, col)

    def _paint(self, rank: int, col: int) -> None:
        text, color = CELL_STYLES[self.states[rank][col]]
        self.buttons[rank][col].configure(text=text, bg=color, activebackground=color)

    def load_preset(self, preset: Preset) -> None:
        for rank, cond in enumerate(preset.ranks):
            for col, stat in enumerate(ALL_STATS):
                self.states[rank][col] = 1 if stat in cond.required else 2 if stat in cond.optional else 0
                self._paint(rank, col)
            self.optional_min[rank].set(cond.optional_min)
            self.min_count[rank].set(cond.min_count)
            self.max_count[rank].set(cond.max_count)

    def to_preset(self, name: str) -> Preset:
        ranks = []
        for rank in range(MAX_RANKS):
            required = frozenset(s for c, s in enumerate(ALL_STATS) if self.states[rank][c] == 1)
            optional = frozenset(s for c, s in enumerate(ALL_STATS) if self.states[rank][c] == 2)
            min_count = _int_of(self.min_count[rank], 0)
            max_count = _int_of(self.max_count[rank], MAX_STAT_COUNT)
            if max_count < min_count:
                raise ValueError(f"{rank + 1}순위: 띵 최대가 최소보다 작아요.")
            ranks.append(RankCondition(
                required, optional,
                optional_min=min(_int_of(self.optional_min[rank], 1), len(optional)),
                min_count=min_count, max_count=max_count,
            ))
        return Preset(name, tuple(ranks))

    # --- 프리셋 관리
    def _refresh_names(self) -> None:
        self.combo["values"] = list(self.ctx.presets)
        self.on_presets_changed()

    def _on_select(self) -> None:
        preset = self.ctx.presets.get(self.name_var.get())
        if preset:
            self.load_preset(preset)

    def _store(self, name: str) -> None:
        try:
            preset = self.to_preset(name)
        except ValueError as error:
            messagebox.showwarning("조건 오류", str(error))
            return
        self.ctx.presets[name] = preset
        self.ctx.persist_presets()
        self.name_var.set(name)
        self._refresh_names()

    def save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            return self.save_as()
        self._store(name)

    def save_as(self) -> None:
        name = simpledialog.askstring("다른 이름으로 저장", "프리셋 이름:", parent=self)
        if name and name.strip():
            self._store(name.strip())

    def new(self) -> None:
        name = simpledialog.askstring("새 조건", "새 프리셋 이름:", parent=self)
        if not name or not name.strip():
            return
        self.load_preset(Preset(name.strip()))
        self._store(name.strip())

    def delete(self) -> None:
        name = self.name_var.get()
        if name not in self.ctx.presets:
            messagebox.showinfo("삭제", "저장된 프리셋을 먼저 선택해 주세요.")
            return
        if not messagebox.askyesno("삭제", f"'{name}' 프리셋을 삭제할까요?"):
            return
        del self.ctx.presets[name]
        self.ctx.persist_presets()
        first = next(iter(self.ctx.presets), None)
        self.name_var.set(first or "")
        self.load_preset(self.ctx.presets[first] if first else Preset(""))
        self._refresh_names()


# ---------------------------------------------------------------- 테이블 탭

class TableTab(ttk.Frame):
    MARK = "●"

    def __init__(self, master, ctx: AppContext) -> None:
        super().__init__(master, padding=8)
        self.ctx = ctx
        self.first_level_var = tk.IntVar(value=ctx.table.first_level)
        self._build_toolbar()
        self._build_tree()
        self.first_level_var.trace_add("write", lambda *_: self._on_first_level())
        self.render()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="첫 줄 레벨").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=999, width=5, textvariable=self.first_level_var).pack(side="left", padx=(4, 12))
        for text, command in (
            ("줄 추가", self.add_row),
            ("선택 줄 앞에 삽입", self.insert_row),
            ("선택 줄 삭제", self.remove_rows),
            ("전체 비우기", self.clear),
            ("불러오기", self.load),
            ("저장", self.save),
        ):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=2)

    def _build_tree(self) -> None:
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        columns = ("lv", *[s.label for s in ALL_STATS])
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("lv", text="구간")
        self.tree.column("lv", width=80, anchor="center")
        for stat in ALL_STATS:
            self.tree.heading(stat.label, text=stat.label)
            self.tree.column(stat.label, width=58, anchor="center")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        ttk.Label(self, foreground="#555", text="스탯 칸을 클릭하면 ●(+1) 토글. 줄 순서가 곧 레벨 순서입니다.").pack(anchor="w", pady=4)

    # --- 렌더링
    def _values(self, index: int) -> tuple:
        table = self.ctx.table
        return (table.label_of(index), *[self.MARK if s in table.rows[index] else "" for s in ALL_STATS])

    def render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index in range(len(self.ctx.table)):
            self.tree.insert("", "end", iid=str(index), values=self._values(index))

    def _selected_indices(self) -> list[int]:
        return sorted(int(iid) for iid in self.tree.selection())

    # --- 이벤트
    def _on_click(self, event) -> None:
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        column = int(self.tree.identify_column(event.x)[1:]) - 1
        if not iid or column < 1:
            return
        index, stat = int(iid), ALL_STATS[column - 1]
        self.ctx.table.rows[index] = self.ctx.table.rows[index] ^ {stat}
        self.tree.item(iid, values=self._values(index))

    def _on_first_level(self) -> None:
        self.ctx.table.first_level = max(1, _int_of(self.first_level_var, 1))
        self.render()

    def add_row(self) -> None:
        self.ctx.table.rows.append(frozenset())
        self.render()

    def insert_row(self) -> None:
        selected = self._selected_indices()
        self.ctx.table.rows.insert(selected[0] if selected else len(self.ctx.table), frozenset())
        self.render()

    def remove_rows(self) -> None:
        for index in reversed(self._selected_indices()):
            del self.ctx.table.rows[index]
        self.render()

    def clear(self) -> None:
        if messagebox.askyesno("전체 비우기", "테이블을 전부 비울까요?"):
            self.ctx.table.rows.clear()
            self.render()

    def load(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV/JSON", "*.csv *.json"), ("All", "*.*")])
        if not path:
            return
        try:
            self.ctx.table = load_table(Path(path))
        except (OSError, ValueError, KeyError) as error:
            messagebox.showerror("불러오기 실패", str(error))
            return
        self.first_level_var.set(self.ctx.table.first_level)
        self.render()

    def save(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv"), ("JSON", "*.json")])
        if path:
            save_table(Path(path), self.ctx.table)


# ---------------------------------------------------------------- 판정 결과 탭

class JudgeTab(ttk.Frame):
    def __init__(self, master, ctx: AppContext) -> None:
        super().__init__(master, padding=8)
        self.ctx = ctx
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        self.picker = PresetPicker(bar, ctx)
        self.picker.pack(side="left")
        ttk.Button(bar, text="판정", command=self.run).pack(side="left", padx=8)
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, justify="left", wraplength=900).pack(anchor="w", pady=(0, 6))
        columns = [Column("lv", "구간", 70), *STAT_COLUMNS, Column("verdict", "판정", 70)]
        self.view = ResultTable(self, columns)
        self.view.pack(fill="both", expand=True)

    def refresh(self) -> None:
        self.picker.refresh()

    def run(self) -> None:
        table = self.ctx.table
        if not table.rows:
            self.view.show([])
            self.summary.set("테이블이 비어 있어요. 테이블 탭에서 줄을 추가하거나 파일을 불러와 주세요.")
            return
        try:
            preset = self.picker.get_preset()
        except ValueError as error:
            return messagebox.showwarning("조건 오류", str(error))
        ranks = judge_table(table.rows, preset)
        self.view.show([
            ResultRow((table.label_of(i), *stat_cells(table.rows[i]), rank_text(r)), r)
            for i, r in enumerate(ranks)
        ])
        runs = find_runs([r == FAIL for r in ranks])
        if runs:
            spans = ", ".join(
                f"{table.label_of(a)} ~ {table.label_of(b)} ({b - a + 1}줄)" for a, b in runs
            )
            self.summary.set(f"넘어야 하는 구간(Fail 연속): {spans}")
        else:
            self.summary.set("Fail 구간이 없어요.")


# ---------------------------------------------------------------- 최적 경로 탭

class PathTab(ttk.Frame):
    def __init__(self, master, ctx: AppContext) -> None:
        super().__init__(master, padding=8)
        self.ctx = ctx
        self._signature: tuple[int, int] | None = None
        self.allowed = tk.IntVar(value=2)
        self.start = tk.IntVar(value=1)
        self.target = tk.IntVar(value=1)
        self.mode = tk.StringVar(value="greedy")
        self.cap = tk.IntVar(value=5)
        self._build_controls()
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, justify="left", wraplength=900).pack(anchor="w", pady=6)
        columns = [
            Column("state", "처리", 56), Column("level", "레벨", 80), Column("orig", "원본 구간", 80),
            *STAT_COLUMNS, Column("verdict", "판정", 70), Column("note", "비고", 170, "w"),
        ]
        self.view = ResultTable(self, columns)
        self.view.pack(fill="both", expand=True)

    def _build_controls(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.picker = PresetPicker(top, self.ctx)
        self.picker.pack(side="left")
        for label, var, high in (("허용 순위(~N순위)", self.allowed, MAX_RANKS), ("시작 레벨", self.start, 999), ("목표 레벨", self.target, 999)):
            ttk.Label(top, text=label).pack(side="left", padx=(12, 2))
            ttk.Spinbox(top, from_=1, to=high, width=5, textvariable=var).pack(side="left")
        mode = ttk.Frame(self)
        mode.pack(fill="x", pady=6)
        ttk.Radiobutton(mode, text="허용 줄이 나올 때까지 순서대로 삭제", variable=self.mode, value="greedy").pack(side="left")
        ttk.Radiobutton(mode, text="전직변경 상한 안에서 허용 줄 최대화", variable=self.mode, value="limited").pack(side="left", padx=12)
        ttk.Label(mode, text="상한(회)").pack(side="left")
        ttk.Spinbox(mode, from_=0, to=999, width=5, textvariable=self.cap).pack(side="left", padx=4)
        ttk.Button(mode, text="계산", command=self.run).pack(side="left", padx=12)

    def refresh(self) -> None:
        self.picker.refresh()
        signature = (self.ctx.table.first_level, len(self.ctx.table))
        if signature != self._signature:  # 테이블이 바뀌면 시작/목표 레벨 기본값 갱신
            self._signature = signature
            self.start.set(signature[0])
            self.target.set(signature[0] + signature[1])

    def run(self) -> None:
        table = self.ctx.table
        if not table.rows:
            return messagebox.showinfo("테이블 없음", "테이블이 비어 있어요. 테이블 탭에서 줄을 추가하거나 파일을 불러와 주세요.")
        try:
            preset = self.picker.get_preset()
        except ValueError as error:
            return messagebox.showwarning("조건 오류", str(error))
        start, target = _int_of(self.start, 1), _int_of(self.target, 1)
        start_index = table.index_of_level(start)
        if not 0 <= start_index < len(table) or target <= start:
            return messagebox.showwarning("입력 오류", "시작/목표 레벨을 확인해 주세요. (시작 레벨은 테이블 범위 안, 목표 > 시작)")

        ranks = judge_table(table.rows, preset)
        accept = allowed_up_to(_int_of(self.allowed, 1))
        level_count = target - start
        if self.mode.get() == "greedy":
            result = simulate_greedy(ranks, start_index, start, level_count, accept)
        else:
            result = simulate_limited(ranks, start_index, start, level_count, accept, _int_of(self.cap, 0))
        self._show(result, accept)

    def _show(self, result: PathResult, accept: Callable[[int], bool]) -> None:
        table = self.ctx.table
        rows = []
        for o in result.outcomes:
            cells = (table.label_of(o.row_index), *stat_cells(table.rows[o.row_index]), rank_text(o.rank))
            orig, stats_and_verdict = cells[0], cells[1:]
            if o.deleted:
                values = ("삭제", f"Lv{o.level}", orig, *stats_and_verdict, "전직변경으로 삭제")
            else:
                note = "" if accept(o.rank) else "비허용 줄 수령"
                values = ("수령", f"{o.level}→{o.level + 1}", orig, *stats_and_verdict, note)
            rows.append(ResultRow(values, o.rank, deleted=o.deleted))
        self.view.show(rows)

        by_level = result.deletions_by_level()
        per_level = ", ".join(f"Lv{lv}: {n}회" for lv, n in sorted(by_level.items())) or "없음"
        text = (
            f"전직변경 총 {result.deletions}회 │ 허용 줄 {result.accepted}/{result.levels_covered}레벨 수령\n"
            f"레벨별 전직변경: {per_level}"
        )
        if result.truncated:
            text += "\n⚠ 테이블 줄이 모자라 목표 레벨까지 도달하지 못했어요."
        self.summary.set(text)


# ---------------------------------------------------------------- 앱

def _fix_treeview_tag_colors(style: ttk.Style) -> None:
    """Tk 8.6.9 Treeview에서 tag 색상이 적용되지 않는 알려진 버그 우회."""
    def fixed(option: str) -> list:
        return [e for e in style.map("Treeview", query_opt=option) if e[:2] != ("!disabled", "!selected")]

    style.map("Treeview", foreground=fixed("foreground"), background=fixed("background"))


def run() -> None:
    root = tk.Tk()
    root.title("전직변경 경로 계산기")
    root.geometry("1100x700")
    _fix_treeview_tag_colors(ttk.Style())
    ctx = AppContext()
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    tabs: dict[str, ttk.Frame] = {}
    refreshable: list = []

    def sync_presets() -> None:
        for tab in refreshable:
            tab.refresh()

    tabs["조건 설정"] = ConditionTab(notebook, ctx, on_presets_changed=sync_presets)
    tabs["테이블"] = TableTab(notebook, ctx)
    tabs["판정 결과"] = JudgeTab(notebook, ctx)
    tabs["최적 경로"] = PathTab(notebook, ctx)
    refreshable.extend([tabs["판정 결과"], tabs["최적 경로"]])
    for title, tab in tabs.items():
        notebook.add(tab, text=title)

    notebook.bind("<<NotebookTabChanged>>", lambda _e: sync_presets())
    sync_presets()
    root.mainloop()
