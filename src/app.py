"""
Travel Deal Hunter Bot - Main Entry Point.

Runs on Render as a Web Service with:
  - Flask web server for Telegram webhook
  - APScheduler for cron-based price checks and scraping
"""

import os
import sys
import json
import logging
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# Add src to path
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from db.database import init_database, cleanup_old_data
from handlers.telegram_bot import app
from handlers.flight_checker import run_flight_check
from handlers.hotel_checker import run_hotel_check
from handlers.digest_generator import run_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config", "config.json")
    with open(config_path) as f:
        return json.load(f)


# ── SCHEDULED JOBS ──

def job_flight_check():
    """Scheduled flight price check."""
    try:
        config = load_config()
        result = run_flight_check(config)
        logger.info(f"Flight check completed: {result}")
    except Exception as e:
        logger.error(f"Flight check failed: {e}")


def job_hotel_check():
    """Scheduled hotel price check."""
    try:
        config = load_config()
        result = run_hotel_check(config)
        logger.info(f"Hotel check completed: {result}")
    except Exception as e:
        logger.error(f"Hotel check failed: {e}")


def job_weekly_digest():
    """Weekly digest generation."""
    try:
        config = load_config()
        result = run_digest(config)
        logger.info(f"Weekly digest completed: {result}")
    except Exception as e:
        logger.error(f"Weekly digest failed: {e}")


def job_loyalty_scraper():
    """Weekly loyalty program scraping."""
    try:
        from scrapers.loyalty_scraper import main as run_loyalty
        run_loyalty()
    except Exception as e:
        logger.error(f"Loyalty scraper failed: {e}")


def job_cc_portal_scraper():
    """Weekly CC portal scraping."""
    try:
        from scrapers.cc_portal_scraper import main as run_cc
        run_cc()
    except Exception as e:
        logger.error(f"CC portal scraper failed: {e}")


def job_airline_promo_scraper():
    """Weekly airline promo scraping."""
    try:
        from scrapers.airline_promo_scraper import main as run_airline
        run_airline()
    except Exception as e:
        logger.error(f"Airline promo scraper failed: {e}")


def job_cleanup():
    """Monthly data cleanup."""
    try:
        result = cleanup_old_data()
        logger.info(f"Cleanup completed: {result}")
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")


def setup_scheduler():
    """Configure APScheduler with all cron jobs."""
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    
    # Flight prices: twice daily at 6 AM and 6 PM IST
    scheduler.add_job(job_flight_check, CronTrigger(hour="6,18", minute=0))
    
    # Hotel prices: twice daily at 7 AM and 7 PM IST (staggered from flights)
    scheduler.add_job(job_hotel_check, CronTrigger(hour="7,19", minute=0))
    
    # Weekly digest: Sunday 9 AM IST
    scheduler.add_job(job_weekly_digest, CronTrigger(day_of_week="sun", hour=9, minute=0))
    
    # Loyalty scraper: Every Wednesday 3 AM IST
    scheduler.add_job(job_loyalty_scraper, CronTrigger(day_of_week="wed", hour=3, minute=0))
    
    # CC portal scraper: Every Thursday 3 AM IST
    scheduler.add_job(job_cc_portal_scraper, CronTrigger(day_of_week="thu", hour=3, minute=0))
    
    # Airline promo scraper: Every Friday 3 AM IST
    scheduler.add_job(job_airline_promo_scraper, CronTrigger(day_of_week="fri", hour=3, minute=0))
    
    # Data cleanup: 1st of every month at 4 AM IST
    scheduler.add_job(job_cleanup, CronTrigger(day=1, hour=4, minute=0))
    
    scheduler.start()
    logger.info("Scheduler started with all jobs configured")
    
    return scheduler


# ── INITIALIZATION ──

def init_app():
    """Initialize database and scheduler on startup."""
    logger.info("Initializing Travel Deal Hunter Bot...")
    
    # Init database tables
    try:
        init_database()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        # Continue anyway - DB might already exist
    
    # Start scheduler
    setup_scheduler()
    
    logger.info("Bot initialized and ready")


# Run init on import (Gunicorn will import this module)
init_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
