import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import time
import webbrowser
from pathlib import Path

import minecraft_launcher_lib as mll

from config import BASE_DIR, CLIENT_ID, IS_WINDOWS, REDIRECT_URL, Settings, VERSION

log = logging.getLogger(__name__)

SERVICE = "qqq-craft-launcher"
ACCOUNT = "refresh-token"

try:
    import keyring
except ImportError:
    keyring = None


CHROMIUM_NAMES = ["chrome", "google-chrome", "google-chrome-stable", "chromium",
                  "chromium-browser", "msedge", "microsoft-edge", "microsoft-edge-stable",
                  "brave", "brave-browser", "vivaldi", "vivaldi-stable", "opera"]
CHROMIUM_MARKERS = ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe",
                    "chromium.exe")


def _default_browser_windows() -> str | None:
    try:
        import winreg
        key = (r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
               r"\https\UserChoice")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            prog_id = winreg.QueryValueEx(handle, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            rf"{prog_id}\shell\open\command") as handle:
            command = winreg.QueryValueEx(handle, "")[0]
    except Exception as error:
        log.debug("Типовий браузер не визначено: %s", error)
        return None

    path = command.split('"')[1] if command.startswith('"') else command.split(" ")[0]
    return path if path.lower().endswith(CHROMIUM_MARKERS) and Path(path).is_file() else None


def _chromium_candidates() -> list[str]:
    found = []
    if IS_WINDOWS:
        default = _default_browser_windows()
        if default:
            found.append(default)
        roots = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                 os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")]
        relative = [r"Google\Chrome\Application\chrome.exe",
                    r"Microsoft\Edge\Application\msedge.exe",
                    r"BraveSoftware\Brave-Browser\Application\brave.exe",
                    r"Vivaldi\Application\vivaldi.exe",
                    r"Chromium\Application\chrome.exe"]
        found += [str(Path(root) / tail) for root in roots if root for tail in relative
                  if (Path(root) / tail).is_file()]
    found += [path for name in CHROMIUM_NAMES if (path := shutil.which(name))]
    return list(dict.fromkeys(found))


