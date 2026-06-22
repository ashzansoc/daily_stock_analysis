#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot for Paper Trading
===============================
Listens for commands and responds with portfolio status.

Commands:
    /status  - Show portfolio status and system info
    /trades  - Show recent trades
    /help    - Show available commands

Usage:
    python scripts/telegram_bot.py
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8957774149:AAGofeyZglf8sv_-B3Q14Q7d91FiCARdVvc")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "813594189")
STOCK_LIST = os.getenv("STOCK_LIST", "AAPL,TSLA,NVDA")
PORTFOLIO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "paper_portfolio.json",
)
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def load_portfolio() -> dict:
    """Load portfolio from disk."""
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "initial_capital": 10000.0,
            "cash": 10000.0,
            "positions": [],
            "closed_trades": [],
        }


def get_status_message() -> str:
    """Build the /status response message."""
    portfolio = load_portfolio()
    capital = portfolio.get("initial_capital", 10000)
    cash = portfolio.get("cash", capital)
    positions = [p for p in portfolio.get("positions", []) if p.get("status") == "open"]
    closed = portfolio.get("closed_trades", [])
    total_invested = sum(p.get("entry_price", 0) * p.get("shares", 0) for p in positions)
    total_value = cash + total_invested
    pnl = total_value - capital
    pnl_pct = (pnl / capital) * 100 if capital > 0 else 0

    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    win_rate = (wins / len(closed) * 100) if closed else 0

    stocks = STOCK_LIST.replace(",", ", ")

    msg = (
        f"📊 Paper Trading Portfolio\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital: ${capital:,.2f}\n"
        f"💵 Cash: ${cash:,.2f}\n"
        f"📈 Total Value: ${total_value:,.2f}\n"
        f"💹 P&L: ${pnl:,.2f} ({pnl_pct:+.1f}%)\n\n"
        f"📋 Open Positions: {len(positions)}/5\n"
        f"✅ Closed Trades: {len(closed)}\n"
        f"🎯 Win Rate: {win_rate:.0f}%\n\n"
    )

    if positions:
        msg += "Open Positions:\n"
        for p in positions:
            msg += (
                f"• {p.get('stock_code')} | {p.get('shares')} shares @ ${p.get('entry_price', 0):.2f}\n"
                f"  SL: ${p.get('stop_loss', 0):.2f} | TP: ${p.get('take_profit', 0):.2f}\n"
            )
        msg += "\n"

    msg += (
        f"📊 Watchlist: {stocks}\n\n"
        f"System active. Checking hourly during market hours.\n"
        f"Will trade only when ALL conditions are met:\n"
        f"- Score >= 60\n"
        f"- Buy signal\n"
        f"- Bullish trend\n"
        f"- R:R >= 2:1\n"
        f"- Levels defined\n"
        f"- Not already holding\n"
        f"- Cash available"
    )

    return msg


def get_trades_message() -> str:
    """Build the /trades response message."""
    portfolio = load_portfolio()
    closed = portfolio.get("closed_trades", [])

    if not closed:
        return "📋 No closed trades yet. Waiting for conditions to be met."

    msg = "📋 Recent Trades\n━━━━━━━━━━━━━━━━━━\n\n"
    for trade in closed[-5:]:  # Last 5 trades
        emoji = "🟢" if trade.get("pnl", 0) > 0 else "🔴"
        pnl = trade.get("pnl", 0)
        msg += (
            f"{emoji} {trade.get('stock_code')} | "
            f"${trade.get('entry_price', 0):.2f} → ${trade.get('exit_price', 0):.2f} | "
            f"P&L: ${pnl:.2f}\n"
        )

    return msg


def get_help_message() -> str:
    """Build the /help response."""
    return (
        "🤖 Paper Trading Bot Commands\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "/status - Portfolio status & system info\n"
        "/trades - Recent closed trades\n"
        "/help - Show this message\n"
    )


def send_message(chat_id: str, text: str) -> bool:
    """Send a message via Telegram."""
    try:
        resp = requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return False


def get_updates(offset: int = 0) -> list:
    """Get new messages from Telegram."""
    try:
        resp = requests.get(
            f"{BASE_URL}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception as e:
        logger.error(f"Failed to get updates: {e}")
    return []


def handle_message(message: dict):
    """Process an incoming message."""
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip().lower()

    # Only respond to our authorized chat
    if chat_id != CHAT_ID:
        logger.warning(f"Unauthorized chat_id: {chat_id}")
        return

    if text == "/status" or text == "/start":
        send_message(chat_id, get_status_message())
    elif text == "/trades":
        send_message(chat_id, get_trades_message())
    elif text == "/help":
        send_message(chat_id, get_help_message())
    elif text.startswith("/"):
        send_message(chat_id, "Unknown command. Try /status, /trades, or /help")


def run_bot():
    """Main bot polling loop."""
    logger.info("Telegram bot starting...")
    logger.info(f"Authorized chat ID: {CHAT_ID}")
    logger.info(f"Watchlist: {STOCK_LIST}")

    offset = 0

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    handle_message(message)
        except KeyboardInterrupt:
            logger.info("Bot stopped.")
            break
        except Exception as e:
            logger.error(f"Error in bot loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_bot()
