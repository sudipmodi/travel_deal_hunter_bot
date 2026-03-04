"""
Hotel Loyalty Program Scraper.
Scrapes Accor ALL / Marriott Bonvoy / ITC Hotels for offers.
Runs as scheduled job within the main Render service.
Uses Playwright for JS-heavy sites.
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
        "name": "Accor ALL",
        "category": "loyalty",
        "urls": [
            "https://all.accor.com/promotions-deals/index.en.shtml",
            "https://all.accor.com/promotions-deals/flash-sales/index.en.shtml"
        ],
        "sale_keywords": ["flash", "sale", "bonus", "point", "member", "exclusive", "save"]
    },
    {
        "name": "Marriott Bonvoy",
        "category": "loyalty",
        "urls": [
            "https://www.marriott.com/loyalty/promotion.mi"
        ],
        "sale_keywords": ["bonus", "point", "earn", "free night", "promotion", "sale", "offer"]
    },
    {
        "name": "ITC Hotels",
        "category": "loyalty",
        "urls": [
            "https://www.itchotels.com/in/en/offers"
        ],
        "sale_keywords": ["offer", "package", "deal", "special", "weekend", "discount"]
    }
]


async def scrape_target(browser, target: dict) -> list:
    """Scrape offers from a single target."""
    offers = []
    
    for url in target["urls"]:
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)
            
            # Generic card/offer extraction
            elements = []
            for selector in ["[class*='offer']", "[class*='promo']", "[class*='deal']",
                           "[class*='card']", "[class*='tile']"]:
                found = await page.query_selector_all(selector)
                elements.extend(found)
            
            seen = set()
            for elem in elements[:20]:
                try:
                    title_el = await elem.query_selector("h2, h3, h4, [class*='title'], [class*='heading']")
                    desc_el = await elem.query_selector("p, [class*='desc'], [class*='body']")
                    
                    title = (await title_el.inner_text()).strip() if title_el else ""
                    desc = (await desc_el.inner_text()).strip()[:200] if desc_el else ""
                    
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    
                    text_lower = f"{title} {desc}".lower()
                    is_relevant = any(kw in text_lower for kw in target["sale_keywords"])
                    
                    if is_relevant:
                        otype = "bonus_points" if any(kw in text_lower for kw in ["bonus", "point", "earn", "double"]) else "promotion"
                        
                        offers.append({
                            "source": target["name"],
                            "offer_type": otype,
                            "category": target["category"],
                            "title": title,
                            "description": desc,
                            "link": url
                        })
                except Exception:
                    continue
            
            await page.close()
        
        except Exception as e:
            logger.error(f"Error scraping {target['name']} at {url}: {e}")
    
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
                logger.info(f"{target['name']}: {len(offers)} offers found")
            except Exception as e:
                logger.error(f"{target['name']} failed: {e}")
            
            await asyncio.sleep(2)
        
        await browser.close()
    
    return all_offers


def main():
    logger.info("Loyalty scraper started")
    
    offers = asyncio.run(run_scrapers())
    logger.info(f"Total loyalty offers: {len(offers)}")
    
    new_count = 0
    for offer in offers:
        row_id = record_offer(**offer)
        if row_id > 0:
            new_count += 1
            
            # Alert on flash sales and bonus point offers
            if offer["offer_type"] in ("bonus_points",) or "flash" in offer["title"].lower():
                cooldown_key = f"loyalty_{offer['source']}_{offer['title'][:30]}"
                if check_cooldown(cooldown_key, 168):  # 7 day cooldown for offers
                    send_alert({
                        "type": "loyalty_offer",
                        "source": offer["source"],
                        "title": offer["title"],
                        "description": offer["description"],
                        "link": offer["link"]
                    })
    
    logger.info(f"New offers stored: {new_count}")


if __name__ == "__main__":
    main()
