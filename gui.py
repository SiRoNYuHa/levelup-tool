"""tkinter GUI: 조건 설정 / 테이블 / 판정 결과 / 최적 경로 탭."""
from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from typing import Callable

from evaluator import FAIL, allowed_up_to, find_runs, judge_row, judge_table, rank_text
from models import (
    ALL_STATS, MAX_RANKS, MAX_STAT_COUNT, GrowthTable, Preset, RankCondition,
)
from models import Stat
from report import annotate
from simulator import PathProblem, PathResult, SearchTooLarge, simulate_greedy, simulate_limited
from statbook import CharacterStatBook, StatBook
from storage import load_presets, save_presets
from tablebook import NeedsName, TableBook

BASE_DIR = Path(__file__).resolve().parent
# exe(PyInstaller)로 묶이면 __file__이 임시 폴더라서, 저장 파일은 exe 옆에 둔다.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE_DIR
PRESET_PATH = APP_DIR / "data" / "presets.json"
TABLES_PATH = APP_DIR / "data" / "tables.json"
CURRENT_STATS_PATH = APP_DIR / "data" / "characters.json"
CAPS_PATH = APP_DIR / "data" / "caps.json"
EDITING_LABEL = "(편집 중인 조건)"

RANK_COLORS = {1: "#1a7f37", 2: "#0969da", 3: "#8250df", 4: "#0a7f8c", 5: "#b35900", FAIL: "#cf222e"}

# 조건 칸 상태: 0 비어있음 / 1 필수 / 2 선택
CELL_STYLES = [("", "#f3f3f3"), ("필수", "#ffc9c9"), ("선택", "#c9dcff")]


