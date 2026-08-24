import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

VERSION = "1.0.30.0"

NEWS_URL = "https://qqq-craft.top/news/"
GITHUB_REPO = "mrbear22/qqq-craft"
LATEST_RELEASE = f"https://github.com/{GITHUB_REPO}/releases/latest"
PACKS_INDEX = f"https://github.com/{GITHUB_REPO}/releases/download/packs/packs.json"

CLIENT_ID = "8015479d-3def-49ae-8f10-2fea4199639f"
FLASK_PORT = 6724
REDIRECT_URL = f"http://localhost:{FLASK_PORT}/auth/callback"

USER_AGENT = f"qqq-craft-launcher/{VERSION} (+https://qqq-craft.top)"

IS_WINDOWS = platform.system() == "Windows"

if platform.system() not in ("Windows", "Linux"):
    raise OSError(f"Непідтримувана система: {platform.system()}")


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _data_dir() -> Path:
    override = os.environ.get("QQQ_HOME")
    if override:
        return Path(override).expanduser()

    pointer = _app_dir() / "game-dir.txt"
    if pointer.is_file():
        try:
            target = pointer.read_text("utf-8").strip()
            if target:
                return Path(target).expanduser()
        except Exception:
            pass

    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "qqq-craft"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "qqq-craft"


APP_DIR = _app_dir()
BASE_DIR = _data_dir()
INSTANCES_DIR = BASE_DIR / "instances"
CACHE_DIR = BASE_DIR / "cache"
LOGS_DIR = BASE_DIR / "logs"

for _d in (BASE_DIR, INSTANCES_DIR, CACHE_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

RAM_PATTERN = re.compile(r"^(\d{1,3})G$")
WINDOW_PATTERN = re.compile(r"^(\d{3,5})x(\d{3,5})$")


def total_ram_gb() -> int:
    try:
        if IS_WINDOWS:
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return max(2, round(status.ullTotalPhys / 1024 ** 3))
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return max(2, round(int(line.split()[1]) / 1024 ** 2))
    except Exception:
        pass
    return 8


def max_ram_gb() -> int:
    total = total_ram_gb()
    return max(2, min(total - 2, int(total * 0.75)))


def screen_modes() -> list[str]:
    modes = set()
    try:
        if IS_WINDOWS:
            import ctypes

            class DevMode(ctypes.Structure):
                _fields_ = [("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", ctypes.c_ushort),
                            ("dmDriverVersion", ctypes.c_ushort), ("dmSize", ctypes.c_ushort),
                            ("dmDriverExtra", ctypes.c_ushort), ("dmFields", ctypes.c_ulong),
                            ("dmOrientation", ctypes.c_short), ("dmPaperSize", ctypes.c_short),
                            ("dmPaperLength", ctypes.c_short), ("dmPaperWidth", ctypes.c_short),
                            ("dmScale", ctypes.c_short), ("dmCopies", ctypes.c_short),
                            ("dmDefaultSource", ctypes.c_short), ("dmPrintQuality", ctypes.c_short),
                            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                            ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                            ("dmCollate", ctypes.c_short), ("dmFormName", ctypes.c_wchar * 32),
                            ("dmLogPixels", ctypes.c_ushort), ("dmBitsPerPel", ctypes.c_ulong),
                            ("dmPelsWidth", ctypes.c_ulong), ("dmPelsHeight", ctypes.c_ulong),
                            ("dmDisplayFlags", ctypes.c_ulong), ("dmDisplayFrequency", ctypes.c_ulong)]

            mode, index = DevMode(), 0
            mode.dmSize = ctypes.sizeof(DevMode)
            while ctypes.windll.user32.EnumDisplaySettingsW(None, index, ctypes.byref(mode)):
                if mode.dmPelsWidth >= 854 and mode.dmPelsHeight >= 480:
                    modes.add(f"{mode.dmPelsWidth}x{mode.dmPelsHeight}")
                index += 1
        else:
            output = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=5).stdout
            for match in re.finditer(r"^\s+(\d{3,5})x(\d{3,5})", output, re.M):
                width, height = int(match.group(1)), int(match.group(2))
                if width >= 854 and height >= 480:
                    modes.add(f"{width}x{height}")
    except Exception:
        pass

    modes.update({"1280x720", "1600x900", "1920x1080"})
    return sorted(modes, key=lambda mode: -_pixels(mode))


def _pixels(mode: str) -> int:
    width, height = mode.split("x")
    return int(width) * int(height)


@dataclass
class Settings:
    pack: str = ""
    ram: str = "4G"
    window: str = "1280x720"
    fullscreen: bool = False
    console: bool = False
    multiplayer: bool = True

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads((BASE_DIR / "settings.json").read_text("utf-8"))
        except Exception:
            data = {}
        return cls.parse(data)

    @classmethod
    def parse(cls, data: dict) -> "Settings":
        def flag(key, default=False):
            value = data.get(key, default)
            return value if isinstance(value, bool) else str(value).lower() in ("true", "on", "1")

        return cls(
            pack=str(data.get("pack", "")),
            ram=cls._ram(data.get("ram")),
            window=cls._window(data.get("window")),
            fullscreen=flag("fullscreen"),
            console=flag("console"),
            multiplayer=flag("multiplayer", True),
        )

    @staticmethod
    def _ram(value) -> str:
        match = RAM_PATTERN.match(str(value or ""))
        if not match:
            return "4G"
        return f"{max(2, min(int(match.group(1)), max_ram_gb()))}G"

    @staticmethod
    def _window(value) -> str:
        match = WINDOW_PATTERN.match(str(value or ""))
        if not match:
            return "1280x720"
        width, height = int(match.group(1)), int(match.group(2))
        return f"{max(854, min(width, 7680))}x{max(480, min(height, 4320))}"

    def save(self):
        (BASE_DIR / "settings.json").write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), "utf-8"
        )

    @property
    def resolution(self) -> tuple[int, int]:
        width, height = self.window.split("x")
        return int(width), int(height)


def check_update() -> dict | None:
    try:
        response = requests.head(LATEST_RELEASE, timeout=8, allow_redirects=True,
                                 headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        latest = response.url.rstrip("/").rsplit("/", 1)[-1]
        if not latest or not latest[0].isdigit():
            return None
    except Exception:
        return None
    return {"outdated": _as_tuple(latest) > _as_tuple(VERSION),
            "latest": latest, "url": response.url}


def _as_tuple(value: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in value.split("."))


def open_path(target: str | Path):
    target = str(target)
    if IS_WINDOWS:
        os.startfile(target)
    else:
        subprocess.Popen(["xdg-open", target], start_new_session=True)
