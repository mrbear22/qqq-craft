"""
МОДУЛЬ МЕНЕДЖЕРА ЗАВАНТАЖЕННЯ
Містить класи для завантаження модпаків та лоадерів Minecraft
"""

import time
import hashlib
import logging
import threading
import concurrent.futures
from pathlib import Path
from typing import Dict, Callable, Optional

import requests
import minecraft_launcher_lib

from .data_manager import PathManager, DataManager, Config


class ModpacksManager:
    """Менеджер для завантаження та встановлення модпаків"""
    
    def __init__(self, path_manager: PathManager, base_url: str):
        self.path_manager = path_manager
        self.data_manager = DataManager(path_manager)
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()
        self.total = 0
        self.done = 0
        self.done_lock = threading.Lock()
        self.base_url = base_url.rstrip('/')
    
    def md5(self, path: Path) -> Optional[str]:
        """
        Обчислює MD5 хеш файлу
        
        Args:
            path: Шлях до файлу
            
        Returns:
            str або None: MD5 хеш або None якщо помилка
        """
        try:
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return None
    
    def get_dir_size(self, dir_path: Path) -> int:
        """
        Обчислює розмір папки
        
        Args:
            dir_path: Шлях до папки
            
        Returns:
            int: Розмір у байтах
        """
        if not dir_path.exists():
            return 0
        return sum(f.stat().st_size for f in dir_path.rglob('*') if f.is_file())
    
    def collect_files(self, items: list, path: str = '') -> Dict:
        """
        Рекурсивно збирає всі файли з дерева папок
        
        Args:
            items: Список елементів (файлів та папок)
            path: Поточний шлях
            
        Returns:
            Dict: Словник з файлами {шлях: інформація}
        """
        files = {}
        for item in items:
            current_path = f"{path}/{item['name']}" if path else item['name']
            if item['type'] == 'file':
                files[current_path] = item
            elif 'children' in item:
                files.update(self.collect_files(item['children'], current_path))
        return files
    
    def download_file(self, path: str, info: Dict, callback: Optional[Callable] = None):
        """
        Завантажує окремий файл
        
        Args:
            path: Відносний шлях до файлу
            info: Інформація про файл (розмір, хеш, тощо)
            callback: Функція зворотного виклику для прогресу
        """
        try:
            local_path = self.target_dir / path

            if local_path.exists():
                if info.get('sync', False):
                    if (local_path.stat().st_size == info['size'] and 
                        self.md5(local_path) == info['checksum']):
                        self._update_progress(info['size'], path, 'skipped', callback)
                        return
                else:
                    self._update_progress(info['size'], path, 'skipped', callback)
                    return
            
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_url = f"{self.base_url}/public/{self.target + '/' if self.target else ''}{info['url']}"

            response = self.session.get(file_url, timeout=30)
            response.raise_for_status()

            local_path.write_bytes(response.content)
            self._update_progress(info['size'], path, 'downloaded', callback)
                
        except Exception as e:
            self.logger.error(f"Download error for {path}: {e}")
            if callback:
                callback(0, path, 'error', f"Помилка завантаження {path}: {e}")
            raise
    
    def _update_progress(self, size: int, path: str, status: str, callback: Optional[Callable]):
        """Оновлює прогрес завантаження"""
        with self.done_lock:
            self.done += size
            current_progress = (self.done / self.total * 100) if self.total > 0 else 0
        
        if callback:
            callback(current_progress, path, status)
    
    def process_files(self, items: list, path: str = '') -> Dict:
        """
        Обробляє список файлів та папок, визначає які потрібно завантажити
        
        Args:
            items: Список елементів для обробки
            path: Поточний шлях
            
        Returns:
            Dict: Словник файлів які потрібно завантажити
        """
        files_to_download = {}
        
        for item in items:
            current_path = f"{path}/{item['name']}" if path else item['name']
            
            if item['type'] == 'dir':
                local_dir = self.target_dir / current_path

                if (local_dir.exists() and 
                    self.get_dir_size(local_dir) == item['size']):

                    for child in item.get('children', []):
                        if child['type'] == 'file':
                            with self.done_lock:
                                self.done += child['size']
                    continue

                if 'children' in item:
                    child_files = self.process_files(item['children'], current_path)
                    files_to_download.update(child_files)
            else:
                files_to_download[current_path] = item
        
        return files_to_download
    
    def cleanup_sync_files(self, server_files: Dict):
        """
        Видаляє файли які більше не потрібні (для синхронізації)
        
        Args:
            server_files: Словник файлів з сервера
        """
        if not self.target_dir.exists():
            return
        
        sync_files = {path for path, info in server_files.items() if info.get('sync', False)}
        
        for local_file in self.target_dir.rglob('*'):
            if local_file.is_file():
                rel_path = str(local_file.relative_to(self.target_dir)).replace('\\', '/')
                
                if rel_path not in server_files:
                    parent_dir = '/'.join(rel_path.split('/')[:-1])
                    if any(sync_file.startswith(parent_dir) for sync_file in sync_files):
                        try:
                            local_file.unlink()
                            self.logger.info(f"Removed obsolete file: {rel_path}")
                        except Exception as e:
                            self.logger.error(f"Failed to remove {rel_path}: {e}")
    
    def check_modpack_exists(self, modpack: str) -> bool:
        """
        Перевіряє чи існує модпак на сервері
        
        Args:
            modpack: Назва модпака
            
        Returns:
            bool: True якщо модпак існує
        """
        try:
            url = f"{self.base_url}/index.php?modpack={modpack}"
            response = self.session.get(url, timeout=10)
            data = response.json()
            return data.get('status') == 'ok'
        except Exception as e:
            self.logger.error(f"Modpack check failed for {modpack}: {e}")
            return False
    
    def install_modpack(self, modpack: str = '', target_dir: str = 'game', 
                       callback: Optional[Callable] = None, config = None) -> bool:
        """
        Встановлює модпак
        
        Args:
            modpack: Назва модпака (порожня для базового)
            target_dir: Папка для встановлення
            callback: Функція зворотного виклику для прогресу
            
        Returns:
            bool: True якщо встановлення успішне
        """
        try:
            self.target_dir = Path(target_dir)
            self.target_dir.mkdir(parents=True, exist_ok=True)

            url = f"{self.base_url}/index.php"
            if modpack:
                url += f"?modpack={modpack}"
            
            if callback:
                callback(0, '', 'start', f"Отримання інформації про модпак")
            
            response = self.session.get(url, timeout=30)
            data = response.json()
            
            if data.get('status') != 'ok':
                error_msg = data.get('message', 'Невідома помилка')
                if callback:
                    callback(0, '', 'error', error_msg)
                return False

            self.total = data['total_size']
            self.target = data.get('target', '')
            self.base_url = data.get('base_url', self.base_url)
            self.done = 0
            
            if callback:
                callback(0, '', 'start', f"Аналіз файлів")

            if not config.skipsync:
                all_files = self.collect_files(data['files'])
                self.cleanup_sync_files(all_files)

            files_to_process = self.process_files(data['files'])
            
            if not files_to_process:
                if callback:
                    callback(100, '', 'complete', "Всі файли вже актуальні")
                return True
            
            if callback:
                callback(0, '', 'start', f"Завантаження {len(files_to_process)} файлів...")

            max_workers = min(10, len(files_to_process))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {
                    executor.submit(self.download_file, path, info, callback): path 
                    for path, info in files_to_process.items()
                }
                
                for future in concurrent.futures.as_completed(future_to_file):
                    path = future_to_file[future]
                    try:
                        future.result()
                    except Exception as exc:
                        self.logger.error(f'Файл {path} згенерував виняток: {exc}')
                        if callback:
                            callback(0, path, 'error', f"Помилка з файлом {path}")
            
            if callback:
                callback(100, '', 'complete', "Встановлення завершено")
            return True
            
        except Exception as e:
            self.logger.error(f"Modpack installation failed: {e}")
            if callback:
                callback(0, '', 'error', f"Помилка встановлення: {e}")
            return False

    def install_loader(self, loader_type: str, version: str, target_dir: str, 
                      callback: Optional[Callable] = None) -> bool:
        """
        Встановлює лоадер Minecraft (Fabric, Forge, Vanilla)
        
        Args:
            loader_type: Тип лоадера ("fabric", "forge", "vanilla")
            version: Версія Minecraft
            target_dir: Папка для встановлення
            callback: Функція зворотного виклику для прогресу
            
        Returns:
            bool: True якщо встановлення успішне
        """
        try:
            install_dir = Path(target_dir)
            install_dir.mkdir(parents=True, exist_ok=True)

            last_percent = -1
            last_time = time.time()
            max_progress = [0]
            
            def set_progress(current: int):
                nonlocal last_percent, last_time
                if max_progress[0] > 0:
                    percent = (current / max_progress[0]) * 100
                    current_time = time.time()
                    if int(percent) != last_percent and current_time - last_time > 0.5:
                        if callback:
                            callback(percent, f"Файл {current}/{max_progress[0]}", 'downloaded')
                        last_percent, last_time = int(percent), current_time
            
            def set_max(max_val: int):
                max_progress[0] = max_val
            
            def set_status(text: str):
                if callback:
                    callback(0, text, 'status')
            
            ml_callback = {
                "setProgress": set_progress,
                "setMax": set_max,
                "setStatus": set_status
            }
            
            if callback:
                callback(0, '', 'start', f"Встановлення {loader_type} {version}...")

            if loader_type == "fabric":
                minecraft_launcher_lib.fabric.install_fabric(
                    version, str(install_dir), callback=ml_callback
                )
            elif loader_type == "forge":
                forge_version = minecraft_launcher_lib.forge.find_forge_version(version)
                if not forge_version:
                    raise ValueError(f"Forge version not found for Minecraft {version}")
                minecraft_launcher_lib.forge.install_forge_version(
                    forge_version, str(install_dir), callback=ml_callback
                )
            elif loader_type == "vanilla":
                minecraft_launcher_lib.install.install_minecraft_version(
                    version, str(install_dir), callback=ml_callback
                )
            else:
                raise ValueError(f"Невідомий тип лоадера: {loader_type}")
            
            if callback:
                callback(100, '', 'complete', f"Встановлення {loader_type} завершено")
            return True
            
        except Exception as e:
            self.logger.error(f"Loader installation failed: {e}")
            if callback:
                callback(0, '', 'error', f"Помилка встановлення {loader_type}: {e}")
            return False
    
    def get_available_versions(self, loader_type: str) -> list:
        """
        Отримує список доступних версій для лоадера
        
        Args:
            loader_type: Тип лоадера
            
        Returns:
            list: Список версій
        """
        try:
            if loader_type == "fabric":
                return minecraft_launcher_lib.fabric.get_all_minecraft_versions()
            elif loader_type == "forge":
                return minecraft_launcher_lib.forge.list_forge_versions()
            elif loader_type == "vanilla":
                return minecraft_launcher_lib.utils.get_minecraft_versions()
            else:
                return []
        except Exception as e:
            self.logger.error(f"Failed to get versions for {loader_type}: {e}")
            return []