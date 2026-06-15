import pytz
import dateutil.parser
import datetime
import dataclasses
import time
import pydantic

BITMEX_MULTIPLIER = 0.00000001  # Converts satoshi numbers to Bitcoin on Bitmex
BITMEX_TF_MINUTES = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}


class Balance:
    def __init__(self, info, exchange):
        if exchange == "binance_futures":
            self.initial_margin = float(info['initialMargin'])
            self.maintenance_margin = float(info['maintMargin'])
            self.margin_balance = float(info['marginBalance'])
            self.wallet_balance = float(info['walletBalance'])
            self.unrealized_pnl = float(info['unrealizedProfit'])

        elif exchange == "binance_spot":
            self.free = float(info['free'])
            self.locked = float(info['locked'])

        elif exchange == "bitmex":
            self.initial_margin = info['initMargin'] * BITMEX_MULTIPLIER
            self.maintenance_margin = info['maintMargin'] * BITMEX_MULTIPLIER
            self.margin_balance = info['marginBalance'] * BITMEX_MULTIPLIER
            self.wallet_balance = info['walletBalance'] * BITMEX_MULTIPLIER
            self.unrealized_pnl = info['unrealisedPnl'] * BITMEX_MULTIPLIER


class RF_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: float = float(order['k1'])
        self.r2: int = int(order['r1'])
        self.k2: float = float(order['k1'])
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='RF'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j


class FRACTALNUBIATIMEHEDGEORDER_mode:
    def __init__(self, order):
        print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id: int = 0
        self.time: int = j
        self.user: str = order['user']
        self.symbol: str = order['symbol']
        self.botname:str=order.get('botname', str(j)) #order['botname']
        self.direction_type: str = order['direction_type']
        self.method: str = order['method']
        self.afterentrytime: str = order['afterentrytime']
        self.ExitatTime: str = order['ExitatTime']
        self.timeframe: str = order['timeframe']
        self.trigger_price: float = float(order['trigger_price'])
        self.comparator_type: str = order['comparator_type']
        self.trigger_type: str = order['trigger_type']
        self.sltrigger_type: str = order['sltrigger_type']
        self.tptrigger_type: str = order['tptrigger_type']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tpprice: bool = order['tpprice'] == '1'
        self.tpfibo: bool = order['tpfibo'] == '1'
        self.slprice: bool = order['slprice'] == '1'
        self.slfibo: bool = order['slfibo'] == '1'
        self.slsignal: bool =order['slsignal']=='1'
        self.tp_1qty: int = int(order['tp_1qty'])
        self.tp_2qty: int = int(order['tp_2qty'])
        self.tp1: float = float(order['tp_1'])
        self.pnl:float=float(order['pnl'])
        
        self.tp2: float = float(order['tp_2'])
        self.sl: float = float(order['sl'])
        self.tp1type: str = order['tp1type']
        self.tp2type: str = order['tp2type']
        self.sltype: str = order['sltype']
        self.status: str = order['status']
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.strategy: str = 'FRACTALNUBIATIMEHEDGEORDER'
        self.botcode: str = order['botcode']# + str(j) + 'EORDER'
        self.live: bool = order['live'].lower() == 'true'
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.position:str=order.get('position', 'out')#order['position']
        self.exittime: int = order.get('exittime', 0)  # Adding exittime if provided
        self.legs:list =order['legs']

        self.usetype: bool = order['usetype'].lower() == 'true'


class SSTRIKE_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSTRIKE'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j

class Candle:
    def __init__(self, candle_info):
        # print(candle_info)
        self.timestamp = datetime.datetime.timestamp(
            datetime.datetime.strptime((candle_info['time']), '%d-%m-%Y %H:%M:%S'))
        self.open = float(candle_info['open'])
        self.high = float(candle_info['high'])
        self.low = float(candle_info['low'])
        self.close = float(candle_info['close'])
        self.volume = float(candle_info['intv'])


def tick_to_decimals(tick_size: float) -> int:
    tick_size_str = "{0:.8f}".format(tick_size)
    while tick_size_str[-1] == "0":
        tick_size_str = tick_size_str[:-1]

    split_tick = tick_size_str.split(".")

    if len(split_tick) > 1:
        return len(split_tick[1])
    else:
        return 0


