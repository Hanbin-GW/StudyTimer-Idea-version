#!/usr/bin/env python3
"""
공부 타이머 v3
- 키오스크 모드 (Dock, 메뉴바 숨김, 단축키 차단)
- 크롬 외 모든 앱 강제 종료
- 크롬 탭 차단 (허용 사이트 외)
- 비상 해제 (비밀번호)
- SHA-256 비밀번호 해싱
- 이모티콘 없음
"""

import tkinter as tk
import threading
import subprocess
import hashlib
import json
import os
import time
from pathlib import Path

# pyobjc -- 키오스크 모드용
try:
    from AppKit import NSApplication, NSApp
    from AppKit import (
        NSApplicationPresentationHideDock,
        NSApplicationPresentationHideMenuBar,
        NSApplicationPresentationDisableAppleMenu,
        NSApplicationPresentationDisableProcessSwitching,
        NSApplicationPresentationDisableForceQuit,
        NSApplicationPresentationDisableSessionTermination,
    )
    PYOBJC_AVAILABLE = True
except ImportError:
    PYOBJC_AVAILABLE = False

# ══════════════════════════════════════════════════════════════
# 설정 관리
# ══════════════════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_SITES = [
    "music.youtube.com",
    "docs.google.com",
    "mail.google.com",
    "gmail.com",
    "chatgpt.com",
    "chat.openai.com",
    "claude.ai",
]

DEFAULT_PW_HASH = hashlib.sha256("1234".encode()).hexdigest()

# 시스템 필수 프로세스 -- 절대 건드리면 안 됨
SYSTEM_PROCESSES = {
    "loginwindow",
    "WindowServer",
    "Dock",
    "SystemUIServer",
    "ControlCenter",
    "NotificationCenter",
    "Spotlight",
    "universalaccessd",
    "coreaudiod",
    "AirPlayUIAgent",
    "UserNotificationCenter",
    "study_timer",   # 자기 자신
    "Python",
    "python3",
    "osascript",
}

# 허용 앱 -- 타이머 중 종료하지 않는 앱
ALLOWED_APPS = {
    "Google Chrome",
    "Terminal",        # macOS 기본 터미널
    "iTerm2",          # 터미널 대용 (있으면 허용)
} | SYSTEM_PROCESSES


class ConfigManager:

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        default = {
            "password_hash": DEFAULT_PW_HASH,
            "allowed_sites": DEFAULT_SITES.copy(),
        }
        self._save(default)
        return default

    def _save(self, data: dict):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def check_password(self, raw: str) -> bool:
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        return hashed == self.data["password_hash"]

    def set_password(self, raw: str):
        self.data["password_hash"] = hashlib.sha256(raw.encode()).hexdigest()
        self._save(self.data)

    @property
    def allowed_sites(self) -> list[str]:
        return self.data["allowed_sites"]

    def add_site(self, site: str):
        site = site.strip().lower()
        if site and site not in self.data["allowed_sites"]:
            self.data["allowed_sites"].append(site)
            self._save(self.data)

    def remove_site(self, site: str):
        if site in self.data["allowed_sites"]:
            self.data["allowed_sites"].remove(site)
            self._save(self.data)


# ══════════════════════════════════════════════════════════════
# 키오스크 모드
# ══════════════════════════════════════════════════════════════

