#!/usr/bin/env python3
"""
QQQ-CRAFT LAUNCHER WITH ERROR HANDLING
"""

import os
import re
import sys
import json
import uuid
import time
import shutil
import hashlib
import logging
import platform
import threading
import subprocess
import traceback
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict

import requests
from flask import Flask, render_template, request, jsonify, send_from_directory
from tkinter import messagebox, scrolledtext
from packaging import version
import minecraft_launcher_lib
import tkinter as tk
import webview
import websockets
import asyncio
import markdown

VERSION = "1.0.23.0"
FLASK_PORT = 6724
WEBSOCKET_PORT = 5263

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

if not (IS_WINDOWS or IS_LINUX):
    raise OSError(f"Unsupported platform: {platform.system()}")

class ErrorHandler:

    @staticmethod
    def show_error_dialog(error_message: str, error_details: str = None):
        try:
            root = tk.Tk()
            root.withdraw()  # не показуємо основне вікно

            full_error = (
                f"Версія лаунчера: {VERSION}\n"
                f"ОС: {platform.system()} {platform.release()}\n"
                f"Час: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"{'-'*40}\n"
                f"{error_message}"
            )

            if error_details:
                full_error += f"\n\nДеталі:\n{error_details}"

            messagebox.showerror("QQQ-CRAFT — Помилка", full_error)

        except Exception as e:
            print("КРИТИЧНА ПОМИЛКА:", error_message)
            print("ДЕТАЛІ:", error_details)
            print("Помилка створення діалогу:", e)

    @staticmethod
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        error_message = str(exc_value)
        error_details = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        ErrorHandler.show_error_dialog(error_message, error_details)

sys.excepthook = ErrorHandler.handle_exception

@dataclass
class Config:
    nickname: str = ""
    loader: str = "fabric"
    ram: str = "4G"
    window_size: str = "1280x720"
    multiplayer: bool = True
    console: bool = False
    fullscreen: bool = False

class PathManager:
    def __init__(self):
        try:
            self.home = Path.home()
            if IS_WINDOWS:
                self.base_dir = self.home / "AppData" / "Local" / "Programs" / "qqq-craft"
            else:  # Linux
                self.base_dir = self.home / ".local" / "share" / "qqq-craft"
            
            self.base_dir.mkdir(parents=True, exist_ok=True)
            (self.base_dir / "static").mkdir(exist_ok=True)
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка ініціалізації PathManager",
                f"Не вдалося створити необхідні папки: {e}"
            )
            raise
    
    def get_install_dir(self, loader: str) -> Path:
        return self.base_dir / "instances" / loader

class DataManager:
    def __init__(self, path_manager: PathManager):
        self.path_manager = path_manager
        self.logger = logging.getLogger(__name__)
    
    def save(self, data: Dict, filename: str) -> bool:
        try:
            file_path = self.path_manager.base_dir / "static" / filename
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save {filename}: {e}")
            ErrorHandler.show_error_dialog(
                f"Помилка збереження файлу {filename}",
                str(e)
            )
            return False
    
    def load(self, filename: str) -> Dict:
        try:
            file_path = self.path_manager.base_dir / "static" / filename
            if file_path.exists() and file_path.stat().st_size > 0:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load {filename}: {e}")
            ErrorHandler.show_error_dialog(
                f"Помилка завантаження файлу {filename}",
                str(e)
            )
        return {}

