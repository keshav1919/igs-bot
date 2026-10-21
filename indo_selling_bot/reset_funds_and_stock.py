"""
Utility to reset all user funds to 0 and remove all stock.
"""
import sqlite3
import os
from pathlib import Path

def reset():
    base_dir = Path(__file__).resolve().parent
    stock_path = base_dir / "stock.txt"
    with open(stock_path, "w", encoding="utf-8") as f:
        f.write("")
    print(f"Cleared stock at {stock_path}")

    # Reset databases
    for db_path in [base_dir / "indo_selling.db", base_dir.parent / "indo_selling.db"]:
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cur.fetchall()]
            if "wallets" in tables:
                cur.execute("UPDATE wallets SET balance = 0;")
                print(f"Reset wallets in {db_path} to 0")
            if "inventory_items" in tables:
                cur.execute("DELETE FROM inventory_items;")
                print(f"Cleared inventory_items in {db_path}")
            conn.commit()
            conn.close()

if __name__ == "__main__":
    reset()