class KioskMode:
    """
    pyobjc를 통해 macOS NSApplication 프레젠테이션 옵션을 제어.

    프레젠테이션 옵션 = macOS가 UI 요소를 얼마나 숨길지 결정하는 플래그.
    비트 OR(|)로 여러 옵션을 조합.

    pyobjc 없으면 AppleScript 방식으로 폴백.
    """

    def enable(self):
        if PYOBJC_AVAILABLE:
            self._enable_pyobjc()
        else:
            self._enable_applescript()

    def disable(self):
        if PYOBJC_AVAILABLE:
            self._disable_pyobjc()
        else:
            self._disable_applescript()

    def _enable_pyobjc(self):
        options = (
            NSApplicationPresentationHideDock |
            NSApplicationPresentationHideMenuBar |
            NSApplicationPresentationDisableAppleMenu |
            NSApplicationPresentationDisableProcessSwitching |
            NSApplicationPresentationDisableForceQuit |
            NSApplicationPresentationDisableSessionTermination
        )
        NSApp.setPresentationOptions_(options)

    def _disable_pyobjc(self):
        # 0 = 기본값 (모든 제한 해제)
        NSApp.setPresentationOptions_(0)

    def _enable_applescript(self):
        # pyobjc 없을 때 AppleScript로 일부 제한
        # Dock 자동숨기기 + 메뉴바 자동숨기기
        script = """
        tell application "System Events"
            tell dock preferences
                set autohide to true
            end tell
        end tell
        """
        subprocess.run(["osascript", "-e", script],
                       capture_output=True, timeout=5)

    def _disable_applescript(self):
        script = """
        tell application "System Events"
            tell dock preferences
                set autohide to false
            end tell
        end tell
        """
        subprocess.run(["osascript", "-e", script],
                       capture_output=True, timeout=5)


# ══════════════════════════════════════════════════════════════
# 앱 차단기
# ══════════════════════════════════════════════════════════════

class AppBlocker:
    """
    3초마다 실행 중인 GUI 앱 목록을 확인하고
    ALLOWED_APPS 외의 앱을 강제 종료.
    """

    POLL_INTERVAL = 3

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _get_running_apps(self) -> list[str]:
        """
        현재 화면에 보이는 앱 목록 반환.
        background only is false = GUI 앱만 (시스템 데몬 제외)
        """
        script = """
            tell application "System Events"
                get name of every process whose background only is false
            end tell
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return []
            raw = result.stdout.strip()
            if not raw:
                return []
            return [a.strip() for a in raw.split(",")]
        except Exception:
            return []

    def _kill_app(self, app_name: str):
        """
        앱 강제 종료.
        1차: AppleScript quit (정상 종료)
        2차: killall -9 (강제)
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", f'tell application "{app_name}" to quit'],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                return
        except Exception:
            pass

        try:
            subprocess.run(
                ["killall", "-9", app_name],
                capture_output=True, text=True
            )
        except Exception:
            pass

    def _loop(self):
        while not self._stop_event.is_set():
            apps = self._get_running_apps()
            for app in apps:
                if app not in ALLOWED_APPS:
                    self._kill_app(app)
            self._stop_event.wait(self.POLL_INTERVAL)

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="AppBlocker"
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()


# ══════════════════════════════════════════════════════════════
# 크롬 탭 차단기
# ══════════════════════════════════════════════════════════════

class ChromeBlocker:

    POLL_INTERVAL = 5

    APPLESCRIPT_TEMPLATE = """
tell application "Google Chrome"
    set allowedDomains to {domains}
    set tabsToClose to {{}}
    repeat with w in every window
        repeat with t in every tab of w
            set tabURL to URL of t
            set isAllowed to false
            repeat with d in allowedDomains
                if tabURL contains d then
                    set isAllowed to true
                    exit repeat
                end if
            end repeat
            if not isAllowed then
                set end of tabsToClose to t
            end if
        end repeat
    end repeat
    repeat with t in tabsToClose
        close t
    end repeat
end tell
"""

    def __init__(self, config: ConfigManager):
        self.config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _build_script(self) -> str:
        domains_as = "{" + ", ".join(
            f'"{d}"' for d in self.config.allowed_sites
        ) + "}"
        return self.APPLESCRIPT_TEMPLATE.format(domains=domains_as)

    def _run_once(self):
        try:
            subprocess.run(
                ["osascript", "-e", self._build_script()],
                capture_output=True, text=True, timeout=8
            )
        except Exception:
            pass

    def _loop(self):
        while not self._stop_event.is_set():
            self._run_once()
            self._stop_event.wait(self.POLL_INTERVAL)

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ChromeBlocker"
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()