class Validator:
    @staticmethod
    def validate_nickname(nickname: str) -> Tuple[bool, str]:
        try:
            if not nickname or not nickname.strip():
                return False, "Нікнейм не може бути порожнім"
            
            nickname = nickname.strip()
            if not (3 <= len(nickname) <= 16):
                return False, "Нікнейм повинен бути від 3 до 16 символів"
            
            # Перевірка тільки на латинські літери, цифри, дефіс і підкреслення
            if not re.fullmatch(r'[A-Za-z0-9_-]+', nickname):
                return False, "Нікнейм може містити лише англійські літери, цифри, дефіс або підкреслення"
            
            forbidden = {'admin', 'moderator', 'staff', 'banned', 'owner'}
            if nickname.lower() in forbidden:
                return False, "Нікнейм містить заборонені слова"
            
            return True, "OK"
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка валідації нікнейму",
                str(e)
            )
            return False, "Помилка валідації"
    
    @staticmethod
    def validate_config(data: Dict) -> Tuple[bool, str, Optional[Config]]:
        try:
            is_valid, message = Validator.validate_nickname(data.get('nickname', ''))
            if not is_valid:
                return False, message, None

            loader = data.get('loader', 'fabric')
            
            ram = data.get('ram', '4G')
            if ram not in ['2G', '4G', '8G', '16G']:
                ram = '4G'
                
            window_size = data.get('windowSize', '1280x720')
            valid_sizes = ['1280x720', '1920x1080', '1366x768', '1600x900']
            if window_size not in valid_sizes:
                window_size = '1280x720'
            
            config = Config(
                nickname=data.get('nickname', '').strip(),
                loader=loader,
                ram=ram,
                window_size=window_size,
                multiplayer=bool(data.get('multiplayer', True)),
                console=bool(data.get('console', False)),
                fullscreen=bool(data.get('fullscreen', False))
            )
            
            return True, "OK", config
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка валідації конфігурації",
                str(e)
            )
            return False, "Помилка валідації", None

