from __future__ import annotations

from copy import deepcopy


COMMODITIES = [
    {
        "symbol": "CRUDEOIL",
        "name": "Crude Oil",
        "traits": ["high liquidity", "news sensitive", "trend friendly"],
        "preferred_timeframes": ["5m", "15m", "30m"],
    },
    {
        "symbol": "NATURALGAS",
        "name": "Natural Gas",
        "traits": ["high volatility", "gap risk", "breakout friendly"],
        "preferred_timeframes": ["5m", "15m"],
    },
    {
        "symbol": "GOLD",
        "name": "Gold",
        "traits": ["global macro driven", "liquid", "mean reversion pockets"],
        "preferred_timeframes": ["15m", "30m", "1h"],
    },
    {
        "symbol": "SILVER",
        "name": "Silver",
        "traits": ["volatile", "trend bursts", "risk-on/risk-off driven"],
        "preferred_timeframes": ["15m", "30m"],
    },
    {
        "symbol": "COPPER",
        "name": "Copper",
        "traits": ["industrial demand driven", "session momentum", "lower noise than gas"],
        "preferred_timeframes": ["15m", "30m"],
    },
]


STRATEGIES = [
    {
        "id": "mcx_opening_range_breakout",
        "rank": 1,
        "strategy_name": "MCX Opening Range Breakout",
        "market_logic": (
            "MCX commodities often define the intraday direction after the Indian evening "
            "liquidity window opens and global markets become active. A break of the early "
            "range with volatility and volume confirmation captures directional expansion "
            "without averaging down."
        ),
        "suitable_commodities": ["CRUDEOIL", "NATURALGAS", "GOLD", "SILVER"],
        "timeframe": "5m execution with 15m trend confirmation",
        "entry_conditions": [
            "Build the opening range from the first 30 minutes of the selected trading session.",
            "Go long when price closes above opening range high and candle range is at least 0.8 x ATR(14).",
            "Go short when price closes below opening range low and candle range is at least 0.8 x ATR(14).",
            "Trade only in the direction of VWAP slope or 15m EMA(20) bias.",
            "Skip entries if spread, slippage, or pending order count breaches risk limits.",
        ],
        "exit_conditions": [
            "Exit when opposite side of the opening range is broken after entry.",
            "Exit on session square-off time.",
            "Exit if the trade fails to move 0.5R in favor within 6 candles.",
        ],
        "stop_loss_logic": "Initial stop beyond the opposite side of the opening range or 1.2 x ATR(14), whichever is tighter but still above exchange minimum tick distance.",
        "target_logic": "Primary target at 1.8R; trail remaining quantity using EMA(9) or 1 x ATR once 1R is achieved.",
        "risk_reward_ratio": "1:1.8 to 1:2.5",
        "expected_win_rate_range": "38% to 48%",
        "best_market_conditions": ["strong global session open", "inventory/news day after first reaction", "clean directional candles"],
        "worst_market_conditions": ["tight range days", "false breakout after major data", "very low volume afternoon sessions"],
        "required_indicators": ["Opening Range High/Low", "ATR(14)", "VWAP", "EMA(20)", "Volume"],
        "parameter_values": {
            "range_minutes": 30,
            "atr_period": 14,
            "minimum_breakout_atr_multiple": 0.8,
            "max_trades_per_symbol_per_day": 2,
            "session_square_off_minutes_before_close": 15,
        },
        "backtesting_requirements": [
            "Use continuous futures with contract rollover handling.",
            "Include brokerage, exchange fees, GST, STT/CTT where applicable, and realistic slippage.",
            "Reject trades where historical bid/ask spread would exceed configured spread limit.",
            "Test separately for normal days, inventory days, Fed/CPI days, and expiry week.",
        ],
        "live_execution_considerations": [
            "Use WebSocket candles built from tick data or broker-provided 5m candles with timestamp validation.",
            "Place stop-loss order immediately after entry confirmation.",
            "Prevent duplicate breakout orders with an idempotency key per symbol/session/range side.",
            "Disable new entries if broker login, WebSocket feed, or risk guard is unhealthy.",
        ],
        "scores": {"simplicity": 92, "robustness": 90, "scalability": 88, "automation": 95, "mcx_fit": 94},
    },
    {
        "id": "mcx_volatility_expansion",
        "rank": 2,
        "strategy_name": "ATR Volatility Expansion Breakout",
        "market_logic": (
            "Commodities frequently move from compression to expansion around global data, "
            "inventory releases, and London/US participation. This system waits for a low "
            "volatility regime, then trades the first confirmed expansion."
        ),
        "suitable_commodities": ["CRUDEOIL", "NATURALGAS", "SILVER", "COPPER"],
        "timeframe": "15m execution",
        "entry_conditions": [
            "ATR(14) percentile over the last 20 sessions is below 35.",
            "Bollinger Band width is below its 20-period moving average before breakout.",
            "Go long when price closes above Donchian(20) high with volume above SMA(volume,20).",
            "Go short when price closes below Donchian(20) low with volume above SMA(volume,20).",
            "Skip if upcoming high-impact event is less than 5 minutes away.",
        ],
        "exit_conditions": [
            "Exit partial at 1.5R.",
            "Trail final quantity using 2 x ATR(14).",
            "Exit when price closes back inside the Donchian channel midpoint.",
        ],
        "stop_loss_logic": "Use 1.5 x ATR(14) from entry or opposite breakout candle extreme.",
        "target_logic": "Scale target between 1.5R and 3R depending on ATR expansion and session liquidity.",
        "risk_reward_ratio": "1:2 to 1:3",
        "expected_win_rate_range": "34% to 44%",
        "best_market_conditions": ["volatility compression", "post-consolidation breakout", "high participation sessions"],
        "worst_market_conditions": ["headline chop", "wide spreads", "late-session exhaustion breakouts"],
        "required_indicators": ["ATR(14)", "ATR percentile", "Donchian Channel(20)", "Bollinger Band Width", "Volume SMA(20)"],
        "parameter_values": {
            "atr_period": 14,
            "lookback_sessions": 20,
            "atr_percentile_max": 35,
            "donchian_period": 20,
            "stop_atr_multiple": 1.5,
        },
        "backtesting_requirements": [
            "Segment results by volatility percentile bucket.",
            "Include event-day filters to verify whether news windows help or hurt.",
            "Measure time-in-trade and maximum adverse excursion.",
        ],
        "live_execution_considerations": [
            "Require candle close confirmation; do not trade intrabar spikes.",
            "Throttle orders during fast markets and confirm broker acknowledgement.",
            "Use price bands and tick-size rounding before placing orders.",
        ],
        "scores": {"simplicity": 84, "robustness": 91, "scalability": 89, "automation": 90, "mcx_fit": 92},
    },
    {
        "id": "mcx_trend_pullback_atr",
        "rank": 3,
        "strategy_name": "ATR Trend Pullback Continuation",
        "market_logic": (
            "When MCX commodities trend with global direction, buying or selling first pullbacks "
            "near VWAP/EMA gives better reward-to-risk than chasing extended candles."
        ),
        "suitable_commodities": ["GOLD", "SILVER", "CRUDEOIL", "COPPER"],
        "timeframe": "15m bias, 5m execution",
        "entry_conditions": [
            "EMA(20) is above EMA(50) for long trades or below EMA(50) for short trades.",
            "ADX(14) is above 22 to confirm trend strength.",
            "Price pulls back to VWAP or EMA(20) without closing beyond EMA(50).",
            "Enter on rejection candle close in the trend direction.",
        ],
        "exit_conditions": [
            "Exit at 2R or at previous swing high/low.",
            "Trail with EMA(20) once price moves 1R in favor.",
            "Exit if ADX drops below 18 and price closes through VWAP.",
        ],
        "stop_loss_logic": "Stop beyond pullback swing low/high plus 0.5 x ATR(14).",
        "target_logic": "First target 1.5R; second target 2R to 2.5R near prior swing extension.",
        "risk_reward_ratio": "1:1.5 to 1:2.5",
        "expected_win_rate_range": "42% to 52%",
        "best_market_conditions": ["clean trending days", "global market alignment", "steady directional volume"],
        "worst_market_conditions": ["range-bound market", "frequent VWAP whipsaws", "news reversal days"],
        "required_indicators": ["EMA(20)", "EMA(50)", "VWAP", "ADX(14)", "ATR(14)"],
        "parameter_values": {
            "fast_ema": 20,
            "slow_ema": 50,
            "adx_period": 14,
            "adx_min": 22,
            "stop_buffer_atr_multiple": 0.5,
        },
        "backtesting_requirements": [
            "Classify trend days using independent trend filter before evaluating entries.",
            "Measure skipped trades during ADX below-threshold periods.",
            "Verify performance by commodity and by session.",
        ],
        "live_execution_considerations": [
            "Use a state machine to avoid repeated entries on the same pullback.",
            "Check enough historical candles are loaded before enabling signals.",
            "Guard against stale VWAP after reconnects.",
        ],
        "scores": {"simplicity": 86, "robustness": 88, "scalability": 86, "automation": 88, "mcx_fit": 89},
    },
    {
        "id": "mcx_session_momentum",
        "rank": 4,
        "strategy_name": "London-US Session Momentum",
        "market_logic": (
            "Crude, metals, and gas often become more directional when London and US liquidity "
            "overlap with MCX evening trading. The strategy focuses only on predefined high-liquidity windows."
        ),
        "suitable_commodities": ["CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER"],
        "timeframe": "15m",
        "entry_conditions": [
            "Trade only during configured MCX evening session windows.",
            "Current candle closes above previous session high for long or below previous session low for short.",
            "RSI(14) is above 55 for long or below 45 for short.",
            "ATR(14) is above its 10-session median.",
        ],
        "exit_conditions": [
            "Exit at 1.8R target.",
            "Exit on momentum failure: RSI crosses back through 50.",
            "Force square-off before illiquid late session.",
        ],
        "stop_loss_logic": "Stop behind signal candle low/high or 1.2 x ATR(14).",
        "target_logic": "Fixed 1.8R target with optional ATR trail in strong sessions.",
        "risk_reward_ratio": "1:1.8",
        "expected_win_rate_range": "40% to 50%",
        "best_market_conditions": ["active London-US overlap", "macro trend alignment", "clean session highs/lows"],
        "worst_market_conditions": ["holiday sessions", "thin volume", "sideways price around session boundary"],
        "required_indicators": ["Session high/low", "RSI(14)", "ATR(14)", "Volume"],
        "parameter_values": {
            "rsi_period": 14,
            "long_rsi_min": 55,
            "short_rsi_max": 45,
            "stop_atr_multiple": 1.2,
            "target_r_multiple": 1.8,
        },
        "backtesting_requirements": [
            "Use exchange calendar and holiday filters.",
            "Backtest each session window separately.",
            "Include forced exits at configured square-off times.",
        ],
        "live_execution_considerations": [
            "Server timezone must be converted to Asia/Kolkata before session checks.",
            "Disable entries outside the configured window even if signal conditions remain true.",
        ],
        "scores": {"simplicity": 88, "robustness": 82, "scalability": 90, "automation": 92, "mcx_fit": 86},
    },
    {
        "id": "mcx_donchian_trend",
        "rank": 5,
        "strategy_name": "Donchian Trend Rider",
        "market_logic": (
            "A classic rule-based breakout approach that enters new 20-period highs/lows and "
            "stays with large commodity trends while cutting losers quickly."
        ),
        "suitable_commodities": ["CRUDEOIL", "GOLD", "SILVER", "COPPER"],
        "timeframe": "30m or 1h",
        "entry_conditions": [
            "Go long on close above Donchian(20) high.",
            "Go short on close below Donchian(20) low.",
            "Only enter when ATR(14) is not above the 90th percentile to avoid overextended risk.",
        ],
        "exit_conditions": [
            "Exit long on close below Donchian(10) low.",
            "Exit short on close above Donchian(10) high.",
            "Exit on daily risk lock or contract rollover rule.",
        ],
        "stop_loss_logic": "Initial stop at 2 x ATR(14) from entry.",
        "target_logic": "No fixed target; trend-following trailing exit.",
        "risk_reward_ratio": "Variable, designed for occasional 3R+ winners",
        "expected_win_rate_range": "30% to 42%",
        "best_market_conditions": ["multi-day trend", "macro shock continuation", "low-chop directional markets"],
        "worst_market_conditions": ["sideways range", "frequent false breakouts", "expiry noise"],
        "required_indicators": ["Donchian Channel(20)", "Donchian Channel(10)", "ATR(14)"],
        "parameter_values": {
            "entry_channel": 20,
            "exit_channel": 10,
            "atr_period": 14,
            "stop_atr_multiple": 2,
            "atr_percentile_max": 90,
        },
        "backtesting_requirements": [
            "Use multi-year data across commodity regimes.",
            "Evaluate drawdown clusters and losing streak tolerance.",
            "Test rollover and overnight gap assumptions.",
        ],
        "live_execution_considerations": [
            "Best suited for smaller position sizing because win rate is lower.",
            "Needs psychological and operational tolerance for streaks of small losses.",
        ],
        "scores": {"simplicity": 80, "robustness": 84, "scalability": 85, "automation": 86, "mcx_fit": 80},
    },
    {
        "id": "mcx_vwap_mean_reversion",
        "rank": 6,
        "strategy_name": "VWAP Deviation Mean Reversion",
        "market_logic": (
            "In non-trending MCX sessions, extreme moves away from VWAP can revert after liquidity "
            "absorbs the imbalance. The strategy trades only when a range filter confirms no strong trend."
        ),
        "suitable_commodities": ["GOLD", "SILVER", "COPPER"],
        "timeframe": "5m",
        "entry_conditions": [
            "ADX(14) is below 18.",
            "Price deviates at least 2 standard deviations from VWAP.",
            "Enter short after rejection above upper VWAP band; enter long after rejection below lower VWAP band.",
            "Do not enter during high-impact event windows or when spread is wide.",
        ],
        "exit_conditions": [
            "Exit at VWAP mean touch.",
            "Exit at 1R if VWAP is too far for target within session.",
            "Exit immediately if ADX rises above 24 after entry.",
        ],
        "stop_loss_logic": "Stop beyond rejection candle extreme plus 0.5 x ATR(14).",
        "target_logic": "VWAP touch or 1.2R fixed target.",
        "risk_reward_ratio": "1:1 to 1:1.3",
        "expected_win_rate_range": "52% to 62%",
        "best_market_conditions": ["range-bound sessions", "post-news stabilization", "low ADX market"],
        "worst_market_conditions": ["strong trend days", "inventory/Fed/CPI breakout", "one-way gas moves"],
        "required_indicators": ["VWAP", "VWAP standard deviation bands", "ADX(14)", "ATR(14)"],
        "parameter_values": {
            "vwap_band_stddev": 2,
            "adx_period": 14,
            "adx_max": 18,
            "trend_abort_adx": 24,
            "target_r_multiple": 1.2,
        },
        "backtesting_requirements": [
            "Separate range days from trend days before judging win rate.",
            "Stress test sudden breakout failures and stop slippage.",
            "Model fills conservatively because reversals may be fast.",
        ],
        "live_execution_considerations": [
            "Never average if price moves further away from VWAP.",
            "Disable for Natural Gas unless specifically validated by backtest.",
            "Use strict maximum daily loss because tail losses can erase many small wins.",
        ],
        "scores": {"simplicity": 78, "robustness": 76, "scalability": 78, "automation": 82, "mcx_fit": 74},
    },
]