# ══════════════════════════════════════════════════════════════
# 비밀번호 입력 다이얼로그
# ══════════════════════════════════════════════════════════════

class PasswordDialog(tk.Toplevel):

    def __init__(self, parent, title="비밀번호 입력", prompt="비밀번호:"):
        super().__init__(parent)
        self.title(title)
        self.configure(bg="#0D0D0D")
        self.resizable(False, False)
        self.grab_set()
        self.result: str | None = None

        W, H = 320, 150
        px = parent.winfo_x() + (parent.winfo_width()  - W) // 2
        py = parent.winfo_y() + (parent.winfo_height() - H) // 2
        self.geometry(f"{W}x{H}+{px}+{py}")

        tk.Label(
            self, text=prompt,
            bg="#0D0D0D", fg="#AAAAAA",
            font=("Courier New", 10),
        ).pack(pady=(22, 6))

        self._entry = tk.Entry(
            self, show="*",
            bg="#1A1A1A", fg="#EEEEEE",
            insertbackground="#4AE3A0",
            relief="flat",
            font=("Courier New", 13),
            width=20,
        )
        self._entry.pack(ipady=6)
        self._entry.focus_set()
        self._entry.bind("<Return>",  lambda e: self._confirm())
        self._entry.bind("<Escape>",  lambda e: self._cancel())

        btn_row = tk.Frame(self, bg="#0D0D0D")
        btn_row.pack(pady=12)

        tk.Button(
            btn_row, text="확인",
            bg="#4AE3A0", fg="#000",
            font=("Courier New", 10, "bold"),
            relief="flat", cursor="hand2",
            padx=16, pady=5,
            command=self._confirm,
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            btn_row, text="취소",
            bg="#222", fg="#888",
            font=("Courier New", 10),
            relief="flat", cursor="hand2",
            padx=16, pady=5,
            command=self._cancel,
        ).pack(side="left")

        self.wait_window(self)

    def _confirm(self):
        self.result = self._entry.get()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ══════════════════════════════════════════════════════════════
# 허용 사이트 관리 창
# ══════════════════════════════════════════════════════════════

