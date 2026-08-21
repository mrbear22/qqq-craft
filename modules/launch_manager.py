"""
МОДУЛЬ МЕНЕДЖЕРА ЗАПУСКУ ТА АВТОРИЗАЦІЇ
"""

import os
import re
import time
import uuid
import logging
import platform
import subprocess
import webbrowser
from typing import Dict, Optional, Callable

import minecraft_launcher_lib

from .data_manager import PathManager, DataManager, Config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AuthManager
# ---------------------------------------------------------------------------

class AuthManager:
    """Менеджер Microsoft авторизації"""

    def __init__(self, data_manager: DataManager, client_id: str, redirect_url: str):
        self.data_manager = data_manager
        self.client_id = client_id
        self.redirect_url = redirect_url

        self._state: Optional[str] = None
        self._code_verifier: Optional[str] = None
        self._auth_completed = False
        self._auth_success = False
        self._auth_data: Optional[Dict] = None

    # ------------------------------------------------------------------

    def get_login_url(self) -> tuple:
        """Повертає (login_url, state, code_verifier) для Microsoft OAuth."""
        return minecraft_launcher_lib.microsoft_account.get_secure_login_data(
            self.client_id, self.redirect_url
        )

    def complete_login(self, auth_code: str, code_verifier: str) -> Dict:
        """
        Завершує авторизацію. Правильна сигнатура бібліотеки:
          complete_login(client_id, redirect_uri, auth_code, code_verifier)
        """
        return minecraft_launcher_lib.microsoft_account.complete_login(
            self.client_id, None, self.redirect_url, auth_code, code_verifier
        ) or {}

    def complete_refresh(self, refresh_token: str) -> Dict:
        """
        Оновлює токен. Правильна сигнатура бібліотеки:
          complete_refresh(client_id, client_secret|None, redirect_uri|None, refresh_token)
        """
        return minecraft_launcher_lib.microsoft_account.complete_refresh(
            self.client_id, None, None, refresh_token
        ) or {}

    # ------------------------------------------------------------------

    def start_microsoft_auth(self) -> bool:
        """Відкриває браузер та чекає завершення авторизації (timeout 5 хв)."""
        login_url, state, code_verifier = self.get_login_url()
        if not login_url:
            return False

        self._state = state
        self._code_verifier = code_verifier
        self._auth_completed = False
        self._auth_success = False

        webbrowser.open(login_url)

        deadline = time.time() + 300
        while not self._auth_completed:
            if time.time() > deadline:
                log.error("Authentication timeout")
                return False
            time.sleep(0.5)

        return self._auth_success

    def handle_auth_callback(self, auth_code: str, state: str) -> tuple[bool, str]:
        """
        Обробляє OAuth callback. Повертає (success, message).
        Викликається з Flask route.
        """
        try:
            if state != self._state:
                return False, "Невірний state параметр"

            login_data = self.complete_login(auth_code, self._code_verifier)
            if not login_data or "access_token" not in login_data:
                return False, "Не вдалося отримати дані від Microsoft"

            if not self.save_auth_data(login_data):
                return False, "Не вдалося зберегти дані авторизації"

            self._auth_data = login_data
            self._auth_success = True
            log.info(f"Auth successful: {login_data.get('name')}")
            return True, "Успішно"

        except Exception as e:
            log.error(f"Auth callback error: {e}")
            return False, f"Помилка: {e}"
        finally:
            self._auth_completed = True

    # ------------------------------------------------------------------

    def save_auth_data(self, data: Dict) -> bool:
        data["saved_at"] = time.time()
        return self.data_manager.save(data, "auth.json")

    def load_auth_data(self) -> Dict:
        return self.data_manager.load("auth.json")

    def clear_auth_data(self) -> bool:
        return self.data_manager.delete_file("auth.json")

    # ------------------------------------------------------------------

    def is_token_valid(self, auth_data: Dict) -> bool:
        if not auth_data:
            return False
        if time.time() - auth_data.get("saved_at", 0) > 23 * 3600:
            return False
        return all(k in auth_data for k in ("access_token", "name", "id"))

    def ensure_valid_token(self) -> Optional[Dict]:
        """
        Повертає валідні auth_data (з авто-оновленням якщо потрібно).
        Повертає None якщо токен недійсний і оновлення не вдалося.
        """
        auth_data = self.load_auth_data()
        if not auth_data:
            return None

        if self.is_token_valid(auth_data):
            return auth_data

        log.info("Token expired, refreshing...")
        try:
            refreshed = self.complete_refresh(auth_data["refresh_token"])
            if refreshed and "access_token" in refreshed:
                self.save_auth_data(refreshed)
                return refreshed
        except minecraft_launcher_lib.exceptions.InvalidRefreshToken:
            log.warning("Refresh token invalid — re-login required")
        except Exception as e:
            log.error(f"Token refresh failed: {e}")

        return None


