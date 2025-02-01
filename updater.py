import requests
import os
import sys
import subprocess
import ctypes
import tkinter as tk
from tkinter import ttk
from packaging import version

DOWNLOAD_URL = "https://qqq-craft.top/launcher/app.exe"
EXE_PATH = "app.exe"
REPO_API_URL = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"

def get_exe_version(file_path):
    try:
        info = ctypes.windll.version.GetFileVersionInfoW(file_path, "\\")
        ms = info['FileVersionMS']
        ls = info['FileVersionLS']
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return "0.0.0.0"

def get_latest_version():
    response = requests.get(REPO_API_URL)
    if response.status_code == 200:
        data = response.json()
        return data["tag_name"]
    return None

def download_new_version():
    response = requests.get(DOWNLOAD_URL, stream=True)
    total_size = int(response.headers.get('content-length', 0))

    if response.status_code == 200:
        with open(EXE_PATH, 'wb') as f, ProgressWindow(total_size) as progress:
            for chunk in response.iter_content(1024):
                if chunk:
                    f.write(chunk)
                    progress.update_progress(len(chunk))

class ProgressWindow:
    def __init__(self, total_size):
        self.total_size = total_size
        self.downloaded = 0
        self.root = tk.Tk()
        self.root.title("Оновлення...")
        self.root.geometry("300x100")
        self.root.resizable(False, False)
        self.label = tk.Label(self.root, text="Завантаження оновлення...", font=("Arial", 12))
        self.label.pack(pady=10)
        self.progress = ttk.Progressbar(self.root, length=250, mode="determinate")
        self.progress.pack(pady=5)
        self.root.update()

    def update_progress(self, size):
        self.downloaded += size
        percent = (self.downloaded / self.total_size) * 100
        self.progress["value"] = percent
        self.root.update()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.root.destroy()

def main():
    local_version = get_exe_version(EXE_PATH)
    latest_version = get_latest_version()

    if latest_version and version.parse(local_version) < version.parse(latest_version):
        download_new_version()
    
    subprocess.Popen([EXE_PATH], shell=True)
    sys.exit()

if __name__ == "__main__":
    main()
