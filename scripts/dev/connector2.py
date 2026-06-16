# import MetaTrader5 as mt5
import warnings
import math
import json
import logging
import yaml
import threading
import enum
import pandas as pd
from finta import TA
import pymongo
from oibased import OILevel
from levelbased import HuntLevel
from strategies import TechnicalStrategy, BreakoutStrategy
from models import *
from dateutil.relativedelta import relativedelta
import time
from typing import *
import typing
import requests
import os
try:
    from TradeMaster import TradeHub as Aliceblue
except ImportError:
    from pya3 import Aliceblue
import pyotp
import datetime
import numpy as np
import sqlite3
import concurrent.futures
requests.get('https://github.com', verify=True)

# mt5.initialize()
warnings.filterwarnings('ignore')
'''tf={"1m": mt5.TIMEFRAME_M1, "2m": mt5.TIMEFRAME_M2, "3m": mt5.TIMEFRAME_M3,
                         "4m": mt5.TIMEFRAME_M4, "5m": mt5.TIMEFRAME_M5, "6m": mt5.TIMEFRAME_M6,
                         "10m": mt5.TIMEFRAME_M10, "15m": mt5.TIMEFRAME_M15,"30m": mt5.TIMEFRAME_M30,
                         "1h": mt5.TIMEFRAME_H1,"4h": mt5.TIMEFRAME_H4, "1D": mt5.TIMEFRAME_D1}'''
logger = logging.getLogger()
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
    CoverOrder = 'CO'
    BracketOrder = 'BO'
    Normal = 'NRML'





