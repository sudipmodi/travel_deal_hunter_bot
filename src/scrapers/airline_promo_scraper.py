"""
Airline Promotion Scraper.
Scrapes airline promo pages for sale fares and bonus miles.
"""

import os
import json
import logging
import asyncio
from datetime import datetime

from db.database import record_offer, check_cooldown
from handlers.telegram_alerts import send_alert

logger = logging.getLogger(__name__)

AIRLINES = [
    {
        "name": "Singapore Airlines", "program": "KrisFlyer",
        "url": "https://www.singaporeair.com/en_UK/in/special-offers/",
        "keywords": ["sale", "special", "offer", "from india", "promotion", "krisflyer"]
    },
    {
        "name": "Emirates", "program": "Skywards",
        "url": "https://www.emirates.com/in/english/special-offers/",
        "keywords": ["sale", "special fare", "offer", "bonus miles", "skywards"]
    },
    {
        "name": "Air India", "program": "Flying Returns",
        "url": "https://www.airindia.com/in/en/offers.html",
        "keywords": ["sale", "offer", "special", "festive", "discount"]
    },
    {
        "name": "British Airways", "program": "Avios",
        "url": "https://www.britishairways.com/en-in/offers",
        "keywords": ["sale", "avios", "offer", "bonus", "companion"]
    },
    {
        "name": "Etihad", "program": "Etihad Guest",
        "url": "https://www.etihad.com/en-in/deals",
        "keywords": ["sale", "deal", "offer", "bonus miles", "guest"]
    },
    {
        "name": "Virgin Atlantic", "program": "Flying Club",
        "url": "https://www.virginatlantic.com/offers/flights-from-india",
        "keywords": ["sale", "offer", "points", "bonus", "flying club"]
    },
    {
        "name": "ANA", "program": "Mileage Club",
        "url": "https://www.ana.co.jp/en/in/promotions/",
        "keywords": ["campaign", "promotion", "bonus", "miles", "sale"]
    },
    {
        "name": "Japan Airlines", "program": "Mileage Bank",
        "url": "https://www.jal.co.jp/en/inter/promotion/",
        "keywords": ["campaign", "promotion", "sale", "special fare"]
    }
]


async def scrape_airline(browser, airline: dict) -> list:
    offers = []
    try:
        page = await browser.new_page()
        await page.goto(airline["url"], wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        elements = []
        for sel in ["[class*='offer']", "[class*='deal']", "[class*='promo']",
                   "[class*='card']", "[class*='tile']", "[class*='campaign']"]:
            found = await page.query_selector_all(sel)
            elements.extend(found)
        
        seen = set()
        for elem in elements[:25]:
            try:
                title_el = await elem.query_selector("h2, h3, h4, [class*='title'], [class*='heading']")
                desc_el = await elem.query_selector("p, [class*='desc'], [class*='detail']")
                price_el = await elem.query_selector("[class*='price'], [class*='fare']")
                
                title = (await title_el.inner_text()).strip() if title_el else ""
                desc = (await desc_el.inner_text()).strip()[:200] if desc_el else ""
                price = (await price_el.inner_text()).strip()[:100] if price_el else ""
                
                if not title or title in seen:
                    continue
                seen.add(title)
                
                text_lower = f"{title} {desc} {price}".lower()
                is_relevant = any(kw in text_lower for kw in airline["keywords"])
                
                if is_relevant:
                    if any(kw in text_lower for kw in ["bonus mile", "bonus point", "earn extra", "double"]):
                        otype = "bonus_miles"
                    elif any(kw in text_lower for kw in ["sale", "special fare", "from inr", "starting"]):
                        otype = "sale_fare"
                    else:
                        otype = "airline_promo"
                    
                    offers.append({
                        "source": airline["name"],
                        "offer_type": otype,
                        "category": "airline",
                        "title": title,
                        "description": desc,
                        "link": airline["url"],
                        "extra": {"program": airline["program"], "price_info": price}
                    })
            except Exception:
                continue
        
        await page.close()
    except Exception as e:
        logger.error(f"Error scraping {airline['name']}: {e}")
    
    return offers


async def run_scrapers() -> list:
    from playwright.async_api import async_playwright
    
    all_offers = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        
        for airline in AIRLINES:
            logger.info(f"Scraping {airline['name']}...")
            try:
                offers = await scrape_airline(browser, airline)
                all_offers.extend(offers)
                logger.info(f"{airline['name']}: {len(offers)} offers")
            except Exception as e:
                logger.error(f"{airline['name']} failed: {e}")
            
            await asyncio.sleep(2)
        
        await browser.close()
    
    return all_offers


def main():
    logger.info("Airline promo scraper started")
    
    offers = asyncio.run(run_scrapers())
    logger.info(f"Total airline offers: {len(offers)}")
    
    new_count = 0
    for offer in offers:
        row_id = record_offer(**offer)
        if row_id > 0:
            new_count += 1
            
            if offer["offer_type"] in ("sale_fare", "bonus_miles"):
                cooldown_key = f"airline_{offer['source']}_{offer['title'][:30]}"
                if check_cooldown(cooldown_key, 168):
                    send_alert({
                        "type": "loyalty_offer",
                        "source": f"{offer['source']} ({offer.get('extra', {}).get('program', '')})",
                        "title": offer["title"],
                        "description": offer["description"],
                        "link": offer["link"]
                    })
    
    logger.info(f"New airline offers stored: {new_count}")


if __name__ == "__main__":
    main()
