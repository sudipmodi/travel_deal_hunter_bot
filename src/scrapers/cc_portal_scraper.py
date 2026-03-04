"""
Credit Card Portal Scraper.
Scrapes HDFC SmartBuy / Axis Travel / Amex Travel for bonus deals.
"""

import os
import json
import logging
import asyncio
from datetime import datetime

from db.database import record_offer, check_cooldown
from handlers.telegram_alerts import send_alert

logger = logging.getLogger(__name__)

SCRAPE_TARGETS = [
    {
        "name": "HDFC SmartBuy",
        "card": "HDFC Regalia Gold",
        "category": "cc_portal",
        "urls": ["https://smartbuy.hdfcbank.com/offers"],
        "keywords": ["flight", "hotel", "travel", "reward", "point", "10x", "5x", "bonus", "smartbuy"]
    },
    {
        "name": "Axis Travel",
        "card": "Axis Atlas",
        "category": "cc_portal",
        "urls": [
            "https://www.axisbank.com/grab-deals/travel",
            "https://www.axisbank.com/grab-deals/atlas"
        ],
        "keywords": ["flight", "hotel", "travel", "miles", "edge", "lounge", "atlas", "booking"]
    },
    {
        "name": "Amex Travel",
        "card": "Amex Platinum Travel",
        "category": "cc_portal",
        "urls": ["https://www.americanexpress.com/in/network/offer"],
        "keywords": ["flight", "hotel", "travel", "airline", "membership reward", "bonus", "accor", "marriott"]
    }
]


async def scrape_target(browser, target: dict) -> list:
    offers = []
    
    for url in target["urls"]:
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)
            
            elements = []
            for sel in ["[class*='offer']", "[class*='deal']", "[class*='card']", "[class*='promo']"]:
                found = await page.query_selector_all(sel)
                elements.extend(found)
            
            seen = set()
            for elem in elements[:20]:
                try:
                    title_el = await elem.query_selector("h2, h3, h4, [class*='title']")
                    desc_el = await elem.query_selector("p, [class*='desc']")
                    
                    title = (await title_el.inner_text()).strip() if title_el else ""
                    desc = (await desc_el.inner_text()).strip()[:200] if desc_el else ""
                    
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    
                    text_lower = f"{title} {desc}".lower()
                    is_travel = any(kw in text_lower for kw in target["keywords"])
                    
                    if is_travel:
                        otype = "bonus_points" if any(kw in text_lower for kw in ["point", "reward", "10x", "5x", "bonus", "miles"]) else "portal_discount"
                        
                        offers.append({
                            "source": target["name"],
                            "offer_type": otype,
                            "category": target["category"],
                            "title": title,
                            "description": desc,
                            "link": url,
                            "extra": {"card": target["card"]}
                        })
                except Exception:
                    continue
            
            await page.close()
        except Exception as e:
            logger.error(f"Error scraping {target['name']}: {e}")
    
    return offers


async def run_scrapers() -> list:
    from playwright.async_api import async_playwright
    
    all_offers = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        
        for target in SCRAPE_TARGETS:
            logger.info(f"Scraping {target['name']}...")
            try:
                offers = await scrape_target(browser, target)
                all_offers.extend(offers)
                logger.info(f"{target['name']}: {len(offers)} offers")
            except Exception as e:
                logger.error(f"{target['name']} failed: {e}")
            
            await asyncio.sleep(2)
        
        await browser.close()
    
    return all_offers


def main():
    logger.info("CC portal scraper started")
    
    offers = asyncio.run(run_scrapers())
    logger.info(f"Total CC portal offers: {len(offers)}")
    
    new_count = 0
    for offer in offers:
        row_id = record_offer(**offer)
        if row_id > 0:
            new_count += 1
            
            if offer["offer_type"] == "bonus_points":
                cooldown_key = f"cc_{offer['source']}_{offer['title'][:30]}"
                if check_cooldown(cooldown_key, 168):
                    send_alert({
                        "type": "cc_portal_deal",
                        "card_name": offer.get("extra", {}).get("card", ""),
                        "title": offer["title"],
                        "description": offer["description"],
                        "portal": offer["source"]
                    })
    
    logger.info(f"New CC offers stored: {new_count}")


if __name__ == "__main__":
    main()
