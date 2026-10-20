# 🚀 Indo Commerce & Telegram Bot - Render Deployment Tutorial

This comprehensive tutorial guides you through deploying your **Indo Commerce Inventory & Telegram Bot** to [Render](https://render.com).

The project is pre-configured to run **both** the **FastAPI Web Admin Panel** and the **aiogram 3 Telegram Bot concurrently** inside a single Render service, while maintaining full compatibility with Render's dynamic port binding, zero-downtime health checks, and managed PostgreSQL databases.

---

## 📋 Table of Contents
1. [Key Features of the Render Configuration](#1-key-features-of-the-render-configuration)
2. [Prerequisites](#2-prerequisites)
3. [Step 1: Push the Code to GitHub](#step-1-push-the-code-to-github)
4. [Step 2: Deployment Method A — Render Blueprint (Recommended, 1-Click)](#step-2-deployment-method-a--render-blueprint-recommended-1-click)
5. [Step 3: Deployment Method B — Manual Web Service Setup](#step-3-deployment-method-b--manual-web-service-setup)
6. [Step 4: Database Persistence (PostgreSQL vs SQLite)](#step-4-database-persistence-postgresql-vs-sqlite)
7. [Step 5: Verifying Your Deployment](#step-5-verifying-your-deployment)
8. [Step 6: Keeping Free Tier Active 24/7](#step-6-keeping-free-tier-active-247)
9. [Troubleshooting & FAQ](#troubleshooting--faq)

---

## 1. Key Features of the Render Configuration

- **Concurrent Execution**: Both FastAPI (Web Admin) and aiogram 3 (Telegram customer bot) run together inside [main.py](main.py).
- **Dynamic Port & Host Binding**: Automatically binds to host `0.0.0.0` and Render's dynamically assigned `$PORT`.
- **Automatic Database Initialization & Seeding**: On startup, tables and default admin credentials (`admin` / `AdminPass@2026` or your custom password) are auto-provisioned if missing.
- **Render PostgreSQL Auto-Conversion**: Automatically transforms Render's `postgres://` or `postgresql://` connection strings into `postgresql+asyncpg://` without manual URL editing.
- **Dedicated Health Checks**: Lightweight `/healthz` and `/health` endpoints return instant HTTP 200 responses to satisfy Render's health monitoring.
- **Secret Protection**: Pre-configured `.gitignore` prevents sensitive `.env` files, SQLite databases, and virtual environments from leaking to GitHub.

---

## 2. Prerequisites

Before beginning, prepare:
1. A **[GitHub](https://github.com/)** account.
2. A **[Render](https://render.com/)** account (Free tier is sufficient).
3. A **Telegram Bot Token** obtained from [@BotFather](https://t.me/BotFather).
4. Your **Telegram Numeric User ID** obtained from [@userinfobot](https://t.me/userinfobot) (e.g. `5232576810`).

---

## Step 1: Push the Code to GitHub

Open **PowerShell** or terminal in the project directory:
```powershell
cd "c:\Users\kesha\Downloads\Telegram Desktop\indo_selling_bot"
```

1. **Initialize Git & Verify Status**:
   ```powershell
   git init
   git status
   ```
   *(Notice that `.env`, `.venv`, and `indo_selling.db` are automatically ignored thanks to `.gitignore`)*.

2. **Stage and Commit**:
   ```powershell
   git add .
   git commit -m "feat: configure project for Render cloud deployment"
   ```

3. **Link to your GitHub Repository**:
   Create a new **Private** repository on [GitHub](https://github.com/new) (e.g., `indo-selling-bot`), then push:
   ```powershell
   git branch -M main
   git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/indo-selling-bot.git
   git push -u origin main
   ```

---

## Step 2: Deployment Method A — Render Blueprint (Recommended, 1-Click)

The repository includes a ready-to-use `render.yaml` file that automatically provisions **both** a free managed PostgreSQL database and the web service with all environment variables mapped.

1. Log in to [Render Dashboard](https://dashboard.render.com).
2. Click **Blueprints** on the top menu (or **New +** > **Blueprint**).
3. Connect your GitHub repository (`indo-selling-bot`).
4. Render will parse `render.yaml` and display:
   - **Service**: `indo-selling-bot` (Web Service)
   - **Database**: `indo-selling-db` (PostgreSQL Free Instance)
5. Fill in the prompted secret environment variables:
   - `TELEGRAM_BOT_TOKEN`: Your token from `@BotFather`.
   - `TELEGRAM_ADMIN_CHAT_ID`: Your numeric Telegram ID.
   - `TELEGRAM_ADMIN_USER_ID`: Your numeric Telegram ID.
   - `DEFAULT_ADMIN_PASSWORD`: A secure password for the web dashboard.
6. Click **Apply**.
7. Render will create the database, build the Python package, seed the initial data, and start your bot and admin panel automatically!

---

## Step 3: Deployment Method B — Manual Web Service Setup

If you prefer setting up the service manually through the Render UI:

1. In Render Dashboard, click **New +** > **Web Service**.
2. Connect your GitHub repository (`indo-selling-bot`).
3. Fill in the service configuration:

| Field | Value |
|---|---|
| **Name** | `indo-selling-bot` |
| **Region** | Singapore or Frankfurt (choose nearest to your users) |
| **Branch** | `main` |
| **Root Directory** | *(Leave blank)* |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Instance Type** | `Free` |

4. Scroll down to **Advanced** and set:
   - **Health Check Path**: `/healthz`
   - **Auto-Deploy**: `Yes`

5. Add **Environment Variables** under the **Environment** tab:

| Key | Value | Description |
|---|---|---|
| `PYTHON_VERSION` | `3.12.0` | Recommended runtime version |
| `TELEGRAM_BOT_TOKEN` | `123456789:ABCdefGh...` | Token from @BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | `5232576810` | Your Telegram User ID |
| `TELEGRAM_ADMIN_USER_ID` | `5232576810` | Your Telegram User ID |
| `DEFAULT_ADMIN_USERNAME`| `admin` | Web panel username |
| `DEFAULT_ADMIN_PASSWORD`| `YourStrongPassword@2026` | Web panel password |
| `SECRET_KEY` | *(Click "Generate" or enter 64-char string)* | Session encryption key |
| `PAYMENT_UPI_ID` | `paytm.s1x87m2@pty` | Your UPI ID |
| `DEFAULT_UPI_NAME` | `Legend Indo Official` | Your UPI display name |
| `DATABASE_URL` | *(Optional, see Step 4)* | Managed PostgreSQL connection string |

6. Click **Create Web Service**.

---

## Step 4: Database Persistence (PostgreSQL vs SQLite)

> [!WARNING]
> On Render's **Free Tier**, web service local disks are **ephemeral**. If the service spins down from inactivity or restarts, any local SQLite file (`indo_selling.db`) will be reset!

### Recommended: Free Render Managed PostgreSQL (Zero Data Loss)
1. On Render, click **New +** > **PostgreSQL**.
2. Name it `indo-selling-db`, select the `Free` plan, and click **Create Database**.
3. Once created, copy the **Internal Database URL** (e.g. `postgres://indo_user:...@dpg-xxx-a/indo_selling`).
4. Go to your Web Service > **Environment** tab.
5. Add/Update:
   ```env
   DATABASE_URL = postgres://indo_user:...@dpg-xxx-a/indo_selling
   ```
6. The app's `config.py` automatically translates this to `postgresql+asyncpg://` so PostgreSQL works seamlessly!

---

## Step 5: Verifying Your Deployment

Once Render finishes deploying (Status turns **Live** with a green dot):

### 1. Test Health Check
In your browser, visit:
```
https://<your-service-name>.onrender.com/healthz
```
You will receive:
```json
{"status": "healthy", "service": "Indo Commerce & Inventory System", "version": "2.0.0"}
```

### 2. Log in to the Web Admin Panel
Navigate to:
```
https://<your-service-name>.onrender.com/login
```
- **Username**: `admin` (or what you set in `DEFAULT_ADMIN_USERNAME`)
- **Password**: Your configured password

### 3. Test the Telegram Bot
1. Open Telegram and search for your bot username.
2. Send `/start`.
3. The bot will welcome you with the interactive customer menu (Catalog, Buy Indo, Wallet Balance, Support).
4. If your Telegram ID matches `TELEGRAM_ADMIN_USER_ID`, the bot will automatically show you admin management commands.

---

## Step 6: Keeping Free Tier Active 24/7

Render's Free Tier web services spin down after 15 minutes of inactivity if they do not receive HTTP traffic. While spinning down, the Telegram bot polling will also sleep until an incoming HTTP request wakes the web server.

To keep your bot running 24/7 for free:
1. Create a free account on [UptimeRobot](https://uptimerobot.com/) or [cron-job.org](https://cron-job.org/).
2. Add a new **HTTP(s) Monitor**:
   - **URL**: `https://<your-service-name>.onrender.com/healthz`
   - **Monitoring Interval**: Every 5 or 10 minutes
3. This sends a tiny ping to `/healthz` every 10 minutes, keeping your Render container continuously awake and your Telegram bot polling 24/7 without interruption.

---

## Troubleshooting & FAQ

### Q1: Render build fails with "Could not find a version that satisfies the requirement..."
- **Fix**: Verify `PYTHON_VERSION` is set to `3.12.0` in your Render Environment variables.

### Q2: Telegram bot gives `TelegramConflictError: terminated by other getUpdates request`
- **Cause**: The bot token is running in two places at the same time (e.g. on your local computer and on Render).
- **Fix**: Stop the local instance on your PC (press `Ctrl + C` in your terminal). Only one polling instance can be active per bot token.

### Q3: How do I change payment accounts or UPI IDs?
- You can either update the `PAYMENT_UPI_ID` environment variable in Render, or log into the Web Admin Panel, go to **Store Settings** (`/settings`), and edit payment channels directly in the UI.

### Q4: How do I backup or export my data?
- In the Web Admin Panel, go to **Inventory** > click **Export CSV** or **Orders** > **Export CSV** to download complete records anytime.
