"""
Weekly Digest Generator.
Compiles comprehensive weekly summary and sends to Telegram.
"""

import json
import logging
from datetime import datetime

from db.database import (
    get_price_trend, get_recent_offers, get_best_flight_price,
    get_best_hotel_by_chain, get_threshold_override
)
from handlers.telegram_alerts import send_message

logger = logging.getLogger(__name__)


def run_digest(config: dict) -> dict:
    """Generate and send weekly digest."""
    logger.info("Weekly digest started")
    
    origin = config["traveler"]["origin_airport"]
    now = datetime.utcnow()
    
    # ── HEADER ──
    msg = f"""📊 <b>WEEKLY TRAVEL DIGEST</b>
📅 Week of {now.strftime('%B %d %Y')}
👤 2 travelers from {origin}

"""
    
    # ── FLIGHTS ──
    msg += "<b>✈️ FLIGHT PRICES</b>\n\n"
    top_deals = []
    
    for category in ["domestic", "international"]:
        msg += f"<b>{category.upper()}:</b>\n"
        
        for dest in config["destinations"][category]:
            best_price = float("inf")
            best_airport = None
            best_trend = None
            
            for airport in dest["airports"]:
                route = f"{origin}-{airport}"
                trend = get_price_trend(f"FLIGHT:{route}", "flight", 7)
                
                if trend["trend"] != "no_data" and trend["current"] < best_price:
                    best_price = trend["current"]
                    best_airport = airport
                    best_trend = trend
            
            if best_trend:
                emoji = {"falling": "📉", "rising": "📈", "stable": "➡️"}.get(best_trend["trend"], "❓")
                threshold = dest["thresholds"]["flight_rt_pp"]
                target_flag = " 🎯" if best_price <= threshold else ""
                
                msg += f"  {dest['name']} ({best_airport}): ₹{best_price:,.0f}/pp {emoji}{target_flag}\n"
                
                pct = ((best_trend["average"] - best_price) / best_trend["average"]) * 100 if best_trend["average"] > 0 else 0
                if pct > 10:
                    top_deals.append({"dest": dest["name"], "price": best_price, "pct": pct, "type": "flight"})
            else:
                msg += f"  {dest['name']}: No data yet\n"
        
        msg += "\n"
    
    # ── HOTELS ──
    msg += "<b>🏨 HOTEL PRICES</b>\n\n"
    
    for category in ["domestic", "international"]:
        msg += f"<b>{category.upper()}:</b>\n"
        
        for dest in config["destinations"][category]:
            chain_info = []
            for chain_key in ["accor", "marriott", "itc"]:
                if not dest.get(chain_key):
                    continue
                best = get_best_hotel_by_chain(dest["name"], chain_key, 7)
                if best:
                    label = {"accor": "Accor", "marriott": "Marriott", "itc": "ITC"}[chain_key]
                    chain_info.append(f"{label}: ₹{float(best['price']):,.0f}")
            
            if chain_info:
                msg += f"  <b>{dest['name']}:</b> {' | '.join(chain_info)}\n"
            else:
                msg += f"  {dest['name']}: No data\n"
        
        msg += "\n"
    
    # ── TOP DEALS ──
    if top_deals:
        top_deals.sort(key=lambda x: x["pct"], reverse=True)
        msg += "<b>🔥 TOP DEALS THIS WEEK</b>\n\n"
        for deal in top_deals[:5]:
            msg += f"  {deal['dest']}: ₹{deal['price']:,.0f}/pp ({deal['pct']:.0f}% below avg)\n"
        msg += "\n"
    
    # ── OFFERS ──
    offers = get_recent_offers(days=7)
    if offers:
        msg += "<b>🎁 ACTIVE OFFERS</b>\n\n"
        seen = set()
        for offer in offers[:8]:
            title = offer.get("title", "")
            if title and title not in seen:
                source = offer.get("source", "")
                msg += f"  [{source}] {title}\n"
                seen.add(title)
        msg += "\n"
    
    # ── FOOTER ──
    msg += """<i>Commands: /check /flights /hotels /deals /offers /config</i>"""
    
    send_message(msg)
    
    result = {
        "deals_found": len(top_deals),
        "offers_found": len(offers) if offers else 0,
        "timestamp": now.isoformat()
    }
    logger.info(f"Digest sent: {json.dumps(result)}")
    return result
