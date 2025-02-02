import os
import requests
import PyInstaller.__main__
from packaging import version
import subprocess

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

scripts = ['launcher', 'updater']
signtool = "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\x64\\signtool.exe"
cert_path = "Certificate.pfx"
timestamp_url = "http://timestamp.digicert.com"
icon_path = 'static/logo.ico'
version_file = 'version.txt'
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

    for script in scripts:
        install_command = [
            '--onefile',
            '--noconsole',
            '--add-data', f'static{os.pathsep}static',
            '--icon', icon_path,
            '--version-file', version_file,
            f'{script}.py'
        ]
        
        if script == "launcher":
            install_command.append('--add-data')
            install_command.append(f'templates{os.pathsep}templates')
            
            install_command.append('--add-data')
            install_command.append(f'game{os.pathsep}game')
        
        PyInstaller.__main__.run(install_command)

        sign_command = [
            signtool,
            "sign",
            "/f", cert_path,
            "/t", timestamp_url,
            "/fd", "sha256",
            f'dist/{script}.exe'
        ]

        subprocess.run(sign_command)

else:
    print("Не вдалося отримати або збільшити версію.")
