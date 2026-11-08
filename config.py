"""Середовище: версія, шляхи, налаштування, залізо і все, що залежить від ОС."""
import json
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import platformdirs
import psutil

log = logging.getLogger(__name__)

VERSION = "1.0.33.0"

GITHUB_REPO = "mrbear22/qqq-craft"
LATEST_RELEASE = f"https://github.com/{GITHUB_REPO}/releases/latest"
PACKS_INDEX = f"https://github.com/{GITHUB_REPO}/releases/download/packs/packs.json"
NEWS_INDEX = f"https://github.com/{GITHUB_REPO}/releases/download/news/news.json"

CLIENT_ID = "8015479d-3def-49ae-8f10-2fea4199639f"
FLASK_PORT = 6724
REDIRECT_URL = f"http://localhost:{FLASK_PORT}/auth/callback"

USER_AGENT = f"qqq-craft-launcher/{VERSION} (+https://qqq-craft.top)"

# ── Система: усе, що залежить від ОС, визначається тут і більше ніде ─────────

OS_NAME = {"Windows": "windows", "Linux": "linux"}.get(platform.system())  # як у правилах Mojang
if OS_NAME is None:
    raise OSError(f"Непідтримувана система: {platform.system()}")
IS_WINDOWS = OS_NAME == "windows"
ARCH = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64",
        "x86": "x86", "i386": "x86", "i686": "x86"}.get(platform.machine().lower(), "x64")
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""

# Де лежали файли старого лаунчера до переходу на теку даних системи.
LEGACY_DIRS = ([Path(platformdirs.user_data_dir(appauthor=False)) / "Programs" / "qqq-craft",
                Path(platformdirs.user_data_dir("qqq-craft", appauthor=False))] if IS_WINDOWS else
               [Path(platformdirs.user_data_dir("qqq-craft")), Path.home() / ".qqq-craft"])


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

    return Path(platformdirs.user_data_dir("qqq-craft", appauthor=False, roaming=True))


APP_DIR = _app_dir()
BASE_DIR = _data_dir()
INSTANCES_DIR = BASE_DIR / "instances"
CACHE_DIR = BASE_DIR / "cache"
LOGS_DIR = BASE_DIR / "logs"


def init():
    for folder in (BASE_DIR, INSTANCES_DIR, CACHE_DIR, LOGS_DIR):
        folder.mkdir(parents=True, exist_ok=True)


RAM_PATTERN = re.compile(r"^(\d{1,3})G$")
WINDOW_PATTERN = re.compile(r"^(\d{3,5})x(\d{3,5})$")


def total_ram_gb() -> int:
    return max(2, round(psutil.virtual_memory().total / 1024 ** 3))


def max_ram_gb() -> int:
    total = total_ram_gb()
    return max(2, min(total - 2, int(total * 0.75)))


COMMON_MODES = ("854x480", "1280x720", "1280x800", "1366x768", "1440x900", "1600x900",
                "1680x1050", "1920x1080", "1920x1200", "2560x1440", "2560x1600", "3840x2160")


def screen_modes() -> list[str]:
    """Типові роздільності, що влазять у найбільший монітор, плюс роздільності самих моніторів."""
    try:
        import webview
        screens = {f"{screen.width}x{screen.height}" for screen in webview.screens}
    except Exception as error:
        log.debug("Монітори не визначено: %s", error)
        screens = set()
    width, height = _size(max(screens, key=_pixels, default="1920x1080"))
    fits = {mode for mode in COMMON_MODES if _size(mode)[0] <= width and _size(mode)[1] <= height}
    return sorted(fits | screens, key=lambda mode: -_pixels(mode))


def _size(mode: str) -> tuple[int, int]:
    width, height = mode.split("x")
    return int(width), int(height)


def _pixels(mode: str) -> int:
    width, height = _size(mode)
    return width * height


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
        return _size(self.window)


def open_path(target: str | Path):
    if IS_WINDOWS:
        os.startfile(str(target))
    else:
        subprocess.Popen(["xdg-open", str(target)], start_new_session=True)


def detached(console: bool) -> dict:
    """Параметри Popen для гри: без вікна консолі на Windows, окрема сесія на Linux."""
    if IS_WINDOWS and not console:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def make_executable(path: Path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _dialog(text: str, question: bool) -> bool:
    if IS_WINDOWS:
        import ctypes
        style = 0x24 if question else 0x10
        return ctypes.windll.user32.MessageBoxW(None, text, "QQQ-CRAFT", style) == 6

    for tool, args in (("zenity", ["--question" if question else "--error", f"--text={text}"]),
                       ("kdialog", ["--yesno" if question else "--error", text])):
        if shutil.which(tool):
            return subprocess.run([tool, *args]).returncode == 0

    print(text, file=sys.stderr)
    if not question:
        return False
    return input("Продовжити? [y/N] ").strip().lower().startswith("y")


def alert(title: str, details: str = ""):
    text = f"{title}\n\n{details}" if details else title
    log.error("%s %s", title, details)
    try:
        _dialog(text, question=False)
    except Exception as error:
        log.warning("Діалог недоступний: %s", error)
        print(text, file=sys.stderr)


def ask(question: str) -> bool:
    try:
        return _dialog(question, question=True)
    except Exception as error:
        log.warning("Діалог недоступний: %s", error)
        return False
