"""
Flight Price Checker.
Primary: fast-flights (free unlimited Google Flights scraper)
Fallback: SerpAPI Google Flights engine (100 free/month)

Checks all configured routes from AMD and stores prices in Supabase.
Triggers alerts on drops and threshold breaches.
"""

import os
import json
import logging
import time
import re
import requests
from datetime import datetime
from datetime import timedelta

from db.database import (
    record_flight_price, check_price_drop, recalculate_baseline,
    check_cooldown
)
from handlers.telegram_alerts import send_alert

logger = logging.getLogger(__name__)


# ============================================================
# PRIMARY: fast-flights (free / unlimited)
# ============================================================

class FastFlightsClient:
    """Google Flights scraper using fast-flights library."""

    def search(self, origin: str, destination: str, depart: str,
               ret: str, adults: int = 2) -> list:
        try:
            from fast_flights import FlightQuery, Passengers, create_query, get_flights

            query = create_query(
                flights=[
                    FlightQuery(date=depart, from_airport=origin, to_airport=destination),
                    FlightQuery(date=ret, from_airport=destination, to_airport=origin),
                ],
                seat="economy",
                trip="round-trip",
                passengers=Passengers(adults=adults),
                language="en-US"
            )

            result = get_flights(query)
            return self._parse(result, adults, depart, ret)

        except ImportError:
            logger.error("fast-flights not installed")
            return []
        except Exception as e:
            logger.error(f"fast-flights error for {origin}-{destination}: {e}")
            return []

    def _parse(self, result, adults: int, depart: str, ret: str) -> list:
        offers = []
        if not result or not hasattr(result, "flights"):
            return offers

        for flight in result.flights:
            try:
                price_raw = getattr(flight, "price", None)
                if not price_raw:
                    continue

                price_total = self._extract_price(str(price_raw))
                if price_total <= 0:
                    continue

                price_pp = round(price_total / adults, 2)
                airline = str(getattr(flight, "airline", "Unknown"))

                stops = 0
                if hasattr(flight, "legs") and flight.legs:
                    stops = len(flight.legs) - 1
                else:
                    raw_stops = getattr(flight, "stops", 0)
                    if isinstance(raw_stops, str):
                        if "nonstop" in raw_stops.lower() or raw_stops == "0":
                            stops = 0
                        else:
                            try:
                                stops = int(raw_stops.split()[0])
                            except (ValueError, IndexError):
                                stops = 1
                    else:
                        stops = int(raw_stops) if raw_stops else 0

                offers.append({
                    "price_pp": price_pp,
                    "price_total": price_total,
                    "airline": airline,
                    "stops": stops,
                    "is_direct": stops == 0,
                    "duration": str(getattr(flight, "duration", "")),
                    "booking_class": "ECONOMY",
                    "departure_date": depart,
                    "return_date": ret,
                    "source": "fast-flights"
                })

            except Exception as e:
                logger.warning(f"fast-flights parse error: {e}")
        return offers

    def _extract_price(self, price_str: str) -> float:
        if not price_str:
            return 0.0
        cleaned = price_str.replace(",", "").replace("₹", "").replace("$", "")
        cleaned = cleaned.replace("INR", "").replace("USD", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            nums = re.findall(r'[\d.]+', cleaned)
            return float(nums[0]) if nums else 0.0


# ============================================================
# FALLBACK: SerpAPI Google Flights
# ============================================================

class SerpAPIFlightsClient:
    """SerpAPI Google Flights engine as fallback."""

    BASE_URL = "https://serpapi.com/search"

    def __init__(self):
        self.api_key = os.environ.get("SERPAPI_KEY", "")

    def search(self, origin: str, destination: str, depart: str,
               ret: str, adults: int = 2) -> list:
        if not self.api_key:
            return []

        try:
            resp = requests.get(
                self.BASE_URL,
                params={
                    "engine": "google_flights",
                    "departure_id": origin,
                    "arrival_id": destination,
                    "outbound_date": depart,
                    "return_date": ret,
                    "adults": adults,
                    "currency": "INR",
                    "hl": "en",
                    "gl": "in",
                    "type": "1",
                    "api_key": self.api_key
                },
                timeout=30
            )
            if resp.status_code != 200:
                logger.error(f"SerpAPI Flights {resp.status_code}")
                return []
            return self._parse(resp.json(), adults, depart, ret)

        except Exception as e:
            logger.error(f"SerpAPI Flights error: {e}")
            return []

    def _parse(self, raw: dict, adults: int, depart: str, ret: str) -> list:
        offers = []
        all_flights = raw.get("best_flights", []) + raw.get("other_flights", [])

        for group in all_flights:
            try:
                price = group.get("price")
                if not price:
                    continue

                price_total = float(price)
                price_pp = round(price_total / adults, 2)

                flights = group.get("flights", [])
                if not flights:
                    continue

                airlines = set()
                for f in flights:
                    al = f.get("airline", "")
                    if al:
                        airlines.add(al)

                stops = len(flights) - 1
                td = group.get("total_duration", 0)
                dur = f"{td // 60}h {td % 60}m" if td else ""

                offers.append({
                    "price_pp": price_pp,
                    "price_total": price_total,
                    "airline": " + ".join(airlines) if airlines else "Unknown",
                    "stops": stops,
                    "is_direct": stops == 0,
                    "duration": dur,
                    "booking_class": flights[0].get("travel_class", "ECONOMY"),
                    "departure_date": depart,
                    "return_date": ret,
                    "source": "serpapi"
                })
            except Exception as e:
                logger.warning(f"SerpAPI parse error: {e}")
        return offers


# ============================================================
# UNIFIED SEARCH WITH FALLBACK
# ============================================================

def search_with_fallback(primary: FastFlightsClient, fallback: SerpAPIFlightsClient,
                         origin: str, destination: str, depart: str, ret: str,
                         adults: int, budget: dict) -> tuple:
    """Try primary first then fallback. Returns (offers, source)."""

    offers = primary.search(origin, destination, depart, ret, adults)
    if offers:
        return offers, "fast-flights"

    if budget["remaining"] <= 0:
        logger.warning(f"SerpAPI budget exhausted. No fallback for {origin}-{destination}")
        return [], "none"

    logger.info(f"fast-flights failed for {origin}-{destination}. Trying SerpAPI.")
    offers = fallback.search(origin, destination, depart, ret, adults)
    if offers:
        budget["remaining"] -= 1
        budget["used"] += 1
        return offers, "serpapi"

    return [], "none"


# ============================================================
# MAIN CHECKER
# ============================================================

def generate_date_pairs(dest_type: str, config: dict) -> list:
    w = config["search_windows"][dest_type]
    today = datetime.utcnow().date()
    pairs = []
    for advance in range(w["advance_days_min"], min(w["advance_days_max"], 180), 21):
        dep = today + timedelta(days=advance)
        for dur in w["trip_durations"][:2]:
            ret_date = dep + timedelta(days=dur)
            pairs.append({"depart": dep.isoformat(), "return": ret_date.isoformat(), "nights": dur})
    return pairs[:6]


def run_flight_check(config: dict) -> dict:
    """Run flight price check across all routes."""
    logger.info("Flight checker started")

    primary = FastFlightsClient()
    fallback = SerpAPIFlightsClient()
    origin = config["traveler"]["origin_airport"]
    travelers = config["traveler"]["travelers_count"]

    # Reserve ~15 SerpAPI calls per run for flight fallback
    budget = {"remaining": 15, "used": 0}
    alerts = []
    prices_recorded = 0
    sources = {"fast-flights": 0, "serpapi": 0, "none": 0}

    for category in ["domestic", "international"]:
        for dest in config["destinations"][category]:
            for airport in dest["airports"]:
                route = f"{origin}-{airport}"
                logger.info(f"Checking {route} ({dest['name']})")

                date_pairs = generate_date_pairs(category, config)
                best_price = float("inf")
                best_offer = None

                for dp in date_pairs:
                    offers, src = search_with_fallback(
                        primary, fallback, origin, airport,
                        dp["depart"], dp["return"], travelers, budget
                    )
                    sources[src] = sources.get(src, 0) + 1

                    for offer in offers:
                        record_flight_price(
                            route=route,
                            destination_name=dest["name"],
                            price=offer["price_pp"],
                            airline=offer["airline"],
                            stops=offer["stops"],
                            is_direct=offer["is_direct"],
                            departure_date=dp["depart"],
                            return_date=dp["return"],
                            duration=offer["duration"],
                            booking_class=offer["booking_class"],
                            metadata={"source": offer["source"]}
                        )
                        prices_recorded += 1

                        if offer["price_pp"] < best_price:
                            best_price = offer["price_pp"]
                            best_offer = offer

                    time.sleep(2)  # Rate limiting

                # ── ALERT EVALUATION ──
                if best_offer and best_price < float("inf"):
                    route_key = f"FLIGHT:{route}"

                    drop = check_price_drop(route_key, best_price, dest["thresholds"]["drop_pct"])
                    if drop:
                        alert = {
                            "type": "flight_drop",
                            "destination_name": dest["name"],
                            "route": route,
                            "current_price": best_price,
                            "avg_price": drop["avg_price"],
                            "drop_percent": drop["drop_percent"],
                            "threshold_price": dest["thresholds"]["flight_rt_pp"],
                            "best_offer": best_offer
                        }
                        if check_cooldown(f"flight_drop_{route}", 12):
                            send_alert(alert)
                            alerts.append(alert)

                    if best_price <= dest["thresholds"]["flight_rt_pp"]:
                        alert = {
                            "type": "flight_threshold",
                            "destination_name": dest["name"],
                            "route": route,
                            "current_price": best_price,
                            "threshold_price": dest["thresholds"]["flight_rt_pp"],
                            "best_offer": best_offer
                        }
                        if check_cooldown(f"flight_threshold_{route}", 12):
                            send_alert(alert)
                            alerts.append(alert)

                    recalculate_baseline(route_key, "flight")

    result = {
        "prices_recorded": prices_recorded,
        "alerts_sent": len(alerts),
        "sources": sources,
        "serpapi_used": budget["used"],
        "timestamp": datetime.utcnow().isoformat()
    }
    logger.info(f"Flight checker done: {json.dumps(result)}")
    return result
