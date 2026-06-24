# Algorithm 143 End-to-End Documentation

## 1. Scope and naming

This document describes the strategy shown in the dashboard as **143 Options** and stored in MongoDB with:

```text
strategy = "EMA"
```

The dashboard also exposes **Index FUTURE 143**. It uses the same EMA signal engine but routes orders to futures instead of options.

This is not the same strategy as:

- `SSTRIKE` / `New 143` / `M143153`
- `FRACTALNUBIATIMEHEDGEORDER`

The name “143” is a product/UI name. The current 143 Options form actually fixes the signal EMA periods at **19 and 20**, not 143.

Primary implementation:

- `models.py`: `EMA_mode` and `EMA_fut_mode`
- `app/api/legacy_compat/strategies.py`: create, edit, start, stop, and delete lifecycle
- `connectors/exchange.py`: `EMA`, `OBUY`, `OSELL`, `OBUYEXIT`, `OSELLEXIT`, `FBUY`, `FSELL`, and `FEXIT`
- `stemplates/ema_form.html`: 143 Options parameters
- `stemplates/ema_fut_form.html`: Index FUTURE 143 parameters

## 2. Strategy in one sentence

Algorithm 143 resamples completed market candles, calculates a short EMA and long EMA, converts their relationship into a bullish or bearish trend, and then buys or sells a CE/PE option—or buys/sells a future—according to the selected mode.

## 3. End-to-end lifecycle

```text
User opens 143 form
        |
        v
Frontend fetches add_ema_form schema
        |
        v
POST /api_add_ema
        |
        v
EMA_mode validates and converts values
        |
        v
Strategy saved in MongoDB strategies collection
        |
        v
User presses Start Live / Start Paper
        |
        +--> Worker must be healthy
        |
        +--> For live mode:
        |      selected broker must not be paper
        |      broker login must be connected
        |      shared market feed must be connected
        |      a fresh underlying price must be available when inside the window
        |
        v
status becomes opened and lifecycle_revision increases
        |
        v
Trading worker loads opened strategies and strategies with position=in
        |
        v
Exchange.process_strategy() routes strategy="EMA" to EMA()
        |
        v
Completed candles -> EMA(19) and EMA(20) -> bullish/bearish signal
        |
        v
Entry time, cooldown, status, position, and admin-control gates
        |
        +--> Options: OBUY or OSELL
        |
        +--> Futures: FBUY or FSELL
        |
        v
Contract selection -> quote wait -> duplicate-entry claim -> broker route
        |
        v
Opositions record created and strategy position becomes in
        |
        v
Every worker cycle evaluates signal, P&L, TP/SL, trailing, time, and expiry exits
        |
        v
Exit order is sent for the recorded open quantity
        |
        v
Fully filled exit -> position close -> strategy position out
        |
        v
Lot progression and re-entry cooldown are updated
```

## 4. Candle and signal calculation

### 4.1 Market-data eligibility

The strategy is evaluated only when:

- the worker is in test mode, or
- the user is present in `userloggedin`; and
- the current IST weekday is before `marketdays`.

If no candles are available, the strategy logs `signal_rejected` with `market_data_unavailable` and places no order.

### 4.2 Resampling

The engine:

1. Reads the underlying or future candle dataframe.
2. Keeps the configured historical candle count.
3. Filters index data to approximately 09:14–15:30.
4. Groups candles by trading date.
5. Resamples each date to the selected timeframe.
6. Removes the latest resampled candle when it is not complete.

Signals therefore use completed candles rather than a partially formed candle.

### 4.3 EMA trend

With the normal user configuration:

```text
short EMA = EMA(close, r1)
long EMA  = EMA(close, k1)
```

The trend code is:

| Condition | Trend value | Meaning |
|---|---:|---|
| short EMA > long EMA | `0` | Bullish |
| short EMA < long EMA | `1` | Bearish |
| short EMA = long EMA | `2` | Neutral |

The default 143 Options form uses:

```text
r1 = 19
k1 = 20
```

### 4.4 New Signal versus Current Signal

`candle1=1` means the most recently completed candle.  
`candle2=2` means the previously completed candle.

#### New Signal

When `Newsignal=true`, entry requires a trend change:

```text
current trend != previous trend
```

- crossover into bullish trend -> `Signal = 1`
- crossover into bearish trend -> `Signal = -1`
- no crossover -> `Signal = 0`