# ---------------------------------------------------------------------------
# JavaRuntimeManager
# ---------------------------------------------------------------------------

class JavaRuntimeManager:
    """Завантаження та перевірка Java runtime у папці гри"""

    def __init__(self, path_manager: PathManager):
        self.path_manager = path_manager

    def get_java_path(self, runtime_name: str, minecraft_dir: str) -> Optional[str]:
        """Повертає шлях до java якщо runtime встановлений, інакше None."""
        path = minecraft_launcher_lib.runtime.get_executable_path(runtime_name, minecraft_dir)
        return path if path and os.path.isfile(path) else None

    def install_runtime(
        self,
        runtime_name: str,
        minecraft_dir: str,
        on_progress: Optional[Callable] = None,
    ) -> bool:
        """Встановлює JVM runtime. Повертає True якщо успішно."""
        log.info(f"Installing runtime '{runtime_name}'...")
        if on_progress:
            on_progress(f"Завантаження Java ({runtime_name})...")

        callback = {
            "setStatus":   lambda s: on_progress(f"Завантаження Java: {s}") if on_progress else None,
            "setProgress": lambda n: on_progress(f"Завантаження Java: {n} файлів") if on_progress else None,
            "setMax":      lambda _: None,
        }

        try:
            minecraft_launcher_lib.runtime.install_jvm_runtime(
                runtime_name, minecraft_dir, callback=callback
            )
            return self.get_java_path(runtime_name, minecraft_dir) is not None
        except Exception as e:
            log.error(f"Failed to install Java runtime {runtime_name}: {e}")
            return False

    def ensure_runtime(
        self,
        runtime_name: str,
        minecraft_dir: str,
        on_progress: Optional[Callable] = None,
    ) -> Optional[str]:
        """
        Повертає шлях до java, встановлює runtime якщо відсутній.
        """
        java_path = self.get_java_path(runtime_name, minecraft_dir)
        if java_path:
            return java_path

        if not self.install_runtime(runtime_name, minecraft_dir, on_progress):
            log.error(f"Failed to install runtime '{runtime_name}'")
            return None

        return self.get_java_path(runtime_name, minecraft_dir)

    @staticmethod
    def get_required_runtime(version_id: str) -> str:
        try:
            info = minecraft_launcher_lib.runtime.get_version_runtime_information(version_id)
            if info and info.get("name"):
                return info["name"]
        except Exception as e:
            log.warning(f"Could not fetch runtime info from manifest for {version_id}: {e}")
        match = re.search(r'(\d+)\.(\d+)', version_id)
        if match:
            major = int(match.group(1))
            if major >= 26:
                return "java-runtime-epsilon"  # Java 25
            elif major == 1:
                minor = int(match.group(2))
                if minor >= 20:
                    return "java-runtime-delta"  # Java 21
                if minor >= 18:
                    return "java-runtime-gamma"  # Java 17
        return "java-runtime-epsilon"


# ---------------------------------------------------------------------------
# MinecraftLauncher
# ---------------------------------------------------------------------------

