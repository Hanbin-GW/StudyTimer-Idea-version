#!/usr/bin/env python3
"""
공부 타이머 — Windows 전용
- 키오스크 모드 (작업표시줄 숨김 + Win키 차단)
- 크롬 외 모든 앱 강제 종료 (taskkill)
- 크롬 탭 차단 — CDP 포트 9222 (허용 사이트 외)
- 일시정지: 비밀번호 입력 → 타이머 정지 + 차단 해제
- 초기화: 비밀번호 입력 → 타이머 리셋 (차단 유지)
- SHA-256 비밀번호 해싱
- 첫 실행 시 크롬 바로가기 자동 수정 (CDP 포트)
"""

import tkinter as tk
import threading
import subprocess
import hashlib
import json
import os
import sys
import platform
from pathlib import Path

if platform.system() != "Windows":
    print("이 파일은 Windows 전용입니다. study_timer_mac.py 를 사용하세요.")
    sys.exit(1)

import ctypes
import ctypes.wintypes

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False

REQUESTS_AVAILABLE = False
try:
    import requests as req_lib
    REQUESTS_AVAILABLE = True
except ImportError:
    pass

# ══════════════════════════════════════════════════════════════
# 상수
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

WIN_SYSTEM_PROCESSES = {
    "explorer.exe", "python.exe", "python3.exe", "pythonw.exe",
    "cmd.exe", "powershell.exe", "windowsterminal.exe", "conhost.exe",
    "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "dwm.exe", "taskmgr.exe",
    "searchui.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "runtimebroker.exe",
    "sihost.exe", "ctfmon.exe", "fontdrvhost.exe", "spoolsv.exe",
    "securityhealthsystray.exe", "securityhealthservice.exe",
    "textinputhost.exe", "searchapp.exe", "lockapp.exe",
}

WIN_ALLOWED_PROCS = {
    "chrome.exe",
} | WIN_SYSTEM_PROCESSES

CDP_PORT = 9222


# ══════════════════════════════════════════════════════════════
# 설정 관리
# ══════════════════════════════════════════════════════════════

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
        return hashlib.sha256(raw.encode()).hexdigest() == self.data["password_hash"]

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

    def __init__(self):
        self._taskbar_hwnd = None
        self._win_hook     = None
        self._hook_proc    = None

    def enable(self):
        self._hide_taskbar()
        self._install_keyboard_hook()

    def disable(self):
        self._show_taskbar()
        self._remove_keyboard_hook()

    def _hide_taskbar(self):
        user32 = ctypes.windll.user32
        self._taskbar_hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if self._taskbar_hwnd:
            user32.ShowWindow(self._taskbar_hwnd, 0)  # SW_HIDE

    def _show_taskbar(self):
        if self._taskbar_hwnd:
            ctypes.windll.user32.ShowWindow(self._taskbar_hwnd, 5)  # SW_SHOW

    def _install_keyboard_hook(self):
        BLOCK_KEYS    = {0x5B, 0x5C}  # LWin, RWin
        WH_KEYBOARD_LL = 13
        WM_KEYDOWN    = 0x0100
        WM_SYSKEYDOWN = 0x0104

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode",      ctypes.wintypes.DWORD),
                ("scanCode",    ctypes.wintypes.DWORD),
                ("flags",       ctypes.wintypes.DWORD),
                ("time",        ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_int,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
        )

        def hook_callback(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(
                    lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)
                ).contents
                if kb.vkCode in BLOCK_KEYS:
                    return 1
            return ctypes.windll.user32.CallNextHookEx(
                self._win_hook, nCode, wParam, lParam
            )

        self._hook_proc = HOOKPROC(hook_callback)
        self._win_hook  = ctypes.windll.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._hook_proc, None, 0
        )

    def _remove_keyboard_hook(self):
        if self._win_hook:
            ctypes.windll.user32.UnhookWindowsHookEx(self._win_hook)
            self._win_hook  = None
            self._hook_proc = None


# ══════════════════════════════════════════════════════════════
# 앱 차단기
# ══════════════════════════════════════════════════════════════

class AppBlocker:

    POLL_INTERVAL = 3

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _get_procs(self) -> list[str]:
        try:
            r = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            procs = []
            for line in r.stdout.strip().splitlines():
                line = line.strip()
                if line:
                    name = line.split(",")[0].strip('"').lower()
                    procs.append(name)
            return procs
        except Exception:
            return []

    def _kill(self, proc_name: str):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", proc_name],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except Exception:
            pass

    def _loop(self):
        while not self._stop_event.is_set():
            for proc in self._get_procs():
                if proc not in WIN_ALLOWED_PROCS:
                    self._kill(proc)
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
# 크롬 탭 차단기 (CDP)
# ══════════════════════════════════════════════════════════════