class Contract:
    def __init__(self, contract_info, exchange):
        if exchange == "binance_futures":
            self.symbol = contract_info['symbol']
            self.base_asset = contract_info['baseAsset']
            self.quote_asset = contract_info['quoteAsset']
            self.price_decimals = contract_info['pricePrecision']
            self.quantity_decimals = contract_info['quantityPrecision']
            self.tick_size = 1 / pow(10, contract_info['pricePrecision'])
            self.lot_size = 1 / pow(10, contract_info['quantityPrecision'])

        elif exchange == "binance_spot":
            self.symbol = contract_info['symbol']
            self.base_asset = contract_info['baseAsset']
            self.quote_asset = contract_info['quoteAsset']

            # The actual lot size and tick size on Binance spot can be found in the 'filters' fields
            # contract_info['filters'] is a list
            for b_filter in contract_info['filters']:
                if b_filter['filterType'] == 'PRICE_FILTER':
                    self.tick_size = float(b_filter['tickSize'])
                    self.price_decimals = tick_to_decimals(
                        float(b_filter['tickSize']))
                if b_filter['filterType'] == 'LOT_SIZE':
                    self.lot_size = float(b_filter['stepSize'])
                    self.quantity_decimals = tick_to_decimals(
                        float(b_filter['stepSize']))

        elif exchange == "bitmex":
            self.symbol = contract_info['symbol']
            self.base_asset = contract_info['rootSymbol']
            self.quote_asset = contract_info['quoteCurrency']
            self.price_decimals = tick_to_decimals(contract_info['tickSize'])
            self.quantity_decimals = tick_to_decimals(contract_info['lotSize'])
            self.tick_size = contract_info['tickSize']
            self.lot_size = contract_info['lotSize']

            self.quanto = contract_info['isQuanto']
            self.inverse = contract_info['isInverse']

            self.multiplier = contract_info['multiplier'] * BITMEX_MULTIPLIER

            if self.inverse:
                self.multiplier *= -1

        self.exchange = exchange


class OrderStatus:
    def __init__(self, order_info, exchange):
        if exchange == "binance_futures":
            self.order_id = order_info['orderId']
            self.status = order_info['status'].lower()
            self.avg_price = float(order_info['avgPrice'])
            self.executed_qty = float(order_info['executedQty'])
        elif exchange == "binance_spot":
            self.order_id = order_info['orderId']
            self.status = order_info['status'].lower()
            self.avg_price = float(order_info['avgPrice'])
            self.executed_qty = float(order_info['executedQty'])
        elif exchange == "bitmex":
            self.order_id = order_info['orderID']
            self.status = order_info['ordStatus'].lower()
            self.avg_price = order_info['avgPx']
            self.executed_qty = order_info['cumQty']


class WebOrder:
    def __init__(self, order):
        print(order)
        j=int(datetime.datetime.now().timestamp())
        self.user: str = order['user']
        self.time: int = j
        self.entry_id: int = self.time
        self.trigger_price: float = float(order['trigger_price'])
        self.trigger_type: str = order['trigger_type']
        self.symbol: str = order['symbol']
        self.comparator_type: str = order['comparator_type']
        self.option_type: str = order['option_type']
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.trail: int = int(order['trail'] )
        self.trail_stoploss: float = float(order['trail_stoploss'])
        self.tp_1: float = float(order['tp_1'])
        self.tp_2: float = float(order['tp_2'])
        self.sl: float = float(order['sl'])
        self.status: str= order['status']#'opened'
        #if 'exittime' not in list(order.keys()):
        self.exittime=order['exittime']



class EMA_fut_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r1'])
        self.k2: int = int(order['k1'])
        self.onspot:bool =order['onspot'].lower() == 'true'
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='EMA'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j









        
class EMA_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r1'])
        self.k2: int = int(order['k1'])
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='EMA'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j








class PEMA_fut_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r1'])
        self.k2: int = int(order['k1'])
        self.onspot:bool =order['onspot'].lower() == 'true'
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='PEMA'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j


class PEMA_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r1'])
        self.k2: int = int(order['k1'])
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='PEMA'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j



class SSALGO_fut_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])
        self.onspot:bool =order['onspot'].lower() == 'true'
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSALGO'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j









        
class SSALGO_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])

        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSALGO'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j




        
