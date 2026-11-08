"""Гравець і гра: акаунт Microsoft, вікно входу, пінг сервера, запуск."""
import json
import logging
import os
import socket
import struct
import subprocess
import time
import webbrowser
from pathlib import Path

import minecraft_launcher_lib as mll

from config import BASE_DIR, CLIENT_ID, REDIRECT_URL, Settings, VERSION, detached
from install import LaunchTarget

log = logging.getLogger(__name__)

SERVICE = "qqq-craft-launcher"
ACCOUNT = "refresh-token"

try:
    import keyring
except ImportError:
    keyring = None


def open_login_window(url: str):
    """Вхід у типовому браузері системи — там збережені паролі людини. Повернення на localhost-callback."""
    if not webbrowser.open(url):
        raise RuntimeError("Не вдалося відкрити браузер")


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


def _keyring(action: str, *args):
    """Виклик сховища ОС. False — сховище недоступне або відмовило."""
    if keyring is None:
        return False
    try:
        return getattr(keyring, action)(SERVICE, ACCOUNT, *args)
    except Exception as error:
        log.debug("Сховище ОС: %s не вдалося: %s", action, error)
        return False


class TokenStore:
    def __init__(self):
        self.profile_path = BASE_DIR / "profile.json"
        self.fallback_path = BASE_DIR / ".token"

    def save(self, data: dict):
        self.profile_path.write_text(json.dumps(
            {"name": data["name"], "id": data["id"], "saved_at": time.time()},
            ensure_ascii=False), "utf-8")
        token = data["refresh_token"]
        if keyring and _keyring("set_password", token) is not False:
            self.fallback_path.unlink(missing_ok=True)
            return
        if keyring:
            log.warning("Сховище ОС недоступне, використовую файл")
        self.fallback_path.write_text(token, "utf-8")
        os.chmod(self.fallback_path, 0o600)

    def profile(self) -> dict:
        try:
            return json.loads(self.profile_path.read_text("utf-8"))
        except Exception:
            return {}

    def token(self) -> str | None:
        token = _keyring("get_password")
        if token:
            return token
        try:
            return self.fallback_path.read_text("utf-8").strip() or None
        except Exception:
            return None

    def clear(self):
        _keyring("delete_password")
        self.fallback_path.unlink(missing_ok=True)
        self.profile_path.unlink(missing_ok=True)


class Account:
    def __init__(self):
        self.store = TokenStore()
        self.session: dict | None = None
        self._state = None
        self._verifier = None

    @property
    def _profile(self) -> dict:
        return self.session or self.store.profile()

    @property
    def name(self) -> str | None:
        return self._profile.get("name")

    @property
    def uuid(self) -> str | None:
        return self._profile.get("id")

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


def launch(target: LaunchTarget, settings: Settings, account: Account,
           server: str | None, progress) -> subprocess.Popen:
    version_id, game_dir, java = target
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
    process = subprocess.Popen(command, cwd=str(game_dir), stdout=output, stderr=output,
                               **detached(settings.console))
    log.info("Гру запущено, PID %s", process.pid)
    return process