class AppContext:
    """탭들이 공유하는 상태."""

    def __init__(self) -> None:
        self.table = GrowthTable()
        self.presets: dict[str, Preset] = load_presets(PRESET_PATH)
        self.table_book = TableBook(TABLES_PATH)
        self.current_book = CharacterStatBook(CURRENT_STATS_PATH)
        self.caps_book = StatBook(CAPS_PATH)
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

    def __init__(self, master, columns: list[Column],
                 on_cell_click: Callable[[int, str], None] | None = None) -> None:
        super().__init__(master)
        self.keys = [c.key for c in columns]
        self.on_cell_click = on_cell_click
        self.tree = ttk.Treeview(self, columns=self.keys, show="headings", selectmode="browse")
        self.tree.bind("<Button-1>", self._on_click)
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
        for index, row in enumerate(rows):
            tag = "deleted" if row.deleted else f"rank{row.rank}"  # 태그는 하나만: 우선순위 충돌 방지
            self.tree.insert("", "end", iid=str(index), values=row.values, tags=(tag,))

    def _on_click(self, event) -> None:
        if not self.on_cell_click or self.tree.identify("region", event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        column = int(self.tree.identify_column(event.x)[1:]) - 1
        if iid and 0 <= column < len(self.keys):
            self.on_cell_click(int(iid), self.keys[column])


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
        self.name_var = tk.StringVar()
        self.status = tk.StringVar()
        self._build_toolbar()
        self._build_tree()
        self.first_level_var.trace_add("write", lambda *_: self._on_first_level())
        self.render()

    def _build_toolbar(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 4))
        ttk.Label(top, text="캐릭터").pack(side="left")
        self.combo = ttk.Combobox(top, textvariable=self.name_var, width=18)
        self.combo.pack(side="left", padx=4)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self.load_character())
        for text, command in (
            ("저장", self.save_character),
            ("삭제", self.delete_character),
            ("가져오기(CSV/JSON)", self.import_file),
            ("내보내기(CSV)", self.export_csv),
        ):
            ttk.Button(top, text=text, command=command).pack(side="left", padx=2)
        ttk.Label(top, textvariable=self.status, foreground="#555").pack(side="left", padx=8)
        self.refresh_names()

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="첫 줄 레벨").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=999, width=5, textvariable=self.first_level_var).pack(side="left", padx=(4, 12))
        for text, command in (
            ("줄 추가", self.add_row),
            ("선택 줄 앞에 삽입", self.insert_row),
            ("선택 줄 삭제", self.remove_rows),
            ("전체 비우기", self.clear),
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
        ttk.Label(
            self, foreground="#555", justify="left",
            text=("스탯 칸을 클릭하면 ●(+1) 토글. 줄 순서가 곧 레벨 순서입니다.\n"
                  "수정한 뒤에는 '저장'을 눌러야 캐릭터에 반영돼요. 새 캐릭터는 이름을 입력하고 저장하면 만들어집니다."),
        ).pack(anchor="w", pady=4)

    # --- 렌더링
    def _values(self, index: int) -> tuple:
        table = self.ctx.table
        return (table.label_of(index), *[self.MARK if s in table.rows[index] else "" for s in ALL_STATS])

    def render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index in range(len(self.ctx.table)):
            self.tree.insert("", "end", iid=str(index), values=self._values(index))

    def refresh_names(self) -> None:
        self.combo["values"] = self.ctx.table_book.characters()

    def _selected_indices(self) -> list[int]:
        return sorted(int(iid) for iid in self.tree.selection())

    # --- 캐릭터 저장소
    def _use_table(self, table: GrowthTable) -> None:
        self.ctx.table = table
        self.first_level_var.set(table.first_level)
        self.render()

    def load_character(self) -> None:
        table = self.ctx.table_book.get(self.name_var.get())
        if table is not None:
            self._use_table(table)
            self.status.set(f"'{self.name_var.get()}' 불러옴")

    def save_character(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showinfo("저장", "캐릭터 이름을 입력하거나 선택해 주세요.")
            return
        self.ctx.table_book.set(name, self.ctx.table)
        self.ctx.table_book.save()
        self.refresh_names()
        self.status.set(f"'{name}' 저장됨")

    def delete_character(self) -> None:
        name = self.name_var.get()
        if name not in self.ctx.table_book.characters():
            messagebox.showinfo("삭제", "저장된 캐릭터를 먼저 선택해 주세요.")
            return
        if not messagebox.askyesno("삭제", f"'{name}' 캐릭터의 테이블을 삭제할까요?"):
            return
        self.ctx.table_book.delete(name)
        self.ctx.table_book.save()
        self.name_var.set("")
        self.refresh_names()
        self.status.set(f"'{name}' 삭제됨")

    def import_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV/JSON", "*.csv *.json"), ("All", "*.*")])
        if not path:
            return
        book, single_name = self.ctx.table_book, None
        try:
            try:
                count = book.import_file(Path(path))
            except NeedsName:
                single_name = simpledialog.askstring(
                    "캐릭터 이름", "캐릭터 구분이 없는 파일이에요.\n이 테이블을 저장할 캐릭터 이름:", parent=self)
                if not single_name or not single_name.strip():
                    return
                single_name = single_name.strip()
                count = book.import_file(Path(path), single_name=single_name)
        except (OSError, ValueError, KeyError) as error:
            messagebox.showerror("가져오기 실패", str(error))
            return
        book.save()
        self.refresh_names()
        if single_name:
            self.name_var.set(single_name)
            self.load_character()
        self.status.set(f"{count}개 캐릭터 가져옴")

    def export_csv(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            self.ctx.table_book.export_csv(Path(path))
        except OSError as error:
            messagebox.showerror("내보내기 실패", str(error))
            return
        self.status.set("저장된 캐릭터 전체를 내보냈어요")

    # --- 편집 이벤트
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
        if messagebox.askyesno("전체 비우기", "화면의 테이블을 전부 비울까요? (저장된 캐릭터는 그대로예요)"):
            self.ctx.table.rows.clear()
            self.render()


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

def _names(stats) -> str:
    return "·".join(s.label for s in ALL_STATS if s in stats)


@dataclass
class RowDetail:
    title: str
    notes: str
    values: dict  # 이 줄 처리 뒤의 누적 능력치
    caps: dict
    raised: frozenset  # 이 줄에서 실제로 오른 스탯


class DetailWindow(tk.Toplevel):
    """한 줄을 처리한 뒤의 9개 스탯 현재/상한을 칸 맞춰 보여주는 창."""

    def __init__(self, master, detail: RowDetail) -> None:
        super().__init__(master)
        self.title("상세보기")
        self.resizable(False, False)
        ttk.Label(self, text=detail.title).pack(anchor="w", padx=12, pady=(10, 2))
        if detail.notes:
            ttk.Label(self, text=detail.notes, foreground="#555").pack(anchor="w", padx=12)
        grid = ttk.Frame(self, padding=12)
        grid.pack()
        for col, stat in enumerate(ALL_STATS):
            current, cap = detail.values.get(stat, 0), detail.caps.get(stat)
            maxed = cap is not None and current >= cap
            text = str(current) if cap is None else f"{current}/{cap}"
            tk.Label(grid, text=stat.label, width=11, relief="ridge", bg="#e9ecef").grid(row=0, column=col)
            tk.Label(
                grid, text=text + (" MAX" if maxed else ""), width=11, relief="ridge",
                bg="#fff3bf" if stat in detail.raised else "white",
                fg="#cf222e" if maxed else "black",
            ).grid(row=1, column=col)
        ttk.Label(self, text="노란 칸 = 이 줄에서 오른 스탯 / MAX = 상한 도달", foreground="#555").pack(padx=12)
        ttk.Button(self, text="닫기", command=self.destroy).pack(pady=10)


class StatInputRow(ttk.LabelFrame):
    """캐릭터/직종 드롭박스 + 9개 스탯 입력칸. 캐릭터+직종 단위로 저장·불러오기."""

    def __init__(self, master, title: str, book: StatBook | CharacterStatBook,
                 on_selected: Callable[[str, str], None] | None = None,
                 importable: bool = False, use_job: bool = True) -> None:
        super().__init__(master, text=title, padding=6)
        self.book = book
        self.use_job = use_job
        self.on_selected = on_selected
        self.char_var = tk.StringVar()
        self.job_var = tk.StringVar()
        self.status = tk.StringVar()
        self.values = {stat: tk.StringVar() for stat in ALL_STATS}

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="캐릭터").pack(side="left")
        self.char_combo = ttk.Combobox(top, textvariable=self.char_var, width=14)
        self.char_combo.pack(side="left", padx=(4, 10))
        self.char_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_char())
        if use_job:
            ttk.Label(top, text="직종").pack(side="left")
            self.job_combo = ttk.Combobox(top, textvariable=self.job_var, width=14)
            self.job_combo.pack(side="left", padx=(4, 10))
            self.job_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_job())
        ttk.Button(top, text="저장", command=self.save).pack(side="left", padx=2)
        ttk.Button(top, text="삭제", command=self.delete).pack(side="left", padx=2)
        if importable:
            ttk.Button(top, text="가져오기(CSV/JSON)", command=self.import_file).pack(side="left", padx=2)
        ttk.Label(top, textvariable=self.status, foreground="#555").pack(side="left", padx=8)

        fields = ttk.Frame(self)
        fields.pack(anchor="w", pady=(4, 0))
        for col, stat in enumerate(ALL_STATS):
            ttk.Label(fields, text=stat.label).grid(row=0, column=col, padx=2)
            ttk.Entry(fields, width=6, justify="center", textvariable=self.values[stat]).grid(row=1, column=col, padx=2)
        self.refresh_lists()

    # --- 목록/선택
    def refresh_lists(self) -> None:
        self.char_combo["values"] = self.book.characters()
        if self.use_job:
            self.job_combo["values"] = self.book.jobs(self.char_var.get())

    def _load(self) -> None:
        saved = self.book.get(self.char_var.get(), self.job_var.get()) or {}
        for stat, var in self.values.items():
            var.set(str(saved[stat]) if stat in saved else "")
        self.status.set("")

    def _notify(self) -> None:
        if self.on_selected:
            self.on_selected(self.char_var.get(), self.job_var.get())

    def _on_char(self) -> None:
        if self.use_job:
            jobs = self.book.jobs(self.char_var.get())
            self.job_combo["values"] = jobs
            self.job_var.set(jobs[0] if jobs else "")
        self._load()
        self._notify()

    def _on_job(self) -> None:
        self._load()
        self._notify()

    def select_character(self, character: str) -> None:
        """다른 입력 줄에서 캐릭터를 골랐을 때 따라가기. 저장된 캐릭터일 때만, 직종은 그 캐릭터의 첫 직종으로."""
        if character not in self.book.characters():
            return
        self.char_var.set(character)
        jobs = self.book.jobs(character)
        self.job_combo["values"] = jobs
        if self.job_var.get() not in jobs:
            self.job_var.set(jobs[0] if jobs else "")
        self._load()

    # --- 값
    def stat_values(self) -> dict[Stat, int]:
        """입력된 스탯만 돌려준다(빈 칸은 제외). 잘못된 값이면 ValueError."""
        result = {}
        for stat, var in self.values.items():
            text = var.get().strip()
            if not text:
                continue
            try:
                number = int(text)
            except ValueError:
                raise ValueError(f"{stat.label}: 정수를 입력해 주세요.") from None
            if number < 0:
                raise ValueError(f"{stat.label}: 0 이상의 값을 입력해 주세요.")
            result[stat] = number
        return result

    # --- 저장소
    def save(self) -> None:
        character, job = self.char_var.get().strip(), self.job_var.get().strip()
        if not character or (self.use_job and not job):
            messagebox.showinfo("저장", "캐릭터와 직종을 입력하거나 선택해 주세요." if self.use_job else "캐릭터를 입력하거나 선택해 주세요.")
            return
        try:
            values = self.stat_values()
        except ValueError as error:
            messagebox.showwarning("입력 오류", str(error))
            return
        self.book.set(character, job, values)
        self.book.save()
        self.refresh_lists()
        self.status.set(f"{self._label(character, job)} 저장됨")

    def delete(self) -> None:
        character, job = self.char_var.get(), self.job_var.get()
        if self.book.get(character, job) is None:
            messagebox.showinfo("삭제", "저장된 항목을 먼저 선택해 주세요.")
            return
        if not messagebox.askyesno("삭제", f"{self._label(character, job)} 을(를) 삭제할까요?"):
            return
        self.book.delete(character, job)
        self.book.save()
        self.job_var.set("")
        if character not in self.book.characters():
            self.char_var.set("")
        self.refresh_lists()
        self._load()

    def _label(self, character: str, job: str) -> str:
        return f"{character}/{job}" if self.use_job else character

    def import_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV/JSON", "*.csv *.json"), ("All", "*.*")])
        if not path:
            return
        try:
            count = self.book.import_file(Path(path))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            messagebox.showerror("가져오기 실패", str(error))
            return
        self.book.save()
        self.refresh_lists()
        self.status.set(f"{count}개 가져옴")


