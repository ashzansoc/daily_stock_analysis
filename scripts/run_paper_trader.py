#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Paper Trading Runner
====================
Runs the stock analysis and automatically evaluates trades.
Sends notifications via Telegram for every trade and daily summary.

Usage:
    python scripts/run_paper_trader.py
    python scripts/run_paper_trader.py --status    # Show portfolio status
    python scripts/run_paper_trader.py --reset     # Reset portfolio to $10,000
"""

import sys
import os
import json
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import setup_env, get_config
from src.logging_config import setup_logging

setup_env()
setup_logging(log_prefix="paper_trader")

import logging
from src.paper_trader import PaperTradingEngine
from src.storage import get_db

logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8957774149:AAGofeyZglf8sv_-B3Q14Q7d91FiCARdVvc")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "813594189")
INITIAL_CAPITAL = float(os.getenv("PAPER_TRADING_CAPITAL", "10000"))
STOCK_LIST = os.getenv("STOCK_LIST", "AAPL,TSLA,NVDA").replace(" ", ",").split(",")
PORTFOLIO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "paper_portfolio.json",
)


def get_latest_analyses() -> list:
    """Get the most recent analysis for each stock in watchlist."""
    db = get_db()
    results = []

    for code in STOCK_LIST:
        code = code.strip()
        if not code:
            continue

        history = db.get_analysis_history(code=code, days=2, limit=1)
        if history:
            record = history[0]
            raw_result = {}
            if record.raw_result:
                try:
                    raw_result = json.loads(record.raw_result)
                except (json.JSONDecodeError, TypeError):
                    pass

            results.append({
                "code": record.code,
                "name": record.name or record.code,
                "sentiment_score": record.sentiment_score,
                "operation_advice": record.operation_advice,
                "trend_prediction": record.trend_prediction,
                "ideal_buy": record.ideal_buy,
                "secondary_buy": record.secondary_buy,
                "stop_loss": record.stop_loss,
                "take_profit": record.take_profit,
                "raw_result": raw_result,
                "analysis_date": record.created_at.isoformat() if record.created_at else None,
            })

    return results


def get_current_prices(codes: list) -> dict:
    """Fetch current prices for exit checking."""
    prices = {}
    try:
        import yfinance as yf
        for code in codes:
            try:
                ticker = yf.Ticker(code)
                info = ticker.fast_info
                price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                if price:
                    prices[code] = float(price)
            except Exception as e:
                logger.warning(f"Failed to get price for {code}: {e}")
    except ImportError:
        logger.warning("yfinance not available for price fetching")

    return prices


def run_paper_trading():
    """Main paper trading loop."""
    logger.info("=" * 50)
    logger.info("Paper Trading Engine - Starting")
    logger.info("=" * 50)

    # Initialize engine
    engine = PaperTradingEngine(
        initial_capital=INITIAL_CAPITAL,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        portfolio_file=PORTFOLIO_FILE,
    )

    # Step 1: Check exits on existing positions
    if engine.portfolio.open_positions:
        codes = [p.stock_code for p in engine.portfolio.open_positions]
        logger.info(f"Checking exits for: {codes}")
        prices = get_current_prices(codes)
        if prices:
            exit_msgs = engine.check_exits(prices)
            for msg in exit_msgs:
                logger.info(f"Exit triggered: {msg[:100]}...")

    # Step 2: Get latest analyses and evaluate new trades (with live price check)
    analyses = get_latest_analyses()
    logger.info(f"Found {len(analyses)} recent analyses to evaluate")

    # Fetch live prices for all watchlist stocks
    all_codes = [a["code"] for a in analyses]
    live_prices = get_current_prices(all_codes)
    logger.info(f"Got live prices for {len(live_prices)} stocks")

    # Send 10-min heartbeat to Telegram
    if engine.telegram and live_prices:
        from datetime import datetime
        now = datetime.utcnow()
        # Count stocks at entry, near entry, and far from entry
        at_entry = []
        near_entry = []
        for a in analyses:
            code = a["code"]
            price = live_prices.get(code)
            ideal = a.get("ideal_buy")
            if price and ideal and ideal > 0:
                diff_pct = ((price - ideal) / ideal) * 100
                if diff_pct <= 0:
                    at_entry.append(f"{code} ${price:.2f} (at entry)")
                elif diff_pct <= 2:
                    near_entry.append(f"{code} ${price:.2f} ({diff_pct:.1f}% above)")

        positions_info = f"Open: {len(engine.portfolio.open_positions)}/5"
        cash_info = f"Cash: ${engine.portfolio.cash:,.0f}"

        heartbeat = (
            f"📡 Price Check | {now.strftime('%H:%M')} UTC\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 Fetched {len(live_prices)}/30 stocks\n"
            f"💼 {positions_info} | {cash_info}\n"
        )
        if at_entry:
            heartbeat += f"\n🎯 AT ENTRY LEVEL:\n" + "\n".join(f"  • {s}" for s in at_entry) + "\n"
        if near_entry:
            heartbeat += f"\n👀 Near entry (<2%):\n" + "\n".join(f"  • {s}" for s in near_entry) + "\n"
        if not at_entry and not near_entry:
            heartbeat += f"\n⏳ No stocks at entry levels yet\n"

        engine.telegram.send(heartbeat)

    for analysis in analyses:
        code = analysis["code"]
        score = analysis.get("sentiment_score", 0)
        advice = analysis.get("operation_advice", "")
        ideal_buy = analysis.get("ideal_buy")
        secondary_buy = analysis.get("secondary_buy")
        current_price = live_prices.get(code)

        # Add live price to analysis for the engine to use
        if current_price:
            analysis["current_price"] = current_price

        # Check if live price has reached entry levels
        price_at_entry = False
        if current_price and ideal_buy and current_price <= ideal_buy:
            price_at_entry = True
            logger.info(f"[{code}] Price ${current_price:.2f} reached ideal entry ${ideal_buy:.2f}")
        elif current_price and secondary_buy and current_price <= secondary_buy:
            price_at_entry = True
            logger.info(f"[{code}] Price ${current_price:.2f} reached secondary entry ${secondary_buy:.2f}")

        if price_at_entry:
            logger.info(f"Evaluating {code}: score={score}, advice='{advice}', price=${current_price:.2f}")
            result = engine.evaluate_and_trade(analysis)
            if result:
                logger.info(f"Trade executed for {code}")
        else:
            # Still evaluate if analysis explicitly says buy (score >= 60 + buy signal)
            if score and score >= 60:
                logger.info(f"Evaluating {code} (high score): score={score}, advice='{advice}'")
                result = engine.evaluate_and_trade(analysis)
                if result:
                    logger.info(f"Trade executed for {code}")
            else:
                logger.debug(f"Skipping {code}: score={score}, price not at entry level")

    logger.info("Paper Trading Engine - Complete")
    return engine


def show_status():
    """Show current portfolio status."""
    engine = PaperTradingEngine(
        initial_capital=INITIAL_CAPITAL,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        portfolio_file=PORTFOLIO_FILE,
    )
    print(engine.get_status().replace("*", "").replace("━", "-"))
    print("\nPortfolio data:", PORTFOLIO_FILE)


def reset_portfolio():
    """Reset portfolio to initial state."""
    engine = PaperTradingEngine(
        initial_capital=INITIAL_CAPITAL,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        portfolio_file=PORTFOLIO_FILE,
    )

    # Send reset notification
    if engine.telegram:
        engine.telegram.send(
            f"🔄 *Paper Portfolio Reset*\n\n"
            f"Capital: ${INITIAL_CAPITAL:,.2f}\n"
            f"All positions closed.\n"
            f"Starting fresh!"
        )

    # Delete and recreate
    if os.path.exists(PORTFOLIO_FILE):
        os.remove(PORTFOLIO_FILE)

    engine = PaperTradingEngine(
        initial_capital=INITIAL_CAPITAL,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        portfolio_file=PORTFOLIO_FILE,
    )
    engine._save_portfolio()
    print(f"Portfolio reset to ${INITIAL_CAPITAL:,.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper Trading Engine")
    parser.add_argument("--status", action="store_true", help="Show portfolio status")
    parser.add_argument("--reset", action="store_true", help="Reset portfolio")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.reset:
        reset_portfolio()
    else:
        run_paper_trading()
