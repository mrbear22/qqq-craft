#!/usr/bin/env python3
"""
QQQ-CRAFT LAUNCHER
"""

import os
import sys
import json
import uuid
import time
import logging
import platform
import threading
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict

# Third-party imports
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory
from tkinter import messagebox
from packaging import version
import minecraft_launcher_lib
import tkinter as tk
import webview
import websockets
import asyncio
import markdown

# Configuration
VERSION = "1.0.20.0"
FLASK_PORT = 7776
WEBSOCKET_PORT = 8765
MC_VERSION = "1.21.1"

# Platform detection
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

if not (IS_WINDOWS or IS_LINUX):
    raise OSError(f"Unsupported platform: {platform.system()}")

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
        self.home = Path.home()
        if IS_WINDOWS:
            self.base_dir = self.home / "AppData" / "Local" / "Programs" / "qqq-craft"
        else:  # Linux
            self.base_dir = self.home / ".local" / "share" / "qqq-craft"
        
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "static").mkdir(exist_ok=True)
    
    def get_install_dir(self, loader: str) -> Path:
        return self.base_dir / loader

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
            return False
    
    def load(self, filename: str) -> Dict:
        try:
            file_path = self.path_manager.base_dir / "static" / filename
            if file_path.exists() and file_path.stat().st_size > 0:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load {filename}: {e}")
        return {}

class Validator:
    @staticmethod
    def validate_nickname(nickname: str) -> Tuple[bool, str]:
        if not nickname or not nickname.strip():
            return False, "Нікнейм не може бути порожнім"
        
        nickname = nickname.strip()
        if not (3 <= len(nickname) <= 16):
            return False, "Нікнейм повинен бути від 3 до 16 символів"
        
        if not nickname.replace('_', '').replace('-', '').isalnum():
            return False, "Нікнейм може містити лише літери, цифри, дефіс або підкреслення"
        
        forbidden = {'admin', 'moderator', 'staff', 'banned', 'owner'}
        if nickname.lower() in forbidden:
            return False, "Нікнейм містить заборонені слова"
        
        return True, "OK"
    
    @staticmethod
    def validate_config(data: Dict) -> Tuple[bool, str, Optional[Config]]:
        # Validate nickname
        is_valid, message = Validator.validate_nickname(data.get('nickname', ''))
        if not is_valid:
            return False, message, None
        
        # Validate loader
        loader = data.get('loader', 'fabric')
        if loader not in ['fabric', 'forge', 'vanilla']:
            loader = 'fabric'
        
        # Validate RAM
        ram = data.get('ram', '4G')
        if ram not in ['2G', '4G', '8G', '16G']:
            ram = '4G'
        
        # Validate window size
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

class MinecraftInstaller:
    def __init__(self, path_manager: PathManager):
        self.path_manager = path_manager
        self.logger = logging.getLogger(__name__)
        self.progress_callback = None
    
    def set_progress_callback(self, callback):
        self.progress_callback = callback
    
    def _create_callback(self):
        last_percent = -1
        last_time = time.time()
        
        def callback_wrapper():
            max_progress = 0
            
            def set_status(text: str):
                if self.progress_callback:
                    self.progress_callback(f"Статус: {text}")
            
            def set_progress(current: int):
                nonlocal last_percent, last_time
                if max_progress > 0:
                    percent = int((current / max_progress) * 100)
                    current_time = time.time()
                    
                    if percent != last_percent and current_time - last_time > 0.5:
                        if self.progress_callback:
                            self.progress_callback(f"Підготовка: {percent}%")
                        last_percent = percent
                        last_time = current_time
            
            def set_max(max_val: int):
                nonlocal max_progress
                max_progress = max_val
            
            return {
                "setStatus": set_status,
                "setProgress": set_progress,
                "setMax": set_max
            }
        
        return callback_wrapper()
    
    def install(self, loader: str) -> bool:
        try:
            install_dir = self.path_manager.get_install_dir(loader)
            install_dir.mkdir(parents=True, exist_ok=True)
            
            callback = self._create_callback()
            
            if loader == "fabric":
                minecraft_launcher_lib.fabric.install_fabric(
                    MC_VERSION, str(install_dir), callback=callback
                )
            elif loader == "forge":
                forge_version = minecraft_launcher_lib.forge.find_forge_version(MC_VERSION)
                minecraft_launcher_lib.forge.install_forge_version(
                    forge_version, str(install_dir), callback=callback
                )
            elif loader == "vanilla":
                minecraft_launcher_lib.install.install_minecraft_version(
                    MC_VERSION, str(install_dir), callback=callback
                )
            else:
                raise ValueError(f"Unknown loader: {loader}")
            
            if self.progress_callback:
                self.progress_callback("Встановлення завершено")
            return True
            
        except Exception as e:
            self.logger.error(f"Installation failed: {e}")
            if self.progress_callback:
                self.progress_callback(f"Помилка встановлення: {e}")
            return False

