"""
runner.py — Standalone GitHub Actions runner for ITI Bus Auto-Booker.

Execution modes:
1. Dispatch Mode:
   Triggered via repository_dispatch (telegram_check / telegram_book) or when EVENT_PAYLOAD is set.
   Reads user details & action from EVENT_PAYLOAD and performs instant /check or /book,
   sending immediate Modern Standard Arabic notifications to the user via Telegram Bot API.

2. Scheduled Mode:
   Triggered via GitHub Actions cron schedule (every 5 min during Cairo booking hours Sun-Thu).
   Queries active users from PythonAnywhere / external API, ACTIVE_USERS env var,
   local SQLite database, or single-user environment variables.
   Uses a single shared trip fetch (anti-ban / rate-limit protection), and automatically
   books available seats for active users matching their primary or backup routes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

import aiohttp
import pytz

import database as db
import iti_api

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("iti_runner")

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "").strip()
RAW_ITI_URL: str = os.environ.get("ITI_URL", "https://schedule.iti.gov.eg/api/v1").rstrip("/")
# Normalize ITI_URL to ensure it targets /api/v1
if not RAW_ITI_URL.endswith("/api/v1"):
    ITI_URL = f"{RAW_ITI_URL}/api/v1"
else:
    ITI_URL = RAW_ITI_URL

CAIRO_TZ = pytz.timezone("Africa/Cairo")


def get_cairo_date_str() -> str:
    """Return today's date in Cairo timezone formatted as YYYY-MM-DD."""
    return datetime.now(CAIRO_TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Direct Telegram Notification Helper
# ---------------------------------------------------------------------------
async def send_telegram(
    session: aiohttp.ClientSession,
    chat_id: int | str | None,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> bool:
    """
    Send a direct Telegram message via Telegram Bot API:
    https://api.telegram.org/bot<BOT_TOKEN>/sendMessage
    """
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN is not configured; skipping Telegram notification.")
        return False
    if not chat_id:
        logger.warning("chat_id is empty or missing; skipping Telegram notification.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.ok:
                logger.info("Telegram notification successfully sent to chat_id %s", chat_id)
                return True
            else:
                body = await resp.text()
                logger.warning("Telegram send failed (HTTP %s): %s", resp.status, body)
                # If HTML parsing failed, retry once without parse_mode
                if parse_mode and "can't parse entities" in body.lower():
                    payload.pop("parse_mode", None)
                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as retry_resp:
                        return retry_resp.ok
                return False
    except Exception as e:
        logger.error("Exception while sending Telegram message to %s: %s", chat_id, e)
        return False


# ---------------------------------------------------------------------------
# ITI API Client & Authentication Helpers
# ---------------------------------------------------------------------------
def build_client(email: str, password: str) -> iti_api.ITIApiClient:
    return iti_api.ITIApiClient(base_url=ITI_URL, email=email, password=password)


async def get_authenticated_client(
    session: aiohttp.ClientSession,
    email: str,
    password: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> tuple[iti_api.ITIApiClient, str | None]:
    """
    Returns an (ITIApiClient, valid_access_token) tuple.
    Handles existing token, refresh token fallback, or fresh login.
    """
    client = build_client(email, password)

    if access_token:
        return client, access_token

    if refresh_token:
        try:
            new_token = await client.refresh(session, refresh_token)
            logger.info("Access token refreshed successfully using refresh token.")
            return client, new_token
        except Exception as e:
            logger.debug("Token refresh failed: %s; falling back to login.", e)

    if email and password:
        try:
            login_data = await client.login(session)
            token = login_data.get("accessToken")
            logger.info("Logged in successfully for %s", email)
            return client, token
        except iti_api.ITIAuthError as e:
            logger.error("ITI authentication failed for %s: %s", email, e)
            return client, None
        except Exception as e:
            logger.error("Unexpected error during login for %s: %s", email, e)
            return client, None

    return client, None


async def fetch_trips_safe(
    session: aiohttp.ClientSession,
    client: iti_api.ITIApiClient,
    access_token: str,
    date_str: str,
) -> tuple[list[dict], str]:
    """
    Fetch available trips for a given date.
    If access token expired (HTTP 401), automatically re-logs in and retries once.
    """
    try:
        trips = await client.get_trips(session, access_token, date_str)
        return trips, access_token
    except iti_api.ITIAuthError:
        logger.info("Access token expired while fetching trips; re-authenticating...")
        login_res = await client.login(session)
        new_token = login_res["accessToken"]
        trips = await client.get_trips(session, new_token, date_str)
        return trips, new_token


# ---------------------------------------------------------------------------
# Backend / Database Sync Helper
# ---------------------------------------------------------------------------
async def notify_backend_booked(
    session: aiohttp.ClientSession,
    chat_id: int | str,
    date_str: str,
    booking_code: str,
) -> None:
    """Notify PythonAnywhere or external API that a user has booked for today."""
    backend_url = os.environ.get("PYTHONANYWHERE_URL") or os.environ.get("API_URL")
    if not backend_url:
        return

    url = f"{backend_url.rstrip('/')}/api/users/{chat_id}/booked"
    token = os.environ.get("PYTHONANYWHERE_TOKEN") or os.environ.get("API_SECRET")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {"date": date_str, "booking_code": booking_code, "booked_today": True}
    try:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            logger.info("Notified backend for user %s booked status (HTTP %s)", chat_id, resp.status)
    except Exception as e:
        logger.debug("Backend notification skipped/failed: %s", e)


# ---------------------------------------------------------------------------
# Active Users Loader (for Scheduled Mode)
# ---------------------------------------------------------------------------
async def load_active_users(session: aiohttp.ClientSession) -> list[dict]:
    """
    Retrieve list of active users to book for.
    Checks:
    1. PythonAnywhere / External REST API endpoints
    2. ACTIVE_USERS JSON environment variable
    3. Local SQLite database (bot_data.db / busauto.db)
    4. Single-user environment variables (ITI_EMAIL, ITI_PASSWORD, etc.)
    """
    # 1. External REST API (e.g. PythonAnywhere)
    pa_base = os.environ.get("PYTHONANYWHERE_URL") or os.environ.get("API_URL") or os.environ.get("USERS_API_URL")
    if pa_base:
        candidate_urls = [
            pa_base if "/api/" in pa_base else f"{pa_base.rstrip('/')}/api/active_users",
            f"{pa_base.rstrip('/')}/api/users/active",
        ]
        headers = {}
        token = os.environ.get("PYTHONANYWHERE_TOKEN") or os.environ.get("API_SECRET")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        for u in candidate_urls:
            try:
                async with session.get(
                    u,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        users = data if isinstance(data, list) else data.get("users", [])
                        if users:
                            logger.info("Loaded %d active users from external API (%s)", len(users), u)
                            return users
            except Exception as e:
                logger.debug("Could not load users from %s: %s", u, e)

    # 2. ACTIVE_USERS JSON env variable
    active_users_raw = os.environ.get("ACTIVE_USERS", "").strip()
    if active_users_raw:
        try:
            parsed = json.loads(active_users_raw)
            if isinstance(parsed, list) and parsed:
                logger.info("Loaded %d active users from ACTIVE_USERS env variable.", len(parsed))
                return parsed
        except Exception as e:
            logger.warning("Failed to parse ACTIVE_USERS JSON: %s", e)

    # 3. Local SQLite database
    db_paths = [
        os.environ.get("DB_PATH", "bot_data.db"),
        "busauto.db",
        "bot_data.db",
    ]
    for p in db_paths:
        if os.path.exists(p):
            try:
                db.init_db(p)
                db_users = db.get_all_active_users()
                if db_users:
                    logger.info("Loaded %d active users from local database (%s)", len(db_users), p)
                    return db_users
            except Exception as e:
                logger.warning("Could not query active users from %s: %s", p, e)

    # 4. Single-user fallback via environment variables / secrets
    email = os.environ.get("ITI_EMAIL")
    password = os.environ.get("ITI_PASSWORD")
    if email and password:
        chat_id_raw = (
            os.environ.get("TELEGRAM_CHAT_ID")
            or os.environ.get("CHAT_ID")
            or os.environ.get("ADMIN_ID", "0")
        )
        chat_id = int(chat_id_raw) if str(chat_id_raw).isdigit() else chat_id_raw
        single_user = {
            "telegram_id": chat_id,
            "chat_id": chat_id,
            "iti_email": email,
            "email": email,
            "iti_password": password,
            "password": password,
            "primary_route_id": os.environ.get("PRIMARY_ROUTE_ID"),
            "primary_stop_id": os.environ.get("PRIMARY_STOP_ID"),
            "primary_stop_name": os.environ.get("PRIMARY_STOP_NAME") or os.environ.get("DROPOFF", "هايبر"),
            "backup_route_id": os.environ.get("BACKUP_ROUTE_ID"),
            "backup_stop_id": os.environ.get("BACKUP_STOP_ID"),
            "backup_stop_name": os.environ.get("BACKUP_STOP_NAME", ""),
            "auto_book": 1,
            "booked_today": 0,
            "is_approved": 1,
        }
        logger.info("Configured single user from ITI_EMAIL env (%s)", email)
        return [single_user]

    return []


# ---------------------------------------------------------------------------
# User Data Enrichment
# ---------------------------------------------------------------------------
def enrich_user(user: dict) -> dict:
    """Enrich a user dictionary with missing fields from DB or environment."""
    enriched = dict(user)
    chat_id = enriched.get("chat_id") or enriched.get("telegram_id")

    # DB lookup
    if chat_id:
        try:
            tid = int(chat_id)
            for path in [os.environ.get("DB_PATH", "bot_data.db"), "busauto.db", "bot_data.db"]:
                if os.path.exists(path):
                    db.init_db(path)
                    db_u = db.get_user(tid)
                    if db_u:
                        for k, v in db_u.items():
                            if v is not None and k not in enriched:
                                enriched[k] = v
                        break
        except Exception as e:
            logger.debug("User enrichment from DB skipped: %s", e)

    # Env vars fallback
    if not enriched.get("email") and not enriched.get("iti_email"):
        if os.environ.get("ITI_EMAIL"):
            enriched["iti_email"] = os.environ["ITI_EMAIL"]
            enriched["email"] = os.environ["ITI_EMAIL"]
    if not enriched.get("password") and not enriched.get("iti_password"):
        if os.environ.get("ITI_PASSWORD"):
            enriched["iti_password"] = os.environ["ITI_PASSWORD"]
            enriched["password"] = os.environ["ITI_PASSWORD"]

    if not enriched.get("primary_route_id") and os.environ.get("PRIMARY_ROUTE_ID"):
        enriched["primary_route_id"] = os.environ["PRIMARY_ROUTE_ID"]
    if not enriched.get("primary_stop_id") and os.environ.get("PRIMARY_STOP_ID"):
        enriched["primary_stop_id"] = os.environ["PRIMARY_STOP_ID"]
    if not enriched.get("primary_stop_name"):
        enriched["primary_stop_name"] = os.environ.get("PRIMARY_STOP_NAME") or os.environ.get("DROPOFF", "هايبر")
    if not enriched.get("backup_route_id") and os.environ.get("BACKUP_ROUTE_ID"):
        enriched["backup_route_id"] = os.environ["BACKUP_ROUTE_ID"]
    if not enriched.get("backup_stop_id") and os.environ.get("BACKUP_STOP_ID"):
        enriched["backup_stop_id"] = os.environ["BACKUP_STOP_ID"]
    if not enriched.get("backup_stop_name") and os.environ.get("BACKUP_STOP_NAME"):
        enriched["backup_stop_name"] = os.environ["BACKUP_STOP_NAME"]

    if not enriched.get("chat_id") and not enriched.get("telegram_id"):
        default_chat = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID") or os.environ.get("ADMIN_ID")
        if default_chat and default_chat != "0":
            enriched["chat_id"] = int(default_chat) if default_chat.isdigit() else default_chat
            enriched["telegram_id"] = enriched["chat_id"]

    return enriched


# ---------------------------------------------------------------------------
# Booking Operation Core
# ---------------------------------------------------------------------------
async def attempt_booking_for_user(
    session: aiohttp.ClientSession,
    user: dict,
    trips: list[dict],
    date_str: str,
    client: iti_api.ITIApiClient | None = None,
    access_token: str | None = None,
    is_dispatch: bool = False,
) -> bool:
    """
    Attempt booking for a single user on primary route first, then backup route.
    Sends Modern Standard Arabic notification on success or error.
    Returns True if successfully booked, False otherwise.
    """
    user = enrich_user(user)
    chat_id = user.get("chat_id") or user.get("telegram_id")
    email = user.get("email") or user.get("iti_email", "")
    password = user.get("password") or user.get("iti_password", "")

    # Collect configured routes to attempt
    routes_to_try: list[tuple[str, int, int, str, str]] = []
    p_rid = user.get("primary_route_id")
    p_sid = user.get("primary_stop_id")
    if p_rid and p_sid:
        routes_to_try.append((
            "primary",
            int(p_rid),
            int(p_sid),
            user.get("primary_route_name", ""),
            user.get("primary_stop_name", ""),
        ))

    b_rid = user.get("backup_route_id")
    b_sid = user.get("backup_stop_id")
    if b_rid and b_sid:
        routes_to_try.append((
            "backup",
            int(b_rid),
            int(b_sid),
            user.get("backup_route_name", ""),
            user.get("backup_stop_name", ""),
        ))

    if not routes_to_try:
        logger.warning("User %s has neither primary nor backup route configured.", chat_id)
        if is_dispatch:
            await send_telegram(
                session,
                chat_id,
                "⚠️ <b>لم يتم تحديد مسار الحافلة أو محطة الركوب.</b>\n"
                "يرجى تحديد الخط الأساسي أو الاحتياطي أولاً عبر أوامر البوت (<code>/primary</code> أو <code>/backup</code>).",
            )
        return False

    # Find candidate trips with available seats > 0
    candidate_trips: list[tuple[str, int, int, str, str, dict]] = []
    for label, route_id, stop_id, route_name, stop_name in routes_to_try:
        for t in trips:
            rid = t.get("routeId")
            seats = t.get("availableSeats", 0)
            if rid == route_id and seats > 0:
                candidate_trips.append((label, route_id, stop_id, route_name, stop_name, t))
                break  # one available trip per route priority is sufficient

    if not candidate_trips:
        logger.info("No seats available on primary or backup route for user %s", chat_id)
        if is_dispatch:
            p_name = user.get("primary_route_name") or f"خط {p_rid}" if p_rid else "غير محدد"
            b_name = user.get("backup_route_name") or f"خط {b_rid}" if b_rid else "غير محدد"
            await send_telegram(
                session,
                chat_id,
                f"ℹ️ <b>لا توجد مقاعد شاغرة حالياً</b> لتاريخ <code>{date_str}</code>:\n\n"
                f"📍 <b>الخط الأساسي:</b> {p_name}\n"
                f"📍 <b>الخط الاحتياطي:</b> {b_name}\n\n"
                f"<i>سيستمر نظام الحجز التلقائي في المتابعة فور توفر أي مقعد.</i>",
            )
        return False

    # Authenticate user if not already provided
    if client is None or access_token is None:
        client, access_token = await get_authenticated_client(
            session, email, password, user.get("access_token"), user.get("refresh_token")
        )

    if not access_token:
        logger.error("Authentication failed during booking for user %s", chat_id)
        if is_dispatch:
            await send_telegram(
                session,
                chat_id,
                f"❌ <b>فشل تسجيل الدخول إلى ITI</b> للحساب: <code>{email}</code>\n"
                f"يرجى التأكد من صحة البريد الإلكتروني وكلمة المرور عبر البوت.",
            )
        return False

    # Execute booking attempts
    for label, route_id, stop_id, route_name, stop_name, trip in candidate_trips:
        trip_id = trip["id"]
        trip_route_name = trip.get("routeName") or route_name or f"خط {route_id}"
        bus_no = trip.get("busNumber") or "-"
        logger.info(
            "Attempting booking for user %s on %s route (%s, trip %s, stop %s)",
            chat_id,
            label,
            trip_route_name,
            trip_id,
            stop_id,
        )

        booking = None
        try:
            booking = await client.book_trip(session, access_token, trip_id, stop_id)
        except iti_api.ITIAuthError:
            logger.info("Token expired during booking; re-logging in...")
            login_res = await client.login(session)
            access_token = login_res["accessToken"]
            try:
                booking = await client.book_trip(session, access_token, trip_id, stop_id)
            except Exception as e:
                logger.error("Booking retry failed: %s", e)
                continue
        except iti_api.ITIBookingError as e:
            logger.warning("ITIBookingError for user %s on %s route: %s", chat_id, label, e)
            continue
        except Exception as e:
            logger.error("Unexpected error booking for user %s: %s", chat_id, e)
            continue

        if booking and booking.get("success"):
            b_code = booking.get("bookingCode") or "مؤكد"
            confirmed_route = booking.get("routeName") or trip_route_name
            confirmed_bus = booking.get("busNumber") or bus_no
            confirmed_stop = booking.get("pickupStopName") or stop_name or "المحطة المحددة"
            departure_time = booking.get("departureTime") or trip.get("departureTime") or "-"
            priority_ar = "الأساسي" if label == "primary" else "الاحتياطي"

            # Direct Modern Standard Arabic Confirmation
            msg = (
                f"🎉 <b>تم تأكيد حجز مقعدك بنجاح!</b> 🚌\n\n"
                f"📌 <b>الخط:</b> {confirmed_route} ({priority_ar})\n"
                f"🎫 <b>كود الحجز:</b> <code>{b_code}</code>\n"
                f"🚌 <b>رقم الحافلة:</b> <code>{confirmed_bus}</code>\n"
                f"🛑 <b>محطة الركوب:</b> {confirmed_stop}\n"
                f"⏰ <b>وقت التحرك:</b> {departure_time}\n"
                f"📅 <b>تاريخ الرحلة:</b> {date_str}\n\n"
                f"نتمنى لك رحلة موفقة وآمنة! ✨"
            )
            await send_telegram(session, chat_id, msg)

            # Update local DB if available
            try:
                if chat_id:
                    db.set_booked_today(int(chat_id), True)
            except Exception:
                pass

            # Notify PythonAnywhere / Backend
            await notify_backend_booked(session, chat_id, date_str, b_code)
            return True

    if is_dispatch:
        await send_telegram(
            session,
            chat_id,
            "⚠️ <b>تعذر إتمام الحجز</b>\n"
            "قد تكون المقاعد المتبقية قد حُجزت للتو، أو تم تسجيل حجز مسبق لهذا الحساب اليوم.",
        )
    return False


# ---------------------------------------------------------------------------
# Dispatch Mode Handlers (/check & /book)
# ---------------------------------------------------------------------------
async def handle_dispatch_check(session: aiohttp.ClientSession, user: dict, date_str: str) -> None:
    """Handle /check request for a single user in dispatch mode."""
    user = enrich_user(user)
    chat_id = user.get("chat_id") or user.get("telegram_id")
    email = user.get("email") or user.get("iti_email", "")
    password = user.get("password") or user.get("iti_password", "")
    token = user.get("access_token")

    if not email or not password:
        await send_telegram(
            session,
            chat_id,
            "❌ <b>بيانات تسجيل الدخول غير متوفرة.</b>\n"
            "يرجى تسجيل الدخول أولاً عبر البوت باستخدام الأمر:\n"
            "<code>/login email password</code>",
        )
        return

    client, active_token = await get_authenticated_client(
        session, email, password, token, user.get("refresh_token")
    )
    if not active_token:
        await send_telegram(
            session,
            chat_id,
            f"❌ <b>فشل تسجيل الدخول إلى ITI</b>\n"
            f"تعذر المصادقة بالبريد: <code>{email}</code>\n"
            f"يرجى التأكد من كلمة المرور.",
        )
        return

    try:
        trips, _ = await fetch_trips_safe(session, client, active_token, date_str)
    except Exception as e:
        logger.error("Failed to fetch trips for check: %s", e)
        await send_telegram(
            session,
            chat_id,
            f"❌ <b>تعذر جلب الرحلات لتاريخ {date_str}</b>\nيرجى المحاولة لاحقاً.",
        )
        return

    if not trips:
        await send_telegram(
            session,
            chat_id,
            f"ℹ️ <b>لا توجد رحلات متاحة لتاريخ اليوم ({date_str}).</b>",
        )
        return

    p_rid = int(user["primary_route_id"]) if user.get("primary_route_id") else None
    b_rid = int(user["backup_route_id"]) if user.get("backup_route_id") else None

    # Filter trips matching user's routes if set
    matched_trips = []
    other_trips = []
    for t in trips:
        rid = t.get("routeId")
        if rid in (p_rid, b_rid):
            matched_trips.append(t)
        else:
            other_trips.append(t)

    display_trips = matched_trips if matched_trips else other_trips[:12]

    lines = [f"🚌 <b>الرحلات المتاحة لتاريخ {date_str}:</b>\n"]
    for t in display_trips:
        r_name = t.get("routeName") or f"خط {t.get('routeId')}"
        bus_no = t.get("busNumber") or "-"
        seats = t.get("availableSeats", 0)
        dep = t.get("departureTime") or "-"
        star = ""
        if p_rid and t.get("routeId") == p_rid:
            star = " ⭐ (خطك الأساسي)"
        elif b_rid and t.get("routeId") == b_rid:
            star = " 🔹 (خطك الاحتياطي)"

        lines.append(
            f"📍 <b>{r_name}</b>{star}\n"
            f"🚌 رقم الحافلة: <code>{bus_no}</code>\n"
            f"💺 المقاعد المتاحة: <b>{seats}</b>\n"
            f"⏰ وقت التحرك: {dep}\n"
            f"─────────────────"
        )

    if matched_trips and all(t.get("availableSeats", 0) == 0 for t in matched_trips):
        lines.append("\n⚠️ <i>تنبيه: لا توجد مقاعد شاغرة حالياً على خطوطك المختارة.</i>")

    await send_telegram(session, chat_id, "\n".join(lines))


async def handle_dispatch_login(session: aiohttp.ClientSession, payload: dict) -> None:
    chat_id = payload.get("chat_id")
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "").strip()
    callback_url = payload.get("callback_url")
    api_secret = payload.get("api_secret")

    if not email or not password:
        await send_telegram(
            session,
            chat_id,
            "⚠️ <b>بيانات الدخول غير مكتملة.</b> يرجى إدخال البريد وكلمة المرور:\n<code>/login email password</code>",
        )
        return

    logger.info("Verifying login credentials on ITI API for: %s", email)
    client = iti_api.ITIApiClient(ITI_URL, email, password)
    try:
        auth = await client.login(session)
        full_name = auth.get("fullName") or email
        logger.info("Login verification SUCCESS for %s (%s)", email, full_name)

        # Call back to PythonAnywhere to save credentials & full name
        if callback_url:
            try:
                save_payload = {
                    "secret": api_secret,
                    "chat_id": chat_id,
                    "email": email,
                    "password": password,
                    "full_name": full_name,
                    "access_token": auth.get("accessToken", ""),
                    "refresh_token": auth.get("refreshToken", ""),
                }
                async with session.post(
                    callback_url,
                    json=save_payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    logger.info("Saved user to PythonAnywhere: HTTP %s", resp.status)
            except Exception as e:
                logger.warning("Failed to save user to callback_url: %s", e)

        msg = (
            f"✅ <b>تم تسجيل الدخول بنجاح وحفظ بياناتك!</b>\n\n"
            f"🎓 مرحباً بك يا <b>{full_name}</b>\n\n"
            f"📍 <b>الخطوة التالية (تحديد خطوط السير):</b>\n"
            f"1. اختر خطك ومحطتك الأساسية عبر <code>/primary</code>\n"
            f"2. اختر خطك الاحتياطي عبر <code>/backup</code>\n\n"
            f"🔍 <i>يمكنك فحص الرحلات عبر <code>/check</code> أو الحجز الفوري عبر <code>/book</code></i>"
        )
        main_keyboard = {
            "keyboard": [
                [{"text": "🚌 الخط الأساسي"}, {"text": "🔄 الخط الاحتياطي"}],
                [{"text": "🔍 فحص الرحلات"}, {"text": "⚡ حجز فوري"}],
                [{"text": "📊 حالتي وإعداداتي"}, {"text": "⚙️ تبديل الحجز التلقائي"}],
                [{"text": "🚪 تسجيل الخروج"}]
            ],
            "resize_keyboard": True,
            "is_persistent": True
        }
        await send_telegram(session, chat_id, msg, reply_markup=main_keyboard)

    except iti_api.ITIAuthError:
        logger.warning("Login verification FAILED for %s (Invalid credentials)", email)
        msg = (
            f"❌ <b>فشل تسجيل الدخول!</b>\n\n"
            f"البريد الإلكتروني أو كلمة المرور غير صحيحة على نظام ITI.\n\n"
            f"يرجى التأكد من البيانات والمحاولة مجدداً عبر:\n"
            f"<code>/login بريدك@example.com كلمة_المرور</code>"
        )
        login_failed_kb = {
            "keyboard": [
                [{"text": "🔑 تسجيل الدخول"}],
                [{"text": "ℹ️ تعليمات الاستخدام"}]
            ],
            "resize_keyboard": True,
            "is_persistent": True
        }
        await send_telegram(session, chat_id, msg, reply_markup=login_failed_kb)
    except Exception as e:
        logger.error("Unexpected error during login verification: %s", e)
        msg = (
            f"⚠️ <b>تعذر التحقق من الدخول حالياً:</b> {e}\n"
            f"يرجى إعادة المحاولة بعد لحظات عبر:\n"
            f"<code>/login بريدك@example.com كلمة_المرور</code>"
        )
        await send_telegram(session, chat_id, msg)


async def run_dispatch_mode(
    session: aiohttp.ClientSession,
    payload: dict,
    action: str,
    date_str: str,
) -> None:
    """Run runner in dispatch mode for a single user request."""
    logger.info("Executing Dispatch Mode. Action: '%s'", action)
    user = enrich_user(payload)

    if action == "login":
        await handle_dispatch_login(session, payload)
        return
    elif action == "check":
        await handle_dispatch_check(session, user, date_str)
    elif action == "book":
        # Fetch trips and attempt booking
        email = user.get("email") or user.get("iti_email", "")
        password = user.get("password") or user.get("iti_password", "")
        token = user.get("access_token")

        client, active_token = await get_authenticated_client(
            session, email, password, token, user.get("refresh_token")
        )
        if not active_token:
            chat_id = user.get("chat_id") or user.get("telegram_id")
            await send_telegram(
                session,
                chat_id,
                f"❌ <b>فشل تسجيل الدخول إلى ITI</b> للحساب <code>{email}</code>.",
            )
            return

        try:
            trips, _ = await fetch_trips_safe(session, client, active_token, date_str)
        except Exception as e:
            logger.error("Failed to fetch trips for dispatch booking: %s", e)
            chat_id = user.get("chat_id") or user.get("telegram_id")
            await send_telegram(
                session,
                chat_id,
                f"❌ <b>تعذر الاستعلام عن الرحلات</b> لتاريخ {date_str}.",
            )
            return

        await attempt_booking_for_user(
            session,
            user,
            trips,
            date_str,
            client=client,
            access_token=active_token,
            is_dispatch=True,
        )
    else:
        logger.error("Unrecognized dispatch action: '%s'", action)


# ---------------------------------------------------------------------------
# Scheduled Mode Handler
# ---------------------------------------------------------------------------
async def run_scheduled_mode(session: aiohttp.ClientSession, date_str: str) -> None:
    """Run runner in scheduled mode across all active users."""
    logger.info("Executing Scheduled Mode for date %s", date_str)
    users = await load_active_users(session)

    if not users:
        logger.info("No active users found. Scheduled run completed.")
        return

    active_to_process = [u for u in users if not u.get("booked_today")]
    logger.info("Total users: %d | Unbooked active users: %d", len(users), len(active_to_process))

    if not active_to_process:
        logger.info("All users have already booked today. Nothing to do.")
        return

    # Anti-ban shared trip fetch: fetch available trips ONCE for all users
    shared_trips: list[dict] | None = None
    for u in active_to_process:
        u_enriched = enrich_user(u)
        email = u_enriched.get("email") or u_enriched.get("iti_email")
        password = u_enriched.get("password") or u_enriched.get("iti_password")
        token = u_enriched.get("access_token")

        if not email or not password:
            continue

        try:
            client, active_token = await get_authenticated_client(
                session, email, password, token, u_enriched.get("refresh_token")
            )
            if active_token:
                shared_trips, _ = await fetch_trips_safe(session, client, active_token, date_str)
                logger.info("Fetched %d available trips using credentials of %s", len(shared_trips), email)
                break
        except Exception as e:
            logger.debug("Failed shared trip fetch with user %s: %s", email, e)

    if not shared_trips:
        logger.warning("Could not retrieve available trips from ITI API; exiting scheduled cycle.")
        return

    # Process each active unbooked user
    booked_count = 0
    for u in active_to_process:
        try:
            success = await attempt_booking_for_user(
                session,
                u,
                shared_trips,
                date_str,
                is_dispatch=False,
            )
            if success:
                booked_count += 1
        except Exception as e:
            logger.exception("Error processing user %s in scheduled run: %s", u.get("telegram_id"), e)

    logger.info("Scheduled run completed. Booked %d seats.", booked_count)


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------
def determine_mode(payload: dict) -> tuple[str, str]:
    """
    Determine execution mode ('dispatch' vs 'scheduled') and action ('check' vs 'book').
    """
    event_name = os.environ.get("EVENT_NAME", "").lower()
    event_action = (os.environ.get("EVENT_ACTION") or "").lower()
    cli_args = [a.lower() for a in sys.argv[1:]]

    # Mode determination
    if "--dispatch" in cli_args or (cli_args and cli_args[0] == "dispatch"):
        mode = "dispatch"
    elif "--scheduled" in cli_args or (cli_args and cli_args[0] == "scheduled"):
        mode = "scheduled"
    elif event_name == "repository_dispatch":
        mode = "dispatch"
    elif payload and any(k in payload for k in ("chat_id", "telegram_id", "action", "command")):
        mode = "dispatch"
    else:
        mode = "scheduled"

    # Action determination
    action = payload.get("action") or payload.get("command") or event_action or ""
    if any("check" in a for a in cli_args):
        action = "check"
    elif any("book" in a for a in cli_args):
        action = "book"

    action_lower = action.lower()
    if "book" in action_lower:
        resolved_action = "book"
    elif "check" in action_lower:
        resolved_action = "check"
    elif "login" in action_lower:
        resolved_action = "login"
    else:
        resolved_action = "check" if mode == "dispatch" else ""

    return mode, resolved_action


async def main() -> None:
    # 1. Parse EVENT_PAYLOAD
    raw_payload = os.environ.get("EVENT_PAYLOAD", "").strip()
    payload: dict[str, Any] = {}
    if raw_payload and raw_payload not in ("null", "None", "{}"):
        try:
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict):
                payload = {}
        except Exception as e:
            logger.warning("Failed to parse EVENT_PAYLOAD JSON: %s", e)

    global BOT_TOKEN
    if not BOT_TOKEN and payload.get("bot_token"):
        BOT_TOKEN = str(payload["bot_token"]).strip()

    # 2. Determine mode & action
    mode, action = determine_mode(payload)
    date_str = payload.get("date") or get_cairo_date_str()

    logger.info("Starting ITI Bus Runner: Mode=%s | Action=%s | Date=%s", mode, action or "None", date_str)

    # 3. Execute
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if mode == "dispatch":
            await run_dispatch_mode(session, payload, action, date_str)
        else:
            await run_scheduled_mode(session, date_str)

    logger.info("ITI Bus Runner finished successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")
    except Exception as e:
        logger.exception("Fatal unhandled error in runner: %s", e)
        sys.exit(1)
