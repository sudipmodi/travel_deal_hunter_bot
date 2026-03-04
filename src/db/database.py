"""
Database layer for Travel Deal Hunter Bot.
Uses Supabase PostgreSQL for persistent storage.

Tables:
  - flight_prices: Flight price observations with route and airline and stops
  - hotel_prices: Hotel price observations with chain identification
  - offers: Loyalty program and CC portal and airline promo offers
  - price_baselines: Rolling average prices for drop detection
  - alert_config: User-customized thresholds per destination
  - alert_cooldowns: Prevents duplicate alerts within cooldown window
"""

import os
import json
import logging
from datetime import datetime
from datetime import timedelta
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2 import sql

logger = logging.getLogger(__name__)

# ============================================================
# SCHEMA INITIALIZATION
# ============================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS flight_prices (
    id BIGSERIAL PRIMARY KEY,
    route VARCHAR(20) NOT NULL,
    destination_name VARCHAR(100) NOT NULL,
    price NUMERIC(12,2) NOT NULL,
    airline VARCHAR(100),
    stops INTEGER DEFAULT 0,
    is_direct BOOLEAN DEFAULT FALSE,
    departure_date DATE,
    return_date DATE,
    duration VARCHAR(20),
    booking_class VARCHAR(30) DEFAULT 'ECONOMY',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hotel_prices (
    id BIGSERIAL PRIMARY KEY,
    destination_name VARCHAR(100) NOT NULL,
    hotel_name VARCHAR(200) NOT NULL,
    chain VARCHAR(50) DEFAULT 'independent',
    price NUMERIC(12,2) NOT NULL,
    rating NUMERIC(3,1),
    check_in DATE,
    check_out DATE,
    is_loyalty_property BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS offers (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(100) NOT NULL,
    offer_type VARCHAR(50) NOT NULL,
    category VARCHAR(50) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT DEFAULT '',
    link TEXT DEFAULT '',
    extra JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS price_baselines (
    route_key VARCHAR(200) PRIMARY KEY,
    avg_price NUMERIC(12,2) NOT NULL,
    min_price NUMERIC(12,2),
    max_price NUMERIC(12,2),
    sample_count INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alert_config (
    destination VARCHAR(100) PRIMARY KEY,
    flight_threshold NUMERIC(12,2),
    hotel_threshold NUMERIC(12,2),
    drop_pct INTEGER DEFAULT 20,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alert_cooldowns (
    alert_key VARCHAR(300) PRIMARY KEY,
    last_sent TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_flight_route_created ON flight_prices(route, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_flight_dest_created ON flight_prices(destination_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hotel_dest_created ON hotel_prices(destination_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hotel_chain ON hotel_prices(chain, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_offers_category ON offers(category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_offers_source ON offers(source, created_at DESC);

-- Cleanup old data (run periodically)
-- DELETE FROM flight_prices WHERE created_at < NOW() - INTERVAL '90 days';
-- DELETE FROM hotel_prices WHERE created_at < NOW() - INTERVAL '90 days';
-- DELETE FROM offers WHERE created_at < NOW() - INTERVAL '30 days';
-- DELETE FROM alert_cooldowns WHERE expires_at < NOW();
"""


def get_connection():
    """Get PostgreSQL connection using DATABASE_URL."""
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def init_database():
    """Create tables and indexes if they do not exist."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        logger.info("Database schema initialized")
    except Exception as e:
        conn.rollback()
        logger.error(f"Schema init failed: {e}")
        raise
    finally:
        conn.close()


# ============================================================
# FLIGHT PRICE OPERATIONS
# ============================================================

def record_flight_price(route: str, destination_name: str, price: float,
                        airline: str, stops: int, is_direct: bool,
                        departure_date: str = None, return_date: str = None,
                        duration: str = None, booking_class: str = "ECONOMY",
                        metadata: dict = None) -> int:
    """Store a flight price observation. Returns row ID."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO flight_prices 
                (route, destination_name, price, airline, stops, is_direct,
                 departure_date, return_date, duration, booking_class, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (route, destination_name, price, airline, stops, is_direct,
                  departure_date, return_date, duration, booking_class,
                  json.dumps(metadata or {})))
            row_id = cur.fetchone()["id"]
        conn.commit()
        return row_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to record flight price: {e}")
        return -1
    finally:
        conn.close()


def get_flight_history(route: str, days: int = 30) -> list:
    """Get flight price history for a route."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT price, airline, stops, is_direct, departure_date, 
                       duration, booking_class, created_at
                FROM flight_prices
                WHERE route = %s AND created_at > NOW() - INTERVAL '%s days'
                ORDER BY created_at DESC
            """, (route, days))
            return cur.fetchall()
    finally:
        conn.close()


def get_best_flight_price(route: str, days: int = 7) -> Optional[dict]:
    """Get the lowest flight price in the last N days."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT price, airline, stops, is_direct, departure_date,
                       return_date, duration, booking_class, created_at
                FROM flight_prices
                WHERE route = %s AND created_at > NOW() - INTERVAL '%s days'
                ORDER BY price ASC
                LIMIT 1
            """, (route, days))
            return cur.fetchone()
    finally:
        conn.close()


# ============================================================
# HOTEL PRICE OPERATIONS
# ============================================================

def record_hotel_price(destination_name: str, hotel_name: str, chain: str,
                       price: float, rating: float = None,
                       check_in: str = None, check_out: str = None,
                       is_loyalty: bool = False, metadata: dict = None) -> int:
    """Store a hotel price observation."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO hotel_prices
                (destination_name, hotel_name, chain, price, rating,
                 check_in, check_out, is_loyalty_property, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (destination_name, hotel_name, chain, price, rating,
                  check_in, check_out, is_loyalty, json.dumps(metadata or {})))
            row_id = cur.fetchone()["id"]
        conn.commit()
        return row_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to record hotel price: {e}")
        return -1
    finally:
        conn.close()


def get_hotel_history(destination_name: str, days: int = 30, 
                      chain: str = None) -> list:
    """Get hotel price history for a destination."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if chain:
                cur.execute("""
                    SELECT hotel_name, chain, price, rating, check_in, 
                           check_out, created_at
                    FROM hotel_prices
                    WHERE destination_name = %s AND chain = %s
                          AND created_at > NOW() - INTERVAL '%s days'
                    ORDER BY created_at DESC
                """, (destination_name, chain, days))
            else:
                cur.execute("""
                    SELECT hotel_name, chain, price, rating, check_in, 
                           check_out, created_at
                    FROM hotel_prices
                    WHERE destination_name = %s
                          AND created_at > NOW() - INTERVAL '%s days'
                    ORDER BY created_at DESC
                """, (destination_name, days))
            return cur.fetchall()
    finally:
        conn.close()


def get_best_hotel_by_chain(destination_name: str, chain: str, 
                            days: int = 7) -> Optional[dict]:
    """Get the lowest hotel price for a chain in a destination."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT hotel_name, chain, price, rating, check_in, 
                       check_out, created_at
                FROM hotel_prices
                WHERE destination_name = %s AND chain = %s
                      AND created_at > NOW() - INTERVAL '%s days'
                ORDER BY price ASC
                LIMIT 1
            """, (destination_name, chain, days))
            return cur.fetchone()
    finally:
        conn.close()


# ============================================================
# OFFER OPERATIONS
# ============================================================

def record_offer(source: str, offer_type: str, category: str,
                 title: str, description: str = "", link: str = "",
                 extra: dict = None) -> int:
    """Store a loyalty/CC/airline offer."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Avoid duplicates: check if same title from same source in last 24h
            cur.execute("""
                SELECT id FROM offers
                WHERE source = %s AND title = %s
                      AND created_at > NOW() - INTERVAL '24 hours'
                LIMIT 1
            """, (source, title))
            
            if cur.fetchone():
                return -1  # Duplicate
            
            cur.execute("""
                INSERT INTO offers (source, offer_type, category, title, 
                                    description, link, extra)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (source, offer_type, category, title, description, link,
                  json.dumps(extra or {})))
            row_id = cur.fetchone()["id"]
        conn.commit()
        return row_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to record offer: {e}")
        return -1
    finally:
        conn.close()


def get_recent_offers(days: int = 7, category: str = None) -> list:
    """Get recent offers optionally filtered by category."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if category:
                cur.execute("""
                    SELECT source, offer_type, category, title, description,
                           link, extra, created_at
                    FROM offers
                    WHERE category = %s AND created_at > NOW() - INTERVAL '%s days'
                    ORDER BY created_at DESC
                """, (category, days))
            else:
                cur.execute("""
                    SELECT source, offer_type, category, title, description,
                           link, extra, created_at
                    FROM offers
                    WHERE created_at > NOW() - INTERVAL '%s days'
                    ORDER BY created_at DESC
                """, (days,))
            return cur.fetchall()
    finally:
        conn.close()


# ============================================================
# BASELINE AND PRICE ANALYSIS
# ============================================================

def get_baseline(route_key: str) -> Optional[dict]:
    """Get rolling average baseline for a route or hotel."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT avg_price, min_price, max_price, sample_count, updated_at
                FROM price_baselines
                WHERE route_key = %s
            """, (route_key,))
            return cur.fetchone()
    finally:
        conn.close()


def update_baseline(route_key: str, avg_price: float, min_price: float,
                    max_price: float, sample_count: int) -> None:
    """Upsert rolling average baseline."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO price_baselines (route_key, avg_price, min_price, 
                                             max_price, sample_count, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (route_key) DO UPDATE SET
                    avg_price = EXCLUDED.avg_price,
                    min_price = EXCLUDED.min_price,
                    max_price = EXCLUDED.max_price,
                    sample_count = EXCLUDED.sample_count,
                    updated_at = NOW()
            """, (route_key, avg_price, min_price, max_price, sample_count))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update baseline: {e}")
    finally:
        conn.close()


def recalculate_baseline(route_key: str, table: str = "flight") -> None:
    """Recalculate baseline from last 30 days of data."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if table == "flight":
                route_val = route_key.replace("FLIGHT:", "")
                cur.execute("""
                    SELECT AVG(price) as avg_p, MIN(price) as min_p,
                           MAX(price) as max_p, COUNT(*) as cnt
                    FROM flight_prices
                    WHERE route = %s AND created_at > NOW() - INTERVAL '30 days'
                """, (route_val,))
            else:
                parts = route_key.replace("HOTEL:", "").split(":", 1)
                cur.execute("""
                    SELECT AVG(price) as avg_p, MIN(price) as min_p,
                           MAX(price) as max_p, COUNT(*) as cnt
                    FROM hotel_prices
                    WHERE destination_name = %s AND created_at > NOW() - INTERVAL '30 days'
                """, (parts[0],))
            
            row = cur.fetchone()
            if row and row["cnt"] and row["cnt"] > 0:
                update_baseline(route_key, float(row["avg_p"]), 
                               float(row["min_p"]), float(row["max_p"]),
                               int(row["cnt"]))
    finally:
        conn.close()


def check_price_drop(route_key: str, current_price: float, 
                     threshold_pct: float) -> Optional[dict]:
    """Check if current price is a significant drop vs baseline."""
    baseline = get_baseline(route_key)
    
    if not baseline or not baseline["avg_price"]:
        return None
    
    avg = float(baseline["avg_price"])
    if avg <= 0:
        return None
    
    drop_pct = ((avg - current_price) / avg) * 100
    
    if drop_pct >= threshold_pct:
        return {
            "current_price": current_price,
            "avg_price": avg,
            "min_price": float(baseline["min_price"]),
            "max_price": float(baseline["max_price"]),
            "drop_percent": round(drop_pct, 1)
        }
    
    return None


def get_price_trend(route_key: str, table: str = "flight", 
                    days: int = 30) -> dict:
    """Generate trend data for a route or destination."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if table == "flight":
                route_val = route_key.replace("FLIGHT:", "")
                cur.execute("""
                    SELECT price, created_at
                    FROM flight_prices
                    WHERE route = %s AND created_at > NOW() - INTERVAL '%s days'
                    ORDER BY created_at ASC
                """, (route_val, days))
            else:
                dest = route_key.replace("HOTEL:", "")
                cur.execute("""
                    SELECT price, created_at
                    FROM hotel_prices
                    WHERE destination_name = %s AND created_at > NOW() - INTERVAL '%s days'
                    ORDER BY created_at ASC
                """, (dest, days))
            
            rows = cur.fetchall()
            
            if not rows:
                return {"trend": "no_data", "data_points": 0}
            
            prices = [float(r["price"]) for r in rows]
            current = prices[-1]
            avg = sum(prices) / len(prices)
            
            # Trend detection
            if len(prices) >= 4:
                mid = len(prices) // 2
                first_half_avg = sum(prices[:mid]) / mid
                second_half_avg = sum(prices[mid:]) / (len(prices) - mid)
                
                if second_half_avg < first_half_avg * 0.95:
                    trend = "falling"
                elif second_half_avg > first_half_avg * 1.05:
                    trend = "rising"
                else:
                    trend = "stable"
            else:
                trend = "insufficient_data"
            
            return {
                "trend": trend,
                "current": current,
                "average": round(avg, 2),
                "min": min(prices),
                "max": max(prices),
                "data_points": len(prices)
            }
    finally:
        conn.close()


# ============================================================
# ALERT CONFIG AND COOLDOWN
# ============================================================

def get_threshold_override(destination: str) -> Optional[dict]:
    """Get user-custom thresholds."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT flight_threshold, hotel_threshold, drop_pct
                FROM alert_config WHERE destination = %s
            """, (destination,))
            return cur.fetchone()
    finally:
        conn.close()


def set_threshold_override(destination: str, flight_threshold: float,
                           hotel_threshold: float, drop_pct: int = 20) -> None:
    """Upsert custom thresholds."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alert_config (destination, flight_threshold, 
                                          hotel_threshold, drop_pct, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (destination) DO UPDATE SET
                    flight_threshold = EXCLUDED.flight_threshold,
                    hotel_threshold = EXCLUDED.hotel_threshold,
                    drop_pct = EXCLUDED.drop_pct,
                    updated_at = NOW()
            """, (destination, flight_threshold, hotel_threshold, drop_pct))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to set threshold: {e}")
    finally:
        conn.close()


def check_cooldown(alert_key: str, cooldown_hours: int = 12) -> bool:
    """Check if alert is in cooldown. Returns True if OK to send."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Clean expired
            cur.execute("DELETE FROM alert_cooldowns WHERE expires_at < NOW()")
            
            cur.execute("""
                SELECT last_sent FROM alert_cooldowns WHERE alert_key = %s
            """, (alert_key,))
            row = cur.fetchone()
            
            if row:
                return False  # Still in cooldown
            
            # Set cooldown
            cur.execute("""
                INSERT INTO alert_cooldowns (alert_key, last_sent, expires_at)
                VALUES (%s, NOW(), NOW() + INTERVAL '%s hours')
                ON CONFLICT (alert_key) DO UPDATE SET
                    last_sent = NOW(),
                    expires_at = NOW() + INTERVAL '%s hours'
            """, (alert_key, cooldown_hours, cooldown_hours))
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Cooldown check failed: {e}")
        return True  # Fail open: send the alert
    finally:
        conn.close()


def cleanup_old_data() -> dict:
    """Remove data older than retention period."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM flight_prices WHERE created_at < NOW() - INTERVAL '90 days'")
            flights_deleted = cur.rowcount
            cur.execute("DELETE FROM hotel_prices WHERE created_at < NOW() - INTERVAL '90 days'")
            hotels_deleted = cur.rowcount
            cur.execute("DELETE FROM offers WHERE created_at < NOW() - INTERVAL '30 days'")
            offers_deleted = cur.rowcount
            cur.execute("DELETE FROM alert_cooldowns WHERE expires_at < NOW()")
            cooldowns_deleted = cur.rowcount
        conn.commit()
        return {
            "flights_deleted": flights_deleted,
            "hotels_deleted": hotels_deleted,
            "offers_deleted": offers_deleted,
            "cooldowns_deleted": cooldowns_deleted
        }
    except Exception as e:
        conn.rollback()
        logger.error(f"Cleanup failed: {e}")
        return {}
    finally:
        conn.close()
# ============================================================
# COMPATIBILITY HELPERS (used by flight_checker)
# ============================================================

def save_flight_price(route: str, price: float, airline: str = "unknown",
                      is_direct: bool = False, stops: int = 0):

    destination = route.split("-")[-1]

    return record_flight_price(
        route=route,
        destination_name=destination,
        price=price,
        airline=airline,
        stops=stops,
        is_direct=is_direct,
        departure_date=None,
        return_date=None,
        duration=None,
        booking_class="ECONOMY",
        metadata={}
    )
