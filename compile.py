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
        return None

def increment_version(latest_version):
    v = version.parse(latest_version)
    new_version = f"{v.major}.{v.minor}.{v.micro + 1}"
    return new_version

script_path = 'app.py'
icon_path = 'static/logo.ico'
version_file = 'version_info.txt'

latest_version = get_latest_version()
if latest_version:
    new_version = increment_version(latest_version)
    print(f"Нова версія: {new_version}")
    
    version_info = f"""
# UTF-8
#
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({new_version.replace(".", ",")}, 0),
    prodvers=({new_version.replace(".", ",")}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904b0',
          [
            StringStruct('CompanyName', 'QQQ-CRAFT'),
            StringStruct('FileDescription', 'Launcher for QQQ-CRAFT project'),
            StringStruct('FileVersion', '{new_version}'),
            StringStruct('InternalName', 'QQQ-CRAFT'),
            StringStruct('LegalCopyright', 'Copyright © 2025'),
            StringStruct('OriginalFilename', 'qqq-craft.exe'),
            StringStruct('ProductName', 'QQQ-CRAFT'),
            StringStruct('ProductVersion', '{new_version}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(version_file, 'w', encoding='utf-8') as f:
        f.write(version_info)

    PyInstaller.__main__.run([
        '--onefile',
        '--noconsole',
        '--add-data', f'static{os.pathsep}static',
        '--add-data', f'templates{os.pathsep}templates',
        '--icon', icon_path,
        '--version-file', version_file,
        script_path
    ])
else:
    print("Не вдалося отримати або збільшити версію.")
