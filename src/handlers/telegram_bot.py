"""
Telegram Bot Webhook Handler.
Flask app that receives Telegram webhook updates and processes commands.

Commands:
  /start - Help and command list
  /check <dest> - Flights + hotels for destination
  /flights <dest> - Flight prices for destination
  /hotels <dest> - Hotel prices for destination
  /deals - Best current deals across all destinations
  /offers - Active loyalty and CC portal offers
  /trends <dest> - 30 day price trend
  /config - View alert thresholds
  /set <dest> <flight> <hotel> - Update thresholds
  /status - Bot health
"""

import os
import json
import logging

from flask import Flask, request, jsonify

from db.database import (
    get_price_trend, get_flight_history, get_hotel_history,
    get_best_flight_price, get_best_hotel_by_chain, get_recent_offers,
    get_threshold_override, set_threshold_override
)
from handlers.telegram_alerts import send_message

logger = logging.getLogger(__name__)

app = Flask(__name__)

CONFIG = None


def load_config():
    global CONFIG
    if CONFIG is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "config.json")
        with open(config_path) as f:
            CONFIG = json.load(f)
    return CONFIG


def resolve_destination(name: str, config: dict):
    """Fuzzy match destination name."""
    name_lower = name.lower().strip()
    for cat in ["domestic", "international"]:
        for dest in config["destinations"][cat]:
            if name_lower in dest["name"].lower():
                return dest, cat
            for ap in dest["airports"]:
                if name_lower == ap.lower():
                    return dest, cat
    return None, None


@app.route("/webhook", methods=["POST"])
def webhook():
    """Telegram webhook endpoint."""
    body = request.get_json(force=True, silent=True) or {}
    message = body.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip()
    
    if not chat_id or not text:
        return jsonify({"ok": True})
    
    # Auth check
    allowed = os.environ.get("TELEGRAM_CHAT_ID", "")
    if chat_id != allowed:
        return jsonify({"ok": True})
    
    config = load_config()
    origin = config["traveler"]["origin_airport"]
    
    parts = text.split()
    cmd = parts[0].lower() if parts and parts[0].startswith("/") else ""
    args = parts[1:] if len(parts) > 1 else []
    arg_str = " ".join(args)
    
    try:
        if cmd == "/start":
            _cmd_start()
        elif cmd == "/check":
            _cmd_check(arg_str, config, origin)
        elif cmd == "/flights":
            _cmd_flights(arg_str, config, origin)
        elif cmd == "/hotels":
            _cmd_hotels(arg_str, config)
        elif cmd == "/deals":
            _cmd_deals(config, origin)
        elif cmd == "/offers":
            _cmd_offers()
        elif cmd == "/trends":
            _cmd_check(arg_str, config, origin)  # same view
        elif cmd == "/config":
            _cmd_config(config)
        elif cmd in ("/set", "/setthreshold"):
            _cmd_set(args, config)
        elif cmd == "/status":
            _cmd_status()
        else:
            send_message("Unknown command. Send /start for help.")
    except Exception as e:
        logger.error(f"Command error: {e}")
        send_message(f"Error: {str(e)[:200]}")
    
    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def _cmd_start():
    send_message("""
<b>Travel Deal Hunter Bot</b>

Monitoring flights + hotels + loyalty deals across 13 destinations from AMD.

<b>Commands:</b>
/check &lt;dest&gt; - Full price check
/flights &lt;dest&gt; - Flight prices
/hotels &lt;dest&gt; - Hotel prices
/deals - Best deals now
/offers - Loyalty + CC offers
/config - Alert thresholds
/set &lt;dest&gt; &lt;flight&gt; &lt;hotel&gt; - Update thresholds
/status - Health check

<b>Destinations:</b>
Kerala | Hyderabad | Tamil Nadu | Karnataka | Assam | Odisha
Japan | London | Singapore | Bali | Indonesia | Vietnam | UAE

<b>Alerts:</b> Weekly digest Sunday 9AM + instant on big drops
""".strip())


