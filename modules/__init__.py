"""
QQQ-CRAFT LAUNCHER MODULES
Ініціалізаційний файл для модулів лаунчера
"""

from .data_manager import (
    PathManager,
    DataManager,
    Config,
    Validator
)

from .download_manager import ModpacksManager

from .launch_manager import (
    AuthManager,
    MinecraftLauncher,
    GameProfileManager
)

__version__ = "1.0.26.0"
__all__ = [
    "PathManager",
    "DataManager", 
    "Config",
    "Validator",
    "ModpacksManager",
    "AuthManager",
    "MinecraftLauncher",
    "GameProfileManager"
]