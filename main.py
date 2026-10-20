"""
Render & Production launcher for Indo Commerce & Inventory System.
Concurrently executes both the FastAPI Web Admin Panel and aiogram 3 Telegram Bot.
"""
import sys
import asyncio
from pathlib import Path

# Ensure workspace root and indo_selling_bot package directory are on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
pkg_dir = BASE_DIR / "indo_selling_bot"
if pkg_dir.exists() and str(pkg_dir) not in sys.path:
    sys.path.insert(0, str(pkg_dir))

from indo_selling_bot.run_all import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[*] Application shutdown complete.")
