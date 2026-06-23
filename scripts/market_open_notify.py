#!/usr/bin/env python3
"""Send a Telegram notification that the market is open and the bot is active."""

import os
import sys
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8957774149:AAGofeyZglf8sv_-B3Q14Q7d91FiCARdVvc")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "813594189")
STOCK_LIST = os.getenv("STOCK_LIST", "AAPL,TSLA,NVDA")

# Load portfolio status
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json

PORTFOLIO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "paper_portfolio.json"
)

try:
    with open(PORTFOLIO_FILE) as f:
        portfolio = json.load(f)
    cash = portfolio.get("cash", 10000)
    positions = len([p for p in portfolio.get("positions", []) if p.get("status") == "open"])
    closed = len(portfolio.get("closed_trades", []))
except:
    cash = 10000
    positions = 0
    closed = 0

stocks = STOCK_LIST.replace(",", ", ")
now = datetime.utcnow().strftime("%H:%M UTC")

msg = (
    f"🔔 US MARKET OPEN | {now}\n"
    f"━━━━━━━━━━━━━━━━━━\n"
    f"🤖 Paper Trading Bot ACTIVE\n\n"
    f"💰 Cash: ${cash:,.2f}\n"
    f"📋 Open Positions: {positions}/5\n"
    f"✅ Closed Trades: {closed}\n\n"
    f"📊 Monitoring {len(STOCK_LIST.split(','))} stocks every 10 min\n"
    f"🎯 Will auto-trade when ALL conditions met\n\n"
    f"Market closes at 2:00 AM IST. Good luck!"
)

resp = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    json={"chat_id": CHAT_ID, "text": msg},
    timeout=10,
)
print(f"Sent: {resp.status_code == 200}")
