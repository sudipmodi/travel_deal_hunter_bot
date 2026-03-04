import os
import logging
import requests
import time

from db.database import save_flight_price

logger = logging.getLogger(__name__)

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")
SERPAPI_URL = "https://serpapi.com/search.json"

# protect API quota
REQUEST_DELAY = 6


def search_flights(origin, destination):

    if not SERPAPI_KEY:
        logger.warning("SERPAPI_KEY missing")
        return None

    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": "2025-04-10",
        "currency": "INR",
        "hl": "en",
        "api_key": SERPAPI_KEY
    }

    try:
        r = requests.get(SERPAPI_URL, params=params, timeout=25)

        if r.status_code != 200:
            logger.error(f"SerpAPI error {r.status_code}")
            return None

        data = r.json()

        flights = data.get("best_flights", [])

        if not flights:
            return None

        return flights[0].get("price")

    except Exception as e:
        logger.error(f"SerpAPI request failed: {e}")
        return None


def run_flight_check(config):

    origin = config["traveler"]["origin_airport"]

    for category in ["domestic", "international"]:

        for dest in config["destinations"][category]:

            for airport in dest["airports"]:

                route = f"{origin}-{airport}"

                logger.info(f"Checking {route}")

                price = search_flights(origin, airport)

                if not price:
                    logger.warning(f"No price found for {route}")
                    continue

                save_flight_price(
                    route=route,
                    price=price,
                    airline="unknown",
                    is_direct=False,
                    stops=0
                )

                logger.info(f"Saved price ₹{price} for {route}")

                # rate limit
                time.sleep(REQUEST_DELAY)
