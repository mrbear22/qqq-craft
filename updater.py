import requests
import os
import sys
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox
from packaging import version
import win32api

LAUNCHER_DIR = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Programs", "QQQ-CRAFT")
LAUNCHER_PATH = os.path.join(LAUNCHER_DIR, "launcher.exe")
DOWNLOAD_URL = "https://qqq-craft.top/launcher/launcher.zip"
REPO_API_URL = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"
ZIP_PATH = os.path.join(LAUNCHER_DIR, "launcher.zip")

def show_message(title, message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(title, message)
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
            return response.json().get("tag_name", "0.0.0.0")
    except requests.RequestException:
        pass
    return "0.0.0.0"

def download_new_version():
    try:
        response = requests.get(DOWNLOAD_URL, stream=True)
        total_size = int(response.headers.get('content-length', 0))

        if response.status_code == 200:
            with open(ZIP_PATH, 'wb') as f, ProgressWindow(total_size) as progress:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
                    progress.update_progress(len(chunk))
            return True
    except requests.RequestException:
        pass
    return False

class ProgressWindow:
    def __init__(self, total_size):
        self.total_size = total_size
        self.downloaded = 0
        self.root = tk.Tk()
        self.root.title("Завантаження...")

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

        self.root.update()

    def update_progress(self, size):
        self.downloaded += size
        self.progress["value"] = (self.downloaded / self.total_size) * 100
        self.root.update()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.root.destroy()

def main():
    local_version = get_exe_version(LAUNCHER_PATH)
    latest_version = get_latest_version()

    if version.parse(local_version) < version.parse(latest_version):
        root = tk.Tk()
        root.withdraw()
        result = messagebox.askyesno("Оновлення", f"Доступна нова версія {latest_version}. Завантажити?")
        root.destroy()

        if result:
            if download_new_version():
                with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
                    zip_ref.extractall(LAUNCHER_DIR)
                subprocess.run(LAUNCHER_PATH);
                show_message("Готово!", "Оновлення завантажено та розпаковано.\nЗапустіть лаунчер повторно.")
            else:
                show_message("Помилка", "Не вдалося завантажити оновлення.")
    else:
        show_message("Оновлення", "У вас найновіша версія.")

if __name__ == "__main__":
    main()
