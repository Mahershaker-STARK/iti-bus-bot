# ITI Bus Auto-Booker Telegram Bot

Multi-User Telegram Bot in Python for reserving seats on the ITI Bus Management System (https://schedule.iti.gov.eg) via its REST API, ready for deployment on Render.com free tier.

## Architecture

- **`bot.py`**: Full asynchronous Telegram bot using `python-telegram-bot` v21 + aiohttp HTTP healthcheck server.
- **`iti_api.py`**: Fully asynchronous client for the ITI REST API (`POST /auth/login`, `GET /trips/available`, `GET /students/routes/{id}/stops`, `POST /bookings`).
- **`database.py`**: SQLite database with thread-safe WAL mode managing user sessions, tokens, routes, and auto-booking state.
- **`requirements.txt`**: Pinned production dependencies.
- **`DEPLOY.md`**: Step-by-step Render and cron-job.org guide.

## Repository
https://github.com/Mahershaker-STARK/iti-bus-bot