#### Current Signal

When `Newsignal=false`, the strategy can enter based on the already-existing trend:

- current bullish trend -> `Signal = 1`
- current bearish trend -> `Signal = -1`

After a completed exit, `ttw` delays a Current Signal re-entry. A New Signal strategy is made eligible immediately, but it still needs a new crossover.

### 4.5 Important implementation detail

Inside the current EMA implementation, `trends1` is a copy of `trends`. Therefore the “two trend confirmations” are not independent confirmations. They always contain the same EMA result.

## 5. Entry behavior

### 5.1 Common entry gates

An entry is allowed only when all applicable conditions are true:

- strategy `status == "opened"`
- strategy `position == "out"`
- `timetowait` has elapsed
- current time is inside the configured strategy window
- signal is `1` or `-1`
- contract selection succeeds
- a usable option/future quote is available
- the entry submission lock is acquired
- for live mode, the selected broker accepts the order

### 5.2 143 Options direction matrix

#### Buyer mode: `BSmode=true`

| EMA signal | Market view | Action |
|---:|---|---|
| `1` | Bullish | Buy CE |
| `-1` | Bearish | Buy PE |

#### Seller mode: `BSmode=false`

| EMA signal | Market view | Action |
|---:|---|---|
| `1` | Bullish | Sell PE |
| `-1` | Bearish | Sell CE |

Seller mode therefore sells the option opposite to the directional view.

### 5.3 Index FUTURE 143 direction matrix

The presence of the `onspot` field selects the futures route:

| EMA signal | Action |
|---:|---|
| `1` | Buy future |
| `-1` | Sell future |

The code checks whether the `onspot` key exists, not whether its value is true. In the future form, even `onspot=false` still selects the futures path.

### 5.4 Contract selection

For options, `MainOptionSelect` uses:

- `symbol`
- CE or PE derived from the signal/mode
- `strike`
- `Expiry`

For futures, `MainFutureSelect` uses:

- `symbol`
- `Expiry`

### 5.5 Expiry rollover before entry

The engine calculates:

```text
selected contract expiry date + RolloverTime
```

If:

```text
now + DaysHead >= rollover timestamp
```

then:

- Current Week becomes Next Week; or
- Current Month becomes Next Month.

The contract is then selected again using the rolled expiry.

### 5.6 Quote acquisition

After contract selection, the engine subscribes to the contract and calls `_wait_for_market_price`.

It waits for a bounded period instead of immediately failing after subscription. If no valid quote is found, the order fails closed and no broker order should be sent.

### 5.7 Duplicate-entry protection

The option entry path uses:

- `position` state
- `entry_order_state`
- `_claim_entry_submission`
- lifecycle revision checks
- an open-position uniqueness check

Live strategies do not repeatedly retry when `entry_order_state` is already `submitting`, `attempted`, or `broker_failed`. A `preflight_failed` state may retry only after the configured preflight retry interval.

### 5.8 Quantity and slicing

Normal option quantity is:

```text
exchange lot size × configured lot
```

For example, if the exchange lot size is 50 and `lot=2`, requested quantity is 100.

The sell-option and future paths split large configured lots when `lot > 20`, using `slicing` as the number of lots per child order. The buy-option path currently submits its calculated quantity without that same lot-slicing branch.

### 5.9 Live versus paper

- `live=false`: no external broker order is sent; the position is recorded as a successful paper position.
- `live=true`: the order is routed through the broker selected for that user in the broker record.

On success:

```text
strategy.position = "in"
strategy.entry_order_state = "success"
Opositions.status = "open"
```

On broker failure:

```text
strategy.position = "out"
strategy.entry_order_state = "broker_failed"
Opositions.status = "broker_failed"
```

## 6. Position and exit behavior

### 6.1 P&L

For a bought option:

```text
P&L = (current option price - entry option price)
      × configured lots
      × exchange lot size
```

For a sold option:

```text
P&L = (entry option price - current option price)
      × configured lots
      × exchange lot size
```

### 6.2 Exit priority

The first matching exit condition wins. The option exit paths broadly evaluate:

1. Opposite signal
2. P&L target or P&L stop, when PNL Exit is selected
3. Maximum-profit or maximum-loss guard
4. Trailing stop
5. Price target or price stop, when TP/SL Exit is selected
6. User-requested exit
7. Bot paused or closed
8. Intraday ExitTime
9. Expiry rollover exit