class Exchange:
    def __init__(self, api,db,cred,reapi):
        self.cred=cred
        self.reapi=reapi
        self.db=db
        self.real=False
        self.testmode=False
        self.tokdf=pd.read_csv('https://developers.stocknote.com/doc/ScripMaster.csv') 
        self.samlist=list(self.tokdf['symbolCode'])
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
        self.topbottomlist=False
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
        self.timestamp=0
        #self.samcolist=pd.read_csv('https://developers.stocknote.com/doc/ScripMaster.csv')
        #print(self.samcolist)
        self.Nse, self.Cds, self.Mcx, self.Nfo ,self.Bse,self.Bfo= self.contracts()
        #self.NfoAB=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/NFO.csv')
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
        self.timeswitch={'1m':'1','2m':'2','3m':'3','5m':'5','10m':'10','15m':'15','30m':'30','1h':'60','2h':'120'}
        self.candleswitch={'1m':500,'2m':500,'3m':500,'5m':1000,'10m':2000,'15m':2000,'30m':5000,'1h':6000,'2h':10000}
        self.controls={'BANKNIFTY':self.admincontrol_collection.find_one({'symbol':"BANKNIFTY"}),
        'NIFTY':self.admincontrol_collection.find_one({'symbol':"NIFTY"}),
        'FINNIFTY':self.admincontrol_collection.find_one({'symbol':"FINNIFTY"}),
        'MIDCPNIFTY':self.admincontrol_collection.find_one({'symbol':"MIDCPNIFTY"}) }
        
        self.strategyinputs={'EMA':self.strategyinput_collection.find_one({'strategy':"EMA"}),
        'SSALGO':self.strategyinput_collection.find_one({'strategy':"SSALGO"}),
        'SSAUTO':self.strategyinput_collection.find_one({'strategy':"SSAUTO"}),
        'PEMA':self.strategyinput_collection.find_one({'strategy':"PEMA"}),
        'SSEQUITYFNO':self.strategyinput_collection.find_one({'strategy':"SSEQUITYFNO"}),
        }
        self.breakoutexit={}
        self.dataframes={
        'BANKNIFTY':[],
        'NIFTY':[],
        'CRUDEOIL':[],
        'FINNIFTY':[],
        'MIDCPNIFTY':[],

        'BANKNIFTY-I':[],
        'NIFTY-I':[],
        'FINNIFTY-I':[],
        'MIDCPNIFTY-I':[]
        
        ,'BANKNIFTY-II':[],
        'NIFTY-II':[],
        'FINNIFTY-II':[],
        'MIDCPNIFTY-II':[]

        ,'BANKNIFTY-III':[],
        'NIFTY-III':[],
        'FINNIFTY-III':[],
        'MIDCPNIFTY-III':[]

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
        #self.dataframes1d=dict()
        #self.dataframes1w=dict()
        self.userloggedin=list()
        self.usernotloggedin=list()
        self.alice=dict()
        self.ordersids=[]
        self.tank = []
        self.prices = dict()
        self.candles1m = dict()
        self.candles15m = dict()
        self.mindata = dict()
        self.loadedwatchsymbols = []
        self.oistrikelvldata = {}
        self.symbols_tok = {'NSE|26037':'FINNIFTY','NSE|26009': 'BANKNIFTY','NSE|26074':'MIDCPNIFTY',
                            'NSE|26000': 'NIFTY', self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & (self.Mcx['Expiry_'].dt.date >= datetime.date.today()) & (self.Mcx['OptionType'] == 'XX')].iloc[0]['FToken']: 'CRUDEOIL'

                            ,self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'NIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'NIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'NIFTY-II'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'BANKNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'BANKNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'BANKNIFTY-II'

                            ,self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'FINNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'FINNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'FINNIFTY-II'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[0]['FToken']:'MIDCPNIFTY-I'
                            ,self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['FToken']:'MIDCPNIFTY-II'
                            }





        self.tok_symbols = {'FINNIFTY':'NSE|26037','BANKNIFTY': 'NSE|26009','MIDCPNIFTY':'NSE|26074', 'NIFTY': 'NSE|26000', "CRUDEOIL": self.Mcx[(self.Mcx['Symbol'] == 'CRUDEOIL') & (
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
                            ,self.Nfo[(self.Nfo['Symbol'] == 'MIDCPNIFTY') & (self.Nfo['Expiry_'].dt.date >= datetime.date.today()) & (self.Nfo['OptionType'] == 'XX')].iloc[1]['SToken']:'MIDCPNIFTY-II'

        }
        self.subscribe_slist = []
        self.subslist=list(self.symbols_stok.keys())
        self.subscribe_slist.extend(self.subslist)

        self.tenstrikes, self.fivestrikes, self.eodstrikes, self.all_oi = {}, {}, {}, {}
        self.Markettime = {
            'NSE': {'start': datetime.time(9, 15, 1, 0), 'end': datetime.time(15, 25, 0, 0)},
            'NFO': {'start': datetime.time(9, 15, 1, 0), 'end': datetime.time(15, 25, 0, 0)},
            'CDS': {'start': datetime.time(9, 0, 1, 0), 'end': datetime.time(16, 55, 0, 0)},
            'MCX': {'start': datetime.time(9, 0, 1, 0), 'end': datetime.time(23, 55, 0, 0)},
        }

        self.websocketretry=0
        self.lastoiupdate = {}
        self.api = api
        self.lastupdate=0
        self.feed_opened = False
        self.api.start_websocket(order_update_callback=self.event_handler_order_update,
                                 subscribe_callback=self.event_handler_feed_update, socket_open_callback=self.open_callback,socket_close_callback=self.close_callback)
        self.api.subscribe(self.subscribe_list)


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
        self.marketdays=5
        
        self.logs = []
        self.reconnect = True

        
        self.hist('BANKNIFTY', tf="1",initial=True)
        self.hist('NIFTY', tf="1",initial=True)
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
        
        t1 = threading.Thread(target=self._loginusers)
        t1.start()
        t2 = threading.Thread(target=self._reloginusers)
        t2.start()
        t2_ = threading.Thread(target=self._dataloader)
        t2_.start()
        t21_ = threading.Thread(target=self._dataequityloader)
        t21_.start()
        websocket_thread = threading.Thread(target=self.run_websocket)
        websocket_thread.daemon = True 
        websocket_thread.start()
        #time.sleep(20)
        # t = threading.Thread(target=self._scriptloop1)
        # t.start()
        # t1 = threading.Thread(target=self._scriptloop1)
        # t1.start()
        

        #t3 = threading.Thread(target=self._scriptorders)
        #t3.start()
        #t2 = threading.Thread(target=self._scripttrades)
        #t2.start()
        t3 = threading.Thread(target=self._datascript)
        t3.start()
        t311 = threading.Thread(target=self._dataequityscript)
        t311.start()
        '''t31 = threading.Thread(target=self._datascript1)
        t31.start()
        t32 = threading.Thread(target=self._datascript2)
        t32.start()
        t33 = threading.Thread(target=self._datascript3)
        t33.start()
        t34 = threading.Thread(target=self._datascript4)
        t34.start()
        t35 = threading.Thread(target=self._datascript5)
        t35.start()'''
        
        #print()
        t4 = threading.Thread(target=self._mainloop)
        t4.start()

        t5=threading.Thread(target=self._stopnotsubusers)
        t5.start()

    def _loginusers(self):
        try:
            import concurrent.futures

            def process_item(item):
                user=item['user']
                user_id = item['apikey']
                api_key = item['apisecret']
                alice_instance = Aliceblue(user_id=user_id, api_key=api_key)
                session_id = alice_instance.get_session_id()
                return user, alice_instance,session_id

            # Assuming list(self.apis_collection.find()) returns a list of items
            items = list(self.apis_collection.find())

            # You can adjust the number of processes as needed
            

            num_threads = 2

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                results = list(executor.map(process_item, items))
            # Assuming self.alice is a dictionary
            #if results:
            for user_id, alice_instance,session_id in results:
                print(session_id)
                if 'sessionID' in list(session_id.keys()):
                    self.alice[user_id] = alice_instance
                    self.userloggedin.append(user_id)
                else:
                    #if user_id not in self.usernotloggedin:
                    self.usernotloggedin.append(user_id)
        except:
            time.sleep(20)
            pass
    def _reloginusers(self):
        while True:
            try:

                #print(self.usernotloggedin)
                #if self.usernotloggedin:
                import concurrent.futures

                def process_item(item):
                    user=item['user']
                    user_id = item['apikey']
                    api_key = item['apisecret']
                    alice_instance = Aliceblue(user_id=user_id, api_key=api_key)
                    session_id = alice_instance.get_session_id()
                    return user, alice_instance,session_id

                # Assuming list(self.apis_collection.find()) returns a list of items
                items = list(self.apis_collection.find({'user': {'$nin': self.userloggedin}}))
                #print('items')
                self.userloggedin=list(set(self.userloggedin))
                #print(self.userloggedin)
                #print(items)
                

                # You can adjust the number of processes as needed
                

                num_threads = 2

                with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                    results = list(executor.map(process_item, items))
                # Assuming self.alice is a dictionary
                
                for user_id, alice_instance,session_id in results:
                    #print(session_id)
                    if 'sessionID' in list(session_id.keys()):
                        self.alice[user_id] = alice_instance
                        self.userloggedin.append(user_id)
                        
                    else:
                        if user_id not in self.usernotloggedin:
                            self.usernotloggedin.append(user_id)
                time.sleep(60)
            except:
                time.sleep(20)
                pass
    def _stopnotsubusers(self):
        while True:
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
    def _dataloader(self):
        #tt=self.hist('BANKNIFTY')
        #self.history_collection = self.db["historical"]
        self.hist('BANKNIFTY', tf="1",initial=True)
        self.hist('NIFTY', tf="1",initial=True)
        self.hist('FINNIFTY', tf="1",initial=True)
        self.hist('MIDCPNIFTY', tf="1",initial=True)
        self.hist('BANKNIFTY-I', tf="1",initial=True)
        self.hist('NIFTY-I', tf="1",initial=True)
        self.hist('FINNIFTY-I', tf="1",initial=True)
        self.hist('MIDCPNIFTY-I', tf="1",initial=True)
        self.hist('BANKNIFTY-II', tf="1",initial=True)
        self.hist('NIFTY-II', tf="1",initial=True)
        self.hist('FINNIFTY-II', tf="1",initial=True)
        self.hist('MIDCPNIFTY-II', tf="1",initial=True)
        #self.hist('CRUDEOIL', tf="1",initial=True)
        #t=pd.to_timedelta(1, unit='minute')
        symbols = ['BANKNIFTY', 'NIFTY', 'FINNIFTY','MIDCPNIFTY','BANKNIFTY-I', 'NIFTY-I', 'FINNIFTY-I','MIDCPNIFTY-I','BANKNIFTY-II', 'NIFTY-II', 'FINNIFTY-II','MIDCPNIFTY-II']
        #datadf=list(self.history_collection.find({'symbol':'BANKNIFTY'}).sort('_id', pymongo.DESCENDING).limit(5000))
        #print(datadf)
        #self.dataframes['BANKNIFTY']=[]
        
        
        while True:
         
            try:
                if ((datetime.datetime.today().weekday() < self.marketdays) and datetime.time(8,59) < datetime.datetime.now().time() and datetime.time(15,28) > datetime.datetime.now().time()) or self.testmode:
                    # Check if it's been more than 2 minutes since the last update
                    if pd.to_datetime(datetime.datetime.now()) > (pd.to_datetime(self.lastupdate,format='%d-%m-%Y %H:%M:%S') + pd.to_timedelta(2, unit='minute')):
                        
                        # List of symbols to fetch data for
                        
                        # Use ThreadPoolExecutor for concurrent data fetching
                        #with concurrent.futures.ThreadPoolExecutor() as executor:
                            # Fetch historical data for each symbol concurrently
                            #results = list(executor.map(self.hist, [symbols, '1', False]))
                        #    results = list(executor.map(lambda symbol: self.hist(symbol, '1', False), symbols))
                        #with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                        #    results = list(executor.map(lambda symbol: self.hist(symbol, '1', True), symbols))

                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            futures = [executor.submit(lambda symbol: self.hist(symbol, '1', False), symbol) for symbol in symbols]

                            for future in concurrent.futures.as_completed(futures):
                                result = future.result()


                        print('I am done with that')
                        #print(self.prices)
                        print(self.lastupdate)

                        #self.dataframes['BANKNIFTY']=list(self.history_collection.find({'symbol':'BANKNIFTY'}).sort('_id', pymongo.DESCENDING).limit(5000))[::-1]
                        
                        # Pause for 1 second before the next iteration
                #self.api.subscribe(self.subscribe_list)
                time.sleep(1)
            except Exception as e:
                # Print any exception that occurs during data loading
                print(f'Data Loader Error: {e}')
                time.sleep(30)
                pass
    def _dataequityloader(self):
        #tt=self.hist('BANKNIFTY')
        #self.history_collection = self.db["historical"]
        #self.hist('BANKNIFTY', tf="1",initial=True)
        #self.hist('CRUDEOIL', tf="1",initial=True)
        #t=pd.to_timedelta(1, unit='minute')
        #symbols = ['BANKNIFTY', 'NIFTY', 'FINNIFTY','MIDCPNIFTY','BANKNIFTY-I', 'NIFTY-I', 'FINNIFTY-I','MIDCPNIFTY-I','BANKNIFTY-II', 'NIFTY-II', 'FINNIFTY-II','MIDCPNIFTY-II']
        #datadf=list(self.history_collection.find({'symbol':'BANKNIFTY'}).sort('_id', pymongo.DESCENDING).limit(5000))
        #print(datadf)
        #self.dataframes['BANKNIFTY']=[]
        
        
        while True:
         
            try:
                if self.topbottomlist:
                    if (self.testmode) or ((datetime.datetime.today().weekday() < self.marketdays) and datetime.time(8,59) < datetime.datetime.now().time() and datetime.time(15,28) > datetime.datetime.now().time()):
                        # Check if it's been more than 2 minutes since the last update
                        if pd.to_datetime(datetime.datetime.now()) > (pd.to_datetime(self.lastupdate,format='%d-%m-%Y %H:%M:%S') + pd.to_timedelta(2, unit='minute')):
                            
                            # List of symbols to fetch data for
                            
                            # Use ThreadPoolExecutor for concurrent data fetching
                            #with concurrent.futures.ThreadPoolExecutor() as executor:
                                # Fetch historical data for each symbol concurrently
                                #results = list(executor.map(self.hist, [symbols, '1', False]))
                            #    results = list(executor.map(lambda symbol: self.hist(symbol, '1', False), symbols))
                            #with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                            #    results = list(executor.map(lambda symbol: self.hist(symbol, '1', True), symbols))

                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                futures = [executor.submit(lambda symbol: self.hist(symbol, '1', True), symbol) for symbol in self.topbottomsymbol]

                                for future in concurrent.futures.as_completed(futures):
                                    result = future.result()


                            print('I am done with that')
                            #print(self.prices)
                            print(self.lastupdate)

                            #self.dataframes['BANKNIFTY']=list(self.history_collection.find({'symbol':'BANKNIFTY'}).sort('_id', pymongo.DESCENDING).limit(5000))[::-1]
                            
                            # Pause for 1 second before the next iteration
                    #self.api.subscribe(self.subscribe_list)
                    time.sleep(1)
            except Exception as e:
                # Print any exception that occurs during data loading
                print(f'Data Loader Error: {e}')
                time.sleep(30)
                pass

    def buy(self,trade):
        if trade['entry_id'] not in self.positionsids:
            
            if trade['trigger_type']=='On Spot' and trade['status']=='opened':
            
                if trade['comparator_type']=='>=':
            
                    if self.prices[trade['symbol']]>=trade['trigger_price']:
                        self.api.subscribe(self.subscribe_list)
                        if trade['option_type']=='CE':
            
                            option,optionlot=self.OptionSelect( trade['symbol'], trade['option_type'], trade['strike'])
                            print(option)
                            if option not in list(self.prices.keys()):
                                


                                self.add_symbol_to_websocket(option)
                            self.add_symbol_to_websocket(option)
                            trade['option']=option
                            pos={'user':str(trade['user']),'time':int(datetime.datetime.now().timestamp()),'entry_id':int(trade['entry_id']),'symbol':trade['symbol'],'entry_price':float(self.prices[trade['symbol']]),'side':trade['option_type'],'tp_1':float(self.prices[trade['symbol']]+trade['tp_1']),
                            'tp_2':float(self.prices[trade['symbol']]+trade['tp_2']),'trail':0,'comparator_type':trade['comparator_type'],'track':trade['trigger_type'], 'tsl':float(self.prices[trade['symbol']]-trade['trail_stoploss']),'sl':float(self.prices[trade['symbol']]-trade['sl']),'status':"open",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
                            'optionentry':float(self.prices[trade['option']]),'optionexit':float(self.prices[trade['option']]),'optionlot':int(optionlot),
                            'optionname':str(option), 'pnlhalf':0,"decision":"intrade"}
                            if self.real:
                                ret = self.api.place_order(buy_or_sell='B', product_type='M',
                                    exchange='NFO', tradingsymbol=trade['option'], 
                                    quantity=pos['optionlot']*pos['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                    retention='DAY', remarks='my_order_001')
                            self.positions[int(trade['entry_id'])]=pos
                            self.positionsids.append(trade['entry_id'])
                            self.fakeorders[trade['entry_id']]['status']='closed'
                            trade['status']='closed'
                            trade['exittime']=pos['time']
                            j = WebOrder(trade)
                            self.positions_collection.insert_one(pos)
                            self.orders_collection.update_one({'entry_id': trade['entry_id']}, {'$set': j.__dict__})
            
                        elif trade['option_type']=='PE':
            
                            option,optionlot=self.OptionSelect( trade['symbol'], trade['option_type'], trade['strike'])
                            self.add_symbol_to_websocket(option)
                            print(option)
                            if option not in list(self.prices.keys()):
                                

                                self.add_symbol_to_websocket(option)
                            trade['option']=option
                            pos={'user':str(trade['user']),'time':int(datetime.datetime.now().timestamp()),'entry_id':int(trade['entry_id']),'symbol':trade['symbol'],'entry_price':float(self.prices[trade['symbol']])
                            ,'side':trade['option_type'],'tp_1':float(self.prices[trade['symbol']]-trade['tp_1']),
                            'tp_2':float(self.prices[trade['symbol']]-trade['tp_2']),'trail':int(0),'comparator_type':trade['comparator_type'],'track':trade['trigger_type'], 'tsl':float(self.prices[trade['symbol']]+trade['trail_stoploss']),'sl':float(self.prices[trade['symbol']]+trade['sl']),'status':"open",'pnl':0,'lot':int(trade['lot']),'initial_lot':int(trade['lot']),
                            'optionentry':float(self.prices[trade['option']]),'optionexit':float(self.prices[trade['option']]),'optionlot':int(optionlot),
                            'optionname':str(option), 'pnlhalf':0,"decision":"intrade"}
                            if self.real:
                                ret = self.api.place_order(buy_or_sell='B', product_type='M',
                                    exchange='NFO', tradingsymbol=trade['option'], 
                                    quantity=pos['optionlot']*pos['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                    retention='DAY', remarks='my_order_001')
                            self.positions[int(trade['entry_id'])]=pos
                            self.positionsids.append(trade['entry_id'])
                            self.fakeorders[trade['entry_id']]['status']='closed'
                            trade['status']='closed'
                            trade['exittime']=pos['time']
                            j = WebOrder(trade)
                            self.positions_collection.insert_one(pos)
                            self.orders_collection.update_one({'entry_id': trade['entry_id']}, {'$set': j.__dict__})
            
                elif trade['comparator_type']=='<=':
                    self.api.subscribe(self.subscribe_list)
                    if self.prices[trade['symbol']]<=trade['trigger_price']:
                        if trade['option_type']=='CE':
            
                            option,optionlot=self.OptionSelect( trade['symbol'], trade['option_type'], trade['strike'])
                            trade['option']=option
                            self.add_symbol_to_websocket(option)
                            print(option)
                            if option not in list(self.prices.keys()):
                                

                                self.add_symbol_to_websocket(option)
                            pos={'user':str(trade['user']),'time':int(datetime.datetime.now().timestamp()),'entry_id':int(trade['entry_id']),'symbol':trade['symbol'],'entry_price':float(self.prices[trade['symbol']]),'side':trade['option_type'],'tp_1':float(self.prices[trade['symbol']]+trade['tp_1']),
                            'tp_2':float(self.prices[trade['symbol']]+trade['tp_2']),'trail':0,'comparator_type':trade['comparator_type'],'track':trade['trigger_type'], 'tsl':float(self.prices[trade['symbol']]-trade['trail_stoploss']),'sl':float(self.prices[trade['symbol']]-trade['sl']),'status':"open",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
                            'optionentry':float(self.prices[trade['option']]),'optionexit':float(self.prices[trade['option']]),'optionlot':int(optionlot),
                            'optionname':str(option), 'pnlhalf':0,"decision":"intrade"}
                            if self.real:
                                ret = self.api.place_order(buy_or_sell='B', product_type='M',
                                    exchange='NFO', tradingsymbol=trade['option'], 
                                    quantity=pos['optionlot']*pos['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                    retention='DAY', remarks='my_order_001')
                            self.positions[int(trade['entry_id'])]=pos
                            self.positionsids.append(trade['entry_id'])
                            self.fakeorders[trade['entry_id']]['status']='closed'
                            trade['status']='closed'
                            trade['exittime']=pos['time']
                            j = WebOrder(trade)
                            self.positions_collection.insert_one(pos)
                            self.orders_collection.update_one({'entry_id': trade['entry_id']}, {'$set': j.__dict__})
                        elif trade['option_type']=='PE':
            
                            option,optionlot=self.OptionSelect( trade['symbol'], trade['option_type'], trade['strike'])
                            trade['option']=option
                            self.add_symbol_to_websocket(option)
                            print(option)
                            if option not in list(self.prices.keys()):
                                

                                self.add_symbol_to_websocket(option)
                            pos={'user':str(trade['user']),'time':int(datetime.datetime.now().timestamp()),'entry_id':int(trade['entry_id']),'symbol':trade['symbol'],'entry_price':float(self.prices[trade['symbol']])
                            ,'side':trade['option_type'],'tp_1':float(self.prices[trade['symbol']]-trade['tp_1']),
                            'tp_2':float(self.prices[trade['symbol']]-trade['tp_2']),'trail':int(0),'comparator_type':trade['comparator_type'],'track':trade['trigger_type'], 'tsl':float(self.prices[trade['symbol']]+trade['trail_stoploss']),'sl':float(self.prices[trade['symbol']]+trade['sl']),'status':"open",'pnl':0,'lot':int(trade['lot']),'initial_lot':int(trade['lot']),
                            'optionentry':float(self.prices[trade['option']]),'optionexit':float(self.prices[trade['option']]),'optionlot':int(optionlot),
                            'optionname':str(option), 'pnlhalf':0,"decision":"intrade"}
                            if self.real:
                                ret = self.api.place_order(buy_or_sell='B', product_type='M',
                                    exchange='NFO', tradingsymbol=trade['option'], 
                                    quantity=pos['optionlot']*pos['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                                    retention='DAY', remarks='my_order_001')
                            self.positions[int(trade['entry_id'])]=pos
                            self.positionsids.append(trade['entry_id'])
                            self.fakeorders[trade['entry_id']]['status']='closed'
                            trade['status']='closed'
                            trade['exittime']=pos['time']
                            j = WebOrder(trade)
                            self.positions_collection.insert_one(pos)
                            self.orders_collection.update_one({'entry_id': trade['entry_id']}, {'$set': j.__dict__})


    def sell(self,trade):
        #print('i ak positions')
        if trade['track']=='On Spot':
            if trade['side']=='CE':
                trade['optionexit']=self.prices[trade['optionname']]
                trade['pnl']=int((self.prices[trade['optionname']]-trade['optionentry'])*trade['lot']*trade['optionlot'])
                if trade['initial_lot']!=trade['lot']:
                    trade['pnl']=int(trade['pnlhalf']+trade['pnl'])
                
                if self.prices[trade['symbol']]>=trade['tp_1'] and trade['initial_lot']==trade['lot']:
                    trade['lot']=trade['lot']/2
                    trade['pnlhalf']=trade['pnl']/2
                    if self.real:

                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### TP 1 HIT ### ')
                elif self.prices[trade['symbol']]>=trade['tp_2']:
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    #trade['lot']-1
                    print(f'{str(datetime.datetime.now())} :: ### TP 2 HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                    
                elif self.prices[trade['symbol']]<=trade['sl']:
                    print(f'{str(datetime.datetime.now())} :: ### SL HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                elif self.prices[trade['symbol']]<=trade['tsl']:
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### TSL HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                    
                elif trade['decision']=='exitit':
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### USER EXIT HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                    
                #print(trade)
            elif trade['side']=='PE':
                trade['optionexit']=self.prices[trade['optionname']]
                trade['pnl']=int((self.prices[trade['optionname']]-trade['optionentry'])*trade['lot']*trade['optionlot'])

                if trade['initial_lot']!=trade['lot']:
                    trade['pnl']=int(trade['pnlhalf']+trade['pnl'])

                if self.prices[trade['symbol']]<=trade['tp_1'] and trade['initial_lot']==trade['lot']:
                    trade['lot']=trade['lot']/2
                    trade['pnlhalf']=trade['pnl']/2
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### TP 1 HIT ### ')
                elif self.prices[trade['symbol']]<=trade['tp_2']:
                    #trade['lot']-1
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### TP 2 HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                elif self.prices[trade['symbol']]>=trade['sl']:
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### SL HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                    
                elif self.prices[trade['symbol']]>=trade['tsl']:
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### TSL HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                    
                elif trade['decision']=='exitit':
                    if self.real:
                        
                        ret = self.api.place_order(buy_or_sell='S', product_type='M',
                            exchange='NFO', tradingsymbol=trade['optionname'], 
                            quantity=trade['optionlot']*trade['lot'], discloseqty=0,price_type='MKT', price=0, trigger_price=0,
                            retention='DAY', remarks='my_order_001')
                    print(f'{str(datetime.datetime.now())} :: ### USER EXIT HIT ### ')
                    trade['lot']=0
                    trade['status']='close'
                    
            trade['exittime']=int(datetime.datetime.now().timestamp())
            self.positions[trade['entry_id']]=trade
            self.positions_collection.update_one({'entry_id': trade['entry_id']}, {'$set': trade })
            

    def _mainloop(self):
        self.api.subscribe(self.subscribe_list)
        while True:
            #try:
            for key, trade in self.fakeorders.items():
                #print()
                #print(trade)
                if trade['status']=='opened':
                    #print(trade)
                    self.buy(trade)
                    #print('all positions')
                    #print(self.positions)
            for key, trade in self.positions.items():
                                
                if trade['status']=='open':
                    #print(self.prices)
                    if trade['optionname'] not in list(self.prices.keys()):
                        self.add_symbol_to_websocket(trade['optionname'])
                    self.sell(trade)
            #print(self.positions)


            time.sleep(1)
            #except:
            #    time.sleep(3)
            #    pass
    def _symboltransformmonthfut(self,date,symbol):
        if 'Current Month' in date:
            return symbol.upper()+'-I'
        elif 'Next Month' in date:
            return symbol.upper()+'-II'
        elif 'Third Month' in date:
            return symbol.upper()+'-III'

    def process_equity_strategy(self,trade):
        #if trade['user'] in list(self.alice.keys()):
        #if (trade['status'] != 'closed' ):
            #print('hello')
        if trade['strategy'] == 'SSEQUITY':
            self.CHARTINK(trade)
        elif trade['strategy'] == 'SSEQUITYFNO':
            self.TOPBOTTOM(trade)
    def process_strategy(self,trade):
        #if trade['user'] in list(self.alice.keys()):
        #if (trade['status'] != 'closed' ):
            #print('hello')
        if trade['strategy'] == 'SSALGO':
            self.SSALGO(trade)
        elif trade['strategy'] == 'EMA':
            self.EMA(trade)
        elif trade['strategy'] == 'PEMA':
            self.PEMA(trade)
        elif trade['strategy'] == 'SSAUTO':
            self.UTBOT(trade)
    def _dataequityscript(self):
        now=datetime.datetime.now()
        midnight = now.replace(hour=0, minute=1, second=0, microsecond=0)
        self.timestamp = int(midnight.timestamp())
        self.api.subscribe(self.subscribe_list)
        while True:
            try:
                #print('god')
                mains = list(self.strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}, {'position': 'in'},{'strategy': {'$in': ['SSEQUITY', 'SSEQUITYFNO']}}]}))

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    executor.map(self.process_equity_strategy, mains)
                #time.sleep(1)
                if self.testmode:
                    #print(self.prices)
                    time.sleep(1)
                #print(f'data : {str(datetime.datetime.now())}')
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _dataequityscript: {e}")
                time.sleep(1)
                pass

    def _datascript(self):
        
        self.api.subscribe(self.subscribe_list)
        while True:
            try:
                
                mains = list(self.strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}, {'position': 'in'}]}))

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    executor.map(self.process_strategy, mains)
                if self.testmode:
                    #print(self.prices)
                    time.sleep(1)
                #print(f'data : {str(datetime.datetime.now())}')
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _datascript: {e}")
                time.sleep(1)
                pass
    def _datascript1(self):
        
        self.api.subscribe(self.subscribe_list)
        while True:
            try:
                mains1=[]
                mains = list(self.strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}, {'position': 'in'}]}))
                mains1.extend(mains)
                desired_num_batches = 5
                batch_size = len(mains) // desired_num_batches + (len(mains) % desired_num_batches > 0)
                
                batches = [mains[i:i + batch_size] for i in range(0, len(mains), batch_size)]
                batches += [[]] * (desired_num_batches - len(batches))
                
                if batches[0]:
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        executor.map(self.process_strategy, batches[0])
                else:
                    time.sleep(1)
                    
                #print(f'data1 : {str(datetime.datetime.now())}')
                if self.testmode:
                    #print(self.prices)
                    time.sleep(1)
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _datascript1: {e}")
                time.sleep(1)
                pass
            
    def _datascript2(self):
        
        self.api.subscribe(self.subscribe_list)
        while True:
            try:
                mains1=[]
                mains = list(self.strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}, {'position': 'in'}]}))
                mains1.extend(mains)
                desired_num_batches = 5
                batch_size = len(mains) // desired_num_batches + (len(mains) % desired_num_batches > 0)
                
                batches = [mains[i:i + batch_size] for i in range(0, len(mains), batch_size)]
                batches += [[]] * (desired_num_batches - len(batches))
                
                if batches[1]:
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        executor.map(self.process_strategy, batches[1])
                else:
                    time.sleep(1)
                    
                #print(f'data2 : {str(datetime.datetime.now())}')
                if self.testmode:
                    #print(self.prices)
                    time.sleep(1)
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _datascript2: {e}")
                time.sleep(1)
                pass



    def _datascript3(self):
        
        self.api.subscribe(self.subscribe_list)
        while True:
            try:
                mains1=[]
                mains = list(self.strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}, {'position': 'in'}]}))
                mains1.extend(mains)
                desired_num_batches = 5
                batch_size = len(mains) // desired_num_batches + (len(mains) % desired_num_batches > 0)
                #print((batch_size))
                batches = [mains[i:i + batch_size] for i in range(0, len(mains), batch_size)]
                batches += [[]] * (desired_num_batches - len(batches))
                if batches[2]:
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        executor.map(self.process_strategy, batches[2])
                else:
                    time.sleep(1)
                    
                #print(f'data3 : {str(datetime.datetime.now())}')
                if self.testmode:
                    #print(self.prices)
                    time.sleep(1)
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _datascript3: {e}")
                time.sleep(1)
                pass
    def _datascript4(self):
        
        self.api.subscribe(self.subscribe_list)
        while True:
            try:
                mains1=[]
                mains = list(self.strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}, {'position': 'in'}]}))
                mains1.extend(mains)
                desired_num_batches = 5
                batch_size = len(mains) // desired_num_batches + (len(mains) % desired_num_batches > 0)
                batches = [mains[i:i + batch_size] for i in range(0, len(mains), batch_size)]
                batches += [[]] * (desired_num_batches - len(batches))
                
                if batches[3]:
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        executor.map(self.process_strategy, batches[3])
                else:
                    time.sleep(1)
                    
                #print(f'data4 : {str(datetime.datetime.now())}')
                if self.testmode:
                    #print(self.prices)
                    time.sleep(1)
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _datascript4: {e}")
                time.sleep(1)
                pass
    def _datascript5(self):
        
        self.api.subscribe(self.subscribe_list)
        while True:
            try:
                mains1=[]
                mains = list(self.strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}, {'position': 'in'}]}))
                mains1.extend(mains)
                desired_num_batches = 5
                batch_size = len(mains) // desired_num_batches + (len(mains) % desired_num_batches > 0)
                batches = [mains[i:i + batch_size] for i in range(0, len(mains), batch_size)]
                batches += [[]] * (desired_num_batches - len(batches))
                
                if batches[4]:
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        executor.map(self.process_strategy, batches[4])
                else:
                    time.sleep(1)
                    
                #print(f'data5 : {str(datetime.datetime.now())}')
                if self.testmode:
                    #print(self.prices)
                    time.sleep(1)
            except Exception as e:
                #"staprint(Exception)
                print(f"Error in _datascript5: {e}")
                time.sleep(1)
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



                '''symbol=trade['symbol']
                #print(symbol)
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)

                #print(symbol)
                #print(trade)
                
                trade['decision']='intrade'
                if self.controls[trade['symbol']]['controlmode']:
                    if self.controls[trade['symbol']]['Buytrade'] and (not self.controls[trade['symbol']]['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif self.controls[trade['symbol']]['Selltrade'] and (not self.controls[trade['symbol']]['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                
                if 'onspot' in list(trade.keys()):
                    #print('.nothinds')
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
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
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
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
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })'''
                    
            
            except Exception as e:
                print(f"Error in CHARTINK: {e}")
    def TOPBOTTOM(self,trade):
        #signal-1 for buy -1 for sell
        if self.testmode or ((trade['user'] in self.userloggedin) and (datetime.date.today().weekday() < self.marketdays)):
                #try:
                #print('hell')
                if (not self.topbottomlist and datetime.datetime.now().time()>datetime.datetime.strptime('9:25', '%H:%M').time()):# and datetime.datetime.now().time()<datetime.datetime.strptime('9:30', '%H:%M').time() :
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
                    if self.strategyinputs[trade['strategy']]['update']:
                        gainers=gainers[gainers['net_price']>int(self.strategyinputs[trade['strategy']]['r1'])]
                    else:    
                        gainers=gainers[gainers['net_price']>2]
                    params = {    'index': 'loosers',}
                    response = requests.get('https://www.nseindia.com/api/live-analysis-variations', params=params, cookies=maincookie, headers=headers)
                    losers=pd.DataFrame(response.json()['FOSec']['data'])
                    if self.strategyinputs[trade['strategy']]['update']:
                        losers=losers[losers['net_price']<-int(self.strategyinputs[trade['strategy']]['k1'])]
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
                    if self.strategyinputs[trade['strategy']]['update']:
                        df=df[df['avgInOI']>int(self.strategyinputs[trade['strategy']]['r2'])]
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
                    #print('iam nothing')

                #print('helooo1')
                exSignal=0
                Signal=0
                #print(self.symbols_tok)
                #print('mardalla')
                tf='10m'
                
                df=self.dataframes['NIFTY'].iloc[-self.candleswitch[tf]:]
                #print(df)
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
                #print('hello3')
                #print(list(df1['dates'].uniques()))
                #print(df1)
                lvl1df1=df1[df1['date'].dt.date==dates[-1]]
                lvl2df1=df1[df1['date'].dt.date==dates[-2]]
                #print('hello4')
                #print(lvl1df1)
                #print(lvl2df1)
                if self.strategyinputs[trade['strategy']]['update']:
                    if self.strategyinputs[trade['strategy']]['k2']==float(1):
                        is_green=True#lvl1df1['close'].iloc[-1] > lvl2df1['close'].iloc[-1]
                        is_red=True#lvl1df1['close'].iloc[-1] < lvl2df1['close'].iloc[-1]
                else:
                    is_green=lvl1df1['close'].iloc[-1] > lvl2df1['close'].iloc[-1]
                    is_red=lvl1df1['close'].iloc[-1] < lvl2df1['close'].iloc[-1]

                #print(f'candle today is {is_green}')
                allpositions=list(self.opositions_collection.find({'user':trade['user'],'botcode':trade['botcode'], 'exittime': {'$gte': self.timestamp}}))
                positions=list(self.opositions_collection.find({'user':trade['user'],'status':"open",'botcode':trade['botcode']}))
                dfpositions=pd.DataFrame(positions)
                dfallpositions=pd.DataFrame(allpositions)
                if not trade['user'] in list( self.userstockcount.keys() ):
                    self.userstockcount[trade['user']]=len(positions)
                #print(trade)
                Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime("15:29", '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                positional=(datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and not trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
                if trade['status']=='opened':
                    if True:#len(dfpositions) <= int(trade['stocks']):
                        #print(len(dfpositions) )
                        if Intraday or positional or self.testmode:
                            for s in self.topbottomsymbol:
                                if len(self.dataframes[s]) >0:#.empty:
                                    tf='5m'
                                    if self.strategyinputs[trade['strategy']]['update']:
                                        tf=self.strategyinputs[trade['strategy']]['timeframe']
                                    else:
                                        tf=tf='5m'

                                    
                                    df=self.dataframes[s].iloc[-self.candleswitch[tf]:]
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
                                    df1['sma']=TA.SMA(df1,int(8))
                                    df1=df1[df1['dates']==df1['dates'].iloc[-1]]
                                    #df=df1
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
                                    if s in self.topbottombuylist:
                                        if s not in list(self.breakoutexitsell.keys()):
                                            self.breakoutexitsell[s]=False
                                    #print(self.breakoutexit)

                                    #print()
                                    if len(df2)>1:
                                        is_breakout=high<df2['close'].iloc[-1]# and high>df2['close'].iloc[-2]
                                    else:
                                        is_breakout=high<df2['close'].iloc[-1]
                                    if len(df2)>1:
                                        is_breakoutsell=low>df2['close'].iloc[-1]# and high>df2['close'].iloc[-2]
                                    else:
                                        is_breakoutsell=low>df2['close'].iloc[-1]
                                    if s in self.topbottombuylist:
                                        self.breakoutexit[s]=df2['sma'].iloc[-2] > df2['close'].iloc[-2] and df2['sma'].iloc[-1] > df2['close'].iloc[-1]# and  df2['sma'].iloc[-2] > df2['sma'].iloc[-1] # and df2['sma'].iloc[-3] < df2['close'].iloc[-3] #and df2['close'].iloc[-2] < df2['open'].iloc[-2] and df2['close'].iloc[-1] < df2['open'].iloc[-1]
                                    if s in self.topbottombuylist:
                                        self.breakoutexitsell[s]=df2['sma'].iloc[-2] < df2['close'].iloc[-2] and df2['sma'].iloc[-1] < df2['close'].iloc[-1] #and  df2['sma'].iloc[-2] < df2['sma'].iloc[-1]# and df2['sma'].iloc[-3] < df2['close'].iloc[-3] #and df2['close'].iloc[-2] < df2['open'].iloc[-2] and df2['close'].iloc[-1] < df2['open'].iloc[-1]
                                    positions=list(self.opositions_collection.find({'user':trade['user'],'status':"open",'botcode':trade['botcode']}))
                                    #dfpositions=pd.DataFrame(positions)
                                    if len(positions) < (int(trade['stocks'])):
                                        if len(list(self.opositions_collection.find({'user':trade['user'],'botcode':trade['botcode'],'symbol':s, 'exittime': {'$gte': self.timestamp}}))) ==0:
                                            if datetime.datetime.strptime('9:25', '%H:%M').time() < datetime.datetime.now().time()<datetime.datetime.strptime('10:30', '%H:%M').time():
                                                if (is_breakout and is_green and df2['close'].iloc[-1] >df2['sma'].iloc[-1]) or (is_breakoutsell and ( is_red) and df2['close'].iloc[-1] <df2['sma'].iloc[-1]):
                                                    if trade['positiontype']=='Equity':
                                                        #print('equity')
                                                        self.EBUY(trade,s)#.replace('-EQ',''))
                                                    if trade['positiontype']=='Options':
                                                        self.EOBUY(trade,s)#.replace('-EQ',''))
                                                    if trade['positiontype']=='Future':
                                                        #print('i am tje world')
                                                        self.EFBUY(trade,s)
                self.EBUYEXIT(trade)


                #self.EBUYEXIT(trade)



                '''symbol=trade['symbol']
                #print(symbol)
                if 'onspot' in list(trade.keys()):
                    symbol=self._symboltransformmonthfut(trade['Expiry'],symbol)

                #print(symbol)
                #print(trade)
                
                trade['decision']='intrade'
                if self.controls[trade['symbol']]['controlmode']:
                    if self.controls[trade['symbol']]['Buytrade'] and (not self.controls[trade['symbol']]['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif self.controls[trade['symbol']]['Selltrade'] and (not self.controls[trade['symbol']]['Buytrade']):
                        trade['decision']='intrade'
                        Signal=-1
                        exSignal=-1
                    else:
                        trade['decision']='exitit'
                        Signal=0
                        exSignal=0
                
                if 'onspot' in list(trade.keys()):
                    #print('.nothinds')
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
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
                    Intraday= (datetime.datetime.now().time()>datetime.datetime.strptime(trade['StartTime'], '%H:%M').time() and datetime.datetime.now().time()<datetime.datetime.strptime(trade['ExitTime'], '%H:%M').time()) and trade['Intraday'] and (datetime.date.today().weekday() < self.marketdays)
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
                                    #self.strategy_collection.update_one({'botname': trade['botname']}, {'$set': {'position':'in'} })'''
                    
                #time.sleep(1)
                #except Exception as e:
                #time.sleep(1)
                #print(f"Error in TOPBOTTOM: {e}")

                
                
    def EMA(self,trade):
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
                        if self.strategyinputs[trade['strategy']]['update']:
                            tf=self.strategyinputs[trade['strategy']]['timeframe']
                        else:
                            tf=trade['timeframe']
                        df=self.dataframes[symbol].iloc[-self.candleswitch[tf]:]


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
                        trends=list(df1['result'])#self.ASSALGO(df1,trade['r1'],trade['k1'])
                        trends1=list(df1['result'])#self.ASSALGO(df1,trade['r2'],trade['k2'])
                        #print(df1)
                        #print(trade['timeframe'])
                        #print(trends[-5:])
                        #print(trends1)
                        
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
                trade['decision']='intrade'
                if self.controls[trade['symbol']]['controlmode']:
                    if self.controls[trade['symbol']]['Buytrade'] and (not self.controls[trade['symbol']]['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif self.controls[trade['symbol']]['Selltrade'] and (not self.controls[trade['symbol']]['Buytrade']):
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
                print(f"Error in EMA: {e}")                
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

                if self.controls[trade['symbol']]['controlmode']:
                    if self.controls[trade['symbol']]['Buytrade'] and (not self.controls[trade['symbol']]['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif self.controls[trade['symbol']]['Selltrade'] and (not self.controls[trade['symbol']]['Buytrade']):
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
                if self.controls[trade['symbol']]['controlmode']:
                    if self.controls[trade['symbol']]['Buytrade'] and (not self.controls[trade['symbol']]['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif self.controls[trade['symbol']]['Selltrade'] and (not self.controls[trade['symbol']]['Buytrade']):
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
                trade['decision']='intrade'
                if self.controls[trade['symbol']]['controlmode']:
                    if self.controls[trade['symbol']]['Buytrade'] and (not self.controls[trade['symbol']]['Selltrade']):
                        trade['decision']='intrade'
                        Signal=1
                        exSignal=1
                    elif self.controls[trade['symbol']]['Selltrade'] and (not self.controls[trade['symbol']]['Buytrade']):
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

    def FBUY(self,trade,OTYPE,Signal):
        try:
            
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
            instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
            print(instrument)
            if type(instrument)==dict:
                instrument = Instrument(exchange=exch, token=optiontoken, symbol=trade['symbol'], name=option, expiry='', lot_size=optionlot)
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
            
            if trade['live']:
                lot=trade['lot']
                if lot>20:
                    totalquant=[trade['slicing']]*int(lot/trade['slicing'])
                    if (lot%trade['slicing'])>0 :
                        totalquant.append(lot%trade['slicing'])
                    for quant in totalquant:
                        #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                        ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Buy ,
                             instrument = instrument,
                             quantity = int(optionlot)*int(quant),
                             order_type = OrderType.Market,
                             product_type =ProductType.Delivery,
                             price = 0.0,
                             trigger_price = None,
                             stop_loss = None,
                             square_off = None,
                             trailing_sl = None,
                             is_amo = False,
                             order_tag='order1')
                        

                else:
                    #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
                    ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Buy ,
                                     instrument = instrument,
                                     quantity = int(optionlot)*int(trade['lot']),
                                     order_type = OrderType.Market,
                                     product_type =ProductType.Delivery,
                                     price = 0.0,
                                     trigger_price = None,
                                     stop_loss = None,
                                     square_off = None,
                                     trailing_sl = None,
                                     is_amo = False,
                                     order_tag='order1')
                    
                print(ret)
            #print('i am goee')
            
            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(pricesss)
            ,'side':OTYPE,'status':"open",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(option), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':Signal,'exitcond':self.oppocond(Signal),'entry_id':int(datetime.datetime.now().timestamp()),'live':trade['live'],
            'exch':exch,'current_price':float(pricesss),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0}
            #print(pos)
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
        except Exception as e:
            print(f"Error in FBUY: {e}")

        
    def FSELL(self,trade,OTYPE,Signal):
        try:
            
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
            instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
            print(instrument)

            if type(instrument)==dict:
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=trade['symbol'], name=option, expiry='', lot_size=optionlot)
            if trade['live']:
                lot=trade['lot']
                if lot>20:
                    totalquant=[trade['slicing']]*int(lot/trade['slicing'])
                    if (lot%trade['slicing'])>0 :
                        totalquant.append(lot%trade['slicing'])
                    for quant in totalquant:
                        #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                        ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                             instrument = instrument,
                             quantity = int(optionlot)*int(quant),
                             order_type = OrderType.Market,
                             product_type =ProductType.Delivery,
                             price = 0.0,
                             trigger_price = None,
                             stop_loss = None,
                             square_off = None,
                             trailing_sl = None,
                             is_amo = False,
                             order_tag='order1')
                        

                else:
                    #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
                    ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                                     instrument = instrument,
                                     quantity = int(optionlot)*int(trade['lot']),
                                     order_type = OrderType.Market,
                                     product_type =ProductType.Delivery,
                                     price = 0.0,
                                     trigger_price = None,
                                     stop_loss = None,
                                     square_off = None,
                                     trailing_sl = None,
                                     is_amo = False,
                                     order_tag='order1')
                    
                print(ret)
            #print('i am goee')
            pricesss=0
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[symbol])
            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(pricesss)
            ,'side':OTYPE,'status':"open",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(option), 'pnlhalf':0,"decision":"intrade",'BSmode':False,'entrycond':Signal,'exitcond':self.oppocond(Signal),'entry_id':int(datetime.datetime.now().timestamp()),'live':trade['live'],
            'exch':exch,'current_price':float(pricesss),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0}
            #print(pos)
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
        except Exception as e:
            print(f"Error in FSELL: {e}")

        
            




    def EBUY(self,trade,symbol):
            #try:

            #MainEquitySelect
            option,optionlot,optiontoken=self.MainEquitySelect(symbol)
            #option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])
            #print(option,optionlot,optiontoken)

            mainoption=option
            side='BUY'
            if symbol in self.topbottombuylist:
                side='BUY'
                #option,optionlot,optiontoken=self.MainEquitySelect(symbol)
            if symbol in self.topbottomselllist:
                side='SELL'
                #option,optionlot,optiontoken=self.MainEquitySelect(symbol)

            
            self.add_symbol_to_websocket(str(option))
            #print(self.prices)
            trade['option']=str(option)

            exch='NSE'
            #if trade['symbol']=='CRUDEOIL':
            #    exch='MCX'
            instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
            print(instrument)
            #option=option.replace('-EQ','')
            if type(instrument)==dict:
                
                self.add_symbol_to_websocket(str(option))
                ftok=exch+'|'+str(int(optiontoken))
                self.add_to_websocket(ftok)
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=symbol, name=str(option), expiry='', lot_size=int(optionlot))
            print(self.prices)
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])
            
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
            if trade['live']:
                pos=ProductType.Delivery
                if trade['positiontype']=='Equity':
                    pos=ProductType.Intraday
                trans=TransactionType.Buy if side == 'BUY' else TransactionType.Sell
                ret=self.alice[trade['user']].place_order(transaction_type =trans ,
                                 instrument = instrument,
                                 quantity = int(lot)*int(optionlot),
                                 order_type = OrderType.Market,
                                 product_type =pos,
                                 price = 0.0,
                                 trigger_price = None,
                                 stop_loss = None,
                                 square_off = None,
                                 trailing_sl = None,
                                 is_amo = False,
                                 order_tag='order1')
                
                print(ret)
            #print('i am goee')
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])

            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(self.prices[option])
            ,'side':side,'status':"open",'pnl':0,'lot':int(lot),'initial_lot':int(lot),
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str('-'),
            'optionname':str(trade['option']), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':1,'exitcond':-1,'entry_id':int(datetime.datetime.now().timestamp()),'live':trade['live'],
            'exch':exch,'current_price':float(self.prices[option]),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,'exittime':int(datetime.datetime.now().timestamp())}
            #print(pos)

            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
            #except Exception as e:
            #print(self.prices)
            #print(f"Error in EBUY: {e}")

    def EOBUY(self,trade,symbol):
            #try:

            #MainEquitySelect
            option,optionlot,optiontoken=self.MainEquitySelect( symbol)
            #option,optionlot,optionexpiry,optiontoken
            mainoption=option
            side='BUY'
            if symbol in self.topbottombuylist:
                side='BUY'
                option,optionlot,optionexpiry,optiontoken=self.MainEquityOptionSelect(option, 'CE', 0,'Current Week')
            if symbol in self.topbottomselllist:
                side='SELL'
                option,optionlot,optionexpiry,optiontoken=self.MainEquityOptionSelect(option, 'PE', 0,'Current Week')
            #option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])
            
            self.add_symbol_to_websocket(str(option))
            #print(self.prices)
            trade['option']=str(option)

            exch='NFO'
            #if trade['symbol']=='CRUDEOIL':
            #    exch='MCX'
            #instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
            #if type(instrument)==dict:
            #print(self.NfoAB[self.NfoAB['Trading Symbol']==option])
            #print(option,optionlot,optionexpiry,optiontoken)
            instrument=self.alice[trade['user']].get_instrument_by_token(exch,(optiontoken))
            #print(instrument)
            if type(instrument)==dict:
                #instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=symbol, name=str(option), expiry='', lot_size=int(optionlot))
            print(self.alice[trade['user']].get_scrip_info(instrument))

            #print(self.prices)
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])
            
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
            print('i am mini ##############################')
            if trade['live']:
                print('i start firing##################')
                trans=TransactionType.Buy if side == 'BUY' else TransactionType.Sell
                ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Buy ,
                                 instrument = instrument,
                                 quantity = int(optionlot)*int(lot),
                                 order_type = OrderType.Market,
                                 product_type =ProductType.Delivery,
                                 price = 0.0,
                                 trigger_price = None,
                                 stop_loss = None,
                                 square_off = None,
                                 trailing_sl = None,
                                 is_amo = False,
                                 order_tag='order1')
                print('tried fired ########################')
                
                print(ret)
            #print('i am goee')
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])

            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(self.prices[mainoption])
            ,'side':side,'status':"open",'pnl':0,'lot':int(lot),'initial_lot':int(lot),
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(trade['option']), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':1,'exitcond':-1,'entry_id':int(datetime.datetime.now().timestamp()),'live':trade['live'],
            'exch':exch,'current_price':float(self.prices[mainoption]),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,'exittime':int(datetime.datetime.now().timestamp())}
            #print(pos)

            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
            #except Exception as e:
            #print(self.prices)
            #print(f"Error in EOBUY: {e}")
    def EFBUY(self,trade,symbol):
            #try:

            #MainEquitySelect
            option,optionlot,optiontoken=self.MainEquitySelect( symbol)
            #option,optionlot,optionexpiry,optiontoken
            print(option,optionlot,optiontoken)
            side='BUY'
            if symbol in self.topbottombuylist:
                side='BUY'
            if symbol in self.topbottomselllist:
                side='SELL'
            option,optionlot,optionexpiry,optiontoken=self.MainFutureSelect(option,'Current Month')
            #option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])
            print(option,optionlot,optionexpiry,optiontoken)
            self.add_symbol_to_websocket(str(option))
            #print(self.prices)
            trade['option']=str(option)

            exch='NFO'
            #if trade['symbol']=='CRUDEOIL':
            #    exch='MCX'
            #instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
            #if type(instrument)==dict:
            #print(self.NfoAB[self.NfoAB['Trading Symbol']==option])
            
            instrument=self.alice[trade['user']].get_instrument_by_token(exch,(optiontoken))
            #print(instrument)
            if type(instrument)==dict:
                #instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
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
                ret=self.alice[trade['user']].place_order(transaction_type =trans ,
                                 instrument = instrument,
                                 quantity = int(lot)*int(optionlot),
                                 order_type = OrderType.Market,
                                 product_type =ProductType.Delivery,
                                 price = 0.0,
                                 trigger_price = None,
                                 stop_loss = None,
                                 square_off = None,
                                 trailing_sl = None,
                                 is_amo = False,
                                 order_tag='order1')


                print('tried fired ########################')
                print(ret)
                
                #print(ret)
            #print('i am goee')
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])

            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':symbol,'entry_price':float(self.prices[option])
            ,'side':side,'status':"open",'pnl':0,'lot':int(lot),'initial_lot':int(lot),
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(trade['option']), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':1,'exitcond':-1,'entry_id':int(datetime.datetime.now().timestamp()),'live':trade['live'],
            'exch':exch,'current_price':float(self.prices[option]),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0,'exittime':int(datetime.datetime.now().timestamp())}
            print(pos)

            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
            #except Exception as e:
            #print(self.prices)
            #print(f"Error in EOBUY: {e}")
    def OBUY(self,trade,OTYPE,Signal):
        try:


            option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])
            
            rollover1=datetime.datetime.strptime(str(optionexpiry)+' '+str(trade['RolloverTime']), "%Y-%m-%d %H:%M")
            if (datetime.datetime.now()+datetime.timedelta(days=trade['DaysHead']))>=rollover1:
                
                if 'Current Week' in trade['Expiry']:
                    trade['Expiry']='Next Week'
                elif 'Current Month' in trade['Expiry']:
                    trade['Expiry']='Next Month'

                option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect(trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])             


            #print('gta5')
            option=str(option)
            print(option)
            

            
            self.add_symbol_to_websocket(str(option))
            #print(self.prices)
            trade['option']=str(option)

            exch='NFO'
            if trade['symbol']=='CRUDEOIL':
                exch='MCX'
            instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])
            if type(instrument)==dict:
                
                self.add_symbol_to_websocket(str(option))
                ftok=exch+'|'+str(int(optiontoken))
                self.add_to_websocket(ftok)
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=trade['symbol'], name=str(option), expiry='', lot_size=int(optionlot))
            if self.websocketretry >10:
                #self.api.close_websocket()
                
                self.api.subscribe(self.subscribe_list)
                self.add_symbol_to_websocket(option)
                if option not in list(self.prices.keys()):
                    self.websocketretry=0
            if option not in list(self.prices.keys()):
                self.websocketretry=self.websocketretry+1
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])

            print('option price: {}'.format(str(pricesss)))
            print(instrument)
            if trade['live']:
                print('kutaa')
                lot=trade['lot']
                if lot>20:
                    totalquant=[trade['slicing']]*int(lot/trade['slicing'])
                    if (lot%trade['slicing'])>0 :
                        totalquant.append(lot%trade['slicing'])
                    for quant in totalquant:
                        #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                        ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Buy ,
                             instrument = instrument,
                             quantity = int(optionlot)*int(quant),
                             order_type = OrderType.Market,
                             product_type =ProductType.Delivery,
                             price = 0.0,
                             trigger_price = None,
                             stop_loss = None,
                             square_off = None,
                             trailing_sl = None,
                             is_amo = False,
                             order_tag='order1')
                    print('kutta1')

                else:
                    #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
                    ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Buy ,
                                     instrument = instrument,
                                     quantity = int(optionlot)*int(trade['lot']),
                                     order_type = OrderType.Market,
                                     product_type =ProductType.Delivery,
                                     price = 0.0,
                                     trigger_price = None,
                                     stop_loss = None,
                                     square_off = None,
                                     trailing_sl = None,
                                     is_amo = False,
                                     order_tag='order1')
                    print('kutta2')
                    print(ret)
                    print(instrument)
                    print('emnd')
                    
                print(ret)
            print('i am goee')
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])

            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':trade['symbol'],'entry_price':float(self.prices[trade['symbol']])
            ,'side':OTYPE,'status':"open",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(option), 'pnlhalf':0,"decision":"intrade",'BSmode':True,'entrycond':Signal,'exitcond':self.oppocond(Signal),'entry_id':int(datetime.datetime.now().timestamp()),'live':trade['live'],
            'exch':exch,'current_price':float(self.prices[trade['symbol']]),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0}
            #print(pos)
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
        except Exception as e:
            #print(self.prices)
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




    def EBUYEXIT(self,trade1):
        try:
            config=trade1
            trades=list(self.opositions_collection.find({'botcode':config['botcode'],'status':'open','user':config['user']}))
            #print(trades)
            for trade in trades:
                #print(len(trades))
                if trade is not None:
                    #print(trade['optionname'])
                    opppp=False
                    #print(len(trades))
                    if trade['optionname'] not in list(self.prices.keys()):
                        #print('i am trying to get option')
                        #print(trade['optionname'])
                        opppp=self.add_symbol_to_websocket(trade['optionname'])

                    if config['strategy']=='SSEQUITYFNO':
                        if (trade['symbol']+'-EQ') not in list(self.prices.keys()):
                            opppp=self.add_symbol_to_websocket(trade['symbol']+'-EQ')

                    
                    if trade['optionname'] in list(self.prices.keys()):
                        pricesss=float(self.prices[trade['optionname']])
                    elif trade['optionname'] in list(self.sprices.keys()):
                        pricesss=float(self.sprices[trade['optionname']])
                    else:
                        #print('ia m godinisds')
                        pricesss=float(self.prices[trade['optionname']])
                    #print('hello')
                    trade['current_price']=self.prices[(trade['symbol']+'-EQ')]
                    #print('hello1')
                    #print((trade['symbol']+'-EQ'))
                    #print(trade['current_price'])
                    trade['optionexit']=pricesss
                    #print('hello2')
                    #print(self.prices)
                    #print(self.sprices)
                    trade['pnl']=int((pricesss-trade['optionentry'])*trade['lot']*trade['optionlot'])
                    if config['strategy']=='SSEQUITYFNO':
                        if trade['side']=='BUY' and  config['positiontype']=='Future':
                            trade['pnl']=int((pricesss-trade['optionentry'])*trade['lot']*trade['optionlot'])
                        if trade['side']!='BUY' and config['positiontype']=='Future':
                            trade['pnl']=int((trade['optionentry']-pricesss)*trade['lot']*trade['optionlot'])
                        if trade['side']!='BUY' and config['positiontype']=='Equity':
                            trade['pnl']=int((trade['optionentry']-pricesss)*trade['lot']*trade['optionlot'])
                    
                    #print('hello3')
                    perlotpnl=int((pricesss-trade['optionentry'])*trade['optionlot'])
                    
                    Signal=False
                    if trade['side'] =='BUY':
                        if trade['symbol'] not in list(self.breakoutexit.keys()):
                            self.breakoutexit[trade['symbol']]=False
                        if config['strategy']=='SSEQUITYFNO':
                            Signal=self.breakoutexit[trade['symbol']]
                    if trade['side'] =='SELL':
                        if trade['symbol'] not in list(self.breakoutexitsell.keys()):
                            self.breakoutexitsell[trade['symbol']]=False
                        if config['strategy']=='SSEQUITYFNO':
                            Signal=self.breakoutexitsell[trade['symbol']]
                    userr=trade['user']
                    #print('hello2')
                    if config['trail']==1:
                        if 'trail_stoploss' not in list(trade.keys()):
                            trade['trail_stoploss']=0
                        kti=config['trail_stoploss']*2
                        dti=int(perlotpnl/config['trail_stoploss'])

                        if perlotpnl>=kti and trade['trail_stoploss']==0:
                            
                            trade['trail_stoploss']=config['trail_stoploss']
                        elif perlotpnl>=kti and trade['trail_stoploss'] !=0:
                            fti=int(trade['trail_stoploss']/config['trail_stoploss'])
                            #if fti >1:
                            if (dti-fti) > 1:
                                trade['trail_stoploss']=trade['trail_stoploss']+config['trail_stoploss']
                    else:
                        trade['trail_stoploss']=0
                    #print('helo3')
                    if config['pct_point']:
                        ex=(trade['optionentry'])*(1+(config['tp']/100))
                        sl=(trade['optionentry'])*(1-(config['sl']/100))
                    else:
                        ex=(trade['optionentry'])+config['tp']
                        sl=(trade['optionentry'])-config['sl']


                    if (trade['pnl']>=(config['tp']*int(config['lot']))) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL TP HIT ### ')
                        trade['status']='close'
                        config['position']='out'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })

                    elif (trade['pnl'] <= -(config['sl']*int(config['lot']))) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL SL HIT ### ')
                        trade['status']='close'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())                 
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    
                    elif  (trade['pnl'] <= (trade['trail_stoploss']*int(config['lot']))) and trade['trail_stoploss']!=0 and config['trail']==1:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY TRAIL SL HIT ### ')
                        trade['status']='close'
                        config['position']='out'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    
                    elif (trade['optionexit']>ex) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### TP HIT ### ')
                        trade['status']='close'
                        config['position']='out'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    
                    elif (trade['optionexit']<sl) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### SL HIT ### ')
                        trade['status']='close'
                        config['position']='out'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    #print('hello4')
                    if trade['decision']=='exitit':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### USER EXIT HIT ### ')
                        trade['status']='close'
                        config['position']='out'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })  
                    elif Signal:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### USER Signal EXIT HIT ### ')
                        trade['status']='close'
                        config['position']='out'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })                    
                    elif config['status']=='paused':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        trade['status']='close'
                        config['position']='out'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    elif config['status']=='closed':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        trade['status']='close'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    
                    elif datetime.datetime.now().time()>datetime.datetime.strptime(config['ExitTime'], '%H:%M').time() and config['Intraday']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Intraday EXIT HIT ### ')
                        trade['status']='close'
                        if trade['live']:
                            self.mainebuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    
                    trade['exittime']=int(datetime.datetime.now().timestamp())                         
                    self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    #print('hello5')
        except Exception as e:
            print(f"Error in EBUYEXIT: {e}")
            


    def OBUYEXIT(self,trade,Signal,exSignal):
        #if trade['side']=='CE':

        try:
            config=trade
            #print(trade)
            #print(' iam noting')
            #print(self.prices)
            trades=list(self.opositions_collection.find({'botcode':config['botcode'],'status':'open','user':trade['user']}))
            #print(trades)
            if len(trades)==0:
                self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': {'position':'out'} })
            
            for trade in trades:
                if trade is not None:
                    if trade['user']=='sjguptha':
                        print(trade['optionname'])
                        print(trade['optionexit'])
                    if config['decision']=='exitit':
                        trade['decision']='exitit'
                        config['decision']='intrade'
                    opppp=self.add_symbol_to_websocket(trade['optionname'])
                    if self.websocketretry >10:
                        print('websocket repair')
                        
                        self.add_symbol_to_websocket(trade['optionname'])
                        if trade['optionname'] in list(self.prices.keys()):
                            self.websocketretry=0
                    if trade['optionname'] not in list(self.prices.keys()):
                        self.websocketretry=self.websocketretry+1
                    
                    if opppp:
                        trade['status']='close'
                        config['position']='out'
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })
                            return True
                    if trade['optionname'] in list(self.prices.keys()):
                        pricesss=float(self.prices[trade['optionname']])
                    elif trade['optionname'] in list(self.sprices.keys()):
                        pricesss=float(self.sprices[trade['optionname']])
                    else:
                        pricesss=float(self.prices[trade['optionname']])
                    trade['current_price']=self.prices[trade['symbol']]
                    trade['optionexit']=pricesss
                    trade['pnl']=int((pricesss-trade['optionentry'])*trade['lot']*trade['optionlot'])
                    perlotpnl=int((pricesss-trade['optionentry'])*trade['optionlot'])

                    userr=trade['user']
                    #print(userr)
                    #print('hello')
                    rollover=datetime.datetime.strptime(trade['optionexpiry'], "%Y-%m-%d")-datetime.timedelta(days=config['DaysHead'])
                    rollover=str(rollover.date())
                    #print(userr)
                    if config['trail']==1:
                        if 'trail_stoploss' not in list(trade.keys()):
                            trade['trail_stoploss']=0
                        kti=config['trail_stoploss']*2
                        dti=int(perlotpnl/config['trail_stoploss'])

                        if perlotpnl>=kti and trade['trail_stoploss']==0:
                            
                            trade['trail_stoploss']=config['trail_stoploss']
                        elif perlotpnl>=kti and trade['trail_stoploss'] !=0:
                            fti=int(trade['trail_stoploss']/config['trail_stoploss'])
                            #if fti >1:
                            if (dti-fti) > 1:
                                trade['trail_stoploss']=trade['trail_stoploss']+config['trail_stoploss']
                    else:
                        trade['trail_stoploss']=0




                    if config['pct_point']:
                        ex=(trade['optionentry'])*(1+(config['tp']/100))
                        sl=(trade['optionentry'])*(1-(config['sl']/100))
                    else:
                        ex=(trade['optionentry'])+config['tp']
                        sl=(trade['optionentry'])-config['sl']


                    if (trade['exitcond']==Signal):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Exit HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']




                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif (trade['pnl']>=(config['tp']*config['lot'])) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif (trade['pnl'] <= -(config['sl']*config['lot'])) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif trade['pnl'] >= (config['maxprofit']*config['lot']):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY MAXPROFIT TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif (trade['pnl'] <= -(config['maxloss']*config['lot'])):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY MAXLOSS SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })
                    elif  (trade['pnl'] <= (trade['trail_stoploss']*config['lot'])) and trade['trail_stoploss']!=0 and config['trail']==1:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY TRAIL SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']
                        if trade['live']:
                            self.mainbuyexit(trade,config)
                    elif (trade['optionexit']>ex) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif (trade['optionexit']<sl) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif trade['decision']=='exitit':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### USER EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif config['status']=='paused':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    elif config['status']=='closed':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    elif datetime.datetime.now().time()>datetime.datetime.strptime(config['ExitTime'], '%H:%M').time() and config['Intraday']:

                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Intraday EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    

                    elif datetime.datetime.now().time()>datetime.datetime.strptime(config['RolloverTime'], '%H:%M').time() and ((str(datetime.date.today())==trade['optionexpiry']) or (str(datetime.date.today())==rollover)):

                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Option Expiry EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainbuyexit(trade,config)


                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })
                    trade['exittime']=int(datetime.datetime.now().timestamp())
                    #del trade['_id']
                         
                    self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                    if trade['status']=='close':
                        if config['Newsignal']:
                            config['timetowait']=int(datetime.datetime.now().timestamp())-1
                        else:
                            config['timetowait']=int(datetime.datetime.now().timestamp())+int((config['ttw']*60))
                        self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })

                    
                    #print('Cycle completed')
        except Exception as e:
            print(f"Error in OBUYEXIT: {e}")
            





    def OSELL(self,trade,OTYPE,Signal):
        try:
            option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect( trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])

            rollover1=datetime.datetime.strptime(str(optionexpiry)+' '+str(trade['RolloverTime']), "%Y-%m-%d %H:%M")
            if (datetime.datetime.now()+datetime.timedelta(days=trade['DaysHead']))>=rollover1:
                
                if 'Current Week' in trade['Expiry']:
                    trade['Expiry']='Next Week'
                elif 'Current Month' in trade['Expiry']:
                    trade['Expiry']='Next Month'

                option,optionlot,optionexpiry,optiontoken=self.MainOptionSelect(trade['symbol'],OTYPE, trade['strike'],trade['Expiry'])             


            self.add_symbol_to_websocket(str(option))
            #print(self.prices)
            trade['option']=str(option)
            exch='NFO'
            if trade['symbol']=='CRUDEOIL':
                exch='MCX'
            instrument=self.alice[trade['user']].get_instrument_by_symbol(exch, trade['option'])


            if type(instrument)==dict:
                
                self.add_symbol_to_websocket(str(option))
                ftok=exch+'|'+str(int(optiontoken))
                self.add_to_websocket(ftok)
                instrument = Instrument(exchange=exch, token=int(optiontoken), symbol=trade['symbol'], name=str(option), expiry='', lot_size=int(optionlot))
            if self.websocketretry >10:
                

                self.api.subscribe(self.subscribe_list)
                self.add_symbol_to_websocket(option)
                if option not in list(self.prices.keys()):
                    self.websocketretry=0
            if option not in list(self.prices.keys()):
                self.websocketretry=self.websocketretry+1

            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])

            print('option price: {}'.format(str(pricesss)))
            print(instrument)
            
            if trade['live']:
                lot=trade['lot']
                if lot>20:
                    totalquant=[trade['slicing']]*int(lot/trade['slicing'])
                    if (lot%trade['slicing'])>0 :
                        totalquant.append(lot%trade['slicing'])
                    for quant in totalquant:
                        #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                        ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                             instrument = instrument,
                             quantity = int(instrument.lot_size)*int(quant),
                             order_type = OrderType.Market,
                             product_type =ProductType.Delivery,
                             price = 0.0,
                             trigger_price = None,
                             stop_loss = None,
                             square_off = None,
                             trailing_sl = None,
                             is_amo = False,
                             order_tag='order1')
                        if ret['stat']!='Ok':
                            ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                             instrument = instrument,
                             quantity = int(instrument.lot_size)*int(quant),
                             order_type = OrderType.Market,
                             product_type =ProductType.Delivery,
                             price = 0.0,
                             trigger_price = None,
                             stop_loss = None,
                             square_off = None,
                             trailing_sl = None,
                             is_amo = False,
                             order_tag='order1')

                else:
                    #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
                    ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                                     instrument = instrument,
                                     quantity = int(instrument.lot_size)*int(trade['lot']),
                                     order_type = OrderType.Market,
                                     product_type =ProductType.Delivery,
                                     price = 0.0,
                                     trigger_price = None,
                                     stop_loss = None,
                                     square_off = None,
                                     trailing_sl = None,
                                     is_amo = False,
                                     order_tag='order1')
                    if ret['stat']!='Ok':
                        ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                                     instrument = instrument,
                                     quantity = int(instrument.lot_size)*int(trade['lot']),
                                     order_type = OrderType.Market,
                                     product_type =ProductType.Delivery,
                                     price = 0.0,
                                     trigger_price = None,
                                     stop_loss = None,
                                     square_off = None,
                                     trailing_sl = None,
                                     is_amo = False,
                                     order_tag='order1')

                print(ret)
            if option in list(self.prices.keys()):
                pricesss=float(self.prices[option])
            elif option in list(self.sprices.keys()):
                pricesss=float(self.sprices[option])
            else:
                pricesss=float(self.prices[option])

            #print('option price: {}'.format(str(pricesss)))
            pos={'user':str(trade['user']),'botname':trade['botname'],'time':int(datetime.datetime.now().timestamp()),'symbol':trade['symbol'],'entry_price':float(self.prices[trade['symbol']])
            ,'side':OTYPE,'status':"open",'pnl':0,'lot':trade['lot'],'initial_lot':trade['lot'],
            'optionentry':float(pricesss),'optionexit':float(pricesss),'optionlot':int(optionlot),'optionexpiry':str(optionexpiry),
            'optionname':str(option), 'pnlhalf':0,"decision":"intrade",'BSmode':False,'entrycond':Signal,'exitcond':self.oppocond(Signal),'entry_id':int(datetime.datetime.now().timestamp()),'live':trade['live'],
            'exch':exch,'current_price':float(self.prices[trade['symbol']]),'botcode':trade['botcode'],'optiontoken':int(optiontoken),'trail_stoploss':0}
            #print(pos)
            self.opositions_collection.insert_one(pos)
            self.strategy_collection.update_one({'botcode': trade['botcode']}, {'$set': {'position':'in'} })
        except Exception as e:
            print(f"Error in OSELL: {e}")



    def FEXIT(self,trade,Signal):
        #if trade['side']=='CE':
        try:
            config=trade
            #print(self.prices)
            #print(config)
            trades=list(self.opositions_collection.find({'botcode':config['botcode'],'status':'open','user':trade['user']}))
            #print(trades)
            if len(trades)==0:
                self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': {'position':'out'} })

            for trade in trades:
                #print('i am not none')
                if trade is not None:
                    self.add_symbol_to_websocket(trade['optionname'])
                    #opppp=self.add_symbol_to_websocket(trade['symbol'])
                    #if opppp:
                    '''    trade['status']='close'
                        config['position']='out'
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })
                            return True'''

                    if trade['optionname'] in list(self.prices.keys()):
                        trade['current_price']=self.prices[trade['optionname']]
                        trade['optionexit']=self.prices[trade['optionname']]
                    elif trade['optionname'] in list(self.sprices.keys()):
                        trade['current_price']=self.sprices[trade['optionname']]
                        trade['optionexit']=self.sprices[trade['optionname']]
                    else:
                        trade['current_price']=self.prices[trade['symbol']]
                        trade['optionexit']=self.prices[trade['symbol']]
                    #print(trade)
                    perlotpnl=0
                    if 'SELL' == trade['side']:
                        trade['pnl']=int((trade['optionentry']-trade['optionexit'])*trade['lot']*trade['optionlot'])
                        perlotpnl=int((trade['optionentry']-trade['optionexit'])*trade['optionlot'])
                    else:
                        trade['pnl']=int((trade['optionexit']-trade['optionentry'])*trade['lot']*trade['optionlot'])
                        perlotpnl=int((trade['optionexit']-trade['optionentry'])*trade['optionlot'])
                    userr=trade['user']
                    rollover=datetime.datetime.strptime(trade['optionexpiry'], "%Y-%m-%d")-datetime.timedelta(days=config['DaysHead'])
                    rollover=str(rollover.date())
                    if config['trail']==1:
                        if 'trail_stoploss' not in list(trade.keys()):
                            trade['trail_stoploss']=0
                        kti=config['trail_stoploss']*2
                        dti=int(perlotpnl/config['trail_stoploss'])

                        if perlotpnl>=kti and trade['trail_stoploss']==0:
                            
                            trade['trail_stoploss']=config['trail_stoploss']
                        elif perlotpnl>=kti and trade['trail_stoploss'] !=0:
                            fti=int(trade['trail_stoploss']/config['trail_stoploss'])
                            #if fti >1:
                            if (dti-fti) > 1:
                                trade['trail_stoploss']=trade['trail_stoploss']+config['trail_stoploss']
                    else:
                        trade['trail_stoploss']=0



                    #print(userr)
                    #print(trade)

                    if config['pct_point']:
                        if trade['side']=='BUY':
                            ex=(trade['optionentry'])*(1+(config['tp']/100))
                            sl=(trade['optionentry'])*(1-(config['sl']/100))
                        else:
                            ex=(trade['optionentry'])*(1-(config['tp']/100))
                            sl=(trade['optionentry'])*(1+(config['sl']/100))
                    else:
                        if trade['side']=='BUY':

                            ex=(trade['optionentry'])+config['tp']
                            sl=(trade['optionentry'])-config['sl']
                        else:
                            ex=(trade['optionentry'])-config['tp']
                            sl=(trade['optionentry'])+config['sl']
                    if (trade['exitcond']==Signal):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Exit HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                            #print(ret)
                    elif (trade['pnl']>=(config['tp']*config['lot'])) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif (trade['pnl'] <= -(config['sl']*config['lot'])) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)                    
                    elif trade['pnl'] >= (config['maxprofit']*config['lot']):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY MAXPROFIT TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif (trade['pnl'] <= -(config['maxloss']*config['lot'])):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY MAXLOSS SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif  (trade['pnl'] <= (trade['trail_stoploss']*config['lot'])) and trade['trail_stoploss']!=0 and config['trail']==1:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY TRAIL SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)

                    elif (trade['optionexit']>ex) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif (trade['optionexit']<sl) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif trade['decision']=='exitit':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### USER EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif config['status']=='paused':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif config['status']=='closed':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)
                    elif datetime.datetime.now().time()>datetime.datetime.strptime(config['ExitTime'], '%H:%M').time() and config['Intraday']:

                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Intraday EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)

                    elif datetime.datetime.now().time()>datetime.datetime.strptime(config['RolloverTime'], '%H:%M').time() and ((str(datetime.date.today())==trade['optionexpiry']) or (str(datetime.date.today())==rollover)):

                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Option Expiry EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            if trade['side']=='BUY':
                                self.mainbuyexit(trade,config)
                            else:
                                self.mainsellexit(trade,config)

                    trade['exittime']=int(datetime.datetime.now().timestamp())
                    #del trade['_id']
                    
                    self.opositions_collection.update_one({'_id':trade['_id'],'entry_id': trade['entry_id']}, {'$set': trade })
                del config['_id']
                if trade['status']=='close':
                    if config['Newsignal']:
                        config['timetowait']=int(datetime.datetime.now().timestamp())-1
                    else:
                        config['timetowait']=int(datetime.datetime.now().timestamp())+int((config['ttw']*60))
                    self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': config })
                #print('Cycle completed')
        except Exception as e:
            print(f"Error in FEXIT: {e}")
    

    def OSELLEXIT(self,trade,Signal,exSignal):
        #if trade['side']=='CE':
        try:
            config=trade
            #print(self.prices)
            trades=list(self.opositions_collection.find({'botcode':config['botcode'],'status':'open','user':trade['user']}))
            #print(trade)
            if len(trades)==0:
                self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': {'position':'out'} })

            for trade in trades:

                if trade is not None:
                    opppp=self.add_symbol_to_websocket(trade['optionname'])
                    if self.websocketretry >10:
                        

                        self.api.subscribe(self.subscribe_list)
                        self.add_symbol_to_websocket(option)
                        if trade['optionname'] in list(self.prices.keys()):
                            self.websocketretry=0
                    if trade['optionname'] not in list(self.prices.keys()):
                        self.websocketretry=self.websocketretry+1
                    if opppp:
                        trade['status']='close'
                        config['position']='out'
                        trade['exittime']=int(datetime.datetime.now().timestamp())
                        #del trade['_id']
                             
                        self.opositions_collection.update_one({'_id':trade['_id']}, {'$set': trade })
                        if trade['status']=='close':
                            self.strategy_collection.update_one({'_id':config['_id']}, {'$set': config })
                            return True

                    if trade['optionname'] in list(self.prices.keys()):
                        pricesss=float(self.prices[trade['optionname']])
                    elif trade['optionname'] in list(self.sprices.keys()):
                        pricesss=float(self.sprices[trade['optionname']])
                    else:
                        pricesss=float(self.prices[trade['optionname']])
                    trade['current_price']=self.prices[trade['symbol']]
                    trade['optionexit']=pricesss
                    trade['pnl']=int((trade['optionentry']-pricesss)*trade['lot']*trade['optionlot'])
                    perlotpnl=int((trade['optionentry']-trade['optionexit'])*trade['optionlot'])
                    userr=trade['user']
                    rollover=datetime.datetime.strptime(trade['optionexpiry'], "%Y-%m-%d")-datetime.timedelta(days=config['DaysHead'])
                    rollover=str(rollover.date())
                    #print(userr)
                    if config['trail']==1:
                        if 'trail_stoploss' not in list(trade.keys()):
                            trade['trail_stoploss']=0
                        kti=config['trail_stoploss']*2
                        dti=int(perlotpnl/config['trail_stoploss'])

                        if perlotpnl>=kti and trade['trail_stoploss']==0:
                            
                            trade['trail_stoploss']=config['trail_stoploss']
                        elif perlotpnl>=kti and trade['trail_stoploss'] !=0:
                            fti=int(trade['trail_stoploss']/config['trail_stoploss'])
                            #if fti >1:
                            if (dti-fti) > 1:
                                trade['trail_stoploss']=trade['trail_stoploss']+config['trail_stoploss']
                    else:
                        trade['trail_stoploss']=0




                    if config['pct_point']:
                        ex=(trade['optionentry'])*(1+(config['tp']/100))
                        sl=(trade['optionentry'])*(1-(config['sl']/100))
                    else:
                        ex=(trade['optionentry'])+config['tp']
                        sl=(trade['optionentry'])-config['sl']
                    if (trade['exitcond']==exSignal):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Exit HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']




                        if trade['live']:
                            self.mainsellexit(trade,config)
                            #print(ret)
                    elif (trade['pnl']>=(config['tp']*config['lot'])) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif (trade['pnl'] <= -(config['sl']*config['lot'])) and config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### PNL SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif trade['pnl'] >= (config['maxprofit']*config['lot']):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY MAXPROFIT TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            
                            self.mainsellexit(trade,config)
                    elif (trade['pnl'] <= -(config['maxloss']*config['lot'])):
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY MAXLOSS SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif  (trade['pnl'] <= (trade['trail_stoploss']*config['lot'])) and trade['trail_stoploss']!=0 and config['trail']==1:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### DAY TRAIL SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif (trade['optionexit']>ex) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### TP HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif (trade['optionexit']<sl) and not config['pnlexit_tpslexit']:
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### SL HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif trade['decision']=='exitit':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### USER EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif config['status']=='paused':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif config['status']=='closed':
                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### BOT EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)
                    elif datetime.datetime.now().time()>datetime.datetime.strptime(config['ExitTime'], '%H:%M').time() and config['Intraday']:

                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Intraday EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']

                        if trade['live']:
                            self.mainsellexit(trade,config)

                    elif datetime.datetime.now().time()>datetime.datetime.strptime(config['RolloverTime'], '%H:%M').time() and ((str(datetime.date.today())==trade['optionexpiry']) or (str(datetime.date.today())==rollover)):

                        print(f'{str(datetime.datetime.now())} :: {userr} :: ### Option Expiry EXIT HIT ### ')
                        #trade['lot']=0
                        trade['status']='close'
                        config['position']='out'
                        if trade['pnl']< 0 and config['FixedLot']=='Doubling':
                            config['lot']=config['lot']+config['lot']
                        elif trade['pnl']< 0 and config['FixedLot']=='FixedLot':
                            config['lot']=(config['lot'])
                        elif trade['pnl']< 0 and config['FixedLot']=='Steps':
                            config['lot']=config['lot']+config['stepvalue']
                        elif trade['pnl']>0:
                            config['lot']=config['initiallot']
                        if trade['live']:
                            self.mainsellexit(trade,config)
                    trade['exittime']=int(datetime.datetime.now().timestamp())
                    self.opositions_collection.update_one({'_id':trade['_id'],'entry_id': trade['entry_id']}, {'$set': trade })
                del config['_id']
                if trade['status']=='close':
                    if config['Newsignal']:
                        config['timetowait']=int(datetime.datetime.now().timestamp())-1
                    else:
                        config['timetowait']=int(datetime.datetime.now().timestamp())+int((config['ttw']*60))
                    self.strategy_collection.update_one({'botcode': trade['botcode'],'user':trade['user']}, {'$set': config })
                #print('Cycle completed')
        except Exception as e:
            print(f"Error in OSELLEXIT: {e}")
    

    def _ABcontracts(self):
        Nse=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/NSE.csv')
        Cds=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/CDS.csv')
        Mcx=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/MCX.csv')
        Nfo=pd.read_csv('https://v2api.aliceblueonline.com/restpy/static/contract_master/NFO.csv')

        return Nse,Cds,Mcx,Nfo


    def mainebuyexit(self,trade,config):
        instrument=self.alice[trade['user']].get_instrument_by_symbol(trade['exch'], trade['optionname'])
        if type(instrument)==dict:
            #token=self.symbol_to_token(trade['optionname'])
            instrument = Instrument(exchange=trade['exch'], token=int(self.tok_symbols[trade['optionname']][4:]), symbol=trade['symbol'], name=trade['optionname'], expiry='', lot_size=trade['optionlot'])
            #instrument=self.alice[trade['user']].get_instrument_by_token(exch,token)
        lot=trade['lot']
        '''if lot>20:
                                    totalquant=[config['slicing']]*int(lot/config['slicing'])
                                    if (lot%config['slicing'])>0 :
                                        totalquant.append(lot%config['slicing'])
                                    for quant in totalquant:
                                        #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                                        ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                                             instrument = instrument,
                                             quantity = int(trade['optionlot'])*int(quant),
                                             order_type = OrderType.Market,
                                             product_type =ProductType.Delivery,
                                             price = 0.0,
                                             trigger_price = None,
                                             stop_loss = None,
                                             square_off = None,
                                             trailing_sl = None,
                                             is_amo = False,
                                             order_tag='order1')
                                        print(ret)
                                        
                        
                                else:'''
        #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
        pos=ProductType.Delivery
        if config['positiontype']=='Equity':
            pos=ProductType.Intraday

        if config['positiontype'] in ['Future','Equity']:
            trans = TransactionType.Sell if trade['side'] == 'BUY' else TransactionType.Buy
        else:
            trans=TransactionType.Sell
        ret=self.alice[trade['user']].place_order(transaction_type =trans ,
                         instrument = instrument,
                         quantity = int(trade['optionlot'])*int(config['lot']),
                         order_type = OrderType.Market,
                         product_type =pos,
                         price = 0.0,
                         trigger_price = None,
                         stop_loss = None,
                         square_off = None,
                         trailing_sl = None,
                         is_amo = False,
                         order_tag='order1')
        print(ret)
        
    def mainbuyexit(self,trade,config):
        instrument=self.alice[trade['user']].get_instrument_by_symbol(trade['exch'], trade['optionname'])
        if type(instrument)==dict:
            #token=self.symbol_to_token(trade['optionname'])
            instrument = Instrument(exchange=trade['exch'], token=int(self.tok_symbols[trade['optionname']][4:]), symbol=trade['symbol'], name=trade['optionname'], expiry='', lot_size=trade['optionlot'])
            #instrument=self.alice[trade['user']].get_instrument_by_token(exch,token)
        lot=trade['lot']
        if lot>20:
            totalquant=[config['slicing']]*int(lot/config['slicing'])
            if (lot%config['slicing'])>0 :
                totalquant.append(lot%config['slicing'])
            for quant in totalquant:
                #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                     instrument = instrument,
                     quantity = int(trade['optionlot'])*int(quant),
                     order_type = OrderType.Market,
                     product_type =ProductType.Delivery,
                     price = 0.0,
                     trigger_price = None,
                     stop_loss = None,
                     square_off = None,
                     trailing_sl = None,
                     is_amo = False,
                     order_tag='order1')
                print(ret)
                

        else:
            #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
            ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Sell ,
                             instrument = instrument,
                             quantity = int(trade['optionlot'])*int(trade['lot']),
                             order_type = OrderType.Market,
                             product_type =ProductType.Delivery,
                             price = 0.0,
                             trigger_price = None,
                             stop_loss = None,
                             square_off = None,
                             trailing_sl = None,
                             is_amo = False,
                             order_tag='order1')
            print(ret)
            

    def mainsellexit(self,trade,config):
        instrument=self.alice[trade['user']].get_instrument_by_symbol(trade['exch'], trade['optionname'])

        if type(instrument)==dict:
            instrument = Instrument(exchange=trade['exch'], token=int(self.tok_symbols[trade['optionname']][4:]), symbol=trade['symbol'], name=trade['optionname'], expiry='', lot_size=trade['optionlot'])

        lot=trade['lot']
        if lot>20:
            totalquant=[config['slicing']]*int(lot/config['slicing'])
            if (lot%config['slicing'])>0 :
                totalquant.append(lot%config['slicing'])
            for quant in totalquant:
                #place_trade('NFO',trade['EntryOption'], quant, 'sell')
                ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Buy ,
                     instrument = instrument,
                     quantity = int(trade['optionlot'])*int(quant),
                     order_type = OrderType.Market,
                     product_type =ProductType.Delivery,
                     price = 0.0,
                     trigger_price = None,
                     stop_loss = None,
                     square_off = None,
                     trailing_sl = None,
                     is_amo = False,
                     order_tag='order1')
                print(ret)
                

        else:
            #place_trade('NFO',trade['EntryOption'], trade['Lot'], 'sell')
            ret=self.alice[trade['user']].place_order(transaction_type =TransactionType.Buy ,
                             instrument = instrument,
                             quantity = int(trade['optionlot'])*int(trade['lot']),
                             order_type = OrderType.Market,
                             product_type =ProductType.Delivery,
                             price = 0.0,
                             trigger_price = None,
                             stop_loss = None,
                             square_off = None,
                             trailing_sl = None,
                             is_amo = False,
                             order_tag='order1')
            print(ret)
            
        
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
        if type(token) == str:
            self.subscribe_list.append(token)
            self.api.subscribe(token)
        elif type(token) == list:
            self.subscribe_list.extend(token)
            self.api.subscribe(token)
        else:
            print('')
    def add_to_swebsocket(self, token):
        if type(token) == str:
            self.subscribe_slist.append(token)
            self.updatelist=True
            #self.api.subscribe(token)
        elif type(token) == list:
            self.subscribe_slist.extend(token)
            self.updatelist=True
            #self.api.subscribe(token)
        else:
            print('')

    
    def hist(self, symbol, tf="1", initial=True):
        # Calculate time frame
        try:
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
        except:
            return 0
            pass
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


    def add_symbol_to_websocket(self, symbol):
        destory=False
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
                self.symbols_tok[token] = symbol
                if stoken in self.samlist:
                    self.subscribe_slist.append(stoken)
                    self.stok_symbols[symbol] = stoken
                    self.symbols_stok[stoken] = symbol
                    self.add_to_swebsocket(stoken)

                self.add_to_websocket(token)
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

            if key in self.SYMBOLDICT:
                # print(key)
                # symbol_info =  self.SYMBOLDICT[key]
                # symbol_info.update(message)
                # print(symbol_info['lp'])
                self.SYMBOLDICT[key] = message['lp']
                # print(SYMBOLDICT[key]['lp'])
                self.prices[self.symbols_tok[key]] = float(message['lp'])
            else:
                self.SYMBOLDICT[key] = message['lp']
                self.prices[self.symbols_tok[key]] = float(message['lp'])
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
    def OptionSelect(self, Symbol, ordertype, strike):
        # self._add_log(self.prices)
        # if Symbol=='BANKNIFTY':

        CurrentPrice = self.prices[Symbol]
        indexltp = float(CurrentPrice)
        mod = int(indexltp) % 50
        if mod < 25:
            atmstrike = int(math.floor(indexltp/100))*100
        else:
            atmstrike = int(math.ceil(indexltp/100))*100
        nextweek = True
        print(Symbol)
        if Symbol in list(self.Nfo['Symbol']):
            k = self.Nfo[self.Nfo['Symbol'] == Symbol]
        elif Symbol in list(self.Mcx['Symbol']):
            k = self.Mcx[self.Mcx['Symbol'] == Symbol]
        elif Symbol in list(self.Cds['Symbol']):
            k = self.Cds[self.Cds['Symbol'] == Symbol]
        #print(k)
        k['Expiry_'] = k['Expiry_'].dt.date
        stre = list(k['Expiry_'].unique())
        stre.sort(reverse=False)
        day = datetime.date.today()+datetime.timedelta(days=1)
        if ((day > stre[0]) or (datetime.date.today() in stre)) and nextweek:
            q = list(k['Expiry_'].unique())
            q.sort(reverse=False)
            q = q[1]
            k = k[k['Expiry_'] == q]
        else:
            q = list(k['Expiry_'].unique())
            q.sort(reverse=False)
            q = q[0]
            k = k[k['Expiry_'] == q]
        batmstrike = abs(atmstrike-(strike*100))
        bk = k[k['StrikePrice'] == batmstrike]
        satmstrike = abs(atmstrike+(strike*100))
        sk = k[k['StrikePrice'] == satmstrike]
        if ordertype == 'CE':
            k = bk[bk['OptionType'] == 'CE']
            self.add_symbol_to_websocket( k.iloc[-1]['TradingSymbol'])
            return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize']
        else:
            k = sk[sk['OptionType'] == 'PE']
            self.add_symbol_to_websocket( k.iloc[-1]['TradingSymbol'])
            return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize']
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

        CurrentPrice = self.prices[Symbol]
        indexltp = float(CurrentPrice)
        #print(Symbol)
        #print(duration)
        Symbol=Symbol.replace('-EQ','')
        if Symbol in list(self.Nfo['Symbol']):
            k = self.Nfo[self.Nfo['Symbol'] == Symbol]
        elif Symbol in list(self.Nfo['Symbol']):
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
        if 'Current Month' in duration:
            k=k.iloc[0]
            print(k)
            self.add_symbol_to_websocket( k['TradingSymbol'])
            return k['TradingSymbol'], k['LotSize'],k['Expiry_'],k['Token']
        elif 'Next Month' in duration:
            k=k.iloc[1]
            self.add_symbol_to_websocket( k['TradingSymbol'])
            return k['TradingSymbol'], k['LotSize'],k['Expiry_'],int(k['Token'])
        elif 'Third Month' in duration:
            k=k.iloc[2]
            self.add_symbol_to_websocket( k['TradingSymbol'])
            return k['TradingSymbol'], k['LotSize'],k['Expiry_'],int(k['Token'])


    def MainEquityOptionSelect(self, Symbol, ordertype, strike,duration):
        # self._add_log(self.prices)
        # if Symbol=='BANKNIFTY':

        CurrentPrice = self.prices[Symbol]
        indexltp = float(CurrentPrice)

        Symbol=Symbol.replace('-EQ','')
        if Symbol in list(self.Nfo['Symbol']):
            k = self.Nfo[self.Nfo['Symbol'] == Symbol]
        elif Symbol in list(self.Nfo['Symbol']):
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
        index_of_find = gt.index(atmstrike)
        value_two_ahead = gt[index_of_find -(strike)]

        batmstrike = abs(gt[index_of_find -(strike)])
        bk = k[k['StrikePrice'] == batmstrike]
        satmstrike = abs(gt[index_of_find +(strike)])
        sk = k[k['StrikePrice'] == satmstrike]
        if ordertype == 'CE':
            k = bk[bk['OptionType'] == 'CE']
            self.add_symbol_to_websocket( k.iloc[-1]['TradingSymbol'])
            #print(k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'],k.iloc[-1]['Expiry_'],int(k.iloc[-1]['Token']))
            return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'],k.iloc[-1]['Expiry_'],int(k.iloc[-1]['Token'])
        else:
            k = sk[sk['OptionType'] == 'PE']
            self.add_symbol_to_websocket( k.iloc[-1]['TradingSymbol'])
            return k.iloc[-1]['TradingSymbol'], k.iloc[-1]['LotSize'],k.iloc[-1]['Expiry_'],int(k.iloc[-1]['Token'])
    def MainOptionSelect(self, Symbol, ordertype, strike,duration):
        # self._add_log(self.prices)
        # if Symbol=='BANKNIFTY':

        CurrentPrice = self.prices[Symbol]
        indexltp = float(CurrentPrice)
        #print(Symbol)
        print(duration)
        if Symbol in list(self.Nfo['Symbol']):
            k = self.Nfo[self.Nfo['Symbol'] == Symbol]
        elif Symbol in list(self.Nfo['Symbol']):
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
                print(f'downloading {zip_file}')
                url = root + zip_file
                r = requests.get(url, allow_redirects=True)
                open(zip_file, 'wb').write(r.content)
                file_to_extract = zip_file.split()

                try:
                    with zipfile.ZipFile(zip_file) as z:
                        z.extractall()
                        print("Extracted: ", zip_file)
                except:
                    print("Invalid file")

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

        n = Nse
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = str(lists[i][0])+' '+str(lists[i][3])+' '+str(lists[i][5])
            lists[i].append(hold)
        Nse = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'Symbol', 'TradingSymbol', 'Instrument',
                                           'TickSize', 'Ticker'])
        Nse['FToken'] = Nse['Exchange']+'|'+Nse['Token'].apply(str)
        Nse['SToken']=Nse['Token'].apply(str)+'_NSE'

        n = Bse
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = str(lists[i][0])+' '+str(lists[i][3])+' '+str(lists[i][5])
            lists[i].append(hold)
        Bse = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'Symbol', 'TradingSymbol', 'Instrument',
                                           'TickSize', 'Ticker'])
        Bse['FToken'] = Bse['Exchange']+'|'+Bse['Token'].apply(str)
        Bse['SToken']=Bse['Token'].apply(str)+'_BSE'

        n = Mcx
        lists = n.values.tolist()
        for i in range(0, len(lists)):
            hold = str(lists[i][0])+' '+str(lists[i][4])+' '+str(lists[i]
                                                                 [6].replace('-', ''))+' '+str(lists[i][9])+' '+str(lists[i][8])
            lists[i].append(hold)
        Mcx = pd.DataFrame(lists, columns=['Exchange', 'Token', 'LotSize', 'GNGD', 'Symbol', 'TradingSymbol',
                                           'Expiry', 'Instrument', 'OptionType', 'StrikePrice', 'TickSize', 'Ticker'])
        Mcx = Mcx[Mcx['Symbol'] == 'CRUDEOIL']
        Mcx['FToken'] = Mcx['Exchange']+'|'+Mcx['Token'].apply(str)
        Mcx['SToken']=Mcx['Token'].apply(str)+'_MFO'
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

        print('i ami pakistan')

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
                self.ws.run_forever(ping_interval=3, reconnect=5)

            except Exception as e:
                print(f"An error occurred in s websocket: {e}")
                time.sleep(5)
                import websocket
                self.login()
                headers = {'x-session-token': self.session_token}

                self.ws = websocket.WebSocketApp("wss://stream.stocknote.com", on_open=self.on_open, on_message=self.on_message,
                                                on_error=self.on_error, on_close=self.on_close, header=headers)
                self.ws.run_forever(ping_interval=3, reconnect=5)
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