class MinecraftLauncher:
    def __init__(self, path_manager: PathManager):
        self.path_manager = path_manager
        self.logger = logging.getLogger(__name__)
    
    @staticmethod
    def generate_offline_uuid(nickname: str) -> str:
        try:
            namespace = uuid.UUID('00000000-0000-0000-0000-000000000000')
            return str(uuid.uuid3(namespace, f"OfflinePlayer:{nickname}"))
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка генерації UUID",
                str(e)
            )
            return str(uuid.uuid4())
 
    def launch(self, config: Config, progress_callback=None) -> bool:
        try:
            install_dir = self.path_manager.get_install_dir(config.loader)
            
            if not install_dir.exists():
                error_msg = "Папка гри не знайдена"
                if progress_callback:
                    progress_callback(error_msg)
                ErrorHandler.show_error_dialog(
                    "Помилка запуску гри",
                    f"Папка {install_dir} не існує"
                )
                return False
            
            try:
                width, height = config.window_size.split('x')
                int(width), int(height)
            except (ValueError, AttributeError):
                width, height = "1280", "720"
            
            options = {
                "username": config.nickname,
                "uuid": self.generate_offline_uuid(config.nickname),
                "gameDirectory": str(install_dir),
                "jvmArguments": [f"-Xmx{config.ram}"],
                "launcherName": "QQQ-Launcher",
                "launcherVersion": VERSION,
                "customResolution": True,
                "resolutionWidth": width,
                "resolutionHeight": height,
                "fullscreen": config.fullscreen,
            }
            
            if config.multiplayer:
                options["quickPlayMultiplayer"] = "play.qqq-craft.top"
            
            command = minecraft_launcher_lib.command.get_minecraft_command(
                config.loader, str(install_dir), options
            )
            
            if progress_callback:
                progress_callback("Запуск гри...")
            
            creation_flags = 0
            if IS_WINDOWS and not config.console:
                creation_flags = subprocess.CREATE_NO_WINDOW
            
            process = subprocess.Popen(
                command,
                cwd=str(install_dir),
                creationflags=creation_flags,
                stdout=subprocess.DEVNULL if not config.console else None,
                stderr=subprocess.DEVNULL if not config.console else None
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Launch failed: {e}")
            error_msg = f"Помилка запуску: {e}"
            if progress_callback:
                progress_callback(error_msg)
            ErrorHandler.show_error_dialog(
                "Помилка запуску Minecraft",
                str(e)
            )
            return False

class ModpacksManager:
    def __init__(self, path_manager):
        self.path_manager = path_manager
        self.data_manager = DataManager(self.path_manager)
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()
        self.total = self.done = 0
        
        config = self.data_manager.load("data.json")
        self.url = config.get("modpacks-url", "").rstrip('/')
        if not self.url:
            raise ValueError("Не знайдено URL для модпаків")
    
    def md5(self, path):
        try:
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except:
            return None
    
    def get_dir_size(self, dir_path):
        if not dir_path.exists():
            return 0
        return sum(f.stat().st_size for f in dir_path.rglob('*') if f.is_file())
    
    def collect_files(self, items, path=''):
        files = {}
        for item in items:
            p = f"{path}/{item['name']}" if path else item['name']
            if item['type'] == 'file':
                files[p] = item
            elif 'children' in item:
                files.update(self.collect_files(item['children'], p))
        return files
    
    def download(self, path, info, callback=None):
        try:
            local = self.target_dir / path
            
            if not local.exists():
                pass
            elif info.get('sync', False):
                if (local.stat().st_size == info['size'] and 
                    self.md5(local) == info['checksum']):
                    self.done += info['size']
                    if callback:
                        callback(self.done / self.total * 100, path, 'skipped')
                    return
            else:
                self.done += info['size']
                if callback:
                    callback(self.done / self.total * 100, path, 'skipped')
                return
            
            local.parent.mkdir(parents=True, exist_ok=True)
            file_url = f"{self.base_url}/public/{self.target + '/' if self.target else ''}{info['url']}"
            r = self.session.get(file_url, timeout=30)
            
            if r.status_code == 200:
                local.write_bytes(r.content)
                self.done += info['size']
                if callback:
                    callback(self.done / self.total * 100, path, 'downloaded')
            else:
                raise requests.RequestException(f"HTTP {r.status_code}")
        except Exception as e:
            ErrorHandler.show_error_dialog(f"Помилка завантаження {path}", str(e))
    
    def process_files(self, items, path=''):
        files_to_download = {}
        
        for item in items:
            current_path = f"{path}/{item['name']}" if path else item['name']
            
            if item['type'] == 'dir':
                local_dir = self.target_dir / current_path
                
                if (local_dir.exists() and 
                    self.get_dir_size(local_dir) == item['size']):
                    
                    for child in item.get('children', []):
                        if child['type'] == 'file':
                            self.done += child['size']
                    continue
                
                if 'children' in item:
                    child_files = self.process_files(item['children'], current_path)
                    files_to_download.update(child_files)
            else:
                files_to_download[current_path] = item
        
        return files_to_download
    
    def cleanup_sync_files(self, server_files):
        if not self.target_dir.exists():
            return
        
        for local_file in self.target_dir.rglob('*'):
            if local_file.is_file():
                rel_path = str(local_file.relative_to(self.target_dir)).replace('\\', '/')
                if rel_path not in server_files:
                    server_file = server_files.get(rel_path.split('/')[0])
                    if server_file and server_file.get('sync', False):
                        local_file.unlink()
    
    def check_modpack_exists(self, modpack: str) -> bool:
        try:
            url = f"{self.url}/index.php?modpack={modpack}"
            response = self.session.get(url, timeout=10)
            return response.json().get('status') == 'ok'
        except:
            return False
    
    def install_modpack(self, modpack='', target_dir='game', callback=None):
        try:
            self.target_dir = Path(target_dir)
            self.target_dir.mkdir(parents=True, exist_ok=True)
            
            url = f"{self.url}/index.php"
            if modpack:
                url += f"?modpack={modpack}"
            
            data = self.session.get(url, timeout=30).json()
            if data.get('status') != 'ok':
                error_msg = data.get('message', 'Невідома помилка')
                if callback:
                    callback(0, '', 'error', error_msg)
                ErrorHandler.show_error_dialog("Помилка встановлення", error_msg)
                return False
            
            self.total = data['total_size']
            self.target = data.get('target', '')
            self.base_url = data.get('base_url', self.url)
            
            all_files = self.collect_files(data['files'])
            self.cleanup_sync_files(all_files)
            
            files_to_process = self.process_files(data['files'])
            
            for path, info in files_to_process.items():
                self.download(path, info, callback)
            
            if callback:
                callback(100, '', 'complete', f"Готово!")
            return True
            
        except Exception as e:
            ErrorHandler.show_error_dialog("Помилка встановлення", str(e))
            return False

    def install_loader(self, loader: str, version: str, target_dir: str, callback=None) -> bool:
        try:
            install_dir = Path(target_dir)
            install_dir.mkdir(parents=True, exist_ok=True)

            last_percent, last_time, max_progress = -1, time.time(), [0]
            
            def set_progress(current: int):
                nonlocal last_percent, last_time
                if max_progress[0] > 0:
                    percent = (current / max_progress[0]) * 100
                    current_time = time.time()
                    if int(percent) != last_percent and current_time - last_time > 0.5:
                        if callback:
                            callback(percent, f"Файл {current}/{max_progress[0]}", 'downloaded')
                        last_percent, last_time = int(percent), current_time
            
            def set_max(max_val: int):
                max_progress[0] = max_val
            
            def set_status(text: str):
                if callback:
                    callback(0, text, 'status')
            
            ml_callback = {
                "setProgress": set_progress,
                "setMax": set_max,
                "setStatus": set_status
            }
            
            if loader == "fabric":
                minecraft_launcher_lib.fabric.install_fabric(version, str(install_dir), callback=ml_callback)
            elif loader == "forge":
                forge_version = minecraft_launcher_lib.forge.find_forge_version(version)
                minecraft_launcher_lib.forge.install_forge_version(forge_version, str(install_dir), callback=ml_callback)
            elif loader == "vanilla":
                minecraft_launcher_lib.install.install_minecraft_version(version, str(install_dir), callback=ml_callback)
            else:
                raise ValueError(f"Unknown loader: {loader}")
            
            if callback:
                callback(100, '', 'complete', f"Встановлення {loader} завершено")
            return True
            
        except Exception as e:
            self.logger.error(f"Installation failed: {e}")
            if callback:
                callback(0, '', 'error', f"Помилка встановлення: {e}")
            ErrorHandler.show_error_dialog(
                f"Помилка встановлення {loader}",
                str(e)
            )
            return False

class WebSocketManager:
    def __init__(self):
        self.clients = set()
        self.logger = logging.getLogger(__name__)
    
    async def handle_client(self, websocket):
        try:
            self.clients.add(websocket)
            async for message in websocket:
                await websocket.send(f"Echo: {message}")
        except Exception as e:
            self.logger.debug(f"WebSocket error: {e}")
        finally:
            self.clients.discard(websocket)
    
    def broadcast(self, message: str):
        if not self.clients:
            return
        
        def send_to_clients():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                async def broadcast_async():
                    if self.clients:
                        disconnected = set()
                        for client in self.clients.copy():
                            try:
                                await client.send(message)
                            except Exception:
                                disconnected.add(client)
                        
                        self.clients -= disconnected
                
                loop.run_until_complete(broadcast_async())
                loop.close()
            except Exception as e:
                self.logger.error(f"Broadcast error: {e}")
        
        threading.Thread(target=send_to_clients, daemon=True).start()
    
    async def start_server(self):
        try:
            server = await websockets.serve(
                self.handle_client, "localhost", WEBSOCKET_PORT
            )
            await server.wait_closed()
        except Exception as e:
            self.logger.error(f"WebSocket server error: {e}")
            ErrorHandler.show_error_dialog(
                "Помилка WebSocket сервера",
                str(e)
            )

class Application:
    def __init__(self):
        try:
            self.setup_logging()
            self.path_manager = PathManager()
            self.data_manager = DataManager(self.path_manager)
            self.modpacks_manager = ModpacksManager(self.path_manager)
            self.launcher = MinecraftLauncher(self.path_manager)
            self.websocket_manager = WebSocketManager()
            self.window = None
            
            self.app = Flask(__name__)
            self.setup_routes()
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка ініціалізації програми",
                str(e)
            )
            raise
    
    def setup_logging(self):
        try:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                handlers=[
                    logging.StreamHandler(),
                    logging.FileHandler('launcher.log', encoding='utf-8')
                ]
            )
            self.logger = logging.getLogger(__name__)
        except Exception as e:
            print(f"Помилка налаштування логування: {e}")
    
    def setup_routes(self):
        @self.app.route('/')
        def index():
            try:
                config_data = self.data_manager.load("user.json")
                news_data = self.load_news()
                return render_template(
                    "index.html",
                    news=news_data,
                    data=config_data,
                    is_latest_version=self.is_latest_version()
                )
            except Exception as e:
                ErrorHandler.show_error_dialog(
                    "Помилка завантаження головної сторінки",
                    str(e)
                )
                return "Помилка завантаження", 500
        
        @self.app.route('/start', methods=['POST'])
        def start_game():
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "Невірні дані"})
                
                is_valid, message, config = Validator.validate_config(data)
                if not is_valid:
                    return jsonify({"success": False, "error": message})
                
                self.data_manager.save(asdict(config), "user.json")

                last_percent = -1
                last_time = time.time()
                def progress_callback(percent, current_file, status, message=None):
                    nonlocal last_percent, last_time
                    current_time = time.time()
                    
                    if status == 'start':
                        self.websocket_manager.broadcast(message)
                    elif status in ['downloaded', 'skipped']:
                        percent_int = int(percent)
                        if (percent_int != last_percent and 
                            (current_time - last_time > 0.5 or abs(percent_int - last_percent) >= 1)):
                            action = "Завантаження" if status == 'downloaded' else "Перевірка файлів"
                            self.websocket_manager.broadcast(f"[{percent:.0f}%] {action}")
                            last_percent = percent_int
                            last_time = current_time
                    elif status == 'complete':
                        self.websocket_manager.broadcast(message)
                    elif status == 'error':
                        self.websocket_manager.broadcast(message)

                def install_and_launch():
                    try:
                        progress_callback(0, '', 'start', f"Підготовка")
                        
                        install_dir = self.path_manager.get_install_dir(config.loader)
                        
                        if self.modpacks_manager.check_modpack_exists(config.loader):
                            install_success = self.modpacks_manager.install_modpack(
                                config.loader, install_dir, progress_callback
                            )
                            error_msg = "Помилка встановлення гри"
                        else:
                            install_success = self.modpacks_manager.install_loader(
                                "vanilla", config.loader, self.websocket_manager.broadcast
                            )
                            error_msg = "Помилка встановлення ванільної версії"
                        
                        if not install_success:
                            self.websocket_manager.broadcast(error_msg)
                            return

                        if self.launcher.launch(config, self.websocket_manager.broadcast):
                            self.websocket_manager.broadcast("Гру запущено успішно! Лаунчер закривається")
                            threading.Timer(3.0, self.close_window).start()
                        else:
                            self.websocket_manager.broadcast("Помилка запуску гри")
                            
                    except Exception as e:
                        self.logger.error(f"Install/Launch error: {e}")
                        self.websocket_manager.broadcast(f"{e}")
                        ErrorHandler.show_error_dialog(
                            "Помилка встановлення/запуску",
                            str(e)
                        )

                threading.Thread(target=install_and_launch, daemon=True).start()
                return jsonify({"success": True})

            except Exception as e:
                self.logger.error(f"Start game error: {e}")
                ErrorHandler.show_error_dialog(
                    "Помилка запуску гри",
                    str(e)
                )
                return jsonify({"success": False, "error": str(e)})
        
        @self.app.route('/static/<filename>')
        def static_files(filename):
            return send_from_directory('static', filename)
                
        @self.app.route('/game_folder', methods=['POST'])
        def game_folder():
            try:
                data = request.form.to_dict()
                if not data:
                    return jsonify({"success": False, "error": "Невірні дані"})
                
                is_valid, message, config = Validator.validate_config(data)
                if not is_valid:
                    return jsonify({"success": False, "error": message})
                
                install_dir = self.path_manager.get_install_dir(config.loader)
                
                if IS_WINDOWS:
                    os.startfile(install_dir)
                else:
                    subprocess.run(['xdg-open', str(install_dir)])
                    
                return jsonify({"success": True, "message": "Папку відкрито!"})
            except Exception as e:
                self.logger.error(f"Помилка при відкритті папки: {e}")
                ErrorHandler.show_error_dialog(
                    "Помилка відкриття папки гри",
                    str(e)
                )
                return jsonify({"success": False, "error": str(e)})
        
        @self.app.route('/close')
        def close_launcher():
            try:
                threading.Timer(1.0, self.close_window).start()
                return jsonify({"success": True, "message": "Лаунчер закривається..."})
            except Exception as e:
                self.logger.error(f"Close launcher error: {e}")
                ErrorHandler.show_error_dialog(
                    "Помилка закриття лаунчера",
                    str(e)
                )
                return jsonify({"success": False, "error": str(e)})
    
    def load_news(self) -> list:
        try:
            config = self.data_manager.load("data.json")
            news_url = config.get("news-url")
            if not news_url:
                self.logger.warning("No 'news-url' found in config.")
                return []

            response = requests.get(news_url, timeout=10)
            response.raise_for_status()
            news = response.json()

            for news_item in news:
                if news_item.get("description"):
                    news_item["description"] = markdown.markdown(news_item["description"])

                if news_item.get("timestamp"):
                    timestamp = news_item["timestamp"]
                    timestamp = timestamp.split(".")[0].replace("T", " ").split("+")[0]
                    news_item["timestamp"] = timestamp

            return news

        except Exception as e:
            self.logger.error(f"Failed to load news: {e}")
            ErrorHandler.show_error_dialog(
                "Помилка завантаження новин",
                str(e)
            )
            return []

    def is_latest_version(self) -> bool:
        try:
            config = self.data_manager.load("data.json")
            repo_url = config.get("github-repo")
            if not repo_url:
                return True
            
            response = requests.get(repo_url, timeout=5)
            response.raise_for_status()
            latest = response.json().get("tag_name", "0.0.0.0")
            return version.parse(VERSION) >= version.parse(latest)
        except Exception as e:
            self.logger.error(f"Version check failed: {e}")
            return True
    
    def close_window(self):
        try:
            if self.window:
                self.window.destroy()
        except Exception as e:
            self.logger.debug(f"Window close error: {e}")
    
    def run_flask(self):
        try:
            self.app.run(
                debug=False,
                port=FLASK_PORT,
                use_reloader=False,
                threaded=True,
                host='127.0.0.1'
            )
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка запуску Flask сервера",
                str(e)
            )
    
    def run_websocket(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.websocket_manager.start_server())
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка запуску WebSocket сервера",
                str(e)
            )
    
    def run(self):
        try:
            threading.Thread(target=self.run_flask, daemon=True).start()
            threading.Thread(target=self.run_websocket, daemon=True).start()
            self.window = webview.create_window(
                f"QQQ - Час стати легендою! (BETA ВЕРСІЯ {VERSION})",
                f"http://127.0.0.1:{FLASK_PORT}/",
                width=1280,
                height=720,
                resizable=False,
                min_size=(800, 600)
            )
            
            webview.start(debug=False)
            
        except Exception as e:
            self.logger.error(f"Application error: {e}")
            ErrorHandler.show_error_dialog(
                "Критична помилка програми",
                str(e)
            )
            sys.exit(1)

def main():
    try:
        app = Application()

        if not app.is_latest_version():
            try:
                root = tk.Tk()
                root.withdraw()
                result = messagebox.askyesno("Оновлення", f"Доступна нова версія лаунчера! Бажаєте завантажити?")
                
                if result:
                    config = app.data_manager.load("data.json")
                    update_url = config.get("update-url")
                    if update_url:
                        if IS_WINDOWS:
                            os.startfile(update_url)
                        else:
                            subprocess.run(['xdg-open', update_url])
                    sys.exit(0)
            except Exception as e:
                ErrorHandler.show_error_dialog(
                    "Помилка перевірки оновлень",
                    str(e)
                )
        
        app.run()
        
    except KeyboardInterrupt:
        print("\nПрограма зупинена користувачем")
        sys.exit(0)
    except Exception as e:
        ErrorHandler.show_error_dialog(
            "Критична помилка запуску програми",
            str(e)
        )
        sys.exit(1)

if __name__ == "__main__":
    main()