The exact order differs slightly between buyer and seller functions, especially where trailing stop is checked.

### 6.3 Signal exits

At entry, the position stores the opposite of the entry signal as `exitcond`.

Example:

```text
Buy CE on Signal=1
exitcond becomes -1
```

A later bearish condition can therefore close the bullish position.

There is a buyer/seller asymmetry:

- `OBUYEXIT` compares `exitcond` with the entry-style `Signal`, so a New Signal buyer normally waits for an opposite crossover.
- `OSELLEXIT` compares `exitcond` with `exSignal`, which represents the current EMA trend, so seller exits can react to the opposite current trend without requiring the same crossover condition.

### 6.4 PNL Exit

When `pnlexit_tpslexit=true`:

```text
target exit when P&L >= tp × current strategy lot
stop exit   when P&L <= -(sl × current strategy lot)
```

Because position P&L already includes lot multiplication, the configured `tp` and `sl` behave as per-lot currency thresholds.

### 6.5 TP/SL Exit

When `pnlexit_tpslexit=false`, `tp` and `sl` become option-price distances.

When `pct_point=true`, they are percentages of the entry option price.

For a bought option:

```text
target price = entry + target distance
stop price   = entry - stop distance
```

For a sold option:

```text
target price = entry - target distance
stop price   = entry + stop distance
```

### 6.6 Trailing stop

`trail=1` enables trailing P&L protection.

For buyer positions, the trail starts after per-lot profit reaches approximately twice `trail_stoploss`. The stored trail floor then advances in steps.

For seller positions, the implementation tracks maximum total P&L and, after sufficient profit, sets:

```text
trailing floor = maximum P&L - configured trail value
```

The buyer and seller trailing implementations are not mathematically identical.

### 6.7 Time exit

For intraday strategies, an open position is eligible for exit after `ExitTime`.

The outer EMA evaluator only calls the exit functions while its market-window gate is open. Consequently, a stop or time-based exit requested after the applicable window may not be processed until a later eligible window. This is a production caveat, not an intended trading rule.

### 6.8 Expiry exit

An open option exits after `RolloverTime` when today is:

- the contract expiry date; or
- the expiry date minus `DaysHead`.

### 6.9 Partial and failed exits

The exit order uses the recorded open quantity, not a newly calculated full quantity.

If the broker partially fills or rejects the exit:

- the position remains open;
- remaining net quantity is preserved;
- `exit_order_state` records partial or failed status;
- strategy `position` remains `in`;
- the worker can continue managing the unresolved exposure.

Only a fully confirmed exit sets:

```text
Opositions.status = "close"
strategy.position = "out"
```

## 7. Lot progression after exit

After a completed losing trade:

| `FixedLot` value | Next lot |
|---|---|
| `FixedLot` | unchanged |
| `Steps` | current lot + `stepvalue` |
| `Doubling` | current lot × 2 |

After a non-losing completed trade:

```text
lot = initiallot
```

This is a loss-recovery sizing mechanism and can increase risk quickly.

## 8. Every parameter and its actual behavior