class EQSSALGO_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: list = list(order['symbol'])
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.FixedLot1: str = order['FixedLot1']
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.lot: int = int(order['lot'])
        self.ttw: int = int(order['ttw'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='EQSSALGO'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j


class MCXSTRATEGY_mode:
    def __init__(self, order):
        j = int(datetime.datetime.now().timestamp())
        self.entry_id: int = 0
        self.botname: str = order['botname']
        self.user: str = order['user']
        self.time: int = j
        self.exchange: str = 'MCX'
        self.symbol: str = order['symbol']
        self.Expiry: str = order.get('Expiry', 'Current Month')
        self.timeframe: str = order['timeframe']
        self.mcx_strategy_type: str = order['mcx_strategy_type']
        self.trade_side: str = order['trade_side']
        self.product_type: str = order['product_type']
        self.order_type: str = order['order_type']
        self.range_minutes: int = int(order['range_minutes'])
        self.atr_period: int = int(order['atr_period'])
        self.breakout_atr_multiple: float = float(order['breakout_atr_multiple'])
        self.stop_atr_multiple: float = float(order['stop_atr_multiple'])
        self.target_r_multiple: float = float(order['target_r_multiple'])
        self.adx_min: int = int(order['adx_min'])
        self.ema_fast: int = int(order['ema_fast'])
        self.ema_slow: int = int(order['ema_slow'])
        self.lot: int = int(order['lot'])
        self.max_trades_per_day: int = int(order['max_trades_per_day'])
        self.risk_per_trade_pct: float = float(order['risk_per_trade_pct'])
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.live: bool = order['live'].lower() == 'true'
        self.status: str = order['status']
        self.position: str = order['position']
        self.strategy: str = 'MCXSTRATEGY'
        self.botcode: str = order['botcode']
        self.timetowait: int = j





























class SSEQUITY_fut_mode:
    def __init__(self, order):
        print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])
        self.onspot:bool =order['onspot'].lower() == 'true'
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSEQUITY'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j









        
class SSEQUITY_mode:
    def __init__(self, order):
        print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])

        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSEQUITY'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j


        
class SSEQUITY_EQ_mode:
    def __init__(self, order):
        print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.qty:int=order['lot']
        self.lot:int=order['lot']
        self.stocks:int=order['stocks']
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.slicing: int = int(order['slicing'])
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSEQUITY'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
  
class SSEQUITYFNO_EQ_mode:
    def __init__(self, order):
        print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.timeframe: str = order['timeframe']
        self.time:int=j
        self.qty:int=order['lot']
        self.lot:int=order['lot']
        self.positiontype:str=order['positiontype']
        self.stocks:int=order['stocks']
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.slicing: int = int(order['slicing'])
        self.StartTime: str = order['StartTime']
        self.signalend: str = order['signalend']

        self.ExitTime: str = order['ExitTime']
        self.DaysHead: int = int(order['DaysHead'])
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSEQUITYFNO'
        self.position:str=order['position']
        self.botcode:str=order['botcode']


