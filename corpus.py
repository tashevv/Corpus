import sys
import os
import subprocess
import csv
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
import threading
import sqlite3
import json
import re
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))


# ── CONFIG ────────────────────────────────────────────────────────────────────
DICTIONARY_PATH = os.path.join("data", "words_list.txt")
DB_PATH         = os.path.join("data", "dictionary.db")
FLAGS_PATH      = os.path.join("data", "flagged_ranks.txt")
NO_DEF_PATH     = os.path.join("data", "not_found.txt")

POS_MAP = {
    "n": "Noun",
    "v": "Verb",
    "j": "Adjective",
    "r": "Adverb",
    "p": "Pronoun",
    "d": "Determiner",
    "i": "Preposition",
    "c": "Conjunction",
    "a": "Article",
    "m": "Numeral",
    "u": "Interjection",
    "x": "Unknown",
}

# inverse of POS_MAP
POS_NAME_TO_CODE = {v: k for k, v in POS_MAP.items()}

COLUMN_SORT_MAP = {
    "Rank":  ("rank",       "num"),
    "Lemma": ("lemma",      "str"),
    "PoS":   ("pos_name",   "str"),
    "Freq":  ("freq",       "num"),
    "Disp":  ("dispersion", "num"),
}

LANGUAGES = {
    "Select language": "",
    "Afrikaans": "af", "Albanian": "sq", "Arabic": "ar", "Azerbaijani": "az",
    "Basque": "eu", "Belarusian": "be", "Bengali": "bn", "Bosnian": "bs",
    "Bulgarian": "bg", "Catalan": "ca", "Chinese (Simplified)": "zh-CN",
    "Chinese (Traditional)": "zh-TW", "Croatian": "hr", "Czech": "cs",
    "Danish": "da", "Dutch": "nl", "Esperanto": "eo", "Estonian": "et",
    "Finnish": "fi", "French": "fr", "Galician": "gl", "Georgian": "ka",
    "German": "de", "Greek": "el", "Gujarati": "gu", "Haitian Creole": "ht",
    "Hebrew": "he", "Hindi": "hi", "Hungarian": "hu", "Icelandic": "is",
    "Indonesian": "id", "Irish": "ga", "Italian": "it", "Japanese": "ja",
    "Kannada": "kn", "Kazakh": "kk", "Korean": "ko", "Kurdish": "ku",
    "Kyrgyz": "ky", "Latvian": "lv", "Lithuanian": "lt", "Macedonian": "mk",
    "Malay": "ms", "Maltese": "mt", "Maori": "mi", "Marathi": "mr",
    "Mongolian": "mn", "Nepali": "ne", "Norwegian": "no", "Persian": "fa",
    "Polish": "pl", "Portuguese": "pt", "Punjabi": "pa", "Romanian": "ro",
    "Russian": "ru", "Serbian": "sr", "Sinhalese": "si", "Slovak": "sk",
    "Slovenian": "sl", "Spanish": "es", "Swahili": "sw", "Swedish": "sv",
    "Tajik": "tg", "Tamil": "ta", "Telugu": "te", "Thai": "th",
    "Turkish": "tr", "Ukrainian": "uk", "Urdu": "ur", "Uzbek": "uz",
    "Vietnamese": "vi", "Welsh": "cy", "Xhosa": "xh", "Zulu": "zu",
}


class FlagMode(str, Enum):
    ALL       = "all"
    FLAGGED   = "flagged"
    UNFLAGGED = "unflagged"

class DefMode(str, Enum):
    ALL       = "all"
    DEFINED   = "defined"
    UNDEFINED = "undefined"