@dataclass(frozen=True)
class RunSettings:
    """계산 버튼을 누른 시점의 최적 경로 설정 스냅샷."""

    rows: list
    preset: Preset
    current: dict
    caps: dict
    start_index: int
    start_level: int
    level_count: int
    per_level: int | None
    greedy: bool
    deletion_cap: int


def simulate_with(settings: RunSettings, accept: Callable[[int], bool]) -> PathResult:
    rows = settings.rows
    problem = PathProblem(
        row_stats=rows,
        rank_at=lambda index, capped: judge_row(rows[index] - capped, settings.preset),
        is_accepted=accept,
        start_index=settings.start_index, start_level=settings.start_level,
        level_count=settings.level_count, max_per_level=settings.per_level,
        headroom={stat: max(0, settings.caps[stat] - settings.current.get(stat, 0)) for stat in settings.caps},
    )
    if settings.greedy:
        return simulate_greedy(problem)
    return simulate_limited(problem, settings.deletion_cap)


class FinalResultWindow(tk.Toplevel):
    """체크한 순위만 허용했을 때의 최종 스탯을 시나리오별로 쌓아서 비교하는 창."""

    def __init__(self, master, settings: RunSettings) -> None:
        super().__init__(master)
        self.title("최종 결과")
        self.geometry("1010x420")
        self.settings = settings
        self.row_ids: dict[str, str] = {}  # 시나리오 이름 -> 표 줄 id (같은 조합은 갱신)

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="허용할 순위").pack(side="left")
        self.vars: list[tk.BooleanVar] = []
        first_active = next((i for i, c in enumerate(settings.preset.ranks) if c.is_active), None)
        for index, condition in enumerate(settings.preset.ranks):
            var = tk.BooleanVar(value=index == first_active)
            self.vars.append(var)
            ttk.Checkbutton(
                top, text=f"{index + 1}순위", variable=var,
                state="normal" if condition.is_active else "disabled",  # 비어 있는 순위는 선택 불가
            ).pack(side="left", padx=4)
        ttk.Button(top, text="추가", command=self.add).pack(side="left", padx=(12, 2))
        ttk.Button(top, text="비우기", command=self.clear).pack(side="left", padx=2)
        end_level = settings.start_level + settings.level_count
        mode = "순서대로 삭제" if settings.greedy else f"전직변경 상한 {settings.deletion_cap}회"
        ttk.Label(
            self, foreground="#555", padding=(8, 0),
            text=f"기준: {settings.start_level}레벨 → {end_level}레벨 / {mode} / 스탯은 최종값(현재 스탯 대비 증가량). "
                 "같은 조합을 다시 추가하면 그 줄이 갱신돼요.",
        ).pack(anchor="w")

        columns = ["scenario", "deletions", "received", *[s.label for s in ALL_STATS]]
        frame = ttk.Frame(self, padding=8)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (("scenario", "시나리오", 110), ("deletions", "전직변경", 80), ("received", "수령", 110)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="center", stretch=False)
        for stat in ALL_STATS:
            self.tree.heading(stat.label, text=stat.label)
            self.tree.column(stat.label, width=76, anchor="center", stretch=False)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    @staticmethod
    def scenario_name(ranks: list[int]) -> str:
        return f"{ranks[0]}순위만" if len(ranks) == 1 else "+".join(map(str, ranks)) + "순위"

    def add(self) -> None:
        chosen = [rank for rank, var in enumerate(self.vars, start=1) if var.get()]
        if not chosen:
            messagebox.showinfo("최종 결과", "허용할 순위를 하나 이상 체크해 주세요.")
            return
        name = self.scenario_name(chosen)
        try:
            result = simulate_with(self.settings, lambda rank: rank in chosen)
        except SearchTooLarge:
            values = (name, "계산 너무 큼", "", *[""] * len(ALL_STATS))
        else:
            infos = annotate(result.outcomes, self.settings.rows, self.settings.current, self.settings.caps)
            final = infos[-1].stats_after if infos else self.settings.current
            received = f"{result.accepted}/{result.levels_covered}" + (" (줄 부족)" if result.truncated else "")
            current = self.settings.current
            cells = [f"{final.get(stat, 0)}(+{final.get(stat, 0) - current.get(stat, 0)})" for stat in ALL_STATS]
            values = (name, f"{result.deletions}회", received, *cells)
        iid = self.row_ids.get(name)
        if iid is None:
            self.row_ids[name] = self.tree.insert("", "end", values=values)
        else:
            self.tree.item(iid, values=values)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.row_ids.clear()


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
        self.per_level_mode = tk.StringVar(value="unlimited")
        self.per_level = tk.IntVar(value=1)

        self.current_row = StatInputRow(
            self, "현재 스탯 (캐릭터별)", ctx.current_book, on_selected=self._follow_cap_row, use_job=False)
        self.current_row.pack(fill="x")
        self.cap_row = StatInputRow(self, "상한 스탯 (캐릭터+직종별, 빈 칸 = 상한 없음)", ctx.caps_book, importable=True)
        self.cap_row.pack(fill="x", pady=(4, 0))
        self._build_controls()
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, justify="left", wraplength=1100).pack(anchor="w", pady=6)
        columns = [
            Column("state", "처리", 56), Column("level", "레벨", 80), Column("orig", "원본 구간", 80),
            *STAT_COLUMNS, Column("verdict", "판정", 70),
            Column("note", "비고", 340, "w"), Column("detail", "상세", 90),
        ]
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(bottom, text="최종 결과", command=self.open_final).pack(side="right")
        self._details: list[RowDetail | None] = []  # 삭제된 줄은 None (상세보기 없음)
        self.view = ResultTable(self, columns, on_cell_click=self._on_cell)
        self.view.pack(fill="both", expand=True)

    def _on_cell(self, index: int, column: str) -> None:
        if column == "detail" and 0 <= index < len(self._details) and self._details[index] is not None:
            DetailWindow(self, self._details[index])

    def _follow_cap_row(self, character: str, job: str) -> None:
        self.cap_row.select_character(character)

    def _build_controls(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(8, 0))
        self.picker = PresetPicker(top, self.ctx)
        self.picker.pack(side="left")
        for label, var, high in (("허용 순위(~N순위)", self.allowed, MAX_RANKS), ("시작 레벨", self.start, 999), ("목표 레벨", self.target, 999)):
            ttk.Label(top, text=label).pack(side="left", padx=(12, 2))
            ttk.Spinbox(top, from_=1, to=high, width=5, textvariable=var).pack(side="left")
        ttk.Label(top, text="레벨당 전직변경").pack(side="left", padx=(12, 2))
        for text, value in (("제한없음", "unlimited"), ("제한있음", "limited")):
            ttk.Radiobutton(top, text=text, variable=self.per_level_mode, value=value,
                            command=self._sync_per_level).pack(side="left")
        self.per_level_spin = ttk.Spinbox(top, from_=0, to=999, width=5, textvariable=self.per_level)
        self.per_level_spin.pack(side="left", padx=(2, 0))
        self._sync_per_level()
        mode = ttk.Frame(self)
        mode.pack(fill="x", pady=6)
        ttk.Radiobutton(mode, text="허용 줄이 나올 때까지 순서대로 삭제", variable=self.mode, value="greedy").pack(side="left")
        ttk.Radiobutton(mode, text="전직변경 상한 안에서 허용 줄 최대화", variable=self.mode, value="limited").pack(side="left", padx=12)
        ttk.Label(mode, text="상한(회)").pack(side="left")
        ttk.Spinbox(mode, from_=0, to=999, width=5, textvariable=self.cap).pack(side="left", padx=4)
        ttk.Button(mode, text="계산", command=self.run).pack(side="left", padx=12)

    def _sync_per_level(self) -> None:
        state = "normal" if self.per_level_mode.get() == "limited" else "disabled"
        self.per_level_spin.configure(state=state)

    def refresh(self) -> None:
        self.picker.refresh()
        signature = (self.ctx.table.first_level, len(self.ctx.table))
        if signature != self._signature:  # 테이블이 바뀌면 시작/목표 레벨 기본값 갱신
            self._signature = signature
            self.start.set(signature[0])
            self.target.set(signature[0] + signature[1])

    def _collect(self) -> RunSettings | None:
        """화면 입력을 검증해서 설정 스냅샷을 만든다. 문제가 있으면 안내 후 None."""
        table = self.ctx.table
        if not table.rows:
            messagebox.showinfo("테이블 없음", "테이블이 비어 있어요. 테이블 탭에서 줄을 추가하거나 캐릭터를 불러와 주세요.")
            return None
        try:
            preset = self.picker.get_preset()
            current, caps = self.current_row.stat_values(), self.cap_row.stat_values()
        except ValueError as error:
            messagebox.showwarning("입력 오류", str(error))
            return None
        start, target = _int_of(self.start, 1), _int_of(self.target, 1)
        start_index = table.index_of_level(start)
        if not 0 <= start_index < len(table) or target <= start:
            messagebox.showwarning("입력 오류", "시작/목표 레벨을 확인해 주세요. (시작 레벨은 테이블 범위 안, 목표 > 시작)")
            return None
        return RunSettings(
            rows=list(table.rows), preset=preset, current=current, caps=caps,
            start_index=start_index, start_level=start, level_count=target - start,
            per_level=None if self.per_level_mode.get() == "unlimited" else max(0, _int_of(self.per_level, 0)),
            greedy=self.mode.get() == "greedy", deletion_cap=_int_of(self.cap, 0),
        )

    def run(self) -> None:
        settings = self._collect()
        if settings is None:
            return
        accept = allowed_up_to(_int_of(self.allowed, 1))
        try:
            result = simulate_with(settings, accept)
        except SearchTooLarge:
            return messagebox.showwarning(
                "계산이 너무 커요",
                "상한 스탯이 있을 때 이 모드는 경우의 수가 너무 많아졌어요.\n"
                "'허용 줄이 나올 때까지 순서대로 삭제' 모드를 쓰거나, 목표 레벨 범위·전직변경 상한을 줄여보세요.")
        over = [f"{s.label}({settings.current[s]}/{settings.caps[s]})"
                for s in settings.caps if settings.current.get(s, 0) > settings.caps[s]]
        self._show(result, accept, settings.preset, settings.current, settings.caps, over)

    def open_final(self) -> None:
        settings = self._collect()
        if settings is not None:
            FinalResultWindow(self, settings)

    def _show(self, result: PathResult, accept: Callable[[int], bool], preset: Preset,
              current: dict, caps: dict, over: list[str]) -> None:
        table = self.ctx.table
        infos = annotate(result.outcomes, table.rows, current, caps)
        required = {s for cond in preset.ranks for s in cond.required}
        rows, self._details = [], []
        for outcome, info in zip(result.outcomes, infos):
            stats = table.rows[outcome.row_index]
            notes = []
            if outcome.deleted:
                notes.append("전직변경으로 삭제")
            elif not accept(outcome.rank):
                notes.append("비허용 줄 수령")
            if info.reached:
                notes.append(f"{_names(info.reached)} 상한 도달")
            if info.excluded:
                text = f"{_names(info.excluded)} 상한이라 제외"
                if info.excluded & required:
                    text += " → 필수 조건 영향, 조건 재검토"
                notes.append(text)
            level_text = f"Lv{outcome.level}" if outcome.deleted else f"{outcome.level}→{outcome.level + 1}"
            action = "삭제" if outcome.deleted else "수령"
            verdict = rank_text(outcome.rank)
            rows.append(ResultRow(
                (action, level_text, table.label_of(outcome.row_index),
                 *stat_cells(stats), verdict, " / ".join(notes), "" if outcome.deleted else "▶ 상세보기"),
                outcome.rank, deleted=outcome.deleted,
            ))
            self._details.append(None if outcome.deleted else RowDetail(
                title=f"원본 {table.label_of(outcome.row_index)} │ {action} {level_text} │ 판정 {verdict}",
                notes=" / ".join(notes),
                values=info.stats_after, caps=caps,
                raised=stats - info.excluded,
            ))
        self.view.show(rows)

        by_level = result.deletions_by_level()
        per_level = ", ".join(f"Lv{lv}: {n}회" for lv, n in sorted(by_level.items())) or "없음"
        lines = [
            f"전직변경 총 {result.deletions}회 │ 허용 줄 {result.accepted}/{result.levels_covered}레벨 수령",
            f"레벨별 전직변경: {per_level}",
        ]
        if caps:
            reached = [f"{_names(i.reached)}(Lv{o.level})" for o, i in zip(result.outcomes, infos) if i.reached]
            lines.append("상한 도달: " + (", ".join(reached) if reached else "없음"))
        if caps or current:
            final = infos[-1].stats_after if infos else current
            lines.append("최종 능력치: " + " ".join(
                f"{s.label} {final.get(s, 0)}" + (f"/{caps[s]}" if s in caps else "") for s in ALL_STATS))
        if over:
            lines.append("⚠ 현재 스탯이 상한을 넘는 항목: " + ", ".join(over))
        if result.truncated:
            lines.append("⚠ 테이블 줄이 모자라 목표 레벨까지 도달하지 못했어요.")
        self.summary.set("\n".join(lines))


def _fix_treeview_tag_colors(style: ttk.Style) -> None:
    """Tk 8.6.9 Treeview에서 tag 색상이 적용되지 않는 알려진 버그 우회."""
    def fixed(option: str) -> list:
        return [e for e in style.map("Treeview", query_opt=option) if e[:2] != ("!disabled", "!selected")]

    style.map("Treeview", foreground=fixed("foreground"), background=fixed("background"))


def run() -> None:
    root = tk.Tk()
    root.title("전직변경 경로 계산기")
    root.geometry("1250x880")
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
