"""
МОДУЛЬ МЕНЕДЖЕРА ЗАПУСКУ ТА АВТОРИЗАЦІЇ
Містить класи для авторизації Microsoft та запуску Minecraft
"""

import os
import time
import uuid
import logging
import platform
import threading
import subprocess
import webbrowser
from typing import Dict, Optional, Callable

import minecraft_launcher_lib

from .data_manager import PathManager, DataManager, Config

class AuthManager:
    """Менеджер Microsoft авторизації"""
    
    def __init__(self, data_manager: DataManager, client_id: str, redirect_url: str):
        self.data_manager = data_manager
        self.client_id = client_id
        self.redirect_url = redirect_url
        self.logger = logging.getLogger(__name__)
        
        self.auth_state = None
        self.auth_code_verifier = None
        self.auth_completed = False
        self.auth_success = False
        self.auth_data = None
    
    def get_login_url(self):
        """
        Отримує URL для Microsoft авторизації
        
        Returns:
            tuple: (login_url, state, code_verifier)
        """
        try:
            return minecraft_launcher_lib.microsoft_account.get_secure_login_data(
                self.client_id, self.redirect_url
            )
        except Exception as e:
            self.logger.error(f"Failed to get login URL: {e}")
            return "", "", ""
    
    def complete_login(self, auth_code: str, state: str, code_verifier: str) -> Dict:
        """
        Завершує Microsoft авторизацію
        
        Args:
            auth_code: Код авторизації
            state: State параметр
            code_verifier: Code verifier для PKCE
            
        Returns:
            Dict: Дані авторизації або порожній словник
        """
        try:
            login_data = minecraft_launcher_lib.microsoft_account.complete_login(
                self.client_id, None, self.redirect_url, auth_code, code_verifier
            )
            
            if login_data and 'access_token' in login_data and 'name' in login_data:
                return login_data
            
            return {}
            
        except Exception as e:
            self.logger.error(f"Login completion failed: {e}")
            return {}
    
    def start_microsoft_auth(self) -> bool:
        """
        Запускає Microsoft авторизацію через браузер
        
        Returns:
            bool: True якщо авторизація успішна
        """
        try:
            login_url, state, code_verifier = self.get_login_url()
            if not login_url:
                self.logger.error("Failed to get login URL")
                return False
            
            self.auth_state = state
            self.auth_code_verifier = code_verifier
            self.auth_completed = False
            self.auth_success = False
            self.auth_data = None
            
            self.logger.info("Opening browser for Microsoft authentication")

            webbrowser.open(login_url)

            timeout = 300
            start_time = time.time()
            
            while not self.auth_completed:
                if time.time() - start_time > timeout:
                    self.logger.error("Authentication timeout")
                    return False
                time.sleep(0.5)
            
            return self.auth_success
            
        except Exception as e:
            self.logger.error(f"Microsoft auth error: {e}")
            return False
    
    def handle_auth_callback(self, auth_code: str, state: str) -> tuple[bool, str]:
        """
        Обробляє callback з Flask route
        
        Args:
            auth_code: Код авторизації з callback
            state: State параметр
            
        Returns:
            tuple: (success: bool, error_message: str)
        """
        try:
            if state != self.auth_state:
                error_msg = f"Невірний state параметр"
                self.logger.error(f"Invalid state parameter: expected {self.auth_state}, got {state}")
                return False, error_msg

            login_data = self.complete_login(auth_code, state, self.auth_code_verifier)
            
            if not login_data:
                error_msg = "Не вдалося отримати дані від Microsoft"
                self.logger.error(error_msg)
                return False, error_msg

            if self.save_auth_data(login_data):
                self.auth_success = True
                self.auth_data = login_data
                self.logger.info(f"Authentication successful for user: {login_data.get('name')}")
                return True, "Успішно"
            else:
                error_msg = "Не вдалося зберегти дані авторизації"
                self.logger.error(error_msg)
                return False, error_msg
            
        except Exception as e:
            error_msg = f"Помилка обробки авторизації: {str(e)}"
            self.logger.error(f"Auth callback error: {e}")
            return False, error_msg
        finally:
            self.auth_completed = True
    
    def save_auth_data(self, auth_data: Dict) -> bool:
        """
        Зберігає дані авторизації
        
        Args:
            auth_data: Дані авторизації
            
        Returns:
            bool: True якщо збереження успішне
        """
        try:
            auth_data['saved_at'] = time.time()
            return self.data_manager.save(auth_data, "auth.json")
        except Exception as e:
            self.logger.error(f"Failed to save auth data: {e}")
            return False
    
    def load_auth_data(self) -> Dict:
        """
        Завантажує збережені дані авторизації
        
        Returns:
            Dict: Дані авторизації або порожній словник
        """
        try:
            return self.data_manager.load("auth.json")
        except Exception as e:
            self.logger.error(f"Failed to load auth data: {e}")
            return {}
    
    def clear_auth_data(self) -> bool:
        """
        Видаляє збережені дані авторизації
        
        Returns:
            bool: True якщо видалення успішне
        """
        try:
            return self.data_manager.delete_file("auth.json")
        except Exception as e:
            self.logger.error(f"Failed to clear auth data: {e}")
            return False
    
    def is_token_valid(self, auth_data: Dict) -> bool:
        """Перевіряє чи токен дійсний (не застарів)"""
        try:
            if not auth_data or 'access_token' not in auth_data:
                return False

            saved_at = auth_data.get('saved_at', 0)
            if time.time() - saved_at > 23 * 3600:  # 23 години
                self.logger.info("Auth token expired")
                return False

            required_fields = ['access_token', 'name', 'id']
            for field in required_fields:
                if field not in auth_data:
                    self.logger.error(f"Missing required field: {field}")
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Token validation error: {e}")
            return False
    
    def refresh_token(self, auth_data: Dict) -> Optional[Dict]:
        """Оновлює токен через refresh_token"""
        try:
            if 'refresh_token' not in auth_data:
                self.logger.error("No refresh token available")
                return None
            
            self.logger.info("Refreshing authorization token...")

            refreshed_data = minecraft_launcher_lib.microsoft_account.complete_refresh(
                self.client_id,
                None,  # client_secret (deprecated, не використовується)
                self.redirect_url,
                auth_data['refresh_token']
            )
            
            if refreshed_data and 'access_token' in refreshed_data:
                refreshed_data['saved_at'] = time.time()
                
                if self.save_auth_data(refreshed_data):
                    self.logger.info("Token refreshed successfully")
                    return refreshed_data
                else:
                    self.logger.error("Failed to save refreshed token")
                    
        except Exception as e:
            self.logger.error(f"Token refresh failed: {e}")
        
        return None
        
    def auto_refresh_token_if_needed(self) -> bool:
        """
        Автоматично оновлює токен якщо потрібно.
        Викликається один раз при старті програми.
        
        Returns:
            bool: True якщо токен валідний (оновлений або ще не застарів)
        """
        try:
            auth_data = self.load_auth_data()

            if not auth_data:
                self.logger.info("No auth data found")
                return False

            if self.is_token_valid(auth_data):
                self.logger.info("Token is still valid")
                return True

            self.logger.info("Token expired, attempting refresh...")
            refreshed_data = self.refresh_token(auth_data)
            
            if refreshed_data:
                self.logger.info("Token auto-refresh successful")
                return True
            else:
                self.logger.warning("Token refresh failed - user needs to login again")
                return False
                
        except Exception as e:
            self.logger.error(f"Auto refresh error: {e}")
            return False

