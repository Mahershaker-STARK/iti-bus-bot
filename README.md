---
title: ITI Bus Bot
emoji: 🚌
colorFrom: red
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# ITI Bus Auto-Booker Telegram Bot

Multi-User Telegram Bot in Python for reserving seats on the ITI Bus Management System (https://schedule.iti.gov.eg) via its REST API, ready for deployment on Hugging Face Spaces or Render.

## Architecture

- **`bot.py`**: Full asynchronous Telegram bot using `python-telegram-bot` v21 + aiohttp HTTP healthcheck server.
- **`iti_api.py`**: Fully asynchronous client for the ITI REST API (`POST /auth/login`, `GET /trips/available`, `GET /students/routes/{id}/stops`, `POST /bookings`).
- **`database.py`**: SQLite database with thread-safe WAL mode managing user sessions, tokens, routes, and auto-booking state.
- **`requirements.txt`**: Pinned production dependencies.
- **`Dockerfile`**: Docker container configuration.
- **`DEPLOY.md`**: Step-by-step cloud deployment and keep-alive guide.

## Repository
https://github.com/Mahershaker-STARK/iti-bus-bot
