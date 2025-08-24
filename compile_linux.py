#!/usr/bin/env python3
"""
QQQ-CRAFT LAUNCHER DEB PACKAGE BUILDER
Скрипт для створення DEB пакету з лаунчера Minecraft
"""

import os
import sys
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime

class DebPackageBuilder:
    def __init__(self):
        self.app_name = "qqq-craft-launcher"
        self.app_version = "1.0.24.0"
        self.app_description = "QQQ-CRAFT Minecraft Launcher"
        self.app_maintainer = "QQQ-CRAFT Team <support@qqq-craft.top>"
        self.app_homepage = "https://qqq-craft.top"
        
        # Поточна директорія з вихідними файлами
        self.source_dir = Path.cwd()
        self.build_dir = self.source_dir / "build"
        self.dist_dir = self.source_dir / "dist"
        
        # Файли що мають бути включені
        self.required_files = [
            "paste.txt",  # Основний файл лаунчера
            "templates/",  # HTML шаблони (якщо існують)
            "static/",     # Статичні файли (якщо існують)
        ]
        
    def check_dependencies(self):
        """Перевірка наявності необхідних залежностей"""
        print("🔍 Перевірка залежностей...")
        
        # Перевірка PyInstaller
        try:
            subprocess.run(["pyinstaller", "--version"], 
                         check=True, capture_output=True)
            print("✅ PyInstaller знайдено")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("❌ PyInstaller не знайдено. Встановлюю...")
            subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], 
                         check=True)
        
        # Перевірка dpkg-deb
        try:
            subprocess.run(["dpkg-deb", "--version"], 
                         check=True, capture_output=True)
            print("✅ dpkg-deb знайдено")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("❌ dpkg-deb не знайдено. Встановіть: sudo apt install dpkg-dev")
            return False
        
        return True
    
    def prepare_build_dirs(self):
        """Підготовка директорій для збірки"""
        print("📁 Підготовка директорій...")
        
        # Очищення старих збірок
        if self.build_dir.exists():
            shutil.rmtree(self.build_dir)
        if self.dist_dir.exists():
            shutil.rmtree(self.dist_dir)
        
        # Створення нових директорій
        self.build_dir.mkdir(parents=True)
        self.dist_dir.mkdir(parents=True)
        
        print("✅ Директорії підготовлено")
    
    def create_executable(self):
        """Створення виконуваного файлу за допомогою PyInstaller"""
        print("🔨 Створення виконуваного файлу...")
        
        # Перейменування основного файлу для зручності
        main_script = self.source_dir / "launcher.py"
        if not main_script.exists():
            shutil.copy(self.source_dir / "paste.txt", main_script)
        
        # Створення spec файлу для PyInstaller
        spec_content = f'''
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=['{self.source_dir}'],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'flask', 'requests', 'minecraft_launcher_lib', 
        'packaging', 'tkinter', 'webview', 'websockets',
        'markdown', 'hashlib'
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='{self.app_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='{self.app_name}',
)
'''
        
        spec_file = self.source_dir / f"{self.app_name}.spec"
        with open(spec_file, 'w', encoding='utf-8') as f:
            f.write(spec_content)
        
        # Запуск PyInstaller
        try:
            subprocess.run([
                "pyinstaller", 
                "--clean",
                "--noconfirm",
                str(spec_file)
            ], check=True)
            print("✅ Виконуваний файл створено")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Помилка PyInstaller: {e}")
            return False
        finally:
            # Очищення тимчасових файлів
            if main_script.exists() and main_script.name == "launcher.py":
                main_script.unlink()
            if spec_file.exists():
                spec_file.unlink()
    
    def create_deb_structure(self):
        """Створення структури DEB пакету"""
        print("📦 Створення структури DEB пакету...")
        
        # Основна структура пакету
        pkg_dir = self.build_dir / f"{self.app_name}_{self.app_version}"
        
        # Директорії пакету
        debian_dir = pkg_dir / "DEBIAN"
        usr_dir = pkg_dir / "usr"
        bin_dir = usr_dir / "bin"
        share_dir = usr_dir / "share"
        app_dir = share_dir / self.app_name
        applications_dir = share_dir / "applications"
        icons_dir = share_dir / "pixmaps"
        
        # Створення всіх директорій
        for directory in [debian_dir, bin_dir, app_dir, applications_dir, icons_dir]:
            directory.mkdir(parents=True, exist_ok=True)
        
        return pkg_dir, debian_dir, app_dir, bin_dir, applications_dir, icons_dir
    
    def copy_application_files(self, app_dir):
        """Копіювання файлів програми"""
        print("📋 Копіювання файлів програми...")
        
        # Копіювання виконуваного файлу та залежностей
        dist_app_dir = self.source_dir / "dist" / self.app_name
        if dist_app_dir.exists():
            for item in dist_app_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, app_dir)
                elif item.is_dir():
                    shutil.copytree(item, app_dir / item.name, dirs_exist_ok=True)
        
        # Копіювання додаткових ресурсів
        for resource in ["templates", "static"]:
            src_path = self.source_dir / resource
            if src_path.exists():
                dst_path = app_dir / resource
                if src_path.is_file():
                    shutil.copy2(src_path, dst_path)
                else:
                    shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        
        print("✅ Файли програми скопійовано")
    
    def create_launcher_script(self, bin_dir, app_dir):
        """Створення скрипту запуску"""
        print("🚀 Створення скрипту запуску...")
        
        launcher_script = f'''#!/bin/bash
# QQQ-CRAFT Launcher Startup Script

# Встановлення змінних середовища
export QQQ_CRAFT_HOME="/usr/share/{self.app_name}"
export PATH="$QQQ_CRAFT_HOME:$PATH"

# Перехід до директорії програми
cd "$QQQ_CRAFT_HOME"

# Запуск програми
exec "./{self.app_name}" "$@"
'''
        
        script_path = bin_dir / self.app_name
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(launcher_script)
        
        # Надання прав на виконання
        script_path.chmod(0o755)
        
        print("✅ Скрипт запуску створено")
    
    def create_desktop_entry(self, applications_dir):
        """Створення .desktop файлу"""
        print("🖥️ Створення desktop файлу...")
        
        desktop_content = f'''[Desktop Entry]
Version=1.0
Type=Application
Name=QQQ-CRAFT Launcher
Name[uk]=QQQ-CRAFT Лаунчер
Comment=Minecraft Launcher for QQQ-CRAFT Server
Comment[uk]=Лаунчер Minecraft для сервера QQQ-CRAFT
GenericName=Game Launcher
GenericName[uk]=Ігровий лаунчер
Exec={self.app_name}
Icon={self.app_name}
Terminal=false
StartupNotify=true
Categories=Game;ActionGame;
Keywords=minecraft;game;launcher;qqq;craft;
StartupWMClass=QQQ-CRAFT-Launcher
'''
        
        desktop_file = applications_dir / f"{self.app_name}.desktop"
        with open(desktop_file, 'w', encoding='utf-8') as f:
            f.write(desktop_content)
        
        desktop_file.chmod(0o644)
        print("✅ Desktop файл створено")
    
    def create_icon(self, icons_dir):
        """Створення іконки (placeholder)"""
        print("🎨 Створення іконки...")
        
        # Створення простої SVG іконки як заглушка
        svg_content = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="64" height="64" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
    <rect width="64" height="64" fill="#4CAF50" rx="8"/>
    <text x="32" y="40" font-family="Arial, sans-serif" font-size="24" 
          font-weight="bold" text-anchor="middle" fill="white">Q</text>