class MinecraftLauncher:
    def __init__(self, path_manager: PathManager):
        self.path_manager = path_manager
        self.logger = logging.getLogger(__name__)
    
    @staticmethod
    def generate_offline_uuid(nickname: str) -> str:
        namespace = uuid.UUID('00000000-0000-0000-0000-000000000000')
        return str(uuid.uuid3(namespace, f"OfflinePlayer:{nickname}"))
        
    def copy_resources(self, config):
        install_dir = self.path_manager.get_install_dir(config.loader)
        os.makedirs(install_dir, exist_ok=True)

        base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))

        for folder in ["fabric", "forge", "vanilla"]:
            src = os.path.join(base_path, folder)
            dst = os.path.join(install_dir, folder)
            if not os.path.exists(dst):
                shutil.copytree(src, dst, dirs_exist_ok=True)
                
    def launch(self, config: Config, progress_callback=None) -> bool:
        try:
            copy_resources(self, config)
            
            install_dir = self.path_manager.get_install_dir(config.loader)
            
            if not install_dir.exists():
                if progress_callback:
                    progress_callback("Папка гри не знайдена")
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

            if config.loader == "fabric":
                loader_version = minecraft_launcher_lib.fabric.get_latest_loader_version()
                version_id = f"fabric-loader-{loader_version}-{MC_VERSION}"
            elif config.loader == "forge":
                forge_version = minecraft_launcher_lib.forge.find_forge_version(MC_VERSION)
                version_id = f"{MC_VERSION}-forge-{forge_version}"
            else:  # vanilla
                version_id = MC_VERSION
            
            command = minecraft_launcher_lib.command.get_minecraft_command(
                version_id, str(install_dir), options
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
            if progress_callback:
                progress_callback(f"Помилка запуску: {e}")
            return False

class WebSocketManager:
    def __init__(self):
        self.clients = set()
        self.logger = logging.getLogger(__name__)
    
    async def handle_client(self, websocket):
        self.clients.add(websocket)
        try:
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
        
        threading.Thread(target=send_to_clients, daemon=True).start()
    
    async def start_server(self):
        try:
            server = await websockets.serve(
                self.handle_client, "localhost", WEBSOCKET_PORT
            )
            await server.wait_closed()
        except Exception as e:
            self.logger.error(f"WebSocket server error: {e}")

class Application:
    def __init__(self):
        self.setup_logging()
        self.path_manager = PathManager()
        self.data_manager = DataManager(self.path_manager)
        self.installer = MinecraftInstaller(self.path_manager)
        self.launcher = MinecraftLauncher(self.path_manager)
        self.websocket_manager = WebSocketManager()
        self.window = None
        
        self.app = Flask(__name__)
        self.setup_routes()
        
        self.installer.set_progress_callback(self.websocket_manager.broadcast)
    
    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('launcher.log', encoding='utf-8')
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def setup_routes(self):
        @self.app.route('/')
        def index():
            config_data = self.data_manager.load("user.json")
            news_data = self.load_news()
            return render_template(
                "index.html",
                news=news_data,
                data=config_data,
                is_latest_version=self.is_latest_version()
            )
        
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

                def install_and_launch():
                    try:
                        if self.installer.install(config.loader):
                            success = self.launcher.launch(config, self.websocket_manager.broadcast)
                            if success:
                                self.websocket_manager.broadcast("Гру запущено успішно! Лаунчер закривається...")
                                threading.Timer(3.0, self.close_window).start()
                            else:
                                self.websocket_manager.broadcast("Помилка запуску гри")
                        else:
                            self.websocket_manager.broadcast("Помилка встановлення гри")
                    except Exception as e:
                        self.logger.error(f"Install/Launch error: {e}")
                        self.websocket_manager.broadcast(f"Помилка: {e}")
                
                threading.Thread(target=install_and_launch, daemon=True).start()
                return jsonify({"success": True})
                
            except Exception as e:
                self.logger.error(f"Start game error: {e}")
                return jsonify({"success": False, "error": str(e)})
        
        @self.app.route('/static/<filename>')
        def static_files(filename):
            return send_from_directory('static', filename)
                
        @self.app.route('/game_folder', methods=['POST'])
        def game_folder():
            data = request.form.to_dict()
            if not data:
                return jsonify({"success": False, "error": "Невірні дані"})
            try:
                is_valid, message, config = Validator.validate_config(data)
                install_dir = self.path_manager.get_install_dir(config.loader)
                os.startfile(install_dir)
                return jsonify({"success": True, "message": "Папку відкрито!"})
            except Exception as e:
                self.logger.error(f"Помилка при відкритті папки: {e}")
                return jsonify({"success": False, "error": str(e)})

            install_dir = self.path_manager.get_install_dir(config.loader)
            os.startfile(install_dir)
            return jsonify({"success": True, "message": "Відкриття папки гри..."})
        
        @self.app.route('/close')
        def close_launcher():
            threading.Timer(1.0, self.close_window).start()
            return jsonify({"success": True, "message": "Лаунчер закривається..."})
    
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
        self.app.run(
            debug=False,
            port=FLASK_PORT,
            use_reloader=False,
            threaded=True,
            host='127.0.0.1'
        )
    
    def run_websocket(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.websocket_manager.start_server())
    
    def run(self):
        try:
            threading.Thread(target=self.run_flask, daemon=True).start()
            threading.Thread(target=self.run_websocket, daemon=True).start()
            self.window = webview.create_window(
                "QQQ - Час стати легендою!",
                f"http://127.0.0.1:{FLASK_PORT}/",
                width=1280,
                height=720,
                resizable=False,
                min_size=(800, 600)
            )
            
            webview.start(debug=False)
            
        except Exception as e:
            self.logger.error(f"Application error: {e}")
            sys.exit(1)

def main():
    try:
        app = Application()

        if not app.is_latest_version():
            root = tk.Tk()
            root.withdraw()
            result = messagebox.askyesno("Оновлення", f"Доступна нова версія лаунчера! Бажаєте завантажити?")
            
            if result:
                config = app.data_manager.load("data.json")
                update_url = config.get("update-url")
                os.startfile(update_url)
                sys.exit(0)
        
        app.run()
    except KeyboardInterrupt:
        print("\nПрограма зупинена користувачем")
        sys.exit(0)
    except Exception as e:
        print(f"Критична помилка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()