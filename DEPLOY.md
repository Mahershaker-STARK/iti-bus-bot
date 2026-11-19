# Deployment Guide — ITI Bus Bot

This guide walks you through creating the Telegram bot, preparing the code, and deploying it for free on Render.com with an automatic keep-alive scheduler.

---

## 1. Create the Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send the command `/newbot`.
3. Follow the prompts:
   - **Name**: Choose a friendly display name (e.g. `ITI Bus Bot`).
   - **Username**: Must end in `bot` (e.g. `iti_bus_bot`).
4. BotFather will reply with your **BOT_TOKEN** — a string like `123456789:AAFxxxxxxxxxxxxxxxxxxxxxxx`.
5. Copy and save this token securely. You will add it as an environment variable during deployment.

---

## 2. Get Your Telegram User ID (for ADMIN_ID)

You need your numeric Telegram user ID so the bot knows who the admin is.

**Method A — @userinfobot (easiest):**
1. Open Telegram and search for **@userinfobot**.
2. Send it any message (e.g. `/start`).
3. It will reply with your **Id** (a plain integer such as `987654321`).

**Method B — from bot logs:**
1. Start the bot locally (`python bot.py`) and send any message to your bot.
2. Look at the console output — incoming `Update` objects include `message.from_user.id`.

Save this integer as `ADMIN_ID`.

---

## 3. Prepare the Code

### File structure

```
iti-bus-bot/
├── bot.py
├── database.py
├── iti_api.py
└── requirements.txt
```

### Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
gh repo create iti-bus-bot --public --source=. --remote=origin --push
```

---

## 4. Deploy on Render (Free Tier)

### 4.1 Connect your repo

1. Go to [https://render.com](https://render.com) and sign in.
2. Click **New +** → **Web Service**.
3. Connect your GitHub account if prompted, then select the `iti-bus-bot` repository (`https://github.com/Mahershaker-STARK/iti-bus-bot`).

### 4.2 Configure the service

| Setting | Value |
|---|---|
| **Environment** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python bot.py` |
| **Instance Type** | Free |

### 4.3 Set environment variables

In the **Environment** section of your Render service, add the following key-value pairs:

| Key | Value |
|---|---|
| `BOT_TOKEN` | Your Telegram bot token |
| `ADMIN_ID` | Your Telegram user ID (integer) |
| `PORT` | `10000` |
| `DB_PATH` | `bot_data.db` |
| `ITI_URL` | `https://schedule.iti.gov.eg` |

Click **Save Changes**, then **Deploy**.

---

## 5. Keep-Alive with cron-job.org

### 5.1 Create an account

1. Go to [https://cron-job.org](https://cron-job.org) and sign up for a free account.

### 5.2 Create the cron job

1. Click **CREATE CRONJOB**.
2. Fill in the details:
   - **Title**: `ITI Bus Bot Keep-Alive`
   - **URL**: `https://<your-render-service>.onrender.com/health`
     *(Replace `<your-render-service>` with the subdomain shown in your Render dashboard.)*
   - **Execution schedule**: Every **10 minutes**
   - **Request method**: `GET`
3. Click **CREATE**.

---

## 6. First Use (Multi-User Flow)

After the service is running:

1. **Admin setup**: Send `/start` to the bot from your Telegram account (the one whose ID matches `ADMIN_ID`).
2. **Approve users**: Open `/admin` to see pending registrations and approve them.
3. **User flow** — each approved user should run these commands in order:
   - `/login <email> <password>` — authenticate with the ITI scheduling portal
   - `/primary` — set their primary bus preference
   - `/backup` — set a fallback bus preference
   - `/auto` — enable automatic seat booking