</svg>'''
        
        icon_file = icons_dir / f"{self.app_name}.svg"
        with open(icon_file, 'w', encoding='utf-8') as f:
            f.write(svg_content)
        
        print("✅ Іконка створена (використайте власну для кращого вигляду)")
    
    def create_control_file(self, debian_dir):
        """Створення control файлу"""
        print("📝 Створення control файлу...")
        
        # Розрахунок розміру пакету
        pkg_size = self.calculate_package_size()
        
        control_content = f'''Package: {self.app_name}
Version: {self.app_version}
Section: games
Priority: optional
Architecture: amd64
Depends: python3 (>= 3.8), python3-tk, python3-pip, libwebkit2gtk-4.0-37
Maintainer: {self.app_maintainer}
Homepage: {self.app_homepage}
Installed-Size: {pkg_size}
Description: {self.app_description}
 QQQ-CRAFT Launcher - офіційний лаунчер для сервера QQQ-CRAFT.
 .
 Особливості:
  * Автоматичне встановлення модпаків
  * Підтримка Fabric та Forge
  * Інтуїтивний веб-інтерфейс
  * Автоматичні оновлення
  * Підтримка різних версій Minecraft
'''
        
        control_file = debian_dir / "control"
        with open(control_file, 'w', encoding='utf-8') as f:
            f.write(control_content)
        
        print("✅ Control файл створено")
    
    def create_postinst_script(self, debian_dir):
        """Створення post-installation скрипту"""
        print("⚙️ Створення post-install скрипту...")
        
        postinst_content = f'''#!/bin/bash
set -e

# Оновлення desktop database
if command -v update-desktop-database > /dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications
fi

# Оновлення mime database
if command -v update-mime-database > /dev/null 2>&1; then
    update-mime-database /usr/share/mime
fi

# Встановлення Python залежностей
if command -v pip3 > /dev/null 2>&1; then
    pip3 install --user flask requests minecraft-launcher-lib packaging webview websockets markdown
fi

echo "QQQ-CRAFT Launcher встановлено успішно!"
echo "Запустіть через меню програм або командою: {self.app_name}"

exit 0
'''
        
        postinst_file = debian_dir / "postinst"
        with open(postinst_file, 'w', encoding='utf-8') as f:
            f.write(postinst_content)
        
        postinst_file.chmod(0o755)
        print("✅ Post-install скрипт створено")
    
    def create_prerm_script(self, debian_dir):
        """Створення pre-removal скрипту"""
        print("🗑️ Створення pre-removal скрипту...")
        
        prerm_content = '''#!/bin/bash
set -e

