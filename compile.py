import os
import requests
import PyInstaller.__main__
from packaging import version

# Отримуємо останню версію з GitHub
def get_latest_version():
    repo_url = "https://api.github.com/repos/mrbear22/qqq-craft/releases/latest"
    response = requests.get(repo_url)
    if response.status_code == 200:
        data = response.json()
        return data["tag_name"]
    else:
        print(f"Не вдалося отримати інформацію про версії. Статус: {response.status_code}")
        print(response.text)  # Виводимо текст відповіді для дебагу
        return None

# Збільшуємо версію на 1
def increment_version(latest_version):
    v = version.parse(latest_version)
    # Збільшуємо останній компонент версії
    new_version = f"{v.major}.{v.minor}.{v.micro + 1}"
    return new_version

# Шлях до файлів
script_path = 'app.py'
icon_path = 'static/logo.ico'

# Отримуємо нову версію
latest_version = get_latest_version()
if latest_version:
    new_version = increment_version(latest_version)
    print(f"Нова версія: {new_version}")
    
    # Запускаємо PyInstaller з новою версією
    PyInstaller.__main__.run([
        '--onefile',
        '--noconsole',
        '--add-data', f'static{os.pathsep}static',
        '--add-data', f'templates{os.pathsep}templates',
        '--icon', icon_path,
        '--version', new_version,  # Вказуємо версію
        script_path
    ])
else:
    print("Не вдалося отримати або збільшити версію.")