def mcx_strategy_catalog() -> dict:
    strategies = deepcopy(STRATEGIES)
    top_3 = [strategy for strategy in strategies if strategy["rank"] <= 3]
    return {
        "product_name": "MCX Strategy Lab",
        "product_category": "Algorithmic commodity strategy research and execution blueprint",
        "one_line_pitch": "Rule-based MCX commodity strategies designed for broker API execution with risk controls first.",
        "market": "Indian Multi Commodity Exchange (MCX)",
        "commodities": deepcopy(COMMODITIES),
        "ranking_criteria": ["simplicity", "robustness", "scalability", "ease of automation", "MCX market fit"],
        "strategies": strategies,
        "top_3": top_3,
        "recommended_top_3": [
            "MCX Opening Range Breakout",
            "ATR Volatility Expansion Breakout",
            "ATR Trend Pullback Continuation",
        ],
        "complete_trading_rules": [
            "Trade only enabled symbols, enabled contracts, and configured MCX sessions.",
            "Build signals from closed candles; intrabar movement can update monitoring but cannot trigger entries.",
            "Validate broker login, WebSocket freshness, quote availability, risk limits, and duplicate-order lock before every order.",
            "Risk per trade must be fixed before entry and converted to quantity using stop distance and lot size.",
            "Place protective stop immediately after confirmed entry. If stop order fails, exit market and disable that strategy.",
            "No martingale, no averaging down, no revenge trades, and no increasing quantity after a loss.",
            "Stop trading for the day after daily loss limit, max rejected order count, or feed staleness breach.",
        ],
        "state_machine": [
            {"state": "DISABLED", "description": "Strategy is saved but not eligible for signal generation."},
            {"state": "WARMING_UP", "description": "Historical candles and live feed are loading."},
            {"state": "READY", "description": "Risk, broker, session, and data checks are healthy."},
            {"state": "SIGNAL_DETECTED", "description": "Closed candle satisfies entry rules and awaits pre-trade validation."},
            {"state": "ORDER_PENDING", "description": "Entry order has been sent and is waiting for broker confirmation."},
            {"state": "IN_POSITION", "description": "Position is open and stop/target management is active."},
            {"state": "EXIT_PENDING", "description": "Exit order has been sent or protective stop is being reconciled."},
            {"state": "COOLDOWN", "description": "Trade completed; duplicate entries blocked for configured cooldown."},
            {"state": "LOCKED", "description": "Daily risk or infrastructure guard has stopped trading."},
        ],
        "pseudocode": [
            "load active MCX strategies",
            "for each market tick: update symbol candle builder and quote cache",
            "on candle close: validate session, feed freshness, broker status, and risk status",
            "compute indicators for configured strategy",
            "if strategy state is READY and rules produce signal: calculate stop, target, quantity, and idempotency key",
            "submit entry order through selected broker adapter",
            "after fill: place stop-loss/target orders and mark strategy IN_POSITION",
            "monitor position until stop, target, trailing exit, session square-off, or risk lock triggers",
            "record order, trade, signal snapshot, rejection reason, and final P&L",
        ],
        "database_schema_requirements": [
            {
                "table": "mcx_strategy_templates",
                "purpose": "Stores reusable rule definitions, parameters, ranking, and version history.",
                "key_fields": ["id", "name", "version", "strategy_type", "default_parameters", "is_active"],
            },
            {
                "table": "mcx_strategy_instances",
                "purpose": "Stores user-specific enabled strategy settings.",
                "key_fields": ["id", "user", "template_id", "broker", "symbol", "timeframe", "quantity_mode", "risk_per_trade", "live_mode"],
            },
            {
                "table": "mcx_signal_events",
                "purpose": "Auditable record of every generated, skipped, or rejected signal.",
                "key_fields": ["id", "instance_id", "symbol", "signal_side", "state", "indicators", "skip_reason", "created_at"],
            },
            {
                "table": "mcx_order_executions",
                "purpose": "Broker order lifecycle and reconciliation log.",
                "key_fields": ["id", "signal_id", "broker", "broker_order_id", "status", "request_payload", "response_payload", "public_ip"],
            },
            {
                "table": "mcx_risk_snapshots",
                "purpose": "Daily and per-strategy risk state used by workers before order placement.",
                "key_fields": ["id", "user", "date", "realized_pnl", "open_risk", "daily_lock", "reason"],
            },
        ],
        "api_execution_flow": [
            "Frontend enables strategy instance with symbol, timeframe, live/paper mode, and risk settings.",
            "FastAPI validates settings and stores instance in PostgreSQL or MongoDB compatibility layer.",
            "Trading worker subscribes to WebSocket market data for enabled MCX symbols.",
            "Worker creates candles, computes indicators, and emits signal events.",
            "Risk engine calculates quantity and blocks orders that exceed user, strategy, or account limits.",
            "Broker adapter sends order to AliceBlue, Dhan, or Zerodha and records raw request/response.",
            "Reconciliation job verifies positions, orders, stop-loss presence, and final P&L.",
            "Cron EOD job closes open audit gaps, rolls contracts, and produces daily performance stats.",
        ],
        "risk_management_framework": {
            "risk_per_trade": "0.25% to 1% of trading capital per strategy instance",
            "daily_loss_limit": "2% of capital or user-configured absolute rupee limit",
            "max_trades_per_day": "2 to 4 per symbol depending on strategy type",
            "max_open_positions": "1 per commodity family unless portfolio risk allows more",
            "position_sizing": "quantity = floor(risk_amount / stop_distance_value) rounded to exchange lot size",
            "kill_switches": [
                "broker disconnected",
                "WebSocket stale beyond configured seconds",
                "public IP mismatch or broker IP restriction",
                "protective stop placement failed",
                "daily loss limit reached",
                "three consecutive rejected orders",
            ],
        },
        "disclaimer": (
            "This catalog is a production blueprint, not a profit guarantee. Each strategy must be "
            "paper traded and backtested on clean MCX data before live deployment."
        ),
    }
