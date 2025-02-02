from flask import Flask, render_template, request, jsonify, send_from_directory
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PyQt5.QtWebEngineWidgets import QWebEngineProfile, QWebEnginePage, QWebEngineView
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QUrl
import minecraft_launcher_lib
from packaging import version
from tkinter import ttk, messagebox
import tkinter as tk
import websockets
import subprocess
import threading
import traceback
import markdown
import win32api
import requests
import logging
import asyncio
import shutil
import socket
import json
import uuid
import sys
import os
import re

def show_message(title, message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(title, message)
    root.destroy()

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

REPO_API_URL = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"
BASE_DIR = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Programs", "QQQ-CRAFT")
INSTALL_DIR = os.path.join(BASE_DIR, "GameData")
USERDATA_FILE = os.path.join(BASE_DIR, "user.json")

FLASK_PORT = 7776
WEBSOCKET_PORT = 8765

app = Flask(__name__)
connected_clients = set()
app_qt = QApplication(sys.argv)
window = None

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

log_file = os.path.join(BASE_DIR, "logs.txt")
sys.stdout = open(log_file, "a", encoding="utf-8")
sys.stderr = sys.stdout

def get_exe_version(path):
    try:
        app.logger.info(f"Getting version for {path}")
        info = win32api.GetFileVersionInfo(path, "\\")
        ms = info['FileVersionMS']
        ls = info['FileVersionLS']
        version = f"{win32api.HIWORD(ms)}.{win32api.LOWORD(ms)}.{win32api.HIWORD(ls)}.{win32api.LOWORD(ls)}"
        app.logger.info(f"Found version: {version}")
        return version
    except Exception as e:
        app.logger.error(f"Error getting version for {path}: {e}")
        return "0.0.0.0"

def get_latest_version():
    try:
        response = requests.get(REPO_API_URL)
        if response.status_code == 200:
            return response.json().get("tag_name", "0.0.0.0")
    except requests.RequestException:
        pass
    return "0.0.0.0"

class BrowserWindow(QMainWindow):
    def __init__(self):
        global window
        super().__init__()
        self.setWindowTitle("QQQ - Час стати легендою!")
        self.setGeometry(0, 0, 1280, 720)
        self.setMinimumSize(1280, 720)
        self.setWindowIcon(QIcon(get_resource_path("static/logo.ico")))
        
        cache_path = os.path.join(BASE_DIR, "cache")
        os.makedirs(cache_path, exist_ok=True)

        self.profile = QWebEngineProfile("QQQProfile", self)
        self.profile.setCachePath(cache_path)
        self.profile.setPersistentStoragePath(cache_path)

        self.page = QWebEnginePage(self.profile, self)

        self.browser = QWebEngineView()
        self.browser.setPage(self.page)
        self.browser.setUrl(QUrl(f"http://127.0.0.1:{FLASK_PORT}/"))

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.browser)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
   
        self.center_window()

    def center_window(self):
        screen_geometry = self.screen().geometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)

@app.route('/update')
def update():
    subprocess.run(['explorer', "https://qqq-craft.top/game"])

@app.route('/static/<filename>')
def static_file(filename):
    return send_from_directory(get_resource_path('static'), filename)

@app.route('/game_folder')
def game_folder():
    subprocess.run(['explorer', INSTALL_DIR])
    return render_template('index.html')

@app.route("/")
def index():
    def get_news():
        json_url = "https://qqq-craft.top/news/?get"
        news = []
        try:
            response = requests.get(json_url)
            response.raise_for_status()
            news = response.json()
            for news_item in news:
                if news_item.get("description"):
                    news_item["description"] = markdown.markdown(news_item["description"])
                if news_item.get("timestamp"):
                    news_item["timestamp"] = news_item["timestamp"].split(".")[0].replace("T", " ").split("+")[0]
        except Exception as e:
            app.logger.error(f"Помилка завантаження новин: {e}")
        return news
    
    def is_latest_version():
                
        local_version = get_exe_version(os.path.join(BASE_DIR, "launcher.exe"))
        latest_version = get_latest_version()
        
        return version.parse(local_version) >= version.parse(latest_version)

    return render_template("index.html", news=get_news(), data=get_data(), is_latest_version=is_latest_version()) 
    
@app.route('/start', methods=['POST'])
def start():
    
    def validate_nickname(nickname):
        if not nickname:
            return False, "Нікнейм не може бути порожнім."
        if len(nickname) < 3 or len(nickname) > 16:
            return False, "Нікнейм повинен бути від 3 до 16 символів."
        if not re.match("^[a-zA-Z0-9_-]*$", nickname):
            return False, "Нікнейм може містити лише літери, цифри, дефіс або підкреслення."
        forbidden_words = ["admin", "moderator", "staff", "banned"]
        if any(word in nickname.lower() for word in forbidden_words):
            return False, "Нікнейм не може містити заборонених слів."
        return True, "Нікнейм валідний."
    
    try:
        data = request.get_json()
        mc_version = "1.21.1"
        fabric_version = "0.16.10"  
        is_valid, message = validate_nickname(data.get('nickname'))
        
        if (is_valid):
            try:
                save_data(data)
                setup_minecraft(mc_version, fabric_version)                
                launch_minecraft(mc_version, fabric_version, data)
                return jsonify({"success": True})
            except Exception as e:
                app.logger.error(f"Помилка: {str(e)}")
                return jsonify({"success": False, "error": str(e)})
        else:
            send_log(message)
            return jsonify({"success": False, "error": message})
    except Exception as e:
        error_message = traceback.format_exc()
        app.logger.error(error_message) 
        


