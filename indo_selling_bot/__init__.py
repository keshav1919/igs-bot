"""
Indo Commerce - Telegram Inventory Bot & Admin Panel.
High-performance inventory and order management system with aiogram 3 & FastAPI.
"""
import sys
from pathlib import Path

# Ensure package directory is self-referential and resolvable
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR.parent))
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

__version__ = "2.0.0"