| Parameter | Stored type | Runtime behavior |
|---|---|---|
| `botname` | string | User-facing strategy name and part of generated bot identity. |
| `user` | string | Owning username; added by the backend, not trusted from the client. |
| `botcode` | string | Unique strategy identifier generated by the backend unless editing an existing strategy. |
| `symbol` | string | Underlying used for candles, contract selection, exchange choice, and feed readiness. |
| `Expiry` | string | Selects Current/Next Week or Month. May be changed in memory to the next expiry by rollover logic. |
| `timeframe` | string | Candle resampling interval. The options form permits 1m, 2m, 3m, 5m, 15m, and 30m; runtime support also depends on configured timeframe maps. |
| `r1` | integer | Short EMA period. Form default and hidden value: 19. |
| `k1` | integer | Long EMA period. Form default and hidden value: 20. |
| `r2` | integer | Automatically copied from `r1`. Not independently configurable in this model. |
| `k2` | integer | Automatically copied from `k1`. Not independently configurable in this model. |
| `Newsignal` | boolean | True requires a crossover; false permits entry in the currently active trend. Also controls post-exit cooldown behavior. |
| `USEMA` | boolean | Stored but not read by the current `EMA()` implementation. It presently has no effect. |
| `ema` | integer | Stored but not read by the current `EMA()` implementation. The visible default 200 EMA filter is not applied. |
| `Intraday` | boolean | True uses the intraday window and enables ExitTime exit; false uses the positional window. |
| `FixedLot` | string | Controls lot progression after a completed loss: fixed, step-up, or doubling. It does not independently calculate the first entry size. |
| `BSmode` | boolean | True routes to option buying; false routes to option selling. Futures ignore this distinction for directional routing. |
| `pct_point` | boolean | True interprets TP/SL price exits as percentages; false interprets them as option-price points. Relevant when TP/SL Exit is selected. |
| `pnlexit_tpslexit` | boolean | True uses currency P&L exits; false uses option-price target and stop exits. |
| `strike` | integer | Strike offset/selector passed to `MainOptionSelect`. `0` normally represents ATM according to the contract-selection implementation. |
| `lot` | integer | Current number of lots. Used for order quantity and P&L. May change after losses. |
| `initiallot` | integer | Baseline lot restored after a non-losing completed exit. |
| `ttw` | integer | Minutes to wait before re-entry after exit when `Newsignal=false`. |
| `timetowait` | timestamp | Internal next-entry timestamp. Initialized automatically and updated after exits. |
| `stepvalue` | integer | Number of lots added after a loss when `FixedLot=Steps`. |
| `MultiFactor` | integer | Stored but not used by the current EMA entry, exit, or sizing paths. |
| `candle1` | integer | Current trend index from the end of the completed candle list. Default 1. |
| `candle2` | integer | Comparison trend index. Default 2. |
| `slicing` | integer | Child-order size in lots for supported large-lot sell/future paths. |
| `DaysHead` | integer | Moves rollover selection and expiry exits earlier by this many calendar days. |
| `RolloverTime` | HH:MM | Time used for pre-entry expiry rollover and open-position expiry exit. |
| `StartTime` | HH:MM | Entries and normal position evaluation begin only after this time. The comparison is strict, so exactly StartTime is not yet inside the window. |
| `ExitTime` | HH:MM | End of configured window and intraday exit threshold. Comparisons are generally strict. |
| `trail` | integer flag | `1` enables trailing stop; `0` disables it. |
| `trail_stoploss` | integer | P&L step/distance used by trailing logic. Buyer and seller implementations use it differently. |
| `tp` | integer | Per-lot P&L target in PNL mode, or option-price percentage/points target in TP/SL mode. |
| `sl` | integer | Per-lot P&L stop in PNL mode, or option-price percentage/points stop in TP/SL mode. |
| `maxprofit` | integer | Hidden hard profit exit. Current form fixes it at 100000. |
| `maxloss` | integer | Hidden hard loss exit. Current form fixes it at 100000. |
| `live` | boolean | True routes to the selected live broker; false records paper trades. |
| `status` | string | `opened` evaluates entries; `paused` blocks new entries and requests exit management for open positions. |
| `position` | string | Internal strategy exposure state: normally `out` or `in`. |
| `onspot` | boolean field | Its presence selects Index FUTURE 143 behavior. The current value itself is not checked in `EMA()`. |
| `entry_order_state` | internal string | Tracks submitting, attempted, success, preflight failure, or broker failure and prevents unsafe duplicate live entries. |
| `lifecycle_revision` | internal integer | Incremented on lifecycle/config changes so stale worker snapshots and queued orders can be rejected. |

## 9. Start, stop, edit, and delete behavior

### Start

Starting is idempotent:

- first start changes status to opened and increments lifecycle revision;
- starting an already opened strategy returns `already_active=true`.

For live mode, the API rejects start when the worker, broker login, feed connection, or required fresh underlying price is unavailable.

### Stop

Stopping:

- changes strategy status to paused;
- increments lifecycle revision;
- marks open positions with `decision="exitit"`.

The worker continues loading strategies whose `position=="in"` so they can be exited.

### Edit

Editing a running strategy:

- preserves current `status`;
- preserves current `position`;
- replaces configuration values;
- increments lifecycle revision.

The worker reloads the latest revision rather than evaluating an obsolete snapshot.

### Delete

Deleting a running strategy:

