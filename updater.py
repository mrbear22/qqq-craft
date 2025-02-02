import requests
import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from packaging import version
import pefile
import win32api

LAUNCHER_PATH = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Programs", "QQQ-CRAFT", "launcher.exe")
DOWNLOAD_URL = "https://qqq-craft.top/launcher/launcher.exe"
REPO_API_URL = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"

def show_error_message(message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Помилка", message)
    root.destroy()

def get_exe_version(path):
    try:
        info = win32api.GetFileVersionInfo(path, "\\")
        ms = info['FileVersionMS']
        ls = info['FileVersionLS']
        return f"{win32api.HIWORD(ms)}.{win32api.LOWORD(ms)}.{win32api.HIWORD(ls)}.{win32api.LOWORD(ls)}"
    except Exception:
        return "0.0.0.0"

def get_latest_version():
    try:
        response = requests.get(REPO_API_URL)
        if response.status_code == 200:
            data = response.json()
            return data["tag_name"]
        else:
            show_error_message("Не вдалося отримати останню версію. Перевірте з'єднання з інтернетом.")
            sys.exit()
    except requests.RequestException:
        show_error_message("Не вдалося підключитися до інтернету. Перевірте з'єднання.")
        sys.exit()

def download_new_version():
    try:
        response = requests.get(DOWNLOAD_URL, stream=True)
        total_size = int(response.headers.get('content-length', 0))

        if response.status_code == 200:
            with open(LAUNCHER_PATH, 'wb') as f, ProgressWindow(total_size) as progress:
                for chunk in response.iter_content(1024):
                    if chunk:
                        f.write(chunk)
                        progress.update_progress(len(chunk))
        else:
            show_error_message("Не вдалося завантажити файл. Перевірте з'єднання з інтернетом.")
            sys.exit()
    except requests.RequestException:
        show_error_message("Не вдалося завантажити файл. Перевірте з'єднання з інтернетом.")
        sys.exit()

class ProgressWindow:
    def __init__(self, total_size):
        self.total_size = total_size
        self.downloaded = 0
        self.root = tk.Tk()
        self.root.title("Оновлення...")
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        window_width = 400
        window_height = 100

        x_position = (screen_width // 2) - (window_width // 2)
        y_position = (screen_height // 2) - (window_height // 2)

        self.root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        self.root.resizable(False, False)

        icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "static", "logo.ico"))
        if os.path.exists(icon_path):  
            self.root.iconbitmap(icon_path)

        self.label = tk.Label(self.root, text="Завантаження оновлення...", font=("Arial", 12))
        self.label.pack(pady=10)
        self.progress = ttk.Progressbar(self.root, length=250, mode="determinate")
        self.progress.pack(pady=5)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)  
        self.root.update()

    def update_progress(self, size):
        self.downloaded += size
        percent = (self.downloaded / self.total_size) * 100
        self.progress["value"] = percent
        self.root.update()

    def on_close(self):
        print("Не можна закрити вікно під час завантаження.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.root.destroy()

def main():
    local_version = get_exe_version(LAUNCHER_PATH)
    latest_version = get_latest_version()

    print(f"Локальна версія: {local_version}")
    print(f"Остання версія на GitHub: {latest_version}")

    if latest_version and version.parse(local_version) < version.parse(latest_version):
        print("🔹 Доступне оновлення! Завантажуємо...")
        download_new_version()
    else:
        print("✅ У вас найновіша версія!")

    subprocess.Popen([LAUNCHER_PATH], shell=True)
    sys.exit()

if __name__ == "__main__":
    main()