class MinecraftLauncher:
    """Клас для запуску Minecraft"""
    
    def __init__(self, path_manager: PathManager, auth_manager: AuthManager):
        self.path_manager = path_manager
        self.auth_manager = auth_manager
        self.logger = logging.getLogger(__name__)
        self.is_windows = platform.system() == "Windows"
    
    @staticmethod
    def generate_offline_uuid(nickname: str) -> str:
        """
        Генерує UUID для офлайн режиму
        
        Args:
            nickname: Нікнейм гравця
            
        Returns:
            str: UUID
        """
        try:
            namespace = uuid.UUID('00000000-0000-0000-0000-000000000000')
            return str(uuid.uuid3(namespace, f"OfflinePlayer:{nickname}"))
        except Exception as e:
            logging.getLogger(__name__).error(f"UUID generation error: {e}")
            return str(uuid.uuid4())
    
    def prepare_launch_options(self, config: Config) -> Dict:
        """
        Підготовляє опції для запуску Minecraft
        
        Args:
            config: Конфігурація гри
            
        Returns:
            Dict: Опції запуску
        """
        install_dir = self.path_manager.get_install_dir(config.loader)

        try:
            width, height = config.window_size.split('x')
            width, height = int(width), int(height)
        except (ValueError, AttributeError):
            width, height = 1280, 720

        options = {
            "gameDirectory": str(install_dir),
            "jvmArguments": [f"-Xmx{config.ram}"],
            "launcherName": "QQQ-Launcher",
            "launcherVersion": "1.0.24.0",
            "customResolution": not config.fullscreen,
            "resolutionWidth": str(width),
            "resolutionHeight": str(height),
            "fullscreen": config.fullscreen,
        }
        
        return options
    
    def setup_authentication(self, options: Dict) -> bool:
        """Налаштовує авторизацію для запуску"""
        auth_data = self.auth_manager.load_auth_data()

        if not self.auth_manager.is_token_valid(auth_data):
            self.logger.info("Token expired during launch, attempting refresh...")
            
            refreshed_data = self.auth_manager.refresh_token(auth_data)
            if refreshed_data:
                auth_data = refreshed_data
            else:
                self.logger.error("Token refresh failed - authentication required")
                return False

        options.update({
            "username": auth_data["name"],
            "uuid": auth_data["id"],
            "token": auth_data["access_token"]
        })
        
        return True
    
    def setup_multiplayer(self, config: Config, options: Dict):
        """
        Налаштовує параметри мультиплеєра
        
        Args:
            config: Конфігурація гри
            options: Словник опцій запуску (модифікується)
        """
        if config.multiplayer:
            options["quickPlayMultiplayer"] = "play.qqq-craft.top"
    
    def get_launch_command(self, config: Config, options: Dict) -> list:
        """
        Генерує команду для запуску Minecraft
        
        Args:
            config: Конфігурація гри
            options: Опції запуску
            
        Returns:
            list: Команда запуску
        """
        install_dir = self.path_manager.get_install_dir(config.loader)
        
        return minecraft_launcher_lib.command.get_minecraft_command(
            config.loader, str(install_dir), options
        )
    
    def launch(self, config: Config, progress_callback: Optional[Callable] = None) -> bool:
        """
        Запускає Minecraft
        
        Args:
            config: Конфігурація гри
            progress_callback: Функція для повідомлень про прогрес
            
        Returns:
            bool: True якщо запуск успішний
        """
        try:
            install_dir = self.path_manager.get_install_dir(config.loader)
            
            if not install_dir.exists():
                error_msg = f"Папка гри не знайдена: {install_dir}"
                self.logger.error(error_msg)
                if progress_callback:
                    progress_callback(error_msg)
                return False
            
            if progress_callback:
                progress_callback("Підготовка до запуску...")

            options = self.prepare_launch_options(config)

            if not self.setup_authentication(options):
                error_msg = "Помилка авторизації"
                if progress_callback:
                    progress_callback(error_msg)
                return False

            self.setup_multiplayer(config, options)
            
            if progress_callback:
                progress_callback("Генерація команди запуску...")

            command = self.get_launch_command(config, options)
            
            if progress_callback:
                progress_callback("Запуск гри...")

            creation_flags = 0
            stdout = stderr = None
            
            if self.is_windows and not config.console:
                creation_flags = subprocess.CREATE_NO_WINDOW
                stdout = stderr = subprocess.DEVNULL
            elif not config.console:
                stdout = stderr = subprocess.DEVNULL

            process = subprocess.Popen(
                command,
                cwd=str(install_dir),
                creationflags=creation_flags,
                stdout=stdout,
                stderr=stderr
            )

            self.logger.info(f"Minecraft launched successfully with PID: {process.pid}")
            self.logger.info(f"Loader: {config.loader}, RAM: {config.ram}, Resolution: {config.window_size}")
            
            if progress_callback:
                progress_callback("Гру запущено успішно!")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Launch failed: {e}")
            error_msg = f"Помилка запуску: {e}"
            if progress_callback:
                progress_callback(error_msg)
            return False
    
    def is_minecraft_running(self) -> bool:
        """
        Перевіряє чи запущений Minecraft
        
        Returns:
            bool: True якщо Minecraft запущений
        """
        try:
            if self.is_windows:
                import psutil
                for proc in psutil.process_iter(['pid', 'name']):
                    if 'java' in proc.info['name'].lower():
                        return True
            else:
                result = subprocess.run(['pgrep', '-f', 'java.*minecraft'], 
                                      capture_output=True, text=True)
                return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Failed to check if Minecraft is running: {e}")
        
        return False
    
    def kill_minecraft_processes(self):
        """Завершує всі процеси Minecraft"""
        try:
            if self.is_windows:
                subprocess.run(['taskkill', '/F', '/IM', 'java.exe'], 
                              capture_output=True)
            else:
                subprocess.run(['pkill', '-f', 'java.*minecraft'], 
                              capture_output=True)
            self.logger.info("Minecraft processes terminated")
        except Exception as e:
            self.logger.error(f"Failed to kill Minecraft processes: {e}")


