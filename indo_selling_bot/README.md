# Indo Commerce - Inventory & Order Management Bot with Premium Dark Web Admin Panel

A high-performance Python system featuring an **aiogram 3** Telegram customer bot and a separate **FastAPI** premium dark responsive web administration console. Built with SQLAlchemy 2 (async), SQLite (WAL mode optimized for Windows), Argon2id password security, and automated CSRF protection.

---

## 🛠️ Tech Stack

- **Python 3.12+** (Compatible with Python 3.12, 3.13, and 3.14)
- **aiogram 3.10+**: Router-based Telegram customer bot with inline category browsing, order creation, and customer isolation
- **FastAPI**: Asynchronous web framework powering the administrative dashboard and REST APIs
- **SQLAlchemy 2.0 (Async)** + **aiosqlite**: Modern ORM with connection pooling, transactional locking, and SQLite WAL pragma optimization
- **Argon2id (`argon2-cffi`)**: Next-generation memory-hard password hashing for administrator accounts
- **HTML5, Vanilla CSS & Modern JavaScript**: Pure zero-CDN premium dark responsive dashboard
- **Jinja2**: Server-side template rendering with custom currency formatting filters
- **pytest & pytest-asyncio**: Comprehensive automated test suite (**44 test cases, 100% passing**)

---

## 📁 System Architecture

```
indo_selling_bot/
├── config.py                 # Configuration loader from .env with token validator & masking
├── database.py               # Async engine, session factory, pragmas (WAL mode)
├── seed_data.py              # Baseline seeder (admin, categories, Day 1-10 inventory)
├── run_admin.py              # Standalone FastAPI Admin server runner
├── run_bot.py                # Standalone aiogram 3 bot runner with graceful shutdown
├── run_all.py                # Concurrent launcher (Admin Web + Telegram Bot)
├── models/
│   ├── user.py              # Admin account with Argon2id hash & role
│   ├── category.py          # Catalog categories
│   ├── inventory.py         # InventoryItem & InventoryChangeLog with timestamp age calculation
│   ├── order.py             # Order & OrderItem (Verified payment workflow)
│   ├── support.py           # Customer Support Tickets
│   ├── setting.py           # Dynamic store settings (Day groups, payments)
│   └── audit.py             # Administrative audit log
├── security/
│   ├── hashing.py           # Argon2id password hasher & verifier
│   ├── auth.py              # Tamper-proof HMAC signed session tokens & revocation
│   ├── csrf.py              # Session-bound CSRF token generator & validator
│   └── rate_limiter.py      # Sliding window rate limiter for login protection
├── services/
│   ├── inventory_service.py # Inventory CRUD, timestamp Day 1-10 grouping, pause/activate, restock
│   ├── order_service.py     # Order creation, customer isolation, authorized verification
│   ├── payment_service.py   # Payment channels & customer instructions
│   ├── csv_service.py       # Inventory CSV import/export, orders CSV, audit CSV
│   └── support_service.py   # Customer ticket submission & admin reply forwarding
├── web/
│   ├── app.py               # FastAPI application setup, middleware, custom 404 & 500 handlers
│   ├── dependencies.py      # Auth cookie validation, CSRF checks, rate limiting
│   ├── static/
│   │   ├── css/dark_theme.css # Premium dark responsive dashboard styles
│   │   └── js/admin.js        # Mobile toggle, restock modals, toast alerts
│   ├── templates/            # Jinja2 templates (dashboard, inventory, orders, 404, 500, etc.)
│   └── routers/              # Modular FastAPI route handlers
├── bot/
│   ├── bot_instance.py      # aiogram 3 Bot & Dispatcher setup with token validator
│   ├── keyboards.py         # Dynamic inline and reply keyboards
│   ├── notifications.py     # Proactive customer notification dispatcher
│   └── handlers/            # Start, catalog, orders, and support handlers
└── tests/                   # Full pytest suite (44 tests, 100% passing)
```

---

## 🚀 Key Features

### 1. Day-Wise Inventory Management (Phase 2 Enhanced)
- **Legitimate Timestamp-Based Age Grouping**: Automatically calculates age in days (`age_days`) and maps to `Day 1` through `Day 10` (or `Day 10+`) from creation timestamps (`created_at`) rather than manual arbitrary labels.
- **Configurable Categories**: Organize goods with custom emoji icons, slugs, and sort orders.
- **Stock Tracking & Alerts**: Real-time stock counts with configurable warning thresholds (e.g., alert when stock &le; 5 units).
- **Admin Actions**: One-click pause/activate listing toggle, quick restock modal with auto-activation of out-of-stock items, and complete audit logging.
- **Search, Filters & Pagination**: Multi-field search (SKU, title, description), category filtering, day-group filtering (Day 1 through Day 10), status filtering, and low-stock toggles.
- **CSV Import & Export**: One-click inventory export and robust CSV import with automatic category resolution and line-by-line error reporting. Includes a downloadable sample CSV template.
- **Complete Inventory Change History**: Every creation, price change, stock deduction, restock, and status adjustment is permanently logged in `inventory_change_logs`.