def open_login_window(url: str):
    for browser in _chromium_candidates():
        try:
            subprocess.Popen([browser, f"--app={url}", "--window-size=500,720"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=not IS_WINDOWS)
            log.info("Логін відкрито через %s", browser)
            return
        except Exception as error:
            log.warning("Не вдалося відкрити %s: %s", browser, error)

    log.info("Chromium-браузер не знайдено, відкриваю типовий")
    webbrowser.open(url)


def _varint(value: int) -> bytes:
    data = b""
    while True:
        chunk = value & 0x7F
        value >>= 7
        data += struct.pack("B", chunk | (0x80 if value else 0))
        if not value:
            return data


def _read_varint(sock) -> int:
    value = shift = 0
    while True:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("зʼєднання закрито")
        value |= (byte[0] & 0x7F) << shift
        if not byte[0] & 0x80:
            return value
        shift += 7


def server_status(address: str, timeout: float = 3.0) -> dict | None:
    host, _, port_text = address.partition(":")
    port = int(port_text) if port_text.isdigit() else 25565
    try:
        with socket.create_connection((host, port), timeout) as sock:
            name = host.encode()
            handshake = (b"\x00" + _varint(770) + _varint(len(name)) + name
                         + struct.pack(">H", port) + _varint(1))
            sock.sendall(_varint(len(handshake)) + handshake)
            sock.sendall(b"\x01\x00")

            _read_varint(sock)
            if _read_varint(sock) != 0:
                return None
            size = _read_varint(sock)
            payload = b""
            while len(payload) < size:
                chunk = sock.recv(size - len(payload))
                if not chunk:
                    break
                payload += chunk

        players = json.loads(payload.decode("utf-8", "replace")).get("players", {})
        return {"online": players.get("online", 0), "max": players.get("max", 0)}
    except Exception as error:
        log.debug("Сервер %s недоступний: %s", address, error)
        return None


class TokenStore:
    def __init__(self):
        self.profile_path = BASE_DIR / "profile.json"
        self.fallback_path = BASE_DIR / ".token"

    def save(self, data: dict):
        self.profile_path.write_text(json.dumps(
            {"name": data["name"], "id": data["id"], "saved_at": time.time()},
            ensure_ascii=False), "utf-8")
        token = data["refresh_token"]
        if keyring:
            try:
                keyring.set_password(SERVICE, ACCOUNT, token)
                self.fallback_path.unlink(missing_ok=True)
                return
            except Exception as error:
                log.warning("Сховище ОС недоступне (%s), використовую файл", error)
        self.fallback_path.write_text(token, "utf-8")
        if not IS_WINDOWS:
            os.chmod(self.fallback_path, 0o600)

    def profile(self) -> dict:
        try:
            return json.loads(self.profile_path.read_text("utf-8"))
        except Exception:
            return {}

    def token(self) -> str | None:
        if keyring:
            try:
                token = keyring.get_password(SERVICE, ACCOUNT)
                if token:
                    return token
            except Exception:
                pass
        try:
            return self.fallback_path.read_text("utf-8").strip() or None
        except Exception:
            return None

    def clear(self):
        if keyring:
            try:
                keyring.delete_password(SERVICE, ACCOUNT)
            except Exception:
                pass
        self.fallback_path.unlink(missing_ok=True)
        self.profile_path.unlink(missing_ok=True)


class Account:
    def __init__(self):
        self.store = TokenStore()
        self.session: dict | None = None
        self._state = None
        self._verifier = None

    @property
    def name(self) -> str | None:
        return (self.session or self.store.profile()).get("name")

    @property
    def uuid(self) -> str | None:
        return (self.session or self.store.profile()).get("id")

    @property
    def logged_in(self) -> bool:
        return bool(self.name) and (self.session is not None or self.store.token() is not None)

    def begin_login(self) -> str:
        url, self._state, self._verifier = mll.microsoft_account.get_secure_login_data(
            CLIENT_ID, REDIRECT_URL)
        return url

    def complete_login(self, code: str, state: str) -> tuple[bool, str]:
        if state != self._state:
            return False, "Невірний параметр state"
        try:
            data = mll.microsoft_account.complete_login(
                CLIENT_ID, None, REDIRECT_URL, code, self._verifier)
        except Exception as error:
            log.error("Помилка авторизації: %s", error)
            return False, str(error)
        if not data or "access_token" not in data:
            return False, "Microsoft не повернув дані профілю"
        self.session = data
        self.store.save(data)
        return True, "OK"

    def ensure_session(self) -> dict | None:
        if self.session:
            return self.session
        token = self.store.token()
        if not token:
            return None
        try:
            data = mll.microsoft_account.complete_refresh(CLIENT_ID, None, None, token)
        except mll.exceptions.InvalidRefreshToken:
            log.warning("Refresh-токен недійсний — потрібен повторний вхід")
            self.store.clear()
            return None
        except Exception as error:
            log.error("Не вдалося оновити токен: %s", error)
            return None
        self.session = data
        self.store.save(data)
        return data

    def logout(self):
        self.session = None
        self.store.clear()


def _write_options(game_dir: Path, settings: Settings):
    """Повноекранний режим живе лише в options.txt — аргументів запуску для нього немає."""
    path = game_dir / "options.txt"
    lines = path.read_text("utf-8").splitlines() if path.is_file() else []
    value = f"fullscreen:{str(settings.fullscreen).lower()}"

    for index, line in enumerate(lines):
        if line.startswith("fullscreen:"):
            lines[index] = value
            break
    else:
        lines.append(value)

    path.write_text("\n".join(lines) + "\n", "utf-8")


def launch(version_id: str, game_dir: Path, java: str, settings: Settings,
           account: Account, server: str | None, progress) -> subprocess.Popen:
    auth = account.ensure_session()
    if not auth:
        raise RuntimeError("Потрібна авторизація Microsoft")

    width, height = settings.resolution
    _write_options(game_dir, settings)
    options = {
        "username": auth["name"],
        "uuid": auth["id"],
        "token": auth["access_token"],
        "gameDirectory": str(game_dir),
        "executablePath": java,
        "jvmArguments": [f"-Xmx{settings.ram}"],
        "launcherName": "QQQ-Launcher",
        "launcherVersion": VERSION,
        "customResolution": True,
        "resolutionWidth": str(width),
        "resolutionHeight": str(height),
    }
    if settings.multiplayer and server:
        options["quickPlayMultiplayer"] = server

    progress("Формування команди запуску", None)
    command = mll.command.get_minecraft_command(version_id, str(game_dir), options)

    output = None if settings.console else subprocess.DEVNULL
    kwargs = {"cwd": str(game_dir), "stdout": output, "stderr": output}
    if IS_WINDOWS and not settings.console:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    log.info("Гру запущено, PID %s", process.pid)
    return process
