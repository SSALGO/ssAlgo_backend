# import MetaTrader5 as mt5
import warnings
import math
import json
import logging
import datetime
import yaml
import threading
import atexit
import enum
import contextlib
import io
import ssl
import socket
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd
from finta import TA
import pymongo
import asyncio
import aiohttp
from oibased import OILevel
from levelbased import HuntLevel
from strategies import TechnicalStrategy, BreakoutStrategy
from models import *
from urllib.parse import parse_qs, quote, urlparse
import os
import re
from dateutil.relativedelta import relativedelta
import time
from typing import *
import typing
import requests

from app.core.config import AppConfig
from app.core.trading_debug import trading_event, trading_exception
from app.core.secrets import decrypt_secret, decrypt_secret_fields, encrypt_secret
from app.domain.brokers.aliceblue_auth import (
    AliceBlueDirectAuthError,
    AliceBlueDirectAuthenticator,
)
from app.domain.brokers.health import SECRET_FIELD_NAMES

INDIA_MARKET_TIMEZONE = datetime.timezone(
    datetime.timedelta(hours=5, minutes=30),
    name="IST",
)


def india_market_now():
    return datetime.datetime.now(datetime.UTC).astimezone(
        INDIA_MARKET_TIMEZONE
    )


def strategy_market_window(trade, marketdays=5, intraday_close=None, now=None):
    if now is None:
        now = india_market_now()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.UTC)
    now = now.astimezone(INDIA_MARKET_TIMEZONE)
    current_time = now.time().replace(tzinfo=None)
    start_time = datetime.datetime.strptime(
        trade["StartTime"], "%H:%M"
    ).time()
    exit_time = datetime.datetime.strptime(
        intraday_close or trade["ExitTime"], "%H:%M"
    ).time()
    market_day = now.weekday() < marketdays
    inside_window = market_day and start_time < current_time < exit_time
    return {
        "intraday": inside_window and bool(trade["Intraday"]),
        "positional": inside_window and not bool(trade["Intraday"]),
        "market_day": market_day,
        "market_time": current_time,
    }


ALICEBLUE_DNS_HOSTS = {
    "a3.aliceblueonline.com",
    "ant.aliceblueonline.com",
    "v2api.aliceblueonline.com",
    "ws1.aliceblueonline.com",
}


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def install_aliceblue_dns_fallback():
    original_getaddrinfo = socket.getaddrinfo
    cache = {}

    def resolve_with_doh(hostname):
        cached = cache.get(hostname)
        if cached:
            return cached
        response = requests.get(
            f"https://1.1.1.1/dns-query?name={hostname}&type=A",
            headers={"accept": "application/dns-json", "Host": "cloudflare-dns.com"},
            verify=False,
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        addresses = [
            item.get("data")
            for item in payload.get("Answer", [])
            if item.get("type") == 1 and item.get("data")
        ]
        if not addresses:
            raise socket.gaierror(f"No DNS A record for {hostname}")
        cache[hostname] = addresses
        return addresses

    def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except socket.gaierror:
            hostname = str(host).lower()
            if hostname not in ALICEBLUE_DNS_HOSTS:
                raise
            results = []
            for address in resolve_with_doh(hostname):
                results.extend(original_getaddrinfo(address, port, family, type, proto, flags))
            return results

    socket.getaddrinfo = patched_getaddrinfo


install_aliceblue_dns_fallback()
try:
    from TradeMaster.TradeSync import TradeHub as AntA3TradeHub
    from TradeMaster.TradeSync import Instrument as AntA3Instrument
except ImportError:
    AntA3TradeHub = None
    AntA3Instrument = None
import pyotp
import numpy as np
import sqlite3
import concurrent.futures
import platform
from rkindicator import *

from NorenRestApiPy.NorenApi import  NorenApi
if platform.system()=='Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    requests.get('https://github.com', verify=True, timeout=5)
except requests.RequestException:
    pass

# mt5.initialize()
warnings.filterwarnings('ignore')
'''tf={"1m": mt5.TIMEFRAME_M1, "2m": mt5.TIMEFRAME_M2, "3m": mt5.TIMEFRAME_M3,
                         "4m": mt5.TIMEFRAME_M4, "5m": mt5.TIMEFRAME_M5, "6m": mt5.TIMEFRAME_M6,
                         "10m": mt5.TIMEFRAME_M10, "15m": mt5.TIMEFRAME_M15,"30m": mt5.TIMEFRAME_M30,
                         "1h": mt5.TIMEFRAME_H1,"4h": mt5.TIMEFRAME_H4, "1D": mt5.TIMEFRAME_D1}'''
logger = logging.getLogger()


def print(*args, **kwargs):
    from app.core.logging_config import log_print

    log_print(logger, *args, **kwargs)
from collections import namedtuple


#Instrument = namedtuple('Instrument', ['exchange', 'token', 'symbol','name', 'expiry', 'lot_size'])

Instrument = namedtuple('Instrument', ['exchange', 'token', 'symbol', 'name', 'expiry', 'lot_size'])


class TransactionType(enum.Enum):
    Buy = 'BUY'
    Sell = 'SELL'

class LiveFeedType(enum.IntEnum):
    MARKET_DATA     = 1
    COMPACT         = 2
    SNAPQUOTE       = 3
    FULL_SNAPQUOTE  = 4

class OrderType(enum.Enum):
    Market = 'MKT'
    Limit = 'L'
    StopLossLimit = 'SL'
    StopLossMarket = 'SL-M'

class ProductType(enum.Enum):
    Intraday = 'MIS'
    Delivery = 'CNC'
    Longterm = 'LONGTERM'
    CoverOrder = 'CO'
    BracketOrder = 'BO'
    Normal = 'NRML'


class AliceBlueTradeHubAdapter:
    """Compatibility wrapper for the Ant-A3 SDK used by existing order flows."""
    BASE_URL = 'https://a3.aliceblueonline.com'

    ORDER_TYPE_MAP = {
        'MKT': 'MARKET',
        'MARKET': 'MARKET',
        'L': 'LIMIT',
        'LIMIT': 'LIMIT',
        'SL': 'SL',
        'SL-M': 'SLM',
        'SLM': 'SLM',
    }
    PRODUCT_TYPE_MAP = {
        'MIS': 'INTRADAY',
        'CNC': 'LONGTERM',
        'NRML': 'LONGTERM',
        'NORMAL': 'LONGTERM',
        'LONGTERM': 'LONGTERM',
        'DELIVERY': 'LONGTERM',
        'INTRADAY': 'INTRADAY',
        'MTF': 'MTF',
    }

    def __init__(self, user_id, auth_code, secret_key, session_id=None):
        if AntA3TradeHub is None:
            raise ImportError("Ant-A3 TradeMaster SDK is not installed")
        self.user_id = user_id
        self.auth_code = auth_code or ''
        self.secret_key = secret_key
        self.session_id = session_id
        self.trade = AntA3TradeHub(
            user_id=user_id,
            auth_code=auth_code or '',
            secret_key=secret_key,
            session_id=session_id
        )

    @staticmethod
    def _enum_value(value):
        return getattr(value, 'value', value)

    @staticmethod
    def _blank_if_none(value):
        return '' if value is None else value

    def get_session_id(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.trade.get_session_id(*args, **kwargs)
        if isinstance(response, dict) and response.get('userSession'):
            response.setdefault('sessionID', response['userSession'])
        if isinstance(response, dict) and response.get('sessionID'):
            self.session_id = response['sessionID']
        return response

    def get_profile(self):
        return self.trade.get_profile()

    def _resolve_session_token(self):
        candidates = [
            self.session_id,
            getattr(self.trade, 'session_id', None),
            getattr(self.trade, 'sessionID', None),
            getattr(self.trade, 'userSession', None),
            getattr(self.trade, 'user_session', None),
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                token = candidate.get('sessionID') or candidate.get('userSession')
            else:
                token = candidate
            token = str(token or '').strip()
            if token:
                self.session_id = token
                return token
        return None

    def _normalize_exchange(self, exchange):
        exchange = str(exchange or '').upper()
        exchange_map = {
            'NSECM': 'NSE',
            'NFO': 'NFO',
            'NSEFO': 'NFO',
            'BSECM': 'BSE',
            'BFO': 'BFO',
            'BSEFO': 'BFO',
            'MCXFO': 'MCX',
            'MFO': 'MCX',
            'CDSFO': 'CDS',
        }
        return exchange_map.get(exchange, exchange)

    def get_positions(self):
        trade_client = getattr(self, 'trade', None)
        for method_name in ('get_positions', 'get_position', 'positions', 'get_netwise_positions'):
            method = getattr(trade_client, method_name, None)
            if callable(method):
                response = method()
                if isinstance(response, dict):
                    result = response.get('result')
                    if isinstance(result, list):
                        return response
                elif isinstance(response, list):
                    return {'status': 'Ok', 'message': 'Success', 'result': response}

        session_token = self._resolve_session_token()
        if not session_token:
            return {'status': 'Not_ok', 'message': 'AliceBlue session token missing for positions'}

        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {session_token}',
        }
        response = requests.get(
            f'{self.BASE_URL}/open-api/od/v1/positions',
            headers=headers,
            timeout=15,
        )
        try:
            result = response.json()
        except ValueError:
            result = {
                'status': 'Not_ok',
                'message': f'Invalid positions response ({response.status_code})',
                'raw': response.text,
            }
        if response.status_code >= 400 and isinstance(result, dict):
            result.setdefault('http_status', response.status_code)
        return result

    def find_open_position(self, exchange, instrument_id=None, symbol=None):
        response = self.get_positions()
        rows = response.get('result') if isinstance(response, dict) else None
        if not isinstance(rows, list):
            return None, response

        exchange = self._normalize_exchange(exchange)
        instrument_id = str(instrument_id or '').strip()
        normalized_symbol = str(symbol or '').replace(' ', '').upper()

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_exchange = self._normalize_exchange(row.get('exchange'))
            row_instrument_id = str(row.get('instrumentId') or '').strip()
            row_symbols = [
                str(row.get('tradingSymbol') or '').replace(' ', '').upper(),
                str(row.get('formattedInstrumentName') or '').replace(' ', '').upper(),
            ]
            try:
                net_quantity = abs(int(float(row.get('netQuantity') or 0)))
            except Exception:
                net_quantity = 0

            if row_exchange != exchange or net_quantity <= 0:
                continue
            if instrument_id and row_instrument_id == instrument_id:
                return row, response
            if normalized_symbol and normalized_symbol in row_symbols:
                return row, response
        return None, response

    def square_off_position(
        self, transaction_type, quantity, product_type, exchange, instrument_id,
        symbol, order_type='MKT', price=None, trigger_price=None, order_tag=None
    ):
        transaction_type = self._enum_value(transaction_type)
        order_type = str(self._enum_value(order_type) or 'MKT').upper()
        product_type = self.PRODUCT_TYPE_MAP.get(
            str(self._enum_value(product_type)).upper(),
            self._enum_value(product_type)
        )
        exchange = self._normalize_exchange(exchange)
        instrument_id = str(instrument_id)
        position_row, positions_response = self.find_open_position(exchange, instrument_id=instrument_id, symbol=symbol)
        if position_row:
            symbol = (
                position_row.get('tradingSymbol')
                or position_row.get('formattedInstrumentName')
                or symbol
            )
            product_type = position_row.get('product') or product_type
            instrument_id = str(position_row.get('instrumentId') or instrument_id)
        elif isinstance(positions_response, dict) and positions_response.get('status'):
            return {
                'status': 'Ok',
                'message': 'No matching AliceBlue open position; treating as already closed',
                'result': [],
                'already_closed': True,
            }
        payload = [{
            'exchange': exchange,
            'symbol': symbol,
            'quantity': str(int(quantity)),
            'price': '' if order_type == 'MKT' else self._blank_if_none(price),
            'product': product_type,
            'transactionType': transaction_type,
            'orderType': order_type,
            'triggerPrice': self._blank_if_none(trigger_price),
            'ret': 'DAY',
            'disclosedQuantity': '',
            'mktProtection': '',
            'target': '',
            'stopLoss': '',
            'trailingStopLoss': '',
            'orderComplexity': 'REGULAR',
            'source': 'WEB',
            'instrumentId': instrument_id,
            'deviceNumber': '',
            'orderTag': order_tag or '',
        }]

        trade_client = getattr(self, 'trade', None)
        for method_name in ('square_off_position', 'squareoff_position', 'squareoffPosition', 'position_square_off'):
            method = getattr(trade_client, method_name, None)
            if callable(method):
                try:
                    return method(payload)
                except TypeError:
                    try:
                        return method(**payload[0])
                    except TypeError:
                        pass

        session_token = self._resolve_session_token()
        if not session_token:
            return {'status': 'Not_ok', 'message': 'AliceBlue session token missing for square-off'}

        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {session_token}',
        }
        response = requests.post(
            f'{self.BASE_URL}/open-api/od/v1/orders/positions/sqroff',
            headers=headers,
            json=payload,
            timeout=15,
        )
        try:
            result = response.json()
        except ValueError:
            result = {
                'status': 'Not_ok',
                'message': f'Invalid response ({response.status_code})',
                'raw': response.text,
            }
        if response.status_code >= 400 and isinstance(result, dict):
            result.setdefault('http_status', response.status_code)
        return result

    def get_instrument_by_token(self, exchange, token):
        return self.trade.get_instrument(exchange=exchange, token=token)

    def get_instrument_by_symbol(self, exchange, symbol):
        return self.trade.get_instrument(exchange=exchange, symbol=symbol)

    def _aliceblue_websocket_session_request(self, action, session_id):
        endpoint_map = {
            "invalidate": "invalidateWsSess",
            "create": "createWsSess",
        }
        endpoint = endpoint_map[action]
        headers = {
            "Authorization": f"Bearer {session_id}",
            "Content-Type": "application/json",
        }
        payload = {
            "source": "API",
            "userId": self.user_id,
        }
        last_error = None
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    f"{self.BASE_URL}/open-api/od/v1/profile/{endpoint}",
                    headers=headers,
                    json=payload,
                    timeout=15,
                )
                content_type = response.headers.get("content-type", "")
                body_preview = str(response.text or "")[:200]
                try:
                    result = response.json()
                except ValueError as exc:
                    raise RuntimeError(
                        f"AliceBlue {endpoint} returned non-JSON response: "
                        f"http_status={response.status_code}, "
                        f"content_type={content_type or 'missing'}, "
                        f"body={body_preview!r}"
                    ) from exc
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"AliceBlue {endpoint} failed: "
                        f"http_status={response.status_code}, response={result}"
                    )
                return result
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(attempt)
        raise RuntimeError(
            f"AliceBlue websocket session {action} failed after 3 attempts: "
            f"{last_error}"
        )

    def start_websocket(self, **kwargs):
        session_id = self._resolve_session_token()
        if not session_id:
            raise RuntimeError("AliceBlue websocket session token is missing")

        self.trade.invalid_sess = (
            lambda token: self._aliceblue_websocket_session_request(
                "invalidate", token
            )
        )
        self.trade.createSession = (
            lambda token: self._aliceblue_websocket_session_request(
                "create", token
            )
        )

        opened = threading.Event()
        original_open_callback = kwargs.get("socket_open_callback")

        def verified_open_callback():
            opened.set()
            if original_open_callback:
                original_open_callback()

        kwargs["socket_open_callback"] = verified_open_callback
        result = self.trade.start_websocket(**kwargs)
        if kwargs.get("run_in_background") and not opened.wait(
            int(os.getenv("SSLAGO_ALICEBLUE_WEBSOCKET_TIMEOUT_SECONDS", "15"))
        ):
            raise RuntimeError(
                "AliceBlue websocket did not open before the startup timeout"
            )
        return result

    def stop_websocket(self):
        return self.trade.stop_websocket()

    def subscribe(self, instruments):
        return self.trade.subscribe(instruments)

    def get_balance(self):
        funds = self.trade.get_funds()
        if isinstance(funds, list):
            return [self._ensure_net_key(item) for item in funds]
        if not isinstance(funds, dict):
            return [{'net': funds}]

        candidate = funds.get('result') or funds.get('data') or funds.get('funds')
        if isinstance(candidate, list):
            return [self._ensure_net_key(item) for item in candidate]
        if isinstance(candidate, dict):
            return [self._ensure_net_key(candidate)]
        return [self._ensure_net_key(funds)]

    @staticmethod
    def _ensure_net_key(item):
        if not isinstance(item, dict) or 'net' in item:
            return item
        for key in (
            'availableBalance', 'available_balance', 'availableCash', 'available_cash',
            'availableMargin', 'available_margin', 'netAvailable', 'net_available',
            'cash', 'balance'
        ):
            if key in item:
                normalized = dict(item)
                normalized['net'] = normalized[key]
                return normalized
        return item

    def place_order(
        self, transaction_type, instrument, quantity, order_type, product_type,
        price=0.0, trigger_price=None, stop_loss=None, square_off=None,
        trailing_sl=None, is_amo=False, order_tag=None, **kwargs
    ):
        transaction_type = self._enum_value(transaction_type)
        order_type = self.ORDER_TYPE_MAP.get(str(self._enum_value(order_type)).upper(), self._enum_value(order_type))
        product_type = self.PRODUCT_TYPE_MAP.get(str(self._enum_value(product_type)).upper(), self._enum_value(product_type))
        order_complexity = 'AMO' if is_amo else 'REGULAR'

        instrument_kwargs = {}
        if AntA3Instrument is not None and isinstance(instrument, AntA3Instrument):
            instrument_kwargs = {'instrument': instrument}
        elif isinstance(instrument, dict):
            instrument_kwargs = {
                'instrumentId': instrument.get('token') or instrument.get('instrumentId') or instrument.get('instrument_id'),
                'exchange': instrument.get('exchange'),
            }
        elif hasattr(instrument, 'token'):
            instrument_kwargs = {
                'instrumentId': getattr(instrument, 'token'),
                'exchange': getattr(instrument, 'exchange', None),
            }

        if order_type == 'MARKET':
            price = ''

        return self.trade.placeOrder(
            transactionType=transaction_type,
            quantity=quantity,
            orderComplexity=order_complexity,
            product=product_type,
            orderType=order_type,
            price=self._blank_if_none(price),
            slTriggerPrice=self._blank_if_none(trigger_price),
            slLegPrice=self._blank_if_none(stop_loss),
            targetLegPrice=self._blank_if_none(square_off),
            validity='DAY',
            trailingSlAmount=self._blank_if_none(trailing_sl),
            disclosedQuantity='',
            marketProtectionPercent='',
            apiOrderSource='',
            algoId='',
            orderTag=order_tag or '',
            **instrument_kwargs
        )

class ShoonyaApiPy(NorenApi):
    def __init__(self):
        NorenApi.__init__(self, host='https://api.shoonya.com/NorenWClientTP/', websocket='wss://api.shoonya.com/NorenWSTP/')
        global api
        api = self




class Exchange:
    def __init__(self, api,db,cred,reapi,sessionusertoken):
        trading_event("strategy_engine_stage", force=True, stage="constructor_started")
        self._shutdown_event = threading.Event()
        atexit.register(self._shutdown_event.set)
        self.cred=cred
        self.reapi=reapi
        self.db=db
        self.real=False
        self.deltasym={}
        self.fyers = {}
        self.zerodha = {}
        self.dhan={}
        self.smc = {}
        # In the __init__ method, where other broker dictionaries are initialized
        self.mofs = {}
        self.angelone = {}
        self.alice=dict()
        self.shoonya=dict()
        self.mstock = {}
        self.testmode=False
        self._debug_last_feed_log = 0
        self._debug_strategy_eval_log_times = {}
        self._debug_legacy_login_log_times = {}
        self._debug_decision_log_state = {}
        self._price_unavailable_log_times = {}
        self.sessionusertoken=sessionusertoken
        trading_event("strategy_engine_stage", force=True, stage="remote_instrument_masters")
        self.tokdf = pd.DataFrame(columns=['symbolCode'])
        self.upstoxsymbolmaster = pd.DataFrame()
        self.samlist = []
        self.kiteSymboldf = pd.DataFrame(
            columns=['exchange', 'exchange_token', 'tradingsymbol']
        )
        fyers_columns = ['exchangeName', 'exToken', 'exSymName']
        self.Fyers_NSE = pd.DataFrame(columns=fyers_columns)
        self.Fyers_BSE = pd.DataFrame(columns=fyers_columns)
        self.Fyers_MCX = pd.DataFrame(columns=fyers_columns)
        self.angelone_scripts = pd.DataFrame(
            columns=['exch_seg', 'token', 'symbol']
        )

        if _env_bool("SSLAGO_LOAD_OPTIONAL_BROKER_MASTERS", False):
            trading_event(
                "strategy_engine_stage",
                force=True,
                stage="optional_broker_masters_enabled",
            )
            self.kiteSymboldf = pd.read_csv('https://api.kite.trade/instruments')
            self.Fyers_NSE = pd.concat([
                pd.read_json(url).T
                for url in (
                    'https://public.fyers.in/sym_details/NSE_FO_sym_master.json',
                    'https://public.fyers.in/sym_details/NSE_CM_sym_master.json',
                )
            ], ignore_index=True)
            self.Fyers_BSE = pd.concat([
                pd.read_json(url).T
                for url in (
                    'https://public.fyers.in/sym_details/BSE_FO_sym_master.json',
                    'https://public.fyers.in/sym_details/BSE_CM_sym_master.json',
                )
            ], ignore_index=True)
            self.Fyers_MCX = pd.read_json(
                'https://public.fyers.in/sym_details/MCX_COM_sym_master.json'
            ).T
            self.angelone_scripts = pd.read_json(
                'https://margincalculator.angelbroking.com/'
                'OpenAPI_File/files/OpenAPIScripMaster.json'
            )
        else:
            trading_event(
                "strategy_engine_stage",
                force=True,
                stage="optional_broker_masters_skipped",
                reason="aliceblue_first_startup",
            )
        trading_event("strategy_engine_stage", force=True, stage="local_contract_masters")
        self.orders_collection = self.db["orders"]
        self.users_collection = self.db["users"]
        self.apis_collection = self.db["apis"]
        self.positions_collection = self.db["positions"]
        self.strategy_collection = self.db["strategies"]
        self.eqstrategy_collection = self.db["eqstrategies"]
        self.opositions_collection=self.db['Opositions']
        self.epositions_collection=self.db['Epositions']
        self.history_collection = self.db["historical"]
        self.payreceipt_collection=self.db['payreceipt']
        self.subscriptionperiod_collection=self.db['subscriptionperiod']
        self.admincontrol_collection=self.db['admincontrol']
        self.stockstoday_collection=self.db['stocktoday']
        self.strategyinput_collection=self.db['strategyinput']
        self.broker_collection=self.db['broker']
        self.topbottomlist=False
        self.equitytime=0
        self.dupper={}
        self.eqlastupdate=0
        self.fibsdf={}
        self.userstockcount={}
        self.userstocklist={}
        self.currentstocklist=[]
        self.newsignalstocklist=[]
        self.equitytransformer={}
        self.inverseequitytransformer={}
        self.topbottomsymbol=[]
        self.topbottombuylist=[]
        self.topbottomselllist=[]
        self.atmstrikelist={}
        self.breakoutexitsell={}
        self.fractalbreakout={}
        self.fractalbreakoutsell={}
        self.timestamp=0
        self.dataframe1={}
        self.dataframe2={}
        #self.samcolist=pd.read_csv('https://developers.stocknote.com/doc/ScripMaster.csv')
        #print(self.samcolist)
        self.Nse, self.Cds, self.Mcx, self.Nfo ,self.Bse,self.Bfo= self.contracts()
        # Alice Blue contract masters are loaded on demand; eager preload here
        # breaks startup on newer SDK versions and is not required because
        # contracts() already hydrates exchange data independently.
        self.testalice = None
        ##self.NfoAB=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/NFO.csv')
        #self.NseAB, self.CdsAB, self.McxAB, self.NfoAB = self._ABcontracts()
        self.symbols = ['BANKNIFTY', 'NIFTY', 'CRUDEOIL']
        self.Nselist = list(self.Nse['Symbol'].unique())
        self.Nfolist = list(self.Nfo['Symbol'].unique())
        self.Cdslist = list(self.Cds['Symbol'].unique())
        self.Mcxlist = list(self.Mcx['Symbol'].unique())
        self.Bselist = list(self.Bse['Symbol'].unique())
        self.Bfolist = list(self.Bfo['Symbol'].unique())
        #self.ws=None
        self.sprices = {}
        self.ws = None
        self.session_token = None

        self.date=str(datetime.datetime.now().date())
        self.symbols.extend(list(self.Mcx['TradingSymbol']))
        self.symbols.extend(list(self.Nse['TradingSymbol']))
        self.symbols.extend(list(self.Nfo['TradingSymbol']))
        self.symbols.extend(list(self.Cds['TradingSymbol']))
        self.symbols.extend(list(self.Bse['TradingSymbol']))
        self.symbols.extend(list(self.Bfo['TradingSymbol']))
        self.timeswitch={'1m':'1','2m':'2','3m':'3','5m':'5','10m':'10','15m':'15','30m':'30','75m':'75','1h':'60','2h':'120'}
        self.candleswitch={'1m':500,'2m':500,'3m':500,'5m':1000,'10m':2000,'15m':2000,'30m':5000,'1h':6000,'2h':10000}
        self.controls={'BANKNIFTY':self.admincontrol_collection.find_one({'symbol':"BANKNIFTY"}),
        'NIFTY':self.admincontrol_collection.find_one({'symbol':"NIFTY"}),
        'FINNIFTY':self.admincontrol_collection.find_one({'symbol':"FINNIFTY"}),
        'MIDCPNIFTY':self.admincontrol_collection.find_one({'symbol':"MIDCPNIFTY"}) 
        ,
        'SENSEX':self.admincontrol_collection.find_one({'symbol':"SENSEX"}) }
        
        self.strategyinputs={'EMA':self.strategyinput_collection.find_one({'strategy':"EMA"}),
        'SSALGO':self.strategyinput_collection.find_one({'strategy':"SSALGO"}),
        'SSAUTO':self.strategyinput_collection.find_one({'strategy':"SSAUTO"}),
        'PEMA':self.strategyinput_collection.find_one({'strategy':"PEMA"}),
        'SSEQUITYFNO':self.strategyinput_collection.find_one({'strategy':"SSEQUITYFNO"}),
        'RF':self.strategyinput_collection.find_one({'strategy':"RF"}),
        'EQSSALGO':self.strategyinput_collection.find_one({'strategy':"EQSSALGO"}),
        }
        
        self.breakoutexit={}
        self.dataframes={
        'BANKNIFTY':[],
        'NIFTY':[],
        'SENSEX':[],
        'CRUDEOIL':[],
        'FINNIFTY':[],
        'MIDCPNIFTY':[],

        'BANKNIFTY-I':[],

        'NIFTY-I':[],
        'FINNIFTY-I':[],
        'MIDCPNIFTY-I':[]
        ,
        'SENSEX-I':[]
        ,'BANKNIFTY-II':[],
        'NIFTY-II':[],
        'FINNIFTY-II':[],
        'MIDCPNIFTY-II':[]
        ,
        'SENSEX-II':[]
        ,'BANKNIFTY-III':[],
        'NIFTY-III':[],
        'FINNIFTY-III':[],
        'MIDCPNIFTY-III':[]
        ,
        'SENSEX-III':[]

        }
        self.dataframes1m=dict()
        self.dataframes2m=dict()
        self.dataframes3m=dict()
        #self.dataframes4m=dict()
        self.dataframes5m=dict()
        self.dataframes10m=dict()
        self.dataframes15m=dict()
        self.dataframes30m=dict()
        self.dataframes1h=dict()
        self.dataframes2h=dict()
        self.dataframes4h=dict()
        self.dataframes1d={}
        #self.dataframes1w=dict()
        self.allpos={}
        self.userloggedin=list()
        self.usernotloggedin=list()
        self.alice=dict()
        self.ordersids=[]
        self.tank = []
        self.prices = dict()
        self.market_depths = {}
        self.market_depth_times = {}
        self.market_depth_max_age_seconds = 3
        self.order_push_ticks = 2
        self.order_push_tick_size = 0.05
        self.order_depth_price_push = 1.0
        self.last_order_price_context = {}
        self.aliceblue_market_depth_enabled = True
        self.aliceblue_depth_started = set()
        self.aliceblue_depth_starting = set()
        self.recent_broker_order_keys = {}
        self.broker_order_duplicate_window_seconds = 30
        self.candles1m = dict()
        self.candles15m = dict()
        self.mindata = dict()
        self.loadedwatchsymbols = []
        self.oistrikelvldata = {}
        self.symbols_tok = {'BSE|1':'SENSEX','NSE|26037':'FINNIFTY','NSE|26009': 'BANKNIFTY','NSE|26074':'MIDCPNIFTY',
                            'NSE|26000': 'NIFTY', self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']: 'CRUDEOIL',
                            self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[0]['FToken']:'SENSEX-I',
                            self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[1]['FToken']:'SENSEX-II'



                            , self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOILM') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']: 'CRUDEOILM'
                            , self.Mcx[(self.Mcx['Symbol'] == 'SILVERM') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']: 'SILVERM'

                            , self.Mcx[(self.Mcx['Symbol'] == 'SILVERMIC') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']: 'SILVERMIC'

                            ,self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'NIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'NIFTY-II'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'BANKNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'BANKNIFTY-II'

                            ,self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'FINNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'FINNIFTY-II'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'MIDCPNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'MIDCPNIFTY-II'
                            }
        print(self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')])
        self.tok_symbols = {'SENSEX':'BSE|1','FINNIFTY':'NSE|26037','BANKNIFTY': 'NSE|26009','MIDCPNIFTY':'NSE|26074', 


        'NIFTY': 'NSE|26000', "CRUDEOIL": self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & (
            self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken'],
        'NIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']
        ,
        'NIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']
        ,
        'BANKNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken'],
        'BANKNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken'],
        'FINNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken'],
        'FINNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken'],
        'MIDCPNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken'],
        'MIDCPNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken'],
        'SENSEX-I':self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[0]['FToken'],
        'SENSEX-II':self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[1]['FToken']

        ,'CRUDEOILM': self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOILM') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']
        , 'SILVERM':self.Mcx[(self.Mcx['Symbol'] == 'SILVERM') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']

        , 'SILVERMIC':self.Mcx[(self.Mcx['Symbol'] == 'SILVERMIC') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']


        }
        self.upstoxtok_symbols = {
        'SENSEX':'BSE_INDEX|SENSEX','FINNIFTY':'NSE_INDEX|Nifty Fin Service','BANKNIFTY': 'NSE_INDEX|Nifty Bank','MIDCPNIFTY':'NSE_INDEX|NIFTY MIDCAP 150', 'NIFTY': 'NSE_INDEX|Nifty 50', 
        "CRUDEOIL": self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & ( self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['UToken'],
        'NIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken']
        ,
        'NIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken']
        ,
        'BANKNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken'],
        'BANKNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken'],
        'FINNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken'],
        'FINNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken'],
        'MIDCPNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken'],
        'MIDCPNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken']
        
                                    ,'CRUDEOILM': self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOILM') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['UToken']
                            , 'SILVERM':self.Mcx[(self.Mcx['Symbol'] == 'SILVERM') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['UToken']

                            , 'SILVERMIC':self.Mcx[(self.Mcx['Symbol'] == 'SILVERMIC') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['UToken'],
        
                        'SENSEX-I':self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Bfo['OptionType'] == 'XX')].iloc[0]['UToken'], 

                        'SENSEX-II':self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Bfo['OptionType'] == 'XX')].iloc[1]['UToken']

        }
        self.upstoxsymbols_tok = {
                    'NSE_INDEX|Nifty Fin Service': 'FINNIFTY',
                    'NSE_INDEX|Nifty Bank': 'BANKNIFTY',
                    'NSE_INDEX|NIFTY MIDCAP 150': 'MIDCPNIFTY',
                    'NSE_INDEX|Nifty 50': 'NIFTY',
                    'BSE_INDEX|SENSEX': 'SENSEX',
                    self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Mcx['OptionType'] == 'XX')].iloc[0]['UToken']: "CRUDEOIL",
                    self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken']: 'NIFTY-I',
                    self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken']: 'NIFTY-II',
                    self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken']: 'BANKNIFTY-I',
                    self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken']: 'BANKNIFTY-II',
                    self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken']: 'FINNIFTY-I',
                    self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken']: 'FINNIFTY-II',
                    self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[0]['UToken']: 'MIDCPNIFTY-I',
                    self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Nfo['OptionType'] == 'XX')].iloc[1]['UToken']: 'MIDCPNIFTY-II',

                    self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Bfo['OptionType'] == 'XX')].iloc[0]['UToken']: 'SENSEX-I',
                    self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & 
                             (self.Bfo['OptionType'] == 'XX')].iloc[1]['UToken']: 'SENSEX-II'
                }
        
        self.updatelist=True
        self.symbols_expiry = {}
        self.SYMBOLDICT = dict()
        self.oi_data = {'BANKNIFTY': 0, 'NIFTY': 0, 'USDINR': 0}
        #self.subscribe_list = ['NSE|26037','NSE|26074','NSE|26000', 'NSE|26009', self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & ( self.Mcx['Expiry_'] > datetime.datetime.now()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']]
        self.sublist=list(self.symbols_tok.keys())
        self.subscribe_list = []
        self.subscribe_list.extend(self.sublist)

        self.subscribe_slist=[
        ]
        self.stok_symbols = {"CRUDEOIL": self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['SToken'],
        'NIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken'],
        'NIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken'],
        'BANKNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken'],
        'BANKNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken'],
        'FINNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken'],
        'FINNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken'],
        'MIDCPNIFTY-I':self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken'],
        'MIDCPNIFTY-II':self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken'],

        'SENSEX-I':self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[0]['SToken'],
        'SENSEX-II':self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[1]['SToken'],
        
        }
        self.symbols_stok = {

        self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['SToken']: 'CRUDEOIL'

                            ,self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken']:'NIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken']:'NIFTY-II'

                            ,self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken']:'BANKNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken']:'BANKNIFTY-II'


                            ,self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken']:'FINNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken']:'FINNIFTY-II'

                            ,self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['SToken']:'MIDCPNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken']:'MIDCPNIFTY-II',

       

        self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[0]['SToken']:'SENSEX-I',
        self.Bfo[(self.Bfo['Symbol'] == 'SENSEX') & (self.Bfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Bfo['OptionType'] == 'XX')].iloc[1]['SToken']:'SENSEX-II',
        }

        self.Mcxlist=list(self.Mcx['Symbol'].unique())
        self.subscribe_slist = []
        self.subslist=list(self.symbols_stok.keys())
        self.subscribe_slist.extend(self.subslist)
        #for i in self.Mcxlist:
            #self.stok_symbols[i]=str(self.Mcx[(self.Mcx['Symbol'] == i) & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()+datetime.timedelta(days=9)) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['SToken'])
            #self.symbols_stok[self.stok_symbols[i]]=i
            #self.tok_symbols[i]=str(self.Mcx[(self.Mcx['Symbol'] == i) & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()+datetime.timedelta(days=9)) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken'])
            #self.symbols_tok[self.tok_symbols[i]]=i
            #self.upstoxtok_symbols[i]=str(self.Mcx[(self.Mcx['Symbol'] == i) & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()+datetime.timedelta(days=9)) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['UToken'])
            #self.upstoxsymbols_tok[self.upstoxtok_symbols[i]]=i
        self.tenstrikes, self.fivestrikes, self.eodstrikes, self.all_oi = {}, {}, {}, {}
        self.Markettime = {
            'NSE': {'start': datetime.time(9, 15, 1, 0), 'end': datetime.time(15, 25, 0, 0)},
            'NFO': {'start': datetime.time(9, 15, 1, 0), 'end': datetime.time(15, 25, 0, 0)},
            'CDS': {'start': datetime.time(9, 0, 1, 0), 'end': datetime.time(16, 55, 0, 0)},
            'MCX': {'start': datetime.time(9, 0, 1, 0), 'end': datetime.time(23, 55, 0, 0)},
        }
        self.stocksunfil=list(self.Nfo[(self.Nfo['OptionType']=='XX') & (self.Nfo['Instrument']=='FUTSTK')]['Symbol'].unique())
        self.nsestocksunfil=[]
        self.stocks=[]
        for i in self.stocksunfil:
            if 'NSETEST' not in i:
                self.stocks.append(i)
        for i in self.stocks:
            self.nsestocksunfil.append(self.Nse[self.Nse['Symbol']==i].to_dict('records')[0])
        #self.nsestocksunfil=pd.DataFrame(self.nsestocksunfil)
        self.websocketretry=0
        self.lastoiupdate = {}
        self.api = api
        self.lastupdate=0
        self.lastupdates={}
        self.feed_opened = False
    
        if self.api is not None:
            trading_event("strategy_engine_stage", force=True, stage="shoonya_websocket_start")
            self.api.start_websocket(
                order_update_callback=self.event_handler_order_update,
                subscribe_callback=self.event_handler_feed_update,
                socket_open_callback=self.open_callback,
                socket_close_callback=self.close_callback,
                socket_error_callback=self.error_callback,
            )
            self.api.subscribe(self.subscribe_list)
        else:
            trading_event(
                "strategy_engine_stage",
                force=True,
                stage="shoonya_websocket_skipped",
                reason="not_configured",
            )
    
        

        self.oi_ = []
        # self.alloi()

        # self.subscribe_list=['NSE|26000','NSE|26009']

        self.strategies: typing.Dict[int,
                                     typing.Union[TechnicalStrategy, BreakoutStrategy]] = dict()
        self.levelbasedstrats: typing.Dict[int,
                                           typing.Union[HuntLevel, HuntLevel]] = dict()
        self.oibasedstrats: typing.Dict[int,
                                        typing.Union[OILevel, OILevel]] = dict()
        self.breakoutstrats: typing.Dict[int,
                                           typing.Union[HuntLevel, HuntLevel]]=dict()
        self.trades = []
        self.fakeorders={}
        self.positions={}
        self.closedpositions={}
        self.positionsids=[]
        self.ostrategies=[]
        self.marketdays=8
        
        self.logs = []
        self.reconnect = True

        
        trading_event("strategy_engine_stage", force=True, stage="initial_candle_load")
        self.hist('BANKNIFTY', tf="1",initial=True)
        self.hist('NIFTY', tf="1",initial=True)
        self.hist('SENSEX', tf="1",initial=True)
        self.hist('FINNIFTY', tf="1",initial=True)
        self.hist('MIDCPNIFTY', tf="1",initial=True)
        self.hist('NIFTY-I', tf="1",initial=True)
        self.hist('FINNIFTY-I', tf="1",initial=True)
        self.hist('MIDCPNIFTY-I', tf="1",initial=True)
        self.hist('BANKNIFTY-II', tf="1",initial=True)
        self.hist('NIFTY-II', tf="1",initial=True)
        self.hist('FINNIFTY-II', tf="1",initial=True)
        self.hist('MIDCPNIFTY-II', tf="1",initial=True)
        #self.hist('CRUDEOIL', tf="1",initial=True)
        self.dbfile={}
        try:
            with open('db.pkl', 'rb') as file:
                #with open('2023-10-22_NIFTY MIDCAP 150.pkl'.format(datetime.date.today(),option), 'rb') as file:
                self.dbfile = pickle.load(file)
            # Do something with loaded_data
        except FileNotFoundError:
            print("File not found.")
        except EOFError:
            print(f"EOFError while loading data from ")
        except Exception as e:
            print(f"An error occurred: {e}")
        trading_event("strategy_engine_stage", force=True, stage="thread_start")
        self._load_open_option_watchlist()
        self._start_all_threads()
        trading_event(
            "strategy_engine_started",
            force=True,
            database=getattr(db, "name", ""),
            test_mode=self.testmode,
        )
        # ============== Helper Methods ==============

    def _load_open_option_watchlist(self):
        try:
            open_positions = self.opositions_collection.find(
                {'status': 'open'},
                {'optionname': 1, 'symbol': 1}
            )
            for position in open_positions:
                optionname = position.get('optionname')
                symbol = position.get('symbol')
                if optionname:
                    self.add_symbol_to_websocket(optionname)
                if symbol:
                    self.add_symbol_to_websocket(symbol)
        except Exception as e:
            print(f"open position websocket restore failed: {e}")

    def _get_active_users(self):
        """Get list of users with active subscriptions"""
        dd = pd.DataFrame(list(self.subscriptionperiod_collection.find()))
        if dd.empty or 'user' not in dd.columns or 'end' not in dd.columns:
            return []
        dd['end'] = pd.to_datetime(dd['end'])
        dd['result'] = dd['end'] >= pd.to_datetime(datetime.datetime.now())
        return list(dd[dd['result'] == True]['user'])

    def _get_broker_users(self, broker_name, active_users=None):
        """Get API credentials for users of a specific broker"""
        if active_users is None:
            active_users = self._get_active_users()
        execution_active_users = self._get_execution_active_users()
        
        users = []
        for user in active_users:
            if user not in execution_active_users:
                continue
            try:
                broker_info = self.broker_collection.find_one({'user': user})
                if broker_info and broker_info.get('selectedbroker') == broker_name:
                    api_info = self.apis_collection.find_one({'user': user, 'broker': broker_name})
                    if api_info:
                        users.append(dict(api_info))
            except Exception as e:
                print(f"Error getting {broker_name} user info for {user}: {str(e)}")
        return users

    def _get_execution_active_users(self):
        """Users who need a live broker session for startup or position management."""
        try:
            query = {
                'live': True,
                '$or': [
                    {'status': {'$in': ['opened', 'paused']}},
                    {'position': 'in'},
                ],
            }
            return {
                row.get('user')
                for row in self.strategy_collection.find(query, {'user': 1})
                if row.get('user')
            }
        except Exception as e:
            print(f"Error getting execution-active users: {e}")
            return set()

    def _get_non_logged_broker_users(self, broker_name):
        """Get users of a broker who are not currently logged in"""
        items = list(self.apis_collection.find({
            'user': {'$nin': self.userloggedin},
            'broker': broker_name
        }))
        self.userloggedin = list(set(self.userloggedin))
        active_users = self._get_active_users()
        execution_active_users = self._get_execution_active_users()
        
        users = []
        for i in items:
            if i['user'] in active_users and i['user'] in execution_active_users:
                j = self.broker_collection.find_one({'user': i['user']})
                if j and j.get('selectedbroker') == broker_name:
                    api_info = self.apis_collection.find_one({'user': i['user'], 'broker': broker_name})
                    if api_info:
                        users.append(dict(api_info))
        return users

    def _update_user_login_state(self, user_id, instance, session_data, broker_dict, success_key):
        """Update user login state based on session data"""
        if instance and session_data and success_key in session_data:
            if not hasattr(self, broker_dict):
                setattr(self, broker_dict, {})
            getattr(self, broker_dict)[user_id] = instance
            if user_id not in self.userloggedin:
                self.userloggedin.append(user_id)
            if user_id in self.usernotloggedin:
                self.usernotloggedin.remove(user_id)
            if broker_dict == 'alice':
                trading_event(
                    "legacy_broker_login_result",
                    force=True,
                    user=user_id,
                    broker="aliceblue",
                    status="connected",
                )
                try:
                    self._ensure_aliceblue_market_depth(user_id)
                except Exception as e:
                    print(f"AliceBlue market depth skipped for {user_id}: {e}")
        else:
            log_key = f"{broker_dict}:{user_id}:rejected"
            now = time.monotonic()
            interval = max(
                1,
                int(os.getenv("DEBUG_TRADING_LOGIN_INTERVAL_SECONDS", "60")),
            )
            if now - self._debug_legacy_login_log_times.get(log_key, 0) >= interval:
                self._debug_legacy_login_log_times[log_key] = now
                trading_event(
                    "legacy_broker_login_result",
                    force=True,
                    user=user_id,
                    broker="aliceblue" if broker_dict == "alice" else broker_dict,
                    status="rejected",
                    reason="missing_or_invalid_session",
                )
            if user_id not in self.usernotloggedin:
                self.usernotloggedin.append(user_id)

    def _save_session_to_db(self, collection_name, filter_key, filter_value, session_data):
        """Save session data to database"""
        getattr(self.db, collection_name).update_one(
            {filter_key: filter_value},
            {
                '$set': {
                    'date': str(datetime.datetime.now().date()),
                    **session_data
                }
            },
            upsert=True
        )

    def _get_existing_session(self, collection_name, filter_key, filter_value):
        """Get existing session from database"""
        return getattr(self.db, collection_name).find_one({
            filter_key: filter_value,
            'date': str(datetime.datetime.now().date())
        })

    def _ensure_collection_exists(self, collection_name):
        """Ensure database collection exists"""
        if collection_name not in self.db.list_collection_names():
            self.db.create_collection(collection_name)

    # ============== Broker-Specific Login Handlers ==============

    def _should_suppress_aliceblue_login_warning(self, user):
        return str(user or '').strip().lower() in {'sravani'}

    def _aliceblue_saved_session(self, item):
        for key in ('user_session', 'sessionID', 'session_id', 'sessionid', 'userSession'):
            value = item.get(key)
            if value:
                return decrypt_secret(value)
        return None

    @staticmethod
    def _aliceblue_profile_is_valid(response):
        if not isinstance(response, dict):
            return False

        status = str(
            response.get('status')
            or response.get('Status')
            or response.get('stat')
            or ''
        ).strip().lower()
        message = ' '.join(
            str(response.get(key) or '')
            for key in (
                'message', 'Message', 'remarks', 'emsg', 'error', 'Error'
            )
        ).lower()
        if status in {'not_ok', 'not ok', 'failed', 'failure', 'rejected'}:
            return False
        if any(
            token in message
            for token in (
                'unauthor',
                'invalid session',
                'session expired',
                'token expired',
            )
        ):
            return False
        if status:
            return status in {'ok', 'success', 'connected'}
        return bool(response.get('result') or response.get('data'))

    def _refresh_aliceblue_auth(self, item):
        """Regenerate AliceBlue auth and session without browser automation."""
        user = item.get('user')
        if not user:
            return None

        latest_item = self.apis_collection.find_one({'user': user, 'broker': 'aliceblue'})
        if latest_item:
            item = dict(latest_item)

        decrypted = decrypt_secret_fields(dict(item), SECRET_FIELD_NAMES)
        user_id = str(decrypted.get('apikey') or '').strip()
        auth_code = str(decrypted.get('auth_code') or '').strip()
        secret_key = str(decrypted.get('apisecret') or '').strip()
        session_api_missing = [
            name
            for name, value in (
                ('apikey', user_id),
                ('auth_code', auth_code),
                ('apisecret', secret_key),
            )
            if not value
        ]
        if not session_api_missing:
            try:
                alice = AliceBlueTradeHubAdapter(
                    user_id=user_id,
                    auth_code=auth_code,
                    secret_key=secret_key,
                )
                session = alice.get_session_id()
                session_value = None
                if isinstance(session, dict):
                    session_value = (
                        session.get('sessionID')
                        or session.get('userSession')
                    )
                profile = alice.get_profile() if session_value else None
                if session_value and self._aliceblue_profile_is_valid(profile):
                    encrypted_session = encrypt_secret(session_value)
                    self.apis_collection.update_one(
                        {'user': user, 'broker': 'aliceblue'},
                        {
                            '$set': {
                                'user_session': encrypted_session,
                                'sessionID': encrypted_session,
                                'session_date': str(datetime.datetime.now().date()),
                            }
                        },
                        upsert=True,
                    )
                    self.db["broker_health"].update_one(
                        {"user": user, "broker": "aliceblue"},
                        {
                            "$set": {
                                "login_status": "connected",
                                "last_error": "",
                                "updated_at": datetime.datetime.utcnow(),
                            }
                        },
                        upsert=True,
                    )
                    print(
                        f"AliceBlue API session refresh completed for {user}"
                    )
                    return dict(
                        self.apis_collection.find_one(
                            {'user': user, 'broker': 'aliceblue'}
                        )
                        or {}
                    )
                print(
                    f"AliceBlue saved auth_code was rejected for {user}"
                )
            except Exception as e:
                print(
                    f"AliceBlue saved auth_code refresh error for {user}: "
                    f"{type(e).__name__}"
                )

        direct_values = {
            'user_id': user_id,
            'password': (
                decrypted.get('alice_password')
                or decrypted.get('password')
                or decrypted.get('pwd')
            ),
            'totp_secret': decrypted.get('totp_key'),
            'app_code': decrypted.get('app_key'),
            'app_secret': secret_key,
        }
        direct_missing = [
            name
            for name, value in direct_values.items()
            if not str(value or '').strip()
        ]
        if direct_missing:
            error_message = (
                "AliceBlue automatic login is missing "
                + ", ".join(direct_missing)
            )
        else:
            try:
                result = AliceBlueDirectAuthenticator().authenticate(
                    **direct_values
                )
                refreshed_auth_code = result['auth_code']
                refreshed_session = result['session_id']
                alice = AliceBlueTradeHubAdapter(
                    user_id=user_id,
                    auth_code=refreshed_auth_code,
                    secret_key=secret_key,
                    session_id=refreshed_session,
                )
                alice.get_session_id(session_id=refreshed_session)
                profile = alice.get_profile()
                if not self._aliceblue_profile_is_valid(profile):
                    raise AliceBlueDirectAuthError(
                        "AliceBlue profile validation rejected the new session"
                    )

                encrypted_session = encrypt_secret(refreshed_session)
                self.apis_collection.update_one(
                    {'user': user, 'broker': 'aliceblue'},
                    {
                        '$set': {
                            'auth_code': encrypt_secret(refreshed_auth_code),
                            'user_session': encrypted_session,
                            'sessionID': encrypted_session,
                            'session_date': str(datetime.datetime.now().date()),
                        }
                    },
                    upsert=True,
                )
                self.db["broker_health"].update_one(
                    {"user": user, "broker": "aliceblue"},
                    {
                        "$set": {
                            "login_status": "connected",
                            "last_error": "",
                            "updated_at": datetime.datetime.utcnow(),
                        }
                    },
                    upsert=True,
                )
                print(f"AliceBlue automatic login completed for {user}")
                return dict(
                    self.apis_collection.find_one(
                        {'user': user, 'broker': 'aliceblue'}
                    )
                    or {}
                )
            except AliceBlueDirectAuthError as e:
                error_message = str(e)
            except Exception as e:
                error_message = (
                    "AliceBlue automatic login failed: "
                    f"{type(e).__name__}"
                )

        self.db["broker_health"].update_one(
            {"user": user, "broker": "aliceblue"},
            {
                "$set": {
                    "login_status": "rejected",
                    "websocket_status": "disconnected",
                    "last_error": error_message,
                    "updated_at": datetime.datetime.utcnow(),
                }
            },
            upsert=True,
        )
        if not self._should_suppress_aliceblue_login_warning(user):
            print(f"AliceBlue automatic login rejected for {user}: {error_message}")
        return None

    def _login_aliceblue(self, item):
        """AliceBlue login handler"""
        try:
            item = decrypt_secret_fields(dict(item or {}), SECRET_FIELD_NAMES)
            for attempt in range(2):
                user_id = str(item.get('apikey', '')).strip()
                auth_code = str(item.get('auth_code', '')).strip()
                secret_key = str(item.get('apisecret', '')).strip()
                existing_session = self._aliceblue_saved_session(item)
                has_session = bool(str(existing_session or '').strip())

                missing_fields = [
                    field_name
                    for field_name, value in (
                        ('apikey', user_id),
                        ('apisecret', secret_key),
                    )
                    if not value
                ]
                if missing_fields:
                    if not self._should_suppress_aliceblue_login_warning(item.get('user')):
                        print(
                            f"AliceBlue login skipped for {item.get('user')}: "
                            f"missing {', '.join(missing_fields)}"
                        )
                    return item['user'], None, None

                if not auth_code and not has_session:
                    refreshed_item = self._refresh_aliceblue_auth(item)
                    if refreshed_item:
                        item = decrypt_secret_fields(
                            dict(refreshed_item), SECRET_FIELD_NAMES
                        )
                        continue
                    if not self._should_suppress_aliceblue_login_warning(item.get('user')):
                        print(
                            f"AliceBlue login skipped for {item.get('user')}: "
                            "missing auth_code and session"
                        )
                    return item['user'], None, None

                alice_instance = AliceBlueTradeHubAdapter(
                    user_id=user_id,
                    auth_code=auth_code,
                    secret_key=secret_key,
                    session_id=existing_session if has_session else None
                )
                session_id = (
                    alice_instance.get_session_id(session_id=existing_session)
                    if has_session
                    else alice_instance.get_session_id()
                )
                session_value = None
                if isinstance(session_id, dict):
                    session_value = (
                        session_id.get('sessionID')
                        or session_id.get('userSession')
                    )

                profile = None
                if session_value:
                    try:
                        profile = alice_instance.get_profile()
                    except Exception as profile_error:
                        profile = {
                            'stat': 'Not_ok',
                            'emsg': str(profile_error),
                        }

                if session_value and self._aliceblue_profile_is_valid(profile):
                    encrypted_session = encrypt_secret(session_value)
                    self.apis_collection.update_one(
                        {'user': item['user'], 'broker': 'aliceblue'},
                        {
                            '$set': {
                                'user_session': encrypted_session,
                                'sessionID': encrypted_session,
                                'session_date': str(datetime.datetime.now().date()),
                            }
                        },
                        upsert=True,
                    )
                    normalized_session = dict(session_id)
                    normalized_session['sessionID'] = session_value
                    return item['user'], alice_instance, normalized_session

                if attempt == 0:
                    refreshed_item = self._refresh_aliceblue_auth(item)
                    if refreshed_item:
                        item = decrypt_secret_fields(
                            dict(refreshed_item), SECRET_FIELD_NAMES
                        )
                        continue

                error_message = (
                    "AliceBlue profile validation rejected the saved session"
                )
                if isinstance(profile, dict):
                    error_message = str(
                        profile.get('message')
                        or profile.get('emsg')
                        or profile.get('error')
                        or error_message
                    )
                self.db["broker_health"].update_one(
                    {"user": item['user'], "broker": "aliceblue"},
                    {
                        "$set": {
                            "login_status": "rejected",
                            "websocket_status": "disconnected",
                            "last_error": error_message,
                            "updated_at": datetime.datetime.utcnow(),
                        }
                    },
                    upsert=True,
                )
                print(
                    f"AliceBlue login rejected for {item['user']}: "
                    f"{error_message}"
                )
                return item['user'], None, None
            return item['user'], None, None
        except Exception as e:
            print(f"AliceBlue login error for {item['user']}: {e}")
            trading_exception(
                "legacy_broker_login_error",
                e,
                user=item.get("user"),
                broker="aliceblue",
            )
            return item['user'], None, None

    def _login_shoonya(self, item):
        """Shoonya login handler"""
        try:
            self._ensure_collection_exists('shoonyaloginsess')
            z = self._get_existing_session('shoonyaloginsess', 'usr', item['usr'])
            
            api = ShoonyaApiPy()
            
            if z and 'session' in z:
                ret = api.set_session(userid=item['usr'], password=item['pwd'], usertoken=z['session'])
                j = api.get_quotes('NSE', 'Nifty 50')
                if j is not None:
                    return item['user'], api, {'susertoken': z['session']}
            
            ret = api.login(
                userid=item['usr'], password=item['pwd'],
                twoFA=str(pyotp.TOTP(item['factor2']).now()),
                vendor_code=item['usr']+'_U', api_secret=item['apikey'], imei='abc1234'
            )
            
            if 'susertoken' in ret:
                self._save_session_to_db('shoonyaloginsess', 'usr', item['usr'], {'session': ret['susertoken']})
                return item['user'], api, ret
            return item['user'], None, None
        except Exception as e:
            print(f"Shoonya login error for {item['user']}: {e}")
            return item['user'], None, None

    def _login_fyers(self, item):
        """Fyers login handler"""
        try:
            from fyers_apiv3 import fyersModel
            import base64
            from urllib.parse import parse_qs, urlparse
            
            self._ensure_collection_exists('fyersloginsess')
            z = self._get_existing_session('fyersloginsess', 'client_id', item['client_id'])
            
            if z and z.get('access_token'):
                fyers = fyersModel.FyersModel(
                    client_id=item['client_id'], is_async=False,
                    token=z['access_token'], log_path=os.getcwd()
                )
                profile = fyers.get_profile()
                if profile.get('code') == 200:
                    return item['user'], fyers, {'access_token': z['access_token']}
            
            def getEncodedString(string):
                base64_bytes = base64.b64encode(str(string).encode("ascii"))
                return base64_bytes.decode("ascii")
            
            session = fyersModel.SessionModel(
                client_id=item['client_id'], secret_key=item['secret_key'],
                redirect_uri=item['redirect_uri'], response_type="code",
                grant_type="authorization_code"
            )
            
            ses = requests.Session()
            res = requests.post(
                url="https://api-t2.fyers.in/vagator/v2/send_login_otp_v2",
                json={"fy_id": getEncodedString(item['fy_id']), "app_id": "2"}
            ).json()
            
            if datetime.datetime.now().second % 30 > 27:
                time.sleep(5)
            
            res2 = requests.post(
                url="https://api-t2.fyers.in/vagator/v2/verify_otp",
                json={"request_key": res["request_key"], "otp": pyotp.TOTP(item['totp_key']).now()}
            ).json()
            
            res3 = ses.post(
                url="https://api-t2.fyers.in/vagator/v2/verify_pin_v2",
                json={"request_key": res2["request_key"], "identity_type": "pin", "identifier": getEncodedString(item['pin'])}
            ).json()
            
            ses.headers.update({'authorization': f"Bearer {res3['data']['access_token']}"})
            
            res3 = ses.post(
                url="https://api-t1.fyers.in/api/v3/token",
                json={
                    "fyers_id": item['fy_id'], "app_id": item['client_id'][:-4],
                    "redirect_uri": item['redirect_uri'], "appType": "100",
                    "code_challenge": "", "state": "None", "scope": "",
                    "nonce": "", "response_type": "code", "create_cookie": True
                }
            ).json()
            
            auth_code = parse_qs(urlparse(res3['Url']).query)['auth_code'][0]
            session.set_token(auth_code)
            response = session.generate_token()
            access_token = response['access_token']
            
            fyers = fyersModel.FyersModel(
                client_id=item['client_id'], is_async=False,
                token=access_token, log_path=os.getcwd()
            )
            
            self._save_session_to_db('fyersloginsess', 'client_id', item['client_id'], {'access_token': access_token})
            return item['user'], fyers, {'access_token': access_token}
        except Exception as e:
            print(f"Fyers login error for {item['user']}: {e}")
            return item['user'], None, None

    def _login_angelone(self, item):
        """Angel One login handler"""
        try:
            from SmartApi import SmartConnect
            
            self._ensure_collection_exists('angeloneloginsess')
            smart_api = SmartConnect(api_key=item['apikey'])
            totp = pyotp.TOTP(item['totp_key']).now()
            
            session_data = smart_api.generateSession(item['client_id'], item['pwd'], totp)
            
            if not session_data.get('data'):
                raise Exception(f"Failed to generate session: {session_data}")
            
            jwt_token = session_data['data']['jwtToken']
            refresh_token = session_data['data']['refreshToken']
            feed_token = smart_api.getfeedToken()
            
            self._save_session_to_db('angeloneloginsess', 'client_id', item['client_id'], {
                'jwt_token': jwt_token, 'refresh_token': refresh_token, 'feed_token': feed_token
            })
            
            return item['user'], smart_api, {
                'jwt_token': jwt_token, 'refresh_token': refresh_token, 'feed_token': feed_token
            }
        except Exception as e:
            print(f"Angel One login error for {item['user']}: {e}")
            return item['user'], None, None

    def _login_dhan(self, item):
        """Dhan login handler (no session persistence needed)"""
        try:
            from dhanhq import dhanhq
            
            dhan_instance = dhanhq(item['client_id'], item['access_token'])
            fund_limits = dhan_instance.get_fund_limits()
            session_id = {'status': True} if fund_limits.get('status') == 'success' else {'status': False}
            
            return item['user'], dhan_instance, session_id
        except Exception as e:
            print(f"Dhan login error for {item['user']}: {e}")
            return item['user'], None, None

    def _login_zerodha(self, item):
        """Zerodha login handler"""
        try:
            from kiteconnect import KiteConnect
            from urllib.parse import urlparse, parse_qs
            
            self._ensure_collection_exists('zerodhaloginsess')
            kite = KiteConnect(api_key=item['api_key'])
            session = requests.Session()
            
            login_response = session.post(
                "https://kite.zerodha.com/api/login",
                {"user_id": item['user_id'], "password": item['password']}
            ).json()
            
            if 'data' not in login_response or 'request_id' not in login_response['data']:
                return item['user'], None, None
            
            request_id = login_response['data']['request_id']
            
            twofa_response = session.post(
                "https://kite.zerodha.com/api/twofa",
                {"user_id": item['user_id'], "request_id": request_id, "twofa_value": pyotp.TOTP(item['totp_key']).now()}
            ).json()
            
            if twofa_response.get('status') != 'success':
                return item['user'], None, None
            
            try:
                api_session = session.get(f"https://kite.trade/connect/login?api_key={item['api_key']}")
                parsed = urlparse(api_session.url)
            except Exception as e:
                parsed = urlparse(e.request.url)
            
            if 'request_token' not in parse_qs(parsed.query):
                return item['user'], None, None
            
            request_token = parse_qs(parsed.query)["request_token"][0]
            session_response = kite.generate_session(request_token, api_secret=item['api_secret'])
            
            if 'access_token' not in session_response:
                return item['user'], None, None
            
            access_token = session_response["access_token"]
            kite.set_access_token(access_token)
            
            self._save_session_to_db('zerodhaloginsess', 'user_id', item['user_id'], {'access_token': access_token})
            return item['user'], kite, {'access_token': access_token}
        except Exception as e:
            print(f"Zerodha login error for {item['user']}: {e}")
            return item['user'], None, None

    def _login_mofs(self, item):
        """MOFS login handler"""
        try:
            from MOFSLOPENAPI import MOFSLOPENAPI
            
            self._ensure_collection_exists('mofsloginsess')
            z = self._get_existing_session('mofsloginsess', 'client_id', item['client_id'])
            
            mofs_api = MOFSLOPENAPI(
                item['api_key'], "https://openapi.motilaloswal.com",
                item['client_id'], "Desktop", "chrome", "104"
            )
            
            if z and z.get('auth_token'):
                mofs_api.AuthToken = z['auth_token']
                try:
                    profile = mofs_api.GetProfile(item['client_id'])
                    if profile.get("status") == "SUCCESS":
                        return item['user'], mofs_api, {'auth_token': z['auth_token']}
                except Exception as e:
                    print(f"MOFS session expired: {e}")
            
            login_response = mofs_api.login(
                f_clientID=item['client_id'], f_password=item['password'],
                f_twoFA=item['_2_FA'], f_totp=pyotp.TOTP(item['totp_key']).now(),
                f_vendorinfo=item['client_id']
            )
            
            if login_response.get("status") == "SUCCESS":
                auth_token = login_response.get("AuthToken")
                self._save_session_to_db('mofsloginsess', 'client_id', item['client_id'], {'auth_token': auth_token})
                return item['user'], mofs_api, {'auth_token': auth_token}
            
            return item['user'], None, None
        except Exception as e:
            print(f"MOFS login error for {item['user']}: {e}")
            return item['user'], None, None

    def _login_smc(self, item):
        """SMC login handler"""
        try:
            from XTSConnect import XTSConnect
            
            self._ensure_collection_exists('smcloginsess')
            z = self._get_existing_session('smcloginsess', 'client_id', item['client_id'])
            
            smc = XTSConnect(
                apiKey=item['interactive_key'], secretKey=item['interactive_secret'],
                source=item.get('source', 'WEBAPI'), root="https://tradex.smcindiaonline.com"
            )
            
            if z and z.get('interactive_token'):
                smc.token = z['interactive_token']
                smc.userID = z.get('userID')
                smc.isInvestorClient = z.get('isInvestorClient', True)
                
                try:
                    profile = smc.get_profile()
                    if profile and profile.get('result'):
                        return item['user'], smc, {
                            'interactive_token': z['interactive_token'],
                            'userID': smc.userID, 'isInvestorClient': smc.isInvestorClient
                        }
                except Exception as e:
                    print(f"SMC session expired: {e}")
            
            interactive_response = smc.interactive_login()
            
            if interactive_response and interactive_response.get('result'):
                session_data = {
                    'interactive_token': interactive_response['result']['token'],
                    'userID': interactive_response['result']['userID'],
                    'isInvestorClient': interactive_response['result'].get('isInvestorClient', True)
                }
                self._save_session_to_db('smcloginsess', 'client_id', item['client_id'], session_data)
                return item['user'], smc, session_data
            
            return item['user'], None, None
        except Exception as e:
            print(f"SMC login error for {item['user']}: {e}")
            return item['user'], None, None

    def _login_mstock(self, item):
        """MStock login handler"""
        try:
            import imaplib, email, re
            from email.header import decode_header
            
            self._ensure_collection_exists('mstockloginsess')
            z = self._get_existing_session('mstockloginsess', 'userid', item['userid'])
            
            if z and 'access_token' in z:
                headers = {
                    'X-Mirae-Version': '1',
                    'Authorization': f'token {item["apikey"]}:{z["access_token"]}',
                }
                try:
                    response = requests.get(
                        'https://api.mstock.trade/openapi/typea/user/fundsummary',
                        headers=headers, timeout=10
                    )
                    if response.status_code == 200 and response.json().get('status') == 'success':
                        return item['user'], {'apikey': item['apikey'], 'access_token': z['access_token']}, {'access_token': z['access_token']}
                except Exception as e:
                    print(f"MStock session expired: {e}")
            
            headers = {'X-Mirae-Version': '1', 'Content-Type': 'application/x-www-form-urlencoded'}
            data = {'username': item['userid'], 'password': item['password']}
            
            login_response = requests.post(
                'https://api.mstock.trade/openapi/typea/connect/login',
                headers=headers, data=data, timeout=10
            )
            
            if login_response.status_code != 200 or login_response.json().get('status') != 'success':
                return item['user'], None, None
            
            time.sleep(3)
            
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(item['eemail'], item['epassword'])
            mail.select("inbox")
            
            status, messages = mail.search(None, '(FROM "info@mstock.com")')
            email_ids = messages[0].split()
            
            if not email_ids:
                return item['user'], None, None
            
            latest_email_id = email_ids[-1]
            status, msg_data = mail.fetch(latest_email_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            
            subject_raw = msg["subject"]
            decoded_subject, encoding = decode_header(subject_raw)[0]
            if isinstance(decoded_subject, bytes):
                decoded_subject = decoded_subject.decode(encoding or "utf-8")
            
            otp_match = re.search(r"\b(\d{3,6})\b", decoded_subject)
            if not otp_match:
                return item['user'], None, None
            
            otp = otp_match.group(1)
            
            token_headers = {'X-Mirae-Version': '1', 'Content-Type': 'application/x-www-form-urlencoded'}
            token_data = {'api_key': item['apikey'], 'request_token': otp, 'checksum': 'L'}
            
            token_response = requests.post(
                'https://api.mstock.trade/openapi/typea/session/token',
                headers=token_headers, data=token_data, timeout=10
            )
            
            if token_response.status_code != 200 or token_response.json().get('status') != 'success':
                return item['user'], None, None
            
            access_token = token_response.json()['data']['access_token']
            self._save_session_to_db('mstockloginsess', 'userid', item['userid'], {'access_token': access_token})
            
            return item['user'], {'apikey': item['apikey'], 'access_token': access_token}, {'access_token': access_token}
        except Exception as e:
            print(f"MStock login error for {item['user']}: {e}")
            return item['user'], None, None

    # ============== Generic Login/Relogin Processor ==============

    def _process_broker_logins(self, broker_name, login_handler, broker_dict, success_key, num_threads=2):
        """Generic broker login processor"""
        try:
            items = self._get_broker_users(broker_name)
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(login_handler, item) for item in items]
                for future in concurrent.futures.as_completed(futures, timeout=150):
                    user_id, instance, session_data = future.result()
                    self._update_user_login_state(user_id, instance, session_data, broker_dict, success_key)
        except Exception as e:
            print(f"{broker_name} login process error: {str(e)}")
            time.sleep(20)

    def _process_broker_relogins(self, broker_name, login_handler, broker_dict, success_key, sleep_time=5, num_threads=2):
        """Generic broker relogin processor (infinite loop)"""
        while not self._shutdown_event.is_set():
            try:
                items = self._get_non_logged_broker_users(broker_name)
                with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                    futures = [executor.submit(login_handler, item) for item in items]
                    for future in concurrent.futures.as_completed(futures, timeout=150):
                        user_id, instance, session_data = future.result()
                        self._update_user_login_state(user_id, instance, session_data, broker_dict, success_key)
                
                if self._shutdown_event.wait(sleep_time):
                    break
            except Exception as e:
                if self._shutdown_event.is_set() or 'shutdown' in str(e).lower():
                    break
                print(f"{broker_name} relogin error: {str(e)}")
                if self._shutdown_event.wait(20):
                    break

    # ============== Original Thread Start Code (Refactored) ==============

    def _start_all_threads(self):
        """Start all login and background threads"""
        threads = [
            threading.Thread(target=self._loginusers),
            threading.Thread(target=self._reloginusers),
            threading.Thread(target=self._shoonyaloginusers),
            threading.Thread(target=self._fyersloginusers),
            threading.Thread(target=self._angeloneloginusers),
            threading.Thread(target=self._dhanloginusers),
            threading.Thread(target=self._zerodhaLoginUsers),
            threading.Thread(target=self._mofsloginusers),
            threading.Thread(target=self._mstockloginusers),
            threading.Thread(target=self._mstockreloginusers),
            threading.Thread(target=self._smcloginusers),

            threading.Thread(target=self._dataloader),
            threading.Thread(target=self._positionshold),
            threading.Thread(target=self._datascript),
            threading.Thread(target=self._dataequityscript),
            threading.Thread(target=self._dataorderscript),
            threading.Thread(target=self._stopnotsubusers),
        ]
        if all(
            os.getenv(name, "").strip()
            for name in (
                "SSLAGO_STOCKNOTE_USER_ID",
                "SSLAGO_STOCKNOTE_PASSWORD",
                "SSLAGO_STOCKNOTE_YOB",
            )
        ):
            threads.append(threading.Thread(target=self.run_websocket, daemon=True))
        else:
            trading_event(
                "strategy_engine_stage",
                force=True,
                stage="stocknote_websocket_skipped",
                reason="not_configured",
            )
        
        for t in threads:
            t.daemon = True
            t.start()

    def shutdown(self):
        self._shutdown_event.set()
        for client in (getattr(self, "api", None), getattr(self, "reapi", None)):
            if client is None:
                continue
            for method_name in ("stop_websocket", "close_websocket", "disconnect"):
                method = getattr(client, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception as exc:
                        print(f"strategy engine shutdown warning: {exc}")
                    break

    # ============== Refactored Login Methods (Keep Same Names) ==============

    def _loginusers(self):
        """AliceBlue login - uses generic processor"""
        try:
            self._process_broker_logins('aliceblue', self._login_aliceblue, 'alice', 'sessionID')
        except:
            time.sleep(20)

    def _shoonyaloginusers(self):
        """Shoonya login - uses generic processor"""
        try:
            self._process_broker_logins('shoonya', self._login_shoonya, 'shoonya', 'susertoken')
        except:
            time.sleep(20)
            print('shoonya login error')

    def _fyersloginusers(self):
        """Fyers login - uses generic processor"""
        try:
            self._process_broker_logins('fyers', self._login_fyers, 'fyers', 'access_token')
        except Exception as e:
            print(f"Fyers login process error: {str(e)}")
            time.sleep(20)

    def _angeloneloginusers(self):
        """Angel One login - uses generic processor"""
        try:
            self._process_broker_logins('angelone', self._login_angelone, 'angelone', 'jwt_token')
        except Exception as e:
            print(f"Angel One login process error: {str(e)}")
            time.sleep(20)

    def _dhanloginusers(self):
        """Dhan login - uses generic processor"""
        try:
            self._process_broker_logins('dhan', self._login_dhan, 'dhan', 'status')
        except:
            time.sleep(20)

    def _zerodhaLoginUsers(self):
        """Zerodha login - uses generic processor"""
        try:
            self._process_broker_logins('zerodha', self._login_zerodha, 'zerodha', 'access_token')
            time.sleep(60*5)
        except Exception as e:
            print(f"Zerodha login process error: {str(e)}")
            time.sleep(20)

    def _mofsloginusers(self):
        """MOFS login - uses generic processor"""
        try:
            self._process_broker_logins('mofs', self._login_mofs, 'mofs', 'auth_token')
        except Exception as e:
            print(f"MOFS login process error: {str(e)}")
            time.sleep(20)

    def _mstockloginusers(self):
        """MStock login - uses generic processor"""
        try:
            self._process_broker_logins('mstock', self._login_mstock, 'mstock', 'access_token')
        except Exception as e:
            print(f"MStock login process error: {str(e)}")
            time.sleep(20)

    def _smcloginusers(self):
        """SMC login - uses generic processor"""
        try:
            self._process_broker_logins('smc', self._login_smc, 'smc', 'interactive_token')
        except Exception as e:
            print(f"SMC login process error: {str(e)}")
            time.sleep(20)

    # ============== Refactored Relogin Methods (Keep Same Names) ==============

    def _reloginusers(self):
        """AliceBlue relogin - uses generic processor"""
        self._process_broker_relogins('aliceblue', self._login_aliceblue, 'alice', 'sessionID', sleep_time=60)

    def _shoonyareloginusers(self):
        """Shoonya relogin - uses generic processor"""
        self._process_broker_relogins('shoonya', self._login_shoonya, 'shoonya', 'susertoken', sleep_time=5)

    def _fyersreloginusers(self):
        """Fyers relogin - uses generic processor"""
        self._process_broker_relogins('fyers', self._login_fyers, 'fyers', 'access_token', sleep_time=5)

    def _angelonereloginusers(self):
        """Angel One relogin - uses generic processor"""
        self._process_broker_relogins('angelone', self._login_angelone, 'angelone', 'jwt_token', sleep_time=5)

    def _dhanreloginusers(self):
        """Dhan relogin - uses generic processor"""
        self._process_broker_relogins('dhan', self._login_dhan, 'dhan', 'status', sleep_time=5)

    def _zerodhaReloginUsers(self):
        """Zerodha relogin - uses generic processor"""
        self._process_broker_relogins('zerodha', self._login_zerodha, 'zerodha', 'access_token', sleep_time=60*5)

    def _mofsreloginusers(self):
        """MOFS relogin - uses generic processor"""
        self._process_broker_relogins('mofs', self._login_mofs, 'mofs', 'auth_token', sleep_time=5)

    def _mstockreloginusers(self):
        """MStock relogin - uses generic processor"""
        self._process_broker_relogins('mstock', self._login_mstock, 'mstock', 'access_token', sleep_time=10)

    def _smcreloginusers(self):
        """SMC relogin - uses generic processor"""
        self._process_broker_relogins('smc', self._login_smc, 'smc', 'interactive_token', sleep_time=5)

    # ============== eodsamco Method (Keep as-is, minimal changes) ==============

    def eodsamco(self):
        """Fetch and cache SAMCO historical data"""
        samk = {}
        headers = {'Accept': 'application/json', 'x-session-token': self.session_token}
        dbfile = {}
        
        # Try to load cached data
        try:
            with open('samcodb.pkl', 'rb') as file:
                k = pickle.load(file)
                if 'updated_date' in k.keys():
                    if datetime.time(8,0) < datetime.datetime.now().time():
                        dd = datetime.datetime.now().date()
                        if k['updated_date'] == dd:
                            dbfile = k['data']
        except (FileNotFoundError, EOFError, Exception) as e:
            print(f"Error loading cache: {e}")
        
        samkdf = {}
        
        if not dbfile:
            # Fetch data for all stocks
            for j in range(3):
                for i in self.stocks:
                    if i not in samk:
                        try:
                            r = requests.get('https://api.stocknote.com/history/candleData',
                                            params={'symbolName': i, 'fromDate': '2021-01-01'},
                                            headers=headers)
                            samk[i] = r.json()['historicalCandleData']
                        except:
                            pass
                j += 1
                print(datetime.datetime.now(), time.time())
        
        # Process data into DataFrames
        for i in self.stocks:
            try:
                if i in dbfile.keys():
                    samkdf[i] = pd.DataFrame.from_dict(dbfile[i])
                else:
                    r = requests.get('https://api.stocknote.com/history/candleData',
                                    params={'symbolName': i, 'fromDate': '2021-01-01'},
                                    headers=headers)
                    samk[i] = r.json()['historicalCandleData']
                    jj = pd.DataFrame(samk[i])
                    jj['datetime'] = jj['date']
                    for col in ['open', 'high', 'close', 'low', 'volume']:
                        jj[col] = jj[col].astype(float)
                    jj['prevclose'] = jj['close'].shift(1)
                    jj['date'] = pd.to_datetime(jj['date'])
                    jj['symbol'] = i
                    samkdf[i] = jj
                    dbfile[i] = jj.to_dict('records')
            except:
                pass
        
        # Save cache
        dd = datetime.datetime.now().date()
        if datetime.time(8,0) >= datetime.datetime.now().time():
            dd -= datetime.timedelta(days=1)
        
        with open('samcodb.pkl', 'wb') as file:
            pickle.dump({'updated_date': dd, 'data': dbfile}, file)
        
        self.dataframes1d = dbfile

    def _stopnotsubusers(self):
        while not self._shutdown_event.is_set():
            try:
                if (datetime.datetime.today().weekday() < self.marketdays) and datetime.time(9,30) < datetime.datetime.now().time() and datetime.time(15,28) > datetime.datetime.now().time():
                    uss=pd.DataFrame(list(self.subscriptionperiod_collection.find()))
                    if not uss.empty:
                        uss['end']=pd.to_datetime(uss['end'])#+pd.Timedelta(days=1)
                        uss=uss[uss['end']<datetime.datetime.now()]
                        stopuser=list(uss['user'])
                        for st in stopuser:
                            sst=self.strategy_collection.find({'user':st,'status':'opened'})
                            for ss in sst:
                                self.strategy_collection.update_one({'botcode':ss['botcode'],'user':st}, {'$set': {'status':'paused'} })

                time.sleep(60*60)
            except:
                time.sleep(20)
                pass

    def _positionshold(self):
        while True:
            try:
                self._refresh_open_opositions_snapshot()
                # Fetch the list of open positions
                g = list(self.opositions_collection.find({'status': {'$in': ['open']}}))
                
                # Initialize a dictionary to hold positions by user
                z = {}
                
                # Group positions by user
                for i in g:
                    if 'user' in i:
                        if i['user'] not in z:
                            z[i['user']] = []
                        del i['_id']
                        z[i['user']].append(i)
                
                # Update self.allpos with the grouped positions
                for user, positions in z.items():
                    if user not in self.allpos:
                        self.allpos[user] = []
                    self.allpos[user] = positions
                for user in list(self.allpos.keys()):
                    if user not in list(z.keys()):
                        self.allpos[user]=[]
                #print(self.allpos)
                time.sleep(2)
            except:
                pass

    def _refresh_open_opositions_snapshot(self):
        open_positions = list(self.opositions_collection.find({'status': 'open'}))
        for position in open_positions:
            try:
                changed = False
                strategy = None
                if position.get('botcode') and position.get('user'):
                    strategy = self.strategy_collection.find_one(
                        {'botcode': position['botcode'], 'user': position['user']}
                    )
                    if strategy:
                        strategy_updates = {}
                        strategy_position = str(strategy.get('position') or '').lower()
                        strategy_status = str(strategy.get('status') or '').lower()

                        if strategy_position == 'out':
                            strategy_updates['position'] = 'in'

                        if (
                            strategy_status in {'paused', 'closed'}
                            and position.get('decision') != 'exitit'
                        ):
                            position['decision'] = 'exitit'
                            changed = True

                        if strategy_updates:
                            self.strategy_collection.update_one(
                                {'_id': strategy['_id']},
                                {'$set': strategy_updates}
                            )
                            strategy.update(strategy_updates)
                            print(
                                f"open-position strategy mismatch repaired: "
                                f"user={position.get('user')}, botcode={position.get('botcode')}, "
                                f"updates={strategy_updates}"
                            )

                if isinstance(position.get('pos'), list):
                    total_pnl = 0
                    for leg in position['pos']:
                        symbol = leg.get('optionname')
                        if symbol:
                            self.add_symbol_to_websocket(symbol)
                        price = self._get_market_price(
                            symbol,
                            leg.get('exch') or position.get('exch'),
                            leg.get('optiontoken')
                        )
                        current_underlying = self._get_underlying_price(
                            leg.get('symbol'),
                            leg.get('current_price') or leg.get('entry_price')
                        )
                        leg['optionexit'] = float(price)
                        leg['current_price'] = float(current_underlying)
                        if str(leg.get('side', '')).upper() == 'SELL':
                            leg_pnl = self._initial_position_pnl(
                                is_sell=True,
                                entry_price=leg.get('optionentry', price),
                                current_price=price,
                                lot=leg.get('lot', 1),
                                optionlot=leg.get('optionlot', 1)
                            )
                        else:
                            leg_pnl = self._initial_position_pnl(
                                is_sell=False,
                                entry_price=leg.get('optionentry', price),
                                current_price=price,
                                lot=leg.get('lot', 1),
                                optionlot=leg.get('optionlot', 1)
                            )
                        leg['cpnl'] = int(leg_pnl)
                        leg['pnl'] = int(leg_pnl)
                        total_pnl += int(leg_pnl)
                        changed = True

                    position['pnl'] = int(total_pnl)

                elif position.get('optionname'):
                    symbol = position.get('optionname')
                    self.add_symbol_to_websocket(symbol)
                    price = self._get_market_price(
                        symbol,
                        position.get('exch'),
                        position.get('optiontoken')
                    )
                    current_underlying = self._get_underlying_price(
                        position.get('symbol'),
                        position.get('current_price') or position.get('entry_price')
                    )
                    is_sell = (position.get('BSmode') is False) or str(position.get('side', '')).upper() == 'SELL'
                    pnl = self._initial_position_pnl(
                        is_sell=is_sell,
                        entry_price=position.get('optionentry', price),
                        current_price=price,
                        lot=position.get('lot', 1),
                        optionlot=position.get('optionlot', 1)
                    )
                    position['optionexit'] = float(price)
                    position['current_price'] = float(current_underlying)
                    position['pnl'] = int(pnl)
                    changed = True

                if changed:
                    self.opositions_collection.update_one(
                        {'_id': position['_id']},
                        {'$set': position}
                    )
            except Exception:
                continue
    def _dataloader(self):
        #tt=self.hist('BANKNIFTY')
        #self.history_collection = self.db["historical"]
        for i in list(self.tok_symbols.keys()):
            self.hist(i, tf="1",initial=True)
        #self.hist('CRUDEOIL', tf="1",initial=True)
        #t=pd.to_timedelta(1, unit='minute')
        #symbols = list(self.tok_symbols.keys()).copy()#['BANKNIFTY', 'NIFTY', 'FINNIFTY','MIDCPNIFTY','BANKNIFTY-I', 'NIFTY-I', 'FINNIFTY-I','MIDCPNIFTY-I','BANKNIFTY-II', 'NIFTY-II', 'FINNIFTY-II','MIDCPNIFTY-II']
        symbols=['SILVERMIC','FINNIFTY', 'BANKNIFTY','SENSEX', 'MIDCPNIFTY', 'NIFTY', 'CRUDEOIL','SILVER', 'NIFTY-I', 'NIFTY-II', 'BANKNIFTY-I', 'BANKNIFTY-II', 'FINNIFTY-I', 'FINNIFTY-II', 'MIDCPNIFTY-I', 'MIDCPNIFTY-II','SENSEX-I','SENSEX-II', 'CRUDEOILM', 'SILVERM', 'NATURALGAS', 'NATGASMINI']#datadf=list(self.history_collection.find({'symbol':'BANKNIFTY'}).sort('_id', pymongo.DESCENDING).limit(5000))
        #print(datadf)
        #self.dataframes['BANKNIFTY']=[]
        #print(self.dataframes.keys())
        
        while True:
         
            try:
                #print('tannng')
                if ((datetime.datetime.today().weekday() < self.marketdays) and datetime.time(8,59) < datetime.datetime.now().time() and datetime.time(23,40) > datetime.datetime.now().time()) or self.testmode:
                    # Check if it's been more than 2 minutes since the last update
                    #print('juuuuuuuuuuuuuuuuuuuttttttttt')
                    
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        futures = [executor.submit(lambda symbol: self.hist(symbol, '1', False), symbol) for symbol in symbols]

                        for future in concurrent.futures.as_completed(futures):
                            result = future.result()


                        #print('I am done with that')
                        #print(self.prices)
                        #print(self.lastupdate)

                        #self.dataframes['BANKNIFTY']=list(self.history_collection.find({'symbol':'BANKNIFTY'}).sort('_id', pymongo.DESCENDING).limit(5000))[::-1]
                        
                        # Pause for 1 second before the next iteration
                #self.api.subscribe(self.subscribe_list)
                time.sleep(1)
            except Exception as e:
                # Print any exception that occurs during data loading
                print(f'Data Loader Error: {e}')
                #print(symbols)
                time.sleep(30)
                pass

    # Asynchronous function to fetch historical data
    async def getHistIntraDayDataAsync(self,session, instrument):
        end_date = datetime.datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')

        instrument = urllib.parse.quote(instrument)
        url = f'https://api-v2.upstox.com/historical-candle/{instrument}/1minute/{end_date}/{start_date}'
        headers = {'accept': 'application/json', 'Api-Version': '2.0'}
        async with session.get(url, headers=headers) as response:
            return await response.json()

    # Asynchronous function to fetch intraday data
    async def getIntraDayDataAsync(self,session, instrument):
        instrument = urllib.parse.quote(instrument)
        url = f'https://api-v2.upstox.com/historical-candle/intraday/{instrument}/1minute'
        headers = {'accept': 'application/json', 'Api-Version': '2.0'}
        async with session.get(url, headers=headers) as response:
            return await response.json()

    # Process each row asynchronously
    async def process_row(self,session, row):
        try:
            symbol = row['tradingsymbol']
            instrument = row['instrument_key']

            # Fetch historical data if not present or insufficient
            if symbol not in list(self.dataframe1.keys()) or len(self.dataframe1[symbol]) < 5:
                hist_data = await self.getHistIntraDayDataAsync(session, instrument)
                self.dataframe1[symbol] = pd.DataFrame(
                    hist_data['data']['candles'],
                    columns=['time', 'open', 'high', 'low', 'close', 'volume', 'None']
                )
                #print(f'{symbol}: {len(hist_data["data"]["candles"])} candles fetched')

            # Fetch intraday data
            intra_data = await self.getIntraDayDataAsync(session, instrument)
            if len(intra_data['data']['candles']) > 5:
                self.dataframe2[symbol]=pd.DataFrame(
                    intra_data['data']['candles'],
                    columns=['time', 'open', 'high', 'low', 'close', 'volume', 'None']
                )
                #self.dataframe2[symbol]
                return symbol, pd.DataFrame(
                    intra_data['data']['candles'],
                    columns=['time', 'open', 'high', 'low', 'close', 'volume', 'None']
                )
            else:
                return symbol, pd.DataFrame()
        except Exception as e:
            print(f'Error in {row["tradingsymbol"]}: {str(e)}')
            return None  # Ensure task returns something valid

    # Main async function to manage all tasks
    async def asynCall(self):
        async with aiohttp.ClientSession() as session:
            #print('&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&      test1 &&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&')
            #print(self.upstoxtok_symbols.keys())
            #print('###############################         test2             #############################')
            tasks = [self.process_row(session, {'tradingsymbol': symbol, 'instrument_key': instrument}) for symbol, instrument in self.upstoxtok_symbols.items()]
            symDataList = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out failed tasks and None results
            symDataMap = {
                symbol: df for result in symDataList 
                if result is not None and isinstance(result, tuple) 
                for symbol, df in [result] if not df.empty
            }
            #print(symDataMap)
            return symDataMap
    async def main(self):
        start_time = datetime.datetime.now()
        output = await self.asynCall()
        end_time = datetime.datetime.now()
        print("Time taken:", end_time - start_time)
    def equityhisrun(self):
        try:
            asyncio.run(self.main())
        except:
            pass

        while True:
            try:
                if (self.testmode) or ((datetime.datetime.today().weekday() < self.marketdays) and datetime.time(8,59) < datetime.datetime.now().time() and datetime.time(15,28) > datetime.datetime.now().time()):
                    # Check if it's been more than 2 minutes since the last update
                    #if (self.testmode) or pd.to_datetime(datetime.datetime.now()) > (pd.to_datetime(self.lastupdate,format='%d-%m-%Y %H:%M:%S') + pd.to_timedelta(2, unit='minute')):
                        
                    asyncio.run(self.asynCall())
                    time.sleep(15)
            except Exception as e:
                print(f'Error: {e}')
            finally:
                time.sleep(30)
    def _topbottomscript(self):
        #print('jjjjj1')
        while True:
            try:
                if ((  datetime.datetime.now().time()>datetime.datetime.strptime('9:25', '%H:%M').time())   and (time.time() >self.equitytime ) and datetime.datetime.now().time()<datetime.datetime.strptime('14:30', '%H:%M').time()) : #not self.topbottomlist# and datetime.datetime.now().time()<datetime.datetime.strptime('9:30', '%H:%M').time() :
                    print('hrlllo')
                    cookies = {}
                    headers = {'authority': 'www.nseindia.com',
                        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                        'accept-language': 'en-US,en;q=0.9',
                        'sec-ch-ua': '"Not/A)Brand";v="99", "Opera GX";v="101", "Chromium";v="115"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"',
                        'sec-fetch-dest': 'document',
                        'sec-fetch-mode': 'navigate',
                        'sec-fetch-site': 'none',
                        'sec-fetch-user': '?1',
                        'upgrade-insecure-requests': '1',
                        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36 OPR/101.0.0.0'}
                    session = requests.Session()
                    response = session.get('https://www.nseindia.com/',cookies=cookies,
                    headers=headers,)
                    maincookie=(session.cookies.get_dict())
                    headers = {
                        'authority': 'www.nseindia.com',
                        'accept': '*/*',
                        'accept-language': 'en-US,en;q=0.9',
                        'referer': 'https://www.nseindia.com/market-data/top-gainers-losers',
                        'sec-ch-ua': '"Not A(Brand";v="99", "Opera GX";v="107", "Chromium";v="121"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"',
                        'sec-fetch-dest': 'empty',
                        'sec-fetch-mode': 'cors',
                        'sec-fetch-site': 'same-origin',
                        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 OPR/107.0.0.0',}
                    params = {    'index': 'gainers',}
                    response = requests.get('https://www.nseindia.com/api/live-analysis-variations', params=params, cookies=maincookie, headers=headers)
                    gainers=pd.DataFrame(response.json()['FOSec']['data'])
                    if self.strategyinputs['SSEQUITYFNO']['update']:
                        gainers=gainers[gainers['net_price']>int(self.strategyinputs['SSEQUITYFNO']['r1'])]
                    else:    
                        gainers=gainers[gainers['net_price']>2]
                    params = {    'index': 'loosers',}
                    response = requests.get('https://www.nseindia.com/api/live-analysis-variations', params=params, cookies=maincookie, headers=headers)
                    losers=pd.DataFrame(response.json()['FOSec']['data'])
                    if self.strategyinputs['SSEQUITYFNO']['update']:
                        losers=losers[losers['net_price']<-int(self.strategyinputs['SSEQUITYFNO']['k1'])]
                    else:    
                        losers=losers[losers['net_price']<-2]
                    headers = {
                        'authority': 'www.nseindia.com',
                        'accept': '*/*',
                        'accept-language': 'en-US,en;q=0.9',
                        'referer': 'https://www.nseindia.com/market-data/oi-spurts',
                        'sec-ch-ua': '"Not A(Brand";v="99", "Opera GX";v="107", "Chromium";v="121"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"',
                        'sec-fetch-dest': 'empty',
                        'sec-fetch-mode': 'cors',
                        'sec-fetch-site': 'same-origin',
                        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 OPR/107.0.0.0'}
                    response = requests.get('https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings', cookies=maincookie, headers=headers)
                    j=response.json()
                    df=pd.DataFrame(j['data'])
                    if self.strategyinputs['SSEQUITYFNO']['update']:
                        df=df[df['avgInOI']>int(self.strategyinputs['SSEQUITYFNO']['r2'])]
                    else:    
                        df=df[df['avgInOI']>7]
                    symbols_to_exclude = ['BANKNIFTY', 'NIFTY', 'MIDCPNIFTY', 'FINNIFTY']
                    df = df[~df['symbol'].isin(symbols_to_exclude)]
                    sell=[]
                    buy=[]
                    both=[]
                    for index, row in gainers.iterrows():
                        if len(df[df['symbol']==row['symbol']]) >0:
                            buy.append(df[df['symbol']==row['symbol']].iloc[-1])
                            both.append(df[df['symbol']==row['symbol']].iloc[-1]['symbol'])
                    for index, row in losers.iterrows():
                        if len(df[df['symbol']==row['symbol']]) >0:
                            sell.append(df[df['symbol']==row['symbol']].iloc[-1])
                            both.append(df[df['symbol']==row['symbol']].iloc[-1]['symbol'])
                    buy=pd.DataFrame(buy)
                    sell=pd.DataFrame(sell)
                    print(sell)
                    print(both)
                    if not (buy.empty):
                        self.topbottombuylist=list(buy['symbol'])
                    if not (sell.empty):
                        self.topbottomselllist=list(sell['symbol'])
                    for i in both:
                        a,b,c=self.MainEquitySelect(i)#k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'], int(k.iloc[-1]['Token'])
                        self.tok_symbols[i]='NSE|'+str(c)
                        self.symbols_tok[('NSE|'+str(c))]=a
                        self.topbottomsymbol.append(i)
                        self.equitytransformer[i]=a
                        self.inverseequitytransformer[a]=i
                    self.topbottomlist=True
                    self.equitytime=time.time()+60*5
                time.sleep(30)
            except:
                print('error in TOP TOPBOTTOM SCRIPT')
                time.sleep(30)
                pass


    
    async def intradayhist(self,session, exch, tok,start,end,interval):
        values = {
            'ordersource': 'API',
            'uid': self.cred['user'],
            'exch': exch,
            'token': str(tok),
            'st': str(start.timestamp()),
            'et': str(end.timestamp()) if end else None,
            'intrv': str(interval) if interval else None
        }

        payload = 'jData=' + json.dumps(values) + f'&jKey={self.sessionusertoken}'
        url = "https://api.shoonya.com/NorenWClientTP/TPSeries"

        async with session.post(url, data=payload) as res:
            if res.status == 200:
                res_text = await res.text()
                resDict = json.loads(res_text)  # Use json.loads directly on the text
                return resDict
            else:
                error_text = await res.text()
                print(f"Error {res.status}: {error_text}")
                return None  # Return None or handle error as needed

    async def process_row(self,session, row):
        try:
            symbol = row['TradingSymbol']
            exch = row['Exchange']
            instrument = row['Token']
            await asyncio.sleep(0.2)
            days=3
            if symbol not in list(self.dataframes.keys()):
                days=75
            start=datetime.datetime.now()-datetime.timedelta(days=days)
            end=datetime.datetime.now()
            interval='5'
            candleRes = await self.intradayhist(session, exch, instrument,start,end,interval)
            
            if candleRes:  # Check if the response is valid
                #print(f'{symbol} ', len(candleRes))
                df=pd.DataFrame(candleRes)
                if not df.empty:
                    #df = df.iloc[::-1].reset_index()
                    df['date'] = df['time']#pd.to_datetime(df['time'])
                    df['open'] = df['into'].astype(float)
                    df['high'] = df['inth'].astype(float)
                    df['close'] = df['intc'].astype(float)
                    df['low'] = df['intl'].astype(float)
                    df['volume'] = df['intv'].astype(int)
                    df1m = df[['date', 'open', 'high', 'low', 'close', 'volume', 'time']]
                    
                    
                    # Database handling
                    df1m['date']=pd.to_datetime(df1m['time'],format='%d-%m-%Y %H:%M:%S')
                    df1m['sqlite_timestamp'] = df1m['date'].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S'))
                    df1m= df1m.iloc[::-1]
                    #print(df1m['time'].iloc[-1])
                    self.eqlastupdate = df1m['time'].iloc[-1]
                    df1m['symbol'] = symbol
                    

                    #print(df1m.tail(5))
                    if symbol not in list(self.dataframes.keys()):
                        df1m=df1m.reset_index(drop=True)
                        self.dataframes[symbol]=df1m
                    else:
                        tdf=self.dataframes[symbol].copy()
                        self.dataframes[symbol] = pd.concat([df1m, tdf]).drop_duplicates(subset='time').sort_values(by='date', ascending=True).reset_index(drop=True)
                        #print(self.dataframes[symbol].tail(5))
                return symbol, pd.DataFrame()
            else:
                print(f'No data returned for {symbol}')
                return symbol, pd.DataFrame()  # Return an empty DataFrame if no data
        except Exception as e:
            print(f'Error in {row["TradingSymbol"]}: {str(e)}')
            return row['TradingSymbol'], pd.DataFrame()  # Return an empty DataFrame on error

    async def asynCall(self):
        async with aiohttp.ClientSession() as session:
            tasks = [self.process_row(session, row) for _, row in pd.DataFrame(self.nsestocksunfil).iterrows()]
            symDataMap = await asyncio.gather(*tasks)
            return symDataMap    

    async def main(self,):
        start_time = datetime.datetime.now()
        output = await self.asynCall()
        end_time = datetime.datetime.now()
        #print(list(output))
        print("Time taken:", end_time - start_time)
        #print(list(self.dataframes.keys()))
        #for i in output:
        #print(i[1])

    def equityhisrun(self):
        try:
            asyncio.run(self.main())
        except:
            pass

        while True:
            try:
                if (self.testmode) or ((datetime.datetime.today().weekday() < self.marketdays) and datetime.time(8,59) < datetime.datetime.now().time() and datetime.time(15,28) > datetime.datetime.now().time()):
                    # Check if it's been more than 2 minutes since the last update
                    if (self.testmode) or pd.to_datetime(datetime.datetime.now()) > (pd.to_datetime(self.eqlastupdate,format='%d-%m-%Y %H:%M:%S') + pd.to_timedelta(10, unit='minute')):
                        
                        asyncio.run(self.main())
                        time.sleep(30)
            except Exception as e:
                print(f'Error: {e}')
            finally:
                time.sleep(30)



    def _symboltransformmonthfut(self,date,symbol):
        if 'Current Month' in date:
            return symbol.upper()+'-I'
        elif 'Next Month' in date:
            return symbol.upper()+'-II'
        elif 'Third Month' in date:
            return symbol.upper()+'-III'
    def process_order_strategy(self,trade):
        #if trade['user'] in list(self.alice.keys()):
        #if (trade['status'] != 'closed' ):
            #print('hello')
        #print(trade['strategy'])
        try:
            self._log_strategy_evaluation(trade)
            if trade['strategy']=='FRACTALNUBIATIMEHEDGEORDER':
                self.FRACTALNUBIATIMEHEDGEORDER(trade)
        except Exception as exc:
            self._log_strategy_exception(trade, exc)
            raise

    def process_equity_strategy(self,trade):
        #if trade['user'] in list(self.alice.keys()):
        #if (trade['status'] != 'closed' ):
            #print('hello')
        try:
            self._log_strategy_evaluation(trade)
            if trade['strategy'] == 'SSEQUITY':
                self.CHARTINK(trade)
            elif trade['strategy'] == 'EQSSALGO':
                self.EQSSALGO(trade)
        except Exception as exc:
            self._log_strategy_exception(trade, exc)
            raise

    def process_strategy(self,trade):
        #if trade['user'] in list(self.alice.keys()):
        #if (trade['status'] != 'closed' ):
            #print('hello')
        try:
            self._log_strategy_evaluation(trade)
            if trade['strategy'] == 'SSALGO':
                self.SSALGO(trade)
            elif trade['strategy'] == 'EMA':
                self.EMA(trade)
            elif trade['strategy'] == 'PEMA':
                self.PEMA(trade)
            elif trade['strategy'] == 'RF':
                self.RF(trade)
            elif trade['strategy'] == 'SSAUTO':
                self.UTBOT(trade)
            elif trade['strategy'] == 'SSTRIKE':
                self.SSTRIKE(trade)
            elif trade['strategy']=='FRACTALNUBIATIMEHEDGEORDER':
                self.FRACTALNUBIATIMEHEDGEORDER(trade)
        except Exception as exc:
            self._log_strategy_exception(trade, exc)
            raise

    def _log_strategy_evaluation(self, trade):
        status = str(trade.get("status") or "").strip().lower()
        if status != "opened" and not _env_bool(
            "DEBUG_TRADING_VERBOSE_EVALUATION",
            False,
        ):
            return

        strategy_id = str(
            trade.get("botcode")
            or trade.get("_id")
            or f"{trade.get('user')}:{trade.get('strategy')}"
        )
        now = time.monotonic()
        interval = max(
            1,
            int(os.getenv("DEBUG_TRADING_EVALUATION_INTERVAL_SECONDS", "60")),
        )
        if (
            not _env_bool("DEBUG_TRADING_VERBOSE_EVALUATION", False)
            and now - self._debug_strategy_eval_log_times.get(strategy_id, 0) < interval
        ):
            return
        self._debug_strategy_eval_log_times[strategy_id] = now

        symbols = trade.get("symbol")
        symbol_details = {}
        if isinstance(symbols, list):
            symbol_details = {
                "symbol_count": len(symbols),
                "symbol_sample": symbols[:5],
            }
        else:
            symbol_details = {"symbol": symbols}
        trading_event(
            "strategy_evaluation_started",
            user=trade.get("user"),
            strategy_id=strategy_id,
            strategy=trade.get("strategy"),
            status=status,
            position=trade.get("position"),
            live=trade.get("live"),
            **symbol_details,
        )

    def _log_strategy_exception(self, trade, exc):
        trading_exception(
            "strategy_evaluation_error",
            exc,
            user=trade.get("user"),
            strategy_id=trade.get("botcode"),
            strategy=trade.get("strategy"),
            symbol=trade.get("symbol"),
        )

    def _log_decision_on_change(
        self, event, trade, state, details, interval_env="DEBUG_TRADING_DECISION_INTERVAL_SECONDS"
    ):
        strategy_id = str(trade.get("botcode") or trade.get("_id") or "")
        key = (event, strategy_id)
        now = time.monotonic()
        interval = max(1, int(os.getenv(interval_env, "60")))
        previous = self._debug_decision_log_state.get(key) or {}
        if previous.get("state") == state and now - previous.get("time", 0) < interval:
            return
        self._debug_decision_log_state[key] = {"state": state, "time": now}
        trading_event(event, **details)

    def _next_entry_id(self):
        return time.time_ns()

    def _admin_control_for_symbol(self, symbol):
        control = self.controls.get(symbol)
        if not control:
            return {"controlmode": False, "Buytrade": False, "Selltrade": False}
        return control

    def _market_dataframe(self, symbol):
        data = self.dataframes.get(symbol, [])
        if isinstance(data, list):
            return pd.DataFrame(data)
        return data

    def _dataorderscript(self):
        
        #self.api.subscribe(self.subscribe_list)
        while not self._shutdown_event.is_set():
            try:

                pos=list(self.opositions_collection.find({'status': 'open'}))
                poss=[]
                for i in pos:
                    poss.append(i['botcode'])
                #print(poss)
                mains = list(self.strategy_collection.find({'$or': [{'status': 'opened'}, {'position': 'in'}]}))
                if len(poss)>0:
                    mains1 = list(self.strategy_collection.find({'botcode': {'$in': poss}}))
                    if len(mains1)>0:
                        #print(mains1)
                        #print('add')
                        mains.extend(mains1)
                unique_mains = []
                seen_botcodes = set()
                for config in mains:
                    botcode = config.get('botcode')
                    dedupe_key = botcode or str(config.get('_id'))
                    if dedupe_key in seen_botcodes:
                        continue
                    seen_botcodes.add(dedupe_key)
                    unique_mains.append(config)
                #with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                #    executor.map(self.process_order_strategy, mains)
                for i in unique_mains:
                    self.process_order_strategy(i)
                #print(f'total length {len(mains)}')
                if self._shutdown_event.wait(1):
                    break
                if self.testmode:
                    #print(self.prices)
                    if self._shutdown_event.wait(1):
                        break
                #print(f'data : {str(datetime.datetime.now())}')
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _order datascript: {e}")
                if self._shutdown_event.wait(1):
                    break
                pass
    

    def _dataequityscript(self):
        now=datetime.datetime.now()
        midnight = now.replace(hour=0, minute=1, second=0, microsecond=0)
        self.timestamp = int(midnight.timestamp())
        if self.api is not None and hasattr(self.api, "subscribe"):
            self.api.subscribe(self.subscribe_list)
        while not self._shutdown_event.is_set():
            try:
                #print(self.prices)
                #time.sleep(1)
                mains = list(self.strategy_collection.find({
                    'strategy': {'$in': ['SSEQUITY', 'SSEQUITYFNO', 'EQSSALGO']},
                    '$or': [{'status': 'opened'}, {'position': 'in'}],
                }))

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    executor.map(self.process_equity_strategy, mains)
                #time.sleep(1)
                if self.testmode:
                    #print(self.prices)
                    if self._shutdown_event.wait(1):
                        break
                #print(f'data : {str(datetime.datetime.now())}')
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _dataequityscript: {e}")
                if self._shutdown_event.wait(1):
                    break
                pass

    def _datascript(self):
        
        if self.api is not None and hasattr(self.api, "subscribe"):
            self.api.subscribe(self.subscribe_list)
        while not self._shutdown_event.is_set():
            try:
                
                mains = list(self.strategy_collection.find({'$or': [{'status': 'opened'}, {'position': 'in'}]}))
                now = time.monotonic()
                if now - self._debug_last_feed_log >= 30:
                    active_symbols = sorted({
                        str(item.get("symbol"))
                        for item in mains
                        if item.get("symbol") and not isinstance(item.get("symbol"), list)
                    })
                    trading_event(
                        "data_feed_status",
                        strategies_loaded=len(mains),
                        symbols=active_symbols,
                        symbols_with_candles=[
                            symbol for symbol in active_symbols
                            if symbol in self.dataframes and len(self.dataframes[symbol]) > 0
                        ],
                        price_symbols=len(self.prices),
                        logged_in_users=list(self.userloggedin),
                    )
                    self._debug_last_feed_log = now

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    executor.map(self.process_strategy, mains)
                if self.testmode:
                    #print(self.prices)
                    if self._shutdown_event.wait(1):
                        break
                #print(f'data : {str(datetime.datetime.now())}')
            except Exception as e:
                #"staprint(Exception)
                if self._shutdown_event.is_set() or 'shutdown' in str(e).lower():
                    break
                print(f"Error in _datascript: {e}")
                trading_exception("strategy_loop_error", e, loop="_datascript")
                if self._shutdown_event.wait(1):
                    break
                pass
    

    def CHARTINK(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                exSignal=0
                Signal=0
                #print(trade)
                allpositions=list(self.opositions_collection.find({'user':trade['user'], 'exittime': {'$gte': self.timestamp}}))
                positions=list(self.opositions_collection.find({'user':trade['user'],'status':"open",'botcode':trade['botcode']}))
                dfpositions=pd.DataFrame(positions)
                dfallpositions=pd.DataFrame(allpositions)
                if not trade['user'] in list( self.userstockcount.keys() ):
                    self.userstockcount[trade['user']]=len(positions)

                stocklist=['RBLBANK','TCS','INFY','IDBI','JIOFIN']
                Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                if trade['status']=='opened':
                    if len(dfpositions) <= int(trade['stocks']):
                        if Intraday or positional or self.testmode:
                            for s in self.newsignalstocklist:
                                if "ETF" not in s:
                                    if (dfallpositions.empty) or s not in list(dfallpositions['symbol']):
                                        if not dfpositions.empty:
                                            if s not in list(dfpositions['symbol']):
                                                self.EBUY(trade,s)
                                        else:
                                            self.EBUY(trade,s)

                self.EBUYEXIT(trade)


            except Exception as e:
                print(f"Error in CHARTINK: {e}")
    @staticmethod
    def _evaluate_143_signal(trade, trends, trends1):
        candle1 = int(trade.get('candle1') or 0)
        candle2 = int(trade.get('candle2') or 0)
        required = max(candle1, candle2)
        if (
            candle1 <= 0
            or candle2 <= 0
            or len(trends) < required
            or len(trends1) < required
        ):
            return {
                'signal': 0,
                'exit_signal': 0,
                'trend_current': None,
                'trend_previous': None,
                'trend2_current': None,
                'trend2_previous': None,
                'reason': 'insufficient_trend_history',
            }

        current = trends[-candle1]
        previous = trends[-candle2]
        current2 = trends1[-candle1]
        previous2 = trends1[-candle2]
        signal = 0

        if trade.get('Newsignal'):
            condition = current != previous and current2 != previous2
        else:
            condition = (
                (current == previous and current2 == previous2)
                or (current != previous and current2 != previous2)
            )

        if condition:
            if current == 0 and current2 == 0:
                signal = 1
            elif current == 1 and current2 == 1:
                signal = -1

        exit_signal = 1 if current == 0 else -1 if current == 1 else 0
        return {
            'signal': signal,
            'exit_signal': exit_signal,
            'trend_current': current,
            'trend_previous': previous,
            'trend2_current': current2,
            'trend2_previous': previous2,
            'reason': (
                'signal_generated'
                if signal in (1, -1)
                else 'entry_condition_false'
            ),
        }

    def SSTRIKE(self,trade):
        #signal-1 for buy -1 for sell
        #print('striker')
        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                if 'timetowait' not in list(trade.keys()):
                    trade['timetowait']=int(datetime.datetime.now().timestamp())

                symbol=trade['symbol']
                Signal=0
                exSignal=0
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)
                if trade['status']=='opened':
                    candle_data = self._market_dataframe(symbol)
                    candle_count = len(candle_data)
                    if candle_count >0:#.empty:
                        tf='1m'
                        #if self.strategyinputs[trade['strategy']]['update']:
                        #    tf=self.strategyinputs[trade['strategy']]['timeframe']
                        #else:
                        tf=trade['timeframe']
                        df=candle_data.iloc[-self.candleswitch[tf]:]
                        df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')#+pd.to_timedelta(1,'minutes')
                        df['dates']=df['date'].dt.date
                        df['weekday']=df['date'].dt.weekday
                        df=df[df['weekday']<5]
                        df.set_index('date', inplace = True)
                        if trade['symbol']=='CRUDEOIL':
                            df=df.between_time('8:59', '23:55')
                        else:
                            df=df.between_time('9:14', '15:30')

                        gp = df.groupby('dates')
                        dfList = []
                        
                        for k, res in gp:
                            resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                           'high': 'max', 
                                                         'low': 'min', 
                                                         'close': 'last','volume':'sum'})
                            resampledf.reset_index(inplace=True)
                            #print(resampledf)
                            dfList.append(resampledf)
                        #print(dfList)

                        df1 = pd.concat(dfList,ignore_index = True)
                        #print(dfList)
                        df=df.reset_index()
                        lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                        #print(lasttimedate)
                        if lasttimedate==df['date'].iloc[-1]:
                            df1=df1
                        else:
                            df1=df1.iloc[:-1]
                            #print('candle not as_completed')
                        #print(df1.tail(10))
                        #df1=df1[df1['date'].dt.weekday<5]
                        Signal=0

                        #if self.strategyinputs[trade['strategy']]['update']:
                        #    ema1=TA.EMA(df1,int(self.strategyinputs[trade['strategy']]['r1']))
                        #    ema2=TA.EMA(df1,int(self.strategyinputs[trade['strategy']]['k1']))
                        #else:  
                         
                        
                        ema1= df1['high'].ewm(span=int(trade['r1']), adjust=False).mean()
                        #ema2=TA.EMA(df1['low'],int(trade['k1']))
                        ema2= df1['low'].ewm(span=int(trade['r1']), adjust=False).mean()
                        
                        df1['short']=ema1
                        df1['long']=ema2
                        
                        #df1['result']=np.where(df1['short']>df1['long'],0,np.where(df1['short']<df1['long'],1,2))
                        df1['result']=np.where((df1['close']>df1['long']) & (df1['close'].shift(1)>df1['long']) & (df1['close']>df1['short']),0,np.where((df1['long']>df1['close']) & (df1['long']>df1['close'].shift(1)),1,2))
                        df1=df1[df1['result']!=2]
                        trends=list(df1['result'])
                        trends1=list(df1['result'])
                        #print(df1[df1['result']!=df1['result'].shift(1)].tail(5)) 
                        #print(df1)
                        #print(trade['timeframe'])
                        #print(trends)
                        #print(trends1)
                        signal_result = self._evaluate_143_signal(
                            trade, trends, trends1
                        )
                        Signal = signal_result['signal']
                        exSignal = signal_result['exit_signal']
                        trading_event(
                            "signal_evaluation",
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=symbol,
                            timeframe=tf,
                            candle_count=len(df1),
                            signal=Signal,
                            exit_signal=exSignal,
                            new_signal=trade.get("Newsignal"),
                            trend_current=signal_result['trend_current'],
                            trend_previous=signal_result['trend_previous'],
                            trend2_current=signal_result['trend2_current'],
                            trend2_previous=signal_result['trend2_previous'],
                            result=signal_result['reason'],
                        )
                    else:
                        trading_event(
                            "signal_rejected",
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=symbol,
                            reason="market_data_unavailable",
                            candle_count=candle_count,
                        )
                trade['decision']='intrade'
                symbol_control = self._admin_control_for_symbol(trade['symbol'])
                if symbol_control['controlmode']:
                    if symbol_control['Buytrade'] and (not symbol_control['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif symbol_control['Selltrade'] and (not symbol_control['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                
                if 'onspot' in list(trade.keys()):
                    #print('.nothinds')
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print('Hello')
                            self.FEXIT(trade,Signal)

                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if Signal==1:
                                print(trade)
                                self.FBUY(trade,"BUY",Signal)
                            elif Signal==-1:
                                print(trade)
                                self.FSELL(trade,"SELL",Signal)

                else:
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode :
                        if trade['position']=='in':
                            #print(trade)
                            if trade['BSmode']:
                                self.OBUYEXIT(trade,Signal,exSignal)
                                #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'out'} })
                            else:
                                self.OSELLEXIT(trade,Signal,exSignal)
                    
                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        #print(trade)
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if trade['BSmode']:
                                if Signal==1:
                                    print(trade)
                                    self.OBUY(trade,"CE",Signal)
                                    
                                elif Signal==-1:
                                    print(trade)
                                    self.OBUY(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                            else:
                                if Signal==1:
                                    print(trade)
                                    self.OSELL(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                                elif Signal==-1:
                                    print(trade)
                                    self.OSELL(trade,"CE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                    
            
            except Exception as e:
                self._log_strategy_exception(trade, e)





    def TOPBOTTOM(self,trade):

        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                #print('hell')
                #deltasym={}
                if 'signalend' not in list(trade.keys()):
                    trade['signalend']='10:30'
                    del trade['_id']
                    self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': trade })

                if (  datetime.datetime.now().time()>datetime.datetime.strptime('9:26', '%H:%M').time())  :
                    exSignal=0
                    Signal=0
                    tf='10m'
                    df=self.dataframes['NIFTY'].iloc[-self.candleswitch[tf]:]
                    df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')#+pd.to_timedelta(1,'minutes')
                    df['dates']=df['date'].dt.date
                    dates=(df['dates'].unique())
                    df.set_index('date', inplace = True)
                    df=df.between_time('9:14', '15:30')

                    gp = df.groupby('dates')
                    dfList = []
                    for k, res in gp:
                        resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                       'high': 'max', 
                                                     'low': 'min', 
                                                     'close': 'last','volume':'sum'})
                        resampledf.reset_index(inplace=True)
                        #print(resampledf)
                        dfList.append(resampledf)
                    #print(dfList)
                    #print('hello1')
                    df1 = pd.concat(dfList,ignore_index = True)
                    #print(dfList)
                    df=df.reset_index()
                    #print('hello2')
                    lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                    #print(lasttimedate)
                    if lasttimedate==df['date'].iloc[-1]:
                        df1=df1
                    else:
                        df1=df1.iloc[:-1]

                    lvl1df1=df1[df1['date'].dt.date==dates[-1]]
                    lvl2df1=df1[df1['date'].dt.date==dates[-2]]
                    is_green=False
                    is_red=False

                    if lvl1df1['close'].iloc[-1] > lvl2df1['close'].iloc[-1]:
                        is_green=True#lvl1df1['close'].iloc[-1] > lvl2df1['close'].iloc[-1]
                        is_red=True#lvl1df1['close'].iloc[-1] < lvl2df1['close'].iloc[-1]
                    else:
                        is_green=False#lvl1df1['close'].iloc[-1] > lvl2df1['close'].iloc[-1]
                        is_red=True#lvl1df1['close'].iloc[-1] < lvl2df1['close'].iloc[-1]
                    if (self.strategyinputs[trade['strategy']]['update']) and (self.strategyinputs[trade['strategy']]['k2']==float(1)):
                        is_green=True
                        is_red=True

                    allpositions=list(self.opositions_collection.find({'user':trade['user'],'botcode':trade['botcode'], 'exittime': {'$gte': self.timestamp}}))
                    positions=list(self.opositions_collection.find({'user':trade['user'],'status':"open",'botcode':trade['botcode']}))
                    dfpositions=pd.DataFrame(positions)
                    dfallpositions=pd.DataFrame(allpositions)
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if trade['status']=='opened':                
                        if Intraday or positional or self.testmode:
                            for s in self.topbottomsymbol:
                                #print('day0')
                                if s not in list(self.dataframes.keys()):
                                    self.dataframes[s]=[]
                                if s not in list(self.deltasym.keys()):
                                    self.deltasym[s]=False
                                lent=15
                                if '3m' in trade['timeframe']:
                                    lent=9
                                if len(self.dataframes[s]) >lent:#.empty:
                                    tf=trade['timeframe']
                                    if self.strategyinputs[trade['strategy']]['update']:
                                        tf=self.strategyinputs[trade['strategy']]['timeframe']
                                    else:
                                        tf=trade['timeframe']

                                    
                                    df=self.dataframes[s].iloc[-self.candleswitch[tf]:]
                                    print('day2')
                                    #print(self.strategyinputs[trade['strategy']])
                                    df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')
                                    df['dates']=df['date'].dt.date
                                    df.set_index('date', inplace = True)
                                    gp = df.groupby('dates')
                                    dfList = []
                                    for k, res in gp:
                                        resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                                       'high': 'max', 
                                                                     'low': 'min', 
                                                                     'close': 'last','volume':'sum'})
                                        resampledf.reset_index(inplace=True)
                                        dfList.append(resampledf)
                                    df1 = pd.concat(dfList,ignore_index = True)
                                    df1['dates']=df1['date'].dt.date
                                    df=df.reset_index()
                                    lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                                    #print(lasttimedate)
                                    if lasttimedate==df['date'].iloc[-1]:
                                        df1=df1
                                    else:
                                        df1=df1.iloc[:-1]

                                    print('day3')
                                    df1['sma']=TA.SMA(df1,int(8))
                                    df1=df1[df1['dates']==df1['dates'].iloc[-1]]
                                    #df=df1
                                    self.topbottomsymbol=list(set(self.topbottomsymbol))
                                    #print(self.topbottomsymbol)
                                    df2=df1.iloc[2:]
                                    df1=df1.iloc[:2]
                                    #print(df1)
                                    high=max(df1['high'])
                                    low=min(df1['low'])
                                    #is_breakout=high<max(df2['close'])
                                    if s in self.topbottombuylist:
                                        if s not in list(self.breakoutexit.keys()):
                                            self.breakoutexit[s]=False
                                    if s in self.topbottomselllist:
                                        if s not in list(self.breakoutexitsell.keys()):
                                            self.breakoutexitsell[s]=False
                                    #print(self.breakoutexit)

                                    #print()
                                    #print('hello1')
                                    is_breakout=False
                                    is_breakoutsell=False
                                    if s in self.topbottombuylist:
                                        if len(df2)>1:
                                            is_breakout=high<float(df2['close'].iloc[-1])# and high>df2['close'].iloc[-2]
                                        else:
                                            is_breakout=high<float(df2['close'].iloc[-1])
                                        self.deltasym[s]=is_breakout
                                    print('hello2')
                                    if s in self.topbottomselllist:
                                        if len(df2)>1:
                                            is_breakoutsell=low>float(df2['close'].iloc[-1])# and high>df2['close'].iloc[-2]
                                        else:
                                            is_breakoutsell=low>float(df2['close'].iloc[-1])
                                        self.deltasym[s]=is_breakoutsell
                                    #print('hello3')
                                    #print(self.topbottombuylist)
                                    if len(df2)>2:
                                        if s in self.topbottombuylist:
                                            self.breakoutexit[s]=float(df2['sma'].iloc[-2]) > float(df2['close'].iloc[-2]) and float(df2['sma'].iloc[-1]) > float(df2['close'].iloc[-1])# and  df2['sma'].iloc[-2] > df2['sma'].iloc[-1] # and df2['sma'].iloc[-3] < df2['close'].iloc[-3] #and df2['close'].iloc[-2] < df2['open'].iloc[-2] and df2['close'].iloc[-1] < df2['open'].iloc[-1]
                                        if s in self.topbottomselllist:
                                            self.breakoutexitsell[s]=float(df2['sma'].iloc[-2] )< float(df2['close'].iloc[-2]) and float(df2['sma'].iloc[-1]) < float(df2['close'].iloc[-1]) #and  df2['sma'].iloc[-2] < df2['sma'].iloc[-1]# and df2['sma'].iloc[-3] < df2['close'].iloc[-3] #and df2['close'].iloc[-2] < df2['open'].iloc[-2] and df2['close'].iloc[-1] < df2['open'].iloc[-1]
                                    buy=(is_breakout and (is_green) and (float(df2['close'].iloc[-1]) >float(df2['sma'].iloc[-1])) and (s in self.topbottombuylist))
                                    sell= (is_breakoutsell and ( is_red) and (float(df2['close'].iloc[-1]) <float(df2['sma'].iloc[-1])) and  (s in self.topbottomselllist))
                                    #print(s,buy,sell)
                                    
                                    positions=list(self.opositions_collection.find({'user':trade['user'],'status':"open",'botcode':trade['botcode']}))
                                    spositions=len(list(self.opositions_collection.find({'user':trade['user'],'botcode':trade['botcode'],'symbol':s, 'exittime': {'$gte': self.timestamp}}))) ==0
                                    if len(positions) < (int(trade['stocks'])):
                                        if datetime.datetime.strptime('9:20', '%H:%M').time()< datetime.datetime.now().time()<datetime.datetime.strptime(trade['signalend'], '%H:%M').time():

                                            if spositions and (buy or sell):
                                                #print('bhai')
                                                if trade['positiontype']=='Equity':
                                                    self.EBUY(trade,s)#.replace('-EQ',''))
                                                if trade['positiontype']=='Options':
                                                    self.EOBUY(trade,s)#.replace('-EQ',''))
                                                if trade['positiontype']=='Future':
                                                    #print('i am tje world')
                                                    self.EFBUY(trade,s)
                #print(self.deltasym)
                self.EBUYEXIT(trade)


            except Exception as e:
                #time.sleep(1)
                print(f"Error in TOPBOTTOM: {e}")


         
    def EQSSALGO(self,trade):

        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                allpositions=list(self.opositions_collection.find({'user':trade['user'],'botcode':trade['botcode'], 'exittime': {'$gte': self.timestamp}}))
                positions=list(self.opositions_collection.find({'user':trade['user'],'status':"open",'botcode':trade['botcode']}))
                dfpositions=pd.DataFrame(positions)
                dfallpositions=pd.DataFrame(allpositions)
                Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<(datetime.datetime.strptime(trade['ExitTime'], '%H:%M') - datetime.timedelta(minutes=5)).time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                #print(trade)
                if trade['status']=='opened':                
                    if Intraday or positional or self.testmode:
                        trade['BSmode']=True
                        if 'ssteps' not in list(trade.keys()):
                            trade['ssteps']={}
                        if 'ssteps' in list(trade.keys()):

                            g=0
                            for s in list(trade['symbol']):
                                if s not in list(trade['ssteps'].keys()):
                                    trade['ssteps'][s]=1
                                    g=g+1
                            if g>0:
                                self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': trade })
                        if 'FixedLot1' not in list(trade.keys()):
                            trade['FixedLot1']='FixedLot'
                            self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': trade })
                        
                        if trade['user']=='sjgupta':
                            print(trade['symbol'])


                        for s in list(trade['symbol']):
                            ss=s+'-EQ'
                            symbol=s
                            lent=9
                            if '3m' in trade['timeframe']:
                                lent=9
                            if '1d' in trade['timeframe']:
                                if symbol not in self.dataframes1d:
                                    headers = {
                                          'Accept': 'application/json',
                                          'x-session-token': self.session_token
                                        }
                                    r = requests.get('https://api.stocknote.com/history/candleData', params={
                                  'symbolName': symbol,  'fromDate': '2021-01-01'
                                    }, headers = headers)
                                    df= (r.json()['historicalCandleData'])
                                    jj=pd.DataFrame(df)
                                    jj['datetime'] = jj['date']#pd.to_datetime(df['time'])
                                    jj['open'] = jj['open'].astype(float)
                                    jj['high'] = jj['high'].astype(float)
                                    jj['close'] = jj['close'].astype(float)
                                    jj['low'] = jj['low'].astype(float)
                                    jj['prevclose'] =jj['close'].shift(1) 
                                    jj['volume'] = jj['volume'].astype(float)
                                    jj['date']=pd.to_datetime(jj['date'])#.dt.date
                                    jj['symbol']=i
                                    self.dataframes1d[symbol]=jj# (r.json()['historicalCandleData'])
                            if ss not in list(self.dataframes.keys()):
                                self.RowMainEquitySelect(s)
                                return f'{ss} Symbol Added'
                            #print('hellow1')
                            tf=trade['timeframe']
                            #print('day1')
                            #print(self.strategyinputs)
                            if self.strategyinputs[trade['strategy']]['update']:
                                tf=self.strategyinputs[trade['strategy']]['timeframe']
                            else:
                                tf=trade['timeframe']
                            if (len(self.dataframes[ss]) >lent) and trade['timeframe']!='1d'   :#.empty:
                                

                                
                                df=self.dataframes[ss].iloc[-self.candleswitch[tf]:]
                                #print('day2')
                                #print(self.strategyinputs[trade['strategy']])

                                df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')
                                df['dates']=df['date'].dt.date
                                df.set_index('date', inplace = True)
                                gp = df.groupby('dates')
                                dfList = []
                                for k, res in gp:
                                    resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                                   'high': 'max', 
                                                                 'low': 'min', 
                                                                 'close': 'last','volume':'sum'})
                                    resampledf.reset_index(inplace=True)
                                    dfList.append(resampledf)
                                df1 = pd.concat(dfList,ignore_index = True)
                                df1['dates']=df1['date'].dt.date
                                df=df.reset_index()
                                lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                                #print(lasttimedate)
                                if lasttimedate==df['date'].iloc[-1]:
                                    df1=df1
                                else:
                                    df1=df1.iloc[:-1]
                            else:
                                df1=pd.DataFrame(self.dataframes1d[symbol])

                                #print('day3')
                                
                            Signal=0
                            if self.strategyinputs[trade['strategy']]['update']:
                                trends=self.ASSALGO(df1,int(self.strategyinputs[trade['strategy']]['r1']),int(self.strategyinputs[trade['strategy']]['r1']))
                                trends1=self.ASSALGO(df1,int(self.strategyinputs[trade['strategy']]['r2']),int(self.strategyinputs[trade['strategy']]['r2']))
                            else:    
                                #trends=self.utbot(df1,trade['r1'],trade['k1'])
                                trends=self.ASSALGO(df1,trade['r1'],trade['k1'])
                                trends1=self.ASSALGO(df1,trade['r2'],trade['k2'])

                            #print('day4')
                            exSignal=0
                            #if True :#datetime.datetime.now().time()>datetime.datetime.strptime(config['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(config['ExitTime'], '%H:%M').time()
                            if trade['Newsignal'] :
                                if trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]:
                                    if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                        Signal=1
                                    elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                        Signal=-1

                                if (trends[-trade['candle1']]==0):
                                    exSignal=1
                                elif (trends[-trade['candle1']]==1):
                                    exSignal=-1
                            elif not trade['Newsignal'] :
                                if  (trends[-trade['candle1']] ==trends[-trade['candle2']] and trends1[-trade['candle1']] ==trends1[-trade['candle2']]) or (trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]):
                                    if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                        Signal=1
                                    elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                        Signal=-1
                                if (trends[-trade['candle1']]==0):
                                    exSignal=1
                                elif (trends[-trade['candle1']]==1):
                                    exSignal=-1
                            else:
                                Signal=0
                                exSignal=0
                            #Signal=1
                            if symbol not in list(self.fractalbreakout.keys()):
                                self.fractalbreakout[symbol]=False

                            if symbol not in list(self.fractalbreakoutsell.keys()):
                                self.fractalbreakoutsell[symbol]=False
                            #print('day5')
                            trade['decision']='intrade'
                            trade['BSmode']=True
                            if exSignal==-1:
                                self.fractalbreakout[symbol]=True
                            if exSignal==1:
                                self.fractalbreakoutsell[symbol]=True
                            #print('day5')
                            trade['positiontype']='Equity'
                            positions=list(self.opositions_collection.find({'user':trade['user'],'status':"open",'botcode':trade['botcode'],'symbol':s}))
                            #print(positions)
                            #spositions=len(list(self.opositions_collection.find({'user':trade['user'],'botcode':trade['botcode'],'symbol':s, 'exittime': {'$gte': self.timestamp}}))) ==0
                            if len(positions) == 0:#(int(trade['stocks'])):
                        
                                if Signal==1:
                                    print('bhai')
                                    if trade['positiontype']=='Equity':
                                        self.EBUY(trade,s,'BUY')#.replace('-EQ',''))
                                elif Signal==-1:
                                    print('eqssalgo sell signal')
                                    if trade['positiontype']=='Equity':
                                        self.EBUY(trade,s,'SELL')#.replace('-EQ',''))
                #print(self.deltasym)
                trade['positiontype']='Equity'
                self.EBUYEXIT(trade)


            except Exception as e:
                #time.sleep(1)
                print(f"Error in EQSSALGO: {e}")

                
                
    def EMA(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or (
            trade['user'] in self.userloggedin
            and india_market_now().weekday() < self.marketdays
        ):
            try:
                if 'timetowait' not in list(trade.keys()):
                    trade['timetowait']=int(datetime.datetime.now().timestamp())
                exSignal=0
                Signal=0

                symbol=trade['symbol']
                #print(symbol)
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)

                #print(symbol)
                #print(trade)
                if trade['status']=='opened':
                    #if trade['user']=='kinguniverse129':        
                    #    print('0 ema')
                    candle_data = self._market_dataframe(symbol)
                    candle_count = len(candle_data)
                    if candle_count >0:#.empty:
                        tf='1m'
                        #print(self.strategyinputs[trade['strategy']]['update'])
                        #print()
                        if self.strategyinputs[trade['strategy']]['update']:
                            tf=self.strategyinputs[trade['strategy']]['timeframe']
                        else:
                            tf=trade['timeframe']
                        df=candle_data.iloc[-self.candleswitch[tf]:]


                        df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')#+pd.to_timedelta(1,'minutes')
                        #print(df)
                        df['dates']=df['date'].dt.date
                        df.set_index('date', inplace = True)
                        if trade['symbol']=='CRUDEOIL':
                            df=df.between_time('8:59', '23:55')
                        else:
                            df=df.between_time('9:14', '15:30')

                        gp = df.groupby('dates')
                        dfList = []
                        for k, res in gp:
                            resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                           'high': 'max', 
                                                         'low': 'min', 
                                                         'close': 'last','volume':'sum'})
                            resampledf.reset_index(inplace=True)
                            #print(resampledf)
                            dfList.append(resampledf)
                        #print(dfList)

                        df1 = pd.concat(dfList,ignore_index = True)
                        #print(dfList)
                        df=df.reset_index()
                        lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                        #print(lasttimedate)
                        if lasttimedate==df['date'].iloc[-1]:
                            df1=df1
                        else:
                            df1=df1.iloc[:-1]
                            #print('candle not as_completed')
                        #print(df1.tail(10))
                        #print(df1)
                        #if trade['user']=='kinguniverse129':        
                        #    print('1 ema')
                        Signal=0
                        if self.strategyinputs[trade['strategy']]['update']:
                            ema1=TA.EMA(df1,int(self.strategyinputs[trade['strategy']]['r1']))
                            ema2=TA.EMA(df1,int(self.strategyinputs[trade['strategy']]['k1']))
                        else:    
                            ema1=TA.EMA(df1,int(trade['r1']))
                            ema2=TA.EMA(df1,int(trade['k1']))
                        df1['short']=ema1
                        df1['long']=ema2
                        df1['result']=np.where(df1['short']>df1['long'],0,np.where(df1['short']<df1['long'],1,2))
                        #if trade['user']=='kinguniverse129':        
                        #    print(df1.tail(150))
                        trends=list(df1['result'])#self.ASSALGO(df1,trade['r1'],trade['k1'])
                        trends1=list(df1['result'])#self.ASSALGO(df1,trade['r2'],trade['k2'])
                        #print(df1)
                        #print(trade['timeframe'])
                        #print(trends[-5:])
                        #print(trends1)
                        
                        #if True :#datetime.datetime.now().time()>datetime.datetime.strptime(config['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(config['ExitTime'], '%H:%M').time()
                        signal_result = self._evaluate_143_signal(
                            trade, trends, trends1
                        )
                        Signal = signal_result['signal']
                        exSignal = signal_result['exit_signal']
                        last_candle_time = str(df1['date'].iloc[-1])
                        signal_details = dict(
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=symbol,
                            timeframe=tf,
                            candle_count=len(df1),
                            signal=Signal,
                            exit_signal=exSignal,
                            new_signal=trade.get("Newsignal"),
                            short_ema=float(df1['short'].iloc[-1]),
                            long_ema=float(df1['long'].iloc[-1]),
                            trend_current=signal_result['trend_current'],
                            trend_previous=signal_result['trend_previous'],
                            last_candle_time=last_candle_time,
                            result=signal_result['reason'],
                        )
                        self._log_decision_on_change(
                            "signal_evaluation",
                            trade,
                            (
                                last_candle_time,
                                Signal,
                                exSignal,
                                signal_result['trend_current'],
                                signal_result['trend_previous'],
                            ),
                            signal_details,
                        )
                    else:
                        trading_event(
                            "signal_rejected",
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=symbol,
                            reason="market_data_unavailable",
                            candle_count=candle_count,
                        )
                trade['decision']='intrade'
                symbol_control = self._admin_control_for_symbol(trade['symbol'])
                if symbol_control['controlmode']:
                    if symbol_control['Buytrade'] and (not symbol_control['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif symbol_control['Selltrade'] and (not symbol_control['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                #if trade['user']=='kinguniverse129':        
                #    print('3 ema')
                if 'onspot' in list(trade.keys()):
                    #print('.nothinds')
                    window = strategy_market_window(
                        trade,
                        marketdays=self.marketdays,
                        intraday_close="15:29",
                    )
                    Intraday = window["intraday"]
                    positional = (
                        strategy_market_window(
                            trade,
                            marketdays=self.marketdays,
                        )["positional"]
                    )
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print('Hello')
                            self.FEXIT(trade,Signal)

                    now_timestamp = int(datetime.datetime.now().timestamp())
                    wait_elapsed = trade['timetowait'] <= now_timestamp
                    if trade['position'] == 'out' and trade['status'] == 'opened':
                        entry_intraday = (
                            strategy_market_window(
                                trade,
                                marketdays=self.marketdays,
                            )["intraday"]
                        )
                        entry_positional = (
                            strategy_market_window(
                                trade,
                                marketdays=self.marketdays,
                            )["positional"]
                        )
                        gate_details = dict(
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=trade.get("symbol"),
                            signal=Signal,
                            position=trade.get("position"),
                            status=trade.get("status"),
                            time_window_open=bool(
                                entry_intraday
                                or entry_positional
                                or self.testmode
                            ),
                            wait_elapsed=wait_elapsed,
                            timetowait=trade.get("timetowait"),
                            now_timestamp=now_timestamp,
                            broker=self._selected_broker_for_user(
                                trade.get("user")
                            ),
                            live=trade.get("live"),
                            price_symbols=len(self.prices),
                            result=(
                                "ready_to_place_order"
                                if wait_elapsed
                                and (
                                    entry_intraday
                                    or entry_positional
                                    or self.testmode
                                )
                                and Signal in (1, -1)
                                else "blocked_before_order"
                            ),
                        )
                        self._log_decision_on_change(
                            "entry_gate_evaluation",
                            trade,
                            (
                                Signal,
                                bool(Intraday or positional or self.testmode),
                                trade.get("timetowait"),
                                trade.get("position"),
                                len(self.prices),
                            ),
                            gate_details,
                        )
                    if  trade['position']=='out' and trade['status']=='opened' and wait_elapsed:
                        entry_window = strategy_market_window(
                            trade,
                            marketdays=self.marketdays,
                        )
                        Intraday = entry_window["intraday"]
                        positional = entry_window["positional"]
                        gate_details = dict(
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=trade.get("symbol"),
                            signal=Signal,
                            position=trade.get("position"),
                            status=trade.get("status"),
                            time_window_open=bool(Intraday or positional or self.testmode),
                            timetowait=trade.get("timetowait"),
                            now_timestamp=int(datetime.datetime.now().timestamp()),
                            broker=self._selected_broker_for_user(trade.get("user")),
                            live=trade.get("live"),
                            result=(
                                "ready_to_place_order"
                                if (Intraday or positional or self.testmode)
                                and Signal in (1, -1)
                                else "blocked_before_order"
                            ),
                        )
                        self._log_decision_on_change(
                            "entry_gate_evaluation",
                            trade,
                            (
                                Signal,
                                bool(
                                    entry_intraday
                                    or entry_positional
                                    or self.testmode
                                ),
                                wait_elapsed,
                                trade.get("position"),
                                len(self.prices),
                            ),
                            gate_details,
                        )
                        if Intraday or positional or self.testmode:
                            if Signal==1:
                                print(trade)
                                self.FBUY(trade,"BUY",Signal)
                            elif Signal==-1:
                                print(trade)
                                self.FSELL(trade,"SELL",Signal)

                else:
                    #if trade['user']=='kinguniverse129':        
                    #    print('4 ema')
                    window = strategy_market_window(
                        trade,
                        marketdays=self.marketdays,
                        intraday_close="15:29",
                    )
                    Intraday = window["intraday"]
                    positional = (
                        strategy_market_window(
                            trade,
                            marketdays=self.marketdays,
                        )["positional"]
                    )
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print(trade)
                            if trade['BSmode']:
                                self.OBUYEXIT(trade,Signal,exSignal)
                                #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'out'} })
                            else:
                                self.OSELLEXIT(trade,Signal,exSignal)
                    #if trade['user']=='kinguniverse129':        
                    #    print('5 ema')
                        #print(trade)
                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        #if trade['user']=='kinguniverse129':
                        #    print(trade)
                        entry_window = strategy_market_window(
                            trade,
                            marketdays=self.marketdays,
                        )
                        Intraday = entry_window["intraday"]
                        positional = entry_window["positional"]
                        if Intraday or positional or self.testmode:
                            if trade['BSmode']:
                                #if trade['user']=='kinguniverse129':
                                #    print(Signal)
                                if Signal==1:
                                    #print('hello')
                                    print(trade)
                                    self.OBUY(trade,"CE",Signal)
                                    
                                elif Signal==-1:
                                    print(trade)
                                    self.OBUY(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                            else:
                                if Signal==1:
                                    print(trade)
                                    self.OSELL(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                                elif Signal==-1:
                                    print(trade)
                                    self.OSELL(trade,"CE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                    
            
            except Exception as e:
                self._log_strategy_exception(trade, e)
    def PEMA(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                if 'timetowait' not in list(trade.keys()):
                    trade['timetowait']=int(datetime.datetime.now().timestamp())
                exSignal=0
                Signal=0
                symbol=trade['symbol']
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)
                if trade['status']=='opened':
                    if  len(self.dataframes[symbol]) >0:#.empty:
                        tf='1m'
                        if self.strategyinputs[trade['strategy']]['update']:
                            tf=self.strategyinputs[trade['strategy']]['timeframe']
                        else:
                            tf=trade['timeframe']
                        df=self.dataframes[symbol].iloc[-self.candleswitch[tf]:]
                        df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')#+pd.to_timedelta(1,'minutes')
                        df['dates']=df['date'].dt.date
                        df.set_index('date', inplace = True)
                        if trade['symbol']=='CRUDEOIL':
                            df=df.between_time('8:59', '23:55')
                        else:
                            df=df.between_time('9:14', '15:30')
                        gp = df.groupby('dates')
                        dfList = []
                        for k, res in gp:
                            resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                           'high': 'max', 
                                                         'low': 'min', 
                                                         'close': 'last','volume':'sum'})
                            resampledf.reset_index(inplace=True)
                            dfList.append(resampledf)
                        df1 = pd.concat(dfList,ignore_index = True)
                        df=df.reset_index()
                        lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                        if lasttimedate==df['date'].iloc[-1]:
                            df1=df1
                        else:
                            df1=df1.iloc[:-1]
                        Signal=0
                        if self.strategyinputs[trade['strategy']]['update']:
                            ema1=TA.EMA(df1,int(self.strategyinputs[trade['strategy']]['r1']))
                            ema2=TA.EMA(df1,int(self.strategyinputs[trade['strategy']]['k1']))
                        else:    
                            ema1=TA.EMA(df1,int(trade['r1']))
                            ema2=TA.EMA(df1,int(trade['k1']))
                        df1['ema1']=ema1
                        df1['ema2']=ema2
                        df1['result']=np.where(df1['ema1']>df1['ema2'],np.where((df1['ema2']<df1['low'])&(df1['low']<df1['ema1'])&(df1['open']<df1['close']),0,2),np.where((df1['ema2']>df1['high'])&(df1['high']>df1['ema1'])&(df1['open']>df1['close']),1,2))

                        conditions = [
                            (df1['ema1'] > df1['ema2']) & (df1['ema2'] < df1['low']) & (df1['low'] < df1['ema1']) & (df1['open'] < df1['close']),
                            (df1['ema1'] < df1['ema2']) & (df1['ema2'] > df1['high']) & (df1['high'] > df1['ema1']) & (df1['open'] > df1['close'])
                        ]
                        choices = [0, 1]
                        df1['result1'] = np.select(conditions, choices, default=2)
                        df1['result1'] = df1['result1'].replace(2, np.nan).fillna(method='ffill').fillna(2)  # Forward fill 2 values

                        trends=list(df1['result1'])#self.ASSALGO(df1,trade['r1'],trade['k1'])
                        trends1=list(df1['result1'])#self.ASSALGO(df1,trade['r2'],trade['k2'])
                        if trade['Newsignal'] :
                            if trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]:
                                if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                    Signal=1
                                elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                    Signal=-1

                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        elif not trade['Newsignal']:
                            if  (trends[-trade['candle1']] ==trends[-trade['candle2']] and trends1[-trade['candle1']] ==trends1[-trade['candle2']]) or (trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]):
                                if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                    Signal=1
                                elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                    Signal=-1
                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        else:
                            Signal=0
                            exSignal=0
                trade['decision']='intrade'

                symbol_control = self._admin_control_for_symbol(trade['symbol'])
                if symbol_control['controlmode']:
                    if symbol_control['Buytrade'] and (not symbol_control['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif symbol_control['Selltrade'] and (not symbol_control['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                
                if 'onspot' in list(trade.keys()):
                    #print('.nothinds')
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print('Hello')
                            self.FEXIT(trade,Signal)

                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if Signal==1:
                                print(trade)
                                self.FBUY(trade,"BUY",Signal)
                            elif Signal==-1:
                                print(trade)
                                self.FSELL(trade,"SELL",Signal)

                else:
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print(trade)
                            if trade['BSmode']:
                                self.OBUYEXIT(trade,Signal,exSignal)
                            else:
                                self.OSELLEXIT(trade,Signal,exSignal)
                        
                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if trade['BSmode']:
                                if Signal==1:
                                    print(trade)
                                    self.OBUY(trade,"CE",Signal)
                                    
                                elif Signal==-1:
                                    print(trade)
                                    self.OBUY(trade,"PE",Signal)
                            else:
                                if Signal==1:
                                    print(trade)
                                    self.OSELL(trade,"PE",Signal)
                                elif Signal==-1:
                                    print(trade)
                                    self.OSELL(trade,"CE",Signal)
            except Exception as e:
                print(f"Error in PEMA: {e}")



    
    def ema1(self,series, period):
        return series.ewm(span=period, adjust=False).mean()

    def rng_size(self,df, qty, n):
        df['diff'] = df['close'].diff().abs()
        df['avrng'] = self.ema1(df['diff'], n)
        wper = (n * 2) - 1
        df['AC'] = self.ema1(df['avrng'], wper) * qty
        return df['AC']


    def rng_filt(self,df, rng_, n):
        r = rng_
        rfilt = np.full(len(df), df['close'].iloc[0])
        for i in range(1, len(df)):
            if df['close'].iloc[i] - r.iloc[i] > rfilt[i-1]:
                rfilt[i] = df['close'].iloc[i] - r.iloc[i]
            elif df['close'].iloc[i] + r.iloc[i] < rfilt[i-1]:
                rfilt[i] = df['close'].iloc[i] + r.iloc[i]
            else:
                rfilt[i] = rfilt[i-1]
        df['rfilt'] = rfilt
        df['hi_band'] = df['rfilt'] + r
        df['lo_band'] = df['rfilt'] - r
        return df

    def _is_exchange_open_for_live_order(self, exchange):
        now = india_market_now()
        if now.weekday() >= self.marketdays:
            return False, 'market holiday/weekend'
        if exchange in {'NFO', 'BFO', 'NSE', 'BSE'}:
            start = datetime.time(9, 15)
            end = datetime.time(15, 30)
        elif exchange in {'MCX', 'MFO'}:
            start = datetime.time(9, 0)
            end = datetime.time(23, 55)
        else:
            return False, f'unsupported exchange {exchange}'
        if not (start <= now.time().replace(tzinfo=None) <= end):
            return False, f'market closed for {exchange}'
        return True, ''

    def _broker_session_available(self, broker, user):
        sessions = {
            'aliceblue': getattr(self, 'alice', {}),
            'shoonya': getattr(self, 'shoonya', {}),
            'fyers': getattr(self, 'fyers', {}),
            'angelone': getattr(self, 'angelone', {}),
            'dhan': getattr(self, 'dhan', {}),
            'zerodha': getattr(self, 'zerodha', {}),
            'mofs': getattr(self, 'mofs', {}),
            'smc': getattr(self, 'smc', {}),
            'mstock': getattr(self, 'mstock', {}),
        }
        return user in sessions.get(broker, {})

    def _broker_order_response_ok(self, broker, response):
        if response is True:
            return True
        if not isinstance(response, dict):
            return response not in (None, False)
        status = str(response.get('status') or response.get('stat') or response.get('s') or '').lower()
        if response.get('brokerOrderId') or response.get('id') or response.get('order_id'):
            return True
        result = response.get('result') or response.get('data')
        if isinstance(result, list) and result:
            return any(bool(item.get('brokerOrderId') or item.get('order_id') or item.get('id')) for item in result if isinstance(item, dict))
        if isinstance(result, dict):
            return bool(result.get('brokerOrderId') or result.get('order_id') or result.get('id'))
        if status in {'ok', 'success'}:
            return True
        return False

    def _fractal_fire_state(self, trade):
        return str(trade.get('fractal_fire_state') or '')

    def _set_fractal_fire_state(self, trade, state, reason=None):
        update = {
            'fractal_fire_state': state,
            'fractal_fire_time': int(datetime.datetime.now().timestamp())
        }
        if reason:
            update['fractal_fire_reason'] = reason
        self.strategy_collection.update_one(
            {'botcode': trade['botcode'], 'user': trade['user']},
            {'$set': update}
        )
        trade.update(update)

    def _should_skip_fractal_fire(self, trade):
        state = self._fractal_fire_state(trade)
        if state in {'attempted', 'blocked'}:
            return True
        return False

    def _prepare_fractal_hedge_order_plan(self, trade, legs, exch):
        reasons = []
        planned = []
        user = trade.get('user')
        broker_info = self.broker_collection.find_one({'user': user}) or {}
        broker = broker_info.get('selectedbroker')
        live = bool(trade.get('live'))

        if not broker:
            reasons.append('selected broker missing')
        elif broker not in {'aliceblue', 'shoonya', 'fyers', 'angelone', 'dhan', 'zerodha', 'mofs', 'smc', 'mstock'}:
            reasons.append(f'unsupported broker {broker}')
        elif live and not self._broker_session_available(broker, user):
            reasons.append(f'{broker} session missing for {user}')

        if live:
            is_open, reason = self._is_exchange_open_for_live_order(exch)
            if not is_open:
                reasons.append(reason)

        try:
            underlying_price = self._get_underlying_price(trade['symbol'], self.prices.get(trade['symbol'], 0))
            if underlying_price <= 0:
                reasons.append(f"{trade['symbol']} price unavailable")
        except Exception as exc:
            underlying_price = 0
            reasons.append(f"{trade['symbol']} price unavailable: {exc}")

        future_symbol = str(trade['symbol']) if trade['symbol'] in self.Mcxlist else str(trade['symbol'] + '-I')
        try:
            future_price = float(self._get_market_price(future_symbol))
        except Exception:
            future_price = float(underlying_price or 0)

        days_head = int(float(trade.get('DaysHead', 0) or 0))
        rollover_time = trade.get('RolloverTime', '14:30')

        for raw_leg in legs:
            trad = dict(raw_leg)
            try:
                lot = int(float(trad.get('lot', 0) or 0))
            except Exception:
                lot = 0
            side = str(trad.get('side', '')).upper()
            if lot <= 0:
                reasons.append(f"invalid lot for leg {trad}")
            if side not in {'BUY', 'SELL'}:
                reasons.append(f"invalid side for leg {trad}")

            try:
                if 'FUT' in str(trad.get('option', '')):
                    option, optionlot, optionexpiry, optiontoken = self.MainFutureSelect(trade['symbol'], trad['expiry'])
                    rollover1 = datetime.datetime.strptime(f"{optionexpiry} {rollover_time}", "%Y-%m-%d %H:%M")
                    if (datetime.datetime.now() + datetime.timedelta(days=days_head)) >= rollover1:
                        trad['expiry'] = 'Next Month' if 'Current Month' in trad['expiry'] else trad['expiry']
                        option, optionlot, optionexpiry, optiontoken = self.MainFutureSelect(trade['symbol'], trad['expiry'])
                else:
                    option, optionlot, optionexpiry, optiontoken = self.MainOptionSelect(
                        trade['symbol'],
                        trad['option'],
                        int(float(trad.get('strike', 0) or 0)),
                        trad['expiry']
                    )
                    rollover1 = datetime.datetime.strptime(f"{optionexpiry} {rollover_time}", "%Y-%m-%d %H:%M")
                    if (datetime.datetime.now() + datetime.timedelta(days=days_head)) >= rollover1:
                        trad['expiry'] = 'Next Week' if 'Current Week' in trad['expiry'] else 'Next Month' if 'Current Month' in trad['expiry'] else trad['expiry']
                        option, optionlot, optionexpiry, optiontoken = self.MainOptionSelect(
                            trade['symbol'],
                            trad['option'],
                            int(float(trad.get('strike', 0) or 0)),
                            trad['expiry']
                        )
                optionlot = int(optionlot)
                optiontoken = int(optiontoken)
                if not option or optionlot <= 0 or optiontoken <= 0:
                    reasons.append(f"invalid instrument for leg {trad}")
                    continue
            except Exception as exc:
                reasons.append(f"instrument selection failed for leg {trad}: {exc}")
                continue

            price_symbol = option
            if 'FUT' in str(trad.get('option', '')) and trade['symbol'] not in self.Mcxlist:
                price_symbol = str(trade['symbol'] + '-I')
            elif 'FUT' in str(trad.get('option', '')) and trade['symbol'] in self.Mcxlist:
                price_symbol = str(trade['symbol'])

            try:
                option_price = float(self._get_market_price(price_symbol, exch, optiontoken))
                if option_price <= 0:
                    reasons.append(f"{price_symbol} price unavailable")
            except Exception as exc:
                option_price = 0
                reasons.append(f"{price_symbol} price unavailable: {exc}")

            price_context = None
            if live and broker == 'aliceblue':
                transaction_type = TransactionType.Sell if side == 'SELL' else TransactionType.Buy
                limit_price, price_context = self._aliceblue_limit_price(transaction_type, option, exch, optiontoken)
                if limit_price is None:
                    reasons.append(f"AliceBlue limit price unavailable for {option}")
            else:
                limit_price = None

            planned.append({
                'trad': trad,
                'option': option,
                'price_symbol': price_symbol,
                'optionlot': optionlot,
                'optionexpiry': optionexpiry,
                'optiontoken': optiontoken,
                'option_price': option_price,
                'underlying_price': underlying_price,
                'future_price': future_price,
                'limit_price': limit_price,
                'limit_price_context': price_context,
                'broker_info': broker_info,
                'broker': broker,
            })

        if not planned:
            reasons.append('no valid planned legs')
        return reasons, planned, broker_info

    def FRACTALNUBIATIMEHEDGEORDER(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or ((trade['user'] in self.userloggedin)):# and (datetime.date.today().weekday() < self.marketdays)):
            try:
                #print('hrel;lklk')
                if trade:
                    symbol=trade['symbol']
                    if symbol in ['BANKNIFTY','NIFTY','FINNIFTY','MIDCPNIFTY']:
                        symbol=symbol+'-I'
                    trades=list(self.opositions_collection.find({'botcode':trade['botcode'],'status':'open','user':trade['user']}))
                    if len(trades)>0:
                        if trade['position']=='out':
                            trade['position']='in'
                        #trade['position']='in'
                    #if len(trades)==0:
                    #print(' thank uy')
                    df1=pd.DataFrame()
                    #print(self.shoonya.keys())
                    #print(self.prices.keys())
                    #print(trade)
                    if True:#
                        if 'None' not in trade['method']:
                            if True:#trade['status']=='opened':
                                print(self.dataframes.keys())
                                if 'On Spot' in trade['trigger_type']:
                                    symb=trade['symbol']
                                else:
                                    symb=symbol
                                print('de;llllllll')
                                if  len(self.dataframes[symb]) >0:#.empty:

                                    tf='1m'
                                    tf=trade['timeframe']
                                    df=self.dataframes[symb].iloc[-self.candleswitch['15m']:]

                                    # Convert time to datetime
                                    print(df)  
                                    df['date'] = pd.to_datetime(df['time'], format='%d-%m-%Y %H:%M:%S')  
                                    df['dates'] = df['date'].dt.date  
                                    #print("After creating 'dates' column - unique dates:", df['dates'].unique())  

                                    # Set index to date  
                                    df.set_index('date', inplace=True)  
                                    #print("After setting date index - index type:", df.index)  

                                    # Filter trading hours based on symbol  
                                    if trade['symbol'] in self.Mcxlist:  
                                        df = df.between_time('8:59', '23:55')  
                                    else:  
                                        df = df.between_time('9:14', '15:30')  
                                    # Resample for the current timeframe  
                                    gp = df.groupby('dates')  
                                    dfList = []  
                                    for k, res in gp:    
                                        resampledf = res.resample('{}min'.format(self.timeswitch['5m']), origin='start').agg({  
                                            'open': 'first',   
                                            'high': 'max',   
                                            'low': 'min',   
                                            'close': 'last',  
                                            'volume': 'sum'  
                                        })  
                                        resampledf.reset_index(inplace=True)  
                                        dfList.append(resampledf)  
                                    df1 = pd.concat(dfList, ignore_index=True)
                                    # Adjust last row based on time  
                                    lasttimedate = df1['date'].iloc[-1] + pd.to_timedelta(int(self.timeswitch['5m'])-1, 'minutes')
                                    #print("Last original df 
                                    if lasttimedate != df.index[-1]:  
                                        df1 = df1.iloc[:-1]  
                                    # Resample for 15-minute timeframe  
                                    df5mList = []  
                                    for k, res in gp:    
                                        resampledf5m = res.resample('{}min'.format(self.timeswitch['5m']), origin='start').agg({  
                                            'open': 'first',   
                                            'high': 'max',   
                                            'low': 'min',   
                                            'close': 'last',  
                                            'volume': 'sum'  
                                        })  
                                        resampledf5m.reset_index(inplace=True)  
                                        df5mList.append(resampledf5m)  
                                    df5m1 = pd.concat(df5mList, ignore_index=True)  
                                    
                                    # Adjust last row for 15-minute timeframe  
                                    lasttimedate = df5m1['date'].iloc[-1] + pd.to_timedelta(int(self.timeswitch['15m'])-1, 'minutes')
                                    if lasttimedate != df.index[-1]:  
                                        df5m1 = df5m1.iloc[:-1]

                                    df5m1['WMA']=TA.WMA(df5m1,period=5,column='close')
                                    df5m1['EMA']=TA.EMA(df5m1,period=5,column='open')
                                    df5m1['wcross']=np.where(df5m1['WMA']>df5m1['EMA'],1,np.where(df5m1['WMA']<df5m1['EMA'],-1,0))

                                    #gp = df.groupby('dates')  
                                    df15mList = []  
                                    for k, res in gp:    
                                        resampledf15m = res.resample('{}min'.format(self.timeswitch['15m']), origin='start').agg({  
                                            'open': 'first',   
                                            'high': 'max',   
                                            'low': 'min',   
                                            'close': 'last',  
                                            'volume': 'sum'  
                                        })  
                                        resampledf15m.reset_index(inplace=True)  
                                        df15mList.append(resampledf15m)  
                                    df15m1 = pd.concat(df15mList, ignore_index=True)  
                                    
                                    # Adjust last row for 15-minute timeframe  
                                    lasttimedate = df15m1['date'].iloc[-1] + pd.to_timedelta(int(self.timeswitch['15m'])-1, 'minutes')
                                    if lasttimedate != df.index[-1]:  
                                        df15m1 = df15m1.iloc[:-1]

                                    df15m1['WMA']=TA.WMA(df15m1,period=5,column='close')
                                    df15m1['EMA']=TA.EMA(df15m1,period=5,column='open')
                                    df15m1['wcross']=np.where(df15m1['WMA']>df15m1['EMA'],1,np.where(df15m1['WMA']<df15m1['EMA'],-1,0))

                                    # Similar checkpoints can be added for the 75-minute timeframe processing  
                                    # Resample for 75-minute timeframe  
                                    #gp = df.groupby('dates')  
                                    df75mList = []  
                                    for k, res in gp:    
                                        resampledf75m = res.resample('{}min'.format(self.timeswitch['75m']), origin='start').agg({  
                                            'open': 'first',   
                                            'high': 'max',   
                                            'low': 'min',   
                                            'close': 'last',  
                                            'volume': 'sum'  
                                        })  
                                        resampledf75m.reset_index(inplace=True)  
                                        df75mList.append(resampledf75m)  
                                    df75m1 = pd.concat(df75mList, ignore_index=True) 
                                    # Reset df75m before checking last row  

                                    # Adjust last row for 75-minute timeframe  
                                    lasttimedate = df75m1['date'].iloc[-1] + pd.to_timedelta(int(self.timeswitch['75m'])-1, 'minutes')  
                                    if lasttimedate != df.index[-1]:  
                                        df75m1 = df75m1.iloc[:-1] 
                                    df75m1['WMA']=TA.WMA(df75m1,period=5,column='close')
                                    df75m1['EMA']=TA.EMA(df75m1,period=5,column='open')
                                    df75m1['wcross']=np.where(df75m1['WMA']>df75m1['EMA'],1,np.where(df75m1['WMA']<df75m1['EMA'],-1,0))


                                    df = df.reset_index()  

                                    #& (df1['wcross'].shift(1)!=1)
                                    #& (df1['wcross'].shift(1)!=-1)
                                    df1['buySignal']=1
                                    df1['sellSignal']=1
                                    if 'WMA' == trade['method']:
                                        df1['buySignal'] = np.where((df5m1['wcross'].iloc[-1]==1)  & (df15m1['wcross'].iloc[-1]==1)& (df75m1['wcross'].iloc[-1]==1),0,1)
                                        df1['sellSignal'] =np.where((df5m1['wcross'].iloc[-1]==-1) & (df15m1['wcross'].iloc[-1]==-1)& (df75m1['wcross'].iloc[-1]==-1),0,1)
                                    
                                    #print(df1)
                                    Signal=0
                                    cvd_period = 21
                                    n = 2
                                    if 'Nubia' in trade['method']:

                                        df1=nubia_indicator(df1)
                                    if 'Cvd' in trade['method']:
                                        df1=calculate_cvd(df1,cvd_period) 
                                    df1=calculate_fractals(df1)
                                    df1=alphatrend_cal(df1)
                                    #print(df1[['date','close','fractaldir','out']].tail(5))
                                    if 'Fractal' == trade['method']:
                                        df1['buySignal'] = np.where((df1['fractaldir']==1),0,1)
                                        df1['sellSignal'] =np.where((df1['fractaldir']==-1),0,1)
                                        
                                    if 'Alpha' == trade['method']:
                                        df1['buySignal'] = np.where((df1['out']==1)&(df1['out'].shift(1)==-1),0,1)
                                        df1['sellSignal'] =np.where((df1['out']==-1)&(df1['out'].shift(1)==1),0,1)
                                    elif 'Fractal & Nubia' == trade['method']:
                                        df1['buySignal'] = np.where((df1['fractaldir']==1)&(df1['ma1_trend']==0),0,1)
                                        df1['sellSignal'] =np.where((df1['fractaldir']==-1)&(df1['ma1_trend']==1),0,1)
                                    elif 'Fractal & Alpha' == trade['method']:
                                        df1['buySignal'] = np.where((df1['fractaldir']==1)&(df1['out']==1),0,1)
                                        df1['sellSignal'] =np.where((df1['fractaldir']==-1)&(df1['out']==-1),0,1)
                                    elif 'Fractal & Nubia & Laggingspan' == trade['method']:
                                        df1['buySignal'] = np.where((df1['fractaldir']==1)&(df1['ma1_trend']==0) &((df1['dirlagspan']=='up')),0,1)
                                        df1['sellSignal'] =np.where((df1['fractaldir']==-1)&(df1['ma1_trend']==1) &((df1['dirlagspan']=='dn')),0,1)
                                    elif 'Fractal & Nubia & (Laggingspan & Cvd)' == trade['method']:
                                        df1['buySignal'] = np.where((df1['fractaldir']==1)&(df1['ma1_trend']==0) &((df1['dirlagspan']=='up') &(df1['cvd']>0)),0,1)
                                        df1['sellSignal'] =np.where((df1['fractaldir']==-1)&(df1['ma1_trend']==1) &((df1['dirlagspan']=='dn')&(df1['cvd']<0)),0,1)
                                            
                                    elif 'Fractal & Nubia & (Laggingspan | Cvd)' == trade['method']:
                                        df1['buySignal'] = np.where((df1['fractaldir']==1)&(df1['ma1_trend']==0) &((df1['dirlagspan']=='up') |(df1['cvd']>0)),0,1)
                                        df1['sellSignal'] =np.where((df1['fractaldir']==-1)&(df1['ma1_trend']==1) &((df1['dirlagspan']=='dn')|(df1['cvd']<0)),0,1)
                                            

                        if trade['position']=='in':
                            #print(trade)
                            #print('zcross')
                             
                            #self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': {'position':'out'} })
                            Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                            positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                            if Intraday or positional or self.testmode:
                                Signal=False
                                if trade['slsignal']:
                                    if trade['direction_type']=='Up Side':
                                        if 'Fractal' in trade['method']:
                                            Signal=(df1['fractaldir'].iloc[-1]==-1) 
                                        elif 'Alpha' in trade['method']:
                                            Signal= (df1['out'].iloc[-1]==-1)
                                        elif 'WMA' in trade['method']:
                                            Signal= (df5m1['wcross'].iloc[-1]==-1)

                                    else:
                                        if 'Fractal' in trade['method']:
                                            Signal=(df1['fractaldir'].iloc[-1]==1) 
                                        elif 'Alpha' in trade['method']:
                                            Signal= (df1['out'].iloc[-1]==1)
                                        elif 'WMA' in trade['method']:
                                            Signal= (df5m1['wcross'].iloc[-1]==1)

                                ztrade=(list(self.opositions_collection.find({'botcode':trade['botcode'],'status':'open'})))
                                if len(ztrade)==0:
                                    if trade.get('position') == 'in':
                                        self.strategy_collection.update_one(
                                            {'botcode': trade['botcode'],'user':trade['user']},
                                            {
                                                '$set': {'position':'out'},
                                                '$unset': {
                                                    'fractal_fire_state': '',
                                                    'fractal_fire_time': '',
                                                    'fractal_fire_reason': ''
                                                }
                                            }
                                        )
                                for itrade in ztrade:
                                    pos=[]
                                    #tradec=(pd.DataFrame(itrade['pos']))
                                    pnl=0
                                    #print('112')
                                    tp1=False
                                    tp2=False
                                    sl=False
                                    for i in range(0,len(itrade['pos'])):

                                        de=(itrade['pos'][i])
                                        if de['type']!='FUT':
                                            if de['optionname'] not in list(self.prices.keys()):
                                                print('dogs1')
                                                try:
                                                    self.add_symbol_to_websocket(de['optionname'])
                                                except:
                                                    pass
                                                #print('gggg')
                                                if de['optionname'] not in list(self.prices.keys()):
                                                    #print('he')
                                                    print(self.api.get_quotes(itrade['exch'],de['optionname']))
                                                    self.prices[de['optionname']]=float(self.api.get_quotes(itrade['exch'],str(de['optiontoken']))['lp'])
                                                    print(de['optionname'])
                                                    print(self.prices)
                                            try:
                                                option_price = self._get_market_price(
                                                    de['optionname'],
                                                    itrade.get('exch'),
                                                    de.get('optiontoken')
                                                )
                                            except Exception as exc:
                                                print(
                                                    f"hedge leg price fallback failed for "
                                                    f"{de['optionname']}: {exc}"
                                                )
                                                option_price = float(
                                                    de.get('optionexit')
                                                    or de.get('optionentry')
                                                    or 0
                                                )
                                            itrade['pos'][i]['optionexit'] = option_price
                                        else:
                                            if trade['symbol'] in self.Mcxlist:
                                                futureprices=float(self._get_market_price(str(trade['symbol'])))
                                            else:
                                                try:
                                                    futureprices=float(self._get_market_price(str(trade['symbol']+'-I')))
                                                except Exception:
                                                    futureprices=self._get_underlying_price(
                                                        trade['symbol'],
                                                        self.prices.get(trade['symbol'], 0)
                                                    )
                                            
                                            itrade['pos'][i]['optionexit']=futureprices#float(self.prices[(str(trade['symbol']+'-I'))])
                                        itrade['pos'][i]['cpnl']=np.where(itrade['pos'][i]['side']=='BUY',float(float(itrade['pos'][i]['optionexit']-itrade['pos'][i]['optionentry'])*int(itrade['pos'][i]['optionlot'])*int(itrade['pos'][i]['lot'])),float(float(itrade['pos'][i]['optionentry']-itrade['pos'][i]['optionexit'])*int(itrade['pos'][i]['optionlot'])*int(itrade['pos'][i]['lot'])))
                                        itrade['pos'][i]['cpnl']=int(itrade['pos'][i]['cpnl'])
                                        pnl=pnl+itrade['pos'][i]['cpnl']
                                        itrade['pos'][i]['current_price']=self._get_underlying_price(
                                            itrade['pos'][i]['symbol'],
                                            self.prices.get(itrade['pos'][i]['symbol'], 0)
                                        )
                                        
                                        #print(itrade['pos'][i])
                                        if trade['tptrigger_type'] == 'On Spot':
                                            if trade['direction_type'] == 'Up Side':
                                                tp1=itrade['pos'][i]['current_price']>=(itrade['pos'][i]['entry_price']+trade['tp1'])
                                                tp2=itrade['pos'][i]['current_price']>=(itrade['pos'][i]['entry_price']+trade['tp2'])
                                                sl=itrade['pos'][i]['current_price']<=(itrade['pos'][i]['entry_price']-trade['sl'])
                                            elif trade['direction_type'] == 'Dn Side':
                                                tp1=itrade['pos'][i]['current_price']<=(itrade['pos'][i]['entry_price']-trade['tp1'])
                                                tp2=itrade['pos'][i]['current_price']<=(itrade['pos'][i]['entry_price']-trade['tp2'])
                                                sl=itrade['pos'][i]['current_price']>=(itrade['pos'][i]['entry_price']+trade['sl'])
                                        elif trade['tptrigger_type'] == 'On Future':
                                            if 'futureprice' in itrade['pos'][i]:
                                                if trade['direction_type'] == 'Up Side':
                                                    tp1=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']>=(itrade['pos'][i]['entry_price']+trade['tp1'])
                                                    tp2=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']>=(itrade['pos'][i]['entry_price']+trade['tp2'])
                                                    sl=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']<=(itrade['pos'][i]['entry_price']-trade['sl'])
                                                elif trade['direction_type'] == 'Dn Side':
                                                    tp1=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']<=(itrade['pos'][i]['entry_price']-trade['tp1'])
                                                    tp2=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']<=(itrade['pos'][i]['entry_price']-trade['tp2'])
                                                    sl=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']>=(itrade['pos'][i]['entry_price']+trade['sl'])


                                        if trade['sltrigger_type'] == 'On Spot':
                                            if trade['direction_type'] == 'Up Side':
                                                sl=itrade['pos'][i]['current_price']<=(itrade['pos'][i]['entry_price']-trade['sl'])
                                            elif trade['direction_type'] == 'Dn Side':
                                                sl=itrade['pos'][i]['current_price']>=(itrade['pos'][i]['entry_price']+trade['sl'])
                                        elif trade['sltrigger_type'] == 'On Future':
                                            if 'futureprice' in itrade['pos'][i]:
                                                if trade['direction_type'] == 'Up Side':
                                                    sl=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']<=(itrade['pos'][i]['entry_price']-trade['sl'])
                                                elif trade['direction_type'] == 'Dn Side':
                                                    sl=itrade['pos'][i][itrade['pos'][i]['symbol']+'-I']>=(itrade['pos'][i]['entry_price']+trade['sl'])
                                        #print('113')
                                    rollover=datetime.datetime.strptime(itrade['pos'][i]['optionexpiry'], "%Y-%m-%d")-datetime.timedelta(days=int(trade['DaysHead']))
                                    #pnl=900
                                    #rollover=str(rollover.date())
                                    #print('1131')
                                    itrade['pnl']=pnl
                                    #print('114')
                                    userr=trade['user']
                                    perlotpnl=pnl#int((pricesss-trade['optionentry'])*trade['optionlot'])
                                    
                                    #print(userr)
                                    if trade['trail']==1:
                                        if 'trail_stoploss' not in list(itrade.keys()):
                                            itrade['trail_stoploss']=0
                                        kti=trade['trail_stoploss']*2
                                        dti=int(perlotpnl/trade['trail_stoploss'])
                                        if perlotpnl>=kti and itrade['trail_stoploss']==0:
                                            itrade['trail_stoploss']=trade['trail_stoploss']
                                        elif perlotpnl>=kti and itrade['trail_stoploss'] !=0:
                                            fti=int(itrade['trail_stoploss']/trade['trail_stoploss'])
                                            #if fti >1:
                                            if (dti-fti) > 1:
                                                itrade['trail_stoploss']=itrade['trail_stoploss']+trade['trail_stoploss']
                                    else:
                                        itrade['trail_stoploss']=0
                                    if isinstance(rollover, datetime.datetime):
                                        rollover = rollover.date()
                                    else:
                                        rollover = rollover 

                                    reason=''
                                    
                                    if  (itrade['pnl'] <= (itrade['trail_stoploss'])) and itrade['trail_stoploss']!=0 and trade['trail']==1:
                                        pnl=float(itrade['pnl'])
                                        reason=reason+f'Trail Stop Loss hit Exit pnl is {str(pnl)} '
                                        Signal=True
                                    if datetime.datetime.now().time()>datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time() and trade['Intraday']:
                                        pnl=float(itrade['pnl'])
                                        reason=reason+f'Intraday hit Exit pnl is {str(pnl)} '
                                        Signal=True
                                    if datetime.datetime.now().time()>datetime.datetime.strptime(trade['RolloverTime'], '%H:%M').time() and ( ((datetime.date.today())>=rollover)):
                                        pnl=float(itrade['pnl'])
                                        reason=reason+f'Rollover time hit Exit pnl is {str(pnl)} '
                                        Signal=True
                                    if sl:
                                        pnl=float(itrade['pnl'])
                                        reason=reason+f'Stop Loss hit Exit pnl is {str(pnl)} '
                                        Signal=True
                                    if tp1:
                                        pnl=float(itrade['pnl'])
                                        reason=reason+f'Take Profit hit Exit pnl is {str(pnl)} '
                                        Signal=True
                                    if 'pnl' in list(trade.keys()) and float(itrade['pnl'])>float(trade['pnl']):
                                        pnl=float(itrade['pnl'])
                                        reason=reason+f'Profit Exit pnl is {str(pnl)} '
                                        Signal=True
                                    #print(itrade)
                                    #print('check')
                                    if Signal or (trade['status'] in ['paused','closed']) :#or (itrade['decision']=='exitit'):
                                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### USER EXIT HIT ### {reason}')
                                        #trade['lot']=0
                                        itrade['reason']=f'{str(datetime.datetime.now())} :: {userr} :: ### USER EXIT HIT ### {reason}'
                                        
                                        itrade['status']='close'
                                        trade['position']='out'

                                        for trad in itrade['pos'][::-1]:

                                            if trad['live']:
                                                lot=trad['lot']
                                                #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
                                                z=self.broker_collection.find_one({'user':trade['user']})
                                                if z['selectedbroker']=='shoonya':
                                                    dire='S'
                                                    if 'SELL' in trad['side']:
                                                        dire='B'
                                                    ret = self.shoonya[trad['user']].place_order(buy_or_sell=dire, product_type='M',
                                                        exchange=trad['exch'], tradingsymbol=trad['optionname'], 
                                                        quantity=int(trad['optionlot'])*int(trad['lot']), discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                                        retention='DAY', remarks='my_order_001')
                                                elif z['selectedbroker']=='aliceblue':
                                                    dire=TransactionType.Sell 
                                                    if 'SELL' in trad['side']:
                                                        dire=TransactionType.Buy 

                                                    instrument=self.alice[trade['user']].get_instrument_by_token(trad['exch'], trad['optiontoken'])
                                                    ret = self._place_aliceblue_limit_order(
                                                        user=trade['user'],
                                                        transaction_type=dire,
                                                        instrument=instrument,
                                                        quantity=int(trad['optionlot']) * int(trad['lot']),
                                                        product_type=ProductType.Delivery,
                                                        symbol=trad['optionname'],
                                                        exch=trad['exch'],
                                                        optiontoken=trad['optiontoken'],
                                                        order_tag='order1'
                                                    )
                                                elif z['selectedbroker']=='fyers':
                                                    print('fyers')
                                                    # Determine order type based on trade
                                                    order_type = 2  # Default to Market Order
                                                    exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                                                    Exch = exch_map.get(trad['exch'], 'NSE')  # Defaulting to NSE if not found
                                                    instrument=self.testalice.get_instrument_by_token(trad['exch'], trad['optiontoken']).name
                                                    if Exch =='NSE':
                                                        instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==trad['optiontoken'])]['exSymName'].iloc[-1]
                                                    elif Exch =='BSE':
                                                        instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==trad['optiontoken'])]['exSymName'].iloc[-1]
                                                    elif Exch =='MCX':
                                                        instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==trad['optiontoken'])]['exSymName'].iloc[-1]
                                                    product_type = 'MARGIN'
                                                    
                                                    data = {
                                                        "symbol": f"{Exch}:{instrument}",
                                                        "qty": int(trad['optionlot']) * int(trad['lot']),
                                                        "type": order_type,
                                                        "side": -1 if trad['side'] == 'BUY' else 1,
                                                        "productType": product_type,
                                                        "limitPrice": 0.0,
                                                        "stopPrice": 0.0,
                                                        "validity": "DAY",
                                                        "disclosedQty": 0,
                                                        "offlineOrder": False,
                                                        "orderTag": "tag1",
                                                        "stopLoss": 0.0,
                                                        "takeProfit": 0.0
                                                    }
                                                    ret=self.fyers[trade['user']].place_order(
                                                        data=data  
                                                    )
                                                elif z['selectedbroker']=='angelone':
                                                    instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==trad['exch'])&(self.angelone_scripts['token']==str(trad['optiontoken']))].iloc[-1]
                                                    orderparams = {
                                                        "variety": "NORMAL",
                                                        "tradingsymbol": instrument['symbol'],
                                                        "symboltoken": instrument['token'],
                                                        "transactiontype": "SELL" if trad['side'] == 'BUY' else 'BUY',
                                                        "exchange": trad['exch'],
                                                        "ordertype": "MARKET",
                                                        "producttype": "CARRYFORWARD",
                                                        "duration": "DAY",
                                                        "price": "0",
                                                        "squareoff": "0",
                                                        "stoploss": "0",
                                                        "quantity": int(trad['optionlot']) * int(trad['lot'])
                                                        }
                                                    ret = self.angelone[trade['user']].placeOrder(orderparams)
                                                elif z['selectedbroker']=='dhan':
                                                    try:    
                                                        exch=trad['exch']
                                                        if exch=='NFO' or  exch=='NSE':
                                                            exch=self.dhan[trade['user']].NSE
                                                        elif exch=='BFO' or  exch=='BSE':
                                                            exch=self.dhan[trade['user']].BSE
                                                        elif exch=='MFO' or  exch=='MCX':
                                                            exch=self.dhan[trade['user']].MCX
                                                        
                                                        ret=self.dhan[trade['user']].place_order(security_id=str(trad['optiontoken']),            # HDFC Bank
                                                            exchange_segment=trad['exch'],
                                                            transaction_type=  "SELL" if trad['side'] == 'BUY' else 'BUY' , 
                                                            quantity=int(trad['optionlot']) * int(trad['lot']),
                                                            order_type="MARKET",
                                                            product_type="MARGIN",
                                                            price=0, trigger_price=0, disclosed_quantity=0,
                                                            after_market_order=False, validity='DAY', amo_time='OPEN',
                                                            bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                                                    except Exception as e:
                                                        print(f"[ERROR] Order failed but returning True anyway: {e}")

                                                    ret = True
                                                    print(ret)
                                                elif z['selectedbroker']=='zerodha':
                                                    exch=trad['exch']
                                                    tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==trad['optiontoken'])]['tradingsymbol'].iloc[-1]
                                                    ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                                                        exchange=exch,
                                                                        transaction_type="SELL" if trad['side'] == 'BUY' else 'BUY',
                                                                        quantity=int(trad['optionlot']) * int(trad['lot']),
                                                                        variety="regular",
                                                                        order_type="MARKET",
                                                                        product="NRML",
                                                                        validity="DAY")
                                                    print(ret)
                                                elif z['selectedbroker']=='mofs':
                                                    exch=trad['exch']
                                                    exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                                                    z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                                                    
                                                    Orderinfo = {
                                                     "clientcode":z1['client_id'],      
                                                     "exchange":exch_map[exch],
                                                     "symboltoken":trad['optiontoken'],
                                                     "buyorsell":"SELL" if trad['side'] == 'BUY' else 'BUY',
                                                     "ordertype":"MARKET",
                                                     "producttype":"NORMAL",
                                                     "orderduration":"DAY",
                                                     "price":0,
                                                     "triggerprice":0,
                                                     "quantityinlot": int(trad['lot']),
                                                     "disclosedquantity":0,
                                                     "amoorder":"N",
                                                     "algoid":"",
                                                     "tag":" "
                                                    }
                                                    ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                                                elif z['selectedbroker']=='smc':
                                                    exch=trad['exch']
                                                    exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                                                    ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                                                    exchangeInstrumentID=int(trad['optiontoken']),
                                                    productType='NRML',
                                                    orderType='MARKET',
                                                    orderSide="SELL" if trad['side'] == 'BUY' else 'BUY',
                                                    timeInForce='DAY',
                                                    disclosedQuantity=0,
                                                    orderQuantity=int(trad['optionlot']) * int(trad['lot']),
                                                    limitPrice=0,
                                                    stopPrice=0,
                                                    apiOrderSource="WEBAPI",
                                                    orderUniqueIdentifier="123abc")
                                                elif z['selectedbroker']=='mstock':
                                                    exch=trad['exch']
                                                    exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                                                    Exch = exch_map.get(trad['exch'], 'NSE')  # Defaulting to NSE if not found
                                                    if Exch =='NSE':
                                                        instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==trad['optiontoken'])]['exSymName'].iloc[-1]
                                                    elif Exch =='BSE':
                                                        instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==trad['optiontoken'])]['exSymName'].iloc[-1]
                                                    elif Exch =='MCX':
                                                        instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==trad['optiontoken'])]['exSymName'].iloc[-1]
                                                    apikey=self.mstock[trade['user']]['apikey']
                                                    access_token=self.mstock[trade['user']]['access_token']
                                                    headers = {
                                                        'X-Mirae-Version': '1',
                                                        'Authorization':  f'token {apikey}:{access_token}',
                                                        'Content-Type': 'application/x-www-form-urlencoded',
                                                    }
                                                    data = {
                                                        'tradingsymbol': instrument,
                                                        'exchange': exch,
                                                        'transaction_type': "SELL" if trad['side'] == 'BUY' else 'BUY',
                                                        'order_type': 'MARKET',
                                                        'quantity': int(trad['optionlot']) * int(trad['lot']),
                                                        'product': 'NRML',
                                                        'validity': 'DAY',
                                                        'price': '0',
                                                        'variety':'regular'
                                                    }

                                                    response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                                                    ret=(response.json())
                                                print(ret)
                                        trade['timetowait']=int(datetime.datetime.now().timestamp())+int((int(self.timeswitch[trade['timeframe']])*60))
                                        print('13')

                                        itrade['exittime']=int(datetime.datetime.now().timestamp())
                                        
                                        
                                        #del itrade['_id']
                                        if 'usetype' in list(trade.keys()) and trade['usetype']==True and trade['status']!='paused':
                                            trade['status']='opened'
                                        else:
                                            trade['status']='paused'
                                        self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': trade })

                                    self.opositions_collection.update_one({'_id':itrade['_id']}, {'$set': itrade })
                                        
                                    #print(itrade)
                                    
                                    
 


                        if  trade['position']=='out' and trade['status']=='opened' and (datetime.datetime.now().time()>datetime.datetime.strptime(trade['afterentrytime'], '%H:%M').time()):
                                #print('ifddddddddddddddd')
                                #self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': {'position':'out'} })
                                Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                                positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                                if Intraday or positional or self.testmode:
                                    Signal=False
                                    sig=False
                                    try:
                                        trigger_spot_price = self._get_underlying_price(
                                            trade['symbol'],
                                            self.prices.get(trade['symbol'], 0)
                                        )
                                    except Exception as price_error:
                                        print(
                                            f"FRACTALNUBIATIMEHEDGEORDER blocked for "
                                            f"{trade.get('user')} {trade.get('botname')}: "
                                            f"{trade.get('symbol')} price unavailable: {price_error}"
                                        )
                                        return
                                    if trigger_spot_price <= 0:
                                        print(
                                            f"FRACTALNUBIATIMEHEDGEORDER blocked for "
                                            f"{trade.get('user')} {trade.get('botname')}: "
                                            f"{trade.get('symbol')} price unavailable"
                                        )
                                        return
                                    if 'None' not in trade['method']:
                                        if trade['direction_type']=='Up Side':
                                            if df1['buySignal'].iloc[-1]==0:# and df1['buySignal'].iloc[-2]==1 :
                                                if trade['trigger_price']!=0:
                                                    if trade['comparator_type'] == '>=' and trade['direction_type']=='Up Side' and trade['trigger_price']> trigger_spot_price:
                                                        sig=True
                                                    elif trade['comparator_type'] == '<=' and trade['direction_type']=='Up Side' and  trade['trigger_price'] < trigger_spot_price:
                                                        sig=True
                                                else:
                                                    sig=True
                                                if sig:
                                                    Signal=True
                                        else:
                                            if df1['sellSignal'].iloc[-1]==0:# and df1['sellSignal'].iloc[-2]==1:
                                                if trade['trigger_price']!=0:
                                                    if trade['comparator_type'] == '>=' and trade['direction_type']=='Dn Side' and trade['trigger_price']< trigger_spot_price:
                                                        sig=True
                                                    if trade['comparator_type'] == '<=' and trade['direction_type']=='Dn Side' and  trade['trigger_price']> trigger_spot_price:
                                                        sig=True
                                                    #print('cond4')
                                                else:
                                                    sig=True
                                                if sig:
                                                    Signal=True
                                    else:
                                        if trade['trigger_price']!=0 and 'None' in trade['method']:
                                            if trade['comparator_type'] == '>=' and trade['direction_type']=='Up Side' and trade['trigger_price']< trigger_spot_price:
                                                Signal=True
                                    
                                            if trade['comparator_type'] == '>=' and trade['direction_type']=='Dn Side' and trade['trigger_price']< trigger_spot_price:
                                                Signal=True
                                            if trade['comparator_type'] == '<=' and trade['direction_type']=='Up Side' and trade['trigger_price']> trigger_spot_price:
                                                Signal=True
                                            if trade['comparator_type'] == '<=' and trade['direction_type']=='Dn Side' and trade['trigger_price']> trigger_spot_price:
                                                Signal=True
                                    if Signal:
                                        if self._should_skip_fractal_fire(trade):
                                            return
                                        poss=[]
                                        ztrade=pd.DataFrame(trade['legs'])
                                        ztrade=ztrade.sort_values(by='side')

                                        
                                        exch = 'NFO' if trade['symbol'] not in self.Mcxlist else 'MCX'
                                        if trade['symbol']=='SENSEX':
                                            exch='BFO'

                                        for trad in ztrade.to_dict('records'):
                                            try:
                                                if 'FUT' in trad['option']:
                                                    option, optionlot, optionexpiry, optiontoken = self.MainFutureSelect(trade['symbol'], trad['expiry'])
                                                    print(f"MainFutureSelect returned: option={option}, lot={optionlot}, expiry={optionexpiry}, token={optiontoken}")
                                                    if not optionexpiry:
                                                        continue
                                                    rollover1 = datetime.datetime.strptime(f"{optionexpiry} {trade['RolloverTime']}", "%Y-%m-%d %H:%M")
                                                    if (datetime.datetime.now() + datetime.timedelta(days=trade['DaysHead'])) >= rollover1:
                                                        trad['expiry'] = 'Next Month' if 'Current Month' in trad['expiry'] else trad['expiry']
                                                        option, optionlot, optionexpiry, optiontoken = self.MainFutureSelect(trade['symbol'], trad['expiry'])
                                                        print(f"MainFutureSelect updated returned: option={option}, lot={optionlot}, expiry={optionexpiry}, token={optiontoken}")
                                                else:
                                                    option, optionlot, optionexpiry, optiontoken = self.MainOptionSelect( trade['symbol'], trad['option'], int(trad['strike']), trad['expiry']                              )
                                                    print(f"MainOptionSelect returned: option={option}, lot={optionlot}, expiry={optionexpiry}, token={optiontoken}")
                                                    if not optionexpiry:
                                                        continue
                                                    rollover1 = datetime.datetime.strptime(f"{optionexpiry} {trade['RolloverTime']}", "%Y-%m-%d %H:%M")
                                                    if (datetime.datetime.now() + datetime.timedelta(days=trade['DaysHead'])) >= rollover1:
                                                        trad['expiry'] = 'Next Week' if 'Current Week' in trad['expiry'] else 'Next Month' if 'Current Month' in trad['expiry'] else trad['expiry']
                                                        option, optionlot, optionexpiry, optiontoken = self.MainOptionSelect(  trade['symbol'], trad['option'], int(trad['strike']), trad['expiry'])
                                            except Exception as exc:
                                                print(f"FRACTALNUBIATIMEHEDGEORDER instrument preview failed for {trad}: {exc}")
                                                
                                        preflight_reasons, planned_legs, z = self._prepare_fractal_hedge_order_plan(
                                            trade,
                                            ztrade.to_dict('records'),
                                            exch
                                        )
                                        if preflight_reasons:
                                            block_reason = '; '.join(preflight_reasons)
                                            self._set_fractal_fire_state(trade, 'blocked', block_reason)
                                            print(
                                                f"FRACTALNUBIATIMEHEDGEORDER blocked for "
                                                f"{trade.get('user')} {trade.get('botname')}: "
                                                f"{block_reason}"
                                            )
                                            return

                                        self._set_fractal_fire_state(trade, 'attempted')

                                        hedge_total_pnl = 0
                                        for planned_leg in planned_legs:
                                            trad = planned_leg['trad']
                                            option = planned_leg['option']
                                            option1 = option
                                            price_symbol = planned_leg['price_symbol']
                                            optionlot = planned_leg['optionlot']
                                            optionexpiry = planned_leg['optionexpiry']
                                            optiontoken = planned_leg['optiontoken']
                                            futureprices = planned_leg['future_price']
                                            underlying_price = planned_leg['underlying_price']
                                            option_price = planned_leg['option_price']
                                            z = planned_leg['broker_info']
                                            print(f"Planned leg passed: option={option}, lot={optionlot}, expiry={optionexpiry}, token={optiontoken}")
                                            if z.get('selectedbroker') == 'aliceblue':
                                                print(
                                                    f"FRACTALNUBIATIMEHEDGEORDER AliceBlue limit plan: "
                                                    f"side={trad['side']}, option={option}, "
                                                    f"limit={planned_leg.get('limit_price')}, "
                                                    f"context={planned_leg.get('limit_price_context')}"
                                                )
                                                
                                            if trade['live']:
                                                lot=optionlot
                                                #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
                                                if z['selectedbroker']=='shoonya':
                                                    dire='B'
                                                    if 'SELL' in trad['side']:
                                                        dire='S'
                                                    ret = self.shoonya[trade['user']].place_order(buy_or_sell=dire, product_type='M',
                                                        exchange=exch, tradingsymbol=option, 
                                                        quantity=int(optionlot)*int(trad['lot']), discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                                        retention='DAY', remarks='my_order_001')
                                                elif z['selectedbroker']=='aliceblue':
                                                    dire=TransactionType.Buy 
                                                    if 'SELL' in trad['side']:
                                                        dire=TransactionType.Sell 

                                                    instrument=self.alice[trade['user']].get_instrument_by_token(exch, optiontoken)
                                                    ret = self._place_aliceblue_limit_order(
                                                        user=trade['user'],
                                                        transaction_type=dire,
                                                        instrument=instrument,
                                                        quantity=int(optionlot) * int(trad['lot']),
                                                        product_type=ProductType.Delivery,
                                                        symbol=option,
                                                        exch=exch,
                                                        optiontoken=optiontoken,
                                                        order_tag='order1'
                                                    )
                                                elif z['selectedbroker']=='fyers':
                                                    print('fyers')
                                                    order_type = 2  # Default to Market Order
                                                    instrument=self.testalice.get_instrument_by_token(exch, optiontoken).name
                                                    product_type = 'MARGIN'
                                                    #exch_map = {'MCX': 'MCX_FO', 'MFO': 'MCX_FO', 'NFO': 'NSE_FO', 'NSE': 'NSE_EQ', 'BSE': 'BSE_EQ', 'BFO': 'BSE_FO'}
                                                    
                                                    exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                                                    Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                                                    # Prepare order data
                                                    if Exch =='NSE':
                                                        instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                                                    elif Exch =='BSE':
                                                        instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                                                    elif Exch =='MCX':
                                                        instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                                                    data = {
                                                        "symbol": f"{Exch}:{instrument}",
                                                        "qty": int(optionlot) * int(trad['lot']),
                                                        "type": order_type,
                                                        "side": 1 if trad['side'] == 'BUY' else -1,
                                                        "productType": product_type,
                                                        "limitPrice": 0.0,
                                                        "stopPrice": 0.0,
                                                        "validity": "DAY",
                                                        "disclosedQty": 0,
                                                        "offlineOrder": False,
                                                        "orderTag": "tag1",
                                                        "stopLoss": 0.0,
                                                        "takeProfit": 0.0
                                                    }
                                                    ret=self.fyers[trade['user']].place_order(
                                                        data=data  
                                                    ) 
                                                    print(ret)
                                                elif z['selectedbroker']=='angelone':
                                                    print('angle')
                                                    #print(exch)
                                                    instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==exch)&(self.angelone_scripts['token']==str(optiontoken))].iloc[-1]
                                                    print(instrument)
                                                    orderparams = {
                                                        "variety": "NORMAL",
                                                        "tradingsymbol": instrument['symbol'],
                                                        "symboltoken": instrument['token'],
                                                        "transactiontype": "BUY" if trad['side'] == 'BUY' else 'SELL',
                                                        "exchange": exch,
                                                        "ordertype": "MARKET",
                                                        "producttype": "CARRYFORWARD",
                                                        "duration": "DAY",
                                                        "price": "0",
                                                        "squareoff": "0",
                                                        "stoploss": "0",
                                                        "quantity": int(optionlot) * int(trad['lot'])
                                                        }
                                                    # Method 1: Place an order and return the order ID
                                                    ret = self.angelone[trade['user']].placeOrder(orderparams)
                                                    print(ret)
                                                elif z['selectedbroker']=='dhan':
                                                    try:
                                                        if exch=='NFO':
                                                            exch1='NSE_FNO'
                                                        elif exch=='NSE':
                                                            exch1='NSE_EQ'
                                                        elif exch=='BFO':
                                                            exch1='BSE_FNO'
                                                        elif   exch=='BSE':
                                                            exch1='BSE_EQ'
                                                        elif exch=='MFO' or  exch=='MCX':
                                                            exch1='MCX_COMM'
                                                        
                                                        ret=self.dhan[trade['user']].place_order(security_id=str(optiontoken),            # HDFC Bank
                                                            exchange_segment=exch1,
                                                            transaction_type='BUY'  if trad['side'] == 'BUY' else 'SELL', 
                                                            quantity=int(optionlot) * int(trad['lot']),
                                                            order_type="MARKET",
                                                            product_type="MARGIN",
                                                            price=0, trigger_price=0, disclosed_quantity=0,
                                                            after_market_order=False, validity='DAY', amo_time='OPEN',
                                                            bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                                                        print(ret)
                                                    except Exception as e:
                                                        print(f"[ERROR] Order failed but returning True anyway: {e}")

                                                    ret = True
                                                    print(ret)
                                                elif z['selectedbroker']=='zerodha':
                                                    #exch=trad['exch']
                                                    tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==optiontoken)]['tradingsymbol'].iloc[-1]
                                                    ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                                                        exchange=exch,
                                                                        transaction_type="BUY" if trad['side'] == 'BUY' else 'SELL',
                                                                        quantity=int(optionlot) * int(trad['lot']),
                                                                        variety="regular",
                                                                        order_type="MARKET",
                                                                        product="NRML",
                                                                        validity="DAY")
                                                    print(ret)
                                                elif z['selectedbroker']=='mofs':
                                                    #exch=trad['exch']
                                                    exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                                                    z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                                                    
                                                    Orderinfo = {
                                                     "clientcode":z1['client_id'],      
                                                     "exchange":exch_map[exch],
                                                     "symboltoken":optiontoken,
                                                     "buyorsell":"BUY" if trad['side'] == 'BUY' else 'SELL',
                                                     "ordertype":"MARKET",
                                                     "producttype":"NORMAL",
                                                     "orderduration":"DAY",
                                                     "price":0,
                                                     "triggerprice":0,
                                                     "quantityinlot": int(trad['lot']),
                                                     "disclosedquantity":0,
                                                     "amoorder":"N",
                                                     "algoid":"",
                                                     "tag":" "
                                                    }
                                                    ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                                                elif z['selectedbroker']=='smc':
                                                    #exch=trad['exch']
                                                    exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                                                    ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                                                    exchangeInstrumentID=int(optiontoken),
                                                    productType='NRML',
                                                    orderType='MARKET',
                                                    orderSide="BUY" if trad['side'] == 'BUY' else 'BUY',
                                                    timeInForce='DAY',
                                                    disclosedQuantity=0,
                                                    orderQuantity=int(optionlot) * int(trad['lot']),
                                                    limitPrice=0,
                                                    stopPrice=0,
                                                    apiOrderSource="WEBAPI",
                                                    orderUniqueIdentifier="123abc")
                                                elif z['selectedbroker']=='mstock':
                                                    #exch=trad['exch']
                                                    exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                                                    Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                                                    if Exch =='NSE':
                                                        instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                                                    elif Exch =='BSE':
                                                        instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                                                    elif Exch =='MCX':
                                                        instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                                                    apikey=self.mstock[trade['user']]['apikey']
                                                    access_token=self.mstock[trade['user']]['access_token']
                                                    headers = {
                                                        'X-Mirae-Version': '1',
                                                        'Authorization':  f'token {apikey}:{access_token}',
                                                        'Content-Type': 'application/x-www-form-urlencoded',
                                                    }
                                                    data = {
                                                        'tradingsymbol': instrument,
                                                        'exchange': exch,
                                                        'transaction_type': "SELL" if trad['side'] == 'BUY' else 'BUY',
                                                        'order_type': 'MARKET',
                                                        'quantity': int(optionlot) * int(trad['lot']),
                                                        'product': 'NRML',
                                                        'validity': 'DAY',
                                                        'price': '0',
                                                        'variety':'regular'
                                                    }

                                                    response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                                                    ret=(response.json())
                                                print('12')
                                                print(ret)
                                                if trade['live'] and not self._broker_order_response_ok(z['selectedbroker'], ret):
                                                    print(
                                                        f"FRACTALNUBIATIMEHEDGEORDER order rejected for "
                                                        f"{trade.get('user')} {option}: {ret}"
                                                    )
                                                    return
                                                #print(self.prices)
                                                option1=option
                                                print('uuuuuuuuuuuuuuuu')
                                                future_symbol = str(trade['symbol']) if trade['symbol'] in self.Mcxlist else str(trade['symbol'] + '-I')
                                                try:
                                                    futureprices = float(self._get_market_price(future_symbol))
                                                except Exception:
                                                    futureprices = self._get_underlying_price(
                                                        trade['symbol'],
                                                        self.prices.get(trade['symbol'], 0)
                                                    )
                                                
                                                if 'FUT' in trad['option'] and trade['symbol'] not in self.Mcxlist:
                                                    option=(str(trade['symbol']+'-I'))
                                                elif 'FUT' in trad['option'] and trade['symbol'] in self.Mcxlist:
                                                    option=(str(trade['symbol']))
                                                print('hellll')
                                            underlying_price = self._get_underlying_price(
                                                trade['symbol'],
                                                self.prices.get(trade['symbol'], 0)
                                            )
                                            try:
                                                option_price = float(self._get_market_price(option, exch, optiontoken))
                                            except Exception:
                                                option_price = float(self.prices.get(option, 0) or 0)
                                            try:
                                                current_option_price = float(
                                                    self._get_market_price(option1, exch, optiontoken)
                                                )
                                            except Exception:
                                                current_option_price = option_price

                                            leg_pnl = (
                                                (current_option_price - option_price)
                                                * int(optionlot)
                                                * int(trad['lot'])
                                            )
                                            if trad['side'] == 'SELL':
                                                leg_pnl = (
                                                    (option_price - current_option_price)
                                                    * int(optionlot)
                                                    * int(trad['lot'])
                                                )
                                            leg_pnl = int(leg_pnl)

                                            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':trade['symbol'],'entry_price':float(underlying_price)
                                            ,'side':trad['side'],'status':"open",'pnl':leg_pnl,'cpnl':leg_pnl,'lot':trad['lot'],'type':trad['option'],
                                            'optionentry':float(option_price),'optionexit':float(current_option_price),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
                                            'optionname':str(option1), 'pnlhalf':0,"decision":"intrade",'live':trade['live'],
                                            'exch':exch,'current_price':float(underlying_price),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,'futureprice':futureprices}

                                            poss.append(pos)
                                            hedge_total_pnl += leg_pnl
                                            print(pos)

                                        pos1={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':trade['symbol'],'status':"open",'live':trade['live'],'pnl':int(hedge_total_pnl),
                                                'exch':exch,'botcode':trade['botcode'],'pos':poss}

                                        self.opositions_collection.insert_one(pos1)
                                        self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position': 'in'}})
            except Exception as e:
                print(f"Error in FRACTALNUBIATIMEHEDGEORDER: {e} ") 


    def RF(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                if 'timetowait' not in list(trade.keys()):
                    trade['timetowait']=int(datetime.datetime.now().timestamp())
                exSignal=0
                Signal=0

                symbol=trade['symbol']
                #print(symbol)
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)

                #print(symbol)
                #print(trade)
                if trade['status']=='opened':
                    if  len(self.dataframes[symbol]) >0:#.empty:
                        tf='1m'
                        #print(self.strategyinputs[trade['strategy']]['update'])
                        #print()
                        
                        #print('stage0')
                        #print(self.strategyinputs)
                        #print(self.strategyinputs[trade['strategy']])
                        if self.strategyinputs[trade['strategy']]['update']:
                            tf=self.strategyinputs[trade['strategy']]['timeframe']
                        else:
                            tf=trade['timeframe']
                        df=self.dataframes[symbol].iloc[-2000:]


                        df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')#+pd.to_timedelta(1,'minutes')
                        #print(df)
                        df['dates']=df['date'].dt.date
                        df.set_index('date', inplace = True)
                        if trade['symbol']=='CRUDEOIL':
                            df=df.between_time('8:59', '23:55')
                        else:
                            df=df.between_time('9:14', '15:30')

                        gp = df.groupby('dates')
                        dfList = []
                        for k, res in gp:
                            resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                           'high': 'max', 
                                                         'low': 'min', 
                                                         'close': 'last','volume':'sum'})
                            resampledf.reset_index(inplace=True)
                            #print(resampledf)
                            dfList.append(resampledf)
                        #print(dfList)


                        df1 = pd.concat(dfList,ignore_index = True)
                        #print(dfList)
                        df=df.reset_index()
                        lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                        #print(lasttimedate)
                        if lasttimedate==df['date'].iloc[-1]:
                            df1=df1
                        else:
                            df1=df1.iloc[:-1]
                            #print('candle not as_completed')
                        #print(df1.tail(10))
                        #print(df1)
                        #print('stage1')
                        Signal=0
                        if self.strategyinputs[trade['strategy']]['update']:
                            rng_per = int(self.strategyinputs[trade['strategy']]['r1'])
                            rng_qty = float(self.strategyinputs[trade['strategy']]['k1'])

                        else:    
                            rng_per = int(trade['r1'])
                            rng_qty = float(trade['k1'])


                        
                        # Calculate range size
                        #print(trade)
                        #print(rng_qty,rng_per)
                        #print('stage2')
                        df1['rng_'] = self.rng_size(df1, rng_qty, rng_per)

                        # Apply range filter
                        df1 = self.rng_filt(df1, df1['rng_'], rng_per)

                        # Determine direction conditions
                        # Determine direction conditions
                        df1['fdir'] = 0
                        df1['fdir'] = np.where(df1['rfilt'] > df1['rfilt'].shift(1), 1, df1['fdir'])
                        df1['fdir'] = np.where(df1['rfilt'] < df1['rfilt'].shift(1), -1, df1['fdir'])
                        df1['upward'] = np.where(df1['fdir'] == 1, 1, 0)
                        df1['downward'] = np.where(df1['fdir'] == -1, 1, 0)

                        # Determine trading conditions
                        df1['longCond'] = ((df1['close'] > df1['rfilt']) & (df1['close'] > df1['close'].shift(1)) & (df1['upward'] > 0)) | \
                                          ((df1['close'] > df1['rfilt']) & (df1['close'] < df1['close'].shift(1)) & (df1['upward'] > 0))
                        df1['shortCond'] = ((df1['close'] < df1['rfilt']) & (df1['close'] < df1['close'].shift(1)) & (df1['downward'] > 0)) | \
                                           ((df1['close'] < df1['rfilt']) & (df1['close'] > df1['close'].shift(1)) & (df1['downward'] > 0))

                        # Generate buy (0) and sell (1) signals
                        df1['result'] = np.where(df1['longCond'], 0, np.where(df1['shortCond'], 1, np.nan))
                        df1['result'] = df1['result'].ffill()
                        #print(df1)
                        #print(df1[df1['result']!=df1['result'].shift(1)])

                        trends=list(df1['result'])#self.ASSALGO(df1,trade['r1'],trade['k1'])
                        trends1=list(df1['result'])#self.ASSALGO(df1,trade['r2'],trade['k2'])
                        #print(df1)
                        #print(trade['timeframe'])
                        #print(trends[-100:])
                        #print(trade['symbol'])
                        ##print(trends1)
                        #print(trade['symbol'])
                        
                        #print('stage4')
                        #if True :#datetime.datetime.now().time()>datetime.datetime.strptime(config['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(config['ExitTime'], '%H:%M').time()
                        if trade['Newsignal'] :
                            if trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]:
                                if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                    Signal=1
                                elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                    Signal=-1

                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        elif not trade['Newsignal']:
                            if  (trends[-trade['candle1']] ==trends[-trade['candle2']] and trends1[-trade['candle1']] ==trends1[-trade['candle2']]) or (trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]):
                                if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                    Signal=1
                                elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                    Signal=-1
                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        else:
                            Signal=0
                            exSignal=0

                #print('stage5')
                trade['decision']='intrade'
                symbol_control = self._admin_control_for_symbol(trade['symbol'])
                if symbol_control['controlmode']:
                    if symbol_control['Buytrade'] and (not symbol_control['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif symbol_control['Selltrade'] and (not symbol_control['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                
                if 'onspot' in list(trade.keys()):
                    #print('.nothinds')
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print('Hello')
                            self.FEXIT(trade,Signal)

                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if Signal==1:
                                print(trade)
                                self.FBUY(trade,"BUY",Signal)
                            elif Signal==-1:
                                print(trade)
                                self.FSELL(trade,"SELL",Signal)

                else:
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print(trade)
                            if trade['BSmode']:
                                self.OBUYEXIT(trade,Signal,exSignal)
                                #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'out'} })
                            else:
                                self.OSELLEXIT(trade,Signal,exSignal)
                        
                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        #print(trade)
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if trade['BSmode']:
                                if Signal==1:
                                    print(trade)
                                    self.OBUY(trade,"CE",Signal)
                                    
                                elif Signal==-1:
                                    print(trade)
                                    self.OBUY(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                            else:
                                if Signal==1:
                                    print(trade)
                                    self.OSELL(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                                elif Signal==-1:
                                    print(trade)
                                    self.OSELL(trade,"CE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                    
            
            except Exception as e:
                print(f"Error in RF: {e}")
    def UTBOT(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                if 'timetowait' not in list(trade.keys()):
                    trade['timetowait']=int(datetime.datetime.now().timestamp())

                symbol=trade['symbol']
                #print(symbol)
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)
                    #print(symbol)
                Signal=0
                exSignal=0
                if trade['status']=='opened':
                    if  len(self.dataframes[symbol]) >0:#.empty:
                        tf='1m'
                        if self.strategyinputs[trade['strategy']]['update']:
                            tf=self.strategyinputs[trade['strategy']]['timeframe']
                        else:
                            tf=trade['timeframe']
                        df=self.dataframes[symbol].iloc[-self.candleswitch[tf]:]

                        df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')#+pd.to_timedelta(1,'minutes')
                        df['dates']=df['date'].dt.date
                        df.set_index('date', inplace = True)
                        if trade['symbol']=='CRUDEOIL':
                            df=df.between_time('8:59', '23:55')
                        else:
                            df=df.between_time('9:14', '15:30')

                        gp = df.groupby('dates')
                        dfList = []
                        tf='1m'
                        if self.strategyinputs[trade['strategy']]['update']:
                            tf=self.strategyinputs[trade['strategy']]['timeframe']
                        else:
                            tf=trade['timeframe']
                        for k, res in gp:
                            resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                           'high': 'max', 
                                                         'low': 'min', 
                                                         'close': 'last','volume':'sum'})
                            resampledf.reset_index(inplace=True)
                            #print(resampledf)
                            dfList.append(resampledf)
                        #print(dfList)

                        df1 = pd.concat(dfList,ignore_index = True)
                        #print(dfList)
                        df=df.reset_index()
                        lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                        #print(lasttimedate)
                        if lasttimedate==df['date'].iloc[-1]:
                            df1=df1
                        else:
                            df1=df1.iloc[:-1]
                        Signal=0
                        if self.strategyinputs[trade['strategy']]['update']:
                            trends=self.utbot(df1,int(self.strategyinputs[trade['strategy']]['r1']),int(self.strategyinputs[trade['strategy']]['r1']))
                        else:    
                            trends=self.utbot(df1,int(trade['r1']),int(trade['k1']))
                        #trends=self.utbot(df1,trade['r1'],trade['k1'])
                        exSignal=0
                        if trade['Newsignal'] :
                            if trends[-trade['candle1']] !=trends[-trade['candle2']]:
                                if (trends[-trade['candle1']]==0):
                                    Signal=1
                                elif (trends[-trade['candle1']]==1):
                                    Signal=-1
                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        elif not trade['Newsignal'] :
                            if  (trends[-trade['candle1']] ==trends[-trade['candle2']] ) or (trends[-trade['candle1']] !=trends[-trade['candle2']]):
                                if (trends[-trade['candle1']]==0) :
                                    Signal=1
                                elif (trends[-trade['candle1']]==1):
                                    Signal=-1
                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        else:
                            Signal=0
                            exSignal=0
                trade['decision']='intrade'
                symbol_control = self._admin_control_for_symbol(trade['symbol'])
                if symbol_control['controlmode']:
                    if symbol_control['Buytrade'] and (not symbol_control['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif symbol_control['Selltrade'] and (not symbol_control['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                    
                if 'onspot' in list(trade.keys()):
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            self.FEXIT(trade,Signal)
                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if Signal==1:
                                print(trade)
                                self.FBUY(trade,"BUY",Signal)
                            elif Signal==-1:
                                print(trade)
                                self.FSELL(trade,"SELL",Signal)
                else:
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode :
                        if trade['position']=='in':
                            if trade['BSmode']:
                                self.OBUYEXIT(trade,Signal,exSignal)
                            else:
                                self.OSELLEXIT(trade,Signal,exSignal)
                    
                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        #print(trade)
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if trade['BSmode']:
                                if Signal==1:
                                    print(trade)
                                    self.OBUY(trade,"CE",Signal)
                                    
                                elif Signal==-1:
                                    print(trade)
                                    self.OBUY(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                            else:
                                if Signal==1:
                                    print(trade)
                                    self.OSELL(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                                elif Signal==-1:
                                    print(trade)
                                    self.OSELL(trade,"CE",Signal)
            except Exception as e:
                print(f"Error in UTBOT: {e}")

    def SSALGO(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
            try:
                if 'timetowait' not in list(trade.keys()):
                    trade['timetowait']=int(datetime.datetime.now().timestamp())

                symbol=trade['symbol']
                Signal=0
                exSignal=0
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)
                if trade['status']=='opened':
                    candle_count = len(self.dataframes.get(symbol, []))
                    if candle_count >0:#.empty:
                        tf='1m'
                        if self.strategyinputs[trade['strategy']]['update']:
                            tf=self.strategyinputs[trade['strategy']]['timeframe']
                        else:
                            tf=trade['timeframe']
                        df=self.dataframes[symbol].iloc[-self.candleswitch[tf]:]
                        df['date']=pd.to_datetime(df['time'],format='%d-%m-%Y %H:%M:%S')#+pd.to_timedelta(1,'minutes')
                        df['dates']=df['date'].dt.date
                        df.set_index('date', inplace = True)
                        if trade['symbol']=='CRUDEOIL':
                            df=df.between_time('8:59', '23:55')
                        else:
                            df=df.between_time('9:14', '15:30')

                        gp = df.groupby('dates')
                        dfList = []
                        
                        for k, res in gp:
                            resampledf = res.resample('{}min'.format(self.timeswitch[tf]), origin='start').agg({'open': 'first', 
                                                           'high': 'max', 
                                                         'low': 'min', 
                                                         'close': 'last','volume':'sum'})
                            resampledf.reset_index(inplace=True)
                            #print(resampledf)
                            dfList.append(resampledf)
                        #print(dfList)

                        df1 = pd.concat(dfList,ignore_index = True)
                        #print(dfList)
                        df=df.reset_index()
                        lasttimedate=df1['date'].iloc[-1]+pd.to_timedelta(int(self.timeswitch[tf])-1,'minutes')
                        #print(lasttimedate)
                        if lasttimedate==df['date'].iloc[-1]:
                            df1=df1
                        else:
                            df1=df1.iloc[:-1]
                            #print('candle not as_completed')
                        #print(df1.tail(10))
                        Signal=0
                        if self.strategyinputs[trade['strategy']]['update']:
                            trends=self.ASSALGO(df1,int(self.strategyinputs[trade['strategy']]['r1']),int(self.strategyinputs[trade['strategy']]['r1']))
                            trends1=self.ASSALGO(df1,int(self.strategyinputs[trade['strategy']]['r2']),int(self.strategyinputs[trade['strategy']]['r2']))
                        else:    
                            #trends=self.utbot(df1,trade['r1'],trade['k1'])
                            trends=self.ASSALGO(df1,trade['r1'],trade['k1'])
                            trends1=self.ASSALGO(df1,trade['r2'],trade['k2'])
                        #print(trade['timeframe'])
                        #print(trends)
                        #print(trends1)
                        exSignal=0
                        #if True :#datetime.datetime.now().time()>datetime.datetime.strptime(config['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(config['ExitTime'], '%H:%M').time()
                        if trade['Newsignal'] :
                            if trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]:
                                if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                    Signal=1
                                elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                    Signal=-1

                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        elif not trade['Newsignal'] :
                            if  (trends[-trade['candle1']] ==trends[-trade['candle2']] and trends1[-trade['candle1']] ==trends1[-trade['candle2']]) or (trends[-trade['candle1']] !=trends[-trade['candle2']]  and  trends1[-trade['candle1']] !=trends1[-trade['candle2']]):
                                if (trends[-trade['candle1']]==0) and (trends1[-trade['candle1']]==0):
                                    Signal=1
                                elif (trends[-trade['candle1']]==1) and (trends1[-trade['candle1']]==1):
                                    Signal=-1
                            if (trends[-trade['candle1']]==0):
                                exSignal=1
                            elif (trends[-trade['candle1']]==1):
                                exSignal=-1
                        else:
                            Signal=0
                            exSignal=0
                        trading_event(
                            "signal_evaluation",
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=symbol,
                            timeframe=tf,
                            candle_count=len(df1),
                            signal=Signal,
                            exit_signal=exSignal,
                            new_signal=trade.get("Newsignal"),
                            trend_current=trends[-trade['candle1']],
                            trend_previous=trends[-trade['candle2']],
                            trend2_current=trends1[-trade['candle1']],
                            trend2_previous=trends1[-trade['candle2']],
                            result="signal_generated" if Signal in (1, -1) else "entry_condition_false",
                        )
                    else:
                        trading_event(
                            "signal_rejected",
                            user=trade.get("user"),
                            strategy_id=trade.get("botcode"),
                            strategy=trade.get("strategy"),
                            symbol=symbol,
                            reason="market_data_unavailable",
                            candle_count=candle_count,
                        )
                trade['decision']='intrade'
                symbol_control = self._admin_control_for_symbol(trade['symbol'])
                if symbol_control['controlmode']:
                    if symbol_control['Buytrade'] and (not symbol_control['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif symbol_control['Selltrade'] and (not symbol_control['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                
                if 'onspot' in list(trade.keys()):
                    #print('.nothinds')
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode:
                        if trade['position']=='in':
                            #print('Hello')
                            self.FEXIT(trade,Signal)

                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if Signal==1:
                                print(trade)
                                self.FBUY(trade,"BUY",Signal)
                            elif Signal==-1:
                                print(trade)
                                self.FSELL(trade,"SELL",Signal)

                else:
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                    if Intraday or positional or self.testmode :
                        if trade['position']=='in':
                            #print(trade)
                            if trade['BSmode']:
                                self.OBUYEXIT(trade,Signal,exSignal)
                                #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'out'} })
                            else:
                                self.OSELLEXIT(trade,Signal,exSignal)
                    
                    if  trade['position']=='out' and trade['status']=='opened' and trade['timetowait'] <= int(datetime.datetime.now().timestamp()):
                        #print(trade)
                        Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                        if Intraday or positional or self.testmode:
                            if trade['BSmode']:
                                if Signal==1:
                                    print(trade)
                                    self.OBUY(trade,"CE",Signal)
                                    
                                elif Signal==-1:
                                    print(trade)
                                    self.OBUY(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                            else:
                                if Signal==1:
                                    print(trade)
                                    self.OSELL(trade,"PE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                                elif Signal==-1:
                                    print(trade)
                                    self.OSELL(trade,"CE",Signal)
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })
                    
            
            except Exception as e:
                print(f"Error in SSALGO: {e}")
                trading_exception(
                    "strategy_evaluation_error",
                    e,
                    user=trade.get("user"),
                    strategy_id=trade.get("botcode"),
                    strategy=trade.get("strategy"),
                    symbol=trade.get("symbol"),
                )

    def FBUY(self,trade,OTYPE,Signal):
        try:
            if trade.get('live') and trade.get('entry_order_state') in {
                'attempted', 'broker_failed'
            }:
                return
            
            option,optionlot,optionexpiry,optiontoken=self.MainFutureSelect(trade['symbol'],trade['Expiry'])
            #print( option,optionlot,optionexpiry)
            rollover1=datetime.datetime.strptime(str(optionexpiry)+' '+str(trade['RolloverTime']), "%Y-%m-%d %H:%M")
            if (datetime.datetime.now()+datetime.timedelta(days=trade['DaysHead']))>=rollover1:
                
                if 'Current Month' in trade['Expiry']:
                    trade['Expiry']='Next Month'

                #option,optionlot,optionexpiry=self.MainOptionSelect(trade['symbol'],OTYPE, trade['strike'],trade['Expiry']) 
                option,optionlot,optionexpiry,optiontoken=self.MainFutureSelect(trade['symbol'],trade['Expiry'])            
            trade['option']=option
            exch='NFO'
            if trade['symbol']=='CRUDEOIL':
                exch='MCX'
            if trade['symbol']=='SENSEX':
                exch='BFO'
            instrument = self._make_instrument(
                exch,
                optiontoken,
                trade['symbol'],
                option,
                optionlot
            )
            print(instrument)
            symbol=self._symboltransformmonthfut(trade['Expiry'],trade['symbol'])
            #self.add_symbol_to_websocket(option)
            #print(self.prices)
            pricesss=0
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[symbol])

            broker_order_results = []
            if trade['live']:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode'], 'user': trade['user']},
                    {
                        '$set': {
                            'entry_order_state': 'attempted',
                            'entry_order_time': int(
                                datetime.datetime.now().timestamp()
                            ),
                        }
                    },
                )
                trade['entry_order_state'] = 'attempted'
                lot=trade['lot']
                if lot>20:
                    totalquant=[trade['slicing']]*int(lot/trade['slicing'])
                    if (lot%trade['slicing'])>0 :
                        totalquant.append(lot%trade['slicing'])
                    for quant in totalquant:
                        #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                        z=self.broker_collection.find_one({'user':trade['user']})
                        ret = None
                        if z['selectedbroker']=='shoonya':
                            ret = self.shoonya[trade['user']].place_order(buy_or_sell='B', product_type='M',
                                exchange=exch, tradingsymbol=option,
                                quantity=int(optionlot)*int(quant), discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                retention='DAY', remarks='my_order_001')
                        elif z['selectedbroker']=='aliceblue':
                            instrument=self.alice[trade['user']].get_instrument_by_token(exch, optiontoken)
                            ret = self._place_aliceblue_limit_order(
                                user=trade['user'],
                                transaction_type=TransactionType.Buy,
                                instrument=instrument,
                                quantity=int(optionlot) * int(quant),
                                product_type=ProductType.Delivery,
                                symbol=option,
                                exch=exch,
                                optiontoken=optiontoken,
                                order_tag='order1'
                            )
                        elif z['selectedbroker']=='fyers':
                            print('fyers')
                            order_type = 2  # Default to Market Order
                            instrument=self.testalice.get_instrument_by_token(exch, optiontoken).name
                            product_type = 'MARGIN'
                            exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                            Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                            if Exch =='NSE':
                                instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='BSE':
                                instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='MCX':
                                instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            
                            data = {
                                "symbol": f"{Exch}:{instrument}",
                                "qty": int(optionlot) * int(quant),
                                "type": order_type,
                                "side": 1,
                                "productType": product_type,
                                "limitPrice": 0.0,
                                "stopPrice": 0.0,
                                "validity": "DAY",
                                "disclosedQty": 0,
                                "offlineOrder": False,
                                "orderTag": "tag1",
                                "stopLoss": 0.0,
                                "takeProfit": 0.0
                            }
                            ret=self.fyers[trade['user']].place_order(
                                data=data  
                            )
                        elif z['selectedbroker']=='angelone':
                            instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==exch)&(self.angelone_scripts['token']==str(optiontoken))].iloc[-1]
                            orderparams = {
                                "variety": "NORMAL",
                                "tradingsymbol": instrument['symbol'],
                                "symboltoken": instrument['token'],
                                "transactiontype":  'BUY',
                                "exchange": exch,
                                "ordertype": "MARKET",
                                "producttype": "CARRYFORWARD",
                                "duration": "DAY",
                                "price": "0",
                                "squareoff": "0",
                                "stoploss": "0",
                                "quantity": int(optionlot) * int(quant)
                                }
                            # Method 1: Place an order and return the order ID
                            ret = self.angelone[trade['user']].placeOrder(orderparams) 
                        elif z['selectedbroker']=='dhan':
                            #exch=trad['exch']
                            try:
                                if exch=='NFO':
                                    exch1='NSE_FNO'
                                elif exch=='NSE':
                                    exch1='NSE_EQ'
                                elif exch=='BFO':
                                    exch1='BSE_FNO'
                                elif   exch=='BSE':
                                    exch1='BSE_EQ'
                                elif exch=='MFO' or  exch=='MCX':
                                    exch1='MCX_COMM'
                                
                                ret=self.dhan[trade['user']].place_order(security_id=str(optiontoken),            # HDFC Bank
                                    exchange_segment=exch1,
                                    transaction_type= 'BUY', 
                                    quantity=int(optionlot) * int(quant),
                                    order_type="MARKET",
                                    product_type="MARGIN",
                                    price=0, trigger_price=0, disclosed_quantity=0,
                        after_market_order=False, validity='DAY', amo_time='OPEN',
                        bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                                print(ret)
                            except Exception as e:
                                ret = {
                                    'status': 'failed',
                                    'error': str(e),
                                }
                                print(f"[ERROR] Dhan future BUY failed: {e}")
                        elif z['selectedbroker']=='zerodha':
                            tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==optiontoken)]['tradingsymbol'].iloc[-1]
                            ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                                exchange=exch,
                                                transaction_type="BUY" ,
                                                quantity=int(optionlot) * int(quant),
                                                variety="regular",
                                                order_type="MARKET",
                                                product="NRML",
                                                validity="DAY")
                            print(ret)
                        elif z['selectedbroker']=='mofs':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                            z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                            
                            Orderinfo = {
                             "clientcode":z1['client_id'],      
                             "exchange":exch_map[exch],
                             "symboltoken":optiontoken,
                             "buyorsell":"BUY" ,
                             "ordertype":"MARKET",
                             "producttype":"NORMAL",
                             "orderduration":"DAY",
                             "price":0,
                             "triggerprice":0,
                             "quantityinlot": int(quant),
                             "disclosedquantity":0,
                             "amoorder":"N",
                             "algoid":"",
                             "tag":" "
                            }
                            ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                        elif z['selectedbroker']=='smc':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                            ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                            exchangeInstrumentID=int(optiontoken),
                            productType='NRML',
                            orderType='MARKET',
                            orderSide='BUY',
                            timeInForce='DAY',
                            disclosedQuantity=0,
                            orderQuantity=int(optionlot) * int(quant),
                            limitPrice=0,
                            stopPrice=0,
                            apiOrderSource="WEBAPI",
                            orderUniqueIdentifier="123abc")
                        elif z['selectedbroker']=='mstock':
                            #exch=trad['exch']
                            exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                            Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                            if Exch =='NSE':
                                instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='BSE':
                                instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='MCX':
                                instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            apikey=self.mstock[trade['user']]['apikey']
                            access_token=self.mstock[trade['user']]['access_token']
                            headers = {
                                'X-Mirae-Version': '1',
                                'Authorization':  f'token {apikey}:{access_token}',
                                'Content-Type': 'application/x-www-form-urlencoded',
                            }
                            data = {
                                'tradingsymbol': instrument,
                                'exchange': exch,
                                'transaction_type': 'BUY',
                                'order_type': 'MARKET',
                                'quantity': int(optionlot) * int(quant),
                                'product': 'NRML',
                                'validity': 'DAY',
                                'price': '0',
                                'variety':'regular'
                            }

                            response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                            ret=(response.json())
                        broker_order_results.append(
                            self._record_broker_order_result(
                                trade,
                                z['selectedbroker'],
                                ret,
                                'BUY',
                                option,
                                int(optionlot) * int(quant),
                            )
                        )

                else:
                    #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')

                    z=self.broker_collection.find_one({'user':trade['user']})
                    ret = None
                    if z['selectedbroker']=='shoonya':
                        ret = self.shoonya[trade['user']].place_order(buy_or_sell='B', product_type='M',
                            exchange=exch, tradingsymbol=option, 
                            quantity=int(optionlot)*int(trade['lot']), discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    elif z['selectedbroker']=='aliceblue':
                        instrument=self.alice[trade['user']].get_instrument_by_token(exch, optiontoken)
                        ret = self._place_aliceblue_limit_order(
                            user=trade['user'],
                            transaction_type=TransactionType.Buy,
                            instrument=instrument,
                            quantity=int(optionlot) * int(trade['lot']),
                            product_type=ProductType.Delivery,
                            symbol=option,
                            exch=exch,
                            optiontoken=optiontoken,
                            order_tag='order1'
                        )

                    elif z['selectedbroker']=='fyers':
                        print('fyers')
                        # Determine order type based on trade
                        order_type = 2  # Default to Market Order
                        
                        instrument=self.testalice.get_instrument_by_token(exch, optiontoken).name
                        # Determine product type based on position type
                        product_type = 'MARGIN'

                        exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                        Exch = exch_map.get(exch, 'NSE')
                        if Exch =='NSE':
                            instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='BSE':
                            instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='MCX':
                            instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        
                        # Prepare order data
                        data = {
                            "symbol": f"{Exch}:{instrument}",
                            "qty": int(optionlot) * int(trade['lot']),
                            "type": order_type,
                            "side": 1 ,
                            "productType": product_type,
                            "limitPrice": 0.0,
                            "stopPrice": 0.0,
                            "validity": "DAY",
                            "disclosedQty": 0,
                            "offlineOrder": False,
                            "orderTag": "tag1",
                            "stopLoss": 0.0,
                            "takeProfit": 0.0
                        }
                        ret=self.fyers[trade['user']].place_order(
                            data=data  
                        ) 
                    elif z['selectedbroker']=='angelone':
                        instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==exch)&(self.angelone_scripts['token']==str(optiontoken))].iloc[-1]
                        orderparams = {
                            "variety": "NORMAL",
                            "tradingsymbol": instrument['symbol'],
                            "symboltoken": instrument['token'],
                            "transactiontype": "BUY" ,
                            "exchange": exch,
                            "ordertype": "MARKET",
                            "producttype": "CARRYFORWARD",
                            "duration": "DAY",
                            "price": "0",
                            "squareoff": "0",
                            "stoploss": "0",
                            "quantity": int(optionlot) * int(trade['lot'])
                            }
                        # Method 1: Place an order and return the order ID
                        ret = self.angelone[trade['user']].placeOrder(orderparams) 
                    elif z['selectedbroker']=='dhan':
                        try:
                            #exch=trad['exch']
                            if exch=='NFO':
                                exch1='NSE_FNO'
                            elif exch=='NSE':
                                exch1='NSE_EQ'
                            elif exch=='BFO':
                                exch1='BSE_FNO'
                            elif   exch=='BSE':
                                exch1='BSE_EQ'
                            elif exch=='MFO' or  exch=='MCX':
                                exch1='MCX_COMM'
                            
                            ret=self.dhan[trade['user']].place_order(security_id=str(optiontoken),            # HDFC Bank
                                exchange_segment=exch1,
                                transaction_type=  'BUY', 
                                quantity=int(optionlot) * int(trade['lot']),
                                order_type="MARKET",
                                product_type="MARGIN",
                                price=0, trigger_price=0, disclosed_quantity=0,
                        after_market_order=False, validity='DAY', amo_time='OPEN',
                        bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                            print(ret)
                        except Exception as e:
                            ret = {
                                'status': 'failed',
                                'error': str(e),
                            }
                            print(f"[ERROR] Dhan future BUY failed: {e}")
                    elif z['selectedbroker']=='zerodha':
                        tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==optiontoken)]['tradingsymbol'].iloc[-1]
                        ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                            exchange=exch,
                                            transaction_type="BUY" ,
                                            quantity=int(optionlot) * int(trade['lot']),
                                            variety="regular",
                                            order_type="MARKET",
                                            product="NRML",
                                            validity="DAY")
                        print(ret)
                    elif z['selectedbroker']=='mofs':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                            z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                            
                            Orderinfo = {
                             "clientcode":z1['client_id'],      
                             "exchange":exch_map[exch],
                             "symboltoken":optiontoken,
                             "buyorsell":"BUY" ,
                             "ordertype":"MARKET",
                             "producttype":"NORMAL",
                             "orderduration":"DAY",
                             "price":0,
                             "triggerprice":0,
                             "quantityinlot":int(trade['lot']),
                             "disclosedquantity":0,
                             "amoorder":"N",
                             "algoid":"",
                             "tag":" "
                            }
                            ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                        #print(self.prices)
                    elif z['selectedbroker']=='smc':
                        #exch=trad['exch']
                        exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                        ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                        exchangeInstrumentID=int(optiontoken),
                        productType='NRML',
                        orderType='MARKET',
                        orderSide='BUY',
                        timeInForce='DAY',
                        disclosedQuantity=0,
                        orderQuantity=int(optionlot) * int(trade['lot']),
                        limitPrice=0,
                        stopPrice=0,
                        apiOrderSource="WEBAPI",
                        orderUniqueIdentifier="123abc")
                    elif z['selectedbroker']=='mstock':
                        #exch=trad['exch']
                        exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                        Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                        if Exch =='NSE':
                            instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='BSE':
                            instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='MCX':
                            instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        apikey=self.mstock[trade['user']]['apikey']
                        access_token=self.mstock[trade['user']]['access_token']
                        headers = {
                            'X-Mirae-Version': '1',
                            'Authorization':  f'token {apikey}:{access_token}',
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }
                        data = {
                            'tradingsymbol': instrument,
                            'exchange': exch,
                            'transaction_type': 'BUY',
                            'order_type': 'MARKET',
                            'quantity': int(optionlot) * int(trade['lot']),
                            'product': 'NRML',
                            'validity': 'DAY',
                            'price': '0',
                            'variety':'regular'
                        }

                        response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                        ret=(response.json())
                    broker_order_results.append(
                        self._record_broker_order_result(
                            trade,
                            z['selectedbroker'],
                            ret,
                            'BUY',
                            option,
                            int(optionlot) * int(trade['lot']),
                        )
                    )
                print(ret)

            broker_order_success = (not trade['live']) or (
                bool(broker_order_results)
                and all(result['success'] for result in broker_order_results)
            )
            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(pricesss)
            ,'side':OTYPE,'status':"open" if broker_order_success else "broker_failed",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(option), 'pnlhalf':0,"decision":"intrade" if broker_order_success else "broker_failed",'BSmode':True,'entrycond':Signal,'exitcond':self.oppocond(Signal),'entry_id':self._next_entry_id(),'live':trade['live'],
            'exch':exch,'current_price':float(pricesss),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,
            'broker_order_results': broker_order_results}
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one(
                {'botcode': trade['botcode'], 'user': trade['user']},
                {
                    '$set': {
                        'position': 'in' if broker_order_success else 'out',
                        'entry_order_state': (
                            'success' if broker_order_success else 'broker_failed'
                        ),
                        'last_broker_order_error': (
                            [] if broker_order_success else broker_order_results
                        ),
                    }
                },
            )
        except Exception as e:
            self.strategy_collection.update_one(
                {
                    'botcode': trade.get('botcode'),
                    'user': trade.get('user'),
                },
                {
                    '$set': {
                        'position': 'out',
                        'entry_order_state': 'preflight_failed',
                        'last_broker_order_error': str(e),
                        'last_broker_order_error_time': int(
                            datetime.datetime.now().timestamp()
                        ),
                    }
                },
            )
            trading_exception(
                "future_entry_order_failed",
                e,
                user=trade.get("user"),
                strategy_id=trade.get("botcode"),
                strategy=trade.get("strategy"),
                symbol=trade.get("symbol"),
                side="BUY",
            )
            print(f"Error in FBUY: {e}")

        
    def FSELL(self,trade,OTYPE,Signal):
        try:
            if trade.get('live') and trade.get('entry_order_state') in {
                'attempted', 'broker_failed'
            }:
                return
            
            option,optionlot,optionexpiry,optiontoken=self.MainFutureSelect(trade['symbol'],trade['Expiry'])
            #print( option,optionlot,optionexpiry)
            rollover1=datetime.datetime.strptime(str(optionexpiry)+' '+str(trade['RolloverTime']), "%Y-%m-%d %H:%M")
            if (datetime.datetime.now()+datetime.timedelta(days=trade['DaysHead']))>=rollover1:
                
                if 'Current Month' in trade['Expiry']:
                    trade['Expiry']='Next Month'

                #option,optionlot,optionexpiry=self.MainOptionSelect(trade['symbol'],OTYPE, trade['strike'],trade['Expiry']) 
                option,optionlot,optionexpiry,optiontoken=self.MainFutureSelect(trade['symbol'],trade['Expiry'])            

            symbol=self._symboltransformmonthfut(trade['Expiry'],trade['symbol'])
            #self.add_symbol_to_websocket(option)
            #print(self.prices)
            trade['option']=option
            exch='NFO'
            if trade['symbol']=='CRUDEOIL':
                exch='MCX'
            if trade['symbol']=='SENSEX':
                exch='BFO'
            instrument = self._make_instrument(
                exch,
                optiontoken,
                trade['symbol'],
                option,
                optionlot
            )
            print(instrument)
            broker_order_results = []
            if trade['live']:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode'], 'user': trade['user']},
                    {
                        '$set': {
                            'entry_order_state': 'attempted',
                            'entry_order_time': int(
                                datetime.datetime.now().timestamp()
                            ),
                        }
                    },
                )
                trade['entry_order_state'] = 'attempted'
                lot=trade['lot']
                if lot>20:
                    totalquant=[trade['slicing']]*int(lot/trade['slicing'])
                    if (lot%trade['slicing'])>0 :
                        totalquant.append(lot%trade['slicing'])
                    for quant in totalquant:
                        #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                        z=self.broker_collection.find_one({'user':trade['user']})
                        ret = None
                        if z['selectedbroker']=='shoonya':
                            ret = self.shoonya[trade['user']].place_order(buy_or_sell='S', product_type='M',
                                exchange=exch, tradingsymbol=option, 
                                quantity=int(optionlot)*int(quant), discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                retention='DAY', remarks='my_order_001')
                        elif z['selectedbroker']=='aliceblue':
                            instrument=self.alice[trade['user']].get_instrument_by_token(exch, optiontoken)
                            ret = self._place_aliceblue_limit_order(
                                user=trade['user'],
                                transaction_type=TransactionType.Sell,
                                instrument=instrument,
                                quantity=int(optionlot) * int(quant),
                                product_type=ProductType.Delivery,
                                symbol=option,
                                exch=exch,
                                optiontoken=optiontoken,
                                order_tag='order1'
                            )
                        elif z['selectedbroker']=='fyers':
                            print('fyers')
                            # Determine order type based on trade
                            order_type = 2  # Default to Market Order
                            
                            instrument=self.testalice.get_instrument_by_token(exch, optiontoken).name
                            # Determine product type based on position type
                            product_type = 'MARGIN'

                            exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                            Exch = exch_map.get(exch, 'NSE')
                            if Exch =='NSE':
                                instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='BSE':
                                instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='MCX':
                                instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            
                            # Prepare order data
                            data = {
                                "symbol": f"{Exch}:{instrument}",
                                "qty": int(optionlot) * int(   quant),
                                "type": order_type,
                                "side":  -1,
                                "productType": product_type,
                                "limitPrice": 0.0,
                                "stopPrice": 0.0,
                                "validity": "DAY",
                                "disclosedQty": 0,
                                "offlineOrder": False,
                                "orderTag": "tag1",
                                "stopLoss": 0.0,
                                "takeProfit": 0.0
                            }
                            ret=self.fyers[trade['user']].place_order(
                                data=data  
                            )
                        elif z['selectedbroker']=='angelone':
                            instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==exch)&(self.angelone_scripts['token']==str(optiontoken))].iloc[-1]
                            orderparams = {
                                "variety": "NORMAL",
                                "tradingsymbol": instrument['symbol'],
                                "symboltoken": instrument['token'],
                                "transactiontype": "SELL" ,
                                "exchange":exch,
                                "ordertype": "MARKET",
                                "producttype": "CARRYFORWARD",
                                "duration": "DAY",
                                "price": "0",
                                "squareoff": "0",
                                "stoploss": "0",
                                "quantity": int(optionlot) * int(   quant)
                                }
                            # Method 1: Place an order and return the order ID
                            ret = self.angelone[trade['user']].placeOrder(orderparams) 
                        elif z['selectedbroker']=='dhan':
                            try:
                                #exch=trad['exch']
                                if exch=='NFO':
                                    exch1='NSE_FNO'
                                elif exch=='NSE':
                                    exch1='NSE_EQ'
                                elif exch=='BFO':
                                    exch1='BSE_FNO'
                                elif   exch=='BSE':
                                    exch1='BSE_EQ'
                                elif exch=='MFO' or  exch=='MCX':
                                    exch1='MCX_COMM'
                                
                                ret=self.dhan[trade['user']].place_order(security_id=str(optiontoken),            # HDFC Bank
                                    exchange_segment=exch1,
                                    transaction_type= "SELL" , 
                                    quantity=int(optionlot) * int(quant),
                                    order_type="MARKET",
                                    product_type="MARGIN",
                                    price=0, trigger_price=0, disclosed_quantity=0,
                                        after_market_order=False, validity='DAY', amo_time='OPEN',
                                        bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                                print(ret)
                            except Exception as e:
                                ret = {
                                    'status': 'failed',
                                    'error': str(e),
                                }
                                print(f"[ERROR] Dhan future SELL failed: {e}")
                        elif z['selectedbroker']=='zerodha':
                            tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==optiontoken)]['tradingsymbol'].iloc[-1]
                            ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                                exchange=exch,
                                                transaction_type= 'SELL',
                                                quantity=int(optionlot) * int(quant),
                                                variety="regular",
                                                order_type="MARKET",
                                                product="NRML",
                                                validity="DAY")
                            print(ret)

                        elif z['selectedbroker']=='mofs':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                            z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                            
                            Orderinfo = {
                             "clientcode":z1['client_id'],      
                             "exchange":exch_map[exch],
                             "symboltoken":optiontoken,
                             "buyorsell":"SELL" ,
                             "ordertype":"MARKET",
                             "producttype":"NORMAL",
                             "orderduration":"DAY",
                             "price":0,
                             "triggerprice":0,
                             "quantityinlot": int(quant),
                             "disclosedquantity":0,
                             "amoorder":"N",
                             "algoid":"",
                             "tag":" "
                            }
                            ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                        elif z['selectedbroker']=='smc':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                            ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                            exchangeInstrumentID=int(optiontoken),
                            productType='NRML',
                            orderType='MARKET',
                            orderSide='SELL',
                            timeInForce='DAY',
                            disclosedQuantity=0,
                            orderQuantity=int(optionlot) * int(quant),
                            limitPrice=0,
                            stopPrice=0,
                            apiOrderSource="WEBAPI",
                            orderUniqueIdentifier="123abc")
                        elif z['selectedbroker']=='mstock':
                            #exch=trad['exch']
                            exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                            Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                            if Exch =='NSE':
                                instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='BSE':
                                instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            elif Exch =='MCX':
                                instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                            apikey=self.mstock[trade['user']]['apikey']
                            access_token=self.mstock[trade['user']]['access_token']
                            headers = {
                                'X-Mirae-Version': '1',
                                'Authorization':  f'token {apikey}:{access_token}',
                                'Content-Type': 'application/x-www-form-urlencoded',
                            }
                            data = {
                                'tradingsymbol': instrument,
                                'exchange': exch,
                                'transaction_type': 'SELL',
                                'order_type': 'MARKET',
                                'quantity': int(optionlot) * int(quant),
                                'product': 'NRML',
                                'validity': 'DAY',
                                'price': '0',
                                'variety':'regular'
                            }

                            response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                            ret=(response.json())
                        broker_order_results.append(
                            self._record_broker_order_result(
                                trade,
                                z['selectedbroker'],
                                ret,
                                'SELL',
                                option,
                                int(optionlot) * int(quant),
                            )
                        )
                else:
                    #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')

                    z=self.broker_collection.find_one({'user':trade['user']})
                    ret = None
                    if z['selectedbroker']=='shoonya':
                        ret = self.shoonya[trade['user']].place_order(buy_or_sell='S', product_type='M',
                            exchange=exch, tradingsymbol=option, 
                            quantity=int(optionlot)*int(trade['lot']), discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    elif z['selectedbroker']=='aliceblue':
                        instrument=self.alice[trade['user']].get_instrument_by_token(exch, optiontoken)
                        ret = self._place_aliceblue_limit_order(
                            user=trade['user'],
                            transaction_type=TransactionType.Sell,
                            instrument=instrument,
                            quantity=int(optionlot) * int(trade['lot']),
                            product_type=ProductType.Delivery,
                            symbol=option,
                            exch=exch,
                            optiontoken=optiontoken,
                            order_tag='order1'
                        )
                    elif z['selectedbroker']=='fyers':
                        print('fyers')
                        # Determine order type based on trade
                        order_type = 2  # Default to Market Order
                        exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                        Exch = exch_map.get(exch, 'NSE')
                        # Determine product type based on position type
                        product_type = 'MARGIN'
   
                        
                        instrument=self.testalice.get_instrument_by_token(exch, optiontoken).name
                        # Prepare order data
                        if Exch =='NSE':
                            instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='BSE':
                            instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='MCX':
                            instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        
                        data = {
                            "symbol": f"{Exch}:{instrument}",
                            "qty": int(optionlot) * int(   trade['lot']),
                            "type": order_type,
                            "side":-1,
                            "productType": product_type,
                            "limitPrice": 0.0,
                            "stopPrice": 0.0,
                            "validity": "DAY",
                            "disclosedQty": 0,
                            "offlineOrder": False,
                            "orderTag": "tag1",
                            "stopLoss": 0.0,
                            "takeProfit": 0.0
                        }
                        ret=self.fyers[trade['user']].place_order(
                            data=data  
                        ) 
                    elif z['selectedbroker']=='angelone':
                        instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==exch)&(self.angelone_scripts['token']==str(optiontoken))].iloc[-1]
                        orderparams = {
                            "variety": "NORMAL",
                            "tradingsymbol": instrument['symbol'],
                            "symboltoken": instrument['token'],
                            "transactiontype": "SELL",
                            "exchange": exch,
                            "ordertype": "MARKET",
                            "producttype": "CARRYFORWARD",
                            "duration": "DAY",
                            "price": "0",
                            "squareoff": "0",
                            "stoploss": "0",
                            "quantity": int(trade['optionlot'])*int(trade['lot'])
                            }
                        # Method 1: Place an order and return the order ID
                        ret = self.angelone[trade['user']].placeOrder(orderparams) 
                    elif z['selectedbroker']=='dhan':
                        try:
                            if exch=='NFO':
                                exch1='NSE_FNO'
                            elif exch=='NSE':
                                exch1='NSE_EQ'
                            elif exch=='BFO':
                                exch1='BSE_FNO'
                            elif   exch=='BSE':
                                exch1='BSE_EQ'
                            elif exch=='MFO' or  exch=='MCX':
                                exch1='MCX_COMM'
                            
                            ret=self.dhan[trade['user']].place_order(security_id=str(optiontoken),            # HDFC Bank
                                exchange_segment=exch1,
                                transaction_type= "SELL" , 
                                quantity=int(optionlot) * int(trade['lot']),
                                order_type="MARKET",
                                product_type="MARGIN",
                                price=0, trigger_price=0, disclosed_quantity=0,
                                after_market_order=False, validity='DAY', amo_time='OPEN',
                                bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                            print(ret)
                        except Exception as e:
                            ret = {
                                'status': 'failed',
                                'error': str(e),
                            }
                            print(f"[ERROR] Dhan future SELL failed: {e}")
                    elif z['selectedbroker']=='zerodha':
                        tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==optiontoken)]['tradingsymbol'].iloc[-1]
                        ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                            exchange=exch,
                                            transaction_type= 'SELL',
                                            quantity=int(optionlot) * int(trade['lot']),
                                            variety="regular",
                                            order_type="MARKET",
                                            product="NRML",
                                            validity="DAY")
                        print(ret)
                    elif z['selectedbroker']=='mofs':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                            z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                            
                            Orderinfo = {
                             "clientcode":z1['client_id'],      
                             "exchange":exch_map[exch],
                             "symboltoken":optiontoken,
                             "buyorsell":"SELL" ,
                             "ordertype":"MARKET",
                             "producttype":"NORMAL",
                             "orderduration":"DAY",
                             "price":0,
                             "triggerprice":0,
                             "quantityinlot": int(trade['lot']),
                             "disclosedquantity":0,
                             "amoorder":"N",
                             "algoid":"",
                             "tag":" "
                            }
                            ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                        #print(self.prices)
                    elif z['selectedbroker']=='smc':
                        #exch=trad['exch']
                        exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                        ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                        exchangeInstrumentID=int(optiontoken),
                        productType='NRML',
                        orderType='MARKET',
                        orderSide='SELL',
                        timeInForce='DAY',
                        disclosedQuantity=0,
                        orderQuantity=int(optionlot) * int(trade['lot']),
                        limitPrice=0,
                        stopPrice=0,
                        apiOrderSource="WEBAPI",
                        orderUniqueIdentifier="123abc")
                    elif z['selectedbroker']=='mstock':
                        #exch=trad['exch']
                        exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                        Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                        if Exch =='NSE':
                            instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='BSE':
                            instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='MCX':
                            instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        apikey=self.mstock[trade['user']]['apikey']
                        access_token=self.mstock[trade['user']]['access_token']
                        headers = {
                            'X-Mirae-Version': '1',
                            'Authorization':  f'token {apikey}:{access_token}',
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }
                        data = {
                            'tradingsymbol': instrument,
                            'exchange': exch,
                            'transaction_type': 'SELL',
                            'order_type': 'MARKET',
                            'quantity': int(optionlot) * int(trade['lot']),
                            'product': 'NRML',
                            'validity': 'DAY',
                            'price': '0',
                            'variety':'regular'
                        }

                        response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                        ret=(response.json())
                    broker_order_results.append(
                        self._record_broker_order_result(
                            trade,
                            z['selectedbroker'],
                            ret,
                            'SELL',
                            option,
                            int(optionlot) * int(trade['lot']),
                        )
                    )
                print(ret)
            pricesss=0
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[symbol])
            broker_order_success = (not trade['live']) or (
                bool(broker_order_results)
                and all(result['success'] for result in broker_order_results)
            )
            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(pricesss)
            ,'side':OTYPE,'status':"open" if broker_order_success else "broker_failed",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(option), 'pnlhalf':0,"decision":"intrade" if broker_order_success else "broker_failed",'BSmode':False,'entrycond':Signal,'exitcond':self.oppocond(Signal),'entry_id':self._next_entry_id(),'live':trade['live'],
            'exch':exch,'current_price':float(pricesss),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,
            'broker_order_results': broker_order_results}
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one(
                {'botcode': trade['botcode'], 'user': trade['user']},
                {
                    '$set': {
                        'position': 'in' if broker_order_success else 'out',
                        'entry_order_state': (
                            'success' if broker_order_success else 'broker_failed'
                        ),
                        'last_broker_order_error': (
                            [] if broker_order_success else broker_order_results
                        ),
                    }
                },
            )
        except Exception as e:
            self.strategy_collection.update_one(
                {
                    'botcode': trade.get('botcode'),
                    'user': trade.get('user'),
                },
                {
                    '$set': {
                        'position': 'out',
                        'entry_order_state': 'preflight_failed',
                        'last_broker_order_error': str(e),
                        'last_broker_order_error_time': int(
                            datetime.datetime.now().timestamp()
                        ),
                    }
                },
            )
            trading_exception(
                "future_entry_order_failed",
                e,
                user=trade.get("user"),
                strategy_id=trade.get("botcode"),
                strategy=trade.get("strategy"),
                symbol=trade.get("symbol"),
                side="SELL",
            )
            print(f"Error in FSELL: {e}")

        
            




    def EBUY(self,trade,symbol,side_override=None):
        try:

            #MainEquitySelect
            option,optionlot,optiontoken=self.MainEquitySelect(symbol)
            #option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])
            #print(option,optionlot,optiontoken)

            mainoption=option
            side='BUY'
            if side_override in ('BUY', 'SELL'):
                side=side_override
            elif trade['strategy']!='EQSSALGO':
                if symbol in self.topbottombuylist:
                    side='BUY'
                    #option,optionlot,optiontoken=self.MainEquitySelect(symbol)
                if symbol in self.topbottomselllist:
                    side='SELL'
                    #option,optionlot,optiontoken=self.MainEquitySelect(symbol)
            else:
                side='BUY'

            
            self.add_symbol_to_websocket(str(option))
            print(self.prices)
            trade['option']=str(option)

            exch='NSE'
            #if trade['symbol']=='CRUDEOIL':
            #    exch='MCX'
            instrument = self._make_instrument(
                exch,
                optiontoken,
                symbol,
                str(option),
                optionlot
            )
            print(instrument)
            self.add_symbol_to_websocket(str(option))
            ftok=exch+'|'+str(int(optiontoken))
            self.add_to_websocket(ftok)
            print(self.prices)
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])
            
            print('equity price: {}'.format(str(pricesss)))
            print(instrument)
            z=self.broker_collection.find_one({'user':trade['user']})
            selected_broker = z.get('selectedbroker') if z else None
            accountbalance=100000
            if trade['live'] and trade['FixedLot'] not in ('Fixed', 'QTY'):
                if selected_broker == 'aliceblue' and trade['user'] in self.alice:
                    accountbalance=float(self.alice[trade['user']].get_balance()[0]['net'])
                else:
                    print(f"Using fallback balance for {trade['user']} {selected_broker}: broker balance lookup is not implemented")
            lot=0
            if trade['FixedLot'] == 'Fixed':
                lot= int(int(trade['lot'])/float(pricesss))
            elif trade['FixedLot'] == 'QTY':
                lot=int(trade['lot'])
            else:
                lot=int(((float(trade['lot'])/100)*accountbalance)/float(pricesss))
            if lot==0:
                lot=1
            if trade['strategy']=='EQSSALGO':
                lot=lot*trade['ssteps'][symbol]
            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(pricesss)
            ,'side':side,'status':"open",'pnl':0,'lot':int(lot),'initial_lot':int(lot),
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str('-'),
            'optionname':str(trade['option']), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':1,'exitcond':-1,'entry_id':self._next_entry_id(),'live':trade['live'],
            'exch':exch,'current_price':float(pricesss),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,'exittime':int(datetime.datetime.now().timestamp())}
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
            if trade['live']:
                pos=ProductType.Delivery
                pos1='C'
                if trade['positiontype']=='Equity':
                    pos=ProductType.Intraday
                    pos1='I'
                trans=TransactionType.Buy if side == 'BUY' else TransactionType.Sell
                trans1='B' if side == 'BUY' else 'S'
                order_qty = int(optionlot) * int(lot)
                if not z or 'selectedbroker' not in z:
                    raise RuntimeError(f"No selected broker found for {trade['user']}")
                if z['selectedbroker']=='shoonya':
                    ret = self.shoonya[trade['user']].place_order(buy_or_sell=trans1, product_type=pos1,
                        exchange=exch, tradingsymbol=option, 
                        quantity=order_qty, discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                        retention='DAY', remarks='my_order_001')
                elif z['selectedbroker']=='aliceblue':
                    instrument=self.alice[trade['user']].get_instrument_by_token(exch, optiontoken)
                    ret = self._place_aliceblue_limit_order(
                        user=trade['user'],
                        transaction_type=trans,
                        instrument=instrument,
                        quantity=order_qty,
                        product_type=pos,
                        symbol=option,
                        exch=exch,
                        optiontoken=optiontoken,
                        order_tag='order1'
                    )
                elif z['selectedbroker']=='fyers':
                    print('fyers')
                    order_type = 2  # Default to Market Order
                    instrument=self.testalice.get_instrument_by_token(exch, optiontoken).name
                    # Determine product type based on position type
                    if trade['positiontype'] == 'Equity':
                        product_type = 'INTRADAY'
                    elif trade['positiontype'] in ['Future', 'Option']:
                        product_type = 'MARGIN'
                    exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                    Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                    if Exch =='NSE':
                        instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                    elif Exch =='BSE':
                        instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                    elif Exch =='MCX':
                        instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                    
                    # Prepare order data
                    data = {
                        "symbol": f"{Exch}:{instrument}",
                        "qty": order_qty,
                        "type": order_type,
                        "side": 1 if side == 'BUY' else -1,
                        "productType": product_type,
                        "limitPrice": 0.0,
                        "stopPrice": 0.0,
                        "validity": "DAY",
                        "disclosedQty": 0,
                        "offlineOrder": False,
                        "orderTag": "tag1",
                        "stopLoss": 0.0,
                        "takeProfit": 0.0
                    }
                    ret=self.fyers[trade['user']].place_order(
                        data=data  
                    )
                elif z['selectedbroker']=='angelone':
                        if trade['positiontype'] == 'Equity':
                            product_type = 'INTRADAY'
                        elif trade['positiontype'] in ['Future', 'Option']:
                            product_type = 'CARRYFORWARD'
                        instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==exch)&(self.angelone_scripts['token']==str(optiontoken))].iloc[-1]
                        orderparams = {
                            "variety": "NORMAL",
                            "tradingsymbol": instrument['symbol'],
                            "symboltoken": instrument['token'],
                            "transactiontype": "BUY" if side == 'BUY' else 'SELL',
                            "exchange": exch,
                            "ordertype": "MARKET",
                            "producttype": product_type,
                            "duration": "DAY",
                            "price": "0",
                            "squareoff": "0",
                            "stoploss": "0",
                            "quantity": order_qty
                            }
                        ret = self.angelone[trade['user']].placeOrder(orderparams) 
                elif z['selectedbroker']=='dhan':
                    try:
                        exch=exch
                        if exch=='NFO':
                            exch1='NSE_FNO'
                        elif exch=='NSE':
                            exch1='NSE_EQ'
                        elif exch=='BFO':
                            exch1='BSE_FNO'
                        elif   exch=='BSE':
                            exch1='BSE_EQ'
                        elif exch=='MFO' or  exch=='MCX':
                            exch1='MCX_COMM'
                        if trade['positiontype'] == 'Equity':
                            product_type = 'INTRADAY'
                        elif trade['positiontype'] in ['Future', 'Option']:
                            product_type = 'MARGIN'
                        ret=self.dhan[trade['user']].place_order(security_id=str(optiontoken),            # HDFC Bank
                            exchange_segment=exch1,
                            transaction_type= "BUY" if side == 'BUY' else 'SELL', 
                            quantity=order_qty,
                            order_type="MARKET",
                            product_type=product_type,
                            price=0, trigger_price=0, disclosed_quantity=0,
                        after_market_order=False, validity='DAY', amo_time='OPEN',
                        bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                        print(ret)
                    except Exception as e:
                        print(f"[ERROR] Order failed but returning True anyway: {e}")

                    ret = True
                    print(ret)
                elif z['selectedbroker']=='zerodha':
                    if trade['positiontype'] == 'Equity':
                        product_type = 'MIS'
                    elif trade['positiontype'] in ['Future', 'Option']:
                        product_type = 'NRML'
                    tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==optiontoken)]['tradingsymbol'].iloc[-1]
                    ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                        exchange=exch,
                                        transaction_type="BUY" if side == 'BUY' else 'SELL',
                                        quantity=order_qty,
                                        variety="regular",
                                        order_type="MARKET",
                                        product=product_type,
                                        validity="DAY")
                elif z['selectedbroker']=='mofs':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                            z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                            
                            Orderinfo = {
                             "clientcode":z1['client_id'],      
                             "exchange":exch_map[exch],
                             "symboltoken":optiontoken,
                             "buyorsell":"BUY" if side == 'BUY' else 'SELL' ,
                             "ordertype":"MARKET",
                             "producttype":"DELIVERY",
                             "orderduration":"DAY",
                             "price":0,
                             "triggerprice":0,
                             "quantityinlot":int(lot),
                             "disclosedquantity":0,
                             "amoorder":"N",
                             "algoid":"",
                             "tag":" "
                            }
                            ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                        #print(self.prices)
                elif z['selectedbroker']=='smc':
                        #exch=trad['exch']
                        exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                        ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                        exchangeInstrumentID=int(optiontoken),
                        productType='CNC',
                        orderType='MARKET',
                        orderSide="BUY" if side == 'BUY' else 'SELL',
                        timeInForce='DAY',
                        disclosedQuantity=0,
                        orderQuantity=order_qty,
                        limitPrice=0,
                        stopPrice=0,
                        apiOrderSource="WEBAPI",
                        orderUniqueIdentifier="123abc")
                elif z['selectedbroker']=='mstock':
                        #exch=trad['exch']
                        exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                        Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                        if Exch =='NSE':
                            instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='BSE':
                            instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='MCX':
                            instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        apikey=self.mstock[trade['user']]['apikey']
                        access_token=self.mstock[trade['user']]['access_token']
                        headers = {
                            'X-Mirae-Version': '1',
                            'Authorization':  f'token {apikey}:{access_token}',
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }
                        data = {
                            'tradingsymbol': instrument,
                            'exchange': exch,
                            'transaction_type': "BUY" if side == 'BUY' else 'SELL',
                            'order_type': 'MARKET',
                            'quantity': order_qty,
                            'product': 'CNC',
                            'validity': 'DAY',
                            'price': '0',
                            'variety':'regular'
                        }

                        response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                        ret=(response.json())
                print(ret)

        except Exception as e:
            print(self.prices)
            print(f"Error in EBUY: {e}")

    def EOBUY(self,trade,symbol):
        try:

            #MainEquitySelect
            option,optionlot,optiontoken=self.MainEquitySelect( symbol)
            #option,optionlot,optionexpiry,optiontoken
            self.add_symbol_to_websocket(str(option))
            mainoption=option
            side='BUY'

            if trade['strategy']!='EQSSALGO':
                if symbol in self.topbottombuylist:
                    side='BUY'
                    option,optionlot,optionexpiry,optiontoken=self.MainEquityOptionSelect(mainoption, 'CE', 0,'Current Week')
                    if datetime.datetime.now().date()>(optionexpiry-datetime.timedelta(days=int(trade['DaysHead']))):
                        option,optionlot,optionexpiry,optiontoken=self.MainEquityOptionSelect(mainoption, 'CE', 0,'Next Week')
                if symbol in self.topbottomselllist:
                    side='SELL'
                    option,optionlot,optionexpiry,optiontoken=self.MainEquityOptionSelect(mainoption, 'PE', 0,'Current Week')
                    if datetime.datetime.now().date()>(optionexpiry-datetime.timedelta(days=int(trade['DaysHead']))):
                        option,optionlot,optionexpiry,optiontoken=self.MainEquityOptionSelect(mainoption, 'PE', 0,'Next Week')
            else:
                side='BUY'
            #option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])
            
            self.add_symbol_to_websocket(str(option))
            #print(self.prices)
            #print('hellow ')
            trade['option']=str(option)

            exch='NFO'
            #if trade['symbol']=='CRUDEOIL':
            #    exch='MCX'
            #instrument=self.testalice.get_instrument_by_symbol(exch, trade['option'])
            #if type(instrument)==dict:
            #print(self.NfoAB[self.NfoAB['Trading Symbol']==option])
            #print(option,optionlot,optionexpiry,optiontoken)
            instrument=self.alice[trade['user']].get_instrument_by_token(exch,(optiontoken))
            #print(instrument)
            if type(instrument)==dict:
                #instrument=self.testalice.get_instrument_by_symbol(exch, trade['option'])
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=symbol, name=str(option), expiry='', lot_size=int(optionlot))
            print(self.alice[trade['user']].get_scrip_info(instrument))

            #print(self.prices)
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])
            if mainoption in list( self.prices.keys()):
                mprice=self.prices[mainoption]
            elif mainoption in list( self.sprices.keys()):
                mprice=self.sprices[mainoption]
            else:
                mprice=float(self.prices[mainoption])

            
            print('equity price: {}'.format(str(pricesss)))
            print(instrument)
            accountbalance=0
            accountbalance=float(self.alice[trade['user']].get_balance()[0]['net'])
            lot=0
            if not trade['live']:
                accountbalance=100000
            if trade['FixedLot'] == 'Fixed':
                lot= int(int(trade['lot'])/float(pricesss))
            elif trade['FixedLot'] == 'QTY':
                lot=int(trade['lot'])
            else:
                lot=int(((float(trade['lot'])/100)*accountbalance)/float(pricesss))
            if lot==0:
                lot=1
            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(self.prices[mainoption])
            ,'side':side,'status':"open",'pnl':0,'lot':int(lot),'initial_lot':int(lot),
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(trade['option']), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':1,'exitcond':-1,'entry_id':self._next_entry_id(),'live':trade['live'],
            'exch':exch,'current_price':float(mprice),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,'exittime':int(datetime.datetime.now().timestamp())}
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
            print('i am mini ##############################')
            if trade['live']:
                print('i start firing##################')
                trans=TransactionType.Buy if side == 'BUY' else TransactionType.Sell

                z=self.broker_collection.find_one({'user':trade['user']})
                if z['selectedbroker']=='shoonya':
                    ret = self.shoonya[trade['user']].place_order(buy_or_sell='B', product_type='M',
                        exchange=exch, tradingsymbol=option, 
                        quantity=int(optionlot)*int(trade['lot']), discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                        retention='DAY', remarks='my_order_001')
                elif z['selectedbroker']=='aliceblue':
                    instrument=self.alice[trade['user']].get_instrument_by_token(exch, optiontoken)
                    ret = self._place_aliceblue_limit_order(
                        user=trade['user'],
                        transaction_type=TransactionType.Buy,
                        instrument=instrument,
                        quantity=int(optionlot) * int(trade['lot']),
                        product_type=ProductType.Delivery,
                        symbol=option,
                        exch=exch,
                        optiontoken=optiontoken,
                        order_tag='order1'
                    )
                elif z['selectedbroker']=='fyers':
                    print('fyers')
                    # Determine order type based on trade
                    order_type = 2  # Default to Market Order
                    exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                    Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                    # Determine product type based on position type
                    product_type = 'MARGIN'

                    instrument=self.testalice.get_instrument_by_token(exch, optiontoken).name
                    if Exch =='NSE':
                        instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                    elif Exch =='BSE':
                        instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                    elif Exch =='MCX':
                        instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                    
                    # Prepare order data
                    data = {
                        "symbol": f"{Exch}:{instrument}",
                        "qty": int(optionlot) * int(   trade['lot']),
                        "type": order_type,
                        "side": 1 ,
                        "productType": product_type,
                        "limitPrice": 0.0,
                        "stopPrice": 0.0,
                        "validity": "DAY",
                        "disclosedQty": 0,
                        "offlineOrder": False,
                        "orderTag": "tag1",
                        "stopLoss": 0.0,
                        "takeProfit": 0.0
                    }
                    ret=self.fyers[trade['user']].place_order(
                        data=data  
                    )


                elif z['selectedbroker']=='angelone':
                    instrument=self.angelone_scripts[(self.angelone_scripts['exch_seg']==exch)&(self.angelone_scripts['token']==str(optiontoken))].iloc[-1]
                    orderparams = {
                        "variety": "NORMAL",
                        "tradingsymbol": instrument['symbol'],
                        "symboltoken": instrument['token'],
                        "transactiontype": "BUY" ,
                        "exchange": exch,
                        "ordertype": "MARKET",
                        "producttype": "CARRYFORWARD",
                        "duration": "DAY",
                        "price": "0",
                        "squareoff": "0",
                        "stoploss": "0",
                        "quantity": int(optionlot) * int(   trade['lot'])
                        }
                    ret = self.angelone[trade['user']].placeOrder(orderparams)
                elif z['selectedbroker']=='dhan':
                    try:
                        #exch=trad['exch']
                        if exch=='NFO':
                            exch1='NSE_FNO'
                        elif exch=='NSE':
                            exch1='NSE_EQ'
                        elif exch=='BFO':
                            exch1='BSE_FNO'
                        elif   exch=='BSE':
                            exch1='BSE_EQ'
                        elif exch=='MFO' or  exch=='MCX':
                            exch1='MCX_COMM'
                        
                        ret=self.dhan[trade['user']].place_order(security_id=str(optiontoken),            # HDFC Bank
                            exchange_segment=exch1,
                            transaction_type= "BUY" , 
                            quantity=int(optionlot) * int(trade['lot']),
                            order_type="MARKET",
                            product_type="MARGIN",
                            price=0, trigger_price=0, disclosed_quantity=0,
                    after_market_order=False, validity='DAY', amo_time='OPEN',
                    bo_profit_value=None, bo_stop_loss_Value=None, tag=None )
                        print(ret)
                    except Exception as e:
                        print(f"[ERROR] Order failed but returning True anyway: {e}")

                    ret = True
                    print(ret)
                elif z['selectedbroker']=='zerodha':
                    tradingsymbol=self.kiteSymboldf[(self.kiteSymboldf['exchange']==exch)&(self.kiteSymboldf['exchange_token']==optiontoken)]['tradingsymbol'].iloc[-1]
                    ret=self.zerodha[trade['user']].place_order(tradingsymbol=tradingsymbol,
                                        exchange=exch,
                                        transaction_type="BUY",
                                        quantity=int(optionlot) * int(trade['lot']),
                                        variety="regular",
                                        order_type="MARKET",
                                        product="NRML",
                                        validity="DAY")
                    print('tried fired ########################')
                elif z['selectedbroker']=='mofs':
                            #exch=trad['exch']
                            exch_map = {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'}
                            z1 = self.db['apis'].find_one({'broker':z['selectedbroker'],'user':trade['user']})
                            
                            Orderinfo = {
                             "clientcode":z1['client_id'],      
                             "exchange":exch_map[exch],
                             "symboltoken":optiontoken,
                             "buyorsell":"BUY" ,
                             "ordertype":"MARKET",
                             "producttype":"NORMAL",
                             "orderduration":"DAY",
                             "price":0,
                             "triggerprice":0,
                             "quantityinlot": int(trade['lot']),
                             "disclosedquantity":0,
                             "amoorder":"N",
                             "algoid":"",
                             "tag":" "
                            }
                            ret=(self.mofs[trade['user']].PlaceOrder(Orderinfo))
                elif z['selectedbroker']=='smc':
                        #exch=trad['exch']
                        exch_map = {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'}
                        ret=self.smc[trade['user']].place_order( exchangeSegment=exch_map[exch],
                        exchangeInstrumentID=int(optiontoken),
                        productType='NRML',
                        orderType='MARKET',
                        orderSide="BUY" ,
                        timeInForce='DAY',
                        disclosedQuantity=0,
                        orderQuantity=int(optionlot) * int(trade['lot']),
                        limitPrice=0,
                        stopPrice=0,
                        apiOrderSource="WEBAPI",
                        orderUniqueIdentifier="123abc")
                elif z['selectedbroker']=='mstock':
                        #exch=trad['exch']
                        exch_map = {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
                        Exch = exch_map.get(exch, 'NSE')  # Defaulting to NSE if not found
                        if Exch =='NSE':
                            instrument=self.Fyers_NSE[(self.Fyers_NSE['exchangeName']==Exch)&(self.Fyers_NSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='BSE':
                            instrument=self.Fyers_BSE[(self.Fyers_BSE['exchangeName']==Exch)&(self.Fyers_BSE['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        elif Exch =='MCX':
                            instrument=self.Fyers_MCX[(self.Fyers_MCX['exchangeName']==Exch)&(self.Fyers_MCX['exToken']==optiontoken)]['exSymName'].iloc[-1]
                        apikey=self.mstock[trade['user']]['apikey']
                        access_token=self.mstock[trade['user']]['access_token']
                        headers = {
                            'X-Mirae-Version': '1',
                            'Authorization':  f'token {apikey}:{access_token}',
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }
                        data = {
                            'tradingsymbol': instrument,
                            'exchange': exch,
                            'transaction_type': 'BUY',
                            'order_type': 'MARKET',
                            'quantity': int(optionlot) * int(trade['lot']),
                            'product': 'NRML',
                            'validity': 'DAY',
                            'price': '0',
                            'variety':'regular'
                        }

                        response = requests.post('https://api.mstock.trade/openapi/typea/orders/regular', headers=headers, data=data)
                        ret=(response.json())
                print(ret)
            
            
        except Exception as e:
            #print(self.prices)
            print(f"Error in EOBUY: {e}")
    def EFBUY(self,trade,symbol):
        try:

            #MainEquitySelect
            option,optionlot,optiontoken=self.MainEquitySelect( symbol)
            #option,optionlot,optionexpiry,optiontoken
            mainoption=option
            self.add_symbol_to_websocket(str(option))
            print(option,optionlot,optiontoken)
            side='BUY'
            if trade['strategy']!='EQSSALGO':
                if symbol in self.topbottombuylist:
                    side='BUY'
                    #option,optionlot,optiontoken=self.MainEquitySelect(symbol)
                if symbol in self.topbottomselllist:
                    side='SELL'
                    #option,optionlot,optiontoken=self.MainEquitySelect(symbol)
            else:
                side='BUY'
            option,optionlot,optionexpiry,optiontoken=self.MainFutureSelect(mainoption,'Current Month')
            if datetime.datetime.now().date()>(optionexpiry-datetime.timedelta(days=int(trade['DaysHead']))):
                option,optionlot,optionexpiry,optiontoken=self.MainFutureSelect(mainoption,'Next Month')
            #option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])
            print(option,optionlot,optionexpiry,optiontoken)
            self.add_symbol_to_websocket(str(option))
            #print(self.prices)
            trade['option']=str(option)

            exch='NFO'
            #if trade['symbol']=='CRUDEOIL':
            #    exch='MCX'
            #instrument=self.testalice.get_instrument_by_symbol(exch, trade['option'])
            #if type(instrument)==dict:
            #print(self.NfoAB[self.NfoAB['Trading Symbol']==option])
            
            instrument=self.alice[trade['user']].get_instrument_by_token(exch,(optiontoken))
            #print(instrument)
            if type(instrument)==dict:
                #instrument=self.testalice.get_instrument_by_symbol(exch, trade['option'])
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=symbol, name=str(option), expiry='', lot_size=int(optionlot))
            print(self.alice[trade['user']].get_scrip_info(instrument))
            #
            #option=option.replace('-EQ','')
            '''if type(instrument)==dict:
                
                self.add_symbol_to_websocket(str(option))
                ftok=exch+'|'+str(int(optiontoken))
                self.add_to_websocket(ftok)
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=symbol, name=str(option), expiry='', lot_size=int(optionlot))
            '''
            print(self.prices)
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])
            if mainoption in list( self.prices.keys()):
                mprice=self.prices[mainoption]
            elif mainoption in list( self.sprices.keys()):
                mprice=self.sprices[mainoption]
            else:
                mprice=float(self.prices[mainoption])
            print('equity price: {}'.format(str(pricesss)))
            print(instrument)
            accountbalance=0
            accountbalance=float(self.alice[trade['user']].get_balance()[0]['net'])
            lot=1
            if not trade['live']:
                accountbalance=100000
            if trade['FixedLot'] == 'Fixed':
                lot= int(int(trade['lot'])/float(pricesss))
            elif trade['FixedLot'] == 'QTY':
                lot=int(trade['lot'])
            else:
                lot=int(((float(trade['lot'])/100)*accountbalance)/float(pricesss))
            if lot==0:
                lot=1
            print('i am mini ##############################')
            if trade['live']:
                print('i start firing##################')
                trans=TransactionType.Buy if side == 'BUY' else TransactionType.Sell
                ret = self._place_aliceblue_limit_order(
                    user=trade['user'],
                    transaction_type=trans,
                    instrument=instrument,
                    quantity=int(lot) * int(optionlot),
                    product_type=ProductType.Delivery,
                    symbol=option,
                    exch=exch,
                    optiontoken=optiontoken,
                    order_tag='order1'
                )


                print('tried fired ########################')
                print(ret)
                

            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(self.prices[option])
            ,'side':side,'status':"open",'pnl':0,'lot':int(lot),'initial_lot':int(lot),
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(trade['option']), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':1,'exitcond':-1,'entry_id':self._next_entry_id(),'live':trade['live'],
            'exch':exch,'current_price':float(mprice),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,'exittime':int(datetime.datetime.now().timestamp())}
            print(pos)

            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
        except Exception as e:
            #print(self.prices)
            print(f"Error in EFBUY: {e}")
    def OBUY(self, trade, OTYPE, Signal):
        try:
            if trade.get('live') and trade.get('entry_order_state') in {'attempted', 'broker_failed'}:
                return
            if trade.get('live') and trade.get('entry_order_state') == 'preflight_failed':
                retry_after = int(
                    os.getenv("SSLAGO_ORDER_PREFLIGHT_RETRY_SECONDS", "30")
                )
                last_failure = int(trade.get('last_broker_order_error_time') or 0)
                if int(datetime.datetime.now().timestamp()) - last_failure < retry_after:
                    return

            # ---------------- OPTION SELECTION ---------------- #

            option, optionlot, optionexpiry, optiontoken = \
                self.MainOptionSelect(
                    trade['symbol'],
                    OTYPE,
                    trade['strike'],
                    trade['Expiry']
                )

            rollover_time = datetime.datetime.strptime(
                str(optionexpiry) + ' ' + str(trade['RolloverTime']),
                "%Y-%m-%d %H:%M"
            )

            if (datetime.datetime.now() +
                datetime.timedelta(days=trade['DaysHead'])) >= rollover_time:

                if 'Current Week' in trade['Expiry']:
                    trade['Expiry'] = 'Next Week'

                elif 'Current Month' in trade['Expiry']:
                    trade['Expiry'] = 'Next Month'

                option, optionlot, optionexpiry, optiontoken = \
                    self.MainOptionSelect(
                        trade['symbol'],
                        OTYPE,
                        trade['strike'],
                        trade['Expiry']
                    )

            option = str(option)
            trade['option'] = option

            print(option)

            # ---------------- EXCHANGE SELECTION ---------------- #

            exch_map = {
                'CRUDEOIL': 'MCX',
                'SENSEX': 'BFO'
            }

            exch = exch_map.get(trade['symbol'], 'NFO')

            # ---------------- WEBSOCKET MANAGEMENT ---------------- #

            self.add_symbol_to_websocket(option)
            ftok = exch + '|' + str(int(optiontoken))
            self.add_to_websocket(ftok)
            instrument = self._make_instrument(
                exch,
                optiontoken,
                trade['symbol'],
                option,
                optionlot
            )

            if self.websocketretry > 10 and self.api is not None:
                self.api.subscribe(self.subscribe_list)
                self.add_symbol_to_websocket(option)

                if option not in self.prices:
                    self.websocketretry = 0

            if option not in self.prices:
                self.websocketretry += 1

            # ---------------- PRICE FETCH ---------------- #

            pricesss = self._wait_for_market_price(
                option,
                exch,
                optiontoken,
            )

            print(f"option price: {pricesss}")
            print(instrument)

            # ---------------- ORDER EXECUTION ---------------- #

            broker_order_results = []

            if trade['live']:

                self.strategy_collection.update_one(
                    {'botcode': trade['botcode'], 'user': trade['user']},
                    {
                        '$set': {
                            'entry_order_state': 'attempted',
                            'entry_order_time': int(datetime.datetime.now().timestamp())
                        }
                    }
                )
                trade['entry_order_state'] = 'attempted'

                print("i start firing ##################")

                broker_info = self.broker_collection.find_one(
                    {'user': trade['user']}
                )

                broker = broker_info['selectedbroker']

                qty = int(optionlot) * int(trade['lot'])

                ret = None

                # ---------------- BROKER ROUTER ---------------- #

                if broker == 'shoonya':

                    ret = self.shoonya[trade['user']].place_order(
                        buy_or_sell='B',
                        product_type='M',
                        exchange=exch,
                        tradingsymbol=option,
                        quantity=qty,
                        discloseqty=0,
                        price_type='MKT',
                        price=0,
                        trigger_price=0,
                        retention='DAY',
                        remarks='my_order_001'
                    )

                elif broker == 'aliceblue':

                    instrument = \
                        self.alice[trade['user']] \
                        .get_instrument_by_token(exch, optiontoken)

                    ret = self._place_aliceblue_limit_order(
                        user=trade['user'],
                        transaction_type=TransactionType.Buy,
                        instrument=instrument,
                        quantity=qty,
                        product_type=ProductType.Longterm,
                        symbol=option,
                        exch=exch,
                        optiontoken=optiontoken,
                        order_tag='order1'
                    )

                elif broker == 'fyers':

                    order_type = 2
                    product_type = 'MARGIN'

                    exch_map2 = {
                        'MCX': 'MCX',
                        'NFO': 'NSE',
                        'BFO': 'BSE'
                    }

                    Exch = exch_map2.get(exch, 'NSE')

                    if Exch == 'NSE':
                        instrument = \
                            self.Fyers_NSE[
                                (self.Fyers_NSE['exchangeName'] == Exch) &
                                (self.Fyers_NSE['exToken'] == optiontoken)
                            ]['exSymName'].iloc[-1]

                    elif Exch == 'BSE':
                        instrument = \
                            self.Fyers_BSE[
                                (self.Fyers_BSE['exchangeName'] == Exch) &
                                (self.Fyers_BSE['exToken'] == optiontoken)
                            ]['exSymName'].iloc[-1]

                    else:
                        instrument = \
                            self.Fyers_MCX[
                                (self.Fyers_MCX['exchangeName'] == Exch) &
                                (self.Fyers_MCX['exToken'] == optiontoken)
                            ]['exSymName'].iloc[-1]

                    data = {
                        "symbol": f"{Exch}:{instrument}",
                        "qty": qty,
                        "type": order_type,
                        "side": 1,
                        "productType": product_type,
                        "limitPrice": 0.0,
                        "stopPrice": 0.0,
                        "validity": "DAY"
                    }

                    ret = self.fyers[trade['user']].place_order(data=data)

                elif broker == 'zerodha':

                    tradingsymbol = \
                        self.kiteSymboldf[
                            (self.kiteSymboldf['exchange'] == exch) &
                            (self.kiteSymboldf['exchange_token'] == optiontoken)
                        ]['tradingsymbol'].iloc[-1]

                    ret = self.zerodha[trade['user']].place_order(
                        tradingsymbol=tradingsymbol,
                        exchange=exch,
                        transaction_type='BUY',
                        quantity=qty,
                        variety="regular",
                        order_type="MARKET",
                        product="NRML",
                        validity="DAY"
                    )

                elif broker == 'dhan':

                    exch_map3 = {
                        'NFO': 'NSE_FNO',
                        'NSE': 'NSE_EQ',
                        'BFO': 'BSE_FNO',
                        'BSE': 'BSE_EQ',
                        'MCX': 'MCX_COMM'
                    }

                    ret = self.dhan[trade['user']].place_order(
                        security_id=str(optiontoken),
                        exchange_segment=exch_map3[exch],
                        transaction_type="BUY",
                        quantity=qty,
                        order_type="MARKET",
                        product_type="MARGIN",
                        price=0
                    )

                print("tried fired ########################")
                print(ret)
                broker_order_results.append(
                    self._record_broker_order_result(trade, broker, ret, 'BUY', option, qty)
                )

            broker_order_success = (not trade['live']) or any(
                result['success'] for result in broker_order_results
            )
            entry_option_price = self._entry_price_from_broker_results(
                broker_order_results,
                pricesss
            )
            current_option_price = self._get_market_price(option, exch, optiontoken)
            initial_pnl = self._initial_position_pnl(
                is_sell=False,
                entry_price=entry_option_price,
                current_price=current_option_price,
                lot=trade['lot'],
                optionlot=optionlot
            )
            current_underlying_price = self._get_underlying_price(
                trade['symbol'],
                self.prices.get(trade['symbol'], 0)
            )

            # ---------------- POSITION STORAGE ---------------- #

            pos = {
                'user': str(trade['user']),
                'botname': trade['botname'],
                'time': int(datetime.datetime.now().timestamp()),
                'symbol': trade['symbol'],
                'entry_price': current_underlying_price,
                'side': OTYPE,
                'status': "open" if broker_order_success else "broker_failed",
                'pnl': initial_pnl,
                'lot': trade['lot'],
                'initial_lot': trade['lot'],
                'optionentry': entry_option_price,
                'optionexit': current_option_price,
                'optionlot': int(optionlot),
                'optionexpiry': str(optionexpiry),
                'optionname': option,
                'pnlhalf': 0,
                "decision": "intrade" if broker_order_success else "broker_failed",
                'BSmode': True,
                'entrycond': Signal,
                'exitcond': self.oppocond(Signal),
                'entry_id': self._next_entry_id(),
                'live': trade['live'],
                'exch': exch,
                'current_price': current_underlying_price,
                'botcode': trade['botcode'],
                'optiontoken': int(optiontoken),
                'trail_stoploss': 0,
                'broker_order_results': broker_order_results
            }

            self.opositions_collection.insert_one(pos)

            if broker_order_success:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode']},
                    {'$set': {'position': 'in', 'entry_order_state': 'success'}}
                )
            else:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode']},
                    {'$set': {'position': 'out', 'entry_order_state': 'broker_failed', 'last_broker_order_error': broker_order_results}}
                )

            print("i am goee")

        except Exception as e:
            error_text = str(e)
            self.strategy_collection.update_one(
                {
                    'botcode': trade.get('botcode'),
                    'user': trade.get('user'),
                },
                {
                    '$set': {
                        'position': 'out',
                        'entry_order_state': 'preflight_failed',
                        'last_broker_order_error': error_text,
                        'last_broker_order_error_time': int(
                            datetime.datetime.now().timestamp()
                        ),
                    }
                },
            )
            trade['entry_order_state'] = 'preflight_failed'
            trade['last_broker_order_error'] = error_text
            trade['last_broker_order_error_time'] = int(
                datetime.datetime.now().timestamp()
            )
            trading_exception(
                "option_entry_order_failed",
                e,
                user=trade.get("user"),
                strategy_id=trade.get("botcode"),
                strategy=trade.get("strategy"),
                symbol=trade.get("symbol"),
                selected_broker=self._selected_broker_for_user(
                    trade.get("user")
                ),
                entry_order_state=trade.get("entry_order_state"),
            )
            print(f"Error in OBUY: {e}")
        
        

    def oppocond(self,Signal):
        if Signal==1:
            return -1
        else:
            return 1
    def get_last_500_rows(self, symbol,limit):
        # Assuming you have a connection to the SQLite database
        db_file = "historical.db"
        conn = sqlite3.connect(db_file)

        try:
            # Create a cursor object to execute SQL queries
            cursor = conn.cursor()

            # Retrieve the last 500 rows for the specified symbol
            #cursor.execute(f"SELECT * FROM {symbol} WHERE symbol=? ORDER BY time DESC LIMIT 500", (symbol,))
            cursor.execute(f"SELECT * FROM {symbol} WHERE symbol=? ORDER BY date DESC LIMIT {limit}", (symbol,))
            rows = cursor.fetchall()
            # Create a DataFrame from the retrieved rows
            columns = ["time", "open", "high", "low", "close", "volume", "time_column", "symbol","sqlite_timestamp"]
            df_last_500_rows = pd.DataFrame(rows[::-1], columns=columns)

            return df_last_500_rows

        finally:
            # Always close the connection in a 'finally' block to ensure it gets closed even if an exception occurs
            conn.close()




    def EBUYEXIT(self, trade1):
        try:
            config = trade1

            trades = list(self.opositions_collection.find({
                'botcode': config['botcode'],
                'status': 'open',
                'user': config['user']
            }))

            for trade in trades:
                if not trade:
                    continue

                now = datetime.datetime.now()
                userr = trade['user']

                # ------------------ PRICE FETCH ------------------
                if trade['optionname'] not in self.prices:
                    self.add_symbol_to_websocket(trade['optionname'])

                if config['strategy'] == 'SSEQUITYFNO':
                    eq_symbol = trade['symbol'] + '-EQ'
                    if eq_symbol not in self.prices:
                        self.add_symbol_to_websocket(eq_symbol)

                price = float(self.prices.get(trade['optionname']) or
                              self.sprices.get(trade['optionname']) or 0)

                trade['optionexit'] = price
                trade['current_price'] = self.prices.get(trade['symbol'] + '-EQ', 0)

                # ------------------ PNL ------------------
                entry = trade.get('optionentry', 0)
                lot = trade.get('lot', 1)
                optlot = trade.get('optionlot', 1)

                is_sell_position = trade.get('side') != 'BUY'
                pnl = (entry - price) * lot * optlot if is_sell_position else (price - entry) * lot * optlot

                trade['pnl'] = int(pnl)
                perlotpnl = int((entry - price) * optlot) if is_sell_position else int((price - entry) * optlot)

                # ------------------ TRAILING ------------------
                trade.setdefault('trail_stoploss', 0)

                if config['trail'] == 1 and config['trail_stoploss'] > 0:
                    step = config['trail_stoploss']
                    if perlotpnl >= step * 2:
                        levels = perlotpnl // step
                        trade['trail_stoploss'] = (levels - 1) * step
                else:
                    trade['trail_stoploss'] = 0

                # ------------------ TP / SL ------------------
                if config['pct_point']:
                    if is_sell_position:
                        tp_price = entry * (1 - config['tp'] / 100)
                        sl_price = entry * (1 + config['sl'] / 100)
                    else:
                        tp_price = entry * (1 + config['tp'] / 100)
                        sl_price = entry * (1 - config['sl'] / 100)
                else:
                    if is_sell_position:
                        tp_price = entry - config['tp']
                        sl_price = entry + config['sl']
                    else:
                        tp_price = entry + config['tp']
                        sl_price = entry - config['sl']

                # ------------------ SIGNAL ------------------
                Signal = False

                if trade['side'] == 'BUY':
                    self.breakoutexit.setdefault(trade['symbol'], False)
                    self.fractalbreakout.setdefault(trade['symbol'], False)

                    if config['strategy'] == 'SSEQUITYFNO':
                        Signal = self.breakoutexit[trade['symbol']]
                    elif config['strategy'] == 'EQSSALGO':
                        Signal = self.fractalbreakout[trade['symbol']]

                else:
                    self.breakoutexitsell.setdefault(trade['symbol'], False)
                    self.fractalbreakoutsell.setdefault(trade['symbol'], False)

                    if config['strategy'] == 'SSEQUITYFNO':
                        Signal = self.breakoutexitsell[trade['symbol']]
                    elif config['strategy'] == 'EQSSALGO':
                        Signal = self.fractalbreakoutsell[trade['symbol']]

                # ------------------ EXIT REASON ------------------
                exit_reason = None

                if trade.get('decision') == 'exitit':
                    exit_reason = "User Exit"

                elif Signal:
                    exit_reason = "Signal Exit"

                elif config['status'] in ['paused', 'closed']:
                    exit_reason = "Bot Exit"

                elif config['Intraday'] and now.time() > datetime.datetime.strptime(config['ExitTime'], '%H:%M').time():
                    exit_reason = "Intraday Exit"

                elif config['pnlexit_tpslexit'] and pnl >= config['tp'] * lot:
                    exit_reason = "PNL TP"

                elif config['pnlexit_tpslexit'] and pnl <= -config['sl'] * lot:
                    exit_reason = "PNL SL"

                elif config['trail'] == 1 and trade['trail_stoploss'] != 0 and pnl <= trade['trail_stoploss'] * lot:
                    exit_reason = "Trailing SL"

                elif not config['pnlexit_tpslexit'] and (
                    price <= tp_price if is_sell_position else price >= tp_price
                ):
                    exit_reason = "TP Hit"

                elif not config['pnlexit_tpslexit'] and (
                    price >= sl_price if is_sell_position else price <= sl_price
                ):
                    exit_reason = "SL Hit"

                elif config['strategy'] == 'EQSSALGO' and trade['symbol'] not in config.get('symbol', []):
                    exit_reason = "Symbol Removed"

                # ------------------ EXECUTE EXIT ------------------
                if exit_reason:
                    if trade.get('live'):
                        try:
                            self.mainebuyexit(trade, config)
                        except Exception as order_error:
                            print(
                                f"Live EBUYEXIT order deferred for "
                                f"{trade.get('user')}/{trade.get('optionname')}: {order_error}"
                            )
                            return False

                    print(f"{now} :: {userr} :: ### {exit_reason} ###")

                    trade['status'] = 'close'
                    config['position'] = 'out'

                    # ------------------ STEP LOGIC ------------------
                    if config['strategy'] == 'EQSSALGO':
                        steps = config['ssteps'][trade['symbol']]

                        if pnl < 0:
                            if config['FixedLot1'] == 'Doubling':
                                steps *= 2
                            elif config['FixedLot1'] == 'Steps':
                                steps += 1
                        else:
                            steps = 1

                        if steps != config['ssteps'][trade['symbol']]:
                            config['ssteps'][trade['symbol']] = steps
                            self.strategy_collection.update_one(
                                {'botcode': config['botcode'], 'user': config['user']},
                                {'$set': config}
                            )

                    trade['exittime'] = int(now.timestamp())

                    self.opositions_collection.update_one(
                        {'_id': trade['_id']},
                        {'$set': trade}
                    )

                    return True

                # ------------------ UPDATE ------------------
                trade['exittime'] = int(now.timestamp())

                self.opositions_collection.update_one(
                    {'_id': trade['_id']},
                    {'$set': trade}
                )

        except Exception as e:
            print(f"Error in EBUYEXIT: {e}")
            


    def OBUYEXIT(self, trade, Signal, exSignal):
        try:
            config = trade

            trades = list(self.opositions_collection.find({
                'botcode': config['botcode'],
                'status': 'open',
                'user': trade['user']
            }))

            if not trades:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode'], 'user': trade['user']},
                    {'$set': {'position': 'out'}}
                )
                return

            for trade in trades:
                if not trade:
                    continue

                # ------------------ WEBSOCKET ------------------
                subscription_failed = self.add_symbol_to_websocket(trade['optionname'])

                if not subscription_failed and self.websocketretry > 10:
                    print('websocket repair')
                    self.add_symbol_to_websocket(trade['optionname'], force=True)
                    if trade['optionname'] in self.prices:
                        self.websocketretry = 0

                if not subscription_failed and trade['optionname'] not in self.prices:
                    self.websocketretry += 1

                # ------------------ PRICE ------------------
                try:
                    price = self._get_market_price(
                        trade['optionname'],
                        trade.get('exch'),
                        trade.get('optiontoken')
                    )
                except (KeyError, ValueError, TypeError) as price_error:
                    price = float(trade.get('optionexit') or trade.get('optionentry') or 0)
                    warning_key = (
                        trade.get('user'),
                        trade.get('botcode'),
                        trade.get('optionname'),
                    )
                    now_monotonic = time.monotonic()
                    last_warning = self._price_unavailable_log_times.get(warning_key, 0)
                    if now_monotonic - last_warning >= 30:
                        print(
                            "OBUYEXIT quote unavailable; using stored price: "
                            f"user={trade.get('user')}, botcode={trade.get('botcode')}, "
                            f"symbol={trade.get('optionname')}, price={price}, "
                            f"error={price_error}"
                        )
                        self._price_unavailable_log_times[warning_key] = now_monotonic

                trade['optionexit'] = price
                trade['current_price'] = self._get_underlying_price(
                    trade['symbol'],
                    trade.get('current_price') or trade.get('entry_price')
                )

                pnl = (price - trade['optionentry']) * trade['lot'] * trade['optionlot']
                trade['pnl'] = int(pnl)
                per_lot_pnl = int((price - trade['optionentry']) * trade['optionlot'])

                # ------------------ TRAILING SL ------------------
                if config['trail']:
                    trade.setdefault('trail_stoploss', 0)
                    step = config['trail_stoploss']
                    trigger = step * 2

                    if per_lot_pnl >= trigger:
                        if trade['trail_stoploss'] == 0:
                            trade['trail_stoploss'] = step
                        else:
                            diff = int(per_lot_pnl / step) - int(trade['trail_stoploss'] / step)
                            if diff > 1:
                                trade['trail_stoploss'] += step
                else:
                    trade['trail_stoploss'] = 0

                # ------------------ TP/SL ------------------
                if config['pct_point']:
                    ex = trade['optionentry'] * (1 + config['tp'] / 100)
                    sl = trade['optionentry'] * (1 - config['sl'] / 100)
                else:
                    ex = trade['optionentry'] + config['tp']
                    sl = trade['optionentry'] - config['sl']

                # ------------------ TIME CONDITIONS ------------------
                now = datetime.datetime.now()
                now_time = now.time()

                intraday_exit = (
                    config['Intraday'] and
                    now_time > datetime.datetime.strptime(config['ExitTime'], '%H:%M').time()
                )

                option_expiry = str(trade.get('optionexpiry') or '').strip()
                expiry_exit = False
                if option_expiry and option_expiry.upper() != 'PERPETUAL':
                    try:
                        rollover = (
                            datetime.datetime.strptime(option_expiry, "%Y-%m-%d")
                            - datetime.timedelta(days=config['DaysHead'])
                        )
                        expiry_exit = (
                            now_time
                            > datetime.datetime.strptime(
                                config['RolloverTime'], '%H:%M'
                            ).time()
                            and str(datetime.date.today())
                            in [option_expiry, str(rollover.date())]
                        )
                    except (TypeError, ValueError) as expiry_error:
                        warning_key = (
                            trade.get('user'),
                            trade.get('botcode'),
                            'invalid_expiry',
                        )
                        now_monotonic = time.monotonic()
                        last_warning = self._price_unavailable_log_times.get(
                            warning_key, 0
                        )
                        if now_monotonic - last_warning >= 30:
                            print(
                                "OBUYEXIT expiry check skipped: "
                                f"user={trade.get('user')}, "
                                f"botcode={trade.get('botcode')}, "
                                f"optionexpiry={option_expiry}, "
                                f"error={expiry_error}"
                            )
                            self._price_unavailable_log_times[warning_key] = (
                                now_monotonic
                            )

                # ------------------ EXIT CONDITIONS ------------------
                exit_reason = None

                if trade['exitcond'] == Signal:
                    exit_reason = "Signal Exit"
                elif config['pnlexit_tpslexit'] and pnl >= config['tp'] * config['lot']:
                    exit_reason = "PNL TP"
                elif config['pnlexit_tpslexit'] and pnl <= -config['sl'] * config['lot']:
                    exit_reason = "PNL SL"
                elif pnl >= config['maxprofit'] * config['lot']:
                    exit_reason = "Max Profit"
                elif pnl <= -config['maxloss'] * config['lot']:
                    exit_reason = "Max Loss"
                elif config['trail'] and trade['trail_stoploss'] and pnl <= trade['trail_stoploss'] * config['lot']:
                    exit_reason = "Trailing SL"
                elif not config['pnlexit_tpslexit'] and price > ex:
                    exit_reason = "TP Hit"
                elif not config['pnlexit_tpslexit'] and price < sl:
                    exit_reason = "SL Hit"
                elif trade.get('decision') == 'exitit':
                    exit_reason = "User Exit"
                elif config['status'] in ['paused', 'closed']:
                    exit_reason = "Bot Stopped"
                elif intraday_exit:
                    exit_reason = "Intraday Exit"
                elif expiry_exit:
                    exit_reason = "Expiry Exit"

                # ------------------ EXECUTE EXIT ------------------
                if exit_reason:
                    if trade.get('live'):
                        try:
                            self.mainbuyexit(trade, config)
                        except Exception as order_error:
                            print(
                                f"Live OBUYEXIT order deferred for "
                                f"{trade.get('user')}/{trade.get('optionname')}: {order_error}"
                            )
                            return False

                    print(f"{now} :: {trade['user']} :: ### {exit_reason} ###")

                    trade['status'] = 'close'
                    config['position'] = 'out'

                    # LOT UPDATE
                    if pnl < 0:
                        if config['FixedLot'] == 'Doubling':
                            config['lot'] *= 2
                        elif config['FixedLot'] == 'Steps':
                            config['lot'] += config['stepvalue']
                    else:
                        config['lot'] = config['initiallot']

                    trade['exittime'] = int(now.timestamp())

                    self.opositions_collection.update_one({'_id': trade['_id']}, {'$set': trade})
                    self.strategy_collection.update_one({'_id': config['_id']}, {'$set': config})

                    return True

                # ------------------ FINAL UPDATE ------------------
                trade['exittime'] = int(now.timestamp())

                self.opositions_collection.update_one({'_id': trade['_id']}, {'$set': trade})

                if trade['status'] == 'close':
                    if config['Newsignal']:
                        config['timetowait'] = int(now.timestamp()) - 1
                    else:
                        config['timetowait'] = int(now.timestamp()) + int(config['ttw'] * 60)

                    self.strategy_collection.update_one({'_id': config['_id']}, {'$set': config})

        except Exception as e:
            print(f"Error in OBUYEXIT: {e}")
            





    def OSELL(self, trade, OTYPE, Signal):
        try:
            if trade.get('live') and trade.get('entry_order_state') in {'attempted', 'broker_failed'}:
                return
            if trade.get('live') and trade.get('entry_order_state') == 'preflight_failed':
                retry_after = int(
                    os.getenv("SSLAGO_ORDER_PREFLIGHT_RETRY_SECONDS", "30")
                )
                last_failure = int(trade.get('last_broker_order_error_time') or 0)
                if int(datetime.datetime.now().timestamp()) - last_failure < retry_after:
                    return


            # ---------- OPTION SELECTION ---------- #

            option, optionlot, optionexpiry, optiontoken = \
                self.MainOptionSelect(
                    trade['symbol'],
                    OTYPE,
                    trade['strike'],
                    trade['Expiry']
                )

            rollover_time = datetime.datetime.strptime(
                str(optionexpiry) + ' ' + str(trade['RolloverTime']),
                "%Y-%m-%d %H:%M"
            )

            if (datetime.datetime.now() +
                datetime.timedelta(days=trade['DaysHead'])) >= rollover_time:

                if 'Current Week' in trade['Expiry']:
                    trade['Expiry'] = 'Next Week'

                elif 'Current Month' in trade['Expiry']:
                    trade['Expiry'] = 'Next Month'

                option, optionlot, optionexpiry, optiontoken = \
                    self.MainOptionSelect(
                        trade['symbol'],
                        OTYPE,
                        trade['strike'],
                        trade['Expiry']
                    )

            option = str(option)
            trade['option'] = option

            # ---------- EXCHANGE ---------- #

            exch_map = {
                'CRUDEOIL': 'MCX',
                'SENSEX': 'BFO'
            }

            exch = exch_map.get(trade['symbol'], 'NFO')

            # ---------- WEBSOCKET ---------- #

            self.add_symbol_to_websocket(option)
            ftok = exch + "|" + str(int(optiontoken))
            self.add_to_websocket(ftok)
            instrument = self._make_instrument(
                exch,
                optiontoken,
                trade['symbol'],
                option,
                optionlot
            )

            if self.websocketretry > 10 and self.api is not None:

                self.api.subscribe(self.subscribe_list)
                self.add_symbol_to_websocket(option)

                if option not in self.prices:
                    self.websocketretry = 0

            if option not in self.prices:
                self.websocketretry += 1

            # ---------- PRICE ---------- #

            pricesss = self._wait_for_market_price(
                option,
                exch,
                optiontoken,
            )

            print(f"option price: {pricesss}")

            # ---------- ORDER EXECUTION ---------- #

            broker_order_results = []

            if trade['live']:

                self.strategy_collection.update_one(
                    {'botcode': trade['botcode'], 'user': trade['user']},
                    {
                        '$set': {
                            'entry_order_state': 'attempted',
                            'entry_order_time': int(datetime.datetime.now().timestamp())
                        }
                    }
                )
                trade['entry_order_state'] = 'attempted'

                broker_info = self.broker_collection.find_one(
                    {'user': trade['user']}
                )

                broker = broker_info['selectedbroker']

                lot = int(trade['lot'])

                # -------- LOT SLICING -------- #

                if lot > 20:

                    total_quant = \
                        [trade['slicing']] * int(lot / trade['slicing'])

                    if (lot % trade['slicing']) > 0:
                        total_quant.append(lot % trade['slicing'])

                else:

                    total_quant = [lot]

                # -------- EXECUTE ORDERS -------- #

                for quant in total_quant:

                    qty = int(optionlot) * int(quant)

                    ret = None

                    # -------- BROKER ROUTING -------- #

                    if broker == 'shoonya':

                        ret = self.shoonya[trade['user']].place_order(
                            buy_or_sell='S',
                            product_type='M',
                            exchange=exch,
                            tradingsymbol=option,
                            quantity=qty,
                            discloseqty=0,
                            price_type='MKT',
                            price=0,
                            trigger_price=0,
                            retention='DAY'
                        )

                    elif broker == 'aliceblue':

                        instrument = \
                            self.alice[trade['user']] \
                            .get_instrument_by_token(exch, optiontoken)

                        ret = self._place_aliceblue_limit_order(
                            user=trade['user'],
                            transaction_type=TransactionType.Sell,
                            instrument=instrument,
                            quantity=qty,
                            product_type=ProductType.Longterm,
                            symbol=option,
                            exch=exch,
                            optiontoken=optiontoken,
                            order_tag='order1'
                        )

                    elif broker == 'fyers':

                        order_type = 2
                        product_type = 'MARGIN'

                        exch_map2 = {
                            'MCX': 'MCX',
                            'NFO': 'NSE',
                            'BFO': 'BSE'
                        }

                        Exch = exch_map2.get(exch, 'NSE')

                        if Exch == 'NSE':

                            instrument = \
                                self.Fyers_NSE[
                                    (self.Fyers_NSE['exchangeName'] == Exch) &
                                    (self.Fyers_NSE['exToken'] == optiontoken)
                                ]['exSymName'].iloc[-1]

                        elif Exch == 'BSE':

                            instrument = \
                                self.Fyers_BSE[
                                    (self.Fyers_BSE['exchangeName'] == Exch) &
                                    (self.Fyers_BSE['exToken'] == optiontoken)
                                ]['exSymName'].iloc[-1]

                        else:

                            instrument = \
                                self.Fyers_MCX[
                                    (self.Fyers_MCX['exchangeName'] == Exch) &
                                    (self.Fyers_MCX['exToken'] == optiontoken)
                                ]['exSymName'].iloc[-1]

                        data = {
                            "symbol": f"{Exch}:{instrument}",
                            "qty": qty,
                            "type": order_type,
                            "side": -1,
                            "productType": product_type,
                            "limitPrice": 0.0,
                            "stopPrice": 0.0,
                            "validity": "DAY"
                        }

                        ret = \
                            self.fyers[trade['user']].place_order(data=data)

                    elif broker == 'zerodha':

                        tradingsymbol = \
                            self.kiteSymboldf[
                                (self.kiteSymboldf['exchange'] == exch) &
                                (self.kiteSymboldf['exchange_token']
                                 == optiontoken)
                            ]['tradingsymbol'].iloc[-1]

                        ret = self.zerodha[trade['user']].place_order(
                            tradingsymbol=tradingsymbol,
                            exchange=exch,
                            transaction_type='SELL',
                            quantity=qty,
                            variety="regular",
                            order_type="MARKET",
                            product="NRML",
                            validity="DAY"
                        )

                    elif broker == 'dhan':

                        exch_map3 = {
                            'NFO': 'NSE_FNO',
                            'NSE': 'NSE_EQ',
                            'BFO': 'BSE_FNO',
                            'BSE': 'BSE_EQ',
                            'MCX': 'MCX_COMM'
                        }

                        ret = self.dhan[trade['user']].place_order(
                            security_id=str(optiontoken),
                            exchange_segment=exch_map3[exch],
                            transaction_type="SELL",
                            quantity=qty,
                            order_type="MARKET",
                            product_type="MARGIN",
                            price=0
                        )

                    print(ret)
                    broker_order_results.append(
                        self._record_broker_order_result(trade, broker, ret, 'SELL', option, qty)
                    )

            broker_order_success = (not trade['live']) or any(
                result['success'] for result in broker_order_results
            )

            # ---------- POSITION STORE ---------- #

            current_option_price = self._get_market_price(option, exch, optiontoken)
            entry_option_price = self._entry_price_from_broker_results(
                broker_order_results,
                current_option_price
            )
            initial_pnl = self._initial_position_pnl(
                is_sell=True,
                entry_price=entry_option_price,
                current_price=current_option_price,
                lot=trade['lot'],
                optionlot=optionlot
            )
            current_underlying_price = self._get_underlying_price(
                trade['symbol'],
                self.prices.get(trade['symbol'], 0)
            )

            pos = {
                'user': str(trade['user']),
                'botname': trade['botname'],
                'time': int(datetime.datetime.now().timestamp()),
                'symbol': trade['symbol'],
                'entry_price': current_underlying_price,
                'side': OTYPE,
                'status': "open" if broker_order_success else "broker_failed",
                'pnl': initial_pnl,
                'lot': trade['lot'],
                'initial_lot': trade['lot'],
                'optionentry': entry_option_price,
                'optionexit': current_option_price,
                'optionlot': int(optionlot),
                'optionexpiry': str(optionexpiry),
                'optionname': option,
                'pnlhalf': 0,
                "decision": "intrade" if broker_order_success else "broker_failed",
                'BSmode': False,
                'entrycond': Signal,
                'exitcond': self.oppocond(Signal),
                'entry_id': self._next_entry_id(),
                'live': trade['live'],
                'exch': exch,
                'current_price': current_underlying_price,
                'botcode': trade['botcode'],
                'optiontoken': int(optiontoken),
                'trail_stoploss': 0,
                'broker_order_results': broker_order_results
            }

            self.opositions_collection.insert_one(pos)

            if broker_order_success:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode']},
                    {'$set': {'position': 'in', 'entry_order_state': 'success'}}
                )
            else:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode']},
                    {'$set': {'position': 'out', 'entry_order_state': 'broker_failed', 'last_broker_order_error': broker_order_results}}
                )

        except Exception as e:
            error_text = str(e)
            failure_time = int(datetime.datetime.now().timestamp())
            self.strategy_collection.update_one(
                {
                    'botcode': trade.get('botcode'),
                    'user': trade.get('user'),
                },
                {
                    '$set': {
                        'position': 'out',
                        'entry_order_state': 'preflight_failed',
                        'last_broker_order_error': error_text,
                        'last_broker_order_error_time': failure_time,
                    }
                },
            )
            trade['entry_order_state'] = 'preflight_failed'
            trade['last_broker_order_error'] = error_text
            trade['last_broker_order_error_time'] = failure_time
            trading_exception(
                "option_entry_order_failed",
                e,
                user=trade.get("user"),
                strategy_id=trade.get("botcode"),
                strategy=trade.get("strategy"),
                symbol=trade.get("symbol"),
                selected_broker=self._selected_broker_for_user(
                    trade.get("user")
                ),
                entry_order_state=trade.get("entry_order_state"),
            )
            print(f"Error in OSELL: {e}")



    def FEXIT(self, trade, Signal):
        try:
            config = trade

            # ---- Helper: Update lot sizing ----
            def update_lot_after_exit(trade, config):
                if trade['pnl'] < 0:
                    if config['FixedLot'] == 'Doubling':
                        config['lot'] = config['lot'] * 2
                    elif config['FixedLot'] == 'Steps':
                        config['lot'] += config['stepvalue']
                    elif config['FixedLot'] == 'FixedLot':
                        config['lot'] = config['lot']
                else:
                    config['lot'] = config['initiallot']

            # ---- Helper: Execute exit ----
            def execute_exit(reason, current_trade, current_user):
                print(f"{datetime.datetime.now()} :: {current_user} :: ### {reason} ###")

                current_trade['status'] = 'close'
                config['position'] = 'out'

                update_lot_after_exit(current_trade, config)

                if current_trade['live']:
                    if current_trade['side'] == 'BUY':
                        self.mainbuyexit(current_trade, config)
                    else:
                        self.mainsellexit(current_trade, config)

            trades = list(
                self.opositions_collection.find({
                    'botcode': config['botcode'],
                    'status': 'open',
                    'user': trade['user']
                })
            )

            if len(trades) == 0:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode'], 'user': trade['user']},
                    {'$set': {'position': 'out'}}
                )

            for trade in trades:

                if trade is None:
                    continue

                userr = trade['user']

                self.add_symbol_to_websocket(trade['optionname'])

                # ---- Price Resolution ----
                if trade['optionname'] in self.prices:
                    price = self.prices[trade['optionname']]
                elif trade['optionname'] in self.sprices:
                    price = self.sprices[trade['optionname']]
                else:
                    price = self._get_market_price(trade['symbol'])

                trade['current_price'] = price
                trade['optionexit'] = price

                # ---- PnL Calculation ----
                if trade['side'] == 'SELL':
                    trade['pnl'] = int(
                        (trade['optionentry'] - price)
                        * trade['lot']
                        * trade['optionlot']
                    )
                    perlotpnl = int(
                        (trade['optionentry'] - price)
                        * trade['optionlot']
                    )
                else:
                    trade['pnl'] = int(
                        (price - trade['optionentry'])
                        * trade['lot']
                        * trade['optionlot']
                    )
                    perlotpnl = int(
                        (price - trade['optionentry'])
                        * trade['optionlot']
                    )

                # ---- Trailing Stop Logic ----
                if config['trail'] == 1:

                    if 'trail_stoploss' not in trade:
                        trade['trail_stoploss'] = 0

                    kti = config['trail_stoploss'] * 2
                    dti = int(perlotpnl / config['trail_stoploss'])

                    if perlotpnl >= kti:

                        if trade['trail_stoploss'] == 0:
                            trade['trail_stoploss'] = config['trail_stoploss']

                        else:
                            fti = int(
                                trade['trail_stoploss']
                                / config['trail_stoploss']
                            )

                            if (dti - fti) > 1:
                                trade['trail_stoploss'] += config['trail_stoploss']
                else:
                    trade['trail_stoploss'] = 0

                # ---- TP/SL Calculation ----
                if config['pct_point']:

                    if trade['side'] == 'BUY':
                        ex = trade['optionentry'] * (1 + config['tp'] / 100)
                        sl = trade['optionentry'] * (1 - config['sl'] / 100)
                    else:
                        ex = trade['optionentry'] * (1 - config['tp'] / 100)
                        sl = trade['optionentry'] * (1 + config['sl'] / 100)

                else:

                    if trade['side'] == 'BUY':
                        ex = trade['optionentry'] + config['tp']
                        sl = trade['optionentry'] - config['sl']
                    else:
                        ex = trade['optionentry'] - config['tp']
                        sl = trade['optionentry'] + config['sl']

                # ---- Rollover Calculation ----
                rollover = (
                    datetime.datetime.strptime(
                        trade['optionexpiry'],
                        "%Y-%m-%d"
                    )
                    - datetime.timedelta(days=config['DaysHead'])
                ).date()

                now_time = datetime.datetime.now().time()

                # ---- Exit Conditions ----
                if trade['exitcond'] == Signal:
                    execute_exit("Exit HIT", trade, userr)

                elif (
                    trade['pnl'] >= config['tp'] * config['lot']
                    and config['pnlexit_tpslexit']
                ):
                    execute_exit("PNL TP HIT", trade, userr)

                elif (
                    trade['pnl'] <= -(config['sl'] * config['lot'])
                    and config['pnlexit_tpslexit']
                ):
                    execute_exit("PNL SL HIT", trade, userr)

                elif trade['pnl'] >= config['maxprofit'] * config['lot']:
                    execute_exit("DAY MAXPROFIT TP HIT", trade, userr)

                elif trade['pnl'] <= -(config['maxloss'] * config['lot']):
                    execute_exit("DAY MAXLOSS SL HIT", trade, userr)

                elif (
                    trade['trail_stoploss'] != 0
                    and config['trail'] == 1
                    and trade['pnl']
                    <= trade['trail_stoploss'] * config['lot']
                ):
                    execute_exit("DAY TRAIL SL HIT", trade, userr)

                elif price > ex and not config['pnlexit_tpslexit']:
                    execute_exit("TP HIT", trade, userr)

                elif price < sl and not config['pnlexit_tpslexit']:
                    execute_exit("SL HIT", trade, userr)

                elif trade['decision'] == 'exitit':
                    execute_exit("USER EXIT HIT", trade, userr)

                elif config['status'] in ['paused', 'closed']:
                    execute_exit("BOT EXIT HIT", trade, userr)

                elif (
                    now_time >
                    datetime.datetime.strptime(
                        config['ExitTime'],
                        '%H:%M'
                    ).time()
                    and config['Intraday']
                ):
                    execute_exit("Intraday EXIT HIT", trade, userr)

                elif (
                    now_time >
                    datetime.datetime.strptime(
                        config['RolloverTime'],
                        '%H:%M'
                    ).time()
                    and (
                        str(datetime.date.today())
                        == trade['optionexpiry']
                        or str(datetime.date.today())
                        == str(rollover)
                    )
                ):
                    execute_exit("Option Expiry EXIT HIT", trade, userr)

                # ---- Save Trade ----
                trade['exittime'] = int(
                    datetime.datetime.now().timestamp()
                )

                self.opositions_collection.update_one(
                    {
                        '_id': trade['_id'],
                        'entry_id': trade['entry_id']
                    },
                    {'$set': trade}
                )

                # ---- Strategy Update ----
                if trade['status'] == 'close':

                    if config['Newsignal']:
                        config['timetowait'] = int(
                            datetime.datetime.now().timestamp()
                        ) - 1
                    else:
                        config['timetowait'] = int(
                            datetime.datetime.now().timestamp()
                        ) + int(config['ttw'] * 60)

                    if '_id' in config:
                        del config['_id']

                    self.strategy_collection.update_one(
                        {
                            'botcode': trade['botcode'],
                            'user': trade['user']
                        },
                        {'$set': config}
                    )

        except Exception as e:
            print(f"Error in FEXIT: {e}")
    

    def OSELLEXIT(self, trade, Signal, exSignal):
        try:
            config = trade

            trades = list(self.opositions_collection.find({
                'botcode': config['botcode'],
                'status': 'open',
                'user': trade['user']
            }))

            if not trades:
                self.strategy_collection.update_one(
                    {'botcode': trade['botcode'], 'user': trade['user']},
                    {'$set': {'position': 'out'}}
                )
                return

            for trade in trades:
                if not trade:
                    continue

                now = datetime.datetime.now()

                # ------------------ WEBSOCKET ------------------
                connected = self.add_symbol_to_websocket(trade['optionname'])

                if self.websocketretry > 10 and self.api is not None:
                    self.api.subscribe(self.subscribe_list)
                    self.add_symbol_to_websocket(trade['optionname'], force=True)
                    if trade['optionname'] in self.prices:
                        self.websocketretry = 0

                if trade['optionname'] not in self.prices:
                    self.websocketretry += 1

                if connected:
                    print(
                        f"OSELLEXIT websocket symbol lookup failed for "
                        f"{trade.get('optionname')}; continuing with broker exit"
                    )

                # ------------------ PRICE ------------------
                price = self._get_market_price(
                    trade['optionname'],
                    trade.get('exch'),
                    trade.get('optiontoken')
                )
                if price is None:
                    price = float(trade.get('optionexit') or trade.get('optionentry') or 0)

                trade['optionexit'] = price
                trade['current_price'] = self._get_underlying_price(
                    trade['symbol'],
                    trade.get('current_price') or trade.get('entry_price')
                )

                # SELL PNL
                pnl = (trade['optionentry'] - price) * trade['lot'] * trade['optionlot']
                trade['pnl'] = int(pnl)

                userr = trade['user']

                # ------------------ TRAILING SL FIX ------------------
                trade.setdefault('max_pnl', 0)

                if trade['pnl'] > trade['max_pnl']:
                    trade['max_pnl'] = trade['pnl']

                if config['trail'] == 1:
                    trail_value = config['trail_stoploss'] * config['lot']

                    if trade['max_pnl'] >= trail_value * 2:
                        trade['trail_stoploss'] = trade['max_pnl'] - trail_value
                    else:
                        trade['trail_stoploss'] = 0
                else:
                    trade['trail_stoploss'] = 0

                # ------------------ TP / SL ------------------
                if config['pct_point']:
                    ex = trade['optionentry'] * (1 + config['tp'] / 100)
                    sl = trade['optionentry'] * (1 - config['sl'] / 100)
                else:
                    ex = trade['optionentry'] + config['tp']
                    sl = trade['optionentry'] - config['sl']

                # ------------------ TIME CONDITIONS ------------------
                now_time = now.time()

                intraday_exit = (
                    config['Intraday'] and
                    now_time > datetime.datetime.strptime(config['ExitTime'], '%H:%M').time()
                )

                rollover = datetime.datetime.strptime(trade['optionexpiry'], "%Y-%m-%d") - \
                           datetime.timedelta(days=config['DaysHead'])

                expiry_exit = (
                    now_time > datetime.datetime.strptime(config['RolloverTime'], '%H:%M').time() and
                    str(datetime.date.today()) in [trade['optionexpiry'], str(rollover.date())]
                )

                # ------------------ EXIT CONDITIONS ------------------
                exit_reason = None

                if trade['exitcond'] == exSignal:
                    exit_reason = "Signal Exit"

                elif config['trail'] == 1 and trade['trail_stoploss'] != 0 and trade['pnl'] <= trade['trail_stoploss']:
                    exit_reason = "Trailing SL"

                elif config['pnlexit_tpslexit'] and pnl >= config['tp'] * config['lot']:
                    exit_reason = "PNL TP"

                elif config['pnlexit_tpslexit'] and pnl <= -config['sl'] * config['lot']:
                    exit_reason = "PNL SL"

                elif pnl >= config['maxprofit'] * config['lot']:
                    exit_reason = "Max Profit"

                elif pnl <= -config['maxloss'] * config['lot']:
                    exit_reason = "Max Loss"

                elif not config['pnlexit_tpslexit'] and price > ex:
                    exit_reason = "TP Hit"

                elif not config['pnlexit_tpslexit'] and price < sl:
                    exit_reason = "SL Hit"

                elif trade.get('decision') == 'exitit':
                    exit_reason = "User Exit"

                elif config['status'] in ['paused', 'closed']:
                    exit_reason = "Bot Stopped"

                elif intraday_exit:
                    exit_reason = "Intraday Exit"

                elif expiry_exit:
                    exit_reason = "Expiry Exit"

                # ------------------ EXECUTE EXIT ------------------
                if exit_reason:
                    if trade.get('live'):
                        try:
                            self.mainsellexit(trade, config)
                        except Exception as order_error:
                            print(
                                f"Live OSELLEXIT order deferred for "
                                f"{trade.get('user')}/{trade.get('optionname')}: {order_error}"
                            )
                            return False

                    print(f"{now} :: {userr} :: ### {exit_reason} ###")

                    trade['status'] = 'close'
                    config['position'] = 'out'

                    # LOT UPDATE
                    if pnl < 0:
                        if config['FixedLot'] == 'Doubling':
                            config['lot'] *= 2
                        elif config['FixedLot'] == 'Steps':
                            config['lot'] += config['stepvalue']
                    else:
                        config['lot'] = config['initiallot']

                    trade['exittime'] = int(now.timestamp())

                    self.opositions_collection.update_one(
                        {'_id': trade['_id'], 'entry_id': trade['entry_id']},
                        {'$set': trade}
                    )

                    self.strategy_collection.update_one(
                        {'botcode': trade['botcode'], 'user': trade['user']},
                        {'$set': config}
                    )

                    return True

                # ------------------ FINAL UPDATE ------------------
                trade['exittime'] = int(now.timestamp())

                self.opositions_collection.update_one(
                    {'_id': trade['_id'], 'entry_id': trade['entry_id']},
                    {'$set': trade}
                )

                if trade['status'] == 'close':
                    if config['Newsignal']:
                        config['timetowait'] = int(now.timestamp()) - 1
                    else:
                        config['timetowait'] = int(now.timestamp()) + int(config['ttw'] * 60)

                    self.strategy_collection.update_one(
                        {'botcode': trade['botcode'], 'user': trade['user']},
                        {'$set': config}
                    )

        except Exception as e:
            print(f"Error in OSELLEXIT: {e}")
    

    def _ABcontracts(self):
        Nse=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/NSE.csv')
        Cds=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/CDS.csv')
        Mcx=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/MCX.csv')
        Nfo=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/NFO.csv')

        return Nse,Cds,Mcx,Nfo

    def _get_exchange_map(self, broker):
        """Get exchange mapping for different brokers"""
        exchange_maps = {
            'fyers': {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'},
            'mofs': {'NFO': 'NSEFO', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSEFO', 'MCX': 'MCX'},
            'smc': {'NFO': 'NSEFO', 'NSE': 'NSECM', 'BSE': 'BSECM', 'BFO': 'BSEFO', 'MCX': 'MCXFO'},
            'mstock': {'MCX': 'MCX', 'MFO': 'MCX', 'NFO': 'NSE', 'NSE': 'NSE', 'BSE': 'BSE', 'BFO': 'BSE'}
        }
        return exchange_maps.get(broker, {})

    def _get_dhan_exchange_segment(self, exch):
        """Get Dhan exchange segment mapping"""
        exchange_segments = {
            'NFO': 'NSE_FNO', 'NSE': 'NSE_EQ', 'BFO': 'BSE_FNO',
            'BSE': 'BSE_EQ', 'MFO': 'MCX_COMM', 'MCX': 'MCX_COMM'
        }
        return exchange_segments.get(exch, 'NSE_FNO')

    def _get_instrument_name(self, broker, exch, optiontoken):
        """Get instrument name for Fyers/Mstock brokers"""
        Exch = self._get_exchange_map(broker).get(exch, 'NSE')
        instrument_dfs = {
            'NSE': self.Fyers_NSE,
            'BSE': self.Fyers_BSE,
            'MCX': self.Fyers_MCX
        }
        df = instrument_dfs.get(Exch)
        if df is not None:
            result = df[(df['exchangeName'] == Exch) & (df['exToken'] == optiontoken)]
            if len(result) > 0:
                return result['exSymName'].iloc[-1], Exch
        return None, Exch

    def _get_broker_session(self, broker_dict, user, broker_name):
        """Return a logged-in broker client or fail with a clear retryable error."""
        session = getattr(self, broker_dict, {}).get(user)
        if session is None:
            raise RuntimeError(f"{broker_name} session unavailable for {user}")
        return session

    def _fallback_instrument(self, trade, exch, optiontoken):
        token = optiontoken
        mapped_token = self.tok_symbols.get(trade['optionname'])
        if mapped_token:
            token = str(mapped_token).split('|')[-1]
        return Instrument(
            exchange=exch,
            token=int(token),
            symbol=trade['symbol'],
            name=trade['optionname'],
            expiry='',
            lot_size=trade['optionlot']
        )

    def _round_price_to_tick(self, price, tick_size=0.05):
        tick = Decimal(str(tick_size))
        rounded = (Decimal(str(price)) / tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick
        return float(rounded)

    def _first_positive_float(self, *values):
        for value in values:
            if value in (None, ''):
                continue
            try:
                price = float(value)
                if price > 0:
                    return price
            except Exception:
                continue
        return None

    def _extract_level1_from_depth(self, depth):
        if not isinstance(depth, dict):
            return None, None

        bid = self._first_positive_float(
            depth.get('bid'), depth.get('best_bid'), depth.get('bestBid'),
            depth.get('bp1'), depth.get('bid_price1'), depth.get('bidPrice1'),
            depth.get('bPrice1'), depth.get('BPrice1')
        )
        ask = self._first_positive_float(
            depth.get('ask'), depth.get('best_ask'), depth.get('bestAsk'),
            depth.get('sp1'), depth.get('ask_price1'), depth.get('askPrice1'),
            depth.get('aPrice1'), depth.get('APrice1'), depth.get('offer'),
            depth.get('best_offer'), depth.get('bestOffer')
        )
        if bid and ask:
            return bid, ask

        for bid_key in ('bids', 'bidDepth', 'buy', 'Buy', 'buyers', 'bid_prices'):
            rows = depth.get(bid_key)
            if isinstance(rows, list) and rows:
                first = rows[0]
                if isinstance(first, dict):
                    bid = bid or self._first_positive_float(
                        first.get('price'), first.get('p'), first.get('bp'), first.get('bid')
                    )
                elif isinstance(first, (list, tuple)) and first:
                    bid = bid or self._first_positive_float(first[0])

        for ask_key in ('asks', 'askDepth', 'sell', 'Sell', 'sellers', 'ask_prices', 'offers'):
            rows = depth.get(ask_key)
            if isinstance(rows, list) and rows:
                first = rows[0]
                if isinstance(first, dict):
                    ask = ask or self._first_positive_float(
                        first.get('price'), first.get('p'), first.get('sp'),
                        first.get('ask'), first.get('offer')
                    )
                elif isinstance(first, (list, tuple)) and first:
                    ask = ask or self._first_positive_float(first[0])

        return bid, ask

    def _depth_timestamp(self, depth):
        if not isinstance(depth, dict):
            return None
        for key in ('_depth_time', 'depth_time', 'time', 'timestamp', 'ft', 'ltt'):
            value = depth.get(key)
            if value in (None, ''):
                continue
            try:
                value = float(value)
                if value > 100000000000:
                    value = value / 1000
                return value
            except Exception:
                continue
        return None

    def _depth_age_ms(self, depth):
        timestamp = self._depth_timestamp(depth)
        if timestamp is None:
            return None
        return int(max(0, (time.time() - timestamp) * 1000))

    def _is_depth_fresh(self, depth):
        age_ms = self._depth_age_ms(depth)
        if age_ms is None:
            return False
        return age_ms <= int(self.market_depth_max_age_seconds * 1000)

    def _remember_market_depth(self, symbol=None, exch=None, token=None, depth=None):
        if not isinstance(depth, dict):
            return
        depth = dict(depth)
        depth.setdefault('_depth_time', time.time())
        keys = []
        if symbol:
            keys.append(str(symbol))
        if exch and token:
            keys.append(f"{exch}|{token}")
        for key in keys:
            self.market_depths[key] = depth
            self.market_depth_times[key] = depth['_depth_time']

    def _normalize_aliceblue_depth_message(self, message):
        if isinstance(message, str):
            try:
                message = json.loads(message)
            except Exception:
                return None
        if not isinstance(message, dict):
            return None
        payload = message.get('data') if isinstance(message.get('data'), dict) else message
        exch = payload.get('e') or payload.get('exchange') or payload.get('exch')
        token = payload.get('tk') or payload.get('token') or payload.get('instrumentId') or payload.get('instrument_id')
        if exch and token:
            payload = dict(payload)
            payload['e'] = exch
            payload['tk'] = str(token)
        return payload

    def _event_handler_aliceblue_depth_update(self, user, message):
        payload = self._normalize_aliceblue_depth_message(message)
        if not payload:
            return
        key = payload.get('e') and payload.get('tk') and f"{payload['e']}|{payload['tk']}"
        symbol = self.symbols_tok.get(key) if key else None
        if symbol:
            self._remember_market_depth(symbol=symbol, exch=payload.get('e'), token=payload.get('tk'), depth=payload)
            if payload.get('lp') not in (None, ''):
                try:
                    self.prices[symbol] = float(payload['lp'])
                except Exception:
                    pass

    def _make_aliceblue_depth_instrument(self, symbol, token, row=None):
        if not token or '|' not in str(token) or AntA3Instrument is None:
            return None
        exch, raw_token = str(token).split('|', 1)
        lot_size = 1
        trading_symbol = symbol
        base_symbol = symbol
        expiry = ''
        if row is not None:
            try:
                lot_size = int(row.get('LotSize', row.get('lot_size', lot_size)))
                trading_symbol = row.get('TradingSymbol', trading_symbol)
                base_symbol = row.get('Symbol', base_symbol)
                expiry = row.get('Expiry', expiry)
            except Exception:
                pass
        return AntA3Instrument(
            exchange=exch,
            token=int(raw_token),
            symbol=base_symbol,
            trading_symbol=trading_symbol,
            expiry=expiry,
            lot_size=lot_size
        )

    def _selected_broker_for_user(self, user):
        try:
            broker_info = self.broker_collection.find_one({'user': user}) or {}
            return broker_info.get('selectedbroker')
        except Exception:
            return None

    def _aliceblue_user_verified_today(self, user):
        try:
            api_info = self.apis_collection.find_one({'user': user, 'broker': 'aliceblue'}) or {}
        except Exception:
            return False

        api_info = decrypt_secret_fields(dict(api_info), SECRET_FIELD_NAMES)
        session_value = self._aliceblue_saved_session(api_info)
        return bool(session_value)

    def _ensure_aliceblue_market_depth(self, user):
        if not getattr(self, 'aliceblue_market_depth_enabled', False):
            return
        if user in self.aliceblue_depth_started:
            return
        if user in self.aliceblue_depth_starting:
            return
        if not self._aliceblue_user_verified_today(user):
            print(f"aliceblue market depth skipped for {user}: not verified today")
            return
        alice = getattr(self, 'alice', {}).get(user)
        if not alice:
            return
        self.aliceblue_depth_starting.add(user)
        try:
            try:
                alice.start_websocket(
                    socket_open_callback=lambda user=user: print(f"aliceblue market depth websocket open user={user}"),
                    socket_close_callback=lambda user=user: print(f"aliceblue market depth websocket closed user={user}"),
                    socket_error_callback=lambda error, user=user: print(f"aliceblue market depth websocket error user={user}: {error}"),
                    subscription_callback=lambda message, user=user: self._event_handler_aliceblue_depth_update(user, message),
                    run_in_background=True,
                    market_depth=True
                )
            except Exception as first_error:
                unauthorized = (
                    '401' in str(first_error)
                    or 'unauthorized' in str(first_error).lower()
                )
                if not unauthorized or not self._refresh_aliceblue_session(
                    user, force=True
                ):
                    raise
                alice = getattr(self, 'alice', {}).get(user)
                if not alice:
                    raise RuntimeError(
                        f"AliceBlue session refresh did not create a client for {user}"
                    ) from first_error
                alice.start_websocket(
                    socket_open_callback=lambda user=user: print(f"aliceblue market depth websocket open user={user}"),
                    socket_close_callback=lambda user=user: print(f"aliceblue market depth websocket closed user={user}"),
                    socket_error_callback=lambda error, user=user: print(f"aliceblue market depth websocket error user={user}: {error}"),
                    subscription_callback=lambda message, user=user: self._event_handler_aliceblue_depth_update(user, message),
                    run_in_background=True,
                    market_depth=True
                )
            self.aliceblue_depth_started.add(user)
            self.db["broker_health"].update_one(
                {"user": user, "broker": "aliceblue"},
                {
                    "$set": {
                        "websocket_status": "connected",
                        "last_error": "",
                        "updated_at": datetime.datetime.utcnow(),
                    }
                },
                upsert=True,
            )
            for symbol in list(self.loadedwatchsymbols):
                token = self.tok_symbols.get(symbol)
                if token:
                    self._subscribe_aliceblue_depth_for_symbol(symbol, token)
        except Exception as e:
            self.db["broker_health"].update_one(
                {"user": user, "broker": "aliceblue"},
                {
                    "$set": {
                        "websocket_status": "disconnected",
                        "last_error": f"Market depth websocket failed: {e}",
                        "updated_at": datetime.datetime.utcnow(),
                    }
                },
                upsert=True,
            )
            trading_exception(
                "aliceblue_market_depth_start_error",
                e,
                user=user,
                broker="aliceblue",
            )
            print(f"aliceblue market depth websocket start failed user={user}: {e}")
        finally:
            self.aliceblue_depth_starting.discard(user)

    def _subscribe_aliceblue_depth_for_symbol(self, symbol, token=None, row=None):
        token = token or self.tok_symbols.get(symbol)
        instrument = self._make_aliceblue_depth_instrument(symbol, token, row)
        if instrument is None:
            return
        for user, alice in list(getattr(self, 'alice', {}).items()):
            if self._selected_broker_for_user(user) != 'aliceblue':
                continue
            if not self._aliceblue_user_verified_today(user):
                continue
            self._ensure_aliceblue_market_depth(user)
            if getattr(getattr(alice, 'trade', None), 'ws', None) is None:
                continue
            try:
                alice.subscribe([instrument])
                print(f"aliceblue depth subscribed user={user}, symbol={symbol}, token={token}")
            except Exception as e:
                print(f"aliceblue depth subscribe failed user={user}, symbol={symbol}, token={token}: {e}")

    def _get_level1_depth_price(self, transaction_type, symbol, exch=None, optiontoken=None, require_fresh=True):
        side = str(getattr(transaction_type, 'value', transaction_type)).upper()
        depth_candidates = []
        if symbol:
            depth_candidates.append(self.market_depths.get(str(symbol)))
        if exch and optiontoken:
            depth_candidates.append(self.market_depths.get(f"{exch}|{optiontoken}"))

        if exch and optiontoken:
            try:
                quote = self.api.get_quotes(exch, str(optiontoken)) if hasattr(self, 'api') and self.api else None
                if isinstance(quote, dict):
                    depth_candidates.append(quote)
                    self._remember_market_depth(symbol, exch, optiontoken, quote)
            except Exception as e:
                print(f"AliceBlue depth quote fallback failed for {symbol}: {e}")

        for depth in depth_candidates:
            if not isinstance(depth, dict):
                continue
            if require_fresh and not self._is_depth_fresh(depth):
                continue
            bid, ask = self._extract_level1_from_depth(depth)
            if side in ('BUY', 'B') and ask:
                return self._round_price_to_tick(ask), depth
            if side in ('SELL', 'S') and bid:
                return self._round_price_to_tick(bid), depth
        return None, None

    def get_order_push_price(self, side, symbol, exch=None, token=None):
        side_text = str(getattr(side, 'value', side)).upper()
        tick_size = self.order_push_tick_size
        push = 5.0
        depth_price, depth = self._get_level1_depth_price(side_text, symbol, exch, token, require_fresh=True)
        bid, ask = self._extract_level1_from_depth(depth) if depth else (None, None)
        ltp = None

        if depth_price is not None:
            if side_text in ('BUY', 'B'):
                limit_price = float(depth_price) + push
            else:
                limit_price = float(depth_price) - push
            limit_price = self._round_price_to_tick(limit_price, tick_size)
            return {
                'limit_price': limit_price,
                'price_source': 'fresh_depth',
                'bid': bid,
                'ask': ask,
                'ltp': self._first_positive_float(depth.get('lp') if isinstance(depth, dict) else None),
                'depth_age_ms': self._depth_age_ms(depth),
            }

        try:
            ltp = self._get_market_price(symbol, exch, token)
        except Exception as e:
            print(f"order push price skipped: price unavailable for {symbol}: {e}")
            return None

        if ltp is None or ltp <= 0:
            print(f"order push price skipped: invalid LTP for {symbol}: {ltp}")
            return None

        if side_text in ('BUY', 'B'):
            limit_price = float(ltp) + push
        else:
            limit_price = float(ltp) - push

        limit_price = self._round_price_to_tick(limit_price, tick_size)
        if limit_price <= 0:
            print(
                f"order push price skipped: non-positive limit price for "
                f"{symbol}, side={side_text}, ltp={ltp}, limit={limit_price}"
            )
            return None
        print(
            f"depth_missing: using LTP fallback for {symbol}, side={side_text}, "
            f"ltp={ltp}, limit={limit_price}"
        )
        return {
            'limit_price': limit_price,
            'price_source': 'depth_missing_ltp_fallback',
            'bid': bid,
            'ask': ask,
            'ltp': float(ltp),
            'depth_age_ms': self._depth_age_ms(depth) if depth else None,
        }

    def _aliceblue_limit_price(self, transaction_type, symbol, exch=None, optiontoken=None):
        price_context = self.get_order_push_price(transaction_type, symbol, exch, optiontoken)
        if not price_context:
            return None, None
        return price_context['limit_price'], price_context

    def _place_aliceblue_limit_order(
        self, user, transaction_type, instrument, quantity, product_type,
        symbol, exch=None, optiontoken=None, order_tag='order1'
    ):
        limit_price, price_context = self._aliceblue_limit_price(transaction_type, symbol, exch, optiontoken)
        side = str(getattr(transaction_type, 'value', transaction_type)).upper()
        if price_context:
            self.last_order_price_context[(user, symbol, side)] = price_context
        duplicate, duplicate_data = self._has_recent_broker_order(user, symbol, side)
        if duplicate:
            print(
                f"AliceBlue duplicate order blocked: user={user}, symbol={symbol}, "
                f"side={side}, recent={duplicate_data}"
            )
            broker_order_id = (duplicate_data or {}).get('broker_order_id')
            if broker_order_id:
                return {
                    'status': 'Ok',
                    'message': 'duplicate order already submitted',
                    'duplicate': True,
                    'brokerOrderId': broker_order_id,
                    'recent_order': duplicate_data,
                }
            return {
                'status': 'Not_ok',
                'message': 'duplicate order blocked',
                'duplicate': True,
                'recent_order': duplicate_data,
            }
        orderbook_duplicate = self._aliceblue_orderbook_duplicate(user, symbol, side)
        if orderbook_duplicate:
            print(
                f"AliceBlue duplicate order blocked from orderbook: user={user}, "
                f"symbol={symbol}, side={side}, brokerOrderId={orderbook_duplicate.get('brokerOrderId')}, "
                f"status={orderbook_duplicate.get('orderStatus')}"
            )
            broker_order_id = (
                orderbook_duplicate.get('brokerOrderId')
                or orderbook_duplicate.get('order_id')
                or orderbook_duplicate.get('orderId')
                or orderbook_duplicate.get('id')
            )
            if broker_order_id:
                return {
                    'status': 'Ok',
                    'message': 'duplicate order already present in orderbook',
                    'duplicate': True,
                    'brokerOrderId': broker_order_id,
                    'recent_order': self._json_safe(orderbook_duplicate),
                }
            return {
                'status': 'Not_ok',
                'message': 'duplicate order blocked from orderbook',
                'duplicate': True,
                'recent_order': self._json_safe(orderbook_duplicate),
            }
        if limit_price is None:
            print(
                f"AliceBlue LIMIT order skipped: user={user}, symbol={symbol}, "
                f"side={side}, qty={quantity}"
            )
            return None
        if not exch or not optiontoken:
            print(
                f"AliceBlue LIMIT order skipped: missing instrument token/exchange, "
                f"user={user}, symbol={symbol}, side={side}, qty={quantity}, "
                f"exch={exch}, optiontoken={optiontoken}"
            )
            return None
        self.last_order_price_context[(user, symbol, side)] = price_context or {}

        instrument_payload = {
            'exchange': exch,
            'token': int(optiontoken),
        }

        order_kwargs = {
            'transaction_type': transaction_type,
            'instrument': instrument_payload,
            'quantity': quantity,
            'order_type': OrderType.Limit,
            'product_type': product_type,
            'price': limit_price,
            'trigger_price': None,
            'stop_loss': None,
            'square_off': None,
            'trailing_sl': None,
            'is_amo': False,
            'order_tag': order_tag,
        }

        print(
            f"AliceBlue LIMIT order price: user={user}, symbol={symbol}, "
            f"side={side}, qty={quantity}, limit={limit_price}, "
            f"source={(price_context or {}).get('price_source')}, "
            f"bid={(price_context or {}).get('bid')}, "
            f"ask={(price_context or {}).get('ask')}, "
            f"ltp={(price_context or {}).get('ltp')}"
        )
        ret = self.alice[user].place_order(**order_kwargs)
        if self._is_unauthorized_response(ret):
            refreshed = self._refresh_aliceblue_session(user)
            if refreshed:
                ret = self.alice[user].place_order(**order_kwargs)
        if self._broker_order_ok(ret):
            self._remember_recent_broker_order(
                user, symbol, side,
                broker_order_id=self._extract_broker_order_id(ret),
                status='submitted'
            )
        return ret

    def _is_unauthorized_response(self, ret):
        if not isinstance(ret, dict):
            return False
        text = ' '.join(str(value).lower() for value in ret.values() if value is not None)
        return '401' in text or 'unauthorized' in text

    def _extract_broker_order_id(self, ret):
        if not isinstance(ret, dict):
            return None
        for key in ('brokerOrderId', 'order_id', 'orderId', 'id', 'NOrdNo', 'nestOrderNumber'):
            if ret.get(key):
                return str(ret[key])
        result = ret.get('result') or ret.get('data')
        if isinstance(result, dict):
            return self._extract_broker_order_id(result)
        if isinstance(result, list):
            for item in result:
                broker_order_id = self._extract_broker_order_id(item)
                if broker_order_id:
                    return broker_order_id
        return None

    def _recent_broker_order_key(self, user, symbol, side):
        return (str(user), str(symbol), str(side).upper())

    def _has_recent_broker_order(self, user, symbol, side):
        key = self._recent_broker_order_key(user, symbol, side)
        data = self.recent_broker_order_keys.get(key)
        if not data:
            return False, None
        age = time.time() - float(data.get('time', 0))
        if age <= self.broker_order_duplicate_window_seconds:
            return True, data
        self.recent_broker_order_keys.pop(key, None)
        return False, None

    def _remember_recent_broker_order(self, user, symbol, side, broker_order_id=None, status='submitted'):
        key = self._recent_broker_order_key(user, symbol, side)
        self.recent_broker_order_keys[key] = {
            'time': time.time(),
            'broker_order_id': broker_order_id,
            'status': status,
        }

    def _aliceblue_orderbook_duplicate(self, user, symbol, side):
        alice = getattr(self, 'alice', {}).get(user)
        if not alice:
            return None
        try:
            orderbook = alice.trade.get_orderbook()
        except Exception as e:
            print(f"AliceBlue duplicate check skipped: user={user}, symbol={symbol}, error={e}")
            return None
        rows = orderbook.get('result') if isinstance(orderbook, dict) else None
        if not isinstance(rows, list):
            return None
        now = datetime.datetime.now()
        side = str(side).upper()
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_symbol = row.get('tradingSymbol') or row.get('formattedInstrumentName') or ''
            row_instrument = str(row.get('formattedInstrumentName') or '')
            row_side = str(row.get('transactionType') or '').upper()
            row_status = str(row.get('orderStatus') or '').upper()
            if side not in row_side:
                continue
            if str(symbol) not in str(row_symbol) and str(symbol) not in row_instrument.replace(' ', ''):
                continue
            if row_status not in {'OPEN', 'PENDING', 'TRIGGER_PENDING', 'COMPLETE'}:
                continue
            order_time = row.get('orderTime') or row.get('requestTime')
            try:
                order_dt = datetime.datetime.strptime(order_time, '%Y-%m-%d %H:%M:%S')
                if (now - order_dt).total_seconds() > self.broker_order_duplicate_window_seconds:
                    continue
            except Exception:
                pass
            return row
        return None

    def _refresh_aliceblue_session(self, user, force=False):
        item = self.apis_collection.find_one({'user': user, 'broker': 'aliceblue'})
        if not item:
            print(f"AliceBlue relogin skipped for {user}: no aliceblue API row")
            return False

        refreshed_item = self._refresh_aliceblue_auth(dict(item))
        if not refreshed_item and force:
            self.db["broker_health"].update_one(
                {"user": user, "broker": "aliceblue"},
                {
                    "$set": {
                        "login_status": "rejected",
                        "websocket_status": "disconnected",
                        "last_error": (
                            "AliceBlue saved session was unauthorized and "
                            "automatic session refresh failed"
                        ),
                        "updated_at": datetime.datetime.utcnow(),
                    }
                },
                upsert=True,
            )
            print(
                f"AliceBlue forced relogin failed for {user}: "
                "automatic auth refresh did not produce a new session"
            )
            return False
        refreshed_item = refreshed_item or self.apis_collection.find_one(
            {'user': user, 'broker': 'aliceblue'}
        )
        if not refreshed_item:
            print(f"AliceBlue relogin failed for {user}: auth refresh returned no data")
            return False

        user_id, alice_instance, session_id = self._login_aliceblue(dict(refreshed_item))
        if alice_instance and isinstance(session_id, dict) and 'sessionID' in session_id:
            self.alice[user_id] = alice_instance
            if user_id not in self.userloggedin:
                self.userloggedin.append(user_id)
            if user_id in self.usernotloggedin:
                self.usernotloggedin.remove(user_id)
            print(f"AliceBlue relogin completed for {user_id}; retrying order")
            return True

        if user not in self.usernotloggedin:
            self.usernotloggedin.append(user)
        if user in self.userloggedin:
            self.userloggedin.remove(user)
        print(f"AliceBlue relogin failed for {user}")
        return False

    def _place_aliceblue_market_order(
        self, user, transaction_type, instrument, quantity, product_type, order_tag='order1'
    ):
        return self.alice[user].place_order(
            transaction_type=transaction_type,
            instrument=instrument,
            quantity=quantity,
            order_type=OrderType.Market,
            product_type=product_type,
            price=0,
            trigger_price=None,
            stop_loss=None,
            square_off=None,
            trailing_sl=None,
            is_amo=False,
            order_tag=order_tag
        )

    def _place_aliceblue_square_off(
        self, user, transaction_type, quantity, product_type, symbol,
        exch=None, optiontoken=None, order_tag='order1'
    ):
        side = str(getattr(transaction_type, 'value', transaction_type)).upper()
        duplicate, duplicate_data = self._has_recent_broker_order(user, symbol, side)
        if duplicate:
            print(
                f"AliceBlue square-off duplicate blocked: user={user}, symbol={symbol}, "
                f"side={side}, qty={quantity}, recent={duplicate_data}"
            )
            broker_order_id = (duplicate_data or {}).get('broker_order_id')
            if broker_order_id:
                return {
                    'status': 'Ok',
                    'message': 'duplicate square-off already submitted',
                    'duplicate': True,
                    'brokerOrderId': broker_order_id,
                    'recent_order': duplicate_data,
                }
            return {
                'status': 'Not_ok',
                'message': 'duplicate square-off blocked',
                'duplicate': True,
                'recent_order': duplicate_data,
            }

        if not exch or not optiontoken:
            print(
                f"AliceBlue square-off skipped: missing instrument token/exchange, "
                f"user={user}, symbol={symbol}, side={side}, qty={quantity}, "
                f"exch={exch}, optiontoken={optiontoken}"
            )
            return None

        print(
            f"AliceBlue square-off request: user={user}, symbol={symbol}, "
            f"side={side}, qty={quantity}, exch={exch}, token={optiontoken}"
        )
        ret = self.alice[user].square_off_position(
            transaction_type=transaction_type,
            quantity=quantity,
            product_type=product_type,
            exchange=exch,
            instrument_id=optiontoken,
            symbol=symbol,
            order_type=OrderType.Market,
            price=None,
            trigger_price=None,
            order_tag=order_tag,
        )
        if self._is_unauthorized_response(ret):
            refreshed = self._refresh_aliceblue_session(user)
            if refreshed:
                ret = self.alice[user].square_off_position(
                    transaction_type=transaction_type,
                    quantity=quantity,
                    product_type=product_type,
                    exchange=exch,
                    instrument_id=optiontoken,
                    symbol=symbol,
                    order_type=OrderType.Market,
                    price=None,
                    trigger_price=None,
                    order_tag=order_tag,
                )
        if self._broker_order_ok(ret):
            self._remember_recent_broker_order(
                user, symbol, side,
                broker_order_id=self._extract_broker_order_id(ret),
                status='submitted'
            )
        return ret

    def _json_safe(self, value):
        try:
            json.dumps(value, default=str)
            return value
        except Exception:
            return str(value)

    def _broker_order_ok(self, ret):
        if ret is None:
            return False
        if ret is True:
            return True
        if isinstance(ret, str):
            return bool(ret.strip())
        if not isinstance(ret, dict):
            return bool(ret)

        lowered = {
            str(key).lower(): str(value).lower()
            for key, value in ret.items()
            if value is not None
        }
        for key in ('stat', 'status', 'success'):
            value = lowered.get(key)
            if value is None:
                continue
            if (
                value in ('not_ok', 'failed', 'failure', 'error', 'rejected', 'false')
                or value.startswith('ec')
            ):
                return False
            if value in ('ok', 'success', 'successful', 'true', 'open', 'pending', 'complete'):
                return True

        if any(key.lower() in lowered for key in ('nordno', 'nestordernumber', 'orderid', 'order_id', 'id')):
            return True
        if self._extract_broker_order_id(ret):
            return True

        error_text = ' '.join(
            lowered.get(key, '')
            for key in ('emsg', 'error', 'message', 'reason')
        )
        if any(
            token in error_text
            for token in ('error', 'reject', 'fail', 'invalid', 'unauthoriz', 'not allowed', 'restriction')
        ):
            return False

        return bool(ret)

    def _record_broker_order_result(self, trade, broker, ret, action, symbol, quantity):
        side = str(action or '').upper()
        price_context = (
            self.last_order_price_context.get((trade.get('user'), symbol, side))
            or self.last_order_price_context.get((trade.get('user'), symbol, 'BUY' if side == 'B' else 'SELL' if side == 'S' else side))
            or {}
        )
        result = {
            'broker': broker,
            'action': action,
            'symbol': symbol,
            'quantity': int(quantity),
            'success': self._broker_order_ok(ret),
            'response': self._json_safe(ret),
            'time': int(datetime.datetime.now().timestamp()),
        }
        if price_context:
            result.update({
                'price_source': price_context.get('price_source'),
                'bid': price_context.get('bid'),
                'ask': price_context.get('ask'),
                'ltp': price_context.get('ltp'),
                'limit_price': price_context.get('limit_price'),
                'depth_age_ms': price_context.get('depth_age_ms'),
            })
        if not result['success']:
            print(
                f"Broker order failed: user={trade['user']}, broker={broker}, "
                f"action={action}, symbol={symbol}, qty={quantity}, response={ret}"
            )
        trading_event(
            "broker_order_result",
            user=trade.get("user"),
            strategy_id=trade.get("botcode"),
            broker=broker,
            action=action,
            symbol=symbol,
            quantity=quantity,
            success=result["success"],
            response=result["response"],
        )
        return result

    def _entry_price_from_broker_results(self, broker_order_results, fallback_price):
        prices = []
        for result in broker_order_results or []:
            if not result.get('success'):
                continue
            limit_price = result.get('limit_price')
            if limit_price in (None, ''):
                continue
            try:
                prices.append(float(limit_price))
            except (TypeError, ValueError):
                continue
        if prices:
            return float(sum(prices) / len(prices))
        return float(fallback_price)

    def _initial_position_pnl(self, is_sell, entry_price, current_price, lot, optionlot):
        if is_sell:
            return int((float(entry_price) - float(current_price)) * int(lot) * int(optionlot))
        return int((float(current_price) - float(entry_price)) * int(lot) * int(optionlot))

    def _place_broker_order(self, trade, config, broker, transaction_type, product_type, quantity, side_override=None):
        """Centralized order placement logic for all brokers"""
        z = self.broker_collection.find_one({'user': trade['user']})
        selected_broker = z['selectedbroker'] if z else broker
        if not selected_broker:
            raise RuntimeError(f"No selected broker found for {trade['user']}")
        
        optiontoken = trade['optiontoken']
        exch = trade['exch']
        optionlot = int(trade['optionlot'])
        total_quantity = optionlot * quantity
        trading_event(
            "broker_order_request",
            user=trade.get("user"),
            strategy_id=trade.get("botcode"),
            broker=selected_broker,
            symbol=trade.get("optionname"),
            exchange=trade.get("exch"),
            transaction_type=transaction_type,
            product_type=product_type,
            quantity=total_quantity,
            live=trade.get("live"),
        )
        
        ret = None
        
        if selected_broker == 'paper':
            fill_price = float(trade.get('current_price') or trade.get('optionentry') or trade.get('optionexit') or trade.get('ltp') or 1)
            ret = {
                'status': 'success',
                'broker': 'paper',
                'message': 'Paper order filled',
                'order_id': f"paper-{int(time.time() * 1000)}",
                'transaction_type': transaction_type,
                'symbol': trade.get('optionname'),
                'quantity': total_quantity,
                'fill_price': fill_price,
            }

        elif selected_broker == 'shoonya':
            shoonya = self._get_broker_session('shoonya', trade['user'], 'Shoonya')
            trans1 = 'S' if transaction_type == 'SELL' else 'B'
            pos1 = 'M' if config.get('positiontype') in ['Future', 'Option'] else 'C'
            ret = shoonya.place_order(
                buy_or_sell=trans1, product_type=pos1, exchange=exch,
                tradingsymbol=trade['optionname'], quantity=total_quantity,
                discloseqty=0, price_type='MKT', price=0, trigger_price=0,
                retention='DAY', remarks='my_order_001'
            )
        
        elif selected_broker == 'aliceblue':
            alice = self._get_broker_session('alice', trade['user'], 'AliceBlue')
            trans = TransactionType.Sell if transaction_type == 'SELL' else TransactionType.Buy
            pos = ProductType.Intraday if config.get('positiontype') == 'Equity' else ProductType.Delivery
            ret = self._place_aliceblue_square_off(
                user=trade['user'],
                transaction_type=trans,
                quantity=total_quantity,
                product_type=pos,
                symbol=trade['optionname'],
                exch=exch,
                optiontoken=optiontoken,
                order_tag='order1'
            )
        
        elif selected_broker == 'fyers':
            instrument_name, Exch = self._get_instrument_name('fyers', exch, optiontoken)
            product_type_fyers = 'INTRADAY' if config.get('positiontype') == 'Equity' else 'MARGIN'
            side = -1 if (side_override is None and transaction_type == 'SELL') else (1 if transaction_type == 'BUY' else -1)
            if side_override is not None:
                side = side_override
            
            data = {
                "symbol": f"{Exch}:{instrument_name}", "qty": total_quantity, "type": 2,
                "side": side, "productType": product_type_fyers, "limitPrice": 0.0,
                "stopPrice": 0.0, "validity": "DAY", "disclosedQty": 0,
                "offlineOrder": False, "orderTag": "tag1", "stopLoss": 0.0, "takeProfit": 0.0
            }
            ret = self.fyers[trade['user']].place_order(data=data)
        
        elif selected_broker == 'angelone':
            instrument = self.angelone_scripts[
                (self.angelone_scripts['exch_seg'] == exch) & 
                (self.angelone_scripts['token'] == str(optiontoken))
            ].iloc[-1]
            product_type_angel = 'INTRADAY' if config.get('positiontype') == 'Equity' else 'CARRYFORWARD'
            orderparams = {
                "variety": "NORMAL", "tradingsymbol": instrument['symbol'],
                "symboltoken": instrument['token'],
                "transactiontype": transaction_type, "exchange": exch,
                "ordertype": "MARKET", "producttype": product_type_angel,
                "duration": "DAY", "price": "0", "squareoff": "0",
                "stoploss": "0", "quantity": total_quantity
            }
            ret = self.angelone[trade['user']].placeOrder(orderparams)
        
        elif selected_broker == 'dhan':
            try:
                exch1 = self._get_dhan_exchange_segment(exch)
                ret = self.dhan[trade['user']].place_order(
                    security_id=str(optiontoken), exchange_segment=exch1,
                    transaction_type=transaction_type, quantity=total_quantity,
                    order_type="MARKET", product_type="MARGIN", price=0,
                    trigger_price=0, disclosed_quantity=0, after_market_order=False,
                    validity='DAY', amo_time='OPEN', bo_profit_value=None,
                    bo_stop_loss_Value=None, tag=None
                )
                print(ret)
            except Exception as e:
                trading_exception(
                    "broker_order_error",
                    e,
                    user=trade.get("user"),
                    strategy_id=trade.get("botcode"),
                    broker=selected_broker,
                    symbol=trade.get("optionname"),
                    quantity=total_quantity,
                )
                raise
        
        elif selected_broker == 'zerodha':
            tradingsymbol = self.kiteSymboldf[
                (self.kiteSymboldf['exchange'] == exch) & 
                (self.kiteSymboldf['exchange_token'] == optiontoken)
            ]['tradingsymbol'].iloc[-1]
            ret = self.zerodha[trade['user']].place_order(
                tradingsymbol=tradingsymbol, exchange=exch,
                transaction_type=transaction_type, quantity=total_quantity,
                variety="regular", order_type="MARKET", product="NRML", validity="DAY"
            )
            print(ret)
        
        elif selected_broker == 'mofs':
            exch_map = self._get_exchange_map('mofs')
            z1 = self.db['apis'].find_one({'broker': selected_broker, 'user': trade['user']})
            Orderinfo = {
                "clientcode": z1['client_id'], "exchange": exch_map.get(exch, 'NSE'),
                "symboltoken": optiontoken, "buyorsell": transaction_type,
                "ordertype": "MARKET", "producttype": "NORMAL",
                "orderduration": "DAY", "price": 0, "triggerprice": 0,
                "quantityinlot": quantity, "disclosedquantity": 0,
                "amoorder": "N", "algoid": "", "tag": " "
            }
            ret = self.mofs[trade['user']].PlaceOrder(Orderinfo)
        
        elif selected_broker == 'smc':
            exch_map = self._get_exchange_map('smc')
            ret = self.smc[trade['user']].place_order(
                exchangeSegment=exch_map.get(exch, 'NSEFO'),
                exchangeInstrumentID=int(optiontoken), productType='NRML',
                orderType='MARKET', orderSide=transaction_type,
                timeInForce='DAY', disclosedQuantity=0,
                orderQuantity=total_quantity, limitPrice=0, stopPrice=0,
                apiOrderSource="WEBAPI", orderUniqueIdentifier="123abc"
            )
        
        elif selected_broker == 'mstock':
            instrument_name, Exch = self._get_instrument_name('mstock', exch, optiontoken)
            apikey = self.mstock[trade['user']]['apikey']
            access_token = self.mstock[trade['user']]['access_token']
            headers = {
                'X-Mirae-Version': '1',
                'Authorization': f'token {apikey}:{access_token}',
                'Content-Type': 'application/x-www-form-urlencoded',
            }
            data = {
                'tradingsymbol': instrument_name, 'exchange': exch,
                'transaction_type': transaction_type, 'order_type': 'MARKET',
                'quantity': total_quantity, 'product': 'NRML',
                'validity': 'DAY', 'price': '0', 'variety': 'regular'
            }
            response = requests.post(
                'https://api.mstock.trade/openapi/typea/orders/regular',
                headers=headers, data=data
            )
            ret = response.json()
        
        print(ret)
        return ret

    def _execute_order_with_slicing(self, trade, config, transaction_type, side_override=None):
        """Execute order with optional quantity slicing"""
        lot = int(trade['lot'])
        slicing_qty = config.get('slicing', lot)
        
        if lot > 20 and slicing_qty > 0:
            totalquant = [slicing_qty] * int(lot / slicing_qty)
            if (lot % slicing_qty) > 0:
                totalquant.append(lot % slicing_qty)
            
            for quant in totalquant:
                self._place_broker_order(trade, config, None, transaction_type, 
                                        config.get('positiontype', ''), quant, side_override)
        else:
            self._place_broker_order(trade, config, None, transaction_type, 
                                    config.get('positiontype', ''), lot, side_override)

    # ============== Original Function Names (Required) ==============

    def mainebuyexit(self, trade, config):
        """Exit buy position - determines transaction type based on trade side and position type"""
        if config.get('positiontype') in ['Future', 'Equity']:
            transaction_type = 'SELL' if trade['side'] == 'BUY' else 'BUY'
        else:
            transaction_type = 'SELL'
        
        self._execute_order_with_slicing(trade, config, transaction_type)

    def mainsplitbuyexit(self, trade, config):
        """Split exit for buy positions - always SELL"""
        self._execute_order_with_slicing(trade, config, 'SELL')

    def mainsplitsellexit(self, trade, config):
        """Split exit for sell positions - always BUY"""
        self._execute_order_with_slicing(trade, config, 'BUY')

    def mainbuyexit(self, trade, config):
        """Buy exit with slicing - always SELL"""
        self._execute_order_with_slicing(trade, config, 'SELL')

    def mainsellexit(self, trade, config):
        """Sell exit with slicing - always BUY"""
        self._execute_order_with_slicing(trade, config, 'BUY')

    def _isholiday(self):
        if datetime.date.today().strftime('%A') not in ['Saturday', 'Sunday']:
            return True
        return False

    def _add_log(self, msg: str):
        logger.info("%s", msg)
        self.logs.append({"log": msg, "displayed": False})

    def optionchain(self, exch, option, strike, count=10):
        ret = self.api.get_option_chain(exchange=exch, tradingsymbol=option,
                                        strikeprice=strike, count=count)
        # print(ret)
        return ret

    def inmkttime(self, symbol):
        if symbol in self.Nselist:
            return self.Markettime['NSE']['start'], self.Markettime['NSE']['end']
        elif symbol in self.Nfolist:
            return self.Markettime['NFO']['start'], self.Markettime['NFO']['end']
        elif symbol in self.Cdslist:
            return self.Markettime['CDS']['start'], self.Markettime['CDS']['end']
        elif symbol in self.Mcxlist:
            return self.Markettime['MCX']['start'], self.Markettime['MCX']['end']

    def add_to_websocket(self, token):
        if self.api is None:
            return
        if type(token) == str:
            if token not in self.subscribe_list:
                self.subscribe_list.append(token)
            self.api.subscribe(token)
        elif type(token) == list:
            new_tokens = []
            for item in token:
                if item not in self.subscribe_list:
                    self.subscribe_list.append(item)
                new_tokens.append(item)
            if new_tokens:
                self.api.subscribe(new_tokens)
        else:
            print('')
    def add_to_swebsocket(self, token):
        if type(token) == str:
            if token not in self.subscribe_slist:
                self.subscribe_slist.append(token)
            self.updatelist=True
            #self.api.subscribe(token)
        elif type(token) == list:
            for item in token:
                if item not in self.subscribe_slist:
                    self.subscribe_slist.append(item)
            self.updatelist=True
            #self.api.subscribe(token)
        else:
            print('')

    def _load_upstox_candles(self, symbol, days=7):
        instrument = self.upstoxtok_symbols.get(symbol)
        if not instrument:
            return pd.DataFrame()

        end_date = datetime.datetime.now().strftime('%Y-%m-%d')
        start_date = (
            datetime.datetime.now() - datetime.timedelta(days=days)
        ).strftime('%Y-%m-%d')
        encoded_instrument = quote(str(instrument), safe='')
        urls = [
            (
                f'https://api-v2.upstox.com/historical-candle/'
                f'{encoded_instrument}/1minute/{end_date}/{start_date}'
            ),
            (
                f'https://api-v2.upstox.com/historical-candle/intraday/'
                f'{encoded_instrument}/1minute'
            ),
        ]
        candles = []
        headers = {'accept': 'application/json', 'Api-Version': '2.0'}
        for url in urls:
            try:
                response = requests.get(url, headers=headers, timeout=20)
                response.raise_for_status()
                candles.extend(
                    response.json().get('data', {}).get('candles', [])
                )
            except Exception as exc:
                trading_exception(
                    "market_data_request_error",
                    exc,
                    provider="upstox",
                    symbol=symbol,
                    url=url,
                )

        if not candles:
            return pd.DataFrame()

        frame = pd.DataFrame(
            candles,
            columns=['date', 'open', 'high', 'low', 'close', 'volume', 'oi'],
        )
        frame['date'] = pd.to_datetime(frame['date'])
        frame['time'] = frame['date'].dt.strftime('%d-%m-%Y %H:%M:%S')
        frame['sqlite_timestamp'] = frame['date'].dt.strftime('%Y-%m-%d %H:%M:%S')
        frame['symbol'] = symbol
        for column in ('open', 'high', 'low', 'close', 'volume'):
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
        return (
            frame[['date', 'open', 'high', 'low', 'close', 'volume', 'time',
                   'sqlite_timestamp', 'symbol']]
            .dropna(subset=['date', 'open', 'high', 'low', 'close'])
            .drop_duplicates(subset='time')
            .sort_values('date')
            .reset_index(drop=True)
        )

    def hist(self, symbol, tf="1", initial=True):
        
            #try:
            #if symbol not in  list(self.dataframes.keys()):
            #    self.dataframes[symbol]=[]
            #if len(self.dataframes[symbol])<10:
            #print(self.lastupdates)
            if symbol not in list(self.lastupdates.keys()):
                self.lastupdates[symbol]=(datetime.datetime.now() - datetime.timedelta(minutes=5)).strftime('%d-%m-%Y %H:%M:%S')
            if pd.to_datetime(datetime.datetime.now()) > (pd.to_datetime(self.lastupdates[symbol],format='%d-%m-%Y %H:%M:%S') + pd.to_timedelta(2, unit='minute')):
                if initial:
                    lastBusDay = datetime.datetime.today() - datetime.timedelta(days=45)
                    lastBusDay = lastBusDay.replace(hour=9, minute=15, second=0, microsecond=0)
                    nextday = lastBusDay.replace(hour=15, minute=30, second=0, microsecond=0) + datetime.timedelta(days=50)
                else:
                    lastBusDay = datetime.datetime.now() - datetime.timedelta(hours=1)
                    lastBusDay = lastBusDay.replace(hour=9, minute=15, second=0, microsecond=0)
                    nextday = lastBusDay.replace(hour=15, minute=30, second=0, microsecond=0) + datetime.timedelta(days=150)

                # Fetch historical data from the API
                #print(symbol)
                #print(self.tok_symbols[symbol])
                #print(symbol)
                if self.api is None:
                    df1m = self._load_upstox_candles(symbol)
                    if df1m.empty:
                        trading_event(
                            "candle_load_result",
                            force=True,
                            provider="upstox",
                            symbol=symbol,
                            candles=0,
                        )
                        return None
                    self.lastupdates[symbol] = df1m['time'].iloc[-1]
                    existing = self.dataframes.get(symbol)
                    if initial or not isinstance(existing, pd.DataFrame):
                        self.dataframes[symbol] = df1m
                    else:
                        self.dataframes[symbol] = (
                            pd.concat([existing, df1m])
                            .drop_duplicates(subset='time')
                            .sort_values('date')
                            .reset_index(drop=True)
                        )
                    trading_event(
                        "candle_load_result",
                        force=True,
                        provider="upstox",
                        symbol=symbol,
                        candles=len(self.dataframes[symbol]),
                    )
                    return self.lastupdates[symbol]

                ret = self.api.get_time_price_series(
                    exchange=self.tok_symbols[symbol][:3],
                    token=self.tok_symbols[symbol][4:],
                    starttime=lastBusDay.timestamp(),
                    endtime=nextday.timestamp(),
                    interval=tf
                )
                # Process data into DataFrame
                
                df = pd.DataFrame(ret)

                if not df.empty:
                    #df = df.iloc[::-1].reset_index()
                    df['date'] = df['time']#pd.to_datetime(df['time'])
                    df['open'] = df['into'].astype(float)
                    df['high'] = df['inth'].astype(float)
                    df['close'] = df['intc'].astype(float)
                    df['low'] = df['intl'].astype(float)
                    df['volume'] = df['intv'].astype(int)
                    df1m = df[['date', 'open', 'high', 'low', 'close', 'volume', 'time']]
                    # Database handling
                    df1m['date']=pd.to_datetime(df1m['time'],format='%d-%m-%Y %H:%M:%S')
                    df1m['sqlite_timestamp'] = df1m['date'].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S'))
                    df1m= df1m.iloc[::-1]
                    #print(df1m['time'].iloc[-1])
                    #if 'SILVERM' in symbol:
                    #    print(df1m)
                    self.lastupdates[symbol] = df1m['time'].iloc[-1]
                    df1m['symbol'] = symbol
                    

                    #print(df1m.tail(5))
                    if initial:
                        df1m=df1m.reset_index(drop=True)
                        self.dataframes[symbol]=df1m
                    else:
                        tdf=self.dataframes[symbol].copy()
                        self.dataframes[symbol] = pd.concat([df1m, tdf]).drop_duplicates(subset='time').sort_values(by='date', ascending=True).reset_index(drop=True)
                    #
                    #if 'SILVERM' in symbol:
                    #    print(self.dataframes[symbol])
                    return self.lastupdates

    def equityhist(self, symbol, tf="1", initial=True):
        # Calculate time frame
        try:
            if self.topbottomlist:
                if str(self.lastupdate) == '0' or initial:
                    lastBusDay = datetime.datetime.today() - datetime.timedelta(days=45)
                    lastBusDay = lastBusDay.replace(hour=9, minute=15, second=0, microsecond=0)
                    nextday = lastBusDay.replace(hour=15, minute=30, second=0, microsecond=0) + datetime.timedelta(days=50)
                else:
                    lastBusDay = datetime.datetime.now() - datetime.timedelta(hours=1)
                    lastBusDay = lastBusDay.replace(hour=9, minute=15, second=0, microsecond=0)
                    nextday = lastBusDay.replace(hour=15, minute=30, second=0, microsecond=0) + datetime.timedelta(hours=5)

                # Fetch historical data from the API
                ret = self.api.get_time_price_series(
                    exchange=self.tok_symbols[symbol][:3],
                    token=self.tok_symbols[symbol][4:],
                    starttime=lastBusDay.timestamp(),
                    endtime=nextday.timestamp(),
                    interval=tf
                )
                # Process data into DataFrame
                df = pd.DataFrame(ret)
                if not df.empty:
                    #df = df.iloc[::-1].reset_index()
                    df['date'] = df['time']#pd.to_datetime(df['time'])
                    df['open'] = df['into'].astype(float)
                    df['high'] = df['inth'].astype(float)
                    df['close'] = df['intc'].astype(float)
                    df['low'] = df['intl'].astype(float)
                    df['volume'] = df['intv'].astype(int)
                    df1m = df[['date', 'open', 'high', 'low', 'close', 'volume', 'time']]
                    
                    
                    # Database handling
                    df1m['date']=pd.to_datetime(df1m['time'],format='%d-%m-%Y %H:%M:%S')
                    df1m['sqlite_timestamp'] = df1m['date'].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S'))
                    df1m= df1m.iloc[::-1]
                    #print(df1m['time'].iloc[-1])
                    self.lastupdate = df1m['time'].iloc[-1]
                    df1m['symbol'] = symbol
                    

                    #print(df1m.tail(5))
                    if initial:
                        df1m=df1m.reset_index(drop=True)
                        self.dataframes[symbol]=df1m
                    else:
                        tdf=self.dataframes[symbol].copy()
                        self.dataframes[symbol] = pd.concat([df1m, tdf]).drop_duplicates(subset='time').sort_values(by='date', ascending=True).reset_index(drop=True)


                    return self.lastupdate
            else:
                time.sleep(1)
                return 0
            
        except:
            return 0
            pass



    def hist1(self, symbol, tf="1", initial=True):
        if str(self.lastupdate) == '0' or initial:
            lastBusDay = datetime.datetime.today() - datetime.timedelta(days=45)
            lastBusDay = lastBusDay.replace(hour=9, minute=15, second=0, microsecond=0)
            nextday = lastBusDay.replace(hour=15, minute=30, second=0, microsecond=0) + datetime.timedelta(days=50)
        else:
            lastBusDay = datetime.datetime.now() - datetime.timedelta(hours=1)
            lastBusDay = lastBusDay.replace(hour=9, minute=15, second=0, microsecond=0)
            nextday = lastBusDay.replace(hour=15, minute=30, second=0, microsecond=0) + datetime.timedelta(hours=5)

        ret = self.api.get_time_price_series(
            exchange=self.tok_symbols[symbol][:3],
            token=self.tok_symbols[symbol][4:],
            starttime=lastBusDay.timestamp(),
            endtime=nextday.timestamp(),
            interval=tf
        )

        df = pd.DataFrame(ret)
        #print(df)
        df = df.iloc[::-1]
        df=df.reset_index()
        df['date']=pd.to_datetime(df['time'])#+ pd.to_timedelta(1, unit='minute')
        df['open'] = df['into'].apply(float)
        df['high'] = df['inth'].apply(float)
        df['close'] = df['intc'].apply(float)
        df['low'] = df['intl'].apply(float)
        df['volume'] = df['intv'].apply(int)
        df1m=df[['date','open','high','low','close','volume','time']]
        df1m['symbol']=symbol
        df=df1m

        # Use ThreadPoolExecutor for parallel updates
        updated_data = df1m.to_dict('records')
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            executor.map(self.update_row, updated_data)

        self.lastupdate = df1m['date'].iloc[-1]
        return self.lastupdate
    def update_row(self,data):
        query = {"date": data["date"], "symbol": data["symbol"]}
        update = {"$set": data}
        #print(data)
        self.history_collection.update_many(query, update, upsert=True)
    def xATRTrailingStop_func(self,close, prev_close, prev_atr, nloss):
            if close > prev_atr and prev_close > prev_atr:
                return max(prev_atr, close - nloss)
            elif close < prev_atr and prev_close < prev_atr:
                return min(prev_atr, close + nloss)
            elif close > prev_atr:
                return close - nloss
            else:
                return close + nloss
    def utbot(self,df: pd.DataFrame,SENSITIVITY: int = 4,ATR_PERIOD: int = 10):

        df["TR"] = np.maximum(df["high"] - df["low"],np.maximum(np.abs(df["high"] - df["close"].shift(1)),np.abs(df["low"] - df["close"].shift(1))))
        df["ATR"] = df["TR"].rolling(window=ATR_PERIOD).mean()
        df["nLoss"] = SENSITIVITY * df["ATR"]
        df = df.reset_index(drop=True)
        # Function to compute ATRTrailingStop
       
        # Filling ATRTrailingStop Variable
        df["ATRTrailingStop"] = [0.0] + [np.nan for i in range(len(df) - 1)]
        for i in range(1, len(df)):
            df.loc[i, "ATRTrailingStop"] = self.xATRTrailingStop_func(
                df.loc[i, "close"],
                df.loc[i - 1, "close"],
                df.loc[i - 1, "ATRTrailingStop"],
                df.loc[i, "nLoss"],
            )
        # Calculate Buy and Sell signals
        df["Buy_Signal"] = (df["close"] > df["ATRTrailingStop"]) & (df["close"].shift(1) <= df["ATRTrailingStop"].shift(1))
        df["Sell_Signal"] = (df["close"] < df["ATRTrailingStop"]) & (df["close"].shift(1) >= df["ATRTrailingStop"].shift(1))
        # Print the resulting DataFrame with the calculated indicators and signals
        df2=(df[["date", "close", "ATR", "nLoss", "ATRTrailingStop"]])
        #df2['result']=np.where(df2['Buy_Signal'],0,np.where(df2['Sell_Signal'],1,2))
        df2['result']=np.where(df["close"] > df["ATRTrailingStop"] ,0,np.where(df["close"] < df["ATRTrailingStop"],1,2))
        return list(df2['result'])

    def ASSALGO(self,data: pd.DataFrame, amplitude: int = 2, channel_deviation: float = 2) -> pd.DataFrame:
        if data.empty:
            return [2,2,2,2,2,2]
        atr2 = TA.ATR(data, 100) / 2
        dev = channel_deviation * atr2
        high_price = data.high.rolling(amplitude).max().fillna(0)
        low_price = data.low.rolling(amplitude).min().fillna(0)
        highma = TA.EMA(data, period=amplitude, column="high")
        lowma = TA.EMA(data, period=amplitude, column="low")

        trend = np.zeros(len(data))
        next_trend = np.zeros(len(data))
        max_low_price = np.zeros(len(data))
        max_low_price[0] = data.low[0]
        min_high_price = np.zeros(len(data))
        min_high_price[0] = data.high[0]

        for i in range(1, len(data)):
            if next_trend[i - 1] == 1:
                max_low_price[i] = max(low_price[i - 1], max_low_price[i - 1])

                if highma[i] < max_low_price[i] and data.close[i] < data.low[i - 1]:
                    trend[i] = 1
                    next_trend[i] = 0
                    min_high_price[i] = high_price[i]
                else:
                    # assign previous values again
                    trend[i] = trend[i - 1]
                    next_trend[i] = next_trend[i - 1]
                    min_high_price[i] = min_high_price[i - 1]
            else:
                min_high_price[i] = min(high_price[i - 1], min_high_price[i - 1])

                if lowma[i] > min_high_price[i] and data.close[i] > data.high[i - 1]:
                    trend[i] = 0
                    next_trend[i] = 1
                    max_low_price[i] = low_price[i]
                else:
                    # assign previous values again
                    trend[i] = trend[i - 1]
                    next_trend[i] = next_trend[i - 1]
                    max_low_price[i] = max_low_price[i - 1]

        up = np.zeros(len(data))
        up[0] = max_low_price[0]
        down = np.zeros(len(data))
        down[0] = min_high_price[0]
        atr_high = np.zeros(len(data))
        atr_low = np.zeros(len(data))

        for i in range(1, len(data)):
            if trend[i] == 0:
                if trend[i - 1] != 0:
                    up[i] = down[i - 1]
                else:
                    up[i] = max(max_low_price[i - 1], up[i - 1])

                atr_high[i] = up[i] + dev[i]
                atr_low[i] = up[i] - dev[i]

            else:
                if trend[i - 1] != 1:
                    down[i] = up[i - 1]
                else:
                    down[i] = min(min_high_price[i - 1], down[i - 1])

                atr_high[i] = down[i] + dev[i]
                atr_low[i] = down[i] - dev[i]

        
        return trend
    def OptionList(self, Symbol):
        import datetime
        if Symbol in ['NIFTY', 'BANKNIFTY']:
            nextweek = False
            if 'BANKNIFTY' == Symbol:

                Symbol = 'BANKNIFTY'
                indexltp = float(self.api.get_quotes('NSE', '26009')['lp'])
                main = 100
                mod = int(indexltp) % 100
                ran = 50
            elif 'NIFTY' == Symbol:
                Symbol = 'NIFTY'
                indexltp = float(self.api.get_quotes('NSE', '26000')['lp'])
                main = 50
                mod = int(indexltp) % 50
                ran = 25
            elif 'FINNIFTY' == Symbol:
                Symbol = 'FINNIFTY'
                indexltp = float(self.api.get_quotes('NSE', '26037')['lp'])
                main = 50
                mod = int(indexltp) % 50
                ran = 25
            if mod < ran:
                atmstrike = int(math.floor(indexltp/main))*main
            else:
                atmstrike = int(math.ceil(indexltp/main))*main

            k = self.Nfo[self.Nfo['Symbol'] == Symbol]
            k['Expiry_'] = k['Expiry_'].dt.date
            stre = list(k['Expiry_'].unique())
            if (datetime.date.today() in stre) and nextweek:
                q = list(k['Expiry_'].unique())
                q.sort(reverse=False)
                q = q[1]
                k = k[k['Expiry_'] == q]
            else:
                q = list(k['Expiry_'].unique())
                q.sort(reverse=False)
                q = q[0]
                k = k[k['Expiry_'] == q]
            k = k[k['StrikePrice'] == float(atmstrike)]
            # if ordertype=='buy':
            # k=bk[bk['OptionType']=='CE']
            k = k.iloc[-1]
            # k=k.T.to_dict()[list(k.T.to_dict())[0]]

            from dateutil.relativedelta import relativedelta

            j = self.Nfo[self.Nfo['Symbol'] == Symbol]
            j = j[j['OptionType'] == 'XX']
            j['Expiry_1'] = j['Expiry_'].dt.date
            day = datetime.date.today()
            j['Expiry_2'] = j['Expiry_'].dt.month
            j = j[(j['Expiry_1'] > day)]
            today = datetime.datetime.today().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            j = j[j['Expiry_2'] == (
                today+relativedelta(months=0)).month].iloc[-1]
            k = pd.DataFrame(self.api.get_option_chain(
                'NFO', k['TradingSymbol'], atmstrike, 11)['values'])
            k['FToken'] = k['exch']+'|'+k['token']

            return k, j, atmstrike
        elif Symbol == 'CRUDEOIL':
            from dateutil.relativedelta import relativedelta
            import datetime
            k = self.Mcx[self.Mcx['Symbol'] == 'CRUDEOIL']
            k = k[k['OptionType'] == 'XX']
            k['Expiry_1'] = k['Expiry_'].dt.date
            day = datetime.date.today()
            k['Expiry_2'] = k['Expiry_'].dt.month
            k = k[(k['Expiry_1'] > day)]
            # k=k.iloc[0]
            today = datetime.datetime.today().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            j = k[k['Expiry_2'] == (
                today+relativedelta(months=0)).month].iloc[-1]

            indexltp = float(self.api.get_quotes('MCX', str(j['Token']))['lp'])
            mod = int(indexltp)
            # print(mod)
            if mod < 25:
                atmstrike = int(math.floor(indexltp/50))*50
            else:
                atmstrike = int(math.ceil(indexltp/50))*50

            k = self.Mcx[self.Mcx['Symbol'] == 'CRUDEOIL']
            k = k[k['OptionType'] == 'CE']
            k['Expiry_1'] = k['Expiry_'].dt.date
            day = datetime.date.today()
            k['Expiry_2'] = k['Expiry_'].dt.month
            k = k[(k['Expiry_1'] > day)].iloc[0]
            # k=k.iloc[0]
            # Mcx[((Mcx['StrikePrice'].apply(float))==6700.0) & (Mcx['Expiry']==k['Expiry'])]
            k = pd.DataFrame(self.api.get_option_chain(
                'MCX', k['TradingSymbol'], atmstrike, 5)['values'])
            k['FToken'] = k['exch']+'|'+k['token']
            return k, j, atmstrike
        elif Symbol == 'USDINR':
            from dateutil.relativedelta import relativedelta
            import datetime
            k = self.Cds[self.Cds['Symbol'] == 'USDINR']
            k = k[k['OptionType'] == 'XX']
            k['Expiry_1'] = k['Expiry_'].dt.date
            k = k.sort_values(by='Expiry_', ascending=True)
            day = datetime.date.today()
            k = k[(k['Expiry_1'] > day)]
            k = k.iloc[0]
            indexltp = float(self.api.get_quotes(
                'CDS', str(k['Token']))['lp'])*1000
            mod = int(indexltp*100) % 250
            # print(mod)
            if mod < 25:
                atmstrike = int(math.floor(indexltp/250))*250/1000
            else:
                atmstrike = int(math.ceil(indexltp/250))*250/1000
            j = pd.DataFrame(self.api.get_option_chain(
                'CDS', k['TradingSymbol'], atmstrike, 5)['values'])
            j['FToken'] = j['exch']+'|'+j['token']
            return j, k, atmstrike


    def add_symbol_to_websocket(self, symbol, force=False):
        destory=False
        if force and symbol in self.loadedwatchsymbols:
            token = self.tok_symbols.get(symbol)
            if token and self.api is not None:
                try:
                    self.api.subscribe(token)
                except Exception as e:
                    print(f"websocket repair failed for {symbol}: {e}")
                    return True
            return False

        if symbol in self.loadedwatchsymbols:
            token = self.tok_symbols.get(symbol)
            stoken = self.stok_symbols.get(symbol)
            if token:
                try:
                    self.add_to_websocket(token)
                except Exception as e:
                    print(f"websocket resubscribe failed for {symbol}: {e}")
                    return True
            if stoken:
                self.add_to_swebsocket(stoken)
            self._subscribe_aliceblue_depth_for_symbol(symbol, token)
            return False

        if symbol not in self.loadedwatchsymbols:
            token = ''
            stoken=''
            if symbol in list(self.Mcx['TradingSymbol']):
                row=self.Mcx[self.Mcx['TradingSymbol'] == str(symbol)].iloc[-1]
                token = row['FToken']
                stoken = row['SToken']
            elif symbol in list(self.Nse['TradingSymbol']):
                row = self.Nse[self.Nse['TradingSymbol'] == str(symbol)].iloc[-1]
                token = row['FToken']
                stoken = row['SToken']
            elif symbol in list(self.Bse['TradingSymbol']):
                row = self.Bse[self.Bse['TradingSymbol'] == str(symbol)].iloc[-1]
                token = row['FToken']
                stoken = row['SToken']
            elif symbol in list(self.Nfo['TradingSymbol']):
                row = self.Nfo[self.Nfo['TradingSymbol'] == str(symbol)].iloc[-1]
                token = row['FToken']
                stoken = row['SToken']
            elif symbol in list(self.Bfo['TradingSymbol']):
                row = self.Bfo[self.Bfo['TradingSymbol'] == str(symbol)].iloc[-1]
                token = row['FToken']
                stoken = row['SToken']
            elif symbol in list(self.Cds['TradingSymbol']):
                row = self.Cds[self.Cds['TradingSymbol']== str(symbol)].iloc[-1]
                token = row['FToken']
                stoken = row['SToken']
            else:
                destory=True

            if token != '':
                self.tok_symbols[symbol] = token
                self.loadedwatchsymbols.append(symbol)
                self.subscribe_list.append(token)
                existing_symbol = self.symbols_tok.get(token)
                if existing_symbol and existing_symbol != symbol:
                    print(
                        f"token mapping conflict for {token}: "
                        f"{existing_symbol} -> {symbol}"
                    )
                self.symbols_tok[token] = symbol
                if stoken in self.samlist:
                    self.subscribe_slist.append(stoken)
                    self.stok_symbols[symbol] = stoken
                    self.symbols_stok[stoken] = symbol
                    self.add_to_swebsocket(stoken)

                self.add_to_websocket(token)
                self._subscribe_aliceblue_depth_for_symbol(symbol, token, row)
        return destory
    def symbol_to_token(self, symbol):
        destory=False
        token = 0
        stoken=''
        #if symbol not in self.loadedwatchsymbols:
            
        if symbol in list(self.Mcx['TradingSymbol']):
            row=self.Mcx[self.Mcx['TradingSymbol'] == str(symbol)].iloc[-1]
            token = row['Token']
            stoken = row['SToken']
        elif symbol in list(self.Nse['TradingSymbol']):
            row = self.Nse[self.Nse['TradingSymbol'] == str(symbol)].iloc[-1]
            token = row['Token']
            stoken = row['SToken']
        elif symbol in list(self.Bse['TradingSymbol']):
            row = self.Bse[self.Bse['TradingSymbol'] == str(symbol)].iloc[-1]
            token = row['FToken']
            stoken = row['SToken']
        elif symbol in list(self.Nfo['TradingSymbol']):
            row = self.Nfo[self.Nfo['TradingSymbol'] == str(symbol)].iloc[-1]
            token = row['Token']
            stoken = row['SToken']
        elif symbol in list(self.Bfo['TradingSymbol']):
            row = self.Bfo[self.Bfo['TradingSymbol'] == str(symbol)].iloc[-1]
            token = row['Token']
            stoken = row['SToken']
        elif symbol in list(self.Cds['TradingSymbol']):
            row = self.Cds[self.Cds['TradingSymbol']== str(symbol)].iloc[-1]
            token = row['Token']
            stoken = row['SToken']
        else:
            destory=True

            
        return int(token)

    def event_handler_order_update(self, message):
        print("order event: " + str(message))

    def event_handler_feed_update(self, inmessage):

        #print(f"feed update {inmessage}")
        self.SYMBOLDICT  # ,self.indexprice
        # e   Exchange
        # tk  Token
        # lp  LTP
        # pc  Percentage change
        # v   volume
        # o   Open price
        # h   High price
        # l   Low price
        # c   Close price
        # ap  Average trade price

        depth_keys = {
            'bp1', 'sp1', 'bid', 'ask', 'best_bid', 'best_ask', 'bestBid', 'bestAsk',
            'bids', 'asks', 'bidDepth', 'askDepth', 'buy', 'sell'
        }
        if isinstance(inmessage, dict) and depth_keys.intersection(inmessage.keys()):
            try:
                key = inmessage.get('e') and inmessage.get('tk') and f"{inmessage['e']}|{inmessage['tk']}"
                symbol = self.symbols_tok.get(key) if key else None
                self._remember_market_depth(
                    symbol=symbol,
                    exch=inmessage.get('e'),
                    token=inmessage.get('tk'),
                    depth=inmessage
                )
            except Exception:
                pass

        # print(indexprice)

        if 'lp' in inmessage:
            fields = ['ts', 'lp']

            message = {field: inmessage[field]
                       for field in set(fields) & set(inmessage.keys())}

            # feedtime = int(inmessage['ft'])
            # message['ft'] = str(datetime.datetime.fromtimestamp( feedtime ))

            # print("quote event: {0}".format(time.strftime('%d-%m-%Y %H:%M:%S')) + str(inmessage))

            # print(message)

            key = inmessage['e'] + '|' + inmessage['tk']
            symbol = self.symbols_tok.get(key)
            price = float(message['lp'])

            if symbol and self._is_suspicious_option_price(symbol, price):
                print(
                    f"ignored suspicious websocket price for {symbol} "
                    f"via {key}: {price}"
                )
                self._recover_suspicious_option_tick(symbol, key)
                return

            if key in self.SYMBOLDICT:
                # print(key)
                # symbol_info =  self.SYMBOLDICT[key]
                # symbol_info.update(message)
                # print(symbol_info['lp'])
                self.SYMBOLDICT[key] = message['lp']
                # print(SYMBOLDICT[key]['lp'])
                self.prices[self.symbols_tok[key]] = price
            else:
                self.SYMBOLDICT[key] = message['lp']
                self.prices[self.symbols_tok[key]] = price
            # pd.DataFrame.from_dict(SYMBOLDICT).transpose()

    def open_callback(self):
        # global self.feed_opened
        self.feed_opened = True
        self.api.subscribe(self.subscribe_list)
    def close_callback(self):
        # global self.feed_opened
        self.feed_opened = False
        print(' iam sorry i am disconnect')
        #self.api.subscribe(self.subscribe_list)

    def error_callback(self, error):
        self.feed_opened = False
        print(f'websocket error: {error}')

    def _recover_suspicious_option_tick(self, symbol, key):
        try:
            self.prices.pop(symbol, None)
        except Exception:
            pass

        exchange = None
        token = None
        try:
            if key and '|' in str(key):
                exchange, raw_token = str(key).split('|', 1)
                token = int(raw_token)
        except Exception:
            token = None

        try:
            self.add_symbol_to_websocket(symbol, force=True)
        except Exception as e:
            print(f"suspicious tick resubscribe failed for {symbol}: {e}")

        try:
            recovered = self._get_direct_quote_price(symbol, exchange, token)
            if recovered is not None:
                print(
                    f"suspicious tick recovered via direct quote for "
                    f"{symbol}: {recovered}"
                )
        except Exception as e:
            print(f"suspicious tick quote recovery failed for {symbol}: {e}")

    def _extract_option_underlying(self, symbol):
        match = re.match(r'^([A-Z]+)\d{2}[A-Z]{3}\d{2}[CP]\d+$', str(symbol or ''))
        if match:
            return match.group(1)
        return None

    def _is_suspicious_option_price(self, symbol, price):
        underlying = self._extract_option_underlying(symbol)
        if not underlying:
            return False

        try:
            price = float(price)
        except (TypeError, ValueError):
            return True

        if price <= 0:
            return True

        spot = None
        for source in (self.prices, self.sprices):
            raw_spot = source.get(underlying)
            if raw_spot in (None, ''):
                continue
            try:
                spot = float(raw_spot)
                break
            except (TypeError, ValueError):
                continue

        if not spot or spot <= 0:
            return False

        # If an option price looks like the underlying spot price, reject it.
        return price > (spot * 0.5) or abs(price - spot) <= max(spot * 0.02, 5)

    def _get_direct_quote_price(self, symbol, exchange=None, token=None):
        if not exchange or not hasattr(self, 'api') or not self.api:
            return None

        for lookup in (str(token), str(symbol)):
            if not lookup or lookup == 'None':
                continue
            try:
                quote = self.api.get_quotes(exchange, lookup)
                if isinstance(quote, dict) and quote.get('lp') not in (None, ''):
                    price = float(quote['lp'])
                    if self._is_suspicious_option_price(symbol, price):
                        print(
                            f"suspicious direct quote for {symbol}: "
                            f"{price} from {lookup}"
                        )
                        continue
                    self.prices[symbol] = price
                    return price
            except Exception as e:
                print(f"quote fallback failed for {symbol} via {lookup}: {e}")

        return None

    def _get_fresh_depth_market_price(self, symbol, exchange=None, token=None):
        depth_candidates = []
        market_depths = getattr(self, 'market_depths', {})
        if symbol:
            depth_candidates.append(market_depths.get(str(symbol)))
        if exchange and token:
            depth_candidates.append(market_depths.get(f"{exchange}|{token}"))

        for depth in depth_candidates:
            if not isinstance(depth, dict) or not self._is_depth_fresh(depth):
                continue
            price = self._first_positive_float(
                depth.get('lp'),
                depth.get('ltp'),
                depth.get('last_price'),
                depth.get('lastPrice'),
            )
            if price is None:
                bid, ask = self._extract_level1_from_depth(depth)
                if bid and ask:
                    price = (float(bid) + float(ask)) / 2
                else:
                    price = ask or bid
            if price is None or self._is_suspicious_option_price(symbol, price):
                continue
            price = float(price)
            self.prices[symbol] = price
            return price
        return None

    def _get_market_price(self, symbol, exchange=None, token=None):
        """Return latest LTP, falling back to the latest candle close."""
        for source in (self.prices, self.sprices):
            if symbol in source and source[symbol] not in (None, ''):
                price = float(source[symbol])
                if self._is_suspicious_option_price(symbol, price):
                    print(f"suspicious cached option price ignored for {symbol}: {price}")
                    continue
                return price

        depth_price = self._get_fresh_depth_market_price(
            symbol,
            exchange,
            token,
        )
        if depth_price is not None:
            return depth_price

        quote_price = self._get_direct_quote_price(symbol, exchange, token)
        if quote_price is not None:
            return quote_price

        df = self.dataframes.get(symbol)
        if (
            isinstance(df, pd.DataFrame)
            and not df.empty
            and 'close' in df.columns
            and not self._extract_option_underlying(symbol)
        ):
            return float(df['close'].iloc[-1])

        raise KeyError(f"{symbol} price unavailable")

    def _wait_for_market_price(
        self,
        symbol,
        exchange=None,
        token=None,
        timeout_seconds=None,
        poll_interval=0.25,
    ):
        if timeout_seconds is None:
            try:
                timeout_seconds = float(
                    os.getenv("SSLAGO_OPTION_QUOTE_WAIT_SECONDS", "5")
                )
            except (TypeError, ValueError):
                timeout_seconds = 5
        timeout_seconds = max(0, float(timeout_seconds))
        deadline = time.monotonic() + timeout_seconds
        last_error = None

        while True:
            try:
                return self._get_market_price(symbol, exchange, token)
            except KeyError as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                raise last_error or KeyError(f"{symbol} price unavailable")
            time.sleep(min(float(poll_interval), max(0, deadline - time.monotonic())))

    def _get_underlying_price(self, symbol, fallback=0):
        token_map = {
            'NIFTY': ('NSE', '26000'),
            'BANKNIFTY': ('NSE', '26009'),
            'FINNIFTY': ('NSE', '26037'),
            'MIDCPNIFTY': ('NSE', '26074'),
        }
        try:
            exchange, token = token_map.get(symbol, (None, None))
            return self._get_market_price(symbol, exchange, token)
        except Exception as e:
            warning_key = ('underlying', symbol)
            now_monotonic = time.monotonic()
            last_warning = self._price_unavailable_log_times.get(warning_key, 0)
            if now_monotonic - last_warning >= 30:
                print(f"underlying quote fallback failed for {symbol}: {e}")
                self._price_unavailable_log_times[warning_key] = now_monotonic
            return float(fallback or 0)

    def _make_instrument(self, exchange, token, symbol, name, lot_size):
        return Instrument(
            exchange=exchange,
            token=int(token),
            symbol=symbol,
            name=name,
            expiry='',
            lot_size=int(lot_size)
        )
    
    def atmfinder(self,k_values, a):
        # Find the nearest value in the list
        nearest_value = min(k_values, key=lambda x: abs(x - a))

        # Find the index of the nearest value
        nearest_index = k_values.index(nearest_value)

        # Calculate the average distance between the current and next element
        if nearest_index < len(k_values) - 1:
            next_value = k_values[nearest_index + 1]
            avg_distance = (next_value - nearest_value) / 2
        else:
            avg_distance = None  # No next element, cannot calculate average distance


        next_value = k_values[nearest_index + 1]

        return nearest_value, abs(next_value-nearest_value)
    def MainFutureSelect(self, Symbol,duration):
        # self._add_log(self.prices)
        # if Symbol=='BANKNIFTY':

        CurrentPrice = self._get_market_price(Symbol)
        indexltp = float(CurrentPrice)
        #print(Symbol)
        #print(duration)
        Symbol=Symbol.replace('-EQ','')
        if Symbol in list(self.Nfo['Symbol']):
            k = self.Nfo[self.Nfo['Symbol'] == Symbol]
        elif Symbol in list(self.Bfo['Symbol']):
            k = self.Bfo[self.Bfo['Symbol'] == Symbol]
        elif Symbol in list(self.Mcx['Symbol']):
            k = self.Mcx[self.Mcx['Symbol'] == Symbol]
        elif Symbol in list(self.Cds['Symbol']):
            k = self.Cds[self.Cds['Symbol'] == Symbol]
        
        #self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[2]['FToken']

        k=k[(k['Symbol'] == Symbol) & (k['Expiry_'].dt.date >= datetime.date.today()) & (k['OptionType'] == 'XX')]#.iloc[2]['FToken']
        k=k.sort_values(by='Expiry_')

        k['Expiry_'] = k['Expiry_'].dt.date
        print(k)
        if 'Next Month' in duration:
            if len(k) < 2:
                print(f"Next month future unavailable for {Symbol}")
                return None, None, None, None
            k=k.iloc[1]
            self.add_symbol_to_websocket( k['TradingSymbol'])
            return k['TradingSymbol'], k['LotSize'],k['Expiry_'],int(k['Token'])
        elif 'Current Month' in duration or 'Current Week' in duration or 'Next Week' in duration:
            if k.empty:
                print(f"Current future unavailable for {Symbol}")
                return None, None, None, None
            k=k.iloc[0]
            print(k)
            self.add_symbol_to_websocket( k['TradingSymbol'])
            return k['TradingSymbol'], k['LotSize'],k['Expiry_'],k['Token']

        print(f"Unsupported future duration {duration} for {Symbol}; using current future")
        if k.empty:
            return None, None, None, None
        k = k.iloc[0]
        self.add_symbol_to_websocket(k['TradingSymbol'])
        return k['TradingSymbol'], k['LotSize'], k['Expiry_'], int(k['Token'])



    def MainEquityOptionSelect(self, Symbol, ordertype, strike, duration):
        try:
            CurrentPrice = self._get_market_price(Symbol)
            indexltp = float(CurrentPrice)

            Symbol = Symbol.replace('-EQ', '')
            k = None  # Initialize k to None
            #print(Symbol)
            if Symbol in list(self.Nfo['Symbol']):
                k = self.Nfo[self.Nfo['Symbol'] == Symbol]
            elif Symbol in list(self.Bfo['Symbol']):
                k = self.Bfo[self.Bfo['Symbol'] == Symbol]
            elif Symbol in list(self.Mcx['Symbol']):
                k = self.Mcx[self.Mcx['Symbol'] == Symbol]
            elif Symbol in list(self.Cds['Symbol']):
                k = self.Cds[self.Cds['Symbol'] == Symbol]
            #print(k)
            if k is not None:  # Check if k is assigned a value
                k['Expiry_'] = k['Expiry_'].dt.date
                stre = list(k['Expiry_'].unique())
                stre.sort(reverse=False)
                currentweek, nextweek, currentmonth, nextmonth = self.get_week_and_month_dates(datetime.date.today(), stre)
                exx = None
                if 'Current Week' in duration:
                    exx = currentweek
                elif 'Next Week' in duration:
                    exx = nextweek
                elif 'Current Month' in duration:
                    exx = currentmonth
                elif 'Next Month' in duration:
                    exx = nextmonth
                else:
                    exx = currentweek

                gt = list(k['StrikePrice'].unique())
                gt.sort(reverse=False)
                k = k[k['Expiry_'] == exx]

                atmstrike, distance = self.atmfinder(gt, indexltp)
                index_of_find = gt.index(atmstrike)
                value_two_ahead = gt[index_of_find - (strike)]

                batmstrike = abs(gt[index_of_find - (strike)])
                bk = k[k['StrikePrice'] == batmstrike]
                satmstrike = abs(gt[index_of_find + (strike)])
                sk = k[k['StrikePrice'] == satmstrike]

                if ordertype == 'CE':
                    k = bk[bk['OptionType'] == 'CE']
                    self.add_symbol_to_websocket(k.iloc[-1]['TradingSymbol'])
                    return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'], k.iloc[-1]['Expiry_'], int(k.iloc[-1]['Token'])
                else:
                    k = sk[sk['OptionType'] == 'PE']
                    self.add_symbol_to_websocket(k.iloc[-1]['TradingSymbol'])
                    return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'], k.iloc[-1]['Expiry_'], int(k.iloc[-1]['Token'])
            else:
                print("Symbol not found in any list.")
                return None, None, None, None  # Return default values or handle the case appropriately
        except Exception as e:
            print(f"Error in MainEquityOptionSelect: {e}")

    def MainOptionSelect(self, Symbol, ordertype, strike,duration):
        # self._add_log(self.prices)
        # if Symbol=='BANKNIFTY':

        CurrentPrice = self._get_market_price(Symbol)
        indexltp = float(CurrentPrice)
        #print(Symbol)
        print(duration)
        if Symbol in list(self.Nfo['Symbol']):
            k = self.Nfo[self.Nfo['Symbol'] == Symbol]
        elif Symbol in list(self.Bfo['Symbol']):
            k = self.Bfo[self.Bfo['Symbol'] == Symbol]
        elif Symbol in list(self.Mcx['Symbol']):
            k = self.Mcx[self.Mcx['Symbol'] == Symbol]
        elif Symbol in list(self.Cds['Symbol']):
            k = self.Cds[self.Cds['Symbol'] == Symbol]
        
        k['Expiry_'] = k['Expiry_'].dt.date
        stre = list(k['Expiry_'].unique())
        stre.sort(reverse=False)
        currentweek, nextweek, currentmonth, nextmonth = self.get_week_and_month_dates(datetime.date.today(), stre)
        exx=None
        if 'Current Week' in duration:
            exx=currentweek
        elif 'Next Week' in duration:
            exx=nextweek
        elif 'Current Month' in duration:
            exx=currentmonth
        elif 'Next Month' in duration:
            exx=nextmonth
        else:
            exx=currentweek
        #print(exx)
        gt=list(k['StrikePrice'].unique())
        gt.sort(reverse=False)
        k=k[k['Expiry_']==exx]
        atmstrike,distance=self.atmfinder(gt, indexltp)
        #print(atmstrike)
        #print(distance)

        batmstrike = abs(atmstrike-(strike*distance))
        bk = k[k['StrikePrice'] == batmstrike]
        satmstrike = abs(atmstrike+(strike*distance))
        sk = k[k['StrikePrice'] == satmstrike]

        if ordertype == 'CE':
            k = bk[bk['OptionType'] == 'CE']
            self.add_symbol_to_websocket( k.iloc[-1]['TradingSymbol'])
            return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'],k.iloc[-1]['Expiry_'],int(k.iloc[-1]['Token'])
        else:
            k = sk[sk['OptionType'] == 'PE']
            self.add_symbol_to_websocket( k.iloc[-1]['TradingSymbol'])
            return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'],k.iloc[-1]['Expiry_'],int(k.iloc[-1]['Token'])

    def MainEquitySelect(self, Symbol):
        if Symbol in list(self.Nse['Symbol']):
            k = self.Nse[self.Nse['Symbol'] == Symbol]
            k = k[k['Instrument'] == 'EQ']

            if not k.empty:
                self.add_symbol_to_websocket(k.iloc[-1]['TradingSymbol'])
                return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'], int(k.iloc[-1]['Token'])
            else:
                # Handle the case when the DataFrame is empty
                print(f"No data found for symbol: {Symbol}")
                return None, None, None
        else:
            # Handle the case when the symbol is not found in the DataFrame
            print(f"Symbol not found: {Symbol}")
            return None, None, None
    def RowMainEquitySelect(self, Symbol):
        if Symbol in list(self.Nse['Symbol']):
            k = self.Nse[self.Nse['Symbol'] == Symbol]
            #k = k[k['Instrument'] == 'EQ']

            if not k.empty:
                self.add_symbol_to_websocket(k.iloc[-1]['TradingSymbol'])
                self.nsestocksunfil.append(k.iloc[-1])
                return k.iloc[-1]  #k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'], int(k.iloc[-1]['Token'])
            else:
                # Handle the case when the DataFrame is empty
                print(f"No data found for symbol: {Symbol}")
                return None#, None, None
        else:
            # Handle the case when the symbol is not found in the DataFrame
            print(f"Symbol not found: {Symbol}")
            return None#, None, None
    def contracts(self):
        check = True  # running_status()
        import requests
        import zipfile
        import os
        import pandas as pd

        if check:
            root = 'https://api.shoonya.com/'
            masters = ['NSE_symbols.txt.zip','BSE_symbols.txt.zip', 'NFO_symbols.txt.zip','BFO_symbols.txt.zip',
                       'CDS_symbols.txt.zip', 'MCX_symbols.txt.zip']

            for zip_file in masters:
                target_file = zip_file.removesuffix('.zip')
                if os.path.exists(target_file) and not _env_bool(
                    "SSLAGO_REFRESH_CONTRACT_MASTERS",
                    False,
                ):
                    trading_event(
                        "contract_master_result",
                        force=True,
                        file=target_file,
                        source="existing",
                    )
                    continue
                print(f'downloading {zip_file}')
                url = root + zip_file
                r = requests.get(url, allow_redirects=True, timeout=30)
                r.raise_for_status()
                with open(zip_file, 'wb') as handle:
                    handle.write(r.content)

                try:
                    with zipfile.ZipFile(zip_file) as z:
                        z.extractall()
                        print("Extracted: ", zip_file)
                    trading_event(
                        "contract_master_result",
                        force=True,
                        file=target_file,
                        source="download",
                    )
                finally:
                    if os.path.exists(zip_file):
                        os.remove(zip_file)

        Nse = pd.read_csv('NSE_symbols.txt')
        Nse = Nse.iloc[:, :-1]
        Bse = pd.read_csv('BSE_symbols.txt')
        Bse = Bse.iloc[:, :-1]
        Bfo = pd.read_csv('BFO_symbols.txt')
        Bfo = Bfo.iloc[:, :-1]
        Nfo = pd.read_csv('NFO_symbols.txt')
        Nfo = Nfo.iloc[:, :-1]
        Mcx = pd.read_csv('MCX_symbols.txt')
        Mcx = Mcx.iloc[:, :-1]
        Cds = pd.read_csv('CDS_symbols.txt')
        Cds = Cds.iloc[:, :-1]
        n = Nfo
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = lists[i][0]+' '+lists[i][3]+' ' + \
                lists[i][5].replace('-', '')+' ' + \
                str(lists[i][8])+' '+lists[i][7]
            lists[i].append(hold)
        Nfo = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'Symbol', 'TradingSymbol', 'Expiry',
                                           'Instrument', 'OptionType', 'StrikePrice', 'TickSize', 'Ticker'])
        #Nfo = Nfo[(Nfo['Symbol'] == 'BANKNIFTY') | (Nfo['Symbol'] == 'NIFTY') | (Nfo['Symbol'] == 'FINNIFTY') | (Nfo['Symbol'] == 'MIDCPNIFTY')]
        Nfo['FToken'] = Nfo['Exchange']+'|'+Nfo['Token'].apply(str)
        Nfo['SToken']=Nfo['Token'].apply(str)+'_NFO'
        Nfo['UToken']='NSE_FO|'+Nfo['Token'].apply(str)
        

        n = Bfo
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = lists[i][0]+' '+lists[i][3]+' ' + \
                lists[i][5].replace('-', '')+' ' + \
                str(lists[i][8])+' '+lists[i][7]
            lists[i].append(hold)
        Bfo = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'Symbol', 'TradingSymbol', 'Expiry',
                                           'Instrument', 'OptionType', 'StrikePrice', 'TickSize', 'Ticker'])
        #Bfo = Bfo[(Bfo['Symbol'] == 'SENSEX') | (Bfo['Symbol'] == 'NIFTY') | (Bfo['Symbol'] == 'FINNIFTY')]
        Bfo['FToken'] = Bfo['Exchange']+'|'+Bfo['Token'].apply(str)
        Bfo['SToken']=Bfo['Token'].apply(str)+'_BFO'
        Bfo['UToken']='BSE_FO|'+Bfo['Token'].apply(str)
        Bfo['Symbol'] = Bfo['Symbol'].replace({
            'BSXFUT': 'SENSEX',
            'BSXOPT': 'SENSEX'
        })
        

        n = Nse
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = str(lists[i][0])+' '+str(lists[i][3])+' '+str(lists[i][5])
            lists[i].append(hold)
        Nse = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'Symbol', 'TradingSymbol', 'Instrument',
                                           'TickSize', 'Ticker'])
        Nse['FToken'] = Nse['Exchange']+'|'+Nse['Token'].apply(str)
        Nse['SToken']=Nse['Token'].apply(str)+'_NSE'
        Nse['UToken'] = 'NSE_EQ'+'|'+Nse['Token'].apply(str)

        n = Bse
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = str(lists[i][0])+' '+str(lists[i][3])+' '+str(lists[i][5])
            lists[i].append(hold)
        Bse = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'Symbol', 'TradingSymbol', 'Instrument',
                                           'TickSize', 'Ticker'])
        Bse['FToken'] = Bse['Exchange']+'|'+Bse['Token'].apply(str)
        Bse['SToken']=Bse['Token'].apply(str)+'_BSE'
        Bse['UToken'] = 'BSE_EQ'+'|'+Bse['Token'].apply(str)

        n = Mcx
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = str(lists[i][0])+' '+str(lists[i][4])+' '+str(lists[i]
                                                                 [6].replace('-', ''))+' '+str(lists[i][9])+' '+str(lists[i][8])
            lists[i].append(hold)
        Mcx = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'GNGD', 'Symbol', 'TradingSymbol',
                                           'Expiry', 'Instrument', 'OptionType', 'StrikePrice', 'TickSize', 'Ticker'])
        #Mcx = Mcx[Mcx['Symbol'] == 'CRUDEOIL']
        Mcx['FToken'] = Mcx['Exchange']+'|'+Mcx['Token'].apply(str)
        Mcx['SToken']=Mcx['Token'].apply(str)+'_MFO'
        
        Mcx['UToken'] = 'MCX_FO'+'|'+Mcx['Token'].apply(str)
        
        n = Cds
        lists = n.values.tolist()

        Cds = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'Precision', 'Multiplier', 'Symbol',
                                           'TradingSymbol', 'Expiry', 'Instrument', 'OptionType', 'StrikePrice',
                                           'TickSize'])
        Cds = Cds[Cds['Symbol'] == 'USDINR']
        Cds['FToken'] = Cds['Exchange']+'|'+Cds['Token'].apply(str)
        Cds['SToken']=Cds['Token'].apply(str)+'_CDS'



        Cds['Expiry_'] = pd.to_datetime(Cds['Expiry'])
        Mcx['Expiry_'] = pd.to_datetime(Mcx['Expiry'])
        Nfo['Expiry_'] = pd.to_datetime(Nfo['Expiry'])
        Bfo['Expiry_'] = pd.to_datetime(Bfo['Expiry'])
        Cds = Cds.sort_values(by='Expiry_')
        Mcx = Mcx.sort_values(by='Expiry_')
        Nfo = Nfo.sort_values(by='Expiry_')
        Bfo = Bfo.sort_values(by='Expiry_')
        return Nse, Cds, Mcx, Nfo,Bse,Bfo
    def login(self):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'}
        user_id = os.getenv("SSLAGO_STOCKNOTE_USER_ID", "").strip()
        password = os.getenv("SSLAGO_STOCKNOTE_PASSWORD", "").strip()
        yob = os.getenv("SSLAGO_STOCKNOTE_YOB", "").strip()
        if not user_id or not password or not yob:
            raise RuntimeError("Stocknote login requires SSLAGO_STOCKNOTE_USER_ID, SSLAGO_STOCKNOTE_PASSWORD, and SSLAGO_STOCKNOTE_YOB")
        requestBody = {"userId": user_id, "password": password, "yob": yob}
        try:
            r = requests.post('https://api.stocknote.com/login', data=json.dumps(requestBody), headers=headers)
            r.raise_for_status()
            print(r.json())
            self.session_token = r.json()['sessionToken']
        except requests.exceptions.RequestException as req_err:
            print(f"Error during login request: {req_err}")
    def on_message(self, ws, msg):
        try:
            message = json.loads(msg)
            data = message.get('response', {}).get('data', {})
            symbol_key = data.get('sym')
            ltp = data.get('ltp')
            if not symbol_key or ltp is None or symbol_key not in self.symbols_stok:
                print(f"Ignoring malformed quote message: {msg}")
                return
            symbol = self.symbols_stok[symbol_key]
            price = float(ltp)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"Ignoring invalid quote message: {exc}")
            return

        self.sprices[symbol] = price
        self.prices[symbol] = price
        if self.updatelist:
            df_symbols = pd.DataFrame({"symbol": self.subscribe_slist})
            json_data = df_symbols.to_json(orient="records")
            data = ('{"request":{"streaming_type":"quote", "data":{"symbols":'+ json_data+'},"request_type":"subscribe", "response_format":"json"}}' )
            ws.send(data)
            ws.send("\n")
            ws.send(data)
            ws.send("\n")
            self.updatelist=False

    def on_error(self, ws, error):
        print(error)
        for i in range(0,len(self.subscribe_slist)):
            if str(error) ==self.subscribe_slist[i]:
                del self.subscribe_slist[i]

    def on_close(self, ws):
        print("Connection Closed")

    def on_open(self, ws):
        
        df_symbols = pd.DataFrame({"symbol": self.subscribe_slist})
        # Convert the DataFrame to a JSON string
        json_data = df_symbols.to_json(orient="records")
        # print(json_data)
        print(len(json_data))
        print("Sending json")
        data = (
            '{"request":{"streaming_type":"quote", "data":{"symbols":'
            + json_data
            + '},"request_type":"subscribe", "response_format":"json"}}'
        )
        ws.send(data)
        ws.send("\n")

    def run_websocket(self):
        #global retry_count
        while True:
            #print('kkkkkkkk')
            try:
                import websocket
                self.login()
                headers = {'x-session-token': self.session_token}

                self.ws = websocket.WebSocketApp("wss://stream.stocknote.com", on_open=self.on_open, on_message=self.on_message,
                                                on_error=self.on_error, on_close=self.on_close, header=headers)
                self.ws.run_forever(
                    ping_interval=3,
                    reconnect=5,
                    sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
                )

            except Exception as e:
                print(f"An error occurred in s websocket: {e}")
                time.sleep(5)
                import websocket
                self.login()
                headers = {'x-session-token': self.session_token}

                self.ws = websocket.WebSocketApp("wss://stream.stocknote.com", on_open=self.on_open, on_message=self.on_message,
                                                on_error=self.on_error, on_close=self.on_close, header=headers)
                self.ws.run_forever(
                    ping_interval=3,
                    reconnect=5,
                    sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
                )
                pass

            

    def get_week_and_month_dates(self, currentdate, date_list):
        date_list.sort(reverse=False)
        currentweek = None
        nextweek = None
        currentmonth = None
        nextmonth = None

        # Get dates for the current week
        currentweek = date_list[0]
        nextweek = date_list[1]

        duplicatecurrentmonth = [i for i in date_list if i.month == currentweek.month and i.year == currentweek.year]

        if duplicatecurrentmonth:
            currentmonth = duplicatecurrentmonth[-1]
            next_month_date = currentmonth + relativedelta(months=1)
            next_month_dates = [i for i in date_list if next_month_date.year == i.year and next_month_date.month == i.month]

            if next_month_dates:
                nextmonth = next_month_dates[-1]

        print(currentweek, nextweek, currentmonth, nextmonth)
        return currentweek, nextweek, currentmonth, nextmonth
