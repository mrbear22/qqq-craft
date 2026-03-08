#!/usr/bin/env python3
"""
QQQ-CRAFT LAUNCHER - ОСНОВНИЙ ФАЙЛ
"""

import os
import sys
import time
import json
import platform
import threading
import traceback
from pathlib import Path
from typing import Dict, Any
from dataclasses import dataclass, asdict

import requests
import tkinter as tk
from tkinter import messagebox
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect
from packaging import version
import webview
import websockets
import asyncio
import markdown
import webbrowser
import urllib.parse
import re

from modules.data_manager import DataManager, PathManager, Config, Validator
from modules.download_manager import ModpacksManager
from modules.launch_manager import MinecraftLauncher, AuthManager

VERSION = "1.0.27.0"
FLASK_PORT = 6724
WEBSOCKET_PORT = 5263

MODPACKS_URL = "https://qqq-craft.top/launcher/get/"
GITHUB_REPO = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"
NEWS_URL = "https://qqq-craft.top/news/"

CLIENT_ID = "8015479d-3def-49ae-8f10-2fea4199639f"
REDIRECT_URL = "http://localhost:6724/auth/callback"

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

if not (IS_WINDOWS or IS_LINUX):
    raise OSError(f"Unsupported platform: {platform.system()}")

class ErrorHandler:
    @staticmethod
    def show_error_dialog(error_message: str, error_details: str = None):
        try:
            root = tk.Tk()
            root.withdraw()

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

class WebSocketManager:
    def __init__(self):
        self.clients = set()
        self.logger = None
    
    async def handle_client(self, websocket):
        try:
            self.clients.add(websocket)
            async for message in websocket:
                await websocket.send(f"Echo: {message}")
        except Exception as e:
            if self.logger:
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
                if self.logger:
                    self.logger.error(f"Broadcast error: {e}")
        
        threading.Thread(target=send_to_clients, daemon=True).start()
    
    async def start_server(self):
        try:
            server = await websockets.serve(
                self.handle_client, "localhost", WEBSOCKET_PORT
            )
            await server.wait_closed()
        except Exception as e:
            if self.logger:
                self.logger.error(f"WebSocket server error: {e}")
            ErrorHandler.show_error_dialog(
                "Помилка WebSocket сервера",
                str(e)
            )

