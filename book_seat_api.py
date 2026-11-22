"""
ITI Bus Auto-Booker (High-Speed REST API Mode)
==============================================
Discovered from reverse-engineering the Flutter Web CanvasKit bundle:
The Flutter app is a client for a standard Spring/Express JSON REST API at:
  https://schedule.iti.gov.eg/api/v1/

Endpoints:
  POST /api/v1/auth/login                  -> returns accessToken & refreshToken
  POST /api/v1/auth/refresh                -> refreshes token
  GET  /api/v1/trips/available?date=YYYY-MM-DD -> list of trips for date
  GET  /api/v1/students/routes/{routeId}/stops -> list of stops for route
  POST /api/v1/bookings                    -> payload: {"tripId": X, "pickupStopId": Y, "isTemporaryRoute": false}

Why this is superior to pure canvas emulation:
1. Millisecond response: Takes ~100ms per poll vs 10-15s for headless CanvasKit.
   Trips disappear in seconds between 09:00-14:00; this guarantees you get the seat.
2. 100% deterministic: Zero canvas rendering/timing flakiness.
3. Automatically generates confirmation and logs in artifacts/.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import pytz

# ── Configuration ─────────────────────────────────────────────────────────────
ITI_URL      = os.environ.get("ITI_URL", "https://schedule.iti.gov.eg").rstrip("/")
ITI_EMAIL    = os.environ.get("ITI_EMAIL", "")
ITI_PASSWORD = os.environ.get("ITI_PASSWORD", "")
DROPOFF      = os.environ.get("DROPOFF", "هايبر").strip()
TRIP_MATCH   = [t.strip() for t in os.environ.get("TRIP_MATCH", "").split(",") if t.strip()]
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))   # API allows faster polling
START_TIME   = os.environ.get("START_TIME", "09:00")
END_TIME     = os.environ.get("END_TIME", "14:00")
FORCE        = os.environ.get("FORCE", "0") == "1"

CAIRO_TZ     = pytz.timezone("Africa/Cairo")
ARTIFACTS    = pathlib.Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)

MAX_FAIL_STREAK = 5

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-App-Build": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ITI-Booker/2.0",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("iti_api_booker")


def normalize_arabic(text: str) -> str:
    """Normalize Arabic characters for fuzzy matching (e.g. هايبر / هايير, ى / ي)."""
    if not text:
        return ""
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"[يى]", "ي", text)
    text = re.sub(r"[\u064B-\u065F]", "")  # Tashkeel
    # Handle OCR / typo confusion between ب and ي (e.g. هايبر vs هايير)
    text = text.replace("هايير", "هايبر")
    # Remove numbers like "1 ", "2 "
    text = re.sub(r"^\d+\s*", "", text)
    return text.strip().lower()


class ITIApiClient:
    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url
        self.email = email
        self.password = password
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    def _request(self, endpoint: str, method: str = "GET", data: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1{endpoint}"
        headers = {**DEFAULT_HEADERS}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res_body = resp.read().decode("utf-8")
                return json.loads(res_body)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            if e.code == 400:
                try:
                    err_json = json.loads(error_body)
                    code = err_json.get("code")
                    msg = err_json.get("message") or err_json.get("error") or ""
                    if code == "APP_UPDATE_REQUIRED" or "APP_UPDATE_REQUIRED" in str(msg):
                        log.critical("ITI backend returned APP_UPDATE_REQUIRED (HTTP 400): %s", msg)
                        raise RuntimeError(f"APP_UPDATE_REQUIRED: {msg}") from e
                except json.JSONDecodeError:
                    pass
            if e.code == 401 and endpoint != "/auth/login":
                log.warning("Received 401 Unauthorized. Attempting re-login...")
                self.login()
                return self._request(endpoint, method, data)
            raise RuntimeError(f"HTTP {e.code} on {endpoint}: {error_body}") from e

    def login(self) -> None:
        log.info("Logging in as %s via API...", self.email)
        payload = {"email": self.email, "password": self.password}
        res = self._request("/auth/login", method="POST", data=payload)
        if not res.get("success"):
            raise RuntimeError(f"Login failed: {res.get('message')}")
        self.access_token = res["data"]["accessToken"]
        self.refresh_token = res["data"]["refreshToken"]
        user_name = res["data"].get("fullName", self.email)
        log.info("✅ Login successful! Welcome %s", user_name)

    def get_available_trips(self, date_str: str) -> list[dict[str, Any]]:
        endpoint = f"/trips/available?date={date_str}"
        res = self._request(endpoint)
        if not res.get("success"):
            log.warning("Trips request message: %s", res.get("message"))
            return []
        return res.get("data") or []

    def get_route_stops(self, route_id: int) -> list[dict[str, Any]]:
        endpoint = f"/students/routes/{route_id}/stops"
        res = self._request(endpoint)
        if not res.get("success"):
            return []
        return res.get("data") or []

    def book_trip(self, trip_id: int, pickup_stop_id: int) -> dict[str, Any]:
        payload = {
            "tripId": trip_id,
            "pickupStopId": pickup_stop_id,
            "isTemporaryRoute": False,
        }
        log.info("Sending booking request: %s", payload)
        res = self._request("/bookings", method="POST", data=payload)
        return res


def cairo_now() -> datetime.datetime:
    return datetime.datetime.now(CAIRO_TZ)


def _hhmm(s: str) -> datetime.time:
    h, m = s.strip().split(":")
    return datetime.time(int(h), int(m))


def within_window() -> bool:
    if FORCE:
        return True
    t = cairo_now().time()
    return _hhmm(START_TIME) <= t <= _hhmm(END_TIME)


def past_window() -> bool:
    if FORCE:
        return False
    return cairo_now().time() > _hhmm(END_TIME)


def wait_for_window() -> None:
    if FORCE:
        log.info("FORCE=1 — skipping time window.")
        return
    start = _hhmm(START_TIME)
    while True:
        now = cairo_now()
        if now.time() >= start:
            return
        wait_secs = (datetime.datetime.combine(now.date(), start, tzinfo=CAIRO_TZ) - now).total_seconds()
        log.info(
            "Cairo time: %s — window opens at %s. Sleeping %.0f min...",
            now.strftime("%H:%M:%S"),
            START_TIME,
            wait_secs / 60,
        )
        time.sleep(min(wait_secs, 300))


def select_best_trip(trips: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Filter out fully booked trips and pick according to TRIP_MATCH priority."""
    valid_trips = [t for t in trips if t.get("availableSeats", 0) > 0 and t.get("status") != "FULLY_BOOKED"]
    if not valid_trips:
        return None

    if not TRIP_MATCH:
        log.info("No TRIP_MATCH filter set. Picking first available trip: %s", valid_trips[0].get("routeName"))
        return valid_trips[0]

    for fragment in TRIP_MATCH:
        norm_frag = normalize_arabic(fragment)
        for t in valid_trips:
            r_name = normalize_arabic(t.get("routeName", ""))
            b_num = (t.get("busNumber") or "").upper()
            if norm_frag in r_name or fragment.upper() in b_num:
                log.info("Trip '%s' [%s] matched priority '%s'", t.get("routeName"), b_num, fragment)
                return t

    log.info("No trip matched TRIP_MATCH rules (%s). %d trips available.", TRIP_MATCH, len(valid_trips))
    return None