class GameProfileManager:
    """Менеджер профілів гри"""
    
    def __init__(self, data_manager: DataManager):
        self.data_manager = data_manager
        self.logger = logging.getLogger(__name__)
    
    def save_profile(self, profile_name: str, config: Config) -> bool:
        """
        Зберігає профіль гри
        
        Args:
            profile_name: Назва профілю
            config: Конфігурація для збереження
            
        Returns:
            bool: True якщо збереження успішне
        """
        try:
            profiles = self.load_all_profiles()
            profiles[profile_name] = {
                'loader': config.loader,
                'ram': config.ram,
                'window_size': config.window_size,
                'multiplayer': config.multiplayer,
                'console': config.console,
                'fullscreen': config.fullscreen,
                'created_at': time.time()
            }
            
            return self.data_manager.save(profiles, "profiles.json")
            
        except Exception as e:
            self.logger.error(f"Failed to save profile {profile_name}: {e}")
            return False
    
    def load_profile(self, profile_name: str) -> Optional[Dict]:
        """
        Завантажує профіль гри
        
        Args:
            profile_name: Назва профілю
            
        Returns:
            Dict або None: Дані профілю або None якщо не знайдено
        """
        try:
            profiles = self.load_all_profiles()
            return profiles.get(profile_name)
        except Exception as e:
            self.logger.error(f"Failed to load profile {profile_name}: {e}")
            return None
    
    def load_all_profiles(self) -> Dict:
        """
        Завантажує всі профілі
        
        Returns:
            Dict: Словник з профілями
        """
        return self.data_manager.load("profiles.json")
    
    def delete_profile(self, profile_name: str) -> bool:
        """
        Видаляє профіль
        
        Args:
            profile_name: Назва профілю
            
        Returns:
            bool: True якщо видалення успішне
        """
        try:
            profiles = self.load_all_profiles()
            if profile_name in profiles:
                del profiles[profile_name]
                return self.data_manager.save(profiles, "profiles.json")
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete profile {profile_name}: {e}")
            return False