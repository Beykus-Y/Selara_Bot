from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


RARITY_OPTIONS = ("common", "rare", "epic", "legendary", "mythic")
THEME_OPTIONS = ("light", "dark")
ELEMENT_OPTIONS = ("", "hydro", "electro", "pyro", "cryo", "anemo", "dendro", "geo", "unknown")
REGION_OPTIONS = (
    "",
    "mondstadt",
    "liyue",
    "inazuma",
    "sumeru",
    "fontaine",
    "natlan",
    "nod_krai",
    "snezhnaya",
    "khaenriah",
    "unknown",
)
CARD_FIELDS = (
    "code",
    "name",
    "rarity",
    "points",
    "primogems",
    "adventure_xp",
    "image_url",
    "region_code",
    "element_code",
    "weight",
)


class BannerEditorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Selara Gacha Banner Editor")
        self.root.geometry("1500x880")

        self.project_dir = Path(__file__).resolve().parents[1]
        self.default_config_dir = self.project_dir / "config" / "banners"
        self.current_path: Path | None = None
        self.banner_data: dict[str, object] = {
            "code": "",
            "title": "",
            "cooldown_seconds": 3600,
            "cards": [],
        }
        self.selected_index: int | None = None
        self._suspend_card_trace = False

        self.banner_vars = {
            "code": tk.StringVar(),
            "title": tk.StringVar(),
            "cooldown_seconds": tk.StringVar(value="3600"),
        }
        self.card_vars = {field: tk.StringVar() for field in CARD_FIELDS}
        self.theme_var = tk.StringVar(value="light")
        self.status_var = tk.StringVar(value="Откройте banner JSON для редактирования.")
        self.summary_var = tk.StringVar()
        self.rarity_summary_var = tk.StringVar()
        self.selected_chance_var = tk.StringVar(value="Шанс карты: -")

        self._build_ui()
        self._bind_events()

        default_file = self.default_config_dir / "genshin.json"
        if default_file.exists():
            self.load_file(default_file)
        else:
            self.refresh_all()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=12)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(7, weight=1)

        ttk.Button(header, text="Открыть JSON", command=self.open_file_dialog).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(header, text="Сохранить", command=self.save).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(header, text="Сохранить как", command=self.save_as).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header, text="Новый баннер", command=self.new_banner).grid(row=0, column=3, padx=(0, 8))
        ttk.Label(header, text="Тема").grid(row=0, column=4, padx=(8, 6), sticky="e")
        ttk.Combobox(header, textvariable=self.theme_var, values=THEME_OPTIONS, state="readonly", width=10).grid(
            row=0,
            column=5,
            sticky="w",
        )
        ttk.Button(header, text="Применить тему", command=self.apply_theme).grid(row=0, column=6, padx=(8, 0))
        ttk.Label(header, textvariable=self.status_var).grid(row=0, column=7, sticky="w")

        left = ttk.Frame(self.root, padding=(12, 0, 6, 12))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        banner_box = ttk.LabelFrame(left, text="Баннер", padding=12)
        banner_box.grid(row=0, column=0, sticky="ew")
        for idx in range(6):
            banner_box.columnconfigure(idx, weight=1 if idx % 2 else 0)

        ttk.Label(banner_box, text="code").grid(row=0, column=0, sticky="w")
        ttk.Entry(banner_box, textvariable=self.banner_vars["code"]).grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(banner_box, text="title").grid(row=0, column=2, sticky="w")
        ttk.Entry(banner_box, textvariable=self.banner_vars["title"]).grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ttk.Label(banner_box, text="cooldown_seconds").grid(row=0, column=4, sticky="w")
        ttk.Entry(banner_box, textvariable=self.banner_vars["cooldown_seconds"], width=10).grid(row=0, column=5, sticky="ew")

        actions = ttk.Frame(left, padding=(0, 12, 0, 12))
        actions.grid(row=1, column=0, sticky="ew")
        for idx in range(8):
            actions.columnconfigure(idx, weight=1)
        ttk.Button(actions, text="Добавить", command=self.add_card).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ttk.Button(actions, text="Дублировать", command=self.duplicate_card).grid(row=0, column=1, padx=(0, 6), sticky="ew")
        ttk.Button(actions, text="Удалить", command=self.delete_card).grid(row=0, column=2, padx=(0, 6), sticky="ew")
        ttk.Button(actions, text="Вверх", command=lambda: self.move_card(-1)).grid(row=0, column=3, padx=(0, 6), sticky="ew")
        ttk.Button(actions, text="Вниз", command=lambda: self.move_card(1)).grid(row=0, column=4, padx=(0, 6), sticky="ew")
        ttk.Button(actions, text="Сорт. по имени", command=lambda: self.sort_cards("name")).grid(row=0, column=5, padx=(0, 6), sticky="ew")
        ttk.Button(actions, text="Сорт. по редкости", command=lambda: self.sort_cards("rarity")).grid(row=0, column=6, padx=(0, 6), sticky="ew")
        ttk.Button(actions, text="Пересчитать", command=self.refresh_all).grid(row=0, column=7, sticky="ew")

        table_box = ttk.LabelFrame(left, text="Карты и шансы", padding=8)
        table_box.grid(row=2, column=0, sticky="nsew")
        table_box.rowconfigure(0, weight=1)
        table_box.columnconfigure(0, weight=1)

        columns = ("idx", "code", "name", "rarity", "weight", "chance", "region", "element")
        self.tree = ttk.Treeview(table_box, columns=columns, show="headings", height=24)
        headings = {
            "idx": "#",
            "code": "code",
            "name": "name",
            "rarity": "rarity",
            "weight": "weight",
            "chance": "chance %",
            "region": "region",
            "element": "element",
        }
        widths = {
            "idx": 40,
            "code": 150,
            "name": 220,
            "rarity": 90,
            "weight": 80,
            "chance": 90,
            "region": 110,
            "element": 90,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(table_box, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        right = ttk.Frame(self.root, padding=(6, 0, 12, 12))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)
        right.rowconfigure(3, weight=1)

        editor_box = ttk.LabelFrame(right, text="Выбранная карта", padding=12)
        editor_box.grid(row=0, column=0, sticky="ew")
        editor_box.columnconfigure(1, weight=1)
        editor_box.columnconfigure(3, weight=1)

        row = 0
        self._add_entry(editor_box, row, "code", "code")
        self._add_entry(editor_box, row, "name", "name", col_offset=2)
        row += 1
        self._add_combo(editor_box, row, "rarity", "rarity", RARITY_OPTIONS)
        self._add_entry(editor_box, row, "weight", "weight", col_offset=2)
        row += 1
        self._add_entry(editor_box, row, "points", "points")
        self._add_entry(editor_box, row, "primogems", "primogems", col_offset=2)
        row += 1
        self._add_entry(editor_box, row, "adventure_xp", "adventure_xp")
        self._add_entry(editor_box, row, "image_url", "image_url", col_offset=2)
        row += 1
        self._add_combo(editor_box, row, "region_code", "region_code", REGION_OPTIONS)
        self._add_combo(editor_box, row, "element_code", "element_code", ELEMENT_OPTIONS, col_offset=2)

        ttk.Label(editor_box, textvariable=self.selected_chance_var).grid(row=row + 1, column=0, columnspan=4, sticky="w", pady=(10, 0))

        summary_box = ttk.LabelFrame(right, text="Сводка баннера", padding=12)
        summary_box.grid(row=1, column=0, sticky="nsew", pady=(12, 12))
        summary_box.columnconfigure(0, weight=1)
        summary_box.rowconfigure(1, weight=1)
        ttk.Label(summary_box, textvariable=self.summary_var, justify="left").grid(row=0, column=0, sticky="nw")

        self.summary_text = tk.Text(summary_box, height=14, wrap="word")
        self.summary_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.summary_text.configure(state="disabled")

        rarity_box = ttk.LabelFrame(right, text="Шансы по редкости", padding=12)
        rarity_box.grid(row=2, column=0, sticky="nsew")
        rarity_box.columnconfigure(0, weight=1)
        rarity_box.rowconfigure(0, weight=1)
        ttk.Label(rarity_box, textvariable=self.rarity_summary_var, justify="left").grid(row=0, column=0, sticky="nw")

        duplicates_box = ttk.LabelFrame(right, text="Дубликаты image_url", padding=12)
        duplicates_box.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        duplicates_box.columnconfigure(0, weight=1)
        duplicates_box.rowconfigure(0, weight=1)

        self.duplicate_images_text = tk.Text(duplicates_box, height=10, wrap="word")
        self.duplicate_images_text.grid(row=0, column=0, sticky="nsew")
        self.duplicate_images_text.configure(state="disabled")

    def _add_entry(
        self,
        parent: ttk.LabelFrame,
        row: int,
        field: str,
        label: str,
        *,
        col_offset: int = 0,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col_offset, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=self.card_vars[field]).grid(row=row, column=col_offset + 1, sticky="ew", pady=3, padx=(6, 12))

    def _add_combo(
        self,
        parent: ttk.LabelFrame,
        row: int,
        field: str,
        label: str,
        values: tuple[str, ...],
        *,
        col_offset: int = 0,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col_offset, sticky="w", pady=3)
        ttk.Combobox(parent, textvariable=self.card_vars[field], values=values).grid(
            row=row,
            column=col_offset + 1,
            sticky="ew",
            pady=3,
            padx=(6, 12),
        )

    def _bind_events(self) -> None:
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        for variable in self.banner_vars.values():
            variable.trace_add("write", self.on_banner_change)
        for variable in self.card_vars.values():
            variable.trace_add("write", self.on_card_change)
        self.theme_var.trace_add("write", self.on_theme_change)

    def on_theme_change(self, *_args) -> None:
        self.apply_theme()

    def apply_theme(self) -> None:
        theme_name = self.theme_var.get().strip().lower() or "light"
        if theme_name == "dark":
            self._apply_dark_palette()
        else:
            self._apply_light_palette()

    def _apply_light_palette(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        background = "#f5f6fa"
        panel = "#ffffff"
        text = "#1f2937"
        muted = "#4b5563"
        accent = "#2563eb"
        self._configure_palette(
            background=background,
            panel=panel,
            text=text,
            muted=muted,
            accent=accent,
            input_bg="#ffffff",
            input_fg=text,
            selection="#dbeafe",
        )

    def _apply_dark_palette(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        background = "#111827"
        panel = "#1f2937"
        text = "#f9fafb"
        muted = "#cbd5e1"
        accent = "#38bdf8"
        self._configure_palette(
            background=background,
            panel=panel,
            text=text,
            muted=muted,
            accent=accent,
            input_bg="#0f172a",
            input_fg=text,
            selection="#1d4ed8",
        )

    def _configure_palette(
        self,
        *,
        background: str,
        panel: str,
        text: str,
        muted: str,
        accent: str,
        input_bg: str,
        input_fg: str,
        selection: str,
    ) -> None:
        style = ttk.Style(self.root)
        style.configure(".", background=background, foreground=text)
        style.configure("TFrame", background=background)
        style.configure("TLabel", background=background, foreground=text)
        style.configure("TLabelframe", background=background, foreground=text)
        style.configure("TLabelframe.Label", background=background, foreground=text)
        style.configure("TButton", background=panel, foreground=text, padding=6)
        style.map("TButton", background=[("active", accent)], foreground=[("active", "#ffffff")])
        style.configure("TEntry", fieldbackground=input_bg, foreground=input_fg)
        style.configure("TCombobox", fieldbackground=input_bg, foreground=input_fg, background=panel)
        style.map("TCombobox", fieldbackground=[("readonly", input_bg)], foreground=[("readonly", input_fg)])
        style.configure(
            "Treeview",
            background=input_bg,
            foreground=input_fg,
            fieldbackground=input_bg,
            bordercolor=panel,
            lightcolor=panel,
            darkcolor=panel,
        )
        style.configure("Treeview.Heading", background=panel, foreground=text)
        style.map("Treeview", background=[("selected", selection)], foreground=[("selected", "#ffffff")])
        self.root.configure(bg=background)
        for widget in (self.summary_text, self.duplicate_images_text):
            widget.configure(
                bg=input_bg,
                fg=input_fg,
                insertbackground=input_fg,
                selectbackground=selection,
                selectforeground="#ffffff",
                highlightbackground=panel,
                highlightcolor=accent,
            )
        self.status_var.set(f"Тема: {'тёмная' if self.theme_var.get() == 'dark' else 'светлая'}")

    def open_file_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Открыть banner JSON",
            initialdir=self.default_config_dir,
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("Ошибка загрузки", str(exc))
            return

        self.current_path = path
        self.banner_data = {
            "code": str(payload.get("code", "")),
            "title": str(payload.get("title", "")),
            "cooldown_seconds": payload.get("cooldown_seconds", 3600),
            "cards": [self._normalize_card(card) for card in payload.get("cards", [])],
        }
        self.selected_index = 0 if self.banner_data["cards"] else None
        self.refresh_all()
        self.status_var.set(f"Загружен {path}")

    def new_banner(self) -> None:
        self.current_path = None
        self.banner_data = {
            "code": "new_banner",
            "title": "New Banner",
            "cooldown_seconds": 3600,
            "cards": [self._normalize_card({})],
        }
        self.selected_index = 0
        self.refresh_all()
        self.status_var.set("Создан новый баннер в памяти. Сохраните его в JSON.")

    def _normalize_card(self, card: dict[str, object]) -> dict[str, object]:
        normalized = {
            "code": str(card.get("code", "")),
            "name": str(card.get("name", "")),
            "rarity": str(card.get("rarity", "common")) or "common",
            "points": self._to_int(card.get("points", 0), default=0),
            "primogems": self._to_int(card.get("primogems", 0), default=0),
            "adventure_xp": self._to_int(card.get("adventure_xp", 0), default=0),
            "image_url": str(card.get("image_url", "")),
            "region_code": self._clean_optional_text(card.get("region_code")),
            "element_code": self._clean_optional_text(card.get("element_code")),
            "weight": self._to_float(card.get("weight", 1), default=1.0),
        }
        if normalized["rarity"] not in RARITY_OPTIONS:
            normalized["rarity"] = "common"
        return normalized

    def on_banner_change(self, *_args) -> None:
        self.banner_data["code"] = self.banner_vars["code"].get().strip()
        self.banner_data["title"] = self.banner_vars["title"].get().strip()
        self.banner_data["cooldown_seconds"] = self._to_int(self.banner_vars["cooldown_seconds"].get(), default=0)
        self.refresh_summary()

    def on_card_change(self, *_args) -> None:
        if self._suspend_card_trace or self.selected_index is None:
            return
        cards = self.banner_data["cards"]
        if not isinstance(cards, list) or self.selected_index >= len(cards):
            return
        cards[self.selected_index] = self._read_card_form()
        self.refresh_table()
        self.refresh_summary()

    def _read_card_form(self) -> dict[str, object]:
        return {
            "code": self.card_vars["code"].get().strip(),
            "name": self.card_vars["name"].get().strip(),
            "rarity": self.card_vars["rarity"].get().strip() or "common",
            "points": self._to_int(self.card_vars["points"].get(), default=0),
            "primogems": self._to_int(self.card_vars["primogems"].get(), default=0),
            "adventure_xp": self._to_int(self.card_vars["adventure_xp"].get(), default=0),
            "image_url": self.card_vars["image_url"].get().strip(),
            "region_code": self._clean_optional_text(self.card_vars["region_code"].get()),
            "element_code": self._clean_optional_text(self.card_vars["element_code"].get()),
            "weight": self._to_float(self.card_vars["weight"].get(), default=1.0),
        }

    def add_card(self) -> None:
        cards = self.banner_data["cards"]
        if not isinstance(cards, list):
            return
        suffix = len(cards) + 1
        cards.append(
            self._normalize_card(
                {
                    "code": f"card_{suffix}",
                    "name": f"Новая карта {suffix}",
                    "rarity": "common",
                    "weight": 1,
                }
            )
        )
        self.selected_index = len(cards) - 1
        self.refresh_all()

    def duplicate_card(self) -> None:
        if self.selected_index is None:
            return
        cards = self.banner_data["cards"]
        if not isinstance(cards, list) or self.selected_index >= len(cards):
            return
        cloned = deepcopy(cards[self.selected_index])
        cloned["code"] = f"{cloned['code']}_copy"
        cloned["name"] = f"{cloned['name']} copy"
        cards.insert(self.selected_index + 1, cloned)
        self.selected_index += 1
        self.refresh_all()

    def delete_card(self) -> None:
        if self.selected_index is None:
            return
        cards = self.banner_data["cards"]
        if not isinstance(cards, list) or self.selected_index >= len(cards):
            return
        del cards[self.selected_index]
        if not cards:
            self.selected_index = None
        else:
            self.selected_index = min(self.selected_index, len(cards) - 1)
        self.refresh_all()

    def move_card(self, offset: int) -> None:
        if self.selected_index is None:
            return
        cards = self.banner_data["cards"]
        if not isinstance(cards, list):
            return
        target = self.selected_index + offset
        if target < 0 or target >= len(cards):
            return
        cards[self.selected_index], cards[target] = cards[target], cards[self.selected_index]
        self.selected_index = target
        self.refresh_all()

    def sort_cards(self, mode: str) -> None:
        cards = self.banner_data["cards"]
        if not isinstance(cards, list):
            return
        selected_code = None
        if self.selected_index is not None and self.selected_index < len(cards):
            selected_code = str(cards[self.selected_index].get("code", ""))
        if mode == "rarity":
            order = {rarity: index for index, rarity in enumerate(RARITY_OPTIONS)}
            cards.sort(key=lambda card: (order.get(str(card.get("rarity", "common")), 0), str(card.get("name", "")).lower()))
        else:
            cards.sort(key=lambda card: str(card.get(mode, "")).lower())
        self.selected_index = self._find_card_index_by_code(selected_code)
        self.refresh_all()

    def on_tree_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        item_id = selection[0]
        try:
            self.selected_index = int(item_id)
        except ValueError:
            return
        self.load_selected_card_into_form()
        self.refresh_summary()

    def load_selected_card_into_form(self) -> None:
        cards = self.banner_data["cards"]
        self._suspend_card_trace = True
        try:
            if self.selected_index is None or not isinstance(cards, list) or self.selected_index >= len(cards):
                for variable in self.card_vars.values():
                    variable.set("")
                self.selected_chance_var.set("Шанс карты: -")
                return
            card = cards[self.selected_index]
            for field in CARD_FIELDS:
                value = card.get(field, "")
                if field in {"points", "primogems", "adventure_xp"}:
                    self.card_vars[field].set(str(int(value)))
                elif field == "weight":
                    self.card_vars[field].set(self._format_number(float(value)))
                else:
                    self.card_vars[field].set("" if value is None else str(value))
        finally:
            self._suspend_card_trace = False
        self.selected_chance_var.set(self._build_selected_card_chance_text())

    def refresh_all(self) -> None:
        self.banner_vars["code"].set(str(self.banner_data.get("code", "")))
        self.banner_vars["title"].set(str(self.banner_data.get("title", "")))
        self.banner_vars["cooldown_seconds"].set(str(self.banner_data.get("cooldown_seconds", 3600)))
        self.refresh_table()
        self.load_selected_card_into_form()
        self.refresh_summary()

    def refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        cards = self.banner_data["cards"]
        if not isinstance(cards, list):
            return
        total_weight = sum(max(0.0, float(card.get("weight", 0) or 0)) for card in cards)
        for index, card in enumerate(cards):
            weight = max(0.0, float(card.get("weight", 0) or 0))
            chance = (weight / total_weight * 100.0) if total_weight > 0 else 0.0
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    index + 1,
                    card.get("code", ""),
                    card.get("name", ""),
                    card.get("rarity", ""),
                    self._format_number(weight),
                    f"{chance:.2f}",
                    card.get("region_code", "") or "",
                    card.get("element_code", "") or "",
                ),
            )
        if self.selected_index is not None and self.selected_index < len(cards):
            self.tree.selection_set(str(self.selected_index))
            self.tree.focus(str(self.selected_index))

    def refresh_summary(self) -> None:
        cards = self.banner_data["cards"]
        if not isinstance(cards, list):
            return
        total_weight = sum(max(0.0, float(card.get("weight", 0) or 0)) for card in cards)
        unique_codes = len({str(card.get("code", "")).strip() for card in cards if str(card.get("code", "")).strip()})
        duplicates = self._find_duplicate_codes(cards)
        duplicate_images = self._find_duplicate_images(cards)
        missing_images = [str(card.get("code", "")) for card in cards if not str(card.get("image_url", "")).strip()]
        missing_image_files = self._find_missing_image_files(cards)
        genshin_missing_meta = []
        if str(self.banner_data.get("code", "")).strip() == "genshin":
            genshin_missing_meta = [
                str(card.get("code", ""))
                for card in cards
                if not str(card.get("region_code", "") or "").strip() or not str(card.get("element_code", "") or "").strip()
            ]

        self.summary_var.set(
            "\n".join(
                [
                    f"Файл: {self.current_path or 'ещё не сохранён'}",
                    f"Карт: {len(cards)}",
                    f"Уникальных code: {unique_codes}",
                    f"Суммарный weight: {self._format_number(total_weight)}",
                    f"Дубликаты code: {', '.join(duplicates) if duplicates else 'нет'}",
                    f"Дубликаты image_url: {len(duplicate_images)}",
                    f"Пустой image_url: {', '.join(missing_images[:8]) if missing_images else 'нет'}",
                    f"Файлы image_url не найдены: {len(missing_image_files)}",
                    f"Genshin без region/element: {', '.join(genshin_missing_meta[:8]) if genshin_missing_meta else 'нет'}",
                ]
            )
        )

        rarity_weights: dict[str, float] = defaultdict(float)
        element_weights: dict[str, float] = defaultdict(float)
        top_cards: list[tuple[str, float]] = []
        for card in cards:
            rarity = str(card.get("rarity", "common"))
            weight = max(0.0, float(card.get("weight", 0) or 0))
            rarity_weights[rarity] += weight
            element = str(card.get("element_code", "") or "none")
            element_weights[element] += weight
            top_cards.append((str(card.get("code", "")), weight))

        rarity_lines = []
        for rarity in RARITY_OPTIONS:
            weight = rarity_weights.get(rarity, 0.0)
            chance = (weight / total_weight * 100.0) if total_weight > 0 else 0.0
            rarity_lines.append(f"{rarity}: {self._format_number(weight)} ({chance:.2f}%)")
        self.rarity_summary_var.set("\n".join(rarity_lines))

        top_cards.sort(key=lambda item: item[1], reverse=True)
        summary_lines = ["Топ карт по весу:"]
        for code, weight in top_cards[:10]:
            chance = (weight / total_weight * 100.0) if total_weight > 0 else 0.0
            summary_lines.append(f"- {code or '<без code>'}: {self._format_number(weight)} ({chance:.2f}%)")
        summary_lines.append("")
        summary_lines.append("Сумма по element_code:")
        for element, weight in sorted(element_weights.items(), key=lambda item: (-item[1], item[0])):
            chance = (weight / total_weight * 100.0) if total_weight > 0 else 0.0
            summary_lines.append(f"- {element}: {self._format_number(weight)} ({chance:.2f}%)")

        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", "\n".join(summary_lines))
        self.summary_text.configure(state="disabled")

        duplicate_lines = []
        if duplicate_images:
            duplicate_lines.append("Дубликаты путей:")
            for image_url, codes in duplicate_images:
                duplicate_lines.append(image_url)
                duplicate_lines.append(f"  коды: {', '.join(codes)}")
        else:
            duplicate_lines.append("Дубликатов image_url нет.")
        duplicate_lines.append("")
        if missing_image_files:
            duplicate_lines.append("Файлы не найдены:")
            for code, image_url in missing_image_files:
                duplicate_lines.append(f"- {code}: {image_url}")
        else:
            duplicate_lines.append("Все локальные image_url найдены.")
        self.duplicate_images_text.configure(state="normal")
        self.duplicate_images_text.delete("1.0", "end")
        self.duplicate_images_text.insert("1.0", "\n".join(duplicate_lines))
        self.duplicate_images_text.configure(state="disabled")
        self.selected_chance_var.set(self._build_selected_card_chance_text())

    def _build_selected_card_chance_text(self) -> str:
        cards = self.banner_data["cards"]
        if self.selected_index is None or not isinstance(cards, list) or self.selected_index >= len(cards):
            return "Шанс карты: -"
        card = cards[self.selected_index]
        total_weight = sum(max(0.0, float(item.get("weight", 0) or 0)) for item in cards)
        card_weight = max(0.0, float(card.get("weight", 0) or 0))
        card_chance = (card_weight / total_weight * 100.0) if total_weight > 0 else 0.0
        rarity = str(card.get("rarity", "common"))
        rarity_weight = sum(
            max(0.0, float(item.get("weight", 0) or 0))
            for item in cards
            if str(item.get("rarity", "common")) == rarity
        )
        rarity_chance = (rarity_weight / total_weight * 100.0) if total_weight > 0 else 0.0
        return (
            f"Шанс карты: {card_chance:.3f}% | "
            f"Шанс редкости {rarity}: {rarity_chance:.3f}% | "
            f"weight: {self._format_number(card_weight)} / {self._format_number(total_weight)}"
        )

    def save(self) -> None:
        if self.current_path is None:
            self.save_as()
            return
        self._save_to_path(self.current_path)

    def save_as(self) -> None:
        initial_name = f"{self.banner_vars['code'].get().strip() or 'banner'}.json"
        path = filedialog.asksaveasfilename(
            title="Сохранить banner JSON",
            initialdir=self.default_config_dir,
            initialfile=initial_name,
            defaultextension=".json",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if path:
            self._save_to_path(Path(path))

    def _save_to_path(self, path: Path) -> None:
        try:
            payload = self._build_payload_for_save()
        except ValueError as exc:
            messagebox.showerror("Ошибка валидации", str(exc))
            return

        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))
            return

        self.current_path = path
        self.status_var.set(f"Сохранено в {path}")
        self.refresh_summary()

    def _build_payload_for_save(self) -> dict[str, object]:
        banner_code = self.banner_vars["code"].get().strip()
        title = self.banner_vars["title"].get().strip()
        cooldown_seconds = self._to_int(self.banner_vars["cooldown_seconds"].get(), default=-1)
        if not banner_code:
            raise ValueError("У баннера должен быть code.")
        if not title:
            raise ValueError("У баннера должен быть title.")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds должен быть больше 0.")

        cards = self.banner_data["cards"]
        if not isinstance(cards, list) or not cards:
            raise ValueError("В баннере должна быть хотя бы одна карта.")

        normalized_cards = []
        seen_codes: set[str] = set()
        for index, raw_card in enumerate(cards, start=1):
            card = self._normalize_card(raw_card)
            if not card["code"]:
                raise ValueError(f"Карта #{index} должна иметь code.")
            if card["code"] in seen_codes:
                raise ValueError(f"Дублирующийся code карты: {card['code']}")
            seen_codes.add(str(card["code"]))
            if not card["name"]:
                raise ValueError(f"Карта {card['code']} должна иметь name.")
            if str(card["rarity"]) not in RARITY_OPTIONS:
                raise ValueError(f"Карта {card['code']} имеет недопустимую rarity.")
            if float(card["weight"]) <= 0:
                raise ValueError(f"Карта {card['code']} должна иметь weight > 0.")
            if banner_code == "genshin":
                if not str(card["region_code"] or "").strip():
                    raise ValueError(f"Для genshin карта {card['code']} должна иметь region_code.")
                if not str(card["element_code"] or "").strip():
                    raise ValueError(f"Для genshin карта {card['code']} должна иметь element_code.")
            payload_card = {
                "code": card["code"],
                "name": card["name"],
                "rarity": card["rarity"],
                "points": int(card["points"]),
                "primogems": int(card["primogems"]),
                "adventure_xp": int(card["adventure_xp"]),
                "image_url": card["image_url"],
                "weight": float(card["weight"]),
            }
            if str(card["region_code"] or "").strip():
                payload_card["region_code"] = str(card["region_code"]).strip()
            if str(card["element_code"] or "").strip():
                payload_card["element_code"] = str(card["element_code"]).strip()
            normalized_cards.append(payload_card)

        return {
            "code": banner_code,
            "title": title,
            "cooldown_seconds": cooldown_seconds,
            "cards": normalized_cards,
        }

    def _find_card_index_by_code(self, code: str | None) -> int | None:
        if not code:
            return 0 if self.banner_data["cards"] else None
        cards = self.banner_data["cards"]
        if not isinstance(cards, list):
            return None
        for index, card in enumerate(cards):
            if str(card.get("code", "")) == code:
                return index
        return 0 if cards else None

    @staticmethod
    def _find_duplicate_codes(cards: list[dict[str, object]]) -> list[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for card in cards:
            code = str(card.get("code", "")).strip()
            if not code:
                continue
            if code in seen:
                duplicates.add(code)
            seen.add(code)
        return sorted(duplicates)

    @staticmethod
    def _find_duplicate_images(cards: list[dict[str, object]]) -> list[tuple[str, list[str]]]:
        by_image: dict[str, list[str]] = defaultdict(list)
        for card in cards:
            image_url = str(card.get("image_url", "")).strip()
            if not image_url:
                continue
            by_image[image_url].append(str(card.get("code", "")).strip() or "<без code>")
        duplicates = [
            (image_url, codes)
            for image_url, codes in sorted(by_image.items())
            if len(codes) > 1
        ]
        return duplicates

    def _find_missing_image_files(self, cards: list[dict[str, object]]) -> list[tuple[str, str]]:
        missing: list[tuple[str, str]] = []
        for card in cards:
            image_url = str(card.get("image_url", "")).strip()
            if not image_url or image_url.startswith("http://") or image_url.startswith("https://"):
                continue
            relative_path = image_url.lstrip("/")
            image_path = self.project_dir / relative_path
            if not image_path.exists():
                code = str(card.get("code", "")).strip() or "<без code>"
                missing.append((code, image_url))
        return missing

    @staticmethod
    def _to_int(value: object, *, default: int) -> int:
        if value in ("", None):
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value: object, *, default: float) -> float:
        if value in ("", None):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clean_optional_text(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _format_number(value: float) -> str:
        if value.is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")


def main() -> None:
    root = tk.Tk()
    app = BannerEditorApp(root)
    app.apply_theme()
    root.minsize(1280, 760)
    root.mainloop()


if __name__ == "__main__":
    main()
