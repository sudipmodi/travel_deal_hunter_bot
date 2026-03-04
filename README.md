# Travel Deal Hunter Bot

Personal travel intelligence bot monitoring flights + hotels + loyalty offers + credit card portal deals across 13 destinations from Ahmedabad. Sends instant Telegram alerts on price drops and weekly digest reports.

## Architecture

```
Render Web Service (Flask + Gunicorn)
├── Telegram Webhook (/webhook) - Bot commands
├── APScheduler (background)
│   ├── Flight Checker (twice daily) --> Amadeus API
│   ├── Hotel Checker (twice daily) --> SerpAPI
│   ├── Loyalty Scraper (weekly) --> Playwright
│   ├── CC Portal Scraper (weekly) --> Playwright
│   ├── Airline Promo Scraper (weekly) --> Playwright
│   ├── Weekly Digest (Sunday 9 AM IST)
│   └── Data Cleanup (monthly)
└── Health Check (/health)

Supabase PostgreSQL
├── flight_prices (90 day retention)
├── hotel_prices (90 day retention)
├── offers (30 day retention)
├── price_baselines (rolling averages)
├── alert_config (custom thresholds)
└── alert_cooldowns (spam prevention)
```

## Setup

### 1. Telegram Bot
1. Message @BotFather on Telegram
2. Send /newbot and follow prompts
3. Save the bot token
4. Send a message to your bot then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find your chat_id from the response

### 2. Supabase
1. Create free account at supabase.com
2. Create a new project
3. Go to Settings > Database > Connection string
4. Copy the URI (replace [YOUR-PASSWORD] with your DB password)
5. Also copy the Project URL and service_role key from Settings > API

### 3. Amadeus API
1. Register at developers.amadeus.com
2. Create an app (Self-Service tier is free)
3. Copy API Key and API Secret

### 4. SerpAPI
1. Register at serpapi.com
2. Free tier: 100 searches/month
3. Copy your API key

### 5. Deploy to Render
1. Push this repo to GitHub
2. Go to render.com > New Web Service
3. Connect your GitHub repo
4. Render auto-detects render.yaml
5. Add environment variables:
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
   - DATABASE_URL (Supabase connection string)
   - SUPABASE_URL
   - SUPABASE_KEY
   - AMADEUS_CLIENT_ID
   - AMADEUS_CLIENT_SECRET
   - SERPAPI_KEY
6. Deploy

### 6. Set Telegram Webhook
After deploy replace YOUR_RENDER_URL:
```
curl https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://YOUR_RENDER_URL.onrender.com/webhook
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| /start | Help and command list |
| /check <dest> | Flight + hotel prices for destination |
| /flights <dest> | Flight prices only |
| /hotels <dest> | Hotel prices only |
| /deals | Best deals across all destinations |
| /offers | Active loyalty and CC portal offers |
| /config | View alert thresholds |
| /set <dest> <flight> <hotel> | Update thresholds |
| /status | Bot health |

## Destinations

**Domestic:** Kerala | Hyderabad | Tamil Nadu | Karnataka | Assam | Odisha
**International:** Japan | London | Singapore | Bali | Indonesia | Vietnam | UAE

## Loyalty Programs Tracked

**Hotels:** Accor ALL (primary) | Marriott Bonvoy | ITC Hotels
**Airlines:** KrisFlyer | Flying Returns | Skywards | Avios | InterMiles | Etihad Guest | ANA | JAL
**CC Portals:** HDFC SmartBuy | Axis Travel | Amex Travel

## Cost

- Render: $0 (free tier)
- Supabase: $0 (free tier / 500MB)
- Amadeus: $0 (free tier / 500 calls/month)
- SerpAPI: $0 (free tier / 100 searches/month)
- **Total: $0/month**

## Caveats

- Render free tier spins down after 15 min idle. First Telegram command after idle takes 30-50s.
- SerpAPI free tier limits hotel searches to ~100/month. Config is tuned to stay within this.
- Scraping (loyalty/CC/airline) may break if sites change their HTML structure. Selectors need periodic maintenance.
- Amadeus free tier is sandbox by default. Apply for production access for real prices.

## File Structure

```
travel-bot/
├── src/
│   ├── app.py                  # Main entry (Flask + scheduler)
│   ├── config/
│   │   └── config.json         # All destinations + thresholds + loyalty config
│   ├── db/
│   │   └── database.py         # Supabase PostgreSQL operations
│   ├── handlers/
│   │   ├── telegram_bot.py     # Webhook handler + all commands
│   │   ├── telegram_alerts.py  # Alert formatting + sending
│   │   ├── flight_checker.py   # Amadeus API flight search
│   │   ├── hotel_checker.py    # SerpAPI hotel search
│   │   └── digest_generator.py # Weekly digest compiler
│   └── scrapers/
│       ├── loyalty_scraper.py  # Accor / Marriott / ITC offers
│       ├── cc_portal_scraper.py # HDFC / Axis / Amex portals
│       └── airline_promo_scraper.py # Airline sale fares + bonus miles
├── config/
│   └── config.json
├── render.yaml                 # Render IaC
├── Dockerfile                  # Local dev
├── requirements.txt
└── .env.example
```