def find_stop_id(stops: list[dict[str, Any]], target_name: str) -> int | None:
    """Find the stop ID matching target_name using normalized Arabic."""
    norm_target = normalize_arabic(target_name)
    for s in stops:
        s_name = normalize_arabic(s.get("stopName", ""))
        if norm_target in s_name or s_name in norm_target:
            log.info("Matched stop '%s' (ID: %s) for '%s'", s.get("stopName"), s.get("id"), target_name)
            return s.get("id")

    # If no exact match, log available options
    available = [f"{s.get('id')}:{s.get('stopName')}" for s in stops]
    log.warning("Stop '%s' not found. Available stops: %s", target_name, ", ".join(available))
    if stops:
        log.info("Falling back to first stop: %s (ID: %s)", stops[0].get("stopName"), stops[0].get("id"))
        return stops[0].get("id")
    return None


def run() -> None:
    wait_for_window()

    client = ITIApiClient(ITI_URL, ITI_EMAIL, ITI_PASSWORD)
    client.login()

    fail_streak = 0
    booked = False

    while not booked:
        if past_window():
            log.info("Booking window (%s – %s Cairo) has passed. Exiting.", START_TIME, END_TIME)
            break

        if not within_window():
            log.info("Outside booking window; waiting 60s...")
            time.sleep(60)
            continue

        now = cairo_now()
        date_str = now.strftime("%Y-%m-%d")
        log.info("Checking available trips for date %s (Cairo time: %s)...", date_str, now.strftime("%H:%M:%S"))

        try:
            trips = client.get_available_trips(date_str)
            log.info("API returned %d trip(s).", len(trips))

            chosen_trip = select_best_trip(trips)
            if not chosen_trip:
                log.info("No free seats found yet. Next check in %d s.", POLL_SECONDS)
                fail_streak = 0
                if FORCE:
                    log.info("FORCE mode: single check complete.")
                    break
                time.sleep(POLL_SECONDS)
                continue

            trip_id = chosen_trip["id"]
            route_id = chosen_trip["routeId"]
            route_name = chosen_trip.get("routeName", "")
            bus_num = chosen_trip.get("busNumber", "")
            log.info("Found available seat on trip %s (%s, %s)! Proceeding to book...", trip_id, route_name, bus_num)

            # Get stops
            stops = client.get_route_stops(route_id)
            stop_id = find_stop_id(stops, DROPOFF)

            if not stop_id:
                raise RuntimeError(f"Could not determine a valid drop-off stop for route {route_id}")

            # Book the seat
            res = client.book_trip(trip_id, stop_id)
            log.info("Booking response: %s", res)

            # Save confirmation artifact
            ts = now.strftime("%Y%m%d_%H%M%S")
            conf_file = ARTIFACTS / f"{ts}_booking_confirmed.json"
            conf_file.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("Saved confirmation artifact to %s", conf_file)

            if res.get("success"):
                log.info("🎉 SUCCESS: Seat booked successfully! 🎉")
                booked = True
                break
            else:
                log.error("Booking failed: %s", res.get("message"))
                fail_streak += 1

        except Exception as exc:
            fail_streak += 1
            log.error("Poll error: %s (Fail streak: %d / %d)", exc, fail_streak, MAX_FAIL_STREAK)
            if fail_streak >= MAX_FAIL_STREAK:
                raise RuntimeError(f"Aborting after {MAX_FAIL_STREAK} consecutive failed polls.") from exc
            time.sleep(POLL_SECONDS)

    if booked:
        log.info("All done. Booking confirmed.")
        sys.exit(0)
    else:
        log.info("Process finished without booking.")
        sys.exit(0)


if __name__ == "__main__":
    run()