def setup_minecraft(mc_version, fabric_version):
    try:
        src_dir = get_resource_path("game/")
        for item in os.listdir(src_dir):
            src_item = os.path.join(src_dir, item)
            dest_item = os.path.join(INSTALL_DIR, item)
            if os.path.isdir(src_item) and not os.path.exists(dest_item):
                shutil.copytree(src_item, dest_item)
            elif os.path.isfile(src_item) and not os.path.exists(dest_item):
                shutil.copy2(src_item, dest_item)
        send_log(f"Встановлення гри...")
        minecraft_launcher_lib.install.install_minecraft_version(mc_version, INSTALL_DIR)
        send_log("Гру успішно встановлено.")
        fabric_folder_path = os.path.join(INSTALL_DIR, "versions", f"fabric-loader-{fabric_version}-{mc_version}")
        if not os.path.exists(fabric_folder_path):
            send_log("Встановлення Fabric...")
            minecraft_launcher_lib.fabric.install_fabric(mc_version, INSTALL_DIR)
            send_log("Fabric успішно встановлено.")
    except Exception as e:
        error_message = traceback.format_exc()
        app.logger.error(error_message) 
        

def launch_minecraft(mc_version, fabric_version, data):
    try:
        def generate_offline_uuid(nickname):
            namespace = uuid.UUID('00000000-0000-0000-0000-000000000000') 
            return uuid.uuid3(namespace, "OfflinePlayer:" + nickname)
        send_log("Підготовка до запуску гри...")
        fabric_loader_name = f"fabric-loader-{fabric_version}-{mc_version}"
        nickname = data.get('nickname')
        width, height = data.get('windowSize', '1024x768').split('x')
        options = {
            "username": nickname,
            "uuid": str(generate_offline_uuid(nickname)),
            "gameDirectory": INSTALL_DIR,
            "jvmArguments": [f"-Xmx{data.get('ram', '2G')}"],
            "launcherName": "QQQ",
            "launcherVersion": "1.0",
            "customResolution": True,
            "resolutionWidth": width,
            "resolutionHeight": height,
            "fullscreen": data.get('fullscreen'),
        }
        if data.get('multiplayer', False):
            options["quickPlayMultiplayer"] = "play.qqq-craft.top"
            
        command = minecraft_launcher_lib.command.get_minecraft_command(fabric_loader_name, INSTALL_DIR, options) 
        send_log("Запуск гри...")

        def close_window():
            window.close()
        threading.Timer(20.0, close_window).start()
        subprocess.run(command, cwd=INSTALL_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        error_message = traceback.format_exc()
        app.logger.error(error_message) 
    
def save_data(data):
    try:
        with open(USERDATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        error_message = traceback.format_exc()
        app.logger.error(error_message) 
        
def get_data():
    if os.path.exists(USERDATA_FILE) and os.path.getsize(USERDATA_FILE) > 0:
        with open(USERDATA_FILE, 'r') as f:
            try:
                return json.load(f)
            except Exception as e:
                error_message = traceback.format_exc()
                print(error_message) 
                return {}
    else:
        return {}
    
def send_log(message):
    global connected_clients
    if connected_clients:
        for client in connected_clients:
            try:
                asyncio.run(client.send(message))
            except Exception as e:
                app.logger.error(f"Помилка відправки повідомлення: {e}")

def run_flask():
    app.run(debug=False, port=FLASK_PORT, use_reloader=False, threaded=True)

def run_websocket():
    async def websocket_handler(websocket):
        global connected_clients
        connected_clients.add(websocket)
        try:
            while True:
                await websocket.recv() 
                await websocket.send("Повідомлення від сервера")
                await asyncio.sleep(5)
        except Exception as e:
            app.logger.error(f"Клієнт відключився: {e}")
        finally:
            connected_clients.remove(websocket)

    async def start_websocket_server():
        server = await websockets.serve(websocket_handler, "127.0.0.1", WEBSOCKET_PORT)
        await server.wait_closed()

    asyncio.new_event_loop().run_until_complete(start_websocket_server())

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

if __name__ == "__main__":
    
    local_version = get_exe_version(os.path.join(BASE_DIR, "launcher.exe"))
    latest_version = get_latest_version()

    if version.parse(local_version) < version.parse(latest_version):
        root = tk.Tk()
        root.withdraw()
        result = messagebox.askyesno("Оновлення", f"Доступна нова версія {latest_version}. Бажаєте завантажити?")
        
        if result:
            subprocess.run(['explorer', "https://qqq-craft.top/game"])
            sys.exit(1)
    
    if is_port_in_use(FLASK_PORT):
        print(f"Помилка: порт {FLASK_PORT} вже використовується!")
        sys.exit(1) 

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=run_websocket, daemon=True).start()
    window = BrowserWindow()
    window.show()
    sys.exit(app_qt.exec_())