- changes status to closed;
- increments lifecycle revision;
- marks open positions for exit instead of silently forgetting them.

## 10. Admin controls

Per-symbol admin controls can override the EMA signal:

- Buy enabled and Sell disabled -> force `Signal=1`
- Sell enabled and Buy disabled -> force `Signal=-1`
- both or neither in control mode -> request exit with zero signal

These controls can therefore supersede normal EMA output.

## 11. Logging and diagnosis

Useful events include:

- `strategy_start_request`
- `strategy_start_rejected`
- `strategy_engine_ready`
- `data_feed_status`
- `signal_evaluation`
- `signal_rejected`
- `entry_gate_evaluation`
- `broker_order_route_selected`
- `broker_order_request`
- `position_state_after_entry_order`
- `position_state_after_exit_order`
- `strategy_evaluation_error`

Typical no-order diagnosis:

1. Confirm the worker logged `strategy_engine_ready`.
2. Confirm the strategy has `status=opened`.
3. Confirm `position=out`.
4. Confirm candles exist for the underlying/future symbol.
5. Inspect `signal_evaluation`.
6. Inspect `entry_gate_evaluation` for time or cooldown blocking.
7. Confirm an option/future quote was obtained.
8. Inspect `entry_order_state`.
9. Inspect the exact broker response.

Broker login success alone does not prove that market data is fresh or that live orders are permitted.

## 12. Known implementation caveats

1. **143 does not mean EMA period 143 in this form.** The current hidden periods are 19 and 20.
2. **USEMA and ema are inert.** Selecting “With Ema” and entering 200 does not currently apply a 200 EMA filter.
3. **The two trend arrays are duplicates.** The apparent double confirmation is currently the same EMA trend checked twice.
4. **Current Signal is intentionally re-entry-capable.** After `ttw`, it may re-enter the same continuing trend.
5. **Buyer and seller signal exits differ.** Buyer exit uses `Signal`; seller exit uses `exSignal`.
6. **Buyer and seller trailing logic differs.**
7. **Large-order slicing is not symmetrical.** Sell-option and futures paths slice large lots; the buy-option path does not use the same branch.
8. **Window gating can delay exits.** Exit functions are called only while the outer EMA market window is considered open.
9. **Visible form validation is not complete business validation.** For example, the HTML permits values such as zero slicing, which can be unsafe if a large-lot path tries to divide by it.
10. **Live broker policy remains external.** A valid signal and quote can still be rejected by broker IP, product, margin, exchange, or account restrictions.

## 13. Worked examples

### Example A: Buyer, New Signal

```text
BSmode = Buyers
Newsignal = New Signal
r1 = 19
k1 = 20
strike = 0
lot = 1
```

If candle 2 was bearish and candle 1 closes bullish:

```text
Signal = 1
Action = Buy ATM CE
```

No additional CE is bought while strategy `position=in`.

### Example B: Seller, Current Signal

```text
BSmode = Sellers
Newsignal = Current Signal
ttw = 10
```

If the current trend is bearish:

```text
Signal = -1
Action = Sell CE
```

After the position closes, the strategy waits 10 minutes and may sell a CE again if the bearish trend is still current.

### Example C: TP/SL by percentage

```text
entry option price = 100
pnlexit_tpslexit = TP/SL Exit
pct_point = PCT
tp = 10
sl = 5
```

For a bought option:

```text
target = 110
stop = 95
```

For a sold option:

```text
target = 90
stop = 105
```

### Example D: Loss-based lot progression

```text
initiallot = 1
lot = 1
FixedLot = Doubling
```

After a completed loss:

```text
next lot = 2
```

After another completed loss:

```text
next lot = 4
```

After a non-losing completed exit:

```text
lot resets to 1
```

## 14. Practical default interpretation

With the current form defaults, 143 Options behaves approximately as:

```text
Underlying: NIFTY
Expiry: Current Week
Signal timeframe: 3 minutes
Signal: fresh 19/20 EMA crossover
Mode: option buyer
Strike: ATM
Lot: 1
Position style: positional
Exit type: P&L
Trailing: enabled
Rollover: expiry day at 13:01, DaysHead 0
Start: 09:17
ExitTime: 15:20
Live trading: enabled
Initial status: paused
```

The default target and stop values are very large, so users should not assume those defaults provide tight risk control.