class SiteManagerDialog(tk.Toplevel):

    def __init__(self, parent, config: ConfigManager):
        super().__init__(parent)
        self.config = config
        self.title("허용 사이트 관리")
        self.configure(bg="#0D0D0D")
        self.resizable(False, False)
        self.grab_set()

        W, H = 400, 440
        px = parent.winfo_x() + (parent.winfo_width()  - W) // 2
        py = parent.winfo_y() + (parent.winfo_height() - H) // 2
        self.geometry(f"{W}x{H}+{px}+{py}")

        self._build_ui()
        self.wait_window(self)

    def _build_ui(self):
        tk.Label(
            self, text="허용 사이트 관리",
            bg="#0D0D0D", fg="#EEEEEE",
            font=("Courier New", 13, "bold"),
        ).pack(pady=(18, 2))

        tk.Label(
            self, text="타이머 실행 중 열어둘 수 있는 크롬 탭",
            bg="#0D0D0D", fg="#555",
            font=("Courier New", 8),
        ).pack()

        list_frame = tk.Frame(self, bg="#161616")
        list_frame.pack(fill="both", expand=True, padx=20, pady=10)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        self._listbox = tk.Listbox(
            list_frame,
            bg="#161616", fg="#EEEEEE",
            selectbackground="#2A2A2A",
            selectforeground="#4AE3A0",
            font=("Courier New", 10),
            relief="flat", bd=0,
            activestyle="none",
            yscrollcommand=scrollbar.set,
        )
        self._listbox.pack(fill="both", expand=True, padx=8, pady=8)
        scrollbar.config(command=self._listbox.yview)
        self._refresh_list()

        add_frame = tk.Frame(self, bg="#0D0D0D")
        add_frame.pack(fill="x", padx=20, pady=(0, 8))

        self._entry = tk.Entry(
            add_frame,
            bg="#1A1A1A", fg="#EEEEEE",
            insertbackground="#4AE3A0",
            relief="flat",
            font=("Courier New", 10),
        )
        self._entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self._entry.insert(0, "예: notion.so")
        self._entry.bind("<FocusIn>", lambda e: self._clear_placeholder())
        self._entry.bind("<Return>",  lambda e: self._add_site())

        tk.Button(
            add_frame, text="추가",
            bg="#4AE3A0", fg="#000",
            font=("Courier New", 9, "bold"),
            relief="flat", cursor="hand2",
            padx=12, pady=6,
            command=self._add_site,
        ).pack(side="left")

        btn_row = tk.Frame(self, bg="#0D0D0D")
        btn_row.pack(fill="x", padx=20, pady=(0, 14))

        tk.Button(
            btn_row, text="선택 삭제",
            bg="#1A0000", fg="#FF5C5C",
            font=("Courier New", 9),
            relief="flat", cursor="hand2",
            padx=12, pady=6,
            command=self._remove_site,
        ).pack(side="left")

        tk.Button(
            btn_row, text="닫기",
            bg="#1A1A1A", fg="#555",
            font=("Courier New", 9),
            relief="flat", cursor="hand2",
            padx=12, pady=6,
            command=self.destroy,
        ).pack(side="right")

    def _refresh_list(self):
        self._listbox.delete(0, tk.END)
        for site in self.config.allowed_sites:
            self._listbox.insert(tk.END, f"  {site}")

    def _clear_placeholder(self):
        if self._entry.get() == "예: notion.so":
            self._entry.delete(0, tk.END)

    def _add_site(self):
        site = self._entry.get().strip()
        if site and site != "예: notion.so":
            self.config.add_site(site)
            self._entry.delete(0, tk.END)
            self._refresh_list()

    def _remove_site(self):
        sel = self._listbox.curselection()
        if not sel:
            return
        site = self.config.allowed_sites[sel[0]]
        self.config.remove_site(site)
        self._refresh_list()


# ══════════════════════════════════════════════════════════════
# 완료 다이얼로그
# ══════════════════════════════════════════════════════════════

class FinishDialog(tk.Toplevel):

    def __init__(self, parent):
        super().__init__(parent)
        self.title("완료")
        self.configure(bg="#0D0D0D")
        self.resizable(False, False)
        self.grab_set()

        W, H = 320, 210
        px = parent.winfo_x() + (parent.winfo_width()  - W) // 2
        py = parent.winfo_y() + (parent.winfo_height() - H) // 2
        self.geometry(f"{W}x{H}+{px}+{py}")

        tk.Label(
            self, text="공부 완료",
            bg="#0D0D0D", fg="#4AE3A0",
            font=("Courier New", 18, "bold"),
        ).pack(pady=(30, 4))

        tk.Label(
            self, text="수고했습니다.",
            bg="#0D0D0D", fg="#555",
            font=("Courier New", 9),
        ).pack(pady=(0, 20))

        tk.Button(
            self, text="확인",
            bg="#4AE3A0", fg="#000",
            font=("Courier New", 11, "bold"),
            relief="flat", cursor="hand2",
            pady=10, padx=40,
            command=self.destroy,
        ).pack(pady=(0, 8))

        tk.Button(
            self, text="컴퓨터 재부팅",
            bg="#0D0D0D", fg="#FF5C5C",
            font=("Courier New", 9),
            relief="flat", cursor="hand2",
            pady=5,
            command=self._confirm_reboot,
        ).pack()

        self.wait_window(self)

    def _confirm_reboot(self):
        self.destroy()
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to restart'],
            capture_output=True,
        )


# ══════════════════════════════════════════════════════════════
# 메인 앱
# ══════════════════════════════════════════════════════════════