def _cmd_check(arg_str: str, config: dict, origin: str):
    if not arg_str:
        send_message("Usage: /check &lt;destination&gt;\nExample: /check japan")
        return
    
    dest, cat = resolve_destination(arg_str, config)
    if not dest:
        send_message(f"'{arg_str}' not found. Try /start for list.")
        return
    
    msg = f"<b>{dest['name']}</b> from {origin}\n\n<b>Flights:</b>\n"
    
    for ap in dest["airports"]:
        route = f"{origin}-{ap}"
        trend = get_price_trend(f"FLIGHT:{route}", "flight", 30)
        
        if trend["trend"] == "no_data":
            msg += f"  {ap}: No data\n"
        else:
            emoji = {"falling": "📉", "rising": "📈", "stable": "➡️"}.get(trend["trend"], "❓")
            msg += f"  {ap}: ₹{trend['current']:,.0f}/pp {emoji}\n"
            msg += f"    Avg: ₹{trend['average']:,.0f} | Range: ₹{trend['min']:,.0f}-₹{trend['max']:,.0f}\n"
    
    msg += "\n<b>Hotels:</b>\n"
    for chain_key in ["accor", "marriott", "itc"]:
        if not dest.get(chain_key):
            continue
        label = {"accor": "Accor", "marriott": "Marriott", "itc": "ITC"}[chain_key]
        best = get_best_hotel_by_chain(dest["name"], chain_key, 7)
        if best:
            msg += f"  {label}: ₹{float(best['price']):,.0f}/night ({best['hotel_name'][:30]})\n"
        else:
            msg += f"  {label}: No data\n"
    
    override = get_threshold_override(dest["name"])
    t = override or dest["thresholds"]
    flight_t = t.get("flight_threshold", t.get("flight_rt_pp", "N/A"))
    hotel_t = t.get("hotel_threshold", t.get("hotel_night", "N/A"))
    
    msg += f"\n<b>Thresholds:</b> Flight ₹{flight_t:,}/pp | Hotel ₹{hotel_t:,}/night"
    
    send_message(msg)


def _cmd_flights(arg_str: str, config: dict, origin: str):
    if not arg_str:
        send_message("Usage: /flights &lt;destination&gt;")
        return
    
    dest, cat = resolve_destination(arg_str, config)
    if not dest:
        send_message(f"'{arg_str}' not found.")
        return
    
    msg = f"<b>Flights to {dest['name']}</b> from {origin}\n\n"
    
    for ap in dest["airports"]:
        route = f"{origin}-{ap}"
        msg += f"<b>{origin} → {ap}:</b>\n"
        
        trend = get_price_trend(f"FLIGHT:{route}", "flight", 30)
        if trend["trend"] == "no_data":
            msg += "  No data yet\n\n"
            continue
        
        emoji = {"falling": "📉 Falling", "rising": "📈 Rising", "stable": "➡️ Stable"}.get(trend["trend"], "❓")
        msg += f"  Trend: {emoji} ({trend['data_points']} points)\n"
        msg += f"  Best: ₹{trend['min']:,.0f} | Avg: ₹{trend['average']:,.0f} | Current: ₹{trend['current']:,.0f}\n"
        
        # Recent best
        best = get_best_flight_price(route, 7)
        if best:
            direct = "DIRECT" if best.get("is_direct") else f"{best.get('stops', '?')} stop"
            msg += f"  Latest best: ₹{float(best['price']):,.0f} on {best.get('airline', '?')} ({direct})\n"
        
        msg += "\n"
    
    if dest.get("preferred_airlines"):
        msg += f"<b>Preferred:</b> {' | '.join(dest['preferred_airlines'])}"
    
    send_message(msg)


def _cmd_hotels(arg_str: str, config: dict):
    if not arg_str:
        send_message("Usage: /hotels &lt;destination&gt;")
        return
    
    dest, cat = resolve_destination(arg_str, config)
    if not dest:
        send_message(f"'{arg_str}' not found.")
        return
    
    msg = f"<b>Hotels in {dest['name']}</b>\n\n"
    
    for chain_key in ["accor", "marriott", "itc"]:
        if not dest.get(chain_key):
            continue
        
        label = {"accor": "Accor ALL 🏨", "marriott": "Marriott Bonvoy 🏨", "itc": "ITC Hotels 🏨"}[chain_key]
        msg += f"<b>{label}:</b>\n"
        
        history = get_hotel_history(dest["name"], 7, chain_key)
        if history:
            # Group by hotel name
            by_hotel = {}
            for h in history:
                name = h["hotel_name"]
                if name not in by_hotel or float(h["price"]) < float(by_hotel[name]["price"]):
                    by_hotel[name] = h
            
            for name, h in sorted(by_hotel.items(), key=lambda x: float(x[1]["price"]))[:3]:
                rating = f"⭐{h['rating']}" if h.get("rating") else ""
                msg += f"  {name[:35]}: ₹{float(h['price']):,.0f}/night {rating}\n"
        else:
            msg += "  No data yet\n"
        
        msg += "\n"
    
    send_message(msg)