### 2. Telegram Bot (@aiogram 3)
- **Interactive Catalog**: Browse categories and filter listings by Day 1 - Day 10 batches.
- **Token Security & Validation**: Token format is verified upon startup; token secrets are masked (`123456****xyz`) in logs to prevent secret leakage.
- **Resilient Startup**: Network interruptions (`TelegramNetworkError`) and missing token errors are safely handled with helpful instructions without crashing the admin panel.
- **Listing Details**: Shows stock availability badge, price in store currency, SKU, and detailed description.
- **Customer Isolation & Privacy**: Customer orders are isolated by Telegram ID; users can only view and manage their own orders.
- **Order Placement**: Automatically reserves inventory stock atomically to prevent overselling.
- **Customer Support Integration**: In-bot `/support` command allows customers to submit inquiries directly to the web admin desk.

### 3. Verified Payment Workflow (Security & Compliance)
- **Strict Verification Policy**: Customer-uploaded screenshots are **never** treated as automatic payment confirmations.
- **Transaction Reference Logging**: Customers submit textual reference IDs (Bank transfer memo, TxID hash, or sender name), which are logged and validated against double-spending.
- **Authorized Administrative Verification**: Deliverable payloads and service assets are **only** released after an authorized administrator verifies the transaction against genuine banking or blockchain records.
- **Double Fulfillment Prevention**: Idempotency checks and database constraints prevent replay attacks and duplicate releases.
- **Automatic Telegram Delivery**: Once verified, deliverables are automatically formatted and messaged to the customer's Telegram chat.

### 4. Premium Dark Responsive Admin Panel
- **Security**: Argon2id password hashing, HTTP-only secure session cookies, signed session payloads, CSRF token validation on all mutating forms, and IP rate limiting against brute force.
- **Dark Theme UI**: Custom zero-CDN dark theme (`#0b0f19` / `#161f30`) with neon emerald and cyan accents.
- **Custom Error Pages**: Dedicated dark-themed 404 (Resource Not Found) and 500 (Internal Server Error) templates for consistent user experience.
- **Real-Time KPIs**: Total Revenue, Total Orders, Pending Payment Reviews, Low-Stock Count, and Inventory Valuation.
- **Customer Support Desk**: Admins can inspect customer tickets and type replies, which are delivered straight to the customer's Telegram chat.
- **Audit Logs**: Tamper-evident log of all admin operations (logins, payment approvals, rejections, CSV imports).

---

## 💻 Windows Setup & Quick Start

### Prerequisites
- Windows 10/11 or Windows Server
- Python 3.12+ installed and added to `PATH`

### 1. Installation
Open PowerShell or Command Prompt in the project folder:
```powershell
# Navigate to the workspace
cd "c:\Users\raipb\Downloads\Telegram Desktop\PARENT-AUTOMATION-v2"

# 1-Click Launch with interactive menu
.\start_local.bat
```

### 2. Manual Commands
If running commands manually step-by-step:

#### Create Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

#### Install Requirements
```powershell
python -m pip install -r indo_selling_bot\requirements.txt
```

#### Configure Environment (`.env`)
```powershell
copy indo_selling_bot\.env.example indo_selling_bot\.env
```
Edit `indo_selling_bot\.env` to add your Telegram Bot Token:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz
ADMIN_HOST=127.0.0.1
ADMIN_PORT=8000
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=AdminPass@2026
```

#### Initialize & Seed Database
```powershell
python -m indo_selling_bot.seed_data
```
Default credentials:
- **URL**: `http://127.0.0.1:8000/login`
- **Username**: `admin`
- **Password**: `AdminPass@2026`

#### Start Services
- **Web Admin Panel**:
  ```powershell
  python -m indo_selling_bot.run_admin
  ```
  Access at: **http://127.0.0.1:8000**
- **Telegram Bot** (Separate Terminal):
  ```powershell
  python -m indo_selling_bot.run_bot
  ```
- **Both Concurrently**:
  ```powershell
  python -m indo_selling_bot.run_all
  ```

---

## 🧪 Automated Testing

Execute the complete test suite with verbose output:
```powershell
python -m pytest indo_selling_bot\tests -v
```
All **44 test cases** pass with 100% success rate across security, inventory aging (Day 1 - Day 10+), order workflows, CSV import/export, bot token security, and dark web admin routes.

---

## ⚙️ External Integrations Requiring Configuration

1. **Telegram Bot Token**:
   - Obtain a bot token by messaging `@BotFather` on Telegram.
   - Set `TELEGRAM_BOT_TOKEN` in `indo_selling_bot\.env`.
2. **Official Payment Channels**:
   - Navigate to **Store Settings** in the Admin Panel (`/settings`) or update `.env`.
   - Update official bank account numbers (e.g., BCA / Mandiri) and crypto wallet addresses (e.g., USDT TRC-20) shown to customers.
   - Note: Automated payment gateways (e.g., Midtrans / Xendit / BTCPay) require genuine merchant credentials and are kept manual pending verification for safety.
