#!/usr/bin/env python3
"""Send a Telegram notification that the market has closed with daily summary."""

import os
import sys
import json
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8957774149:AAGofeyZglf8sv_-B3Q14Q7d91FiCARdVvc")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "813594189")

PORTFOLIO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "paper_portfolio.json"
)

try:
    with open(PORTFOLIO_FILE) as f:
        portfolio = json.load(f)
    capital = portfolio.get("initial_capital", 10000)
    cash = portfolio.get("cash", 10000)
    positions = [p for p in portfolio.get("positions", []) if p.get("status") == "open"]
    closed = portfolio.get("closed_trades", [])
    total_invested = sum(p.get("entry_price", 0) * p.get("shares", 0) for p in positions)
    total_value = cash + total_invested
    pnl = total_value - capital
    pnl_pct = (pnl / capital) * 100 if capital > 0 else 0
    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    win_rate = (wins / len(closed) * 100) if closed else 0
except:
    capital = 10000
    cash = 10000
    positions = []
    closed = []
    total_value = 10000
    pnl = 0
    pnl_pct = 0
    win_rate = 0

now = datetime.utcnow().strftime("%H:%M UTC")

msg = (
    f"🔕 US MARKET CLOSED | {now}\n"
    f"━━━━━━━━━━━━━━━━━━\n"
    f"🤖 Bot going to sleep until tomorrow\n\n"
    f"📊 Today's Summary:\n"
    f"💰 Portfolio Value: ${total_value:,.2f}\n"
    f"💹 P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%)\n"
    f"📋 Open Positions: {len(positions)}/5\n"
    f"✅ Total Closed Trades: {len(closed)}\n"
    f"🎯 Win Rate: {win_rate:.0f}%\n"
)

if positions:
    msg += "\nHolding overnight:\n"
    for p in positions:
        msg += f"  • {p.get('stock_code')} | {p.get('shares')} shares @ ${p.get('entry_price', 0):.2f}\n"

msg += "\nNext session: Tomorrow 7:00 PM IST. Good night!"

resp = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    json={"chat_id": CHAT_ID, "text": msg},
    timeout=10,
)
print(f"Sent: {resp.status_code == 200}")