def _cmd_deals(config: dict, origin: str):
    msg = "<b>🔥 Current Best Deals</b>\n\n"
    deals = []
    
    for cat in ["domestic", "international"]:
        for dest in config["destinations"][cat]:
            for ap in dest["airports"]:
                route = f"{origin}-{ap}"
                trend = get_price_trend(f"FLIGHT:{route}", "flight", 7)
                
                if trend["trend"] != "no_data" and trend["average"] > 0:
                    pct = ((trend["average"] - trend["current"]) / trend["average"]) * 100
                    deals.append({
                        "dest": dest["name"],
                        "airport": ap,
                        "price": trend["current"],
                        "pct": pct
                    })
    
    deals.sort(key=lambda x: x["pct"], reverse=True)
    
    if not deals:
        msg += "No data yet. Prices will appear after first scan."
    else:
        for d in deals[:12]:
            emoji = "🔥" if d["pct"] > 15 else ("👍" if d["pct"] > 0 else "📈")
            msg += f"{emoji} {d['dest']} ({d['airport']}): ₹{d['price']:,.0f}/pp ({d['pct']:+.0f}%)\n"
    
    send_message(msg)


def _cmd_offers():
    offers = get_recent_offers(7)
    msg = "<b>🎁 Active Offers</b>\n\n"
    
    if not offers:
        msg += "No offers tracked yet. Scraping runs weekly."
    else:
        seen = set()
        for o in offers[:12]:
            title = o.get("title", "")
            if title and title not in seen:
                source = o.get("source", "")
                cat = o.get("category", "")
                emoji = {"loyalty": "🏨", "cc_portal": "💳", "airline": "✈️"}.get(cat, "🎁")
                msg += f"{emoji} [{source}] {title}\n"
                seen.add(title)
    
    send_message(msg)


def _cmd_config(config: dict):
    msg = "<b>Alert Thresholds</b>\n\n"
    
    for cat in ["domestic", "international"]:
        msg += f"<b>{cat.upper()}:</b>\n"
        for dest in config["destinations"][cat]:
            override = get_threshold_override(dest["name"])
            t = override or dest["thresholds"]
            ft = t.get("flight_threshold", t.get("flight_rt_pp", "?"))
            ht = t.get("hotel_threshold", t.get("hotel_night", "?"))
            dp = t.get("drop_pct", 20)
            custom = " ✏️" if override else ""
            msg += f"  {dest['name']}: F ₹{ft:,} | H ₹{ht:,} | {dp}% drop{custom}\n"
        msg += "\n"
    
    msg += "Update: /set &lt;dest&gt; &lt;flight_price&gt; &lt;hotel_price&gt;"
    send_message(msg)


def _cmd_set(args: list, config: dict):
    if len(args) < 3:
        send_message("Usage: /set &lt;dest&gt; &lt;flight_price&gt; &lt;hotel_price&gt;\nExample: /set japan 30000 7000")
        return
    
    dest, _ = resolve_destination(args[0], config)
    if not dest:
        send_message(f"'{args[0]}' not found.")
        return
    
    try:
        ft = int(args[1])
        ht = int(args[2])
    except ValueError:
        send_message("Prices must be numbers.")
        return
    
    set_threshold_override(dest["name"], ft, ht, dest["thresholds"].get("drop_pct", 20))
    send_message(f"Updated {dest['name']}:\n  Flight: ₹{ft:,}/pp\n  Hotel: ₹{ht:,}/night")


def _cmd_status():
    from datetime import datetime
    send_message(f"""
<b>Bot Status</b>

Status: Online
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Components: Flight + Hotel + Loyalty + CC Portal + Digest
Digest: Sunday 9 AM IST
""".strip())