class Application:
    def __init__(self):
        try:
            import logging
            logs_dir = Path("logs")
            logs_dir.mkdir(exist_ok=True)
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                handlers=[
                    logging.StreamHandler(),
                    logging.FileHandler(logs_dir / 'log.log', encoding='utf-8')
                ]
            )
            self.logger = logging.getLogger(__name__)
            
            self.path_manager = PathManager()
            self.data_manager = DataManager(self.path_manager)
            self.auth_manager = AuthManager(self.data_manager, CLIENT_ID, REDIRECT_URL)
            self.modpacks_manager = ModpacksManager(self.path_manager, MODPACKS_URL)
            self.launcher = MinecraftLauncher(self.path_manager, self.auth_manager)
            self.websocket_manager = WebSocketManager()
            self.websocket_manager.logger = self.logger
            self.window = None
            
            self.logger.info("Checking authentication status...")
            self.auth_manager.ensure_valid_token()
            
            self.app = Flask(__name__)
            self.setup_routes()
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка ініціалізації програми",
                str(e)
            )
            raise
    
    def setup_routes(self):
        @self.app.route('/')
        def index():
            try:
                config_data = self.data_manager.load("user.json")
                auth_data = self.auth_manager.load_auth_data()
                news_data = self.load_news()
                
                has_microsoft_auth = self.auth_manager.is_token_valid(auth_data)
                if has_microsoft_auth:
                    config_data['microsoft_name'] = auth_data.get('name', '')
                
                return render_template(
                    "index.html",
                    news=news_data,
                    data=config_data,
                    is_latest_version=self.is_latest_version(),
                    has_microsoft_auth=has_microsoft_auth
                )
            except Exception as e:
                ErrorHandler.show_error_dialog(
                    "Помилка завантаження головної сторінки",
                    str(e)
                )
                return "Помилка завантаження", 500
        
        @self.app.route('/microsoft_login', methods=['POST'])
        def microsoft_login():
            try:
                def do_auth():
                    success = self.auth_manager.start_microsoft_auth()
                    if success:
                        self.websocket_manager.broadcast("auth_success")
                    else:
                        self.websocket_manager.broadcast("Авторизацію скасовано або сталася помилка")
                
                threading.Thread(target=do_auth, daemon=True).start()
                return jsonify({"success": True, "message": "Браузер відкривається..."})
                
            except Exception as e:
                self.logger.error(f"Microsoft login error: {e}")
                return jsonify({"success": False, "error": str(e)})
               
        @self.app.route('/auth/callback')
        def auth_callback():
            try:
                auth_code = request.args.get('code')
                state = request.args.get('state')
                
                if not auth_code or not state:
                    return render_template('callback.html',
                                         title="Помилка авторизації",
                                         message="Відсутні параметри авторизації",
                                         success=False)
                
                success, reason = self.auth_manager.handle_auth_callback(auth_code, state)
                
                if success:
                    return render_template('callback.html',
                                         title="Авторизація успішна!",
                                         message="Можете закрити це вікно та повернутися до лаунчера",
                                         success=True)
                else:
                    return render_template('callback.html',
                                         title="Помилка авторизації",
                                         message=f"Не вдалося завершити авторизацію: {reason}",
                                         success=False)
            except Exception as e:
                self.logger.error(f"Auth callback error: {e}")
                return render_template('callback.html',
                                     title="Критична помилка",
                                     message=f"Виникла помилка: {str(e)}",
                                     success=False)
        
        @self.app.route('/microsoft_logout', methods=['POST'])
        def microsoft_logout():
            try:
                if self.auth_manager.clear_auth_data():
                    return jsonify({"success": True, "message": "Вихід виконано"})
                else:
                    return jsonify({"success": False, "error": "Помилка виходу"})
            except Exception as e:
                self.logger.error(f"Microsoft logout error: {e}")
                return jsonify({"success": False, "error": str(e)})
        
        @self.app.route('/start', methods=['POST'])
        def start_game():
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "Невірні дані"})
                
                is_valid, message, config = Validator.validate_config(data)
                if not is_valid:
                    return jsonify({"success": False, "error": message})
                
                auth_data = self.auth_manager.load_auth_data()
                if not self.auth_manager.is_token_valid(auth_data):
                    return jsonify({
                        "success": False, 
                        "error": "Потрібна авторизація Microsoft"
                    })
            
                self.data_manager.save(asdict(config), "user.json")

                def install_and_launch():
                    try:
                        self.websocket_manager.broadcast("Підготовка")
                        
                        install_dir = self.path_manager.get_install_dir(config.loader)
                        
                        if self.modpacks_manager.check_modpack_exists(config.loader):
                            install_success = self.modpacks_manager.install_modpack(
                                config.loader, install_dir, self._create_progress_callback(), config
                            )
                        else:
                            install_success = self.modpacks_manager.install_loader(
                                "vanilla", config.loader, install_dir, self._create_progress_callback()
                            )
                        
                        if not install_success:
                            self.websocket_manager.broadcast("Помилка встановлення гри")
                            return

                        if self.launcher.launch(config, self.websocket_manager.broadcast):
                            self.websocket_manager.broadcast("Гру запущено успішно! Лаунчер закривається")
                            threading.Timer(3.0, self.close_window).start()
                        else:
                            self.websocket_manager.broadcast("Помилка запуску гри")
                            
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
        
        @self.app.route('/external_link')
        def external_link():
            try:
                url = request.args.get('url')
                if url:
                    decoded_url = urllib.parse.unquote(url)
                    if not (decoded_url.startswith('http://') or decoded_url.startswith('https://')):
                        decoded_url = 'https://' + decoded_url
                    return redirect(decoded_url)
                else:
                    return redirect('/')
            except Exception as e:
                self.logger.error(f"Помилка редіректу: {e}")
                return redirect('/')
                
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
                    import subprocess
                    subprocess.run(['xdg-open', str(install_dir)])
                    
                return jsonify({"success": True, "message": "Папку відкрито!"})
            except Exception as e:
                self.logger.error(f"Помилка при відкритті папки: {e}")
                return jsonify({"success": False, "error": str(e)})
        
        @self.app.route('/close')
        def close_launcher():
            try:
                threading.Timer(1.0, self.close_window).start()
                return jsonify({"success": True, "message": "Лаунчер закривається..."})
            except Exception as e:
                self.logger.error(f"Close launcher error: {e}")
                return jsonify({"success": False, "error": str(e)})

    def _create_auth_response(self, title: str, message: str, success: bool):
        delay = 3000 if success else 5000
        return f'''
            <html><body style="font-family: Arial; text-align: center; padding: 50px;">
                <h2>{title}</h2>
                <p>{message}</p>
                <script>setTimeout(() => window.close(), {delay});</script>
            </body></html>
        '''

    def _create_progress_callback(self):
        last_percent = -1
        last_time = time.time()
        
        def progress_callback(percent, current_file=None, status=None, message=None):
            nonlocal last_percent, last_time
            current_time = time.time()
            
            if status == 'start':
                self.websocket_manager.broadcast(message or "Початок")
            elif status in ['downloaded', 'skipped']:
                percent_int = int(percent)
                if (percent_int != last_percent and 
                    (current_time - last_time > 0.5 or abs(percent_int - last_percent) >= 1)):
                    action = "Завантаження" if status == 'downloaded' else "Перевірка файлів"
                    self.websocket_manager.broadcast(f"[{percent:.0f}%] {action}")
                    last_percent = percent_int
                    last_time = current_time
            elif status == 'complete':
                self.websocket_manager.broadcast(message or "Завершено")
            elif status == 'error':
                self.websocket_manager.broadcast(message or "Помилка")
        
        return progress_callback
    
    def replace_links_with_redirect(self, content):
        try:
            def replace_link(match):
                original_url = match.group(1)
                attributes = match.group(2)
                link_text = match.group(3)
                
                encoded_url = urllib.parse.quote(original_url, safe='')
                new_url = f"/external_link?url={encoded_url}"
                
                if 'target=' not in attributes:
                    attributes += ' target="_blank"'
                
                return f'<a href="{new_url}"{attributes}>{link_text}</a>'
            
            return re.sub(r'<a\s+(?:[^>]*\s+)?href=["\']([^"\']+)["\']([^>]*)>(.*?)</a>', 
                         replace_link, content, flags=re.IGNORECASE | re.DOTALL)
            
        except Exception as e:
            self.logger.error(f"Помилка обробки посилань: {e}")
            return content
        
    def load_news(self) -> list:
        try:
            headers = {
                'Accept': 'application/json'
            }
            response = requests.get(NEWS_URL, headers=headers, timeout=10)
            response.raise_for_status()
            news = response.json()
            processed_news = []
            for msg in news:
                if not msg.get('embeds'):
                    continue
                
                embed = msg['embeds'][0]
                if not embed.get('title') and not embed.get('image', {}).get('url'):
                    continue

                reactions = []
                if msg.get('reactions'):
                    for reaction in msg['reactions']:
                        emoji_data = reaction.get('emoji', {})
                        emoji = emoji_data.get('name', '') if isinstance(emoji_data, dict) else reaction.get('name', '')
                        count = reaction.get('count', 0)
                        if emoji:
                            reactions.append({
                                'emoji': emoji,
                                'count': count
                            })
                
                news_item = {
                    'id': msg['id'],
                    'title': embed.get('title', ''),
                    'description': '',
                    'timestamp': '',
                    'image': embed.get('image', {}).get('url', ''),
                    'footer': embed.get('footer', {}).get('text', ''),
                    'url': embed.get('url', ''),
                    'reactions': reactions
                }
                
                if embed.get('description'):
                    html_content = markdown.markdown(embed['description'])
                    news_item['description'] = self.replace_links_with_redirect(html_content)
                
                if msg.get('timestamp'):
                    timestamp = msg['timestamp']
                    timestamp = timestamp.split(".")[0].replace("T", " ").split("+")[0]
                    news_item['timestamp'] = timestamp
                
                processed_news.append(news_item)
            
            return processed_news
        except Exception as e:
            self.logger.error(f"Failed to load news: {e}")
            return []
            
    def is_latest_version(self) -> bool:
        try:
            response = requests.get(GITHUB_REPO, timeout=5)
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
    
    def is_already_running() -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', FLASK_PORT)) == 0

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

        if app.is_already_running():
            messagebox.showinfo("QQQ-CRAFT", "Лаунчер вже запущений!")
            sys.exit(0)

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
                            import subprocess
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