# ── DICTIONARY STORE ──────────────────────────────────────────────────────────
class DictionaryStore:
    """Handles loading, filtering, and flagging — no UI dependency."""

    def __init__(self):
        self.entries       = []
        self.flagged_ranks = set()
        self.no_def_lemmas = set()

    def load_dictionary(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Dictionary file not found:\n{path}")

        entries = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f, delimiter="\t")
            for lineno, row in enumerate(reader, 1):
                if len(row) < 5:
                    continue
                rank, lemma, pos, freq, dispersion = row[:5]
                if rank == "----" or rank.lower() == "rank":
                    continue
                try:
                    entries.append({
                        "rank":       int(rank),
                        "lemma":      lemma.strip(),
                        "pos":        pos.strip(),
                        "freq":       int(freq),
                        "dispersion": float(dispersion),
                    })
                except ValueError as exc:
                    print(f"[load_dictionary] skipping malformed row {lineno}: {row!r} ({exc})")

        self.entries = entries
        return entries

    def load_flags(self, path):
        self.flagged_ranks = set()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.flagged_ranks.add(int(line))
                        except ValueError:
                            pass
        except OSError:
            pass

    def save_flags(self, path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                for rank in sorted(self.flagged_ranks):
                    f.write(str(rank) + "\n")
        except OSError as e:
            raise OSError(f"Failed to save flags:\n{e}") from e

    def toggle_flag(self, rank):
        if rank in self.flagged_ranks:
            self.flagged_ranks.discard(rank)
        else:
            self.flagged_ranks.add(rank)

    def apply_filters(self, *, search_field, search_query, range_field, range_min, range_max, pos_filter, flag_mode, def_mode):
        result = self.entries

        if search_query:
            result = self._filter_by_search(result, search_field, search_query)

        field_map = {"Rank": "rank", "Frequency": "freq", "Dispersion": "dispersion"}
        rf = field_map.get(range_field, "rank")
        if range_min is not None or range_max is not None:
            result = [
                e for e in result
                if (range_min is None or e[rf] >= range_min)
                and (range_max is None or e[rf] <= range_max)
            ]

        if pos_filter != "All":
            code = POS_NAME_TO_CODE.get(pos_filter)
            if code:
                result = [e for e in result if e["pos"].lower() == code]

        if flag_mode == FlagMode.FLAGGED:
            result = [e for e in result if e["rank"] in self.flagged_ranks]
        elif flag_mode == FlagMode.UNFLAGGED:
            result = [e for e in result if e["rank"] not in self.flagged_ranks]

        if def_mode == DefMode.DEFINED:
            result = [
                e for e in result
                if e["lemma"].lower() not in self.no_def_lemmas
            ]
        elif def_mode == DefMode.UNDEFINED:
            result = [
                e for e in result
                if e["lemma"].lower() in self.no_def_lemmas
            ]

        return result

    def _filter_by_search(self, entries, field, query):
        q_lower  = query.lower()
        q_is_num = self._is_numeric(query)

        def match_rank(e):  return str(e["rank"]).startswith(query)
        def match_lemma(e):  return q_lower in e["lemma"].lower()
        def match_pos(e):
            display = POS_MAP.get(e["pos"].lower(), e["pos"]).lower()
            return q_lower in display or q_lower == e["pos"].lower()
        def match_freq(e):  return str(e["freq"]).startswith(query)
        def match_disp(e):  return str(round(e["dispersion"], 2)).startswith(query)
        def match_all(e):
            return (
                match_lemma(e)
                or match_pos(e)
                or (q_is_num and (match_rank(e) or match_freq(e) or match_disp(e)))
            )

        dispatch = {
            "All":            match_all,
            "Rank":           match_rank,
            "Lemma":           match_lemma,
            "Part of Speech": match_pos,
            "Frequency":      match_freq,
            "Dispersion":     match_disp,
        }
        fn = dispatch.get(field, match_all)
        return [e for e in entries if fn(e)]

    @staticmethod
    def _is_numeric(s):
        try:
            float(s)
            return True
        except (ValueError, TypeError):
            return False


# ── DB LOOKUP ─────────────────────────────────────────────────────────────────
def fetch_word_data(word):
    """Return (data_dict, None) or (None, error_string). Safe to call off-thread."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            cur.execute("SELECT json FROM entries WHERE word = ? COLLATE NOCASE", (word,))
            row = cur.fetchone()

        if row is None:
            return None, f"'{word}' not found in the local dictionary."

        data  = json.loads(row[0])
        entry = data[0]

        phonetic = entry.get("phonetic", "")
        if not phonetic:
            for ph in entry.get("phonetics", []):
                if ph.get("text"):
                    phonetic = ph["text"]
                    break

        return {"phonetic": phonetic, "meanings": entry.get("meanings", [])}, None

    except sqlite3.Error as e:
        return None, f"Database error: {e}"
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        return None, f"Failed to parse entry: {e}"


# ── NO-DEF LOADER ─────────────────────────────────────────────────────────────
def load_no_def_lemmas(path):
    """Return a set of lowercase lemmas that have no definition."""
    no_def = set()
    if not os.path.exists(path):
        return no_def
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 3 and parts[2].strip() == "NOT_FOUND":
                    no_def.add(parts[1].strip().lower())
    except OSError:
        pass
    return no_def


# ── THEME ─────────────────────────────────────────────────────────────────────
COLORS = {
    "bg":           "#0a0e14",
    "surface":      "#0d1117",
    "surface2":     "#161b22",
    "border":       "#2d3a44",
    "accent":       "#4a90a4",
    "accent_dim":   "#3a7a8e",
    "accent_text":  "#8ea8b8",
    "flag_bg":      "#0d1f2d",
    "flag_text":    "#6896ae",
    "text_primary": "#e6edf3",
    "text_muted":   "#9aacba",
    "text_hint":    "#4a6278",
    "success":      "#3fb950",
    "danger":       "#f85149",
    "entry_bg":     "#0d1117",
    "entry_focus":  "#4a90a4",
    "btn_bg":       "#161b22",
    "btn_hover":    "#1e2d40",
    "treeselect":   "#0c2d48",
    "tag_bg":       "#0f2535",
    "tag_border":   "#2a4a60",
    "tag_text":     "#6896ae",
    "tag_x":        "#4a6a80",
    "no_def_text":  "#c0504a",
}

FONT_MONO   = ("Segoe UI", 10)
FONT_BODY   = ("Segoe UI", 10)
FONT_BODY_S = ("Segoe UI", 9)
FONT_TITLE  = ("Segoe UI Semibold", 22)
FONT_META   = ("Segoe UI", 10)
FONT_DEF    = ("Segoe UI Semibold", 14)
FONT_LABEL  = ("Segoe UI", 9)
FONT_STATUS = ("Segoe UI", 9)


# ── THEMED WIDGET MIXINS ──────────────────────────────────────────────────────
class ThemedWidgets:
    """Factory methods for consistently styled widgets. Mixed into CorpusApp."""

    def _entry(self, parent, textvariable=None, **kw):
        return tk.Entry(
            parent,
            bg=COLORS["entry_bg"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["treeselect"],
            selectforeground=COLORS["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            font=FONT_BODY,
            textvariable=textvariable,
            **kw,
        )

    def _label_on(self, parent, bg, text, muted=False, **kw):
        color = COLORS["text_muted"] if muted else COLORS["text_primary"]
        return tk.Label(parent, text=text, bg=bg, fg=color, font=FONT_LABEL, **kw)

    def _button(self, parent, text, command, accent=False, **kw):
        bg = COLORS["accent"] if accent else COLORS["btn_bg"]
        fg = "#fff" if accent else COLORS["text_primary"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=COLORS["accent_dim"] if accent else COLORS["btn_hover"],
            activeforeground="#fff" if accent else COLORS["text_primary"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=FONT_BODY,
            padx=12,
            pady=5,
            **kw,
        )

    def _combobox(self, parent, values, textvariable=None, width=14, **kw):
        return ttk.Combobox(
            parent,
            values=values,
            state="readonly",
            width=width,
            style="Dark.TCombobox",
            textvariable=textvariable,
            **kw,
        )


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _strip_flag_indicator(label: str) -> str:
    """Remove the ' ⚑' suffix added to flagged lemmas in the treeview."""
    return label[:-2] if label.endswith(" ⚑") else label


# ── MAIN APP ──────────────────────────────────────────────────────────────────
class CorpusApp(ThemedWidgets):

    def __init__(self, root):
        self.root  = root
        self.store = DictionaryStore()
        self._last_definition_data = None

        try:
            self.root.iconbitmap("icon.ico")
        except tk.TclError:
            pass

        self.root.title("Corpus")
        self.root.geometry("1080x540")
        self.root.minsize(1080, 540)
        self.root.configure(bg=COLORS["bg"])

        self.current_word  = ""
        self.sort_col      = "Rank"
        self.sort_asc      = True

        # FIX #5: generation counter replaces bare thread reference.
        # Each word selection increments _fetch_gen; the callback checks its
        # captured value against the current one before touching the UI, so
        # a slow response from an earlier selection is silently discarded.
        self._fetch_gen    = 0

        # FIX #8: debounce handle for filter keystrokes
        self._filter_job   = None

        # Filter state
        self.search_field  = tk.StringVar(value="All")
        self.search_text   = tk.StringVar()
        self.range_field   = tk.StringVar(value="Rank")
        self.range_min_var = tk.StringVar()
        self.range_max_var = tk.StringVar()
        self.pos_filter    = tk.StringVar(value="All")
        self.flag_mode     = tk.StringVar(value=FlagMode.ALL)
        self.def_mode      = tk.StringVar(value=DefMode.ALL)

        self._apply_theme()
        self.build_ui()
        self.store.load_flags(FLAGS_PATH)
        self.no_def_lemmas = load_no_def_lemmas(NO_DEF_PATH)
        self.store.no_def_lemmas = self.no_def_lemmas
        self.load_data()

    # ── THEME ─────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(
            "Corpus.Treeview",
            background=COLORS["surface"],
            foreground=COLORS["text_primary"],
            fieldbackground=COLORS["surface"],
            rowheight=26,
            borderwidth=0,
            relief="flat",
            font=FONT_BODY,
        )
        style.configure(
            "Corpus.Treeview.Heading",
            background=COLORS["surface2"],
            foreground=COLORS["text_muted"],
            borderwidth=0,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padding=(6, 5),
        )
        style.map(
            "Corpus.Treeview",
            background=[("selected", COLORS["treeselect"])],
            foreground=[("selected", COLORS["accent_text"])],
        )
        style.map(
            "Corpus.Treeview.Heading",
            background=[("active", COLORS["surface2"])],
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=COLORS["entry_bg"],
            background=COLORS["surface2"],
            foreground=COLORS["text_primary"],
            selectbackground=COLORS["treeselect"],
            selectforeground=COLORS["accent_text"],
            bordercolor=COLORS["border"],
            arrowcolor=COLORS["text_muted"],
            relief="flat",
            padding=(8, 4),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", COLORS["entry_bg"])],
            foreground=[("readonly", COLORS["text_primary"])],
            bordercolor=[("focus", COLORS["accent"])],
        )

    # ── FLAGS ─────────────────────────────────────────────────────────────────
    def toggle_flag(self):
        selected = self.tree.selection()
        if not selected:
            return
        try:
            rank = int(self.tree.item(selected[0])["values"][0])
        except (IndexError, ValueError):
            return

        self.store.toggle_flag(rank)
        try:
            self.store.save_flags(FLAGS_PATH)
        except OSError as e:
            messagebox.showerror("Save Error", str(e))

        self.filter_entries()
        self.update_flag_button()

    def update_flag_button(self):
        selected = self.tree.selection()
        if not selected:
            self.flag_button.config(text="Toggle Flag")
            return
        try:
            rank = int(self.tree.item(selected[0])["values"][0])
        except (IndexError, ValueError):
            self.flag_button.config(text="Toggle Flag")
            return

        self.flag_button.config(
            text="⚑  Unflag Lemma" if rank in self.store.flagged_ranks else "⚐  Flag Lemma"
        )

    # ── SORTING ───────────────────────────────────────────────────────────────
    def _on_heading_click(self, col):
        if self.sort_col == col:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = col
            self.sort_asc = True
        self._update_headings()
        self.filter_entries()

    def _update_headings(self):
        labels = {
            "Rank":  "Rank",
            "Lemma": "Lemma",
            "PoS":   "Part of Speech",
            "Freq":  "Frequency",
            "Disp":  "Dispersion",
        }
        for col, base in labels.items():
            indicator = (" ▲" if self.sort_asc else " ▼") if col == self.sort_col else ""
            self.tree.heading(col, text=base + indicator)

    # ── BUILD UI ──────────────────────────────────────────────────────────────
    def build_ui(self):
        GAP = 6
        root_frame = tk.Frame(self.root, bg=COLORS["bg"], padx=12, pady=12)
        root_frame.pack(fill="both", expand=True)

        # LEFT PANEL ──────────────────────────────────────────────────────────
        left_panel = tk.Frame(root_frame, bg=COLORS["bg"], width=600)
        left_panel.pack(side="left", fill="y")
        left_panel.pack_propagate(False)

        # Row 1: Search
        row1 = tk.Frame(left_panel, bg=COLORS["bg"])
        row1.pack(fill="x", pady=(0, GAP))

        self._label_on(row1, COLORS["bg"], "Search", muted=True).pack(side="left", padx=(0, 4))

        field_cb = self._combobox(
            row1,
            ["All", "Rank", "Lemma", "Part of Speech", "Frequency", "Dispersion"],
            textvariable=self.search_field,
            width=15,
        )
        field_cb.pack(side="left", padx=(0, 6))
        field_cb.bind("<<ComboboxSelected>>", self.filter_entries)

        self.search_entry = self._entry(row1, textvariable=self.search_text, width=30)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", self.filter_entries)

        # Row 2: Numeric range + PoS
        row3 = tk.Frame(left_panel, bg=COLORS["bg"])
        row3.pack(fill="x", pady=(0, GAP))

        self._label_on(row3, COLORS["bg"], "Filter", muted=True).pack(side="left", padx=(0, 4))

        range_cb = self._combobox(
            row3,
            ["Rank", "Frequency", "Dispersion"],
            textvariable=self.range_field,
            width=16,
        )
        range_cb.pack(side="left", padx=(0, 6))
        range_cb.bind("<<ComboboxSelected>>", self.filter_entries)

        self._label_on(row3, COLORS["bg"], "Min", muted=True).pack(side="left", padx=(0, 4))
        self.filter_min = self._entry(row3, textvariable=self.range_min_var, width=15)
        self.filter_min.pack(side="left", padx=(0, 8))
        self.filter_min.bind("<KeyRelease>", self.filter_entries)

        self._label_on(row3, COLORS["bg"], "Max", muted=True).pack(side="left", padx=(0, 4))
        self.filter_max = self._entry(row3, textvariable=self.range_max_var, width=15)
        self.filter_max.pack(side="left")
        self.filter_max.bind("<KeyRelease>", self.filter_entries)

        self._label_on(row3, COLORS["bg"], "PoS", muted=True).pack(side="left", padx=(16, 4))
        self.pos_cb = self._combobox(
            row3,
            ["All"] + list(POS_MAP.values()),
            textvariable=self.pos_filter,
            width=10,
        )
        self.pos_cb.pack(side="right")
        self.pos_cb.bind("<<ComboboxSelected>>", self.filter_entries)

        # Row 3: Flag mode / Def mode
        row5 = tk.Frame(left_panel, bg=COLORS["bg"])
        row5.pack(fill="x", pady=(0, GAP))

        self._label_on(row5, COLORS["bg"], "Flags", muted=True).pack(side="left", padx=(0, 4))

        for value, label in [
            (FlagMode.ALL,       "All"),
            (FlagMode.FLAGGED,   "Flagged"),
            (FlagMode.UNFLAGGED, "Unflagged"),
        ]:
            tk.Radiobutton(
                row5,
                text=label,
                variable=self.flag_mode,
                value=value,
                command=self.filter_entries,
                bg=COLORS["bg"],
                fg=COLORS["text_muted"],
                activebackground=COLORS["bg"],
                activeforeground=COLORS["accent_text"],
                selectcolor=COLORS["surface2"],
                highlightthickness=0,
                bd=0,
                font=FONT_BODY_S,
                cursor="hand2",
            ).pack(side="left", padx=(0, 10))

        row6 = tk.Frame(left_panel, bg=COLORS["bg"])
        row6.pack(fill="x", pady=(0, GAP))

        self._label_on(row6, COLORS["bg"], "Definitions", muted=True).pack(side="left", padx=(0, 4))

        for value, label in [
            (DefMode.ALL, "All"),
            (DefMode.DEFINED, "Defined"),
            (DefMode.UNDEFINED, "Undefined"),
        ]:
            tk.Radiobutton(
                row6,
                text=label,
                variable=self.def_mode,
                value=value,
                command=self.filter_entries,
                bg=COLORS["bg"],
                fg=COLORS["text_muted"],
                activebackground=COLORS["bg"],
                activeforeground=COLORS["accent_text"],
                selectcolor=COLORS["surface2"],
                highlightthickness=0,
                bd=0,
                font=FONT_BODY_S,
                cursor="hand2",
            ).pack(side="left", padx=(0, 10))

        # Row 4: Status + Reset
        row7 = tk.Frame(left_panel, bg=COLORS["bg"])
        row7.pack(fill="x", pady=(0, GAP))

        self._button(row7, "Reset filters", self._reset_filters).pack(side="left")

        self.status_label = tk.Label(
            row7,
            text="Loading…",
            bg=COLORS["bg"],
            fg=COLORS["text_hint"],
            font=FONT_STATUS,
        )
        self.status_label.pack(side="right")

        # Treeview
        tree_outer = tk.Frame(left_panel, bg=COLORS["border"], bd=1, relief="flat")
        tree_outer.pack(fill="both", expand=True)

        tree_frame = tk.Frame(tree_outer, bg=COLORS["surface"])
        tree_frame.pack(fill="both", expand=True, padx=1, pady=1)

        columns = ("Rank", "Lemma", "PoS", "Freq", "Disp")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", style="Corpus.Treeview"
        )

        self.tree.heading("Rank",  text="Rank ▲",        command=lambda: self._on_heading_click("Rank"))
        self.tree.heading("Lemma", text="Lemma",           command=lambda: self._on_heading_click("Lemma"))
        self.tree.heading("PoS",   text="Part of Speech", command=lambda: self._on_heading_click("PoS"))
        self.tree.heading("Freq",  text="Frequency",      command=lambda: self._on_heading_click("Freq"))
        self.tree.heading("Disp",  text="Dispersion",     command=lambda: self._on_heading_click("Disp"))

        self.tree.column("Rank",  width=58,  anchor="center", minwidth=40)
        self.tree.column("Lemma", width=130,                  minwidth=80)
        self.tree.column("PoS",   width=100, anchor="center", minwidth=70)
        self.tree.column("Freq",  width=90,  anchor="e",      minwidth=60)
        self.tree.column("Disp",  width=80,  anchor="center", minwidth=50)

        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)

        self.tree.tag_configure(
            "no_def", foreground=COLORS["no_def_text"]
        )

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_select_word)

        # Divider
        tk.Frame(root_frame, bg=COLORS["border"], width=1).pack(
            side="left", fill="y", padx=(10, 0)
        )

        # RIGHT PANEL ─────────────────────────────────────────────────────────
        right_panel = tk.Frame(root_frame, bg=COLORS["bg"], padx=16, pady=4)
        right_panel.pack(side="right", fill="both", expand=True)

        # Configure grid: definition gets weight, translation is fixed
        right_panel.grid_rowconfigure(6, weight=7)  # definition box
        right_panel.grid_rowconfigure(8, weight=3)  # translation box
        right_panel.grid_columnconfigure(0, weight=1)

        title_frame = tk.Frame(right_panel, bg=COLORS["bg"])
        title_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.word_title = tk.Label(
            title_frame, text="Select a word", font=FONT_TITLE,
            bg=COLORS["bg"], fg=COLORS["text_primary"],
        )
        self.word_title.pack(side="left")

        self.phonetic_label = tk.Label(
            title_frame, text="", font=("Segoe UI", 13),
            bg=COLORS["bg"], fg=COLORS["accent_text"],
        )
        self.phonetic_label.pack(side="left", padx=(14, 0))

        self.google_link = tk.Label(
            title_frame, text="[↗]", font=FONT_BODY_S,
            bg=COLORS["bg"], fg=COLORS["accent_text"],
            cursor="hand2",
        )
        self.google_link.pack(side="left", padx=(10, 0))
        self.google_link.bind("<Button-1>", self._open_google)
        self.google_link.bind(
            "<Enter>",
            lambda e: self.google_link.config(
                font=(*FONT_BODY_S[:1], FONT_BODY_S[1], "underline")
            ),
        )
        self.google_link.bind("<Leave>", lambda e: self.google_link.config(font=FONT_BODY_S))

        self.speak_button = tk.Button(
            title_frame, text="🔊", command=self.speak_word,
            bg=COLORS["btn_bg"], fg=COLORS["text_primary"],
            activebackground=COLORS["btn_hover"], activeforeground=COLORS["text_primary"],
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=("Segoe UI", 13), padx=8, pady=3,
        )
        self.speak_button.pack(side="left", padx=(10, 0))

        self.meta_label = tk.Label(
            right_panel, text="", font=FONT_META,
            bg=COLORS["bg"], fg=COLORS["text_muted"], justify="left",
        )
        self.meta_label.grid(row=1, column=0, sticky="w", pady=(0, 10))

        self.flag_button = self._button(
            right_panel, "☆  Flag Word", self.toggle_flag, accent=False, width=16,
        )
        self.flag_button.grid(row=2, column=0, sticky="w", pady=(0, 12))

        tk.Frame(right_panel, bg=COLORS["border"], height=1).grid(
            row=3, column=0, sticky="ew", pady=(0, 12)
        )

        # Definition box
        definition_frame = tk.Frame(right_panel, bg=COLORS["border"], bd=1, relief="flat")
        definition_frame.grid(row=6, column=0, sticky="nsew")
        definition_frame.grid_rowconfigure(0, weight=1)
        definition_frame.grid_columnconfigure(0, weight=1)

        self.definition_box = tk.Text(
            definition_frame,
            wrap="word",
            font=FONT_DEF,
            bg=COLORS["surface"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["treeselect"],
            selectforeground=COLORS["accent_text"],
            bd=0,
            highlightthickness=0,
            spacing2=4, spacing3=2,
            height=1,
            relief="flat",
            padx=12, pady=8,
            state="disabled",
        )
        self.definition_box.grid(row=0, column=0, sticky="nsew")

        self.definition_box.tag_configure(
            "pos_header",
            font=("Segoe UI", 12, "bold"), foreground="#7ab0cc",
            spacing1=10, spacing3=4,
        )
        self.definition_box.tag_configure(
            "def_num", font=("Segoe UI", 11), foreground=COLORS["text_hint"]
        )
        self.definition_box.tag_configure(
            "def_text", font=("Segoe UI", 13), foreground=COLORS["text_primary"]
        )
        self.definition_box.tag_configure(
            "example",
            font=("Segoe UI", 11, "italic"), foreground=COLORS["text_muted"],
            lmargin1=20, lmargin2=20, spacing1=2,
        )
        self.definition_box.tag_configure(
            "syn_label", font=("Segoe UI", 10, "bold"), foreground="#6896ae"
        )
        self.definition_box.tag_configure(
            "syn_val", font=("Segoe UI", 10), foreground="#6896ae"
        )
        self.definition_box.tag_configure(
            "error", font=("Segoe UI", 11), foreground=COLORS["danger"]
        )
        self.definition_box.tag_configure(
            "loading", font=("Segoe UI", 11), foreground=COLORS["text_hint"]
        )

        definition_scrollbar = ttk.Scrollbar(
            definition_frame, orient="vertical", command=self.definition_box.yview
        )
        self.definition_box.configure(yscrollcommand=definition_scrollbar.set)
        definition_scrollbar.grid(row=0, column=1, sticky="ns")

        # ── TRANSLATION ───────────────────────────────────────────────────────
        trans_header = tk.Frame(right_panel, bg=COLORS["bg"])
        trans_header.grid(row=7, column=0, sticky="ew", pady=(10, 4))

        tk.Label(
            trans_header, text="Translation", font=("Segoe UI Semibold", 10),
            bg=COLORS["bg"], fg=COLORS["text_muted"],
        ).pack(side="left", padx=(0, 10))

        self.lang_var = tk.StringVar(value="Select language")
        lang_cb = ttk.Combobox(
            trans_header,
            values=list(LANGUAGES.keys()),
            textvariable=self.lang_var,
            state="readonly",
            width=22,
            style="Dark.TCombobox",
        )
        lang_cb.pack(side="left")
        lang_cb.bind("<<ComboboxSelected>>", self._on_language_select)

        trans_outer = tk.Frame(right_panel, bg=COLORS["border"], bd=1, relief="flat")
        trans_outer.grid(row=8, column=0, sticky="nsew", pady=(0, 4))
        trans_outer.grid_rowconfigure(0, weight=1)
        trans_outer.grid_columnconfigure(0, weight=1)

        trans_inner = tk.Frame(trans_outer, bg=COLORS["surface"])
        trans_inner.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        trans_inner.grid_rowconfigure(0, weight=1)
        trans_inner.grid_columnconfigure(0, weight=1)

        self.translation_box = tk.Text(
            trans_inner,
            wrap="word",
            font=("Segoe UI", 11),
            bg=COLORS["surface"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["treeselect"],
            selectforeground=COLORS["accent_text"],
            relief="flat", bd=0,
            highlightthickness=0,
            padx=12, pady=8,
            state="disabled",
            height=1,
        )
        self.translation_box.grid(row=0, column=0, sticky="nsew")

        self.translation_box.tag_configure(
            "trans_text", font=("Segoe UI", 11), foreground=COLORS["text_primary"]
        )
        self.translation_box.tag_configure(
            "loading", font=("Segoe UI", 11), foreground=COLORS["text_hint"]
        )
        self.translation_box.tag_configure(
            "error", font=("Segoe UI", 11), foreground=COLORS["danger"]
        )
        self.translation_box.tag_configure(
            "pos_header",
            font=("Segoe UI", 12, "bold"), foreground="#7ab0cc",
            spacing1=10, spacing3=4,
        )
        self.translation_box.tag_configure(
            "def_num", font=("Segoe UI", 11), foreground=COLORS["text_hint"]
        )
        self.translation_box.tag_configure(
            "def_text", font=("Segoe UI", 13), foreground=COLORS["text_primary"]
        )
        self.translation_box.tag_configure(
            "example",
            font=("Segoe UI", 11, "italic"), foreground=COLORS["text_muted"],
            lmargin1=20, lmargin2=20, spacing1=2,
        )
        self.translation_box.tag_configure(
            "syn_label", font=("Segoe UI", 10, "bold"), foreground="#6896ae"
        )
        self.translation_box.tag_configure(
            "syn_val", font=("Segoe UI", 10), foreground="#6896ae"
        )

        trans_scroll = ttk.Scrollbar(
            trans_inner, orient="vertical", command=self.translation_box.yview
        )
        self.translation_box.configure(yscrollcommand=trans_scroll.set)
        trans_scroll.grid(row=0, column=1, sticky="ns")

    # ── RESET ─────────────────────────────────────────────────────────────────
    def _reset_filters(self, refilter=True):
        self.search_text.set("")
        self.search_field.set("All")
        self.range_field.set("Rank")
        self.range_min_var.set("")
        self.range_max_var.set("")
        self.pos_filter.set("All")
        self.flag_mode.set(FlagMode.ALL)
        self.def_mode.set(DefMode.ALL)

        self.sort_col = "Rank"
        self.sort_asc = True
        self._update_headings()

        if hasattr(self, "preset_cb") and self.preset_cb is not None:
            try:
                self.preset_cb.current(0)
            except tk.TclError:
                pass

        if refilter:
            self.filter_entries()

    # ── LOAD DATA ─────────────────────────────────────────────────────────────
    def load_data(self):
        try:
            self.store.load_dictionary(DICTIONARY_PATH)
            self.populate_tree()
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    # ── POPULATE TREE ─────────────────────────────────────────────────────────
    def populate_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        filtered = self._collect_filters()
        sort_field, sort_type = COLUMN_SORT_MAP[self.sort_col]

        def sort_key(entry):
            if sort_field == "pos_name":
                return POS_MAP.get(entry["pos"].lower(), entry["pos"]).lower()
            if sort_field == "has_def":
                return 0 if entry["lemma"].lower() in self.no_def_lemmas else 1
            if sort_type == "str":
                return entry[sort_field].lower()
            return entry[sort_field]

        filtered.sort(key=sort_key, reverse=not self.sort_asc)

        for entry in filtered:
            pos_name  = POS_MAP.get(entry["pos"].lower(), entry["pos"])
            is_flagged = entry["rank"] in self.store.flagged_ranks
            is_no_def  = entry["lemma"].lower() in self.no_def_lemmas

            lemma_display = f"{entry['lemma']} ⚑" if is_flagged else entry["lemma"]

            tags = []
            if is_flagged:
                tags.append("flagged")
            if is_no_def:
                tags.append("no_def")
            tags = tuple(tags)

            self.tree.insert(
                "", "end",
                values=(
                    entry["rank"],
                    lemma_display,
                    pos_name,
                    f"{entry['freq']:,}",
                    f"{entry['dispersion']:.2f}",
                ),
                tags=tags,
            )

        shown_count   = len(filtered)
        shown_flagged = sum(1 for e in filtered if e["rank"] in self.store.flagged_ranks)
        flagged_pct   = (shown_flagged / shown_count * 100) if shown_count else 0

        shown_defined = sum(1 for e in filtered if e["lemma"].lower() not in self.store.no_def_lemmas)
        defined_pct = (shown_defined / shown_count * 100) if shown_count else 0

        self.status_label.config(
            text=(
                f"{shown_count:,} Results  ·  "
                f"{shown_flagged:,} Flagged ({flagged_pct:.1f}%)  ·  "
                f"{shown_defined:,} Defined ({defined_pct:.1f}%)"
            )
        )

    def _collect_filters(self):
        def _try_float(s):
            try:
                return float(s) if s.strip() else None
            except ValueError:
                return None

        return self.store.apply_filters(
            search_field=self.search_field.get(),
            search_query=self.search_text.get().strip(),
            range_field=self.range_field.get(),
            range_min=_try_float(self.range_min_var.get()),
            range_max=_try_float(self.range_max_var.get()),
            pos_filter=self.pos_filter.get(),
            flag_mode=self.flag_mode.get(),
            def_mode=self.def_mode.get(),
        )

    # FIX #8: debounce filter — cancels any pending redraw and schedules a new
    # one 120 ms later, so rapid keystrokes collapse into a single tree rebuild.
    def filter_entries(self, event=None):
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(120, self._run_filter)

    def _run_filter(self):
        self._filter_job = None
        self.populate_tree()

    # ── SELECT WORD ───────────────────────────────────────────────────────────
    # FIX #3: extract word cleanly via helper; unpack values explicitly.
    # FIX #5: increment generation counter so any in-flight fetch for a
    # previous word will see a stale gen and discard its result.
    def on_select_word(self, event):
        selected = self.tree.selection()
        if not selected:
            return

        values     = self.tree.item(selected[0])["values"]
        rank       = values[0]
        word       = _strip_flag_indicator(values[1])
        pos        = values[2]
        freq       = values[3]
        dispersion = values[4]

        self.current_word = word
        self._fetch_gen  += 1
        gen = self._fetch_gen  # capture for closure

        if self.lang_var.get() != "Select language":
            self.translation_box.config(state="normal")
            self.translation_box.delete("1.0", tk.END)
            self.translation_box.insert(tk.END, "Translating…", "loading")
            self.translation_box.config(state="disabled")

        self.word_title.config(text=word)
        self.phonetic_label.config(text="")
        self.phonetic_label.pack_configure(padx=(0, 0))
        self.meta_label.config(
            text=f"Rank {rank}   ·   {pos}   ·   Freq {freq}   ·   Dispersion {dispersion}"
        )

        self._set_definition_text("Loading…", "loading")
        self.update_flag_button()

        def do_fetch():
            result, error = fetch_word_data(word)
            # Only update the UI if this fetch is still the most recent one.
            if gen == self._fetch_gen:
                self.root.after(0, lambda: self._render_definition(result, error))

        threading.Thread(target=do_fetch, daemon=True).start()

    def _set_definition_text(self, text, tag="def_text"):
        self.definition_box.config(state="normal")
        self.definition_box.delete("1.0", tk.END)
        self.definition_box.insert(tk.END, text, tag)
        self.definition_box.config(state="disabled")

    # FIX #5: word-equality guard removed — generation counter is sufficient
    # and more robust (handles rapid re-selection of the same word correctly).
    def _render_definition(self, result, error):
        self.definition_box.config(state="normal")
        self.definition_box.delete("1.0", tk.END)

        if error:
            self._last_definition_data = None
            self.definition_box.insert(tk.END, error, "error")
        else:
            self._last_definition_data = result
            ph = result.get("phonetic", "")
            if ph:
                self.phonetic_label.config(text=ph)
                self.phonetic_label.pack_configure(padx=(14, 0))
            else:
                self.phonetic_label.config(text="")
                self.phonetic_label.pack_configure(padx=(0, 0))

            meanings = result.get("meanings", [])
            for m_idx, meaning in enumerate(meanings):
                part = meaning.get("partOfSpeech", "")
                syns = meaning.get("synonyms", [])
                ants = meaning.get("antonyms", [])

                if m_idx > 0:
                    self.definition_box.insert(tk.END, "\n")
                self.definition_box.insert(tk.END, f"{part.upper()}\n", "pos_header")

                for i, d in enumerate(meaning.get("definitions", [])[:8], 1):
                    definition = d.get("definition", "")
                    example    = d.get("example", "")
                    d_syns     = d.get("synonyms", [])

                    self.definition_box.insert(tk.END, f"{i}. ", "def_num")
                    self.definition_box.insert(tk.END, definition + "\n", "def_text")
                    if example:
                        self.definition_box.insert(tk.END, f"  ↳ {example}\n", "example")
                    if d_syns:
                        self.definition_box.insert(tk.END, "  syn: ", "syn_label")
                        self.definition_box.insert(tk.END, ", ".join(d_syns[:6]) + "\n", "syn_val")

                if syns:
                    self.definition_box.insert(tk.END, "\nSynonyms: ", "syn_label")
                    self.definition_box.insert(tk.END, ", ".join(syns[:10]) + "\n", "syn_val")
                if ants:
                    self.definition_box.insert(tk.END, "Antonyms: ", "syn_label")
                    self.definition_box.insert(tk.END, ", ".join(ants[:10]) + "\n", "syn_val")

        self.definition_box.config(state="disabled")
        if self.lang_var.get() != "Select language":
            self.root.after(0, self._on_language_select)

    # ── SPEAK WORD ────────────────────────────────────────────────────────────
    def speak_word(self):
        if not self.current_word:
            return

        safe_word = re.sub(r"[^\w\s'-]", "", self.current_word)
        if not safe_word:
            return

        def _speak():
            try:
                ps_script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$s.Speak('{safe_word}')"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                pass
            except Exception as e:
                self.root.after(
                    0,
                    lambda: messagebox.showerror("Speech Error", f"Failed to vocalize word:\n{e}"),
                )

        threading.Thread(target=_speak, daemon=True).start()

    # ── OPEN GOOGLE ───────────────────────────────────────────────────────────
    def _open_google(self, event=None):
        if self.current_word:
            webbrowser.open(f"https://www.google.com/search?q={self.current_word}")

    # ── TRANSLATE ─────────────────────────────────────────────────────────────────
    def _on_language_select(self, event=None):
        lang_name = self.lang_var.get()
        if not lang_name or lang_name == "Select language":
            self.translation_box.config(state="normal")
            self.translation_box.delete("1.0", tk.END)
            self.translation_box.config(state="disabled")
            return
        lang_code = LANGUAGES.get(lang_name, "")
        if not lang_code:
            return

        if not self._last_definition_data:
            # No structured data — translate the raw error text directly
            raw = self.definition_box.get("1.0", tk.END).strip()
            if not raw:
                return
            self.translation_box.config(state="normal")
            self.translation_box.delete("1.0", tk.END)
            self.translation_box.insert(tk.END, "Translating…", "loading")
            self.translation_box.config(state="disabled")

            def do_translate_raw():
                import urllib.request, urllib.parse
                try:
                    params = urllib.parse.urlencode({
                        "client": "gtx", "sl": "en", "tl": lang_code, "dt": "t", "q": raw,
                    })
                    url = f"https://translate.googleapis.com/translate_a/single?{params}"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())
                    result = "".join(seg[0] for seg in data[0] if seg[0])
                    err = None
                except Exception as e:
                    result = None
                    err = str(e)

                def update():
                    self.translation_box.config(state="normal")
                    self.translation_box.delete("1.0", tk.END)
                    if result is not None:
                        self.translation_box.insert(tk.END, result, "trans_text")
                    else:
                        self.translation_box.insert(tk.END, f"Translation failed: {err}", "error")
                    self.translation_box.config(state="disabled")

                self.root.after(0, update)

            threading.Thread(target=do_translate_raw, daemon=True).start()
            return

        self.translation_box.config(state="normal")
        self.translation_box.delete("1.0", tk.END)
        self.translation_box.insert(tk.END, "Translating…", "loading")
        self.translation_box.config(state="disabled")

        data = self._last_definition_data

        def _translate_text(text):
            import urllib.request, urllib.parse
            if not text.strip():
                return text
            params = urllib.parse.urlencode({
                "client": "gtx", "sl": "en", "tl": lang_code, "dt": "t", "q": text,
            })
            url = f"https://translate.googleapis.com/translate_a/single?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read().decode())
            return "".join(seg[0] for seg in d[0] if seg[0])

        def do_translate():
            try:
                # Build a list of (text, tag) pairs mirroring _render_definition
                segments = []
                meanings = data.get("meanings", [])
                for m_idx, meaning in enumerate(meanings):
                    part = meaning.get("partOfSpeech", "")
                    syns = meaning.get("synonyms", [])
                    ants = meaning.get("antonyms", [])

                    if m_idx > 0:
                        segments.append(("\n", "def_text"))
                    segments.append((f"{part.upper()}\n", "pos_header"))

                    for i, d in enumerate(meaning.get("definitions", [])[:8], 1):
                        definition = d.get("definition", "")
                        example    = d.get("example", "")
                        d_syns     = d.get("synonyms", [])

                        segments.append((f"{i}. ", "def_num"))
                        segments.append((_translate_text(definition) + "\n", "def_text"))
                        if example:
                            segments.append((f"  ↳ {_translate_text(example)}\n", "example"))
                        if d_syns:
                            segments.append(("  syn: ", "syn_label"))
                            segments.append((", ".join(d_syns[:6]) + "\n", "syn_val"))

                    if syns:
                        segments.append(("\nSynonyms: ", "syn_label"))
                        segments.append((", ".join(syns[:10]) + "\n", "syn_val"))
                    if ants:
                        segments.append(("Antonyms: ", "syn_label"))
                        segments.append((", ".join(ants[:10]) + "\n", "syn_val"))

                err = None
            except Exception as e:
                segments = None
                err = str(e)

            def update():
                self.translation_box.config(state="normal")
                self.translation_box.delete("1.0", tk.END)
                if segments is not None:
                    for text, tag in segments:
                        self.translation_box.insert(tk.END, text, tag)
                else:
                    self.translation_box.insert(tk.END, f"Translation failed: {err}", "error")
                self.translation_box.config(state="disabled")

            self.root.after(0, update)

        threading.Thread(target=do_translate, daemon=True).start()


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("corpus.app")
    except AttributeError:
        pass

    root = tk.Tk()
    app  = CorpusApp(root)
    root.mainloop()