class StudyTimerApp:

    C_BG     = "#0D0D0D"
    C_CARD   = "#161616"
    C_ACCENT = "#4AE3A0"
    C_DIM    = "#1E1E1E"
    C_TEXT   = "#EEEEEE"
    C_SUB    = "#444444"
    C_WARN   = "#FF5C5C"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("공부 타이머")
        self.root.configure(bg=self.C_BG)
        self.root.resizable(False, False)

        W, H = 460, 580
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")

        self.config       = ConfigManager()
        self.kiosk        = KioskMode()
        self.app_blocker  = AppBlocker()
        self.chrome_block = ChromeBlocker(self.config)

        self.remaining = 0
        self.total     = 0
        self.running   = False

        self._stop_event = threading.Event()

        # Cmd+Q 차단 -- 타이머 실행 중 앱 강제 종료 방지
        self.root.createcommand("tk::mac::Quit", self._intercept_quit)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────

    def _build_ui(self):
        # 헤더
        hdr = tk.Frame(self.root, bg=self.C_BG)
        hdr.pack(fill="x", padx=32, pady=(26, 0))

        tk.Label(
            hdr, text="FOCUS",
            bg=self.C_BG, fg=self.C_ACCENT,
            font=("Courier New", 10, "bold"),
        ).pack(side="left")

        tk.Button(
            hdr, text="사이트 관리",
            bg=self.C_BG, fg=self.C_SUB,
            relief="flat", cursor="hand2",
            font=("Courier New", 8),
            command=self._open_site_manager,
        ).pack(side="right")

        # 타이머 카드
        card = tk.Frame(self.root, bg=self.C_CARD)
        card.pack(fill="x", padx=32, pady=(14, 0))

        self.lbl_time = tk.Label(
            card, text="00:00:00",
            bg=self.C_CARD, fg=self.C_TEXT,
            font=("Courier New", 56, "bold"),
            pady=22,
        )
        self.lbl_time.pack()

        self.bar = tk.Canvas(
            card, bg=self.C_CARD, height=3, highlightthickness=0
        )
        self.bar.pack(fill="x")
        self._bar_fill = self.bar.create_rectangle(
            0, 0, 0, 3, fill=self.C_ACCENT, outline=""
        )
        self.bar.bind("<Configure>", lambda e: self._redraw_bar())

        # 상태
        self.lbl_status = tk.Label(
            self.root, text="대기 중",
            bg=self.C_BG, fg=self.C_SUB,
            font=("Courier New", 9),
        )
        self.lbl_status.pack(pady=(10, 0))

        # 시간 입력
        self._build_time_inputs()

        # 프리셋
        self._build_presets()

        # 버튼
        self._build_controls()

        # 허용 사이트 미리보기
        self._build_site_preview()

    def _build_time_inputs(self):
        frm = tk.Frame(self.root, bg=self.C_BG)
        frm.pack(fill="x", padx=32, pady=(18, 0))

        tk.Label(
            frm, text="공부 시간",
            bg=self.C_BG, fg=self.C_SUB,
            font=("Courier New", 8),
        ).pack(anchor="w", pady=(0, 5))

        row = tk.Frame(frm, bg=self.C_BG)
        row.pack(fill="x")

        self.var_h = tk.IntVar(value=0)
        self.var_m = tk.IntVar(value=25)
        self.var_s = tk.IntVar(value=0)

        for label, var, maxv in [("시간", self.var_h, 23),
                                  ("분",   self.var_m, 59),
                                  ("초",   self.var_s, 59)]:
            cell = tk.Frame(row, bg=self.C_DIM, padx=10, pady=8)
            cell.pack(side="left", expand=True, fill="x", padx=(0, 6))
            tk.Label(
                cell, text=label,
                bg=self.C_DIM, fg=self.C_SUB,
                font=("Courier New", 7),
            ).pack()
            tk.Spinbox(
                cell, from_=0, to=maxv, textvariable=var,
                width=4, justify="center",
                bg=self.C_DIM, fg=self.C_TEXT,
                buttonbackground=self.C_DIM,
                relief="flat", bd=0,
                font=("Courier New", 15, "bold"),
                highlightthickness=0,
                format="%02.0f",
            ).pack()

    def _build_presets(self):
        row = tk.Frame(self.root, bg=self.C_BG)
        row.pack(fill="x", padx=32, pady=(10, 0))

        for label, h, m in [("25분", 0, 25), ("45분", 0, 45),
                              ("1시간", 1, 0), ("2시간", 2, 0)]:
            tk.Button(
                row, text=label,
                bg=self.C_DIM, fg=self.C_TEXT,
                relief="flat", cursor="hand2",
                font=("Courier New", 8, "bold"),
                padx=10, pady=5,
                command=lambda h=h, m=m: self._set_preset(h, m),
            ).pack(side="left", padx=(0, 6))

    def _build_controls(self):
        frm = tk.Frame(self.root, bg=self.C_BG)
        frm.pack(fill="x", padx=32, pady=(20, 0))

        self.btn_start = tk.Button(
            frm, text="시작",
            bg=self.C_ACCENT, fg="#000",
            relief="flat", cursor="hand2",
            font=("Courier New", 12, "bold"),
            pady=12,
            command=self._toggle,
        )
        self.btn_start.pack(fill="x")

        btn_row = tk.Frame(frm, bg=self.C_BG)
        btn_row.pack(fill="x", pady=(7, 0))

        self.btn_emergency = tk.Button(
            btn_row, text="비상 해제",
            bg=self.C_DIM, fg=self.C_WARN,
            relief="flat", cursor="hand2",
            font=("Courier New", 9),
            pady=8,
            command=self._emergency_unlock,
        )
        self.btn_emergency.pack(side="left", expand=True, fill="x", padx=(0, 6))

        tk.Button(
            btn_row, text="초기화",
            bg=self.C_DIM, fg=self.C_SUB,
            relief="flat", cursor="hand2",
            font=("Courier New", 9),
            pady=8,
            command=self._reset,
        ).pack(side="left", expand=True, fill="x")

    def _build_site_preview(self):
        frm = tk.Frame(self.root, bg=self.C_BG)
        frm.pack(fill="x", padx=32, pady=(14, 0))

        tk.Label(
            frm, text="허용 사이트",
            bg=self.C_BG, fg=self.C_SUB,
            font=("Courier New", 7),
        ).pack(anchor="w")

        self.lbl_sites = tk.Label(
            frm,
            text=self._sites_text(),
            bg=self.C_BG, fg="#333",
            font=("Courier New", 7),
            wraplength=380, justify="left",
        )
        self.lbl_sites.pack(anchor="w", pady=(2, 0))

    def _sites_text(self) -> str:
        return "  " + " · ".join(self.config.allowed_sites)

    # ── 타이머 ────────────────────────────────────────────────

    def _set_preset(self, h: int, m: int):
        if not self.running:
            self.var_h.set(h)
            self.var_m.set(m)
            self.var_s.set(0)

    def _toggle(self):
        if self.running:
            self._pause()
        else:
            self._start()

    def _start(self):
        if self.remaining == 0:
            total = (self.var_h.get() * 3600
                     + self.var_m.get() * 60
                     + self.var_s.get())
            if total <= 0:
                self._flash("시간을 설정하세요", error=True)
                return
            self.total     = total
            self.remaining = total

        self.running = True
        self._stop_event.clear()

        # 키오스크 + 앱 차단 + 탭 차단 동시 시작
        self.kiosk.enable()
        self.app_blocker.start()
        self.chrome_block.start()

        self.btn_start.config(text="일시정지", bg=self.C_DIM, fg=self.C_TEXT)
        self.lbl_status.config(
            text="집중 중  |  앱 차단 + 탭 차단 활성화",
            fg=self.C_ACCENT,
        )

        threading.Thread(
            target=self._countdown, daemon=True, name="Countdown"
        ).start()

    def _pause(self):
        self.running = False
        self._stop_event.set()

        # 모든 차단 해제
        self.kiosk.disable()
        self.app_blocker.stop()
        self.chrome_block.stop()

        self.btn_start.config(text="재개", bg=self.C_ACCENT, fg="#000")
        self.lbl_status.config(text="일시정지", fg=self.C_SUB)

    def _reset(self):
        self._pause()
        self.remaining = 0
        self.total     = 0
        self.lbl_time.config(text="00:00:00", fg=self.C_TEXT)
        self.btn_start.config(text="시작", bg=self.C_ACCENT, fg="#000")
        self.lbl_status.config(text="대기 중", fg=self.C_SUB)
        self._redraw_bar()

    def _countdown(self):
        while self.remaining > 0 and not self._stop_event.is_set():
            self.root.after(0, self._refresh_display)
            self._stop_event.wait(1)
            if not self._stop_event.is_set():
                self.remaining -= 1

        if not self._stop_event.is_set() and self.remaining <= 0:
            self.remaining = 0
            self.root.after(0, self._on_finish)

    def _refresh_display(self):
        h = self.remaining // 3600
        m = (self.remaining % 3600) // 60
        s = self.remaining % 60
        self.lbl_time.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        self._redraw_bar()

    def _redraw_bar(self):
        w     = self.bar.winfo_width()
        ratio = 1 - (self.remaining / self.total) if self.total > 0 else 0
        self.bar.coords(self._bar_fill, 0, 0, w * ratio, 3)

    def _on_finish(self):
        self.running = False

        # 타이머 종료 시 모든 제한 해제
        self.kiosk.disable()
        self.app_blocker.stop()
        self.chrome_block.stop()

        self.lbl_time.config(fg=self.C_ACCENT)
        self.btn_start.config(text="시작", bg=self.C_ACCENT, fg="#000")
        self.lbl_status.config(text="완료", fg=self.C_ACCENT)
        self._notify()
        FinishDialog(self.root)

    def _notify(self):
        try:
            subprocess.run(
                ["osascript", "-e",
                 'display notification "공부 완료" '
                 'with title "FOCUS Timer" sound name "Glass"'],
                capture_output=True, timeout=3,
            )
        except Exception:
            pass

    # ── 비상 해제 ─────────────────────────────────────────────

    def _emergency_unlock(self):
        dlg = PasswordDialog(self.root, title="비상 해제", prompt="비상 해제 비밀번호:")
        if dlg.result is None:
            return
        if self.config.check_password(dlg.result):
            self._pause()
            self.lbl_status.config(
                text="비상 해제 -- 모든 차단 비활성화", fg=self.C_WARN
            )
        else:
            self._flash("비밀번호가 틀렸습니다", error=True)

    # ── 사이트 관리 ───────────────────────────────────────────

    def _open_site_manager(self):
        dlg = PasswordDialog(self.root, title="사이트 관리", prompt="관리자 비밀번호:")
        if dlg.result is None:
            return
        if self.config.check_password(dlg.result):
            SiteManagerDialog(self.root, self.config)
            self.lbl_sites.config(text=self._sites_text())
        else:
            self._flash("비밀번호가 틀렸습니다", error=True)

    # ── Cmd+Q 차단 ────────────────────────────────────────────

    def _intercept_quit(self):
        """
        타이머 실행 중 Cmd+Q 차단.
        실행 중이 아닐 때는 정상 종료 허용.
        """
        if self.running:
            self._flash("타이머 실행 중에는 종료할 수 없습니다", error=True)
        else:
            self._on_close()

    def _on_close(self):
        self._stop_event.set()
        self.kiosk.disable()
        self.app_blocker.stop()
        self.chrome_block.stop()
        self.root.destroy()

    # ── 유틸 ──────────────────────────────────────────────────

    def _flash(self, msg: str, error: bool = False):
        color = self.C_WARN if error else self.C_ACCENT
        prev_text  = self.lbl_status.cget("text")
        prev_color = self.lbl_status.cget("fg")
        self.lbl_status.config(text=msg, fg=color)
        self.root.after(
            2000,
            lambda: self.lbl_status.config(text=prev_text, fg=prev_color),
        )


# ══════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()
    app  = StudyTimerApp(root)
    root.mainloop()