"""
МОДУЛЬ ДЛЯ РОБОТИ З ДАНИМИ
Містить класи для управління конфігурацією, валідації та роботи з файлами
"""

import os
import re
import json
import platform
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

@dataclass
class Config:
    """Клас конфігурації лаунчера"""
    nickname: str = ""
    loader: str = "fabric"
    ram: str = "4G"
    window_size: str = "1280x720"
    multiplayer: bool = True
    console: bool = False
    skipsync: bool = False
    fullscreen: bool = False


class PathManager:
    """Менеджер шляхів та папок"""
    
    def __init__(self):
        try:
            self.home = Path.home()
            self.is_windows = platform.system() == "Windows"
            
            if self.is_windows:
                self.base_dir = self.home / "AppData" / "Local" / "Programs" / "qqq-craft"
            else:  # Linux
                self.base_dir = self.home / ".local" / "share" / "qqq-craft"
            
            self.base_dir.mkdir(parents=True, exist_ok=True)
            (self.base_dir / "static").mkdir(exist_ok=True)
            
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка ініціалізації PathManager",
                f"Не вдалося створити необхідні папки: {e}"
            )
            raise
    
    def get_install_dir(self, loader: str) -> Path:
        """Повертає шлях до папки встановлення для конкретного лоадера"""
        return self.base_dir / "instances" / loader
    
    def get_static_file(self, filename: str) -> Path:
        """Повертає шлях до файлу в папці static"""
        return self.base_dir / "static" / filename


class DataManager:
    """Менеджер для збереження та завантаження даних"""
    
    def __init__(self, path_manager: PathManager):
        self.path_manager = path_manager
        self.logger = logging.getLogger(__name__)
    
    def save(self, data: Dict, filename: str) -> bool:
        """
        Зберігає дані у JSON файл
        
        Args:
            data: Словник з даними для збереження
            filename: Ім'я файлу
            
        Returns:
            bool: True якщо збереження успішне, False якщо помилка
        """
        try:
            file_path = self.path_manager.get_static_file(filename)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save {filename}: {e}")
            ErrorHandler.show_error_dialog(
                f"Помилка збереження файлу {filename}",
                str(e)
            )
            return False
    
    def load(self, filename: str) -> Dict:
        """
        Завантажує дані з JSON файлу
        
        Args:
            filename: Ім'я файлу
            
        Returns:
            Dict: Словник з даними або порожній словник якщо помилка
        """
        try:
            file_path = self.path_manager.get_static_file(filename)
            if file_path.exists() and file_path.stat().st_size > 0:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
                    
        except Exception as e:
            self.logger.error(f"Failed to load {filename}: {e}")
            ErrorHandler.show_error_dialog(
                f"Помилка завантаження файлу {filename}",
                str(e)
            )
            
        return {}
    
    def delete_file(self, filename: str) -> bool:
        """
        Видаляє файл
        
        Args:
            filename: Ім'я файлу
            
        Returns:
            bool: True якщо видалення успішне, False якщо помилка
        """
        try:
            file_path = self.path_manager.get_static_file(filename)
            if file_path.exists():
                file_path.unlink()
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to delete {filename}: {e}")
            return False


class Validator:
    """Клас для валідації даних"""
    
    @staticmethod
    def validate_nickname(nickname: str) -> Tuple[bool, str]:
        """
        Валідує нікнейм користувача
        
        Args:
            nickname: Нікнейм для валідації
            
        Returns:
            Tuple[bool, str]: (True/False, повідомлення про помилку)
        """
        try:
            if not nickname or not nickname.strip():
                return False, "Помилка валідації", None
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка валідації нікнейму",
                str(e)
            )
            return False, "Помилка валідації"
    @staticmethod
    def validate_ram_size(ram: str) -> bool:
        """
        Перевіряє чи є розмір RAM валідним
        
        Args:
            ram: Розмір RAM (наприклад "4G")
            
        Returns:
            bool: True якщо валідний, False якщо ні
        """
        try:
            valid_ram_sizes = ['1G', '2G', '4G', '8G', '16G', '32G']
            return ram in valid_ram_sizes
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка валідації оперативної пам'яті",
                str(e)
            )
            return False, "Помилка валідації"
            
    @staticmethod
    def validate_window_size(size: str) -> bool:
        """
        Перевіряє чи є розмір вікна валідним
        
        Args:
            size: Розмір вікна у форматі "1280x720"
            
        Returns:
            bool: True якщо валідний, False якщо ні
        """
        try:
            if 'x' not in size:
                return False
            
            width, height = size.split('x')
            width, height = int(width), int(height)
            
            return (640 <= width <= 3840) and (480 <= height <= 2160)
            
        except (ValueError, AttributeError):
            return False, "Нікнейм не може бути порожнім"
            
            nickname = nickname.strip()
            if not (3 <= len(nickname) <= 16):
                return False, "Нікнейм повинен бути від 3 до 16 символів"
            
            if not re.fullmatch(r'[A-Za-z0-9_-]+', nickname):
                return False, "Нікнейм може містити лише англійські літери, цифри, дефіс або підкреслення"
            
            forbidden = {'admin', 'moderator', 'staff', 'banned', 'owner'}
            if nickname.lower() in forbidden:
                return False, "Нікнейм містить заборонені слова"
            
            return True, "OK"
            
        except Exception as e:
            
            ErrorHandler.show_error_dialog(
                "Помилка валідації нікнейму",
                str(e)
            )
            return False, "Помилка валідації"
    
    @staticmethod
    def validate_config(data: Dict) -> Tuple[bool, str, Optional[Config]]:
        """
        Валідує конфігураційні дані
        
        Args:
            data: Словник з конфігураційними даними
            
        Returns:
            Tuple[bool, str, Optional[Config]]: (True/False, повідомлення, об'єкт Config або None)
        """
        try:
            # Валідуємо лоадер
            loader = data.get('loader')
            if not loader:
                loader = 'vanilla'
            
            # Валідуємо RAM
            ram = data.get('ram', '4G')
            valid_ram = ['2G', '4G', '8G', '16G']
            if ram not in valid_ram:
                ram = '4G'
                
            # Валідуємо розмір вікна
            window_size = data.get('windowSize', '1280x720')
            valid_sizes = ['1280x720', '1920x1080', '1366x768', '1600x900']
            if window_size not in valid_sizes:
                window_size = '1280x720'
            
            # Створюємо об'єкт конфігурації
            config = Config(
                nickname=data.get('nickname', '').strip(),
                loader=loader,
                ram=ram,
                window_size=window_size,
                multiplayer=bool(data.get('multiplayer', True)),
                console=bool(data.get('console', False)),
                skipsync=bool(data.get('skipsync', False)),
                fullscreen=bool(data.get('fullscreen', False)),
            )
            
            return True, "OK", config
            
        except Exception as e:
            ErrorHandler.show_error_dialog(
                "Помилка валідації конфігурації",
                str(e)
            )
            return False,