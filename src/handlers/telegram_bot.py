"""
Telegram Bot Webhook Handler.
Flask app that receives Telegram webhook updates and processes commands.
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


# ─────────────────────────────────────────────
# CONFIG LOADER
# ─────────────────────────────────────────────

def load_config():
    global CONFIG
    if CONFIG is None:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "config.json")
        with open(config_path) as f:
            CONFIG = json.load(f)
    return CONFIG


# ─────────────────────────────────────────────
# DESTINATION NORMALIZATION
# ─────────────────────────────────────────────

DEST_ALIASES = {
    "vn": "vietnam",
    "viet": "vietnam",
    "sing": "singapore",
    "uae": "uae",
    "dubai": "uae",
    "uk": "london"
}


def normalize_dest(name: str):
    name = name.lower().strip()
    return DEST_ALIASES.get(name, name)


def resolve_destination(name: str, config: dict):
    """Fuzzy match destination name."""
    name = normalize_dest(name)

    for cat in ["domestic", "international"]:
        for dest in config["destinations"][cat]:
            if name in dest["name"].lower():
                return dest, cat

            for ap in dest["airports"]:
                if name == ap.lower():
                    return dest, cat

    return None, None


# ─────────────────────────────────────────────
# TELEGRAM WEBHOOK
# ─────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json(force=True, silent=True) or {}

    message = body.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return jsonify({"ok": True})

    allowed = os.environ.get("TELEGRAM_CHAT_ID", "")
    if chat_id != allowed:
        return jsonify({"ok": True})

    config = load_config()
    origin = config["traveler"]["origin_airport"]

    parts = text.split()
    cmd = parts[0].lower() if parts else ""
    args = parts[1:]
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
            _cmd_check(arg_str, config, origin)

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


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

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
""".strip())


def _cmd_check(arg_str: str, config: dict, origin: str):

    if not arg_str:
        send_message("Usage: /check &lt;destination&gt;")
        return

    dest, cat = resolve_destination(arg_str, config)

    if not dest:
        send_message(f"'{arg_str}' not found.")
        return

    msg = f"<b>{dest['name']}</b> from {origin}\n\n<b>Flights:</b>\n"

    for ap in dest["airports"]:
        route = f"{origin}-{ap}"

        trend = get_price_trend(f"FLIGHT:{route}", "flight", 30)

        if trend["trend"] == "no_data":
            msg += f"{ap}: No data\n"
            continue

        emoji = {"falling": "📉", "rising": "📈", "stable": "➡️"}.get(trend["trend"], "❓")

        msg += f"{ap}: ₹{trend['current']:,.0f}/pp {emoji}\n"

    send_message(msg)


def _cmd_flights(arg_str: str, config: dict, origin: str):

    if not arg_str:
        send_message("Usage: /flights &lt;destination&gt;")
        return

    dest, cat = resolve_destination(arg_str, config)

    if not dest:
        send_message(f"'{arg_str}' not found.")
        return

    msg = f"<b>Flights to {dest['name']}</b>\n\n"

    for ap in dest["airports"]:

        route = f"{origin}-{ap}"

        trend = get_price_trend(f"FLIGHT:{route}", "flight", 30)

        if trend["trend"] == "no_data":
            msg += f"{ap}: No data yet\n\n"
            continue

        msg += f"{ap}: ₹{trend['current']:,.0f}\n"

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

    for chain in ["accor", "marriott", "itc"]:

        best = get_best_hotel_by_chain(dest["name"], chain, 7)

        if best:
            msg += f"{chain}: ₹{float(best['price']):,.0f}/night\n"
        else:
            msg += f"{chain}: No data\n"

    send_message(msg)


def _cmd_deals(config: dict, origin: str):

    msg = "<b>🔥 Current Best Deals</b>\n\n"

    deals = []

    for cat in ["domestic", "international"]:
        for dest in config["destinations"][cat]:

            for ap in dest["airports"]:

                route = f"{origin}-{ap}"

                trend = get_price_trend(f"FLIGHT:{route}", "flight", 7)

                if trend["trend"] != "no_data":

                    deals.append((dest["name"], trend["current"]))

    if not deals:
        msg += "No data yet."

    else:
        for d in sorted(deals, key=lambda x: x[1])[:10]:
            msg += f"{d[0]} ₹{d[1]:,.0f}\n"

    send_message(msg)


def _cmd_offers():

    offers = get_recent_offers(7)

    msg = "<b>🎁 Active Offers</b>\n\n"

    if not offers:
        msg += "No offers tracked yet."

    else:
        for o in offers[:10]:
            msg += f"{o.get('title','')}\n"

    send_message(msg)


def _cmd_config(config: dict):

    msg = "<b>Alert Thresholds</b>\n\n"

    for cat in ["domestic", "international"]:

        for dest in config["destinations"][cat]:

            t = dest["thresholds"]

            msg += f"{dest['name']} → F ₹{t.get('flight_rt_pp')} | H ₹{t.get('hotel_night')}\n"

    send_message(msg)


def _cmd_set(args: list, config: dict):

    if len(args) < 3:
        send_message("Usage: /set <dest> <flight> <hotel>")
        return

    dest, _ = resolve_destination(args[0], config)

    if not dest:
        send_message("Destination not found.")
        return

    try:
        ft = int(args[1])
        ht = int(args[2])
    except:
        send_message("Invalid numbers.")
        return

    set_threshold_override(dest["name"], ft, ht, dest["thresholds"].get("drop_pct", 20))

    send_message(f"Updated {dest['name']} thresholds.")


def _cmd_status():
    from datetime import datetime

    send_message(f"""
<b>Bot Status</b>

Status: Online
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Components: Flight + Hotel + Loyalty + CC Portal + Digest
""".strip())
