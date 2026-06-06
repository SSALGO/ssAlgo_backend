import logging
from typing import *
import time
import tkinter as tk
from threading import Timer
import pandas as pd

from models import *
import datetime

'''if TYPE_CHECKING:  # Import the connector class names only for typing purpose (the classes aren't actually imported)
    #from connectors.bitmex import BitmexClient
    #from connectors.binance import BinanceClient
'''

logger = logging.getLogger()

# TF_EQUIV is used in parse_trades() to compare the last candle timestamp to the new trade timestamp
TF_EQUIV = {"1m": 60, "5m": 300, "15m": 900,
            "30m": 1800, "1h": 3600, "4h": 14400}


class LevelBased:
    def __init__(self, client, trigger_price: float, trigger_type: str, symbol: str, comparator_type: str, option_type: str, strike: str, lot: int, trail: bool, trail_stoploss: str,
                 tp_1: float, tp_2: float, stop_loss: float, atmstrike: int,entry_id:int):
        self._client = client
        self.entry_id = entry_id
        self.exch='NSE'
        self.symbol = symbol
        self.trigger_type = trigger_type
        self.trigger_price = trigger_price
        self.comparator_type = comparator_type
        self.strike = strike
        self.initial_lot = int(lot)
        self.lot = f"{str(lot)}/{str(lot)}"
        self._client.api.subscribe(self._client.subscribe_list)
        print(self._client.prices)
        self.currentprice=self._client.prices[symbol]

        print('prices')
        '''try:
            print(self._client.prices)
            self.currentprice=self._client.prices[symbol]
        except:
            while True:
                self._client.feed_opened = False
                self._client.api.start_websocket(order_update_callback=self._client.event_handler_order_update,
                                     subscribe_callback=self._client.event_handler_feed_update, socket_open_callback=self._client.open_callback)
                self._client.api.subscribe(self._client.subscribe_list)
                print('Trying to connect websocket channel')
                if symbol in list(self._client.prices.keys()):
                    break
            self.currentprice=self._client.prices[symbol]'''
        self.option_type = option_type
        self.option,self.optionlot=self._client.OptionSelect(self.symbol, self.option_type, self.strike)
        self.trail = trail
        self.direction = 'buy' if option_type == 'CE' else 'sell'
        self.trail_stoploss = (float(trigger_price)-float(trail_stoploss)) if option_type == 'CE' else (float(trigger_price)+float(trail_stoploss))
        self.tp_1 = tp_1
        self.tp_2 = tp_2
        self.stop_loss = stop_loss
        self.count = 1
        self.tf = '1m'
        self.tf_equiv = TF_EQUIV[self.tf]

        self.atmstrike = atmstrike
        #self.marketcycle = self._client.hist15(self.symbol, tf='15')
        self.ongoing_position = False
        self.candles: List[Candle] = []
        #self.trades: List[LevelBasedTrade] = []
        self.optionchain = pd.DataFrame()
        self.logs = []
        self.pnlhalf = 0
        self.reconnect = True
        #self.b_index = b_index

    def _add_log(self, msg: str):
        logger.info("%s", msg)
        self.logs.append({"log": msg, "displayed": False})

    def _open_position(self, signal_result: int):
        """
        Open Long or Short position based on the signal result.
        :param signal_result: 1 (Long) or -1 (Short)
        :return:
        """

        # self._client.get_trade_size(self.symbol, self.candles[-1].close, self.balance_pct)
        trade_size = self.lot
        if trade_size is None:
            return

        order_side = "buy" if signal_result == 1 else "sell"
        position_side = "CE" if signal_result == 1 else "PE"

        self._add_log(
            f"{position_side.capitalize()} signal on {self.symbol} {self.tf}")

        # self._client.place_order(self.symbol, "MARKET", trade_size, order_side)
        order_status = True
        

        # if order_status is not None:
        self._add_log(
            f"{order_side.capitalize()} order placed Status: {order_status}")

        #self.ongoing_position = True

        avg_fill_price = None
        if self.trigger_type == 'On Spot':
            cur = self._client.prices[self.symbol]
        else:
            cur = self._client.prices[self.option]

        new_trade = LevelBasedTrade({"entry_id":self.entry_id,"time": int(datetime.datetime.now().timestamp()),
                                     "track": self.trigger_type, "comparator_type": self.comparator_type,
                                     "option_type": self.option_type, "strike": int(self.strike),
                                     "entry_price": float(cur),
                                     "current_price": 0,
                                     "optionname": self.option,
                                     "optionentry": float(self._client.prices[self.option]),
                                     "optionexit": float(self._client.prices[self.option]),
                                     "optionlot": int(self.optionlot),
                                     "symbol": self.symbol, "side": position_side,
                                     "tp_1": int(self.tp_1),
                                     "tp_2": int(self.tp_2), "sl": int(self.stop_loss), "tsl": int(self.trail_stoploss), "trail": self.trail, "traildrag": 0, "lastprice": 0, "initial_lot": self.initial_lot,
                                     "status": "open", "pnl": 0, "lot": self.lot, "pnlhalf": 0, "decision": 'none'})
        # self._add_log(vars(new_trade))
        self._client.ordersids.append(self.entry_id)
        self._client.trades.append(new_trade)
        self._client.positions_collection.insert_one(new_trade.__dict__)
    def _check_tp_sl(self, trade: LevelBasedTrade, prices, otherprice):

        """
        Based on the average entry price, calculates whether the defined stop loss or take profit has been reached.
        :param trade:
        :return:
        """

        tp_triggered = False
        sl_triggered = False
        tp_triggered_1 = False
        tsl_triggered = False
        currentexit = 0
        if trade.track == 'On Spot':
            price = prices
        else:
            price = otherprice

        trade.optionexit = otherprice
        mainlot = int(trade.lot.split('/')[0])
        # print(mainlot)
        exitit = False
        if trade.decision == 'exit':
            exitit = True
        # trade.pnl=(float(trade.optionexit)-float(trade.optionentry))*int(trade.optionlot)

        # price = self.candles[-1].close

        if trade.side == "CE":
            trade.pnl = ((float(trade.optionexit)-float(trade.optionentry))
                         * int(trade.optionlot)*int(mainlot))  # +trade.pnlhalf
            print(trade.optionname)
            print(trade.optionexit)
            print((trade.optionentry))

            print(float(trade.optionexit)-float(trade.optionentry))

            if trade.sl is not None:
                if (price <= (trade.entry_price - trade.sl)):
                    # trade.pnl=((float(trade.optionexit)-float(trade.optionentry))*int(trade.optionlot)*int(mainlot))+self.pnlhalf
                    sl_triggered = True
            if (trade.tp_1 is not None) and int(mainlot) == int(trade.initial_lot):
                if (price >= trade.entry_price + trade.tp_1):

                    trade.lot = f"{str(int(int(mainlot)/2))}/{str(trade.initial_lot)}"
                    trade.sl = trade.entry_price
                    trade.pnlhalf = ((float(
                        trade.optionexit)-float(trade.optionentry))*int(trade.optionlot))*((int(mainlot))/2)
                    self._client.api.place_order(buy_or_sell='S', product_type='M',
                                                 exchange=self.exch, tradingsymbol=trade.optionname,
                                                 quantity=self.optionlot*(int(mainlot)/2), discloseqty=0, price_type='MKT', price=0, trigger_price=0,
                                                 retention='DAY', remarks='my_order_001')
                    # trade.pnl=(float(trade.optionexit)-float(trade.optionentry))*int(trade.optionlot)*int(mainlot)

                    tp_triggered_1 = True
            elif trade.tp_2 is not None:

                if (price >= trade.entry_price + trade.tp_2):
                    tp_triggered = True
                    # trade.pnlhalf=(float(trade.optionexit)-float(trade.optionentry))*int(trade.optionlot)*int(trade.lot)
                    # trade.pnl=((float(trade.optionexit)-float(trade.optionentry))*int(trade.optionlot)*int(mainlot))+self.pnlhalf

            if trade.trail:
                if float(trade.lastprice) == 0:
                    trade.lastprice = price
                    trade.traildrag = trade.tsl
                if price > trade.lastprice:
                    trade.traildrag += price-trade.lastprice

                if (price <= (trade.tsl+trade.traildrag)):
                    tsl_triggered = True

        elif trade.side == "PE":
            trade.pnl = ((float(trade.optionexit)-float(trade.optionentry))
                         * int(trade.optionlot)*int(mainlot))  # +trade.pnlhalf
            print(trade.pnl)
            if trade.sl is not None:

                if price >= (trade.entry_price + trade.sl):
                    sl_triggered = True
                    # trade.pnl=((float(trade.optionentry)-float(trade.optionexit))*int(trade.optionlot)*int(mainlot))+self.pnlhalf

            if (trade.tp_1 is not None) and int(mainlot) == int(trade.initial_lot):
                if price <= (float(trade.entry_price) - trade.tp_1):

                    currentexit = int(mainlot/2)
                    trade.lot = f"{str(int(int(mainlot)/2))}/{str(trade.initial_lot)}"
                    trade.sl = trade.entry_price
                    tp_triggered_1 = True
                    trade.pnlhalf = ((float(
                        trade.optionexit)-float(trade.optionentry))*int(trade.optionlot))*int(currentexit)
                    self._client.api.place_order(buy_or_sell='S', product_type='M',
                                                 exchange=self.exch, tradingsymbol=trade.optionname,
                                                 quantity=self.optionlot*(int(mainlot)/2), discloseqty=0, price_type='MKT', price=0, trigger_price=0,
                                                 retention='DAY', remarks='my_order_001')

                    # trade.pnl=((float(trade.optionentry)-float(trade.optionexit))*int(trade.optionlot)*int(mainlot))#+self.pnlhalf

            elif trade.tp_2 is not None:
                if price <= (float(trade.entry_price) - trade.tp_2):
                    tp_triggered = True
                    # trade.pnl=((float(trade.optionentry)-float(trade.optionexit))*int(trade.optionlot)*int(mainlot))+self.pnlhalf

            if trade.trail:
                if float(trade.lastprice) == 0:
                    trade.lastprice = price
                    trade.traildrag = trade.tsl
                if price < trade.lastprice:
                    trade.traildrag += price-trade.lastprice

                if (price >= (trade.tsl+trade.traildrag)):
                    tsl_triggered = True

        if tp_triggered_1:
            self._add_log(f"Take profit for {trade.symbol} | Current Price = {price} (Entry price was {trade.entry_price})"
                          f" Exited Lots {currentexit} and Current Holdings {trade.lot}")

        if tp_triggered or sl_triggered or exitit:
            if not exitit:

                self._add_log(f"{'Stop loss' if sl_triggered else 'Take profit'} for {trade.symbol}  "
                              f"| Current Price = {price} (Entry price was {trade.entry_price})")
            else:
                self._add_log(
                    f"Exited by User for {trade.symbol} | Current Price = {price} (Entry price was {trade.entry_price})")

            order_side = "SELL" if trade.side == "CE" else "BUY"

            # self._client.place_order(self.symbol, "MARKET", trade.quantity, order_side)
            order_status = True

            if order_status:
                trade.lot = f"0/{str(trade.initial_lot)}"
                self._client.api.place_order(buy_or_sell='S', product_type='M',
                                             exchange=self.exch, tradingsymbol=trade.optionname,
                                             quantity=self.optionlot*mainlot, discloseqty=0, price_type='MKT', price=0, trigger_price=0,
                                             retention='DAY', remarks='my_order_001')
                self._add_log(
                    f"Exit order on {self.symbol} placed successfully")
                trade.status = "closed"
                self.ongoing_position = False
                self.count = 2
                # self._client.logging_frame.add_log(str(vars(self._client.levelbasedstrats[self.b_index])))
                # print(str(vars(self._exchanges.exch.levelbasedstrats[b_index])))
                # self._trades_frame_levelbased.body_widgets['pnl_var']
                '''del self._client.levelbasedstrats[self.b_index]
                self._client._trades_frame_levelbased.body_widgets['LOT_var'][trade.time].set(
                    trade.lot)

                self._client._trades_frame_levelbased.body_widgets['STATUS_var'][trade.time].set(
                    trade.status.capitalize())
                self._client._trades_frame_levelbased.body_widgets['EXIT'][trade.time].config(
                    state=tk.DISABLED)

                for param in self._client._levelbased_frame._base_params:
                    code_name = param['code_name']

                    if code_name != "activation" and "_var" not in code_name:
                        self._client._levelbased_frame.body_widgets[code_name][self.b_index].config(
                            state=tk.NORMAL)

                self._client._levelbased_frame.body_widgets['activation'][self.b_index].config(bg="darkred", text="OFF")'''

    def check_trade(self, tick_type: str):
        
        """
        To be triggered from the websocket _on_message() methods. Triggered only once per candlestick to avoid
        constantly calculating the indicators. A trade can occur only if the is no open position at the moment.
        :param tick_type: same_candle or new_candle
        :return:
        """

        #if not self.ongoing_position:
        signal_result = self._check_signal()

        if signal_result in [1, -1]:
            self._open_position(signal_result)

    def parse_trades(self, price, otherprice, size):
        last_candle = self.candles[-1]
        pretimestamp = last_candle.timestamp
        # Same Candle
        timestamp = int(time.time())  # -60
        # print(last_candle.close)
        # print(pretimestamp+120)
        # print(timestamp)

        if timestamp < (pretimestamp + self.tf_equiv+self.tf_equiv):

            last_candle.close = price
            last_candle.volume += size
            # print(last_candle.close)

            if price > last_candle.high:
                last_candle.high = price
            elif price < last_candle.low:
                last_candle.low = price

            # Check Take profit / Stop loss

            for trade in self._client.trades:
                if trade.status == "open" and trade.entry_price is not None:
                    self._check_tp_sl(trade, price, otherprice)

            return "same_candle"

        elif timestamp >= (last_candle.timestamp + self.tf_equiv+self.tf_equiv):
            new_ts = last_candle.timestamp + self.tf_equiv
            new_candle = self._client.hist(self.symbol, tf='1')
            self.candles = new_candle
            self.marketcycle = self._client.hist15(self.symbol, tf='15m')
            logger.info(" New candle for %s %s", self.symbol, self.tf)

            return "new_candle"