echo "Видалення QQQ-CRAFT Launcher..."

# Зупинка процесів лаунчера
pkill -f "qqq-craft-launcher" 2>/dev/null || true

exit 0
'''
        
        prerm_file = debian_dir / "prerm"
        with open(prerm_file, 'w', encoding='utf-8') as f:
            f.write(prerm_content)
        
        prerm_file.chmod(0o755)
        print("✅ Pre-removal скрипт створено")
    
    def calculate_package_size(self):
        """Розрахунок розміру пакету в KB"""
        total_size = 0
        
        # Розрахунок розміру dist директорії
        dist_dir = self.source_dir / "dist" / self.app_name
        if dist_dir.exists():
            for file_path in dist_dir.rglob('*'):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
        
        return max(1024, total_size // 1024)  # Мінімум 1MB
    
    def build_deb_package(self, pkg_dir):
        """Збірка DEB пакету"""
        print("🔧 Збірка DEB пакету...")
        
        deb_filename = f"{self.app_name}_{self.app_version}_amd64.deb"
        deb_path = self.dist_dir / deb_filename
        
        try:
            # Встановлення правильних прав доступу
            for root, dirs, files in os.walk(pkg_dir):
                for directory in dirs:
                    os.chmod(os.path.join(root, directory), 0o755)
                for file in files:
                    file_path = os.path.join(root, file)
                    if file in ['postinst', 'prerm', 'postrm', 'preinst']:
                        os.chmod(file_path, 0o755)
                    else:
                        os.chmod(file_path, 0o644)
            
            # Збірка пакету
            subprocess.run([
                "dpkg-deb", 
                "--build", 
                "--root-owner-group",
                str(pkg_dir), 
                str(deb_path)
            ], check=True)
            
            print(f"✅ DEB пакет створено: {deb_path}")
            return deb_path
            
        except subprocess.CalledProcessError as e:
            print(f"❌ Помилка збірки DEB пакету: {e}")
            return None
    
    def validate_package(self, deb_path):
        """Валідація створеного пакету"""
        print("🔍 Валідація пакету...")
        
        try:
            # Перевірка структури пакету
            result = subprocess.run([
                "dpkg-deb", "--contents", str(deb_path)
            ], capture_output=True, text=True, check=True)
            
            print("📋 Вміст пакету:")
            print(result.stdout)
            
            # Перевірка метаданих
            result = subprocess.run([
                "dpkg-deb", "--info", str(deb_path)
            ], capture_output=True, text=True, check=True)
            
            print("ℹ️ Інформація про пакет:")
            print(result.stdout)
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"❌ Помилка валідації: {e}")
            return False
    
    def cleanup(self):
        """Очищення тимчасових файлів"""
        print("🧹 Очищення тимчасових файлів...")
        
        # Видалення build директорії
        if self.build_dir.exists():
            shutil.rmtree(self.build_dir)
        
        # Очищення PyInstaller файлів
        for path in self.source_dir.glob("*.spec"):
            path.unlink()
        
        build_dir = self.source_dir / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir)
        
        print("✅ Очищення завершено")
    
    def build(self):
        """Основний метод збірки"""
        print("🚀 Початок збірки DEB пакету QQQ-CRAFT Launcher")
        print(f"📦 Версія: {self.app_version}")
        print("=" * 50)
        
        try:
            # Перевірка залежностей
            if not self.check_dependencies():
                return False
            
            # Підготовка директорій
            self.prepare_build_dirs()
            
            # Створення виконуваного файлу
            if not self.create_executable():
                return False
            
            # Створення структури пакету
            pkg_dir, debian_dir, app_dir, bin_dir, applications_dir, icons_dir = self.create_deb_structure()
            
            # Копіювання файлів
            self.copy_application_files(app_dir)
            self.create_launcher_script(bin_dir, app_dir)
            self.create_desktop_entry(applications_dir)
            self.create_icon(icons_dir)
            
            # Створення метаданих пакету
            self.create_control_file(debian_dir)
            self.create_postinst_script(debian_dir)
            self.create_prerm_script(debian_dir)
            
            # Збірка пакету
            deb_path = self.build_deb_package(pkg_dir)
            if not deb_path:
                return False
            
            # Валідація
            if not self.validate_package(deb_path):
                return False
            
            # Очищення
            self.cleanup()
            
            print("=" * 50)
            print("🎉 Збірка завершена успішно!")
            print(f"📦 Пакет збережено: {deb_path}")
            print(f"📏 Розмір файлу: {deb_path.stat().st_size / 1024 / 1024:.1f} MB")
            print()
            print("📋 Для встановлення виконайте:")
            print(f"   sudo dpkg -i {deb_path}")
            print("   sudo apt-get install -f  # якщо виникнуть проблеми з залежностями")
            print()
            print("🚀 Для запуску після встановлення:")
            print(f"   {self.app_name}")
            
            return True
            
        except Exception as e:
            print(f"❌ Критична помилка: {e}")
            return False


def main():
    """Головна функція"""
    builder = DebPackageBuilder()
    
    try:
        success = builder.build()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Збірка перервана користувачем")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Неочікувана помилка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()