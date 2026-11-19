"""
iti_api.py – Async REST API client for the ITI Bus Scheduling system.
Base URL: https://schedule.iti.gov.eg/api/v1
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import aiohttp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://schedule.iti.gov.eg/api/v1"

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# Arabic tashkeel (diacritics) Unicode block range
_TASHKEEL_RE = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]"
)
# Leading digits (and optional Arabic-Indic digits) + spaces
_LEADING_NUMS_RE = re.compile(r"^[\d\u0660-\u0669]+\s*")


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class ITIAuthError(Exception):
    """Raised when login or token refresh fails."""


class ITIBookingError(Exception):
    """Raised when a trip booking request fails."""

    def __init__(self, message: str, http_status: int) -> None:
        super().__init__(message)
        self.http_status = http_status


# ---------------------------------------------------------------------------
# Arabic text normalization
# ---------------------------------------------------------------------------


def normalize_stop(text: str) -> str:
    """Normalize an Arabic stop name for fuzzy matching.

    Steps:
    1. Strip leading digits (ASCII + Arabic-Indic) and spaces.
    2. Normalize ي/ى  →  ي  and  أ/إ/آ  →  ا.
    3. Strip Arabic tashkeel (diacritics).
    4. Return lowercased result (useful for mixed-script names).
    """
    if not text:
        return text

    # 1. Strip leading numbers + spaces
    text = _LEADING_NUMS_RE.sub("", text)

    # 2. Normalize alef variants → bare alef, ya variants → ya
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي")

    # 3. Strip tashkeel
    text = _TASHKEEL_RE.sub("", text)

    # 4. Lowercase (affects Latin characters in mixed names)
    return text.lower()


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


async def get_all_routes(
    session: aiohttp.ClientSession, access_token: str
) -> list[dict[str, Any]]:
    """Fetch all available bus routes from the ITI API.

    GET /students/routes

    Returns a list of dicts, each with keys:
        id, name, routeCode, startLocation
    """
    url = f"{BASE_URL}/students/routes"
    headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {access_token}"}

    async with session.get(url, headers=headers) as resp:
        if resp.status == 401:
            raise ITIAuthError("Access token expired or invalid while fetching routes.")
        resp.raise_for_status()
        data = await resp.json()

    routes_raw: list[dict] = data if isinstance(data, list) else data.get("data", [])

    return [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "routeCode": r.get("routeCode"),
            "startLocation": r.get("startLocation"),
        }
        for r in routes_raw
    ]


# ---------------------------------------------------------------------------
# Main API client class
# ---------------------------------------------------------------------------


class ITIApiClient:
    """Async client for the ITI Bus Scheduling REST API.

    Usage::

        async with aiohttp.ClientSession() as session:
            client = ITIApiClient(BASE_URL, email, password)
            tokens = await client.login(session)
            trips  = await client.get_trips(session, tokens["accessToken"], "2024-01-15")
    """

    def __init__(self, base_url: str, email: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self, session: aiohttp.ClientSession) -> dict[str, Any]:
        """Authenticate with the ITI API and return token data.

        POST /auth/login

        Returns:
            dict with keys: ``accessToken``, ``refreshToken``, ``fullName``

        Raises:
            ITIAuthError: If credentials are rejected or the response is
                          malformed.
        """
        url = f"{self.base_url}/auth/login"
        payload = {"email": self.email, "password": self.password}

        async with session.post(url, json=payload, headers=DEFAULT_HEADERS) as resp:
            if resp.status in (401, 403, 422):
                body = await resp.text()
                raise ITIAuthError(
                    f"Login failed (HTTP {resp.status}): {body[:200]}"
                )
            resp.raise_for_status()
            data = await resp.json()

        # Support both flat and nested { data: {...} } response shapes
        if "data" in data:
            data = data["data"]

        access_token = data.get("accessToken") or data.get("access_token")
        refresh_token = data.get("refreshToken") or data.get("refresh_token")
        full_name = (
            data.get("fullName")
            or data.get("full_name")
            or data.get("name")
            or ""
        )

        if not access_token:
            raise ITIAuthError("Login response missing accessToken.")

        return {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "fullName": full_name,
        }

    async def refresh(
        self, session: aiohttp.ClientSession, refresh_token: str
    ) -> str:
        """Refresh the access token using a refresh token.

        POST /auth/refresh

        Returns:
            New ``access_token`` string.

        Raises:
            ITIAuthError: If the refresh token is rejected.
        """
        url = f"{self.base_url}/auth/refresh"
        payload = {"refreshToken": refresh_token}

        async with session.post(url, json=payload, headers=DEFAULT_HEADERS) as resp:
            if resp.status in (401, 403):
                body = await resp.text()
                raise ITIAuthError(
                    f"Token refresh failed (HTTP {resp.status}): {body[:200]}"
                )
            resp.raise_for_status()
            data = await resp.json()

        if "data" in data:
            data = data["data"]

        access_token = data.get("accessToken") or data.get("access_token")
        if not access_token:
            raise ITIAuthError("Refresh response missing accessToken.")

        return access_token

    # ------------------------------------------------------------------
    # Trip data
    # ------------------------------------------------------------------

    async def get_trips(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        date_str: str,
    ) -> list[dict[str, Any]]:
        """Fetch available trips for a given date.

        GET /trips/available?date=<date_str>

        Args:
            date_str: ISO-8601 date string, e.g. ``"2024-01-15"``.

        Returns:
            List of trip dicts, each with keys:
                ``id``, ``routeId``, ``routeName``, ``availableSeats``,
                ``totalCapacity``, ``busNumber``, ``status``,
                ``departureTime``, ``isReturnTrip``.

        Raises:
            ITIAuthError: On HTTP 401.
        """
        url = f"{self.base_url}/trips/available"
        headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {access_token}"}
        params = {"date": date_str}

        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status == 401:
                raise ITIAuthError("Access token expired or invalid.")
            resp.raise_for_status()
            data = await resp.json()

        trips_raw: list[dict] = data if isinstance(data, list) else data.get("data", [])

        return [
            {
                "id": t.get("id"),
                "routeId": t.get("routeId") or t.get("route_id"),
                "routeName": t.get("routeName") or t.get("route_name"),
                "availableSeats": t.get("availableSeats") or t.get("available_seats"),
                "totalCapacity": t.get("totalCapacity") or t.get("total_capacity"),
                "busNumber": t.get("busNumber") or t.get("bus_number"),
                "status": t.get("status"),
                "departureTime": t.get("departureTime") or t.get("departure_time"),
                "isReturnTrip": t.get("isReturnTrip") or t.get("is_return_trip", False),
            }
            for t in trips_raw
        ]

    async def get_route_stops(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        route_id: int | str,
    ) -> list[dict[str, Any]]:
        """Fetch the ordered list of stops for a given route.

        GET /students/routes/<route_id>/stops

        Returns:
            List of stop dicts, each with keys: ``id``, ``stopName``,
            ``stopOrder``.
        """
        url = f"{self.base_url}/students/routes/{route_id}/stops"
        headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {access_token}"}

        async with session.get(url, headers=headers) as resp:
            if resp.status == 401:
                raise ITIAuthError("Access token expired or invalid.")
            resp.raise_for_status()
            data = await resp.json()

        stops_raw: list[dict] = data if isinstance(data, list) else data.get("data", [])

        return [
            {
                "id": s.get("id"),
                "stopName": s.get("stopName") or s.get("stop_name") or s.get("name"),
                "stopOrder": s.get("stopOrder") or s.get("stop_order") or s.get("order"),
            }
            for s in stops_raw
        ]

    # ------------------------------------------------------------------
    # Booking
    # ------------------------------------------------------------------

    async def book_trip(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        trip_id: int | str,
        pickup_stop_id: int | str,
    ) -> dict[str, Any]:
        """Book a seat on a trip at the specified pickup stop.

        POST /students/bookings

        Returns:
            dict with keys: ``success``, ``bookingCode``, ``routeName``,
            ``busNumber``, ``pickupStopName``, ``departureTime``.

        Raises:
            ITIAuthError:    On HTTP 401.
            ITIBookingError: On any other booking failure (4xx/5xx).
        """
        url = f"{self.base_url}/bookings"
        headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {access_token}"}
        payload = {
            "tripId": trip_id,
            "pickupStopId": pickup_stop_id,
            "isTemporaryRoute": False,
        }

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 401:
                raise ITIAuthError("Access token expired or invalid during booking.")

            # Attempt to parse JSON for a richer error message
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {}

            if not resp.ok:
                message = (
                    data.get("message")
                    or data.get("error")
                    or f"Booking failed with HTTP {resp.status}"
                )
                raise ITIBookingError(str(message), resp.status)

        if "data" in data:
            data = data["data"]

        return {
            "success": True,
            "bookingCode": (
                data.get("bookingCode")
                or data.get("booking_code")
                or data.get("code")
            ),
            "routeName": data.get("routeName") or data.get("route_name"),
            "busNumber": data.get("busNumber") or data.get("bus_number"),
            "pickupStopName": (
                data.get("pickupStopName")
                or data.get("pickup_stop_name")
                or data.get("stopName")
            ),
            "departureTime": data.get("departureTime") or data.get("departure_time"),
        }
