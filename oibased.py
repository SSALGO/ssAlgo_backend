import logging
from typing import *
import time

from threading import Timer
import pandas as pd
import math
from models import *
import datetime

'''if TYPE_CHECKING:  # Import the connector class names only for typing purpose (the classes aren't actually imported)
    #from connectors.bitmex import BitmexClient
    #from connectors.binance import BinanceClient
'''

logger = logging.getLogger()

# TF_EQUIV is used in parse_trades() to compare the last candle timestamp to the new trade timestamp
TF_EQUIV = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}



class OIBased:
    def __init__(self, client,symbol:str,option_type:str,strike:str,lot:int,trail:bool,trail_stoploss:str, 
                 tp_1:float,tp_2:float, stop_loss: float,option:str,optionprice:float,
                 exch:str,atmstrike:int,optionlot:int):

        self._client = client

        self.symbol = symbol
        self.strike=strike
        
        self.lot=lot
        self.initial_lot=lot
        self.option_type=option_type
        self.trail=trail
        self.direction='buy' if option_type== 'CE' else 'sell'
        self.trail_stoploss=float(trail_stoploss)#(float(trigger_price)-float(trail_stoploss)) if option_type=='CE' else (float(trigger_price)+float(trail_stoploss))
        self.tp_1=tp_1
        self.tp_2=tp_2
        self.stop_loss=stop_loss
        self.tf='1m'
        self.tf_equiv=TF_EQUIV[self.tf]
        self.option=option
        self.optionprice=optionprice
        self.exch=exch
        self.atmstrike=atmstrike
        self.ongoing_position = False
        self.candles: List[Candle] = []
        self.trades: List[OIBasedTrade] = []
        self.optionchain=pd.DataFrame()
        self.logs = []
        self.marketcycle= self._client.exch.hist15(self.symbol,tf='15')
        self.reconnect=True

    def _add_log(self, msg: str):
        logger.info("%s", msg)
        self.logs.append({"log": msg, "displayed": False})


    def _open_position(self, signal_result: int):

        """
        Open Long or Short position based on the signal result.
        :param signal_result: 1 (Long) or -1 (Short)
        :return:
        """


        trade_size = self.lot#self._client.get_trade_size(self.symbol, self.candles[-1].close, self.balance_pct)
        if trade_size is None:
            return



        order_side = "buy" if signal_result == 1 else "sell"
        position_side = "CE" if signal_result == 1 else "PE"

        self._add_log(f"{position_side.capitalize()} signal on {self.symbol} {self.tf}")

        order_status = True#self._client.place_order(self.symbol, "MARKET", trade_size, order_side)

        #if order_status is not None:
        self._add_log(f"{order_side.capitalize()} order placed Status: {order_status}")

        self.ongoing_position = True

        avg_fill_price = None

        '''self._add_log({"time": int(time.time() * 1000), 
        "track":self.trigger_type,"comparator_type":self.comparator_type,"option_type":self.option_type,"strike":self.strike,
            "entry_price": self.candles[-1].close,
                            "symbol": self.symbol,"side": position_side,"tp_1":self.tp_1,
                            "tp_2":self.tp_2,"SL":self.stop_loss,"tsl":self.trail_stoploss
                            ,"trail":self.trail,
                            "status": "open", "pnl": 0, "quantity": self.lot, "entry_id": 'none'})'''

        new_trade = OIBasedTrade({"time": int(time.time() * 1000),
                            "option_type":self.option_type,"strike":self.strike,
                            "entry_price": self._client.exch.prices[self.symbol],
                            "current_price":0,
                            "optionlot":0,

                            "optionentry":self.optionprice,
                            "optionexit":self.optionprice,
                            "symbol": self.symbol,"side": position_side,
                            "tp_1":self.tp_1,
                            "tp_2":self.tp_2,"SL":self.stop_loss,"tsl":self.trail_stoploss
                            ,"trail":self.trail,"traildrag":0,"lastprice":0,"initial_lot":self.lot,
                            "status": "open", "pnl": 0, "lot": self.lot})
        #self._add_log(vars(new_trade))
        self.trades.append(new_trade)

    def _check_tp_sl(self, trade: OIBasedTrade,prices,otherprice):

        """
        Based on the average entry price, calculates whether the defined stop loss or take profit has been reached.
        :param trade:
        :return:
        """

        tp_triggered = False
        sl_triggered = False
        tp_triggered_1=False
        tsl_triggered=False
        currentexit=0
        #if self.trigger_type=='On Spot':
        #price=prices
        price=prices

        #price = self.candles[-1].close

        if trade.side == "CE":
            if self.stop_loss is not None:
                if (price <= (trade.entry_price - self.stop_loss)) :
                    sl_triggered = True
            if (self.tp_1 is not None) and trade.lot==trade.initial_lot:
                if (price >= trade.entry_price + self.tp_1):
                    #self.lot = (self.lot%2)#int(self.lot/2)
                    trade.lot=(int(trade.lot)%2)
            elif self.tp_2 is not None:
                if (price >= trade.entry_price + self.tp_2):
                    tp_triggered = True
            if trade.trail:
                if float(trade.lastprice)==0:
                    trade.lastprice=price
                    trade.traildrag=trade.tsl
                if price > trade.lastprice:
                    trade.traildrag+=price-trade.lastprice

                if (price <= (trade.tsl+trade.traildrag) ):
                    tsl_triggered=True


        elif trade.side == "PE":
            if self.stop_loss is not None:
                if price >= (trade.entry_price + self.stop_loss):
                    sl_triggered = True
            if (self.tp_1 is not None) and trade.lot==trade.initial_lot:
                if price <= (trade.entry_price - self.tp_1):
                    #tp_triggered_1 = True
                    #self.lot =int(trade.lot/2)+(trade.lot%2)
                    currentexit=int(trade.lot/2)
                    trade.lot=int(trade.lot/2)+(trade.lot%2)
                    tp_triggered_1=True
            elif self.tp_2 is not None:
                if price <= (trade.entry_price - self.tp_2):
                    tp_triggered = True
            if trade.trail:
                if float(trade.lastprice)==0:
                    trade.lastprice=price
                    trade.traildrag=trade.tsl
                if price < trade.lastprice:
                    trade.traildrag+=price-trade.lastprice

                if (price >= (trade.tsl+trade.traildrag) ):
                    tsl_triggered=True

        if tp_triggered_1:
            self._add_log(f"Take profit for {self.symbol} | Current Price = {price} (Entry price was {trade.entry_price})"
                f" Exited Lots {currentexit} and Current Holdings {trade.lot}")


        if tp_triggered or sl_triggered:

            self._add_log(f"{'Stop loss' if sl_triggered else 'Take profit'} for {self.symbol}  "
                          f"| Current Price = {price} (Entry price was {trade.entry_price})")

            order_side = "SELL" if trade.side == "long" else "BUY"

            order_status = True #self._client.place_order(self.symbol, "MARKET", trade.quantity, order_side)

            if order_status is not None:
                self._add_log(f"Exit order on {self.symbol} placed successfully")
                trade.status = "closed"
                self.ongoing_position = False




    def check_trade(self, tick_type: str):

        """
        To be triggered from the websocket _on_message() methods. Triggered only once per candlestick to avoid
        constantly calculating the indicators. A trade can occur only if the is no open position at the moment.
        :param tick_type: same_candle or new_candle
        :return:
        """

        if tick_type == "same_candle" and not self.ongoing_position:
            signal_result = self._check_signal()

            if signal_result in [1, -1]:
                self._open_position(signal_result)

    def parse_trades(self,price,otherprice,size):
        

        last_candle = self.candles[-1]
        pretimestamp=last_candle.timestamp 
        # Same Candle
        timestamp=int(time.time())#-60
        #print(last_candle.close)
        #print(pretimestamp+120)
        #print(timestamp)

        if timestamp < (pretimestamp + self.tf_equiv+self.tf_equiv):
            
            last_candle.close = price
            last_candle.volume += size
            #print(last_candle.close)

            if price > last_candle.high:
                last_candle.high = price
            elif price < last_candle.low:
                last_candle.low = price

            # Check Take profit / Stop loss

            for trade in self.trades:
                if trade.status == "open" and trade.entry_price is not None:
                    self._check_tp_sl(trade,price,otherprice)

            return "same_candle"

        elif timestamp >= (last_candle.timestamp + self.tf_equiv+self.tf_equiv):
            new_ts = last_candle.timestamp + self.tf_equiv
            #candle_info = {'ts': new_ts, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': size}
            new_candle = self._client.exch.hist(self.symbol,tf='1')
            self.candles=new_candle
            #optionchain = self._client.exch.optionchain(self.exch,self.option,self.atmstrike)
            #self.optionchain=optionchain
            self.marketcycle= self._client.exch.hist15(self.symbol,tf='15')


            logger.info(" New candle for %s %s", self.symbol, self.tf)

            return "new_candle"

