import os
import requests
import PyInstaller.__main__
from packaging import version

def get_latest_version():
    repo_url = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"
    response = requests.get(repo_url)
    if response.status_code == 200:
        data = response.json()
        return data["tag_name"]
    else:
        print(f"Не вдалося отримати інформацію про версії. Статус: {response.status_code}")
        print(response.text)
        return "1.0.0"

def increment_version(latest_version):
    v = version.parse(latest_version)
    return f"{v.major}.{v.minor}.{v.micro + 1}"

scripts = ["launcher"]
icon_path = "static/logo.ico"
version_file = "version.txt"

latest_version = get_latest_version()
if not latest_version:
    print("Не вдалося отримати або збільшити версію.")
    exit(1)

new_version = increment_version(latest_version)
print(f"Нова версія: {new_version}")

# Optional: write version string to version.txt (informational only)
with open(version_file, 'w', encoding='utf-8') as f:
    f.write(f"Version: {new_version}\n")

# Run PyInstaller
for script in scripts:
    build_command = [
        '--noconfirm',
        '--clean',
        '--onefile',
        '--noconsole',
        '--name', f'{script}-linux',
        '--icon', icon_path,
        '--add-data', f'static{os.pathsep}static',
        '--add-data', f'templates{os.pathsep}templates',
        '--add-data', f'fabric{os.pathsep}fabric',
        '--add-data', f'forge{os.pathsep}forge',
        '--add-data', f'vanilla{os.pathsep}vanilla',
        f'{script}.py'
    ]

    PyInstaller.__main__.run(build_command)
