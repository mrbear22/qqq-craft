from flask import Flask, render_template, request, jsonify, send_from_directory
from tkinter import messagebox
import minecraft_launcher_lib
from packaging import version
import tkinter as tk
import websockets
import subprocess
import threading
import traceback
import markdown
import win32api
import requests
import webview
import asyncio
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

BASE_DIR = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Programs", "qqq-craft")

def save_data(data, file_name):
    try:
        with open(os.path.join(BASE_DIR, "static", file_name), 'w') as f:
            json.dump(data, f, indent=4)
    except:
        pass
        
def get_data(file_name):
    data_file = os.path.join(BASE_DIR, "static", file_name)
    if os.path.exists(data_file) and os.path.getsize(data_file) > 0:
        with open(data_file, 'r') as f:
            try:
                return json.load(f)
            except:
                pass
    return {}

def get_resource_path(relative_path):
    return os.path.abspath(relative_path)

INSTALL_DIR = os.path.join(BASE_DIR, get_data("user.json")["loader"])

FLASK_PORT = 7776
WEBSOCKET_PORT = 8765

app = Flask(__name__)
connected_clients = set()
window = None

def get_exe_version(path):
    try:
        app.logger.info(f"Getting version for {path}")
        info = win32api.GetFileVersionInfo(path, "\\")
        ms = info['FileVersionMS']
        ls = info['FileVersionLS']
        version = f"{win32api.HIWORD(ms)}.{win32api.LOWORD(ms)}.{win32api.HIWORD(ls)}.{win32api.LOWORD(ls)}"
        app.logger.info(f"Found version: {version}")
        return version
    except:
        return "0.0.0.0"

def get_latest_version():
    try:
        response = requests.get(get_data("data.json")["github-repo"])
        if response.status_code == 200:
            return response.json().get("tag_name", "0.0.0.0")
    except:
        return "0.0.0.0"

@app.route('/update')
def update():
    os.startfile(get_data("data.json")["update-url"])


@app.route('/static/<filename>')
def static_file(filename):
    return send_from_directory(get_resource_path('static'), filename)

@app.route('/game_folder')
def game_folder():
    os.startfile(INSTALL_DIR)
    return render_template('index.html')

@app.route("/")
def index():
    def get_news():
        try:
            response = requests.get(get_data("data.json")["news-url"])
            response.raise_for_status()
            news = response.json()
            for news_item in news:
                if news_item.get("description"):
                    news_item["description"] = markdown.markdown(news_item["description"])
                if news_item.get("timestamp"):
                    news_item["timestamp"] = news_item["timestamp"].split(".")[0].replace("T", " ").split("+")[0]
            return news
        except:
            return {}
    
    def is_latest_version():  
        local_version = get_exe_version(os.path.join(BASE_DIR, "launcher.exe"))
        latest_version = get_latest_version()
        return version.parse(local_version) >= version.parse(latest_version)

    return render_template("index.html", news=get_news(), data=get_data("user.json"), is_latest_version=is_latest_version()) 
    
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
        loader = data.get('loader')
        is_valid, message = validate_nickname(data.get('nickname'))
        
        if (is_valid):
            try:
                save_data(data, "user.json")
                setup_minecraft(mc_version, loader)                
                launch_minecraft(mc_version, loader, data)
                return jsonify({"success": True})
            except Exception as e:
                app.logger.error(f"Помилка: {str(e)}")
                return jsonify({"success": False, "error": str(e)})
        else:
            send_log(message)
            return jsonify({"success": False, "error": message})
    except:
        show_message("Який жах!", f"Сталась помилка. Покажіть дане повідомлення адміністрації сервера: \n{traceback.format_exc()}")
        
