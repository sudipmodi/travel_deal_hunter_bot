import os
import logging
import requests
from datetime import datetime

from db.database import save_flight_price

logger = logging.getLogger(__name__)

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")


def search_flights(origin, destination):

    if not SERPAPI_KEY:
        logger.warning("SERPAPI_KEY missing")
        return None

    url = "https://serpapi.com/search.json"

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
        r = requests.get(url, params=params, timeout=20)

        if r.status_code != 200:
            logger.error(f"SerpAPI error {r.status_code}")
            return None

        data = r.json()

        flights = data.get("best_flights", [])

        if not flights:
            return None

        price = flights[0].get("price")

        return price

    except Exception as e:
        logger.error(f"SerpAPI request failed: {e}")
        return None


def run_flight_check(config):

    origin = config["traveler"]["origin_airport"]

    for cat in ["domestic", "international"]:

        for dest in config["destinations"][cat]:

            for airport in dest["airports"]:

                logger.info(f"Checking {origin}-{airport}")

                price = search_flights(origin, airport)

                if not price:
                    logger.warning(f"No price found for {airport}")
                    continue

                save_flight_price(
                    route=f"{origin}-{airport}",
                    price=price,
                    airline="unknown",
                    is_direct=False,
                    stops=0
                )

                logger.info(f"Saved price ₹{price} for {airport}")