class MinecraftLauncher:
    """Запуск Minecraft"""

    def __init__(
        self,
        path_manager: PathManager,
        auth_manager: AuthManager,
        java_manager: Optional[JavaRuntimeManager] = None,
    ):
        self.path_manager = path_manager
        self.auth_manager = auth_manager
        self.java_manager = java_manager or JavaRuntimeManager(path_manager)
        self.is_windows = platform.system() == "Windows"

    @staticmethod
    def generate_offline_uuid(nickname: str) -> str:
        namespace = uuid.UUID("00000000-0000-0000-0000-000000000000")
        return str(uuid.uuid3(namespace, f"OfflinePlayer:{nickname}"))

    def launch(self, config: Config, on_progress: Optional[Callable] = None) -> bool:
        def progress(msg: str):
            log.info(msg)
            if on_progress:
                on_progress(msg)

        install_dir = self.path_manager.get_install_dir(config.loader)

        if not install_dir.exists():
            progress(f"Папка гри не знайдена: {install_dir}")
            return False

        progress("Підготовка до запуску...")

        auth_data = self.auth_manager.ensure_valid_token()
        if not auth_data:
            progress("Помилка авторизації — увійдіть знову")
            return False

        runtime_name = self.java_manager.get_required_runtime(config.loader)
        java_path = self.java_manager.ensure_runtime(
            runtime_name, str(install_dir), on_progress
        )
        if not java_path:
            progress("Не вдалося підготувати Java")
            return False
        versions_dir = install_dir / "versions"
        loader_dir = versions_dir / config.loader
        loader_json = loader_dir / f"{config.loader}.json"
        loader_jar = loader_dir / f"{config.loader}.jar"

        if "-" in config.loader:
            base_version = config.loader.split("-")[-1]
            base_dir = versions_dir / base_version
            base_json = base_dir / f"{base_version}.json"
            base_jar = base_dir / f"{base_version}.jar"
        try:
            width, height = config.window_size.split("x")
            width, height = int(width), int(height)
        except (ValueError, AttributeError):
            width, height = 1280, 720

        options: Dict = {
            "username":         auth_data["name"],
            "uuid":             auth_data["id"],
            "token":            auth_data["access_token"],
            "gameDirectory":    str(install_dir),
            "executablePath":   java_path,
            "jvmArguments":     [f"-Xmx{config.ram}"],
            "launcherName":     "QQQ-Launcher",
            "launcherVersion":  "1.0.24.0",
            "customResolution": not config.fullscreen,
            "resolutionWidth":  str(width),
            "resolutionHeight": str(height),
            "fullscreen":       config.fullscreen,
        }

        if config.multiplayer:
            options["quickPlayMultiplayer"] = "play.qqq-craft.top"

        # Формуємо команду
        try:
            command = minecraft_launcher_lib.command.get_minecraft_command(
                config.loader, str(install_dir), options
            )
        except Exception as e:
            log.error(f"❌ Помилка генерування команди minecraft-launcher-lib: {e}", exc_info=True)
            progress("Помилка формування команди запуску")
            return False

        stdout = stderr = subprocess.DEVNULL if not config.console else None

        kwargs = {
            "cwd":    str(install_dir),
            "stdout": stdout,
            "stderr": stderr,
        }

        if self.is_windows and not config.console:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(command, **kwargs)

        log.info(f"Launched — PID: {process.pid}")
        progress("Гру запущено!")
        return True

    # ------------------------------------------------------------------

    def is_minecraft_running(self) -> bool:
        try:
            if self.is_windows:
                import psutil
                return any("java" in p.info["name"].lower()
                           for p in psutil.process_iter(["name"]))
            result = subprocess.run(
                ["pgrep", "-f", "java.*minecraft"], capture_output=True
            )
            return result.returncode == 0
        except Exception as e:
            log.error(f"is_minecraft_running error: {e}")
            return False

    def kill_minecraft(self):
        try:
            if self.is_windows:
                subprocess.run(["taskkill", "/F", "/IM", "java.exe"], capture_output=True)
            else:
                subprocess.run(["pkill", "-f", "java.*minecraft"], capture_output=True)
        except Exception as e:
            log.error(f"kill_minecraft error: {e}")


# ---------------------------------------------------------------------------
# GameProfileManager
# ---------------------------------------------------------------------------

class GameProfileManager:
    """Менеджер профілів гри"""

    def __init__(self, data_manager: DataManager):
        self.data_manager = data_manager

    def _load(self) -> Dict:
        return self.data_manager.load("profiles.json")

    def _save(self, profiles: Dict) -> bool:
        return self.data_manager.save(profiles, "profiles.json")

    def save_profile(self, name: str, config: Config) -> bool:
        profiles = self._load()
        profiles[name] = {
            "loader":      config.loader,
            "ram":         config.ram,
            "window_size": config.window_size,
            "multiplayer": config.multiplayer,
            "console":     config.console,
            "fullscreen":  config.fullscreen,
            "created_at":  time.time(),
        }
        return self._save(profiles)

    def load_profile(self, name: str) -> Optional[Dict]:
        return self._load().get(name)

    def delete_profile(self, name: str) -> bool:
        profiles = self._load()
        profiles.pop(name, None)
        return self._save(profiles)

    def list_profiles(self) -> list:
        return list(self._load().keys())