def setup_minecraft(mc_version, loader):
    global INSTALL_DIR
    try:
        if loader == "fabric":
            INSTALL_DIR = os.path.join(BASE_DIR, "fabric")
            fabric_version = "0.16.10"
            fabric_folder_path = os.path.join(INSTALL_DIR, "versions", f"fabric-loader-{fabric_version}-{mc_version}")
            if not os.path.exists(fabric_folder_path):
                send_log("Встановлення Fabric...")
                minecraft_launcher_lib.fabric.install_fabric(mc_version, INSTALL_DIR)

        elif loader == "forge":
            INSTALL_DIR = os.path.join(BASE_DIR, "forge")
            forge_version = minecraft_launcher_lib.forge.find_forge_version("1.21.1")
            forge_folder_path = os.path.join(INSTALL_DIR, "versions", f"{mc_version}-forge-52.0.47")
            if not os.path.exists(forge_folder_path):
                send_log("Встановлення Forge...")
                minecraft_launcher_lib.forge.install_forge_version(forge_version, INSTALL_DIR)

        elif loader == "vanilla":
            INSTALL_DIR = os.path.join(BASE_DIR, "vanilla")
            if not os.path.exists(os.path.join(INSTALL_DIR, "versions", mc_version)):
                send_log("Встановлення Vanilla...")
                minecraft_launcher_lib.forge.install_minecraft_version(mc_version, INSTALL_DIR)
                
        send_log("Гру успішно встановлено.")

    except:
        show_message("Який жах!", f"Сталась помилка. Покажіть дане повідомлення адміністрації сервера: \n{traceback.format_exc()}")

def launch_minecraft(mc_version, loader, data):
    try:
        def generate_offline_uuid(nickname):
            namespace = uuid.UUID('00000000-0000-0000-0000-000000000000') 
            return uuid.uuid3(namespace, "OfflinePlayer:" + nickname)

        send_log("Підготовка до запуску гри...")

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

        def close_window():
            window.destroy()

        try:
            send_log(f"Запуск гри...")

            if loader == "fabric":
                fabric_version = "0.16.14"
                command = minecraft_launcher_lib.command.get_minecraft_command(f"fabric-loader-{fabric_version}-{mc_version}", INSTALL_DIR, options)
            elif loader == "forge":
                forge_version = "52.0.47"
                command = minecraft_launcher_lib.command.get_minecraft_command(f"{mc_version}-forge-{forge_version}", INSTALL_DIR, options)
            elif loader == "vanilla":
                command = minecraft_launcher_lib.command.get_minecraft_command(mc_version, INSTALL_DIR, options)

            threading.Timer(20.0, close_window).start()
            if data.get('console', False):
                subprocess.run(command, cwd=INSTALL_DIR, check=True)
            else:
                subprocess.run(command, cwd=INSTALL_DIR, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                
        except Exception as e:
            show_message("Який жах!", f"Помилка запуску гри ({loader}): {str(e)}")

    except:
        show_message("Який жах!", f"Сталась помилка. Покажіть дане повідомлення адміністрації сервера: \n{traceback.format_exc()}")
    
def send_log(message):
    global connected_clients
    if connected_clients:
        for client in connected_clients:
            try:
                asyncio.run(client.send(message))
            except:
                pass

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
        except:
            pass
        finally:
            connected_clients.remove(websocket)

    async def start_websocket_server():
        server = await websockets.serve(websocket_handler, "localhost", WEBSOCKET_PORT)
        await server.wait_closed()

    asyncio.new_event_loop().run_until_complete(start_websocket_server())

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0

if __name__ == "__main__":
    
    local_version = get_exe_version(os.path.join(BASE_DIR, "launcher.exe"))
    latest_version = get_latest_version()

    if version.parse(local_version) < version.parse(latest_version):
        root = tk.Tk()
        root.withdraw()
        result = messagebox.askyesno("Оновлення", f"Доступна нова версія {latest_version}. Бажаєте завантажити?")
        
        if result:
            os.startfile(get_data("data.json")["update-url"])
            sys.exit(1)
    
    if is_port_in_use(FLASK_PORT):
        print(f"Помилка: порт {FLASK_PORT} вже використовується!")
        sys.exit(1) 

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=run_websocket, daemon=True).start()
    
    window = webview.create_window(
        "QQQ - Час стати легендою!",
        f"http://localhost:{FLASK_PORT}/",
        width=1280,
        height=720,
        resizable=False
    )

    webview.start(icon=get_resource_path("static/logo.ico"))