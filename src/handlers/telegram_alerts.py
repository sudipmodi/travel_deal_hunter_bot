"""
Telegram messaging utilities.
Handles message formatting and sending with chunking and rate limiting.
"""

import os
import logging
import time
import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


def get_api_url() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return TELEGRAM_API.format(token=token)


def get_chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def send_message(text: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
    """Send a message to Telegram. Auto-chunks if over 4096 chars."""
    chat_id = chat_id or get_chat_id()
    api_url = get_api_url()
    
    if not chat_id or "your_" in chat_id:
        logger.warning("Telegram chat_id not configured")
        return False
    
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    success = True
    
    for chunk in chunks:
        try:
            resp = requests.post(
                f"{api_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True
                },
                timeout=10
            )
            
            if resp.status_code == 429:
                # Rate limited. Wait and retry once.
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                logger.warning(f"Telegram rate limited. Waiting {retry_after}s")
                time.sleep(retry_after)
                resp = requests.post(
                    f"{api_url}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True
                    },
                    timeout=10
                )
            
            if resp.status_code != 200:
                logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
                success = False
        
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            success = False
        
        # Small delay between chunks to avoid rate limits
        if len(chunks) > 1:
            time.sleep(0.5)
    
    return success


# ============================================================
# ALERT FORMATTERS
# ============================================================

def format_flight_drop(alert: dict) -> str:
    """Format flight price drop alert."""
    offer = alert.get("best_offer", {})
    direct_flag = "DIRECT" if offer.get("is_direct") else f"{offer.get('stops', '?')} stop(s)"
    
    return f"""
🔥 <b>FLIGHT PRICE DROP</b>

<b>{alert['destination_name']}</b> ({alert['route']})

💰 Now: <b>₹{alert['current_price']:,.0f}</b>/person
📊 Average: ₹{alert.get('avg_price', 0):,.0f}
📉 Drop: <b>{alert.get('drop_percent', 0):.1f}%</b>

✈️ {offer.get('airline', 'Unknown')} | {direct_flag}
📅 {offer.get('departure_date', 'N/A')} to {offer.get('return_date', 'N/A')}
🎫 {offer.get('booking_class', 'ECONOMY')}
""".strip()


def format_flight_threshold(alert: dict) -> str:
    """Format flight below-threshold alert."""
    offer = alert.get("best_offer", {})
    
    return f"""
🎯 <b>FLIGHT BELOW TARGET</b>

<b>{alert['destination_name']}</b> ({alert['route']})

💰 Price: <b>₹{alert['current_price']:,.0f}</b>/person
🎯 Your target: ₹{alert['threshold_price']:,}

✈️ {offer.get('airline', 'Unknown')}
📅 {offer.get('departure_date', 'N/A')} to {offer.get('return_date', 'N/A')}
""".strip()


def format_hotel_drop(alert: dict) -> str:
    """Format hotel price drop alert."""
    chain_label = {"accor": "Accor ALL", "marriott": "Marriott Bonvoy", "itc": "ITC Hotels"}.get(
        alert.get("chain", ""), alert.get("chain", ""))
    
    return f"""
🔥 <b>HOTEL PRICE DROP</b>

<b>{alert['destination_name']}</b>

🏨 <b>{alert.get('hotel_name', 'Unknown')}</b>
🏷️ {chain_label}

💰 Now: <b>₹{alert['current_price']:,.0f}</b>/night
📊 Average: ₹{alert.get('avg_price', 0):,.0f}
📉 Drop: <b>{alert.get('drop_percent', 0):.1f}%</b>
""".strip()


def format_hotel_threshold(alert: dict) -> str:
    """Format hotel below-threshold alert."""
    return f"""
🎯 <b>HOTEL BELOW TARGET</b>

<b>{alert['destination_name']}</b>

🏨 <b>{alert.get('hotel_name', 'Unknown')}</b>
💰 Price: <b>₹{alert['current_price']:,.0f}</b>/night
🎯 Your target: ₹{alert['threshold_price']:,}
""".strip()


def format_loyalty_offer(alert: dict) -> str:
    """Format loyalty program offer alert."""
    return f"""
🎁 <b>LOYALTY OFFER</b>

<b>{alert.get('source', 'Unknown')}</b>

{alert.get('title', '')}
{alert.get('description', '')}

🔗 {alert.get('link', 'Check source')}
""".strip()


def format_cc_portal_deal(alert: dict) -> str:
    """Format CC portal deal alert."""
    return f"""
💳 <b>CREDIT CARD PORTAL DEAL</b>

<b>{alert.get('card_name', 'Unknown')}</b>

{alert.get('title', '')}
{alert.get('description', '')}

🔗 Portal: {alert.get('portal', 'N/A')}
""".strip()


FORMATTERS = {
    "flight_drop": format_flight_drop,
    "flight_threshold": format_flight_threshold,
    "hotel_drop": format_hotel_drop,
    "hotel_threshold": format_hotel_threshold,
    "loyalty_offer": format_loyalty_offer,
    "cc_portal_deal": format_cc_portal_deal
}


def send_alert(alert: dict) -> bool:
    """Format and send a single alert."""
    alert_type = alert.get("type", "unknown")
    formatter = FORMATTERS.get(alert_type)
    
    if not formatter:
        logger.warning(f"No formatter for alert type: {alert_type}")
        return False
    
    message = formatter(alert)
    return send_message(message)