class SSAUTO_fut_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])
        self.onspot:bool =order['onspot'].lower() == 'true'
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSAUTO'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j



        
class SSAUTO_mode:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r1: int = int(order['r1'])
        self.k1: int = int(order['k1'])
        self.r2: int = int(order['r2'])
        self.k2: int = int(order['k2'])

        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: str = order['FixedLot']
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.initiallot: int = int(order['initiallot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSAUTO'
        self.position:str=order['position']
        self.botcode:str=order['botcode']
        self.timetowait:int = j



class SSALGO1:
    def __init__(self, order):
        #print(order)
        j = int(datetime.datetime.now().timestamp())
        self.entry_id:int=0
        self.botname:str=order['botname']
        self.user:str=order['user']
        self.time:int=j
        self.symbol: str = order['symbol']
        self.Expiry: str = order['Expiry']
        self.timeframe: str = order['timeframe']
        self.r: int = int(order['r'])
        self.k: int = int(order['k'])
        self.Newsignal: bool = order['Newsignal'].lower() == 'true'
        self.USEMA: bool = order['USEMA'].lower() == 'true'
        self.ema: int = int(order['ema'])
        self.Intraday: bool = order['Intraday'].lower() == 'true'
        self.FixedLot: bool = order['FixedLot'].lower() == 'true'
        self.BSmode: bool = order['BSmode'].lower() == 'true'
        self.pct_point: bool = order['pct_point'].lower() == 'true'
        self.pnlexit_tpslexit: bool = order['pnlexit_tpslexit'].lower() == 'true'
        self.strike: int = int(order['strike'])
        self.lot: int = int(order['lot'])
        self.ttw: int = int(order['ttw'])
        self.stepvalue: int = int(order['stepvalue'])
        self.MultiFactor: int = int(order['MultiFactor'])
        self.candle1: int = int(order['candle1'])
        self.candle2: int = int(order['candle2'])
        self.slicing: int = int(order['slicing'])
        self.DaysHead: int = int(order['DaysHead'])
        self.RolloverTime: str = order['RolloverTime']
        self.StartTime: str = order['StartTime']
        self.ExitTime: str = order['ExitTime']
        self.trail: int = int(order['trail'])
        self.trail_stoploss: int = int(order['trail_stoploss'])
        self.tp: int = int(order['tp'])
        self.sl: int = int(order['sl'])
        self.status: str = order['status']
        self.maxprofit: int = int(order['maxprofit'])
        self.maxloss: int = int(order['maxloss'])
        self.live: bool = order['live'].lower() == 'true'
        self.strategy:str='SSALGO'
        self.position:str=order['position']
class Trade:
    def __init__(self, trade_info):
        self.time: int = trade_info['time']
        self.contract: Contract = trade_info['contract']
        self.strategy: str = trade_info['strategy']
        self.side: str = trade_info['side']
        self.entry_price: float = trade_info['entry_price']
        self.status: str = trade_info['status']
        self.pnl: float = trade_info['pnl']
        self.quantity = trade_info['quantity']
        self.entry_id = trade_info['entry_id']


class LevelBasedTrade:
    def __init__(self, trade_info):
        self.time: int = trade_info['time']
        self.entry_id: int = trade_info['entry_id']
        self.symbol: str = trade_info['symbol']
        self.entry_price: float = trade_info['entry_price']
        self.side: str = trade_info['side']
        self.tp_1: float = trade_info['tp_1']
        self.tp_2: float = trade_info['tp_2']
        self.trail: bool = trade_info['trail']
        self.comparator_type: str = trade_info['comparator_type']
        self.track: str = trade_info['track']
        self.strike: str = trade_info['strike']
        self.option_type: str = trade_info['option_type']
        self.tsl: float = trade_info['tsl']
        self.traildrag: float = trade_info['traildrag']
        self.lastprice: float = trade_info['lastprice']
        self.sl: float = trade_info['sl']
        self.status: str = trade_info['status']
        self.pnl: float = trade_info['pnl']
        self.lot: str = trade_info['lot']
        self.initial_lot: int = trade_info['initial_lot']
        self.optionentry: float = trade_info['optionentry']
        self.optionexit: float = trade_info['optionexit']
        self.optionlot: int = trade_info['optionlot']
        self.optionname: str = trade_info['optionname']
        self.pnlhalf: float = trade_info['pnlhalf']
        self.decision: str = trade_info['decision']


class OIBasedTrade:
    def __init__(self, trade_info):
        self.time: int = trade_info['time']
        self.symbol: str = trade_info['symbol']
        self.entry_price: float = trade_info['entry_price']
        self.side: str = trade_info['side']
        self.tp_1: float = trade_info['tp_1']
        self.tp_2: float = trade_info['tp_2']
        self.trail: bool = trade_info['trail']
        self.strike: str = trade_info['strike']
        self.option_type: str = trade_info['option_type']
        self.tsl: float = trade_info['tsl']
        self.traildrag: float = trade_info['traildrag']
        self.lastprice: float = trade_info['lastprice']
        self.sl: float = trade_info['SL']
        self.status: str = trade_info['status']
        self.pnl: float = trade_info['pnl']
        self.lot: int = trade_info['lot']
        self.initial_lot: int = trade_info['initial_lot']
        self.optionentry: float = trade_info['optionentry']
        self.optionexit: float = trade_info['optionexit']
        self.optionlot: int = trade_info['optionlot']


class OIstrike:
    def __init__(self, oi):
        self.index = oi['index']
        self.no = oi['#']
        self.ce_coi = oi['CE_COI']
        self.pe_coi = oi['PE_COI']
        self.ce_toi = oi['CE_TOI']
        self.pe_toi = oi['PE_TOI']
        self.ce_vol = oi['CE_Vol']
        self.pe_vol = oi['PE_Vol']
        self.ce_pct = round(oi['CE_PCT'], 2)
        self.pe_pct = round(oi['PE_PCT'], 2)
