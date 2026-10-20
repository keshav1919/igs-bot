"""
Entry point to run FastAPI Web Admin Panel.
"""
import sys
from pathlib import Path

# Bootstrap sys.path to enable 'indo_selling_bot' package imports when run directly from project root
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT.parent))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if "indo_selling_bot" not in sys.modules:
    try:
        import indo_selling_bot
    except ModuleNotFoundError:
        import types
        pkg = types.ModuleType("indo_selling_bot")
        pkg.__path__ = [str(_PROJECT_ROOT)]
        pkg.__file__ = str(_PROJECT_ROOT / "__init__.py")
        sys.modules["indo_selling_bot"] = pkg

import uvicorn
from indo_selling_bot.config import settings

def main():
    print(f"[*] Starting Indo Commerce Admin Panel on http://{settings.ADMIN_HOST}:{settings.ADMIN_PORT}")
    uvicorn.run(
        "indo_selling_bot.web.app:app",
        host=settings.ADMIN_HOST,
        port=settings.ADMIN_PORT,
        reload=False
    )

if __name__ == "__main__":
    main()