class OILevel(OIBased):
    
    def __init__(self, client,symbol:str,option_type:str,strike:str,lot:int,
        trail:bool,trail_stoploss:str, tp_1:float,tp_2:float, 
        stop_loss: float,option:str,optionprice:float,exch:str,
        atmstrike:float ,optionlot:int,other_params: Dict):
        super().__init__( client,symbol,option_type,strike,lot,trail,trail_stoploss,tp_1,tp_2, stop_loss,option,optionprice,exch,atmstrike,optionlot)


    def _check_signal(self,price):

        """
        Compute technical indicators and compare their value to some predefined levels to know whether to go Long,
        Short, or do nothing.
        :return: 1 for a Long signal, -1 for a Short signal, 0 for no signal
        """
        from market_profile import MarketProfile
        mp = MarketProfile(self.marketcycle,mode='tpo')
        mp_slice = mp[self.marketcycle.index.min():self.marketcycle.index.max()]
        data = mp_slice.poc_price
        print('poc')
        print(data)
        indexltp=float(price)
        if self.symbol=='BANKNIFTY':
            l=100
        else:
            l=50
        mod=int(price)%50
        if mod <25:
            atmstrike = int(math.floor(indexltp/l))*l
        else:
            atmstrike = int(math.ceil(indexltp/l))*l
        print(atmstrike)
        print('kuuuuu')
        #print(self._client.exch.tenstrikes[self.symbol])
        ten=self._client.exch.tenstrikes[self.symbol]
        samestrike=ten[ten['#']==(float(atmstrike))].iloc[-1]

        #curr=(self._client.exch.oi_data[self.symbol][str(float(atmstrike))])
        
        ccurrmain=(samestrike['PE_TOI']/samestrike['CE_TOI'])>1
        ccurrsub=(samestrike['PE_COI']/samestrike['CE_COI'])>1
        pcurrmain=(samestrike['CE_TOI']/samestrike['PE_TOI'])>1
        pcurrsub=(samestrike['CE_COI']/samestrike['PE_COI'])>1
        if (price < float(atmstrike+10)) and ccurrmain and ccurrsub  and self.option_type=='CE':
            return 1
        elif (price > float(atmstrike-10)) and pcurrmain and pcurrsub and self.option_type=='PE':
            return -1
        return 0

    def check_trade(self, price,tick_type: str):

        """
        To be triggered from the websocket _on_message() methods. Triggered only once per candlestick to avoid
        constantly calculating the indicators. A trade can occur only if the is no open position at the moment.
        :param tick_type: same_candle or new_candle
        :return:
        """

        if tick_type == "same_candle" and not self.ongoing_position:
            signal_result = self._check_signal(price)

            if signal_result in [1, -1]:
                self._open_position(signal_result)























