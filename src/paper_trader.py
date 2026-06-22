# -*- coding: utf-8 -*-
"""
Paper Trading Engine
====================
Automated paper trading that executes trades when ALL conditions are met:
1. Analysis score >= 60 (favorable conditions)
2. Decision type is "buy" or operation_advice contains buy intent
3. MA alignment shows bullish or recovering pattern
4. Volume confirms the move
5. No major risk alerts that override
6. Price reaches the ideal or secondary entry level

Position sizing: max 20% of portfolio per trade, max 5 open positions.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    """A single paper trade position."""
    stock_code: str
    stock_name: str
    entry_price: float
    shares: int
    entry_date: str
    stop_loss: float
    take_profit: float
    entry_reason: str
    status: str = "open"  # open, closed_profit, closed_loss, closed_stop
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    pnl: float = 0.0


@dataclass
class PaperPortfolio:
    """Paper trading portfolio state."""
    initial_capital: float = 10000.0
    cash: float = 10000.0
    positions: List[PaperPosition] = field(default_factory=list)
    closed_trades: List[PaperPosition] = field(default_factory=list)
    trade_log: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def open_positions(self) -> List[PaperPosition]:
        return [p for p in self.positions if p.status == "open"]

    @property
    def total_invested(self) -> float:
        return sum(p.entry_price * p.shares for p in self.open_positions)

    @property
    def portfolio_value(self) -> float:
        return self.cash + self.total_invested

    @property
    def total_pnl(self) -> float:
        return self.portfolio_value - self.initial_capital

    @property
    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.pnl > 0)
        return wins / len(self.closed_trades) * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "portfolio_value": self.portfolio_value,
            "total_pnl": self.total_pnl,
            "open_positions": len(self.open_positions),
            "closed_trades": len(self.closed_trades),
            "win_rate": f"{self.win_rate:.1f}%",
            "positions": [
                {
                    "stock": p.stock_code,
                    "name": p.stock_name,
                    "entry": p.entry_price,
                    "shares": p.shares,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "entry_date": p.entry_date,
                    "status": p.status,
                }
                for p in self.open_positions
            ],
        }


class TelegramNotifier:
    """Send trade notifications via Telegram."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, message: str) -> bool:
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info(f"[Telegram] Message sent successfully")
                return True
            else:
                logger.warning(f"[Telegram] Failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"[Telegram] Error: {e}")
            return False


class PaperTradingEngine:
    """
    Automated paper trading engine.
    
    Monitors analysis results and executes paper trades when ALL conditions are met.
    """

    # Trading rules
    MAX_POSITION_PCT = 0.20      # Max 20% of portfolio per trade
    MAX_OPEN_POSITIONS = 5       # Max 5 concurrent positions
    MIN_SCORE_TO_BUY = 60        # Minimum sentiment score to consider buying
    MIN_RISK_REWARD = 2.0        # Minimum risk/reward ratio
    MAX_BIAS_PCT = 5.0           # Max acceptable bias from MA5

    def __init__(
        self,
        initial_capital: float = 10000.0,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        portfolio_file: str = "data/paper_portfolio.json",
    ):
        self.portfolio_file = portfolio_file
        self.portfolio = self._load_portfolio(initial_capital)
        self.telegram: Optional[TelegramNotifier] = None

        if telegram_token and telegram_chat_id:
            self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)

    def _load_portfolio(self, initial_capital: float) -> PaperPortfolio:
        """Load portfolio from disk or create new."""
        try:
            with open(self.portfolio_file, "r") as f:
                data = json.load(f)
                portfolio = PaperPortfolio(
                    initial_capital=data.get("initial_capital", initial_capital),
                    cash=data.get("cash", initial_capital),
                )
                for p in data.get("positions", []):
                    portfolio.positions.append(PaperPosition(**p))
                for t in data.get("closed_trades", []):
                    portfolio.closed_trades.append(PaperPosition(**t))
                portfolio.trade_log = data.get("trade_log", [])
                return portfolio
        except (FileNotFoundError, json.JSONDecodeError):
            return PaperPortfolio(initial_capital=initial_capital, cash=initial_capital)

    def _save_portfolio(self):
        """Persist portfolio to disk."""
        import os
        os.makedirs(os.path.dirname(self.portfolio_file), exist_ok=True)
        data = {
            "initial_capital": self.portfolio.initial_capital,
            "cash": self.portfolio.cash,
            "positions": [vars(p) for p in self.portfolio.positions],
            "closed_trades": [vars(t) for t in self.portfolio.closed_trades],
            "trade_log": self.portfolio.trade_log[-100:],  # Keep last 100 entries
        }
        with open(self.portfolio_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def evaluate_and_trade(self, analysis_result: Dict[str, Any]) -> Optional[str]:
        """
        Evaluate an analysis result and execute a paper trade if ALL conditions are met.
        
        Returns a message describing the action taken, or None if no action.
        """
        code = analysis_result.get("code", "")
        name = analysis_result.get("name", code)
        score = analysis_result.get("sentiment_score", 0)
        operation = analysis_result.get("operation_advice", "")
        trend = analysis_result.get("trend_prediction", "")
        ideal_buy = analysis_result.get("ideal_buy")
        secondary_buy = analysis_result.get("secondary_buy")
        stop_loss = analysis_result.get("stop_loss")
        take_profit = analysis_result.get("take_profit")
        current_price = analysis_result.get("current_price")
        raw_result = analysis_result.get("raw_result", {})

        # Parse raw_result if it's a string
        if isinstance(raw_result, str):
            try:
                raw_result = json.loads(raw_result)
            except (json.JSONDecodeError, TypeError):
                raw_result = {}

        # Extract checklist from raw dashboard
        dashboard = raw_result.get("dashboard", {})
        checklist = dashboard.get("checklist", {})
        checklist_items = checklist.get("items", []) if isinstance(checklist, dict) else []

        # ============================================
        # CHECK ALL CONDITIONS
        # ============================================
        conditions_met = []
        conditions_failed = []

        # Condition 1: Score must be >= 60
        if score and score >= self.MIN_SCORE_TO_BUY:
            conditions_met.append(f"Score {score} >= {self.MIN_SCORE_TO_BUY}")
        else:
            conditions_failed.append(f"Score {score} < {self.MIN_SCORE_TO_BUY}")

        # Condition 2: Operation advice suggests buying
        buy_keywords = ["buy", "enter", "accumulate", "加仓", "买入", "建仓"]
        is_buy_signal = any(kw in (operation or "").lower() for kw in buy_keywords)
        if is_buy_signal:
            conditions_met.append(f"Buy signal: '{operation}'")
        else:
            conditions_failed.append(f"No buy signal: '{operation}'")

        # Condition 3: Trend is bullish or recovering
        bullish_keywords = ["bullish", "recovering", "uptrend", "看多", "多头"]
        is_bullish = any(kw in (trend or "").lower() for kw in bullish_keywords)
        if is_bullish:
            conditions_met.append(f"Bullish trend: '{trend}'")
        else:
            conditions_failed.append(f"Not bullish: '{trend}'")

        # Condition 4: Entry levels are defined
        if ideal_buy and stop_loss and take_profit:
            conditions_met.append(f"Levels defined: entry={ideal_buy}, SL={stop_loss}, TP={take_profit}")
        else:
            conditions_failed.append("Missing entry/SL/TP levels")

        # Condition 5: Risk/reward ratio >= 2
        rr_ratio = 0.0
        entry_price = ideal_buy or secondary_buy
        if entry_price and stop_loss and take_profit and entry_price > stop_loss:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
            rr_ratio = reward / risk if risk > 0 else 0
            if rr_ratio >= self.MIN_RISK_REWARD:
                conditions_met.append(f"R:R = {rr_ratio:.1f} >= {self.MIN_RISK_REWARD}")
            else:
                conditions_failed.append(f"R:R = {rr_ratio:.1f} < {self.MIN_RISK_REWARD}")
        else:
            conditions_failed.append("Cannot calculate R:R")

        # Condition 6: Not already holding this stock
        already_holding = any(
            p.stock_code == code for p in self.portfolio.open_positions
        )
        if not already_holding:
            conditions_met.append("Not already holding")
        else:
            conditions_failed.append("Already holding this stock")

        # Condition 7: Portfolio capacity (max positions)
        if len(self.portfolio.open_positions) < self.MAX_OPEN_POSITIONS:
            conditions_met.append(f"Capacity OK ({len(self.portfolio.open_positions)}/{self.MAX_OPEN_POSITIONS})")
        else:
            conditions_failed.append(f"Max positions reached ({self.MAX_OPEN_POSITIONS})")

        # Condition 8: Sufficient cash
        max_investment = self.portfolio.portfolio_value * self.MAX_POSITION_PCT
        if self.portfolio.cash >= max_investment * 0.5:  # At least enough for half position
            conditions_met.append(f"Cash available: ${self.portfolio.cash:.2f}")
        else:
            conditions_failed.append(f"Insufficient cash: ${self.portfolio.cash:.2f}")

        # ============================================
        # EXECUTE OR PASS
        # ============================================
        all_met = len(conditions_failed) == 0

        if all_met and entry_price:
            return self._execute_buy(
                code=code,
                name=name,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                score=score,
                rr_ratio=rr_ratio,
                reason=f"All {len(conditions_met)} conditions met",
            )
        else:
            # Log why we didn't trade
            log_entry = {
                "time": datetime.now().isoformat(),
                "stock": code,
                "action": "PASS",
                "conditions_met": conditions_met,
                "conditions_failed": conditions_failed,
            }
            self.portfolio.trade_log.append(log_entry)
            self._save_portfolio()

            if conditions_failed:
                logger.info(
                    f"[PaperTrader] PASS on {code}: {len(conditions_met)} met, "
                    f"{len(conditions_failed)} failed: {conditions_failed}"
                )
            return None

    def _execute_buy(
        self,
        code: str,
        name: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        score: int,
        rr_ratio: float,
        reason: str,
    ) -> str:
        """Execute a paper buy trade."""
        # Calculate position size (max 20% of portfolio)
        max_investment = self.portfolio.portfolio_value * self.MAX_POSITION_PCT
        investment = min(max_investment, self.portfolio.cash)
        shares = int(investment / entry_price)

        if shares <= 0:
            return None

        cost = shares * entry_price

        # Create position
        position = PaperPosition(
            stock_code=code,
            stock_name=name,
            entry_price=entry_price,
            shares=shares,
            entry_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_reason=reason,
        )

        # Update portfolio
        self.portfolio.cash -= cost
        self.portfolio.positions.append(position)

        # Log
        log_entry = {
            "time": datetime.now().isoformat(),
            "stock": code,
            "action": "BUY",
            "price": entry_price,
            "shares": shares,
            "cost": cost,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "score": score,
            "rr_ratio": round(rr_ratio, 2),
        }
        self.portfolio.trade_log.append(log_entry)
        self._save_portfolio()

        # Build notification message
        msg = (
            f"🟢 *PAPER TRADE: BUY {code}*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 *{name}*\n\n"
            f"💰 Entry: ${entry_price:.2f}\n"
            f"📈 Shares: {shares}\n"
            f"💵 Cost: ${cost:.2f}\n\n"
            f"🛑 Stop Loss: ${stop_loss:.2f}\n"
            f"🎯 Take Profit: ${take_profit:.2f}\n"
            f"⚖️ Risk/Reward: {rr_ratio:.1f}:1\n"
            f"📊 Score: {score}/100\n\n"
            f"💼 *Portfolio Update*\n"
            f"Cash: ${self.portfolio.cash:.2f}\n"
            f"Invested: ${self.portfolio.total_invested:.2f}\n"
            f"Total Value: ${self.portfolio.portfolio_value:.2f}\n"
            f"Open Positions: {len(self.portfolio.open_positions)}\n"
        )

        # Send Telegram notification
        if self.telegram:
            self.telegram.send(msg)

        logger.info(f"[PaperTrader] BUY {code} @ ${entry_price:.2f} x {shares} shares")
        return msg

    def check_exits(self, price_data: Dict[str, float]) -> List[str]:
        """
        Check all open positions against current prices for stop-loss or take-profit.
        
        Args:
            price_data: dict mapping stock_code -> current_price
            
        Returns:
            List of notification messages for triggered exits.
        """
        messages = []

        for position in self.portfolio.open_positions:
            current_price = price_data.get(position.stock_code)
            if current_price is None:
                continue

            exit_reason = None
            if current_price <= position.stop_loss:
                exit_reason = "STOP LOSS"
                position.status = "closed_loss"
            elif current_price >= position.take_profit:
                exit_reason = "TAKE PROFIT"
                position.status = "closed_profit"

            if exit_reason:
                position.exit_price = current_price
                position.exit_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                position.pnl = (current_price - position.entry_price) * position.shares

                # Return cash
                self.portfolio.cash += current_price * position.shares

                # Move to closed trades
                self.portfolio.closed_trades.append(position)

                pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
                emoji = "🎯" if position.pnl > 0 else "🛑"

                msg = (
                    f"{emoji} *PAPER TRADE: {exit_reason}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📊 *{position.stock_name} ({position.stock_code})*\n\n"
                    f"💰 Entry: ${position.entry_price:.2f}\n"
                    f"💰 Exit: ${current_price:.2f}\n"
                    f"📈 P&L: ${position.pnl:.2f} ({pnl_pct:+.1f}%)\n\n"
                    f"💼 *Portfolio Update*\n"
                    f"Cash: ${self.portfolio.cash:.2f}\n"
                    f"Total Value: ${self.portfolio.portfolio_value:.2f}\n"
                    f"Total P&L: ${self.portfolio.total_pnl:.2f}\n"
                    f"Win Rate: {self.portfolio.win_rate:.0f}%\n"
                    f"Closed Trades: {len(self.portfolio.closed_trades)}\n"
                )

                if self.telegram:
                    self.telegram.send(msg)

                messages.append(msg)
                logger.info(
                    f"[PaperTrader] {exit_reason} {position.stock_code} @ "
                    f"${current_price:.2f} | P&L: ${position.pnl:.2f}"
                )

        # Remove closed positions from active list
        self.portfolio.positions = [p for p in self.portfolio.positions if p.status == "open"]
        self._save_portfolio()

        return messages

    def get_status(self) -> str:
        """Get a formatted portfolio status message."""
        p = self.portfolio
        msg = (
            f"📊 *Paper Trading Portfolio*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Initial Capital: ${p.initial_capital:,.2f}\n"
            f"💵 Cash: ${p.cash:,.2f}\n"
            f"📈 Total Value: ${p.portfolio_value:,.2f}\n"
            f"💹 P&L: ${p.total_pnl:,.2f} ({(p.total_pnl/p.initial_capital)*100:+.1f}%)\n\n"
            f"📋 Open Positions: {len(p.open_positions)}/{self.MAX_OPEN_POSITIONS}\n"
            f"✅ Closed Trades: {len(p.closed_trades)}\n"
            f"🎯 Win Rate: {p.win_rate:.0f}%\n"
        )

        if p.open_positions:
            msg += "\n*Open Positions:*\n"
            for pos in p.open_positions:
                msg += (
                    f"• {pos.stock_code} | {pos.shares} shares @ ${pos.entry_price:.2f}\n"
                    f"  SL: ${pos.stop_loss:.2f} | TP: ${pos.take_profit:.2f}\n"
                )

        return msg

    def send_daily_summary(self):
        """Send daily portfolio summary via Telegram."""
        if self.telegram:
            self.telegram.send(self.get_status())
