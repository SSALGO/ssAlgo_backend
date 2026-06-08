class BacktestService:
    @staticmethod
    def _sma(values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    @staticmethod
    def _max_drawdown(equity_curve):
        peak = None
        max_drawdown = 0.0
        for point in equity_curve:
            equity = float(point["equity"])
            peak = equity if peak is None else max(peak, equity)
            if peak:
                max_drawdown = min(max_drawdown, (equity - peak) / peak)
        return abs(round(max_drawdown * 100, 4))

    @staticmethod
    def _profit_factor(trades):
        gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        if gross_loss == 0:
            return None if gross_profit == 0 else float("inf")
        return round(gross_profit / gross_loss, 4)

    @staticmethod
    def _max_loss_streak(trades):
        max_streak = 0
        current = 0
        for trade in trades:
            if trade["pnl"] < 0:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    def run_sma_crossover(self, candles, fast=9, slow=21, initial_capital=100000, quantity=1):
        if fast <= 0 or slow <= 0 or fast >= slow:
            raise ValueError("Use periods where 0 < fast < slow")
        closes = []
        cash = float(initial_capital)
        position = None
        trades = []
        equity_curve = []
        last_signal = None

        for candle in candles:
            close = float(candle.get("close", candle.get("Close", 0)))
            if close <= 0:
                continue
            closes.append(close)
            fast_ma = self._sma(closes, int(fast))
            slow_ma = self._sma(closes, int(slow))
            timestamp = candle.get("time") or candle.get("date") or len(closes)
            signal = None
            if fast_ma is not None and slow_ma is not None:
                signal = "long" if fast_ma > slow_ma else "flat"

            if signal == "long" and last_signal != "long" and position is None:
                position = {"entry_price": close, "entry_time": timestamp, "quantity": int(quantity)}
                cash -= close * int(quantity)
            elif signal == "flat" and last_signal == "long" and position is not None:
                pnl = (close - position["entry_price"]) * position["quantity"]
                cash += close * position["quantity"]
                trades.append(
                    {
                        "entry_time": position["entry_time"],
                        "exit_time": timestamp,
                        "entry_price": position["entry_price"],
                        "exit_price": close,
                        "quantity": position["quantity"],
                        "pnl": round(pnl, 4),
                    }
                )
                position = None

            last_signal = signal or last_signal
            open_pnl = 0 if position is None else (close - position["entry_price"]) * position["quantity"]
            equity_curve.append({"time": timestamp, "equity": round(cash + open_pnl, 4)})

        if position and closes:
            close = closes[-1]
            pnl = (close - position["entry_price"]) * position["quantity"]
            cash += close * position["quantity"]
            trades.append(
                {
                    "entry_time": position["entry_time"],
                    "exit_time": "end",
                    "entry_price": position["entry_price"],
                    "exit_price": close,
                    "quantity": position["quantity"],
                    "pnl": round(pnl, 4),
                }
            )

        wins = [trade for trade in trades if trade["pnl"] > 0]
        final_equity = equity_curve[-1]["equity"] if equity_curve else float(initial_capital)
        return {
            "strategy": "sma_crossover",
            "fast": int(fast),
            "slow": int(slow),
            "initial_capital": float(initial_capital),
            "final_equity": round(final_equity, 4),
            "total_pnl": round(final_equity - float(initial_capital), 4),
            "trade_count": len(trades),
            "win_rate": round((len(wins) / len(trades)) * 100, 4) if trades else 0,
            "drawdown": self._max_drawdown(equity_curve),
            "profit_factor": self._profit_factor(trades),
            "max_loss_streak": self._max_loss_streak(trades),
            "equity_curve": equity_curve,
            "trades": trades,
        }
