import csv
import json
import os
import queue
import re
import shutil
import sys
import threading
import tkinter as tk
from contextlib import redirect_stderr, redirect_stdout
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

# exe로 패키징된 경우 번들된 Playwright 브라우저 경로를 자동 설정
# (playwright import 전에 실행되어야 함)
if getattr(sys, "frozen", False):
    _browsers_path = os.path.join(os.path.dirname(sys.executable), "browsers")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _browsers_path)

import main as app_main


def get_app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir():
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_base_dir()
RESOURCE_DIR = get_resource_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
INPUT_PATH = os.path.join(APP_DIR, "input.csv")
OUTPUT_DIR = os.path.join(APP_DIR, "output")
ICON_PATH = os.path.join(RESOURCE_DIR, "app_icon.ico")
ICON_PATH_ALT = os.path.join(APP_DIR, "icon.ico")
CONFIG_TEMPLATE_PATH = os.path.join(RESOURCE_DIR, "config.json")
INPUT_TEMPLATE_PATH = os.path.join(RESOURCE_DIR, "input.csv")
COLUMNS = ("업체명", "키워드", "식별값")
VALID_BROWSER_MODES = {"auto", "chrome", "chromium"}
FONT_FAMILY = "맑은 고딕"
RESULT_COMPANY_TAGS = (
    ("summary_company_1", "#86EFAC"),
    ("summary_company_2", "#93C5FD"),
    ("summary_company_3", "#F9A8D4"),
    ("summary_company_4", "#FBBF24"),
    ("summary_company_5", "#67E8F9"),
    ("summary_company_6", "#C4B5FD"),
)

F = FONT_FAMILY  # 축약


def ensure_runtime_file(target_path, template_path):
    if os.path.exists(target_path):
        return
    if not template_path or not os.path.exists(template_path):
        return
    if os.path.abspath(target_path) == os.path.abspath(template_path):
        return

    try:
        shutil.copy2(template_path, target_path)
    except Exception:
        pass


def ensure_runtime_files():
    ensure_runtime_file(CONFIG_PATH, CONFIG_TEMPLATE_PATH)
    ensure_runtime_file(INPUT_PATH, INPUT_TEMPLATE_PATH)


def load_json(path):
    if not os.path.exists(path):
        return {}
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with open(path, "r", encoding=encoding) as file:
                return json.load(file)
        except UnicodeDecodeError:
            continue
        except Exception:
            return {}
    return {}


def read_rows():
    if not os.path.exists(INPUT_PATH):
        return []
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with open(INPUT_PATH, "r", encoding=encoding, newline="") as file:
                reader = csv.DictReader(file)
                return [
                    {
                        "업체명": str(row.get("업체명", "")).strip(),
                        "키워드": str(row.get("키워드", "")).strip(),
                        "식별값": str(row.get("식별값", "")).strip(),
                    }
                    for row in reader
                ]
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def write_rows(rows):
    with open(INPUT_PATH, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})


class QueueWriter:
    def __init__(self, log_queue, token):
        self.log_queue = log_queue
        self.token = token
        self.buffer = ""

    def write(self, text):
        if not text:
            return 0
        self.buffer += str(text)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.log_queue.put({"type": "stdout", "token": self.token, "text": line.rstrip()})
        return len(text)

    def flush(self):
        if self.buffer:
            self.log_queue.put({"type": "stdout", "token": self.token, "text": self.buffer.rstrip()})
            self.buffer = ""


class AddRowDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_confirm):
        super().__init__(parent)
        self.on_confirm = on_confirm
        self.title("행 추가")
        self.geometry("560x230")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        for icon_path in (ICON_PATH, ICON_PATH_ALT):
            if os.path.exists(icon_path):
                try:
                    self.iconbitmap(icon_path)
                    break
                except Exception:
                    pass

        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text="업체명 *", font=("맑은 고딕", 14)).grid(
            row=0, column=0, padx=(28, 12), pady=(26, 12), sticky="e"
        )
        self.company_entry = ctk.CTkEntry(self, width=320, placeholder_text="예: 최용석행정사사무소", font=("맑은 고딕", 13))
        self.company_entry.grid(row=0, column=1, padx=(0, 28), pady=(26, 12), sticky="ew")
        ctk.CTkLabel(self, text="키워드", font=("맑은 고딕", 14)).grid(
            row=1, column=0, padx=(28, 12), pady=12, sticky="e"
        )
        self.keyword_entry = ctk.CTkEntry(self, width=320, placeholder_text="예: 여성기업 신청", font=("맑은 고딕", 13))
        self.keyword_entry.grid(row=1, column=1, padx=(0, 28), pady=12, sticky="ew")
        ctk.CTkLabel(self, text="식별값", font=("맑은 고딕", 14)).grid(
            row=2, column=0, padx=(28, 12), pady=12, sticky="e"
        )
        self.identifier_entry = ctk.CTkEntry(self, width=320, placeholder_text="비우면 업체명으로 자동 설정", font=("맑은 고딕", 13))
        self.identifier_entry.grid(row=2, column=1, padx=(0, 28), pady=12, sticky="ew")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=3, column=0, columnspan=2, pady=(20, 18))
        ctk.CTkButton(
            button_frame,
            text="확인",
            width=90,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            font=("맑은 고딕", 13),
            command=self.confirm,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            button_frame,
            text="취소",
            width=90,
            fg_color="#E5E7EB",
            text_color="#111827",
            hover_color="#D1D5DB",
            font=("맑은 고딕", 13),
            command=self.cancel,
        ).pack(side="left", padx=8)

        self.bind("<Return>", lambda _event: self.confirm())
        self.bind("<Escape>", lambda _event: self.cancel())
        self.after(100, self.company_entry.focus_set)

    def confirm(self):
        company = self.company_entry.get().strip()
        keyword = self.keyword_entry.get().strip()
        identifier = self.identifier_entry.get().strip() or company
        if not company:
            messagebox.showwarning("확인 필요", "업체명은 비워둘 수 없습니다.", parent=self)
            return
        self.on_confirm({"업체명": company, "키워드": keyword, "식별값": identifier})
        self.destroy()

    def cancel(self):
        self.destroy()