class ChromeBlocker:

    POLL_INTERVAL = 5

    def __init__(self, config: ConfigManager):
        self.config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _run_once(self):
        if not REQUESTS_AVAILABLE:
            return
        try:
            tabs = req_lib.get(
                f"http://localhost:{CDP_PORT}/json", timeout=3
            ).json()
            for tab in tabs:
                if tab.get("type") != "page":
                    continue
                url = tab.get("url", "")
                if not url or url in ("chrome://newtab/", "about:blank"):
                    continue
                if not any(d in url for d in self.config.allowed_sites):
                    tab_id = tab.get("id", "")
                    if tab_id:
                        req_lib.get(
                            f"http://localhost:{CDP_PORT}/json/close/{tab_id}",
                            timeout=3
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
# 크롬 바로가기 자동 수정
# ══════════════════════════════════════════════════════════════

def patch_chrome_shortcut() -> bool:
    if not WINREG_AVAILABLE:
        return False

    CDP_FLAG = f"--remote-debugging-port={CDP_PORT}"
    chrome_exe = None

    reg_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
    ]
    for rp in reg_paths:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rp)
            chrome_exe, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            if chrome_exe:
                break
        except Exception:
            continue

    if not chrome_exe or not Path(chrome_exe).exists():
        return False

    shortcut_dirs = [
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
        Path(os.environ.get("APPDATA",     ""))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("PUBLIC", "")) / "Desktop",
    ]

    patched = False
    for d in shortcut_dirs:
        if not d.exists():
            continue
        for lnk in d.rglob("*.lnk"):
            if "chrome" not in lnk.stem.lower():
                continue
            try:
                ps = f"""
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut('{str(lnk)}')
if ($lnk.Arguments -notlike '*{CDP_FLAG}*') {{
    $lnk.Arguments = $lnk.Arguments + ' {CDP_FLAG}'
    $lnk.Save()
    Write-Output 'patched'
}} else {{
    Write-Output 'already'
}}
"""
                r = subprocess.run(
                    ["powershell", "-Command", ps],
                    capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=10
                )
                if "patched" in r.stdout or "already" in r.stdout:
                    patched = True
            except Exception:
                continue

    return patched


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
        self._entry.bind("<Return>", lambda e: self._confirm())
        self._entry.bind("<Escape>", lambda e: self._cancel())

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

        sb = tk.Scrollbar(list_frame)
        sb.pack(side="right", fill="y")

        self._listbox = tk.Listbox(
            list_frame,
            bg="#161616", fg="#EEEEEE",
            selectbackground="#2A2A2A",
            selectforeground="#4AE3A0",
            font=("Courier New", 10),
            relief="flat", bd=0,
            activestyle="none",
            yscrollcommand=sb.set,
        )
        self._listbox.pack(fill="both", expand=True, padx=8, pady=8)
        sb.config(command=self._listbox.yview)
        self._refresh_list()

        add_frame = tk.Frame(self, bg="#0D0D0D")
        add_frame.pack(fill="x", padx=20, pady=(0, 8))

        self._entry = tk.Entry(
            add_frame,
            bg="#1A1A1A", fg="#EEEEEE",
            insertbackground="#4AE3A0",
            relief="flat", font=("Courier New", 10),
        )
        self._entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self._entry.insert(0, "예: notion.so")
        self._entry.bind("<FocusIn>", lambda e: self._clear_ph())
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

    def _clear_ph(self):
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
        self.config.remove_site(self.config.allowed_sites[sel[0]])
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
            relief="flat", cursor="hand2", pady=5,
            command=self._reboot,
        ).pack()

        self.wait_window(self)

    def _reboot(self):
        self.destroy()
        subprocess.run(["shutdown", "/r", "/t", "0"])


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

        W, H = 460, 560
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

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 크롬 바로가기 백그라운드 수정
        threading.Thread(
            target=self._setup_chrome, daemon=True
        ).start()

        self._build_ui()

    def _setup_chrome(self):
        patched = patch_chrome_shortcut()
        msg = "크롬 CDP 설정 완료" if patched else "크롬 바로가기 수동 설정 필요"
        self.root.after(0, lambda: self.lbl_status.config(text=msg))

    # ── UI ────────────────────────────────────────────────────

    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=self.C_BG)
        hdr.pack(fill="x", padx=32, pady=(26, 0))

        tk.Label(
            hdr, text="FOCUS  Windows",
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

        self.lbl_status = tk.Label(
            self.root, text="대기 중",
            bg=self.C_BG, fg=self.C_SUB,
            font=("Courier New", 9),
        )
        self.lbl_status.pack(pady=(10, 0))

        self._build_time_inputs()
        self._build_presets()
        self._build_controls()
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
            command=self._start,
        )
        self.btn_start.pack(fill="x")

        btn_row = tk.Frame(frm, bg=self.C_BG)
        btn_row.pack(fill="x", pady=(7, 0))

        self.btn_pause = tk.Button(
            btn_row, text="일시정지",
            bg=self.C_DIM, fg=self.C_TEXT,
            relief="flat", cursor="hand2",
            font=("Courier New", 9), pady=8,
            state="disabled",
            command=self._request_pause,
        )
        self.btn_pause.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_reset = tk.Button(
            btn_row, text="초기화",
            bg=self.C_DIM, fg=self.C_SUB,
            relief="flat", cursor="hand2",
            font=("Courier New", 9), pady=8,
            state="disabled",
            command=self._request_reset,
        )
        self.btn_reset.pack(side="left", expand=True, fill="x")

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

        self.kiosk.enable()
        if not self.app_blocker._thread or not self.app_blocker._thread.is_alive():
            self.app_blocker.start()
        if not self.chrome_block._thread or not self.chrome_block._thread.is_alive():
            self.chrome_block.start()

        self.btn_start.config(state="disabled", bg=self.C_DIM, fg=self.C_SUB)
        self.btn_pause.config(state="normal",   fg=self.C_TEXT)
        self.btn_reset.config(state="normal")
        self.lbl_status.config(
            text="집중 중  |  앱 차단 + 탭 차단 활성화",
            fg=self.C_ACCENT,
        )

        threading.Thread(
            target=self._countdown, daemon=True, name="Countdown"
        ).start()

    def _do_pause(self):
        self.running = False
        self._stop_event.set()
        self.kiosk.disable()
        self.app_blocker.stop()
        self.chrome_block.stop()
        self.btn_start.config(state="normal", text="재개", bg=self.C_ACCENT, fg="#000")
        self.btn_pause.config(state="disabled")
        self.btn_reset.config(state="normal")
        self.lbl_status.config(text="일시정지  |  차단 해제됨", fg=self.C_SUB)

    def _do_reset(self):
        self._stop_event.set()
        self.running   = False
        self.remaining = 0
        self.total     = 0
        self.lbl_time.config(text="00:00:00", fg=self.C_TEXT)
        self.btn_start.config(state="normal", text="시작", bg=self.C_ACCENT, fg="#000")
        self.btn_pause.config(state="disabled")
        self.btn_reset.config(state="disabled")
        self.lbl_status.config(text="대기 중  |  차단 유지 중", fg=self.C_SUB)
        self._redraw_bar()

    def _request_pause(self):
        dlg = PasswordDialog(self.root, title="일시정지", prompt="비밀번호를 입력하세요:")
        if dlg.result is None:
            return
        if self.config.check_password(dlg.result):
            self._do_pause()
        else:
            self._flash("비밀번호가 틀렸습니다", error=True)

    def _request_reset(self):
        dlg = PasswordDialog(self.root, title="초기화", prompt="비밀번호를 입력하세요:")
        if dlg.result is None:
            return
        if self.config.check_password(dlg.result):
            self._do_reset()
        else:
            self._flash("비밀번호가 틀렸습니다", error=True)

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
        self.kiosk.disable()
        self.app_blocker.stop()
        self.chrome_block.stop()
        self.lbl_time.config(fg=self.C_ACCENT)
        self.btn_start.config(state="normal", text="시작", bg=self.C_ACCENT, fg="#000")
        self.btn_pause.config(state="disabled")
        self.btn_reset.config(state="disabled")
        self.lbl_status.config(text="완료", fg=self.C_ACCENT)
        self._notify()
        FinishDialog(self.root)

    def _notify(self):
        try:
            ps = (
                'Add-Type -AssemblyName System.Windows.Forms;'
                '[System.Windows.Forms.MessageBox]::Show('
                '"공부 완료! 수고했습니다.","FOCUS Timer")'
            )
            subprocess.Popen(
                ["powershell", "-Command", ps],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except Exception:
            pass

    def _open_site_manager(self):
        dlg = PasswordDialog(self.root, title="사이트 관리", prompt="관리자 비밀번호:")
        if dlg.result is None:
            return
        if self.config.check_password(dlg.result):
            SiteManagerDialog(self.root, self.config)
            self.lbl_sites.config(text=self._sites_text())
        else:
            self._flash("비밀번호가 틀렸습니다", error=True)

    def _on_close(self):
        self._stop_event.set()
        self.kiosk.disable()
        self.app_blocker.stop()
        self.chrome_block.stop()
        self.root.destroy()

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
