import os
import shutil
import subprocess
from pathlib import Path
import PyInstaller.__main__
from packaging import version
import requests

APP_NAME = "qqq-craft"
LAUNCHER_SCRIPT = "launcher.py"
OUTPUT_BINARY_NAME = "qqq-craft"
DEB_VERSION = "1.0.0"
BUILD_DIR = Path("build_deb")
DIST_DIR = Path("dist")
DEB_OUTPUT = DIST_DIR / f"{APP_NAME}_{DEB_VERSION}_amd64.deb"

def get_latest_version():
    repo_url = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"
    try:
        response = requests.get(repo_url)
        if response.status_code == 200:
            tag = response.json()["tag_name"]
            return tag
    except:
        pass
    return "1.0.0"

def increment_version(ver: str):
    v = version.parse(ver)
    return f"{v.major}.{v.minor}.{v.micro + 1}"

def build_pyinstaller():
    print("[1/4] Компіляція PyInstaller...")
    PyInstaller.__main__.run([
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name", OUTPUT_BINARY_NAME,
        "--add-data", f"static:static",
        "--add-data", f"templates:templates",
        "--add-data", f"fabric:fabric",
        "--add-data", f"forge:forge",
        "--add-data", f"vanilla:vanilla",
        LAUNCHER_SCRIPT
    ])

def prepare_deb_structure():
    print("[2/4] Підготовка структури .deb...")
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    # Структура: /usr/local/bin/qqq-craft
    bin_path = BUILD_DIR / "usr/local/bin"
    bin_path.mkdir(parents=True, exist_ok=True)
    shutil.copy(DIST_DIR / OUTPUT_BINARY_NAME, bin_path / APP_NAME)
    os.chmod(bin_path / APP_NAME, 0o755)

    # DEBIAN/control
    debian_path = BUILD_DIR / "DEBIAN"
    debian_path.mkdir(parents=True, exist_ok=True)
    control_content = f"""Package: {APP_NAME}
Version: {DEB_VERSION}
Section: games
Priority: optional
Architecture: amd64
Maintainer: Your Name <you@example.com>
Description: QQQ-Craft launcher for Linux.
"""
    with open(debian_path / "control", "w", encoding="utf-8") as f:
        f.write(control_content)

def build_deb():
    print("[3/4] Створення .deb пакета...")
    if not DIST_DIR.exists():
        DIST_DIR.mkdir()
    subprocess.run(["dpkg-deb", "--build", BUILD_DIR, DEB_OUTPUT], check=True)

def cleanup():
    print("[4/4] Прибирання...")
    shutil.rmtree(BUILD_DIR)

if __name__ == "__main__":
    latest_version = get_latest_version()
    DEB_VERSION = increment_version(latest_version)

    build_pyinstaller()
    prepare_deb_structure()
    build_deb()
    cleanup()

    print(f"\n✅ Готово: {DEB_OUTPUT}")
    print("Щоб встановити:")
    print(f"  sudo dpkg -i {DEB_OUTPUT}")
