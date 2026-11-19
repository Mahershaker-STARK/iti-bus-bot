"""
bot.py – Full async Telegram bot for ITI Bus Booking
python-telegram-bot >= 20, aiohttp, pytz
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, date, timedelta, timezone

import aiohttp
from aiohttp import web
import pytz
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import database as db
import iti_api

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_ID: int = int(os.environ.get("ADMIN_ID", "0"))
DB_PATH: str = os.environ.get("DB_PATH", "bot_data.db")
PORT: int = int(os.environ.get("PORT", "10000"))
ITI_URL: str = os.environ.get("ITI_URL", "https://schedule.iti.gov.eg")

CAIRO_TZ = pytz.timezone("Africa/Cairo")

# ---------------------------------------------------------------------------
# Arabic text constants
# ---------------------------------------------------------------------------
MSG_WELCOME = (
    "مرحباً بك في بوت حجز حافلات ITI 🚌\n\n"
    "للبدء، قم بتسجيل الدخول بأمر:\n"
    "/login بريدك@example.com كلمة_المرور\n\n"
    "ثم حدد خطك الأساسي عبر:\n"
    "/primary"
)

MSG_LOGIN_USAGE = (
    "⚠️ الاستخدام الصحيح:\n"
    "/login بريدك@example.com كلمة_المرور"
)

MSG_LOGIN_OK = (
    "✅ تم تسجيل الدخول بنجاح!\n"
    "مرحباً {name}\n\n"
    "الآن حدد خطك الأساسي عبر /primary"
)

MSG_LOGIN_FAIL = "❌ فشل تسجيل الدخول. تحقق من البريد الإلكتروني وكلمة المرور."

MSG_PENDING_APPROVAL = (
    "⏳ حسابك في انتظار موافقة الإدارة.\n"
    "سيتم إشعارك فور الموافقة."
)

MSG_ADMIN_NEW_USER = (
    "👤 مستخدم جديد يطلب الموافقة:\n"
    "المعرف: {telegram_id}\n"
    "الاسم: {name}\n"
    "البريد: {email}"
)

MSG_NOT_LOGGED_IN = "❌ لم تقم بتسجيل الدخول بعد. استخدم /login"

MSG_NOT_APPROVED = (
    "⏳ حسابك في انتظار موافقة الإدارة. لا يمكنك استخدام هذه الميزة حتى يتم الموافقة."
)

MSG_SELECT_ROUTE = "🚌 اختر الخط:"

MSG_SELECT_STOP = "🛑 اختر المحطة للخط: {route_name}"

MSG_PRIMARY_SAVED = "✅ تم حفظ الخط الأساسي:\nالخط: {route_name}\nالمحطة: {stop_name}"

MSG_BACKUP_SAVED = "✅ تم حفظ الخط الاحتياطي:\nالخط: {route_name}\nالمحطة: {stop_name}"

MSG_AUTO_ON = "✅ تم تفعيل الحجز التلقائي."
MSG_AUTO_OFF = "❌ تم إيقاف الحجز التلقائي."

MSG_NO_TRIPS = "ℹ️ لا توجد رحلات متاحة اليوم."

MSG_TRIPS_HEADER = "🚌 الرحلات المتاحة اليوم:\n\n"

MSG_TRIP_ITEM = (
    "📍 {route_name}\n"
    "🚌 رقم الحافلة: {bus_number}\n"
    "💺 المقاعد المتاحة: {seats}\n"
    "⏰ وقت المغادرة: {departure}\n"
    "──────────────\n"
)

MSG_STATUS_TEMPLATE = (
    "📊 إعداداتك الحالية:\n\n"
    "الخط الأساسي: {primary_line}\n"
    "محطة الأساسي: {primary_stop}\n\n"
    "الخط الاحتياطي: {backup_line}\n"
    "محطة الاحتياطي: {backup_stop}\n\n"
    "الحجز التلقائي: {auto_book}\n"
    "محجوز اليوم: {booked_today}"
)

MSG_STATUS_YES = "✅ نعم"
MSG_STATUS_NO = "❌ لا"
MSG_STATUS_NOT_SET = "غير محدد"

MSG_LOGGED_OUT = "👋 تم تسجيل الخروج وحذف بياناتك."

MSG_ADMIN_ONLY = "⛔ هذا الأمر للمسؤول فقط."

MSG_NO_USERS = "ℹ️ لا يوجد مستخدمون مسجلون."

MSG_ADMIN_USER_ITEM = (
    "👤 {name} ({telegram_id})\n"
    "البريد: {email}\n"
    "الحالة: {status}"
)

MSG_APPROVED = "✅ تمت الموافقة على المستخدم {telegram_id}."
MSG_BLOCKED = "🚫 تم حظر المستخدم {telegram_id}."
MSG_USER_APPROVED_NOTIFY = (
    "✅ تمت الموافقة على حسابك!\n"
    "يمكنك الآن استخدام البوت. حدد خطك الأساسي عبر /primary"
)
MSG_USER_BLOCKED_NOTIFY = "🚫 تم حظر حسابك من قبل الإدارة."

MSG_BOOKING_SUCCESS = (
    "✅ تم الحجز بنجاح!\n\n"
    "كود الحجز: {booking_code}\n"
    "الخط: {route_name}\n"
    "الحافلة: {bus_number}\n"
    "المحطة: {stop_name}\n"
    "وقت المغادرة: {departure}"
)

MSG_AUTH_ERROR = (
    "❌ انتهت صلاحية جلستك ولم يمكن تجديدها تلقائياً.\n"
    "الرجاء تسجيل الدخول مجدداً عبر /login"
)

MSG_ROUTES_FAIL = "❌ تعذّر جلب قائمة الخطوط. حاول لاحقاً."
MSG_STOPS_FAIL = "❌ تعذّر جلب قائمة المحطات. حاول لاحقاً."
MSG_CHECK_FAIL = "❌ تعذّر جلب الرحلات. حاول لاحقاً."

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_working_window() -> bool:
    """Return True if current Cairo time is within booking window (Sun–Thu 09–16)."""
    now = datetime.now(CAIRO_TZ)
    # weekday(): Monday=0 … Sunday=6
    # Working days Sun(6), Mon(0), Tue(1), Wed(2), Thu(3)
    if now.weekday() not in (0, 1, 2, 3, 6):
        return False
    return 9 <= now.hour < 16


def _build_client(email: str, password: str) -> iti_api.ITIApiClient:
    return iti_api.ITIApiClient(base_url=ITI_URL, email=email, password=password)


async def _ensure_valid_token(
    session: aiohttp.ClientSession,
    user: dict,
) -> str | None:
    """
    Try to get a valid access_token for `user`.
    Returns the token string on success, None on hard failure.
    Persists updated tokens to DB on success.
    """
    client = _build_client(user["iti_email"], user["iti_password"])
    tid = user["telegram_id"]

    # 1. Try current token (caller should handle 401 externally, but we do a
    #    quick refresh attempt here when called from the poller).
    try:
        new_access = await client.refresh(session, user["refresh_token"])
        # find new refresh token: re-login for simplicity if refresh gives only access
        db.set_tokens(tid, new_access, user["refresh_token"])
        return new_access
    except iti_api.ITIAuthError:
        pass

    # 2. Full re-login
    try:
        result = await client.login(session)
        db.set_tokens(tid, result["accessToken"], result["refreshToken"])
        return result["accessToken"]
    except iti_api.ITIAuthError:
        return None


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MSG_WELCOME)


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tid = update.effective_user.id
    args = context.args  # list of words after /login

    if not args or len(args) < 2:
        await update.message.reply_text(MSG_LOGIN_USAGE)
        return

    email = args[0]
    password = args[1]

    # Check DB for existing user
    user = db.get_user(tid)
    if user and not user["is_approved"]:
        await update.message.reply_text(MSG_PENDING_APPROVAL)
        return

    # Attempt login via API
    async with aiohttp.ClientSession() as session:
        client = _build_client(email, password)
        try:
            result = await client.login(session)
        except iti_api.ITIAuthError:
            await update.message.reply_text(MSG_LOGIN_FAIL)
            return

    full_name = result.get("fullName", "")
    access_token = result["accessToken"]
    refresh_token = result["refreshToken"]

    # Determine approval: existing approved users keep it; admin is auto-approved; if ADMIN_ID is 0, auto-approve
    is_approved = 1 if (user and user["is_approved"]) or (ADMIN_ID != 0 and tid == ADMIN_ID) or (ADMIN_ID == 0) else 0

    db.upsert_user(
        tid,
        iti_email=email,
        iti_password=password,
        access_token=access_token,
        refresh_token=refresh_token,
        full_name=full_name,
        is_approved=is_approved,
    )

    if is_approved:
        await update.message.reply_text(MSG_LOGIN_OK.format(name=full_name))
    else:
        # Notify admin
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"admin:approve:{tid}"),
             InlineKeyboardButton("🚫 حظر", callback_data=f"admin:block:{tid}")]
        ])
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=MSG_ADMIN_NEW_USER.format(
                    telegram_id=tid,
                    name=full_name,
                    email=email,
                ),
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Could not notify admin: %s", e)
        await update.message.reply_text(MSG_PENDING_APPROVAL)


async def _send_routes_keyboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: aiohttp.ClientSession,
    access_token: str,
    kind: str,
) -> None:
    """Fetch routes and send inline keyboard."""
    try:
        routes = await iti_api.get_all_routes(session, access_token)
    except Exception as e:
        logger.error("get_all_routes failed: %s", e)
        msg = update.message or update.callback_query.message
        await msg.reply_text(MSG_ROUTES_FAIL)
        return

    if not routes:
        msg = update.message or update.callback_query.message
        await msg.reply_text(MSG_ROUTES_FAIL)
        return

    # Cache route names in user_data
    if "routes_cache" not in context.user_data:
        context.user_data["routes_cache"] = {}
    for r in routes:
        context.user_data["routes_cache"][r["id"]] = r.get("name") or r.get("routeCode") or str(r["id"])

    buttons = []
    row = []
    for r in routes:
        label = r.get("name") or r.get("routeCode") or str(r["id"])
        btn = InlineKeyboardButton(label, callback_data=f"route:{kind}:{r['id']}")
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    kb = InlineKeyboardMarkup(buttons)
    if update.message:
        await update.message.reply_text(MSG_SELECT_ROUTE, reply_markup=kb)
    else:
        await update.callback_query.message.reply_text(MSG_SELECT_ROUTE, reply_markup=kb)


async def _route_command(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    tid = update.effective_user.id
    user = db.get_user(tid)
    if not user:
        await update.message.reply_text(MSG_NOT_LOGGED_IN)
        return
    if not user["is_approved"]:
        await update.message.reply_text(MSG_NOT_APPROVED)
        return

    async with aiohttp.ClientSession() as session:
        await _send_routes_keyboard(update, context, session, user["access_token"], kind)


async def cmd_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _route_command(update, context, "primary")


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _route_command(update, context, "backup")


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tid = update.effective_user.id
    user = db.get_user(tid)
    if not user:
        await update.message.reply_text(MSG_NOT_LOGGED_IN)
        return
    if not user["is_approved"]:
        await update.message.reply_text(MSG_NOT_APPROVED)
        return

    new_val = db.toggle_auto_book(tid)
    await update.message.reply_text(MSG_AUTO_ON if new_val else MSG_AUTO_OFF)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tid = update.effective_user.id
    user = db.get_user(tid)
    if not user:
        await update.message.reply_text(MSG_NOT_LOGGED_IN)
        return
    if not user["is_approved"]:
        await update.message.reply_text(MSG_NOT_APPROVED)
        return

    today_str = date.today().strftime("%Y-%m-%d")
    async with aiohttp.ClientSession() as session:
        client = _build_client(user["iti_email"], user["iti_password"])
        try:
            trips = await client.get_trips(session, user["access_token"], today_str)
        except iti_api.ITIAuthError:
            # Try refresh
            token = await _ensure_valid_token(session, user)
            if token is None:
                await update.message.reply_text(MSG_AUTH_ERROR)
                return
            user = db.get_user(tid)
            try:
                trips = await client.get_trips(session, token, today_str)
            except Exception:
                await update.message.reply_text(MSG_CHECK_FAIL)
                return
        except Exception:
            await update.message.reply_text(MSG_CHECK_FAIL)
            return

    available = [t for t in trips if t.get("availableSeats", 0) > 0]
    if not available:
        await update.message.reply_text(MSG_NO_TRIPS)
        return

    text = MSG_TRIPS_HEADER
    for t in available:
        text += MSG_TRIP_ITEM.format(
            route_name=t.get("routeName", ""),
            bus_number=t.get("busNumber", ""),
            seats=t.get("availableSeats", 0),
            departure=t.get("departureTime", ""),
        )
    await update.message.reply_text(text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tid = update.effective_user.id
    user = db.get_user(tid)
    if not user:
        await update.message.reply_text(MSG_NOT_LOGGED_IN)
        return

    primary_line = user.get("primary_route_name") or (f"خط {user['primary_route_id']}" if user.get("primary_route_id") else MSG_STATUS_NOT_SET)
    primary_stop = user.get("primary_stop_name") or MSG_STATUS_NOT_SET
    backup_line = user.get("backup_route_name") or (f"خط {user['backup_route_id']}" if user.get("backup_route_id") else MSG_STATUS_NOT_SET)
    backup_stop = user.get("backup_stop_name") or MSG_STATUS_NOT_SET
    auto_book = MSG_STATUS_YES if user.get("auto_book") else MSG_STATUS_NO
    booked_today = MSG_STATUS_YES if user.get("booked_today") else MSG_STATUS_NO

    await update.message.reply_text(
        MSG_STATUS_TEMPLATE.format(
            primary_line=primary_line,
            primary_stop=primary_stop,
            backup_line=backup_line,
            backup_stop=backup_stop,
            auto_book=auto_book,
            booked_today=booked_today,
        )
    )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tid = update.effective_user.id
    db.delete_user(tid)
    await update.message.reply_text(MSG_LOGGED_OUT)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tid = update.effective_user.id
    if tid != ADMIN_ID:
        await update.message.reply_text(MSG_ADMIN_ONLY)
        return

    users = db.get_all_users()
    if not users:
        await update.message.reply_text(MSG_NO_USERS)
        return

    for u in users:
        status_txt = "✅ موافق" if u.get("is_approved") else "⏳ انتظار"
        text = MSG_ADMIN_USER_ITEM.format(
            name=u.get("full_name") or "—",
            telegram_id=u["telegram_id"],
            email=u.get("iti_email") or "—",
            status=status_txt,
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ موافقة", callback_data=f"admin:approve:{u['telegram_id']}"),
                InlineKeyboardButton("🚫 حظر", callback_data=f"admin:block:{u['telegram_id']}"),
            ]
        ])
        await update.message.reply_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Callback query handler
# ---------------------------------------------------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    tid = update.effective_user.id

    # --- Admin actions ---
    if data.startswith("admin:"):
        if tid != ADMIN_ID:
            await query.edit_message_text(MSG_ADMIN_ONLY)
            return
        _, action, target_id_str = data.split(":", 2)
        target_id = int(target_id_str)

        if action == "approve":
            db.approve_user(target_id)
            await query.edit_message_text(MSG_APPROVED.format(telegram_id=target_id))
            try:
                await context.bot.send_message(chat_id=target_id, text=MSG_USER_APPROVED_NOTIFY)
            except Exception as e:
                logger.warning("Could not notify user %s: %s", target_id, e)
        elif action == "block":
            db.upsert_user(target_id, is_approved=0)
            await query.edit_message_text(MSG_BLOCKED.format(telegram_id=target_id))
            try:
                await context.bot.send_message(chat_id=target_id, text=MSG_USER_BLOCKED_NOTIFY)
            except Exception as e:
                logger.warning("Could not notify user %s: %s", target_id, e)
        return

    # --- Route selection: "route:<kind>:<route_id>" ---
    if data.startswith("route:"):
        parts = data.split(":", 2)
        kind = parts[1]
        route_id = int(parts[2])

        user = db.get_user(tid)
        if not user:
            await query.edit_message_text(MSG_NOT_LOGGED_IN)
            return

        async with aiohttp.ClientSession() as session:
            client = _build_client(user["iti_email"], user["iti_password"])
            try:
                stops = await client.get_route_stops(session, user["access_token"], route_id)
            except iti_api.ITIAuthError:
                token = await _ensure_valid_token(session, user)
                if token is None:
                    await query.edit_message_text(MSG_AUTH_ERROR)
                    return
                try:
                    stops = await client.get_route_stops(session, token, route_id)
                except Exception:
                    await query.edit_message_text(MSG_STOPS_FAIL)
                    return
            except Exception:
                await query.edit_message_text(MSG_STOPS_FAIL)
                return

        # Cache stops and route name in context.user_data for safe retrieval
        if "stops_cache" not in context.user_data:
            context.user_data["stops_cache"] = {}
        for s in stops:
            context.user_data["stops_cache"][s["id"]] = s.get("stopName", "")

        route_name = context.user_data.get("routes_cache", {}).get(route_id, str(route_id))
        context.user_data[f"selected_{kind}_route_id"] = route_id
        context.user_data[f"selected_{kind}_route_name"] = route_name

        buttons = []
        for s in stops:
            stop_name = s.get("stopName", "")
            # Short callback data to strictly respect Telegram's 64-byte limit:
            cb = f"stop:{kind}:{route_id}:{s['id']}"
            buttons.append([InlineKeyboardButton(stop_name, callback_data=cb)])

        kb = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            MSG_SELECT_STOP.format(route_name=route_name),
            reply_markup=kb,
        )
        return

    # --- Stop selection: "stop:<kind>:<route_id>:<stop_id>" ---
    if data.startswith("stop:"):
        parts = data.split(":")
        kind = parts[1]
        route_id = int(parts[2])
        stop_id = int(parts[3])

        # Retrieve stop name & route name from cache
        stop_name = context.user_data.get("stops_cache", {}).get(stop_id, str(stop_id))
        route_name = context.user_data.get(f"selected_{kind}_route_name") or context.user_data.get("routes_cache", {}).get(route_id, str(route_id))

        db.set_route(tid, kind, route_id, stop_id, stop_name, route_name)

        if kind == "primary":
            msg = MSG_PRIMARY_SAVED.format(route_name=route_name, stop_name=stop_name)
        else:
            msg = MSG_BACKUP_SAVED.format(route_name=route_name, stop_name=stop_name)

        await query.edit_message_text(msg)
        return


# ---------------------------------------------------------------------------
# Background: booking poller (Single shared poller for anti-ban)
# ---------------------------------------------------------------------------

async def _book_for_user(
    session: aiohttp.ClientSession,
    user: dict,
    trips: list[dict],
    bot,
) -> None:
    """Attempt to book a trip for one user using the shared trips list."""
    tid = user["telegram_id"]
    client = _build_client(user["iti_email"], user["iti_password"])
    access_token = user.get("access_token")

    async def refresh_token_flow() -> str | None:
        nonlocal access_token
        token = await _ensure_valid_token(session, db.get_user(tid))
        if token:
            access_token = token
            return token
        return None

    # Determine routes to try: Primary first, then Backup
    routes_to_try = []
    if user.get("primary_route_id") and user.get("primary_stop_id"):
        routes_to_try.append(("primary", user["primary_route_id"], user["primary_stop_id"]))
    if user.get("backup_route_id") and user.get("backup_stop_id"):
        routes_to_try.append(("backup", user["backup_route_id"], user["backup_stop_id"]))

    if not routes_to_try:
        return

    for label, route_id, stop_id in routes_to_try:
        # Find matching trip on this route with available seats
        matching = [
            t for t in trips
            if t.get("routeId") == route_id and t.get("availableSeats", 0) > 0
        ]
        if not matching:
            continue

        trip = matching[0]
        logger.info("User %s: Found seat on %s route (%s, trip %s). Booking...", tid, label, trip.get("routeName"), trip.get("id"))

        # Ensure we have a valid token before booking
        if not access_token:
            access_token = await refresh_token_flow()
            if not access_token:
                await bot.send_message(chat_id=tid, text=MSG_AUTH_ERROR)
                return

        try:
            booking = await client.book_trip(session, access_token, trip["id"], stop_id)
        except iti_api.ITIAuthError:
            token = await refresh_token_flow()
            if token is None:
                await bot.send_message(chat_id=tid, text=MSG_AUTH_ERROR)
                return
            try:
                booking = await client.book_trip(session, token, trip["id"], stop_id)
            except iti_api.ITIBookingError as e:
                logger.warning("BookingError after refresh for %s: %s", tid, e)
                continue
            except Exception as e:
                logger.error("book_trip failed for %s: %s", tid, e)
                continue
        except iti_api.ITIBookingError as e:
            logger.warning("ITIBookingError for %s on %s route: %s", tid, label, e)
            continue
        except Exception as e:
            logger.error("Unexpected error booking for %s: %s", tid, e)
            continue

        if booking.get("success"):
            db.set_booked_today(tid, True)
            msg = MSG_BOOKING_SUCCESS.format(
                booking_code=booking.get("bookingCode", ""),
                route_name=booking.get("routeName", trip.get("routeName", "")),
                bus_number=booking.get("busNumber", trip.get("busNumber", "")),
                stop_name=booking.get("pickupStopName", user.get(f"{label}_stop_name", "")),
                departure=booking.get("departureTime", trip.get("departureTime", "")),
            )
            try:
                await bot.send_message(chat_id=tid, text=msg)
                logger.info("Successfully booked seat for %s on %s!", tid, trip.get("routeName"))
            except Exception as e:
                logger.warning("Could not send booking confirmation to %s: %s", tid, e)
            return  # Successfully booked; stop trying further routes


async def booking_poller_task(bot) -> None:
    """Runs forever; polls for auto-booking within working hours using a shared single poll."""
    logger.info("Shared anti-ban booking poller started.")
    while True:
        try:
            if _is_working_window():
                today_str = date.today().strftime("%Y-%m-%d")
                users = db.get_all_active_users()
                if users:
                    async with aiohttp.ClientSession() as session:
                        # 1. Fetch available trips ONCE for all users using the first valid token
                        trips = None
                        for u in users:
                            token = u.get("access_token")
                            if not token:
                                token = await _ensure_valid_token(session, u)
                            if not token:
                                continue
                            try:
                                client = _build_client(u["iti_email"], u["iti_password"])
                                trips = await client.get_trips(session, token, today_str)
                                break
                            except iti_api.ITIAuthError:
                                fresh_token = await _ensure_valid_token(session, u)
                                if fresh_token:
                                    try:
                                        trips = await client.get_trips(session, fresh_token, today_str)
                                        break
                                    except Exception:
                                        pass
                            except Exception as e:
                                logger.warning("Failed to fetch shared trips with user %s: %s", u["telegram_id"], e)

                        # 2. If trips were successfully fetched, check and book for each waiting user
                        if trips:
                            for u in users:
                                await _book_for_user(session, u, trips, bot)
        except Exception as e:
            logger.exception("Unexpected error in poller cycle: %s", e)
        await asyncio.sleep(120)


async def reset_booked_today_task() -> None:
    """Waits until midnight Cairo time, then resets booked_today daily."""
    logger.info("Midnight reset task started.")
    while True:
        now = datetime.now(CAIRO_TZ)
        # Compute next midnight
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_secs = (tomorrow - now).total_seconds()
        logger.info("Reset task sleeping %.0f seconds until midnight.", wait_secs)
        await asyncio.sleep(wait_secs)
        try:
            db.reset_booked_today()
            logger.info("booked_today reset for all users.")
        except Exception as e:
            logger.error("reset_booked_today failed: %s", e)


# ---------------------------------------------------------------------------
# HTTP healthcheck server
# ---------------------------------------------------------------------------

async def _health_handler(request: web.Request) -> web.Response:
    payload = {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return web.json_response(payload)


async def run_health_server() -> None:
    if PORT <= 0:
        logger.info("Health server disabled (PORT <= 0)")
        await asyncio.Event().wait()
        return

    try:
        app = web.Application()
        app.router.add_get("/", _health_handler)
        app.router.add_get("/health", _health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info("Health server listening on port %s", PORT)
        # Keep running forever
        await asyncio.Event().wait()
    except OSError as e:
        logger.warning(
            "Could not bind health server on port %s: %s (continuing without health server)",
            PORT,
            e,
        )
        await asyncio.Event().wait()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    # 1. Init DB
    db.init_db(DB_PATH)

    # 2. Build Telegram application
    application = Application.builder().token(BOT_TOKEN).build()

    # 3. Register command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("login", cmd_login))
    application.add_handler(CommandHandler("primary", cmd_primary))
    application.add_handler(CommandHandler("backup", cmd_backup))
    application.add_handler(CommandHandler("auto", cmd_auto))
    application.add_handler(CommandHandler("check", cmd_check))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("logout", cmd_logout))
    application.add_handler(CommandHandler("admin", cmd_admin))

    # 4. Register callback query handler
    application.add_handler(CallbackQueryHandler(callback_handler))

    # 5. Initialize and start the application
    await application.initialize()
    await application.start()

    bot = application.bot

    # 6. Launch background tasks
    poller = asyncio.create_task(booking_poller_task(bot))
    resetter = asyncio.create_task(reset_booked_today_task())
    health = asyncio.create_task(run_health_server())

    # 7. Start polling (non-blocking updater)
    await application.updater.start_polling(drop_pending_updates=True)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        # Wait for all background tasks; they run until cancelled
        await asyncio.gather(poller, resetter, health)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        poller.cancel()
        resetter.cancel()
        health.cancel()
        await asyncio.gather(poller, resetter, health, return_exceptions=True)
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
