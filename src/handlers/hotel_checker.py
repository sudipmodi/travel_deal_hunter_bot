"""
Hotel Price Checker.
Queries SerpAPI Google Hotels for pricing.
Identifies Accor / Marriott / ITC properties.
Stores prices and triggers alerts on drops.
"""

import os
import json
import logging
import requests
from datetime import datetime
from datetime import timedelta

from db.database import (
    record_hotel_price, check_price_drop, recalculate_baseline,
    check_cooldown
)
from handlers.telegram_alerts import send_alert

logger = logging.getLogger(__name__)

CHAIN_KEYWORDS = {
    "accor": ["novotel", "ibis", "sofitel", "pullman", "mercure", "grand mercure",
              "fairmont", "raffles", "swissotel", "mgallery", "movenpick", "25hours"],
    "marriott": ["marriott", "sheraton", "westin", "w hotel", "courtyard", "jw marriott",
                 "ritz-carlton", "st. regis", "le meridien", "renaissance", "aloft",
                 "four points", "fairfield", "moxy"],
    "itc": ["itc hotels", "welcomhotel", "fortune", "mementos", "itc grand",
            "itc mughal", "itc maratha", "itc gardenia"]
}


def identify_chain(hotel_name: str) -> str:
    name_lower = hotel_name.lower()
    for chain_key, keywords in CHAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return chain_key
    return "independent"


def extract_price(price_str) -> float:
    if isinstance(price_str, (int, float)):
        return float(price_str)
    if isinstance(price_str, str):
        cleaned = price_str.replace(",", "").replace("INR", "").replace("$", "").replace("₹", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


class SerpAPIHotels:
    """SerpAPI Google Hotels search client."""
    
    BASE_URL = "https://serpapi.com/search"
    
    def __init__(self):
        self.api_key = os.environ["SERPAPI_KEY"]
    
    def search(self, query: str, check_in: str, check_out: str,
               adults: int = 2) -> list:
        try:
            resp = requests.get(
                self.BASE_URL,
                params={
                    "engine": "google_hotels",
                    "q": query,
                    "check_in_date": check_in,
                    "check_out_date": check_out,
                    "adults": adults,
                    "currency": "INR",
                    "gl": "in",
                    "hl": "en",
                    "api_key": self.api_key
                },
                timeout=30
            )
            
            if resp.status_code != 200:
                logger.error(f"SerpAPI {resp.status_code}: {resp.text[:200]}")
                return []
            
            return self._parse(resp.json(), check_in, check_out)
        
        except Exception as e:
            logger.error(f"SerpAPI error: {e}")
            return []
    
    def _parse(self, raw: dict, check_in: str, check_out: str) -> list:
        hotels = []
        nights = max((datetime.strptime(check_out, "%Y-%m-%d") - 
                      datetime.strptime(check_in, "%Y-%m-%d")).days, 1)
        
        for prop in raw.get("properties", []):
            try:
                name = prop.get("name", "Unknown")
                chain = identify_chain(name)
                
                rate = prop.get("rate_per_night", {})
                price_str = rate.get("lowest")
                
                if not price_str:
                    total_str = prop.get("total_rate", {}).get("lowest")
                    if total_str:
                        price_val = extract_price(total_str) / nights
                    else:
                        continue
                else:
                    price_val = extract_price(price_str)
                
                if price_val <= 0:
                    continue
                
                hotels.append({
                    "name": name,
                    "chain": chain,
                    "price": round(price_val, 2),
                    "rating": prop.get("overall_rating"),
                    "check_in": check_in,
                    "check_out": check_out,
                    "is_loyalty": chain in ("accor", "marriott", "itc")
                })
            except Exception:
                continue
        
        return hotels


def generate_hotel_dates(dest_type: str, config: dict) -> list:
    w = config["search_windows"][dest_type]
    today = datetime.utcnow().date()
    pairs = []
    
    for weeks_out in [4, 8, 12]:
        ci = today + timedelta(weeks=weeks_out)
        for nights in w["trip_durations"][:2]:
            co = ci + timedelta(days=nights)
            pairs.append({"check_in": ci.isoformat(), "check_out": co.isoformat()})
    
    return pairs[:4]


def run_hotel_check(config: dict) -> dict:
    """Run hotel price check across all destinations."""
    logger.info("Hotel checker started")
    
    serpapi = SerpAPIHotels()
    alerts = []
    prices_recorded = 0
    
    for category in ["domestic", "international"]:
        for dest in config["destinations"][category]:
            logger.info(f"Checking hotels: {dest['name']}")
            
            date_pairs = generate_hotel_dates(category, config)
            
            # Track best by chain
            best_by_chain = {}
            
            for query in dest["hotels_search"]:
                for dp in date_pairs:
                    hotels = serpapi.search(query, dp["check_in"], dp["check_out"],
                                          config["traveler"]["travelers_count"])
                    
                    for hotel in hotels:
                        record_hotel_price(
                            destination_name=dest["name"],
                            hotel_name=hotel["name"],
                            chain=hotel["chain"],
                            price=hotel["price"],
                            rating=hotel["rating"],
                            check_in=dp["check_in"],
                            check_out=dp["check_out"],
                            is_loyalty=hotel["is_loyalty"]
                        )
                        prices_recorded += 1
                        
                        chain = hotel["chain"]
                        if chain not in best_by_chain or hotel["price"] < best_by_chain[chain]["price"]:
                            best_by_chain[chain] = hotel
            
            # Check drops for loyalty chains
            for chain_key in ["accor", "marriott", "itc"]:
                if not dest.get(chain_key):
                    continue
                
                best = best_by_chain.get(chain_key)
                if not best:
                    continue
                
                route_key = f"HOTEL:{dest['name']}:{chain_key}"
                drop = check_price_drop(route_key, best["price"], dest["thresholds"]["drop_pct"])
                
                if drop:
                    alert = {
                        "type": "hotel_drop",
                        "destination_name": dest["name"],
                        "hotel_name": best["name"],
                        "chain": chain_key,
                        "current_price": best["price"],
                        "avg_price": drop["avg_price"],
                        "drop_percent": drop["drop_percent"]
                    }
                    cooldown_key = f"hotel_drop_{dest['name']}_{chain_key}"
                    if check_cooldown(cooldown_key, 12):
                        send_alert(alert)
                        alerts.append(alert)
                
                if best["price"] <= dest["thresholds"]["hotel_night"]:
                    alert = {
                        "type": "hotel_threshold",
                        "destination_name": dest["name"],
                        "hotel_name": best["name"],
                        "chain": chain_key,
                        "current_price": best["price"],
                        "threshold_price": dest["thresholds"]["hotel_night"]
                    }
                    cooldown_key = f"hotel_threshold_{dest['name']}_{chain_key}"
                    if check_cooldown(cooldown_key, 12):
                        send_alert(alert)
                        alerts.append(alert)
                
                recalculate_baseline(route_key, "hotel")
    
    result = {
        "prices_recorded": prices_recorded,
        "alerts_sent": len(alerts),
        "timestamp": datetime.utcnow().isoformat()
    }
    logger.info(f"Hotel checker done: {json.dumps(result)}")
    return result
