"""
Database seeder script:
Initializes tables, provisions initial Argon2-hashed admin credentials,
sample categories, Day 1 to Day 10 inventory listings, and payment settings.
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

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select
from indo_selling_bot.config import settings
from indo_selling_bot.database import init_db, AsyncSessionLocal
from indo_selling_bot.models.user import User
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.setting import StoreSetting
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.security.hashing import hash_password
from indo_selling_bot.services.payment_service import DEFAULT_PAYMENT_CHANNELS

async def seed_all() -> None:
    print("[*] Initializing database schema...")
    await init_db()

    async with AsyncSessionLocal() as db:
        # 1. Seed Admin User
        admin_stmt = select(User).where(User.username == settings.DEFAULT_ADMIN_USERNAME)
        existing_admin = (await db.execute(admin_stmt)).scalar_one_or_none()
        if not existing_admin:
            print(f"[+] Creating default admin account: '{settings.DEFAULT_ADMIN_USERNAME}'")
            admin_user = User(
                username=settings.DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
                email="admin@indo-commerce.local",
                role="superadmin",
                is_active=True,
                created_at=datetime.now(timezone.utc)
            )
            db.add(admin_user)
            await db.flush()
        else:
            print(f"[*] Admin account '{settings.DEFAULT_ADMIN_USERNAME}' already exists.")

        # 2. Seed Default Settings
        settings_to_seed = [
            (
                "day_groups",
                ", ".join(settings.DEFAULT_DAY_GROUPS),
                "Configured inventory day groups (Day 1 - Day 10)"
            ),
            (
                "currency",
                settings.DEFAULT_CURRENCY,
                "Store operating currency"
            ),
            (
                "stock_alert_threshold",
                str(settings.DEFAULT_STOCK_ALERT_THRESHOLD),
                "Low stock alert threshold"
            ),
            (
                "payment_methods",
                json.dumps(DEFAULT_PAYMENT_CHANNELS, indent=2),
                "Configured payment channels and accounts"
            )
        ]
        for key, val, desc in settings_to_seed:
            stmt = select(StoreSetting).where(StoreSetting.key == key)
            existing_s = (await db.execute(stmt)).scalar_one_or_none()
            if not existing_s:
                s = StoreSetting(key=key, value=val, description=desc)
                db.add(s)

        # 3. Seed Default Categories
        sample_cats = [
            ("Software Licenses", "software-licenses", "Authentic software and digital tool licenses", "🔑", 1),
            ("Cloud Services", "cloud-services", "Cloud hosting credits and server vouchers", "☁️", 2),
            ("Digital Merchandise", "digital-merchandise", "Verified digital tokens and vouchers", "🎁", 3),
            ("Enterprise Solutions", "enterprise-solutions", "Commercial workflow automation and tools", "🏢", 4)
        ]
        cat_map = {}
        for name, slug, desc, icon, sort_order in sample_cats:
            stmt = select(Category).where(Category.slug == slug)
            existing_c = (await db.execute(stmt)).scalar_one_or_none()
            if not existing_c:
                print(f"[+] Creating category: {name}")
                c = Category(
                    name=name,
                    slug=slug,
                    description=desc,
                    icon=icon,
                    sort_order=sort_order,
                    is_active=True
                )
                db.add(c)
                await db.flush()
                cat_map[slug] = c
            else:
                cat_map[slug] = existing_c

        # 4. Seed Sample Inventory Items with Day 1 - Day 10 Groupings
        sample_items = [
            (
                "LIC-WIN-D1",
                cat_map.get("software-licenses").id if cat_map.get("software-licenses") else 1,
                "Windows Pro 2026 Genuine Digital Key",
                "Day 1",
                450000.0,
                15,
                5,
                "WIN-PRO-2026-ABCD-9988-1122",
                "Authentic single-device license key with lifetime activation."
            ),
            (
                "LIC-OFF-D2",
                cat_map.get("software-licenses").id if cat_map.get("software-licenses") else 1,
                "Office Pro Plus 2026 Lifetime License",
                "Day 2",
                375000.0,
                8,
                4,
                "OFF-PLUS-2026-X7Y8-ZZ99-3344",
                "Official office productivity suite activation token."
            ),
            (
                "CLOUD-AWS-D3",
                cat_map.get("cloud-services").id if cat_map.get("cloud-services") else 2,
                "AWS Cloud $50 Promotional Credit Coupon",
                "Day 3",
                250000.0,
                12,
                3,
                "AWS-PROMO-50USD-CODE-776655",
                "Promotional credit coupon for compute and database instances."
            ),
            (
                "CLOUD-DO-D5",
                cat_map.get("cloud-services").id if cat_map.get("cloud-services") else 2,
                "DigitalOcean $100 Infrastructure Credit",
                "Day 5",
                400000.0,
                3,  # Low stock test
                5,
                "DO-CREDIT-100USD-VOUCHER-5544",
                "Infrastructure credit code for verified cloud hosting."
            ),
            (
                "DIGI-GAME-D7",
                cat_map.get("digital-merchandise").id if cat_map.get("digital-merchandise") else 3,
                "Steam Global Wallet $20 Digital Card",
                "Day 7",
                320000.0,
                20,
                5,
                "STEAM-WALLET-20USD-ALPHA-BETA",
                "Official Steam digital wallet gift card code."
            ),
            (
                "ENT-API-D10",
                cat_map.get("enterprise-solutions").id if cat_map.get("enterprise-solutions") else 4,
                "Enterprise API Gateway Dedicated Pass (30 Days)",
                "Day 10",
                1500000.0,
                5,
                2,
                "API-GW-DEDICATED-KEY-ENT-9900",
                "High-throughput verified API access key for enterprise workloads."
            )
        ]

        for sku, cat_id, title, day_group, price, qty, thresh, payload, desc in sample_items:
            stmt = select(InventoryItem).where(InventoryItem.sku == sku)
            existing_i = (await db.execute(stmt)).scalar_one_or_none()
            if not existing_i:
                print(f"[+] Seeding item: {sku} ({day_group})")
                item = InventoryItem(
                    sku=sku,
                    category_id=cat_id,
                    title=title,
                    day_group=day_group,
                    price=price,
                    currency="IDR",
                    quantity=qty,
                    stock_alert_threshold=thresh,
                    status="active",
                    fulfillment_type="digital_asset",
                    fulfillment_payload=payload,
                    internal_notes=f"Initial seeded stock for {day_group}",
                    description=desc,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(item)
                await db.flush()

                # Add initial audit log
                log = InventoryChangeLog(
                    item_id=item.id,
                    item_sku=item.sku,
                    item_title=item.title,
                    action="CREATE",
                    admin_user="SEED_SCRIPT",
                    new_value=f"Initial stock: {qty}, DayGroup: {day_group}",
                    notes="System initial database seed",
                    created_at=datetime.now(timezone.utc)
                )
                db.add(log)

        # 5. Log audit action
        audit = AuditLog(
            action="SYSTEM_INITIALIZE",
            performed_by="SYSTEM",
            details="Database schema initialized and seeded with baseline data.",
            created_at=datetime.now(timezone.utc)
        )
        db.add(audit)

        await db.commit()
        print("[OK] Database seeding completed successfully!")

if __name__ == "__main__":
    asyncio.run(seed_all())