class MatchResultsDialog(ctk.CTkToplevel):
    COLUMNS = ("업체명", "키워드", "화면", "API", "매칭값", "상태")

    def __init__(self, parent):
        super().__init__(parent)

        self.title("매칭 결과 확인")
        self.geometry("980x520")
        self.minsize(860, 420)
        self.configure(fg_color="#F3F4F6")
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.summary_var = tk.StringVar(value="총 0 | 진행 0/0 | 노출 0 | 미노출 0 | 오류 0")
        self.status_text_var = tk.StringVar(value="현재 상태: 대기 중")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="매칭 결과 확인",
            font=(F, 22),
            text_color="#111827",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="검색 로그를 길게 스크롤하지 않고 결과만 빠르게 확인합니다.",
            font=(F, 12),
            text_color="#6B7280",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        info_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="#FFFFFF", border_width=1, border_color="#D1D5DB")
        info_frame.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        info_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            info_frame,
            textvariable=self.summary_var,
            font=(F, 13),
            text_color="#111827",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            info_frame,
            textvariable=self.status_text_var,
            font=(F, 12),
            text_color="#6B7280",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        table_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="#FFFFFF", border_width=1, border_color="#D1D5DB")
        table_frame.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 12))
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.configure(
            "Match.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#111827",
            borderwidth=0,
            rowheight=34,
            font=(F, 11),
        )
        style.map("Match.Treeview", background=[("selected", "#DBEAFE")], foreground=[("selected", "#111827")])
        style.configure(
            "Match.Treeview.Heading",
            background="#F8FAFC",
            foreground="#374151",
            relief="flat",
            font=(F, 11),
        )

        self.tree = ttk.Treeview(
            table_frame,
            columns=self.COLUMNS,
            show="headings",
            style="Match.Treeview",
            selectmode="browse",
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.tag_configure("odd", background="#FFFFFF")
        self.tree.tag_configure("even", background="#FAFBFC")

        for column, width in (
            ("업체명", 180),
            ("키워드", 220),
            ("화면", 90),
            ("API", 110),
            ("매칭값", 260),
            ("상태", 90),
        ):
            self.tree.heading(column, text=column, anchor="center")
            self.tree.column(column, anchor="center", width=width, stretch=True)

        self.empty_label = ctk.CTkLabel(
            table_frame,
            text="아직 표시할 결과가 없습니다.",
            font=(F, 13),
            text_color="#6B7280",
        )
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer,
            text="닫기",
            width=90,
            fg_color="#FFFFFF",
            text_color="#1F2937",
            border_width=1,
            border_color="#9CA3AF",
            hover_color="#F3F4F6",
            font=(F, 13),
            command=self.close,
        ).grid(row=0, column=1)

    def refresh(self, summary_text, rows, status_text):
        self.summary_var.set(summary_text)
        self.status_text_var.set(status_text)

        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

        for index, row in enumerate(rows):
            tag = "even" if index % 2 else "odd"
            values = (
                row.get("company", ""),
                row.get("keyword", ""),
                row.get("screen", ""),
                row.get("api", ""),
                row.get("matched_name", ""),
                row.get("status", ""),
            )
            self.tree.insert("", "end", values=values, tags=(tag,))

        if rows:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def close(self):
        self.destroy()


class BlogRankApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title("블로그 순위 체크")
        self.geometry("960x860")
        self.minsize(920, 780)
        self.configure(fg_color="#F3F4F6")
        for icon_path in (ICON_PATH, ICON_PATH_ALT):
            if os.path.exists(icon_path):
                try:
                    self.iconbitmap(icon_path)
                    break
                except Exception:
                    pass

        self.log_queue = queue.Queue()
        self.rows = read_rows()
        self.config_data = load_json(CONFIG_PATH)
        self.client_id_var = tk.StringVar(value=str(self.config_data.get("client_id", "")))
        self.client_secret_var = tk.StringVar(value=str(self.config_data.get("client_secret", "")))
        self.browser_mode = str(self.config_data.get("browser_mode", "auto")).strip().lower()
        if self.browser_mode not in VALID_BROWSER_MODES:
            self.browser_mode = "auto"

        self.api_locked = bool(self.client_id_var.get() and self.client_secret_var.get())
        self.search_thread = None
        self.pause_event = None
        self.stop_event = None
        self.running_token = None
        self.display_token = None
        self.next_token = 1
        self.finished_once = False
        self.total_tasks = 0
        self.completed_tasks = 0
        self.skipped_tasks = 0
        self.started_tasks = 0
        self.pending_by_keyword = {}
        self.pending_rows = []
        self.results = []
        self.company_color_tags = {}
        self.editor = None
        self.editor_info = None
        self.match_dialog = None
        self.template_path = ""
        self.last_clicked_column = "#1"
        self.status_var = tk.StringVar(value="대기 중")
        self.summary_primary_var = tk.StringVar(value="총 0 | 진행 0/0 | 노출 0 | 미노출 0 | 오류 0")

        self._build_ui()
        self._load_tree_rows()
        self._apply_api_lock_state()
        self._update_summary_panel()
        self.after(100, self._poll_log_queue)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        header.grid_columnconfigure(1, weight=1)

        title_wrap = ctk.CTkFrame(header, fg_color="transparent")
        title_wrap.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_wrap,
            text="Q",
            width=28,
            height=28,
            corner_radius=8,
            fg_color="#14A16E",
            text_color="white",
            font=("맑은 고딕", 15),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            title_wrap,
            text="블로그 순위 체크",
            font=("맑은 고딕", 26),
            text_color="#111827",
        ).pack(side="left")

        api_frame = ctk.CTkFrame(header, fg_color="transparent")
        api_frame.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(api_frame, text="API", text_color="#374151", font=("맑은 고딕", 13)).pack(
            side="left", padx=(0, 8)
        )
        self.client_id_entry = ctk.CTkEntry(api_frame, width=150, textvariable=self.client_id_var, font=("맑은 고딕", 12))
        self.client_id_entry.pack(side="left", padx=4)
        self.client_secret_entry = ctk.CTkEntry(api_frame, width=130, textvariable=self.client_secret_var, show="*", font=("맑은 고딕", 12))
        self.client_secret_entry.pack(side="left", padx=4)
        self.api_button = ctk.CTkButton(
            api_frame,
            text="잠금 해제",
            width=86,
            fg_color="#F59E0B",
            hover_color="#D97706",
            font=("맑은 고딕", 13),
            command=self._toggle_api_lock,
        )
        self.api_button.pack(side="left", padx=(8, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        body.grid_rowconfigure(0, weight=2)
        body.grid_rowconfigure(3, weight=3)
        body.grid_columnconfigure(0, weight=1)

        list_frame = ctk.CTkFrame(body, corner_radius=14, fg_color="#EAEAEA")
        list_frame.grid(row=0, column=0, sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(2, weight=1)

        title_row = ctk.CTkFrame(list_frame, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        title_row.grid_columnconfigure(0, weight=1)

        title_left = ctk.CTkFrame(title_row, fg_color="transparent")
        title_left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(title_left, text="검색 목록", font=("맑은 고딕", 22), text_color="#111827").pack(
            side="left"
        )
        self.count_label = ctk.CTkLabel(title_left, text="0개", font=("맑은 고딕", 16, "bold"), text_color="#374151")
        self.count_label.pack(side="left", padx=(8, 0))

        toolbar = ctk.CTkFrame(list_frame, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=14)
        toolbar.grid_columnconfigure(1, weight=1)

        left_buttons = ctk.CTkFrame(toolbar, fg_color="transparent")
        left_buttons.grid(row=0, column=0, sticky="w")
        self.add_row_button = ctk.CTkButton(
            left_buttons,
            text="+ 행 추가",
            width=104,
            height=36,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            text_color="#111111",
            font=("맑은 고딕", 14),
            command=self._open_add_dialog,
        )
        self.add_row_button.pack(side="left", padx=(0, 8))
        self.delete_selected_button = ctk.CTkButton(
            left_buttons,
            text="선택 삭제",
            width=98,
            height=36,
            fg_color="#F97316",
            hover_color="#EA580C",
            text_color="#111111",
            font=("맑은 고딕", 14),
            command=self._delete_selected_rows,
        )
        self.delete_selected_button.pack(side="left", padx=8)
        self.delete_all_button = ctk.CTkButton(
            left_buttons,
            text="전체 삭제",
            width=98,
            height=36,
            fg_color="#EF4444",
            hover_color="#DC2626",
            text_color="#111111",
            font=("맑은 고딕", 14),
            command=self._delete_all_rows,
        )
        self.delete_all_button.pack(side="left", padx=8)

        right_tools = ctk.CTkFrame(toolbar, fg_color="transparent")
        right_tools.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(right_tools, text="일괄추가 →", text_color="#4B5563", font=("맑은 고딕", 13)).pack(
            side="left", padx=(0, 8)
        )
        self.bulk_company_entry = ctk.CTkEntry(right_tools, width=140, placeholder_text="업체명 입력", font=("맑은 고딕", 12))
        self.bulk_company_entry.pack(side="left", padx=(0, 8))
        for label, count, color in (
            ("5개 추가", 5, "#7C3AED"),
            ("10개 추가", 10, "#6D28D9"),
            ("20개 추가", 20, "#5B21B6"),
        ):
            button = ctk.CTkButton(
                right_tools,
                text=label,
                width=80,
                fg_color=color,
                hover_color=color,
                font=("맑은 고딕", 14),
                command=lambda value=count: self._bulk_add_rows(value),
            )
            button.pack(side="left", padx=4)

        ctk.CTkLabel(
            list_frame,
            text="더블클릭 또는 F2 → 셀 편집  |  Tab → 다음 셀  |  Enter → 저장  |  Esc → 취소  |  Delete → 행 삭제",
            text_color="#374151",
            font=("맑은 고딕", 12),
        ).grid(row=2, column=0, sticky="nw", padx=16, pady=(8, 0))

        table_container = ctk.CTkFrame(
            list_frame, corner_radius=10, fg_color="white", border_width=1, border_color="#D1D5DB"
        )
        table_container.grid(row=2, column=0, sticky="nsew", padx=14, pady=(32, 14))
        table_container.grid_columnconfigure(0, weight=1)
        table_container.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Rank.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#111827",
            borderwidth=0,
            rowheight=42,
            font=("맑은 고딕", 11),
        )
        style.map("Rank.Treeview", background=[("selected", "#DBEAFE")], foreground=[("selected", "#111827")])
        style.configure(
            "Rank.Treeview.Heading",
            background="#F8FAFC",
            foreground="#374151",
            relief="flat",
            font=("맑은 고딕", 11),
        )

        self.tree = ttk.Treeview(
            table_container, columns=COLUMNS, show="headings", style="Rank.Treeview", selectmode="extended"
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(table_container, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.tag_configure("odd", background="#FFFFFF")
        self.tree.tag_configure("even", background="#FAFBFC")

        for column, width in (("업체명", 250), ("키워드", 340), ("식별값", 250)):
            self.tree.heading(column, text=column, anchor="center")
            self.tree.column(column, anchor="center", width=width, stretch=True)

        self.tree.bind("<Double-1>", self._handle_double_click)
        self.tree.bind("<Button-1>", self._remember_column)
        self.tree.bind("<F2>", self._start_keyboard_edit)
        self.tree.bind("<Delete>", lambda _event: self._delete_selected_rows())
        self.tree.bind("<Escape>", lambda _event: self._cancel_edit())

        action_row = ctk.CTkFrame(body, fg_color="transparent")
        action_row.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        action_row.grid_columnconfigure(5, weight=1)

        self.search_button = ctk.CTkButton(
            action_row,
            text="▶  검색 시작",
            width=118,
            height=38,
            fg_color="#14A16E",
            hover_color="#0E8A5E",
            text_color="#111111",
            font=("맑은 고딕", 15, "bold"),
            command=self._start_search,
        )
        self.search_button.grid(row=0, column=0, padx=(0, 10))
        self.search_from_selected_button = ctk.CTkButton(
            action_row,
            text="선택 검색",
            width=110,
            height=38,
            fg_color="#0EA5A4",
            hover_color="#0F8F8D",
            text_color="#111111",
            font=("맑은 고딕", 14, "bold"),
            command=self._start_search_from_selected,
        )
        self.search_from_selected_button.grid(row=0, column=1, padx=(0, 10))
        self.pause_button = ctk.CTkButton(
            action_row,
            text="⏸  일시정지",
            width=118,
            height=38,
            fg_color="#F59E0B",
            hover_color="#D97706",
            text_color="#111111",
            font=("맑은 고딕", 14, "bold"),
            command=self._toggle_pause,
            state="disabled",
        )
        self.pause_button.grid(row=0, column=2, padx=5)
        self.open_folder_button = ctk.CTkButton(
            action_row,
            text="📁  결과 폴더",
            width=118,
            height=38,
            fg_color="#FFFFFF",
            text_color="#1F2937",
            border_width=1,
            border_color="#9CA3AF",
            hover_color="#F3F4F6",
            font=("맑은 고딕", 14),
            command=self._open_output_folder,
        )
        self.open_folder_button.grid(row=0, column=3, padx=(5, 0))
        self.status_button = ctk.CTkButton(
            action_row,
            text="매칭 확인",
            width=110,
            height=38,
            fg_color="#FFFFFF",
            text_color="#1F2937",
            border_width=1,
            border_color="#9CA3AF",
            hover_color="#F3F4F6",
            font=(F, 14),
            command=self._open_match_dialog,
        )
        self.status_button.grid(row=0, column=4, padx=(10, 0))

        # ── 보고서 템플릿 행 ────────────────────────────────────────────────
        template_row = ctk.CTkFrame(body, fg_color="transparent")
        template_row.grid(row=2, column=0, sticky="ew", pady=(2, 4))
        template_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            template_row,
            text="보고서 템플릿",
            font=(F, 13),
            text_color="#374151",
        ).grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.template_label = ctk.CTkLabel(
            template_row,
            text="선택된 파일 없음",
            font=(F, 12),
            text_color="#9CA3AF",
            anchor="w",
        )
        self.template_label.grid(row=0, column=1, padx=(0, 10), sticky="ew")

        ctk.CTkButton(
            template_row,
            text="파일 선택",
            width=90,
            height=32,
            fg_color="#6366F1",
            hover_color="#4F46E5",
            text_color="white",
            font=(F, 13),
            command=self._select_template,
        ).grid(row=0, column=2, padx=(0, 6))

        self.load_kw_button = ctk.CTkButton(
            template_row,
            text="키워드 불러오기",
            width=120,
            height=32,
            fg_color="#0891B2",
            hover_color="#0E7490",
            text_color="white",
            font=(F, 13),
            state="disabled",
            command=self._load_keywords_from_template,
        )
        self.load_kw_button.grid(row=0, column=3, padx=(0, 6))

        self.report_button = ctk.CTkButton(
            template_row,
            text="보고서 생성",
            width=108,
            height=32,
            fg_color="#7C3AED",
            hover_color="#6D28D9",
            text_color="white",
            font=(F, 13),
            state="disabled",
            command=self._generate_report,
        )
        self.report_button.grid(row=0, column=4)
        # ───────────────────────────────────────────────────────────────────

        result_frame = ctk.CTkFrame(body, corner_radius=14, fg_color="#111318")
        result_frame.grid(row=3, column=0, sticky="nsew")
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(1, weight=1)

        # 헤더: 제목 + 상태 라벨
        result_header = ctk.CTkFrame(result_frame, fg_color="transparent")
        result_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        result_header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            result_header, text="실행 결과",
            font=("맑은 고딕", 21), text_color="#E5E7EB",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            result_header,
            textvariable=self.summary_primary_var,
            font=(F, 12),
            text_color="#D1D5DB",
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(14, 0))
        ctk.CTkLabel(
            result_header,
            textvariable=self.status_var,
            text_color="#10B981",
            font=("맑은 고딕", 15),
        ).grid(row=0, column=2, sticky="e", padx=(10, 4))

        self.log_text = ctk.CTkTextbox(
            result_frame,
            height=250,
            corner_radius=10,
            fg_color="#0D0F12",
            text_color="#D1D5DB",
            border_width=0,
            font=("맑은 고딕", 14),
            wrap="word",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.log_text.configure(state="disabled")
        text_widget = self.log_text._textbox
        log_font = ("맑은 고딕", 14)
        summary_font = ("맑은 고딕", 13)
        text_widget.tag_config("blue", foreground="#6DD3FF", font=log_font)
        text_widget.tag_config("green", foreground="#74FF97", font=log_font)
        text_widget.tag_config("yellow", foreground="#FACC15", font=log_font)
        text_widget.tag_config("red", foreground="#FB7185", font=log_font)
        text_widget.tag_config("white", foreground="#E5E7EB", font=log_font)
        text_widget.tag_config("title", foreground="#FFFFFF", font=("맑은 고딕", 14), spacing3=8)
        for tag_name, color in RESULT_COMPANY_TAGS:
            text_widget.tag_config(tag_name, foreground=color, font=summary_font, spacing3=4)
        text_widget.tag_config("summary_meta", foreground="#E5E7EB", font=summary_font, spacing3=4)
        text_widget.tag_config("summary_match", foreground="#BFDBFE", font=summary_font, spacing3=4)
        text_widget.tag_config("summary_error", foreground="#FCA5A5", font=summary_font, spacing3=4)
        text_widget.tag_config("summary_total", foreground="#FDE68A", font=summary_font, spacing1=8, spacing3=6)
        text_widget.tag_config("summary_done", foreground="#D1D5DB", font=summary_font)

    def _load_tree_rows(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, row in enumerate(self.rows):
            tag = "even" if index % 2 else "odd"
            self.tree.insert("", "end", iid=str(index), values=(row["업체명"], row["키워드"], row["식별값"]), tags=(tag,))
        self._update_count_label()

    def _update_count_label(self):
        self.count_label.configure(text=f"{len(self.rows)}개")
        if hasattr(self, "summary_primary_var"):
            self._update_summary_panel()

    def _toggle_api_lock(self):
        if self.api_locked:
            self.api_locked = False
            self._apply_api_lock_state()
            self.client_id_entry.focus_set()
            return
        self._save_config()
        self.api_locked = True
        self._apply_api_lock_state()

    def _apply_api_lock_state(self):
        entry_state = "disabled" if self.api_locked else "normal"
        self.client_id_entry.configure(state=entry_state)
        self.client_secret_entry.configure(state=entry_state)
        self.api_button.configure(text="잠금 해제" if self.api_locked else "API 저장")
        self._update_summary_panel()

    def _save_config(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "client_id": self.client_id_var.get().strip(),
                    "client_secret": self.client_secret_var.get().strip(),
                    "browser_mode": self.browser_mode,
                    "rows": self.rows,
                },
                file,
                ensure_ascii=False,
                indent=2,
            )

    def _apply_runtime_credentials(self):
        client_id = self.client_id_var.get().strip()
        client_secret = self.client_secret_var.get().strip()
        if client_id:
            os.environ["NAVER_CLIENT_ID"] = client_id
        if client_secret:
            os.environ["NAVER_CLIENT_SECRET"] = client_secret

    def _collect_rows_from_tree(self):
        rows = []
        for item_id in self.tree.get_children():
            company, keyword, identifier = self.tree.item(item_id, "values")
            rows.append({"업체명": company.strip(), "키워드": keyword.strip(), "식별값": identifier.strip()})
        return rows

    def _persist_rows(self):
        self._finish_edit(save=True)
        self.rows = self._collect_rows_from_tree()
        write_rows(self.rows)
        self._save_config()
        self._update_count_label()

    def _open_add_dialog(self):
        self._finish_edit(save=True)
        AddRowDialog(self, self._append_row)

    def _append_row(self, row):
        self.rows.append(row)
        self._load_tree_rows()
        self._persist_rows()
        if self.tree.get_children():
            target = self.tree.get_children()[-1]
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)

    def _bulk_add_rows(self, count):
        self._finish_edit(save=True)
        company = self.bulk_company_entry.get().strip()
        if not company:
            messagebox.showwarning("확인 필요", "업체명을 먼저 입력해 주세요.", parent=self)
            return
        for _ in range(count):
            self.rows.append({"업체명": company, "키워드": "", "식별값": company})
        self._load_tree_rows()
        self._persist_rows()

    def _delete_selected_rows(self):
        self._finish_edit(save=True)
        selected = list(self.tree.selection())
        if not selected:
            return
        for item_id in selected:
            self.tree.delete(item_id)
        self.rows = self._collect_rows_from_tree()
        self._load_tree_rows()
        self._persist_rows()

    def _delete_all_rows(self):
        self._finish_edit(save=True)
        if not self.rows:
            return
        if not messagebox.askyesno("전체 삭제", "검색 목록을 모두 지울까요?", parent=self):
            return
        self.rows = []
        self._load_tree_rows()
        self._persist_rows()

    def _remember_column(self, event):
        column = self.tree.identify_column(event.x)
        if column:
            self.last_clicked_column = column
        self._finish_edit(save=True)

    def _handle_double_click(self, event):
        item_id = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item_id and column:
            self.last_clicked_column = column
            self._start_edit(item_id, column)

    def _start_keyboard_edit(self, _event=None):
        item_id = self.tree.focus() or (self.tree.selection()[0] if self.tree.selection() else "")
        if item_id:
            self._start_edit(item_id, self.last_clicked_column or "#1")

    def _start_edit(self, item_id, column):
        self._finish_edit(save=True)
        bbox = self.tree.bbox(item_id, column)
        if not bbox:
            return
        x, y, width, height = bbox
        values = list(self.tree.item(item_id, "values"))
        column_index = int(column.replace("#", "")) - 1

        entry = tk.Entry(self.tree, justify="center", relief="solid", bd=1, font=("맑은 고딕", 11))
        entry.insert(0, values[column_index])
        entry.place(x=x + 1, y=y + 1, width=width - 2, height=height - 2)
        entry.focus_set()
        entry.select_range(0, tk.END)
        entry.bind("<Return>", lambda _event: self._finish_edit(save=True))
        entry.bind("<Escape>", lambda _event: self._cancel_edit())
        entry.bind("<Tab>", lambda event: self._move_edit(event, 1))
        entry.bind("<Shift-Tab>", lambda event: self._move_edit(event, -1))

        self.editor = entry
        self.editor_info = (item_id, column)

    def _move_edit(self, _event, direction):
        info = self.editor_info
        self._finish_edit(save=True)
        if not info:
            return "break"
        item_id, column = info
        column_index = max(0, min(len(COLUMNS) - 1, int(column.replace("#", "")) - 1 + direction))
        next_column = f"#{column_index + 1}"
        self.last_clicked_column = next_column
        self._start_edit(item_id, next_column)
        return "break"

    def _finish_edit(self, save):
        if not self.editor or not self.editor_info:
            return
        item_id, column = self.editor_info
        if save:
            values = list(self.tree.item(item_id, "values"))
            values[int(column.replace("#", "")) - 1] = self.editor.get().strip()
            self.tree.item(item_id, values=values)
        self.editor.destroy()
        self.editor = None
        self.editor_info = None
        self.rows = self._collect_rows_from_tree()
        write_rows(self.rows)
        self._save_config()
        self.tree.focus(item_id)
        self.tree.selection_set(item_id)
        self.tree.focus_set()

    def _cancel_edit(self):
        if not self.editor or not self.editor_info:
            return
        item_id, _column = self.editor_info
        self.editor.destroy()
        self.editor = None
        self.editor_info = None
        self.tree.focus(item_id)
        self.tree.selection_set(item_id)
        self.tree.focus_set()

    def _set_search_controls(self, searching):
        self.search_button.configure(state="disabled" if searching else "normal")
        self.search_from_selected_button.configure(state="disabled" if searching else "normal")
        self.pause_button.configure(state="normal" if searching else "disabled")

    def _clear_run_state(self):
        self.finished_once = False
        self.total_tasks = 0
        self.completed_tasks = 0
        self.skipped_tasks = 0
        self.started_tasks = 0
        self.pending_by_keyword = {}
        self.pending_rows = []
        self.results = []
        self.company_color_tags = {}
        self._update_summary_panel()

    def _get_selected_start_item_id(self):
        selection = set(self.tree.selection())
        if not selection:
            return ""

        focus_item = self.tree.focus()
        if focus_item in selection:
            return focus_item

        for item_id in self.tree.get_children():
            if item_id in selection:
                return item_id
        return ""

    def _build_search_scope(self, from_selected=False):
        all_rows = self._collect_rows_from_tree()
        scoped_rows = list(all_rows)
        start_label = ""

        if from_selected:
            start_item_id = self._get_selected_start_item_id()
            if not start_item_id:
                messagebox.showwarning("선택 확인", "검색을 시작할 키워드 행을 먼저 선택해주세요.", parent=self)
                return [], [], 0, ""

            item_ids = list(self.tree.get_children())
            try:
                start_index = item_ids.index(start_item_id)
            except ValueError:
                start_index = -1

            if start_index < 0:
                messagebox.showwarning("선택 확인", "선택한 행을 찾지 못했습니다. 다시 선택해주세요.", parent=self)
                return [], [], 0, ""

            scoped_rows = all_rows[start_index:]
            values = self.tree.item(start_item_id, "values")
            company = str(values[0] if len(values) > 0 else "").strip()
            keyword = str(values[1] if len(values) > 1 else "").strip()
            start_label = keyword or company or f"{start_index + 1}번째 행"

        valid_rows = []
        skipped = 0
        for row in scoped_rows:
            keyword = row.get("키워드", "").strip()
            if keyword:
                valid_rows.append(
                    {
                        "업체명": row.get("업체명", "").strip(),
                        "키워드": keyword,
                        "식별값": row.get("식별값", "").strip() or row.get("업체명", "").strip(),
                    }
                )
            else:
                skipped += 1

        return all_rows, valid_rows, skipped, start_label

    def _start_search_from_selected(self):
        self._start_search(from_selected=True)

    def _start_search(self, from_selected=False):
        self._finish_edit(save=True)
        self._persist_rows()
        if self.search_thread and self.search_thread.is_alive():
            return

        if not self.client_id_var.get().strip() or not self.client_secret_var.get().strip():
            messagebox.showwarning("API 확인", "Client ID와 Client Secret을 입력해주세요.", parent=self)
            return

        all_rows, valid_rows, skipped, start_label = self._build_search_scope(from_selected=from_selected)
        if not valid_rows:
            message = "선택한 위치 이후에 키워드가 입력된 행이 없습니다." if from_selected else "키워드가 입력된 행이 없습니다."
            messagebox.showwarning("검색 목록", message, parent=self)
            return

        self._clear_run_state()
        self.total_tasks = len(valid_rows)
        self.skipped_tasks = skipped
        self.pending_rows = [dict(row) for row in valid_rows]
        self.running_token = self.next_token
        self.display_token = self.running_token
        self.next_token += 1
        self.status_var.set("실행 중...")
        self.pause_button.configure(text="⏸  일시정지")
        self._set_search_controls(True)
        self._clear_log()
        if from_selected and start_label:
            self._append_log(f"선택한 키워드부터 검색합니다: {start_label}\n", "yellow")
        self._append_log(f"검색을 시작합니다. (총 {self.total_tasks}개 키워드)\n", "white")
        if skipped:
            self._append_log(f"키워드 없는 행 {skipped}개 건너뜀\n\n", "yellow")
        else:
            self._append_log("\n", "white")
        self._update_summary_panel()

        self.search_thread = threading.Thread(
            target=self._run_search_worker,
            args=(self.running_token, all_rows, valid_rows),
            daemon=True,
        )
        self.search_thread.start()

    def _run_search_worker(self, token, all_rows, valid_rows):
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()
        app_main._PAUSE_EVENT = self.pause_event
        app_main._STOP_EVENT = self.stop_event

        writer = QueueWriter(self.log_queue, token)
        try:
            write_rows(valid_rows)
            self._save_config()
            self._apply_runtime_credentials()
            with redirect_stdout(writer), redirect_stderr(writer):
                app_main.main()
        except Exception as exc:
            self.log_queue.put({"type": "worker_error", "token": token, "message": f"{type(exc).__name__}: {exc}"})
        finally:
            writer.flush()
            write_rows(all_rows)
            app_main._PAUSE_EVENT = None
            app_main._STOP_EVENT = None
            self.log_queue.put({"type": "thread_done", "token": token})

    def _toggle_pause(self):
        if not self.pause_event:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="▶  다시 시작")
            self.status_var.set("일시정지")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="⏸  일시정지")
            self.status_var.set("실행 중...")
        self._update_summary_panel()

    def _load_keywords_from_template(self):
        if not self.template_path or not os.path.exists(self.template_path):
            messagebox.showwarning("템플릿 없음", "보고서 템플릿 파일을 먼저 선택해주세요.", parent=self)
            return

        # 업체명: 일괄추가 입력란 → 없으면 직접 입력 요청
        company = self.bulk_company_entry.get().strip()
        if not company:
            from tkinter import simpledialog
            company = simpledialog.askstring(
                "업체명 입력",
                "키워드를 불러올 업체명을 입력해주세요.\n(일괄추가 란에 미리 입력해두면 자동으로 사용됩니다)",
                parent=self,
            )
            if not company or not company.strip():
                return
            company = company.strip()

        import openpyxl
        from excel_writer import read_schedule_keywords

        try:
            wb = openpyxl.load_workbook(self.template_path)
            keywords = read_schedule_keywords(wb)
        except Exception as exc:
            messagebox.showerror("읽기 오류", f"템플릿에서 키워드를 읽는 중 오류가 발생했습니다:\n{exc}", parent=self)
            return

        if not keywords:
            messagebox.showwarning("키워드 없음", "schedule 시트의 F21 이후에 키워드가 없습니다.", parent=self)
            return

        # 기존 목록이 있으면 교체 여부 확인
        if self.rows:
            if not messagebox.askyesno(
                "기존 목록 대체",
                f"현재 검색 목록 {len(self.rows)}개를 삭제하고\n"
                f"템플릿 키워드 {len(keywords)}개로 교체할까요?",
                parent=self,
            ):
                return

        new_rows = [
            {"업체명": company, "키워드": kw, "식별값": company}
            for kw, _ in keywords
        ]
        self.rows = new_rows
        self._load_tree_rows()
        self._persist_rows()
        self._append_log(f"템플릿에서 키워드 {len(new_rows)}개를 불러왔습니다. (업체: {company})\n", "yellow")

    def _select_template(self):
        path = filedialog.askopenfilename(
            title="보고서 템플릿 파일 선택",
            filetypes=[("Excel 파일", "*.xlsx"), ("모든 파일", "*.*")],
            parent=self,
        )
        if not path:
            return
        self.template_path = path
        filename = os.path.basename(path)
        self.template_label.configure(text=filename, text_color="#111827")
        self.report_button.configure(state="normal")
        self.load_kw_button.configure(state="normal")

    def _generate_report(self, auto=False):
        if not self.template_path or not os.path.exists(self.template_path):
            if not auto:
                messagebox.showerror("오류", "보고서 템플릿 파일을 먼저 선택해주세요.", parent=self)
            return

        captures_dir = os.path.join(OUTPUT_DIR, "captures")
        if not os.path.isdir(captures_dir):
            if not auto:
                messagebox.showwarning("캡처 없음", "captures 폴더가 없습니다. 먼저 검색을 실행해주세요.", parent=self)
            return

        import datetime
        from excel_writer import insert_captures_to_report, best_rank

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.splitext(os.path.basename(self.template_path))[0]
        output_name = f"{base}_완성_{timestamp}.xlsx"
        output_path = os.path.join(OUTPUT_DIR, output_name)

        # 검색 결과에서 키워드별 최고 순위 딕셔너리 생성
        ranks = {}
        for r in self.results:
            kw = str(r.get("keyword", "")).strip()
            if kw:
                ranks[kw] = best_rank(r.get("api", ""), r.get("screen", ""))

        # 버튼 비활성화 + 시작 로그
        self.report_button.configure(state="disabled", text="생성 중...")
        self._append_log(f"\n보고서 생성 시작: {output_name}\n", "yellow")

        def _on_progress(current, total, keyword):
            self.after(0, lambda c=current, t=total, kw=keyword:
                self._append_log(f"  [{c}/{t}] {kw}\n", "gray"))

        def _run():
            try:
                report = insert_captures_to_report(
                    template_path=self.template_path,
                    captures_dir=captures_dir,
                    output_path=output_path,
                    ranks=ranks,
                    progress_callback=_on_progress,
                )
                self.after(0, lambda: _finish(report, None))
            except Exception as exc:
                self.after(0, lambda e=exc: _finish(None, e))

        def _finish(report, error):
            self.report_button.configure(state="normal", text="보고서 생성")
            if error:
                messagebox.showerror("보고서 생성 오류", str(error), parent=self)
                return
            inserted   = sum(1 for _, s in report if s.startswith("완료"))
            missing    = sum(1 for _, s in report if s == "캡처 없음")
            resize_err = sum(1 for _, s in report if "리사이즈실패" in s)
            self._append_log(f"보고서 생성 완료: {output_name}\n", "green")
            summary = f"  이미지 삽입 {inserted}개 | 캡처 없음 {missing}개"
            if resize_err:
                summary += f" | 리사이즈 실패 {resize_err}개"
                for kw, s in report:
                    if "리사이즈실패" in s:
                        self._append_log(f"  ⚠ 리사이즈 실패: {kw}\n", "yellow")
            self._append_log(summary + "\n", "white")
            if not auto:
                messagebox.showinfo(
                    "보고서 생성 완료",
                    f"보고서가 저장되었습니다.\n\n{output_path}\n\n"
                    f"이미지 삽입: {inserted}개  |  캡처 없음: {missing}개",
                    parent=self,
                )

        threading.Thread(target=_run, daemon=True).start()

    def _open_output_folder(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        try:
            os.startfile(OUTPUT_DIR)
        except Exception as exc:
            messagebox.showerror("폴더 열기 실패", str(exc), parent=self)

    def _open_match_dialog(self):
        if self.match_dialog and self.match_dialog.winfo_exists():
            self._refresh_match_dialog()
            self.match_dialog.focus()
            return
        self.match_dialog = MatchResultsDialog(self)
        self._refresh_match_dialog()
        self.match_dialog.focus()

    def _poll_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                token = item.get("token")
                if item.get("type") == "stdout":
                    if token == self.display_token:
                        self._handle_stdout_line(item.get("text", ""))
                elif item.get("type") == "worker_error":
                    if token == self.display_token:
                        self._append_log(f"오류: {item.get('message', '')}\n", "red")
                elif item.get("type") == "thread_done" and token == self.running_token:
                    self._cleanup_search_state()
                    if not self.finished_once:
                        self._finalize_search()
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _handle_stdout_line(self, line):
        text = str(line or "").strip()
        if not text:
            return

        start_match = re.match(r"^\[검색 시작\]\s*(.*?)\s*/\s*(.*)$", text)
        if start_match:
            keyword = start_match.group(1).strip()
            identifier = start_match.group(2).strip()
            self.started_tasks += 1
            row_context = self.pending_rows.pop(0) if self.pending_rows else {}
            company = str(row_context.get("업체명", "")).strip()
            info = {
                "label": f"{keyword} / {identifier}",
                "keyword": keyword,
                "identifier": identifier,
                "company": company,
            }
            self.pending_by_keyword.setdefault(keyword, []).append(info)
            self._append_log(f"🔍 ({self.started_tasks}/{self.total_tasks})  {info['label']}\n", "blue")
            self._update_summary_panel()
            return

        result_match = re.match(
            r"^\[결과\]\s*(.*?)\s*->\s*API\s*(.*?)\s*/\s*화면\s*(.*?)\s*/\s*브라우저\s*(.*?)\s*/\s*매칭\s*(.*)$",
            text,
        )
        if result_match:
            keyword = result_match.group(1).strip()
            api_rank = result_match.group(2).strip()
            screen_rank = result_match.group(3).strip()
            matched_name = result_match.group(5).strip()
            info = self._pop_pending_info(keyword)
            self.completed_tasks += 1
            self.results.append(
                {
                    "label": info["label"],
                    "company": info.get("company", ""),
                    "keyword": info.get("keyword", keyword),
                    "identifier": info.get("identifier", ""),
                    "api": api_rank,
                    "screen": screen_rank,
                    "error": False,
                    "matched_name": matched_name,
                }
            )
            screen_text = f"화면 {screen_rank}위" if screen_rank.isdigit() else f"화면 {screen_rank}"
            api_text = f"네이버 API {api_rank}위" if api_rank.isdigit() else f"네이버 API {api_rank}"
            self._append_log(f"→  ✅ {screen_text}  ({api_text})\n", "green")
            self._update_summary_panel()
            self._finalize_if_ready()
            return

        error_match = re.match(r"^\[오류\]\s*(.*?)\s*->\s*(.*)$", text)
        if error_match:
            keyword = error_match.group(1).strip()
            info = self._pop_pending_info(keyword)
            self.completed_tasks += 1
            self.results.append(
                {
                    "label": info["label"],
                    "company": info.get("company", ""),
                    "keyword": info.get("keyword", keyword),
                    "identifier": info.get("identifier", ""),
                    "api": "오류",
                    "screen": "오류",
                    "error": True,
                    "matched_name": "",
                }
            )
            self._append_log(f"→  오류 발생: {info['label']}\n", "red")
            self._update_summary_panel()
            self._finalize_if_ready()

    def _pop_pending_info(self, keyword):
        bucket = self.pending_by_keyword.get(keyword) or []
        if bucket:
            return bucket.pop(0)
        return {"label": keyword, "keyword": keyword, "identifier": "", "company": ""}

    def _get_company_color_tag(self, company_name):
        key = re.sub(r"\s+", "", str(company_name or "")).lower() or "_default"
        if key not in self.company_color_tags:
            palette = [tag_name for tag_name, _color in RESULT_COMPANY_TAGS]
            self.company_color_tags[key] = palette[len(self.company_color_tags) % len(palette)]
        return self.company_color_tags[key]

    def _count_valid_rows(self):
        return sum(1 for row in self.rows if str(row.get("키워드", "")).strip())

    def _calculate_result_stats(self):
        stats = {"exposed": 0, "missed": 0, "errors": 0}
        for result in self.results:
            if result.get("error"):
                stats["errors"] += 1
                continue

            screen = str(result.get("screen", "")).strip()
            if screen.isdigit():
                stats["exposed"] += 1
            else:
                stats["missed"] += 1

        return stats

    def _format_screen_value(self, value):
        text = str(value or "").strip()
        if not text:
            return "-"
        return f"{text}위" if text.isdigit() else text

    def _format_api_value(self, value):
        text = str(value or "").strip()
        if not text:
            return "-"
        return f"{text}위" if text.isdigit() else text

    def _build_match_rows(self):
        rows = []
        for result in self.results:
            screen = str(result.get("screen", "")).strip()
            if result.get("error"):
                status = "오류"
            elif screen.isdigit():
                status = "노출"
            else:
                status = "미노출"

            rows.append(
                {
                    "company": str(result.get("company", "")).strip() or "-",
                    "keyword": str(result.get("keyword", "")).strip() or "-",
                    "screen": self._format_screen_value(result.get("screen", "")),
                    "api": self._format_api_value(result.get("api", "")),
                    "matched_name": str(result.get("matched_name", "")).strip() or "-",
                    "status": status,
                }
            )
        return rows

    def _refresh_match_dialog(self):
        if self.match_dialog and self.match_dialog.winfo_exists():
            self.match_dialog.refresh(
                self.summary_primary_var.get(),
                self._build_match_rows(),
                f"현재 상태: {self.status_var.get()}",
            )

    def _update_summary_panel(self):
        if not hasattr(self, "summary_primary_var"):
            return

        total = self.total_tasks if self.total_tasks else self._count_valid_rows()
        completed = min(self.completed_tasks, total) if total else self.completed_tasks
        stats = self._calculate_result_stats()

        self.summary_primary_var.set(
            f"총 {total} | 진행 {completed}/{total} | 노출 {stats['exposed']} | 미노출 {stats['missed']} | 오류 {stats['errors']}"
        )

        self._refresh_match_dialog()

    def _finalize_search(self):
        if self.finished_once:
            return
        self.finished_once = True
        self.status_var.set("완료")
        self._cleanup_search_state()
        stats = self._calculate_result_stats()
        self._append_log("\n────────────────────────\n", "white")
        self._append_log("\n최종 결과\n\n", "title")
        for result in self.results:
            if result.get("error"):
                self._append_log(f"• {result['label']}  -  오류\n", "summary_error")
                continue
            screen = str(result.get("screen", "")).strip()
            api = str(result.get("api", "")).strip()
            screen_text = f"화면 {screen}위" if screen.isdigit() else f"화면 {screen}"
            api_text = f"네이버 API {api}위" if api.isdigit() else f"네이버 API {api}"
            matched_name = str(result.get("matched_name", "")).strip()
            company_name = str(result.get("company", "")).strip()
            line = f"✓ {result['label']}  -  {screen_text} ({api_text})"
            if matched_name:
                line += f"  [매칭: {matched_name}]"
            line += "\n"
            self._append_log(line, self._get_company_color_tag(company_name))
        self._append_log(
            f"\n총 {self.total_tasks}개 | 노출 {stats['exposed']}개 | 미노출 {stats['missed']}개 | 오류 {stats['errors']}개\n",
            "summary_total",
        )
        self._append_log("모든 검색 완료! 결과 폴더를 확인하세요.\n", "summary_done")
        self._update_summary_panel()

        # 보고서 템플릿이 선택되어 있으면 자동 생성
        if self.template_path:
            self._append_log("\n보고서 템플릿이 선택되어 있어 자동으로 보고서를 생성합니다...\n", "yellow")
            self.after(500, lambda: self._generate_report(auto=True))

    def _finalize_if_ready(self):
        if self.finished_once:
            return
        if self.total_tasks > 0 and self.completed_tasks >= self.total_tasks:
            self._finalize_search()

    def _cleanup_search_state(self):
        self.running_token = None
        self.display_token = None
        self.search_thread = None
        self.pause_event = None
        self.stop_event = None
        self._set_search_controls(False)
        self._update_summary_panel()

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, text, tag):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text, tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


if __name__ == "__main__":
    ensure_runtime_files()
    app = BlogRankApp()
    app.mainloop()
