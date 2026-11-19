"""
bot_web.py — ITI Bus Telegram Bot with Flask/WSGI & Long-Polling Support.

Features:
1. Modern Standard Arabic interface for all interactions.
2. Fully pre-cached ITI routes & stops (no external HTTP calls to ITI domain from PythonAnywhere).
3. GitHub Actions repository_dispatch integration for /check and /book commands.
4. Dual runtime:
   - Flask/WSGI application for PythonAnywhere Web App (webhook mode).
   - Long-polling mode for local development or background worker (`--polling`).
5. Secure REST API endpoints for GitHub Actions runner to query active users.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Optional

import requests
from flask import Flask, jsonify, request

import database as db

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot_web")

# ---------------------------------------------------------------------------
# Configuration & Environment Variables
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "9127650907:AAE9vm6arlCS2-AkfcR_bBXPzUloFBXN0us").strip()
ADMIN_ID: int = int(os.environ.get("ADMIN_ID", "0"))
DB_PATH: str = os.environ.get("DB_PATH", "bot_data.db").strip()
API_SECRET: str = os.environ.get("API_SECRET", "iti_secret_token_2026").strip()
PORT: int = int(os.environ.get("PORT", "5000"))

# GitHub Dispatch Configuration
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO: str = os.environ.get("GITHUB_REPO", "Mahershaker-STARK/iti-bus-bot").strip()
GITHUB_DISPATCH_URL: str = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"

TELEGRAM_API_BASE: str = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Initialize Database on import
db.init_db(DB_PATH)

# ---------------------------------------------------------------------------
# Arabic Message Templates
# ---------------------------------------------------------------------------
MSG_WELCOME = (
    "مرحباً بك في بوت حجز حافلات ITI 🚌✨\n\n"
    "يعمل هذا البوت بالنظام الهجين فائق السرعة عبر سحابة GitHub Actions لتجاوز قيود الشبكة وضمان سرعة الحجز.\n\n"
    "📋 <b>الأوامر المتاحة:</b>\n"
    "🔹 <code>/login البريد كلمة_المرور</code> — تسجيل الدخول لحساب ITI\n"
    "🔹 <code>/primary</code> — اختيار الخط والمحطة الأساسية\n"
    "🔹 <code>/backup</code> — اختيار الخط والمحطة الاحتياطية\n"
    "🔹 <code>/check</code> — فحص المقاعد المتاحة فورياً عبر السحابة\n"
    "🔹 <code>/book</code> — حجز المقعد فورياً عبر السحابة\n"
    "🔹 <code>/auto</code> — تفعيل أو إيقاف الحجز التلقائي الذكي\n"
    "🔹 <code>/status</code> — عرض إعداداتك وحالتك الحالية\n"
    "🔹 <code>/logout</code> — تسجيل الخروج ومسح بياناتك\n"
    "🔹 <code>/help</code> — عرض هذه المساعدة"
)

MSG_LOGIN_USAGE = (
    "⚠️ <b>الاستخدام الصحيح لأمر تسجيل الدخول:</b>\n"
    "<code>/login your_email@example.com your_password</code>\n\n"
    "مثال:\n"
    "<code>/login ahmed.ali@iti.gov.eg mySecretPass123</code>"
)

MSG_LOGIN_SUCCESS = (
    "✅ <b>تم تسجيل بيانات حسابك بنجاح!</b>\n\n"
    "👤 <b>البريد:</b> <code>{email}</code>\n\n"
    "📍 <b>الخطوة التالية:</b>\n"
    "1. حدد خطك ومحطتك الأساسية عبر <code>/primary</code>\n"
    "2. حدد خطك الاحتياطي عبر <code>/backup</code>\n"
    "3. افحص الرحلات بأمر <code>/check</code> أو احجز بأمر <code>/book</code>"
)

MSG_PENDING_APPROVAL = (
    "⏳ <b>تم استلام بياناتك بنجاح!</b>\n"
    "حسابك حالياً في انتظار موافقة الإدارة. سيصلك إشعار فور التفعيل."
)

MSG_ADMIN_ALERT_NEW_USER = (
    "👤 <b>طلب انضمام مستخدم جديد:</b>\n"
    "المعرف: <code>{telegram_id}</code>\n"
    "البريد: <code>{email}</code>\n"
    "الاسم: {name}"
)

MSG_NOT_LOGGED_IN = (
    "❌ <b>لم تقم بتسجيل الدخول بعد!</b>\n"
    "يرجى تسجيل بياناتك أولاً باستخدام:\n"
    "<code>/login your_email@example.com your_password</code>"
)

MSG_NOT_APPROVED = (
    "⏳ <b>حسابك قيد المراجعة</b>\n"
    "يرجى انتظار موافقة الإدارة لتتمكن من استخدام هذه الميزة."
)

MSG_SELECT_ROUTE_PRIMARY = "🚌 <b>اختر الخط الأساسي المفضل لديك:</b>"
MSG_SELECT_ROUTE_BACKUP = "🚌 <b>اختر الخط الاحتياطي المفضل لديك:</b>"
MSG_SELECT_STOP = "🛑 <b>اختر محطة الركوب لخط ({route_name}):</b>"

MSG_ROUTE_SAVED_PRIMARY = (
    "✅ <b>تم حفظ الخط الأساسي بنجاح!</b>\n\n"
    "📍 <b>الخط:</b> {route_name}\n"
    "🛑 <b>محطة الركوب:</b> {stop_name}"
)

MSG_ROUTE_SAVED_BACKUP = (
    "✅ <b>تم حفظ الخط الاحتياطي بنجاح!</b>\n\n"
    "📍 <b>الخط:</b> {route_name}\n"
    "🛑 <b>محطة الركوب:</b> {stop_name}"
)

MSG_DISPATCHING_CHECK = "⏳ جاري تشغيل الفحص السحابي عبر GitHub Actions... ستصلك النتيجة هنا فوراً."
MSG_DISPATCHING_BOOK = "⏳ جاري تشغيل الحجز السحابي عبر GitHub Actions... ستصلك النتيجة هنا فوراً."
MSG_DISPATCH_FAILED = "❌ تعذر إرسال الطلب إلى السحابة حالياً. يرجى إعادة المحاولة بعد لحظات."

MSG_AUTO_ENABLED = (
    "✅ <b>تم تفعيل الحجز التلقائي الذكي!</b>\n\n"
    "سيقوم النظام السحابي بمحاولة حجز مقعدك تلقائياً كل 5 دقائق أثناء مواعيد عمل الحجز الرسمية (الأحد - الخميس من 9:00 ص إلى 4:00 م)."
)
MSG_AUTO_DISABLED = "❌ <b>تم إيقاف الحجز التلقائي الذكي.</b>"

MSG_STATUS_TEMPLATE = (
    "📊 <b>إعدادات وحالة حسابك الحالية:</b>\n\n"
    "👤 <b>البريد:</b> <code>{email}</code>\n"
    "🟢 <b>حالة الحساب:</b> {status}\n\n"
    "📍 <b>الخط الأساسي:</b> {primary_route}\n"
    "🛑 <b>محطة الأساسي:</b> {primary_stop}\n\n"
    "📍 <b>الخط الاحتياطي:</b> {backup_route}\n"
    "🛑 <b>محطة الاحتياطي:</b> {backup_stop}\n\n"
    "⚡ <b>الحجز التلقائي:</b> {auto_book}\n"
    "🎫 <b>تم الحجز اليوم:</b> {booked_today}"
)

MSG_LOGGED_OUT = "👋 تم تسجيل الخروج وحذف جميع بياناتك المحفوظة بأمان."
MSG_ADMIN_ONLY = "⛔ هذا الأمر مخصص للمشرفين فقط."


# ---------------------------------------------------------------------------
# Telegram Bot API Helpers
# ---------------------------------------------------------------------------
def send_telegram_message(
    chat_id: int | str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> bool:
    """Send message directly via Telegram Bot API."""
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN is not set; cannot send Telegram message.")
        return False

    url = f"{TELEGRAM_API_BASE}/sendMessage"
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
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.warning("Telegram sendMessage error (%s): %s", resp.status_code, resp.text)
            # If HTML entities failed, retry once in plain text
            if parse_mode and "can't parse entities" in resp.text.lower():
                payload.pop("parse_mode", None)
                retry_resp = requests.post(url, json=payload, timeout=8)
                return retry_resp.ok
        return resp.ok
    except Exception as e:
        logger.error("Failed to send Telegram message to %s: %s", chat_id, e)
        return False


def edit_telegram_message(
    chat_id: int | str,
    message_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> bool:
    """Edit existing message via Telegram Bot API."""
    url = f"{TELEGRAM_API_BASE}/editMessageText"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.ok
    except Exception as e:
        logger.error("Failed to edit Telegram message (%s, %s): %s", chat_id, message_id, e)
        return False


def answer_callback_query(callback_query_id: str, text: Optional[str] = None) -> bool:
    """Acknowledge Telegram callback query."""
    url = f"{TELEGRAM_API_BASE}/answerCallbackQuery"
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        resp = requests.post(url, json=payload, timeout=5)
        return resp.ok
    except Exception as e:
        logger.debug("Failed to answer callback query: %s", e)
        return False


# ---------------------------------------------------------------------------
# GitHub Repository Dispatch Integration
# ---------------------------------------------------------------------------
def dispatch_github_action(event_type: str, client_payload: dict) -> bool:
    """
    Trigger GitHub Actions workflow via repository_dispatch event.
    Dispatches to https://api.github.com/repos/{owner}/{repo}/dispatches
    """
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN is missing; cannot dispatch GitHub Action.")
        return False

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "ITI-Bus-Bot/2.0",
    }
    body = {
        "event_type": event_type,
        "client_payload": client_payload,
    }

    try:
        resp = requests.post(GITHUB_DISPATCH_URL, json=body, headers=headers, timeout=12)
        if resp.status_code in (200, 201, 202, 204):
            logger.info("Successfully dispatched '%s' event to GitHub Actions.", event_type)
            return True
        else:
            logger.error(
                "GitHub dispatch failed with HTTP %s: %s",
                resp.status_code,
                resp.text,
            )
            return False
    except Exception as e:
        logger.error("Exception occurred while dispatching GitHub Action: %s", e)
        return False


# ---------------------------------------------------------------------------
# Telegram Bot Core Handlers
# ---------------------------------------------------------------------------
def build_routes_keyboard(kind: str) -> dict:
    """Generate inline keyboard for routes using pre-cached database table."""
    routes = db.get_cached_routes()
    keyboard = []
    for r in routes:
        name = r["name"]
        rid = r["id"]
        keyboard.append([{"text": f"🚌 {name}", "callback_data": f"route:{kind}:{rid}"}])
    return {"inline_keyboard": keyboard}


def build_stops_keyboard(kind: str, route_id: int) -> dict:
    """Generate inline keyboard for stops of a given route using pre-cached database table."""
    stops = db.get_cached_stops(route_id)
    keyboard = []
    for s in stops:
        sname = s["stop_name"]
        sid = s["id"]
        keyboard.append([{"text": f"🛑 {sname}", "callback_data": f"stop:{kind}:{route_id}:{sid}"}])
    keyboard.append([{"text": "🔙 العودة لاختيار الخط", "callback_data": f"back_to_routes:{kind}"}])
    return {"inline_keyboard": keyboard}


def handle_command(chat_id: int, user_id: int, text: str, user_name: str = "") -> None:
    """Process incoming slash commands."""
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].lower().split("@")[0]  # strip bot username if present
    args = parts[1:]

    # /start or /help
    if cmd in ("/start", "/help"):
        send_telegram_message(chat_id, MSG_WELCOME)
        return

    # /login <email> <password>
    if cmd == "/login":
        if len(args) < 2:
            send_telegram_message(chat_id, MSG_LOGIN_USAGE)
            return

        email = args[0].strip()
        password = args[1].strip()

        send_telegram_message(
            chat_id,
            "⏳ <b>جاري التحقق من صحة بيانات الدخول عبر سيرفرات ITI...</b>\nستصلك رسالة التأكيد فور اكتمال الفحص.",
        )

        payload = {
            "chat_id": chat_id,
            "email": email,
            "password": password,
            "action": "login",
            "bot_token": BOT_TOKEN,
            "callback_url": "https://maherstark.pythonanywhere.com/api/v1/save_user",
            "api_secret": API_SECRET,
        }

        success = dispatch_github_action("telegram_login", payload)
        if not success:
            send_telegram_message(chat_id, MSG_DISPATCH_FAILED)
        return

    # /primary — select primary route
    if cmd == "/primary":
        user = db.get_user(user_id)
        if not user or not user.get("iti_email"):
            send_telegram_message(chat_id, MSG_NOT_LOGGED_IN)
            return
        if not user.get("is_approved"):
            send_telegram_message(chat_id, MSG_NOT_APPROVED)
            return

        kb = build_routes_keyboard("primary")
        send_telegram_message(chat_id, MSG_SELECT_ROUTE_PRIMARY, reply_markup=kb)
        return

    # /backup — select backup route
    if cmd == "/backup":
        user = db.get_user(user_id)
        if not user or not user.get("iti_email"):
            send_telegram_message(chat_id, MSG_NOT_LOGGED_IN)
            return
        if not user.get("is_approved"):
            send_telegram_message(chat_id, MSG_NOT_APPROVED)
            return

        kb = build_routes_keyboard("backup")
        send_telegram_message(chat_id, MSG_SELECT_ROUTE_BACKUP, reply_markup=kb)
        return

    # /check — dispatch telegram_check to GitHub Actions
    if cmd == "/check":
        user = db.get_user(user_id)
        if not user or not user.get("iti_email"):
            send_telegram_message(chat_id, MSG_NOT_LOGGED_IN)
            return
        if not user.get("is_approved"):
            send_telegram_message(chat_id, MSG_NOT_APPROVED)
            return

        payload = {
            "chat_id": chat_id,
            "email": user["iti_email"],
            "password": user["iti_password"],
            "primary_route_id": user.get("primary_route_id"),
            "primary_stop_id": user.get("primary_stop_id"),
            "backup_route_id": user.get("backup_route_id"),
            "backup_stop_id": user.get("backup_stop_id"),
            "action": "check",
            "bot_token": BOT_TOKEN,
        }

        # Reply immediately as required by specification
        send_telegram_message(chat_id, MSG_DISPATCHING_CHECK)

        # Trigger GitHub Actions dispatch
        success = dispatch_github_action("telegram_check", payload)
        if not success:
            send_telegram_message(chat_id, MSG_DISPATCH_FAILED)
        return

    # /book — dispatch telegram_book to GitHub Actions
    if cmd == "/book":
        user = db.get_user(user_id)
        if not user or not user.get("iti_email"):
            send_telegram_message(chat_id, MSG_NOT_LOGGED_IN)
            return
        if not user.get("is_approved"):
            send_telegram_message(chat_id, MSG_NOT_APPROVED)
            return

        payload = {
            "chat_id": chat_id,
            "email": user["iti_email"],
            "password": user["iti_password"],
            "primary_route_id": user.get("primary_route_id"),
            "primary_stop_id": user.get("primary_stop_id"),
            "backup_route_id": user.get("backup_route_id"),
            "backup_stop_id": user.get("backup_stop_id"),
            "action": "book",
            "bot_token": BOT_TOKEN,
        }

        # Reply immediately as required by specification
        send_telegram_message(chat_id, MSG_DISPATCHING_BOOK)

        # Trigger GitHub Actions dispatch
        success = dispatch_github_action("telegram_book", payload)
        if not success:
            send_telegram_message(chat_id, MSG_DISPATCH_FAILED)
        return

    # /auto — toggle auto-booking
    if cmd == "/auto":
        user = db.get_user(user_id)
        if not user or not user.get("iti_email"):
            send_telegram_message(chat_id, MSG_NOT_LOGGED_IN)
            return
        if not user.get("is_approved"):
            send_telegram_message(chat_id, MSG_NOT_APPROVED)
            return

        new_val = db.toggle_auto_book(user_id)
        msg = MSG_AUTO_ENABLED if new_val else MSG_AUTO_DISABLED
        send_telegram_message(chat_id, msg)
        return

    # /status — display current configuration
    if cmd == "/status":
        user = db.get_user(user_id)
        if not user or not user.get("iti_email"):
            send_telegram_message(chat_id, MSG_NOT_LOGGED_IN)
            return

        status_text = "✅ معتمد ومفعل" if user.get("is_approved") else "⏳ بانتظار الموافقة"
        primary_route = user.get("primary_route_name") or (f"خط #{user['primary_route_id']}" if user.get("primary_route_id") else "غير محدد")
        primary_stop = user.get("primary_stop_name") or "غير محدد"
        backup_route = user.get("backup_route_name") or (f"خط #{user['backup_route_id']}" if user.get("backup_route_id") else "غير محدد")
        backup_stop = user.get("backup_stop_name") or "غير محدد"
        auto_book = "✅ مفعل" if user.get("auto_book") else "❌ معطل"
        booked_today = "✅ تم الحجز بنجاح" if user.get("booked_today") else "❌ لم يتم الحجز بعد"

        text = MSG_STATUS_TEMPLATE.format(
            email=user.get("iti_email", "—"),
            status=status_text,
            primary_route=primary_route,
            primary_stop=primary_stop,
            backup_route=backup_route,
            backup_stop=backup_stop,
            auto_book=auto_book,
            booked_today=booked_today,
        )
        send_telegram_message(chat_id, text)
        return

    # /logout — delete user record
    if cmd == "/logout":
        db.delete_user(user_id)
        send_telegram_message(chat_id, MSG_LOGGED_OUT)
        return

    # /admin — manage users (admin only)
    if cmd == "/admin":
        if user_id != ADMIN_ID:
            send_telegram_message(chat_id, MSG_ADMIN_ONLY)
            return

        users = db.get_all_users()
        if not users:
            send_telegram_message(chat_id, "ℹ️ لا يوجد مستخدمون مسجلون في قاعدة البيانات.")
            return

        for u in users:
            stat = "✅ موافق عليه" if u.get("is_approved") else "⏳ بانتظار الموافقة"
            u_text = (
                f"👤 <b>{u.get('full_name') or 'مستخدم'}</b> (<code>{u['telegram_id']}</code>)\n"
                f"📧 البريد: <code>{u.get('iti_email') or '—'}</code>\n"
                f"📊 الحالة: {stat}\n"
                f"🚌 الأساسي: {u.get('primary_route_name') or 'غير محدد'}\n"
                f"🛑 المحطة: {u.get('primary_stop_name') or 'غير محدد'}"
            )
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "✅ موافقة", "callback_data": f"admin:approve:{u['telegram_id']}"},
                        {"text": "🚫 حظر", "callback_data": f"admin:block:{u['telegram_id']}"},
                    ]
                ]
            }
            send_telegram_message(chat_id, u_text, reply_markup=kb)
        return


def handle_callback_query(callback_query: dict) -> None:
    """Process incoming button clicks / callback queries."""
    cq_id = callback_query.get("id")
    from_user = callback_query.get("from", {})
    user_id = from_user.get("id")
    msg = callback_query.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    data = callback_query.get("data", "")

    answer_callback_query(cq_id)

    # --- Back to Routes Selection: back_to_routes:<kind> ---
    if data.startswith("back_to_routes:"):
        kind = data.split(":")[1]
        kb = build_routes_keyboard(kind)
        text = MSG_SELECT_ROUTE_PRIMARY if kind == "primary" else MSG_SELECT_ROUTE_BACKUP
        edit_telegram_message(chat_id, msg_id, text, reply_markup=kb)
        return

    # --- Route Selection: route:<kind>:<route_id> ---
    if data.startswith("route:"):
        parts = data.split(":")
        if len(parts) >= 3:
            kind = parts[1]
            route_id = int(parts[2])

            route = db.get_cached_route(route_id)
            route_name = route["name"] if route else f"خط #{route_id}"

            kb = build_stops_keyboard(kind, route_id)
            text = MSG_SELECT_STOP.format(route_name=route_name)
            edit_telegram_message(chat_id, msg_id, text, reply_markup=kb)
        return

    # --- Stop Selection: stop:<kind>:<route_id>:<stop_id> ---
    if data.startswith("stop:"):
        parts = data.split(":")
        if len(parts) >= 4:
            kind = parts[1]
            route_id = int(parts[2])
            stop_id = int(parts[3])

            route = db.get_cached_route(route_id)
            stop = db.get_cached_stop(stop_id)

            route_name = route["name"] if route else f"خط #{route_id}"
            stop_name = stop["stop_name"] if stop else f"محطة #{stop_id}"

            # Save in database
            db.set_route(user_id, kind, route_id, stop_id, stop_name, route_name)

            if kind == "primary":
                confirmation = MSG_ROUTE_SAVED_PRIMARY.format(route_name=route_name, stop_name=stop_name)
            else:
                confirmation = MSG_ROUTE_SAVED_BACKUP.format(route_name=route_name, stop_name=stop_name)

            edit_telegram_message(chat_id, msg_id, confirmation)
        return

    # --- Admin Approval Actions: admin:<action>:<target_id> ---
    if data.startswith("admin:"):
        if user_id != ADMIN_ID:
            edit_telegram_message(chat_id, msg_id, MSG_ADMIN_ONLY)
            return

        parts = data.split(":")
        if len(parts) >= 3:
            action = parts[1]
            target_id = int(parts[2])

            if action == "approve":
                db.approve_user(target_id)
                edit_telegram_message(chat_id, msg_id, f"✅ تمت الموافقة على المستخدم <code>{target_id}</code>.")
                send_telegram_message(
                    target_id,
                    "🎉 <b>تمت الموافقة على تفعيل حسابك بنجاح!</b>\n\n"
                    "يمكنك الآن استخدام البوت بشكل كامل. ابدأ باختيار خطك الأساسي عبر <code>/primary</code>",
                )
            elif action == "block":
                db.upsert_user(target_id, is_approved=0)
                edit_telegram_message(chat_id, msg_id, f"🚫 تم حظر/إيقاف المستخدم <code>{target_id}</code>.")
                send_telegram_message(
                    target_id,
                    "🚫 <b>تم إيقاف تفعيل حسابك من قِبل الإدارة.</b>",
                )
        return


def process_update(update: dict) -> None:
    """Entry point for handling any raw Telegram Update object."""
    try:
        if "message" in update:
            msg = update["message"]
            text = msg.get("text", "")
            chat = msg.get("chat", {})
            from_u = msg.get("from", {})
            chat_id = chat.get("id")
            user_id = from_u.get("id")
            user_name = from_u.get("first_name", "")
            if from_u.get("last_name"):
                user_name += f" {from_u['last_name']}"

            if chat_id and user_id and text:
                handle_command(chat_id, user_id, text, user_name)

        elif "callback_query" in update:
            handle_callback_query(update["callback_query"])
    except Exception as e:
        logger.exception("Error processing Telegram update: %s", e)


# ---------------------------------------------------------------------------
# Flask / WSGI Application
# ---------------------------------------------------------------------------
app = Flask(__name__)
application = app  # Standard WSGI entry point for PythonAnywhere


def _is_authorized(req: request) -> bool:
    """Validate API secret from query parameter, JSON body, or Authorization Bearer header."""
    secret = req.args.get("secret")
    if secret and secret == API_SECRET:
        return True

    data = req.get_json(silent=True) or {}
    if isinstance(data, dict) and data.get("secret") == API_SECRET:
        return True

    auth_header = req.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token == API_SECRET:
            return True

    return False


@app.route("/", methods=["GET"])
def index():
    """Healthcheck and information endpoint."""
    return jsonify({
        "service": "ITI Bus Telegram Web Bot",
        "status": "online",
        "version": "2.0.0",
        "endpoints": {
            "health": "/health",
            "webhook": "/webhook",
            "active_users": "/api/v1/active_users?secret=<KEY>",
        },
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": time.time()}), 200


@app.route("/webhook", methods=["POST"])
@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    """Receives incoming updates from Telegram webhook."""
    if not request.is_json:
        return "Expected JSON", 400

    data = request.get_json(force=True, silent=True)
    if not data:
        return "Invalid JSON", 400

    process_update(data)
    return "OK", 200


@app.route("/api/v1/active_users", methods=["GET"])
@app.route("/api/active_users", methods=["GET"])
@app.route("/api/users/active", methods=["GET"])
def get_active_users():
    """
    Secure API endpoint for GitHub Actions scheduled runner.
    Returns JSON list of approved active users who need auto-booking today.
    """
    if not _is_authorized(request):
        logger.warning("Unauthorized access attempt to /api/v1/active_users")
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    users = db.get_all_active_users()
    logger.info("External runner fetched %d active users.", len(users))
    return jsonify({
        "status": "success",
        "count": len(users),
        "users": users,
    }), 200


@app.route("/api/users/<chat_id>/booked", methods=["POST"])
@app.route("/api/v1/mark_booked", methods=["POST"])
def mark_booked(chat_id: Optional[str] = None):
    """Marks a user as booked_today in the database."""
    if not _is_authorized(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    tid = chat_id or data.get("telegram_id") or data.get("chat_id")
    if not tid:
        return jsonify({"status": "error", "message": "Missing telegram_id / chat_id"}), 400

    try:
        db.set_booked_today(int(tid), True)
        logger.info("Marked user %s as booked_today=1", tid)
        return jsonify({"status": "success", "telegram_id": int(tid), "booked_today": 1}), 200
    except Exception as e:
        logger.error("Error setting booked_today for %s: %s", tid, e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/v1/save_user", methods=["POST"])
def save_user():
    """Secure endpoint to save/update a user verified by GitHub Actions."""
    if not _is_authorized(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id") or data.get("telegram_id")
    email = data.get("email") or data.get("iti_email")
    password = data.get("password") or data.get("iti_password")

    if not chat_id or not email or not password:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    try:
        db.upsert_user(
            int(chat_id),
            iti_email=email,
            iti_password=password,
            full_name=data.get("full_name") or f"User_{chat_id}",
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            is_approved=1,
        )
        logger.info("Successfully saved verified user %s (%s)", chat_id, email)
        return jsonify({"status": "success", "message": "User saved", "chat_id": int(chat_id)}), 200
    except Exception as e:
        logger.error("Error saving user %s: %s", chat_id, e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/v1/reset_booked", methods=["POST"])
def reset_booked():
    """Reset booked_today flag for all users."""
    if not _is_authorized(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    try:
        db.reset_booked_today()
        logger.info("Reset booked_today flag for all users.")
        return jsonify({"status": "success", "message": "All users reset"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/v1/notify", methods=["POST"])
def send_notification():
    """Allows external scripts / GitHub Actions to push a message through the bot."""
    if not _is_authorized(request):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    cid = data.get("chat_id") or data.get("telegram_id")
    msg = data.get("text")
    if not cid or not msg:
        return jsonify({"status": "error", "message": "Missing chat_id or text"}), 400

    ok = send_telegram_message(cid, msg, parse_mode=data.get("parse_mode", "HTML"))
    return jsonify({"status": "success" if ok else "failed"}), 200 if ok else 500


# ---------------------------------------------------------------------------
# Long-Polling Worker
# ---------------------------------------------------------------------------
def run_polling() -> None:
    """
    Long-polling mode for local testing or persistent process.
    Continuously calls getUpdates on Telegram Bot API.
    """
    logger.info("Starting Telegram long-polling mode...")
    # Delete webhook to prevent conflicts with getUpdates
    try:
        requests.get(f"{TELEGRAM_API_BASE}/deleteWebhook", timeout=10)
        logger.info("Cleared existing webhook before starting long-polling.")
    except Exception as e:
        logger.warning("Could not clear webhook: %s", e)

    offset = 0
    while True:
        try:
            params = {"offset": offset, "timeout": 25}
            resp = requests.get(f"{TELEGRAM_API_BASE}/getUpdates", params=params, timeout=30)
            if resp.ok:
                result = resp.json().get("result", [])
                for u in result:
                    process_update(u)
                    offset = u["update_id"] + 1
            elif resp.status_code == 409:
                logger.warning("Conflict: another bot instance is running. Waiting 5s...")
                time.sleep(5)
            else:
                logger.warning("getUpdates HTTP %s: %s", resp.status_code, resp.text)
                time.sleep(3)
        except KeyboardInterrupt:
            logger.info("Polling stopped by user.")
            break
        except Exception as e:
            logger.error("Error in polling loop: %s", e)
            time.sleep(3)


# ---------------------------------------------------------------------------
# Webhook Setup Utilities
# ---------------------------------------------------------------------------
def setup_webhook(webhook_url: str) -> bool:
    """Register webhook URL with Telegram Bot API."""
    url = f"{TELEGRAM_API_BASE}/setWebhook"
    logger.info("Setting Telegram webhook to: %s", webhook_url)
    resp = requests.post(url, json={"url": webhook_url}, timeout=10)
    logger.info("setWebhook response: %s", resp.text)
    return resp.ok


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ITI Bus Telegram Web Bot")
    parser.add_argument(
        "--polling",
        action="store_true",
        help="Run in Telegram long-polling mode instead of Flask server",
    )
    parser.add_argument(
        "--set-webhook",
        metavar="URL",
        help="Set Telegram webhook URL (e.g. https://username.pythonanywhere.com/webhook)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help=f"Port to bind Flask server (default: {PORT})",
    )
    args = parser.parse_args()

    if args.set_webhook:
        setup_webhook(args.set_webhook)
        sys.exit(0)

    if args.polling or os.environ.get("RUN_MODE", "").lower() == "polling":
        run_polling()
    else:
        logger.info("Starting Flask WSGI app on 0.0.0.0:%d", args.port)
        app.run(host="0.0.0.0", port=args.port, debug=False)