class HuntLevel(LevelBased):

    def __init__(self, client, trigger_price: float, trigger_type: str, symbol: str, comparator_type: str, option_type: str, strike: str, lot: int, trail: bool, trail_stoploss: str, tp_1: float, tp_2: float, stop_loss: float,atmstrike: int,entry_id:int):
        super().__init__(client, trigger_price, trigger_type, symbol, comparator_type, option_type, strike, lot,
                         trail, trail_stoploss, tp_1, tp_2, stop_loss, atmstrike,entry_id)

    def _check_signal(self, price):
        """
        Compute technical indicators and compare their value to some predefined levels to know whether to go Long,
        Short, or do nothing.
        :return: 1 for a Long signal, -1 for a Short signal, 0 for no signal
        """

        #if self.count == 1:

        if self.comparator_type == '>=':
            if (price > float(self.trigger_price)) and self.option_type == 'CE':
                return 1
            elif (price > float(self.trigger_price)) and self.option_type == 'PE':
                return -1
        elif self.comparator_type == '<=':
            if (price < float(self.trigger_price)) and self.option_type == 'CE':
                return 1
            elif (price < float(self.trigger_price)) and self.option_type == 'PE':
                return -1
        else:
            return 0

    def check_trade(self, price, tick_type: str):
        """
        To be triggered from the websocket _on_message() methods. Triggered only once per candlestick to avoid
        constantly calculating the indicators. A trade can occur only if the is no open position at the moment.
        :param tick_type: same_candle or new_candle
        :return:
        """

        #if not self.ongoing_position:
        if self.entry_id not in self._client.ordersids:
            signal_result = self._check_signal(price)

            if signal_result in [1, -1]:
                self._open_position(signal_result)

    def parse_trades(self, price, otherprice, size):
        print('heeelllllllloooooooo')
        '''#for trade in self._client.trades:
            if trade.status == "open" and trade.entry_price is not None:
                option = self._client.prices[trade.optionname]
                price = self._client.prices[trade.symbol]
                self._check_tp_sl(trade, price, option)

                return "same_candle"
        '''
        for i in range(0,len(self._client.trades)):
            #for trade in self._client.trades:
            if self._client.trades[i].status == "open" and self._client.trades[i].entry_price is not None:
                option = self._client.prices[self._client.trades[i].optionname]
                price = self._client.prices[self._client.trades[i].symbol]
                self._client.trades[i].optionexit=option
                self._check_tp_sl(self._client.trades[i], price, option)

                return "same_candle"
