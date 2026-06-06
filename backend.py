from traceback import format_exc
import ssl
import hashlib
from bson import json_util
from bson import ObjectId
import razorpay
import datetime
from flask_socketio import SocketIO, emit
from waitress import serve
from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response, session,flash
from flask_jwt_extended import JWTManager, create_access_token, decode_token, jwt_required, get_jwt_identity
#from pymongo import MongoClient
import pymongo
import certifi
import requests
from connectors.connector import Exchange
import logging
import yaml
import pyotp
from levelbased import *
from threading import Thread,Lock
from models import *
import json
from flask_mail import Mail, Message
import bcrypt
import secrets
import hashlib
import ast
from tabulate import tabulate
import re
from bs4 import BeautifulSoup
from backend_modules.api_apikey_routes import register_apikey_api_routes
from backend_modules.api_auth_routes import register_auth_api_routes
from backend_modules.auth_security import ApiAuthManager
from backend_modules.broker_registry import broker_payload
from backend_modules.config import AppConfig
from backend_modules.shared_services import BackendServices
from exchangeload import *
from random import randint
#from datetime import datetime, timedelta
import functools
import logging
"""
Planning to add more here eventually, for now will be used for handling keys.
"""

# Set this to something unique.
pin = '1234'


# Generate unique token from pin.  This adds a marginal amount of security.
def get_token():
    token = hashlib.sha224(pin.encode('utf-8'))
    return token.hexdigest()


def parse_webhook(webhook_data):

    """
    This function takes the string from tradingview and turns it into a python dict.
    :param webhook_data: POST data from tradingview, as a string.
    :return: Dictionary version of string.
    """

    data = ast.literal_eval(webhook_data)
    return data
cred = {}

#gddfgdf
# from NorenRestApiPy.NorenApi import  NorenApi
from NorenApi import NorenApi
def quickedit(enabled=0): # This is a patch to the system that sometimes hangs
        import ctypes

        # -10 is input handle => STD_INPUT_HANDLE (DWORD) -10 | https://docs.microsoft.com/en-us/windows/console/getstdhandle
        # default = (0x4|0x80|0x20|0x2|0x10|0x1|0x40|0x200)
        # 0x40 is quick edit, #0x20 is insert mode
        # 0x8 is disabled by default
        # https://docs.microsoft.com/en-us/windows/console/setconsolemode
        kernel32 = ctypes.windll.kernel32
        if enabled:
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), (0x4|0x80|0x20|0x2|0x10|0x1|0x40|0x100))
            print("Console Quick Edit Enabled")
        else:
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), (0x4|0x80|0x00|0x2|0x10|0x1|0x00|0x100))
            print("Console Quick Edit Disabled")

#quickedit(0) # Disable quick edit in terminaldef quickedit(enabled=1): # This is a patch to the system that sometimes hangs

''''''
#quickedit(0) # Disable quick edit in terminal

class Order:
    def __init__(self, buy_or_sell: str = None, product_type: str = None,
                 exchange: str = None, tradingsymbol: str = None,
                 price_type: str = None, quantity: int = None,
                 price: float = None, trigger_price: float = None, discloseqty: int = 0,
                 retention: str = 'DAY', remarks: str = "tag",
                 order_id: str = None):
        self.buy_or_sell = buy_or_sell
        self.product_type = product_type
        self.exchange = exchange
        self.tradingsymbol = tradingsymbol
        self.quantity = quantity
        self.discloseqty = discloseqty
        self.price_type = price_type
        self.price = price
        self.trigger_price = trigger_price
        self.retention = retention
        self.remarks = remarks
        self.order_id = None


class ShoonyaApiPy(NorenApi):

    def __init__(self):
        NorenApi.__init__(self, host='https://api.shoonya.com/NorenWClientTP/',
                          websocket='wss://api.shoonya.com/NorenWSTP/')
        global api
        api = self

    def place_basket(self, orders):

        resp_err = 0
        resp_ok = 0
        result = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:

            future_to_url = {executor.submit(
                self.place_order, order): order for order in orders}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
            try:
                result.append(future.result())
            except Exception as exc:
                print(exc)
                resp_err = resp_err + 1
            else:
                resp_ok = resp_ok + 1

        return result

    def placeOrder(self, order: Order):
        ret = NorenApi.place_order(self, buy_or_sell=order.buy_or_sell, product_type=order.product_type,
                                   exchange=order.exchange, tradingsymbol=order.tradingsymbol,
                                   quantity=order.quantity, discloseqty=order.discloseqty, price_type=order.price_type,
                                   price=order.price, trigger_price=order.trigger_price,
                                   retention=order.retention, remarks=order.remarks)
        # print(ret)

        return ret
'''
api1 = ShoonyaApiPy()
api = ShoonyaApiPy()
# ret = api.login(userid=user, password=pwd, twoFA=token, vendor_code=vc, api_secret=app_key, imei=imei)
ret = api.login(userid=cred['user'], password=cred['pwd'], twoFA=pyotp.TOTP(
    cred['factor2']).now(), vendor_code=cred['vc'], api_secret=cred['apikey'], imei=cred['imei'])
sessionusertoken=ret['susertoken']
'''
# Create and configure the logger object

logger = logging.getLogger()

logger.setLevel(logging.ERROR)


ca = certifi.where()
app = Flask(__name__, static_url_path='/static')
from flask_cors import CORS
app.config.from_object(AppConfig)
CORS(app, resources={r"/*": {"origins": AppConfig.CORS_ALLOWED_ORIGINS}}, supports_credentials=True)

jwt = JWTManager(app)
logging.basicConfig(filename='werkzeug_errors.log', level=logging.ERROR)
# Prefer eventlet/gevent when installed, otherwise fall back to threading.
async_mode = None
for _candidate in ('eventlet', 'gevent', 'threading'):
    try:
        if _candidate == 'threading':
            async_mode = 'threading'
            break
        __import__(_candidate)
        async_mode = _candidate
        break
    except ImportError:
        continue
socketio = SocketIO(app, async_mode=async_mode, cors_allowed_origins=AppConfig.CORS_ALLOWED_ORIGINS)
thread = None
thread_lock = Lock()
# Get the logger for werkzeug
log = logging.getLogger('werkzeug')

log.setLevel(logging.ERROR)

app.secret_key = AppConfig.FLASK_SECRET_KEY

mail = Mail(app)
threads = {}
# your connection string
# client = MongoClient(    "mongodb+srv://ramakrishnamekala129:Ramu1303@cluster0.cjdnkgy.mongodb.net/")
client = pymongo.MongoClient(AppConfig.MONGO_URI, maxPoolSize=100)
db = client[AppConfig.MONGO_DB]

freeday=1
orders_collection = db["orders"]
positions_collection = db["positions"]
users_collection = db["users"]
apikeys_collection = db["apis"]
strategy_collection=db['strategies']
eqstrategy_collection=db['eqstrategies']
history_collection = db["historical"]
opositions_collection=db['Opositions']
payreceipt_collection=db['payreceipt']
subscriptionperiod_collection=db['subscriptionperiod']
admincontrol_collection=db['admincontrol']
stockstoday_collection=db['stocktoday']
broker_collection=db['broker']

adminco=list(admincontrol_collection.find())
strategyinput_collection=db['strategyinput']
strategyco=list(strategyinput_collection.find())

if not adminco:
    aa=[
    {'symbol':"BANKNIFTY",'controlmode':False,'Buytrade':True,'Selltrade':False},
    {'symbol':"NIFTY",'controlmode':False,'Buytrade':True,'Selltrade':False},
    {'symbol':"FINNIFTY",'controlmode':False,'Buytrade':True,'Selltrade':False},
    {'symbol':"MIDCPNIFTY",'controlmode':False,'Buytrade':True,'Selltrade':False},
    {'symbol':"SENSEX",'controlmode':False,'Buytrade':True,'Selltrade':False}]
    admincontrol_collection.insert_many(aa)
if not strategyco:
    aa=[
    {'strategy':"EMA",'r1':19,'k1':20,'r2':19,'k2':20,"timeframe":"5m",'update':False},
    {'strategy':"SSALGO",'r1':2,'k1':2,'r2':2,'k2':2,"timeframe":"5m",'update':False},
    {'strategy':"SSAUTO",'r1':5,'k1':10,'r2':5,'k2':10,"timeframe":"5m",'update':False},
    {'strategy':"PEMA",'r1':10,'k1':20,'r2':10,'k2':20,"timeframe":"5m",'update':False},
    {'strategy':"SSEQUITYFNO",'r1':2,'k1':2,'r2':7,'k2':0,"timeframe":"5m",'update':False},
    {'strategy':"SSTRIKE",'r1':143,'k1':143,'r2':0,'k2':0,"timeframe":"5m",'update':False},
    {'strategy':"RF",'r1':20,'k1':3.5,'r2':0,'k2':0,"timeframe":"5m",'update':False},


    ]
    strategyinput_collection.insert_many(aa)

adminco=list(admincontrol_collection.find({'symbol':"SENSEX"}))
if not adminco:
    aa=[{'symbol':"SENSEX",'controlmode':False,'Buytrade':True,'Selltrade':False}]
    admincontrol_collection.insert_many(aa)



strategyco=list(strategyinput_collection.find({'strategy':"EQSSALGO"}))
if not strategyco:
    aa=[{'strategy':"EQSSALGO",'r1':2,'k1':2,'r2':2,'k2':2,"timeframe":"5m",'update':False},]
    strategyinput_collection.insert_many(aa)
history_collection.create_index([("time", pymongo.ASCENDING), ("symbol", pymongo.ASCENDING)], unique=True)





uu='stemplates/'
kk='_form.html'
mform={}
def clean_jinja(value):
    """Remove Jinja templating elements from a string."""
    return jinja_pattern.sub('', value).strip()

def get_label_text(input_id):
    """Find the label text associated with an input/select tag using its 'id'."""
    label = soup.find('label', {'for': input_id})
    return label.text.strip() if label else 'Unnamed Field'

urls=['ema','ema_fut','rf','ssalgo','ssalgo_fut','ssequity_eq','ssequityfno_eq','sstrike','eqssalgo','fractalnubiatimehedgeorder']
for url in urls:
    with open(uu + url + kk, 'r') as file:
        html_content = file.read()

    soup = BeautifulSoup(html_content, 'html.parser')
    jinja_pattern = re.compile(r'{%.*?%}|{{.*?}}')
    input_tags = soup.find_all(['input', 'select'])
    tags_list = []
    for tag in input_tags:
        tag_dict = {'tag': tag.name}
        for attr, value in tag.attrs.items():
            if isinstance(value, str):
                cleaned_value = clean_jinja(value)
                if cleaned_value:
                    tag_dict[attr] = cleaned_value
            else:
                tag_dict[attr] = value
        input_id = tag.get('id', '')
        tag_dict['label'] = get_label_text(input_id)
        if tag.name == 'input' and tag.attrs.get('type') in ['hidden', 'checkbox', 'radio']:
            tag_dict.pop('required', None)
        if tag.name == 'select':
            tag_dict['options'] = []
            for option in tag.find_all('option'):
                option_dict = {
                    'value': clean_jinja(option.get('value', '')),
                    'text': clean_jinja(option.text)
                }
                tag_dict['options'].append(option_dict)
        tag_dict.pop('class', None)
        tag_dict.pop('id', None)
        if tag_dict['name'] not in ['ooption','ostrike','oside','oexpiry','olot']:
            tags_list.append(tag_dict)
    mform['add_'+url+kk]=tags_list
    # Special handling for fractalnubiatimehedgeorder_form.html to support add-row table
    if url == 'fractalnubiatimehedgeorder':
        table = soup.find('table', {'id': 'optionsTable'})
        if table:
            headers = [th.get_text(strip=True) for th in table.find_all('th')]
            tbody = table.find('tbody')
            row_template = None
            if tbody:
                first_row = tbody.find('tr')
                if first_row:
                    row_template = []
                    for td in first_row.find_all('td'):
                        input_tag = td.find(['input', 'select'])
                        if input_tag:
                            tag_dict = {'tag': input_tag.name}
                            for attr, value in input_tag.attrs.items():
                                if isinstance(value, str):
                                    cleaned_value = clean_jinja(value)
                                    if cleaned_value:
                                        tag_dict[attr] = cleaned_value
                                else:
                                    tag_dict[attr] = value
                            if input_tag.name == 'select':
                                tag_dict['options'] = []
                                for option in input_tag.find_all('option'):
                                    option_dict = {
                                        'value': clean_jinja(option.get('value', '')),
                                        'text': clean_jinja(option.text)
                                    }
                                    tag_dict['options'].append(option_dict)
                            tag_dict.pop('class', None)
                            tag_dict.pop('id', None)
                            row_template.append(tag_dict)
                        else:
                            row_template.append({'tag': 'td', 'text': td.get_text(strip=True)})
            tags_list.append({'tag': 'table', 'children': row_template})
            mform['add_'+url+kk] = tags_list
        else:
            mform['add_'+url+kk] = []
  
#print(mform)



'''
trader = Exchange(api, db,cred,api1,sessionusertoken)
trader.real=True
'''

def redine():
    orders = list(positions_collection.find())
    for j in orders:
        trader.ordersids.append(j['entry_id'])
        trader.add_symbol_to_websocket((j['optionname']))
        trader.trades.append(LevelBasedTrade(j))

def redine1():
    #try:
    orders = list(orders_collection.find())
    for j in orders:
        #if j['time'] not in trader.ordersids:
        try:
            trader.fakeorders[j['time']]=j
            trader.breakoutstrats[j['time']] = HuntLevel(trader, j['trigger_price'], j['trigger_type'], j['symbol'], j['comparator_type'],
                                                 j['option_type'], j['strike'], j['lot'], j['trail'], j['trail_stoploss'], j['tp_1'], j['tp_2'], j['sl'], j['strike'], j['time'])
        except:
            pass

    orders = list(positions_collection.find())
    for j in orders:
        #if j['time'] not in trader.ordersids:
        try:
            if j['status']=='open':
                trader.add_symbol_to_websocket(j['optionname'])
                trader.positions[j['entry_id']]=j
            else:
                #if j['status']=='open':
                #trader.add_symbol_to_websocket(j['optionname'])
                trader.closedpositions[j['entry_id']]=j
        except:
            pass
    orders = list(strategy_collection.find({'status': {'$in': ['opened', 'paused']}}))
    for j in orders:
        try:
            trader.ostrategies.append(j)
        except:
            pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Helper functions for common operations
def get_user_from_token(token):
    """Get user from token with projection to limit returned fields"""
    return users_collection.find_one({'username': token})


api_auth_manager = ApiAuthManager(users_collection, decode_token)


def get_bearer_token():
    return api_auth_manager.get_bearer_token(request)


def get_user_from_access_token(access_token):
    return api_auth_manager.get_user_from_access_token(access_token)


API_AUTH_EXEMPT_ENDPOINTS = {
    '/api_login',
    '/api_register',
    '/api_logout',
    '/api_forgot_reset_password',
    '/api_reset_password',
    '/api_forgot_otp_reset_password',
    '/api_otp_verify',
    '/api_otp_reset_password',
    '/api_pricing',
}


@app.before_request
def require_api_access_token():
    if not request.path.startswith('/api_'):
        return None
    if request.path in API_AUTH_EXEMPT_ENDPOINTS or request.path.startswith('/api_reset_password/'):
        return None

    access_token = get_bearer_token()
    if not access_token:
        return generate_response(message='Authorization bearer token is required', success=False, status_code=401)

    user = get_user_from_access_token(access_token)
    if not user:
        return generate_response(message='Invalid or expired authorization token', success=False, status_code=401)

    form_token = request.form.get('token')
    if form_token and form_token != user['username']:
        logger.warning(f"API token user mismatch on {request.path}")
        return generate_response(message='Token user mismatch', success=False, status_code=403)

    request.api_user = user
    return None

def generate_response(data=None, message="Success", success=True, status_code=200):
    """Generate a standardized API response"""
    return jsonify({'success': success, 'message': message, 'data': data}), status_code

def create_botcode(username, botname):
    """Generate a unique botcode for strategies"""
    user = users_collection.find_one({'username': username})
    created_at_ms = int(datetime.datetime.now().timestamp() * 1000)
    unique_suffix = secrets.token_hex(3)
    return '{}_{}_{}_{}_{}'.format(
        botname, 
        str(user['_id']), 
        created_at_ms,
        user['mobile'],
        unique_suffix
    )

# Authentication decorators
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            logger.warning(f"Unauthorized access attempt to {request.path}")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            logger.warning(f"Unauthorized access attempt to {request.path}")
            return redirect(url_for('login'))
        
        userdata = users_collection.find_one({'username': session['username']})
        if 'admin' not in userdata or not userdata['admin']:
            logger.warning(f"Non-admin user {session['username']} attempted to access admin route {request.path}")
            return redirect(url_for('index'))
        
        return f(*args, **kwargs)
    return decorated_function

def api_token_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        bearer_user = getattr(request, 'api_user', None)
        token = request.form.get('token')
        if not token:
            logger.warning(f"API call without token to {request.path}")
            return generate_response(message='Authentication token is missing', success=False, status_code=401)
        
        user = bearer_user or get_user_from_token(token)
        if not user:
            logger.warning(f"API call with invalid token to {request.path}")
            return generate_response(message='User not found', success=False, status_code=404)

        if bearer_user and token != bearer_user['username']:
            logger.warning(f"API token user mismatch on {request.path}")
            return generate_response(message='Token user mismatch', success=False, status_code=403)
        
        # Add user to kwargs so the route can access it
        kwargs['user'] = user
        return f(*args, **kwargs)
    return decorated_function

#redine()
tanl = 1
@app.route("/api_searchsymbol", methods=["POST", "GET"])
def searchsymbol():
    query = request.form.get('query') if request.method == 'POST' else request.args.get('query')

    # Check if query is provided and has at least 3 characters
    if not query or len(query) < 3:
        return jsonify({'success': False, 'message': 'Query must be at least 3 characters long.'}), 400

    # Filter the NseList based on the search query
    results = [symbol for symbol in trader.Nselist if query.upper() in symbol.upper()]

    return jsonify({'success': True, 'results': results})


@app.route('/wss')
def wsindex():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('wssindex.html', async_mode=socketio.async_mode)

@socketio.on('connect')
def handle_connect(auth=None):
    if 'username' in session:
        user = session.get('username', 'default_user')
    else:
        access_token = (auth or {}).get('token') if isinstance(auth, dict) else None
        authenticated_user = get_user_from_access_token(access_token) if access_token else None
        requested_user = request.args.get('username')
        if not authenticated_user or requested_user != authenticated_user['username']:
            logger.warning("Rejected unauthenticated websocket connection")
            return False
        user = authenticated_user['username']
    sid = request.sid
    if sid not in threads:
        threads[sid] = socketio.start_background_task(background_thread, user, sid)
    emit('my_response', {'data': f'Connected as {user}', 'count': 0}, room=sid)

def background_thread(username, sid):
    """Example of how to send server generated events to clients."""
    count = 0
    while True:
        socketio.sleep(0.5)
        
        pos = []
        pos1=[]
        try:
            pos = trader.allpos[username]  # Assuming this is a dictionary or list
            for posse in pos:
                pos1.append(posse)
                if 'pos' in list(posse.keys()):
                    for poss1 in posse['pos']:
                        poss1['pnl']=poss1['cpnl']
                        pos1.append(poss1)
            pos=pos1




        except Exception as e:
            #print(f"Error fetching positions for {username}: {e}")
            pos = []

        # Ensure the data is JSON-serializable
        pos_json = []
        try:
            pos_json = json.dumps(pos)
        except Exception as e:
            print(f"Error serializing positions for {username}: {e}")
            pos_json = json.dumps([])

        socketio.emit('my_response',
                      {'data': f'{username}', 'position': pos,'userloggedin':username in trader.userloggedin},
                      room=sid)

@socketio.event
def my_event(message):
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response', {'data': message['data'], 'count': session['receive_count']})

@socketio.on('test_message')
def handle_message(data):
    print('received message: ' + str(data))
    emit('test_response', {'data': 'Test response sent'})

@socketio.on('broadcast_message')
def handle_broadcast(data):
    print('received: ' + str(data))
    emit('broadcast_response', {'data': 'Broadcast sent'}, broadcast=True)

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        # Parse the string data from tradingview into a python dict
        data = parse_webhook(request.get_data(as_text=True))
        # Check that the key is correct
        print(data)
        if   str(get_token()) in data['alert_name']:
            print(' [Alert Received] ')
            print(data['stocks'])
            if 'BUY' in (data['alert_name']).upper():
                print('I AM BUYING ALLL')
            print('POST Received:', data['stocks'])
            stocks=[stock.strip() for stock in data['stocks'].split(',')]

            #chatid=4128957645
            chatid=4128957645
            chatid=1001904195117
            #chatid=6423665415
            botcode="5386609048:AAF_BZIT-lTm_moW46Srs44aP2_I_xNKunw"
            botcode="7055737743:AAGHfZGkwD07HiaCKVwztrZxYNBNXwZu0pI"

            if not  trader.currentstocklist:
                stocks=[stock.strip() for stock in data['stocks'].split(',')][-25:]
                prices=[float(stock.strip()) for stock in data['trigger_prices'].split(',')][-25:]
                for i in range(0,len(stocks)):
                    if not stockstoday_collection.find_one({'date': str(datetime.datetime.now().date()), 'stocks': stocks[i]}):
                        stockstoday_collection.insert_one({'date':str(datetime.datetime.now().date()),'stocks':stocks[i],'time':str(datetime.datetime.now().time()),'prices':prices[i]})


                g=(str(tabulate({"Stocks": stocks,"Prices": prices}, headers="keys", tablefmt="simple",showindex=True, disable_numparse=True,stralign="left",numalign="right")))

                trader.newsignalstocklist = [stock.strip() for stock in data['stocks'].split(',')]#[num for num in data['stocks'] if num not in trader.currentstocklist]
                try:

                    #stock=[num for num in stock if num not in trader.currentstocklist]
                    #base_url = f"https://api.telegram.org/bot5386609048:AAF_BZIT-lTm_moW46Srs44aP2_I_xNKunw/sendMessage?chat_id=-{chatid}&text={g}"
                    base_url = f"https://api.telegram.org/bot{botcode}/sendMessage?chat_id=-{chatid}&text={g}"
                    print(requests.get(base_url))
                except:
                    print('No stock')
                    pass
            else:
                shock=[num for num in shock if num not in trader.currentstocklist]
                stocks=[stock.strip() for stock in data['stocks'].split(',')][-25:]
                k=[num for num in stocks if num not in trader.currentstocklist][-25:]
                prices=[float(stock.strip()) for stock in data['trigger_prices'].split(',')]
                for i in range(0,len(stocks)):
                    if not stockstoday_collection.find_one({'date': str(datetime.datetime.now().date()), 'stocks': stocks[i]}):
                        stockstoday_collection.insert_one({'date':str(datetime.datetime.now().date()),'stocks':stocks[i],'time':str(datetime.datetime.now().time()),'prices':prices[i]})
                g=(str(tabulate({"Stocks": stocks,"Prices": prices}, headers="keys", tablefmt="simple",showindex=True, disable_numparse=True,stralign="left",numalign="right")))
                try:
                    #stock=[num for num in stock if num not in trader.currentstocklist]
                    #base_url = f"https://api.telegram.org/bot5386609048:AAF_BZIT-lTm_moW46Srs44aP2_I_xNKunw/sendMessage?chat_id=-{chatid}&text={g}"
                    base_url = f"https://api.telegram.org/bot{botcode}/sendMessage?chat_id=-{chatid}&text={g}"
                    print(requests.get(base_url))
                except:
                    print('No stock')
                    pass
                trader.newsignalstocklist = [num for num in shock if num not in trader.currentstocklist]
            #send_order(data)
            return '', 200
        else:
            abort(403)
    else:
        abort(400)
@app.route('/stocks')
def stockslist():
    # Get date parameter from query string (if present)unique_dates
    if 'username' not in session:
        return redirect(url_for('login'))
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    if usersubscri:
        sample_data=list(stockstoday_collection.find().sort('_id', -1).limit(50))
        unique_dates = list(stockstoday_collection.distinct('date'))
        if len(unique_dates)==0:
            unique_dates=[str(datetime.datetime.now().date())]    
        if request.args.get('date'):
            selected_date = request.args.get('date')
        else:
            selected_date=unique_dates[-1]
        if selected_date:
            sample_data=list(stockstoday_collection.find({'date':selected_date}).sort('_id', -1).limit(50))
            filtered_data = [entry for entry in sample_data if entry['date'] == selected_date]
        else:
            filtered_data = sample_data
        return render_template('stockslist.html', data=filtered_data,dates=unique_dates,selected_date=selected_date)
    else:
        return redirect(url_for('index'))

@app.route('/prices')
def prices():
    return trader.prices

@app.route('/pos')
def pos():
    j = []
    for i in (trader.trades):
        j.append( i.__dict__)
    return str(j)

def historicalbacktestget_data(user,cd,nd):

    data = opositions_collection.find({"user":user,"status":"close","time": {"$gte": cd, "$lte": nd} }, {'_id': 0}).sort('_id', -1)#.skip(skip).limit(PER_PAGE)
    #print(data)
    datas=[]
    pnl=0
    for i in data:
        gmt_offset = datetime.timedelta(hours=5, minutes=30)
        i['time'] = str((datetime.datetime.utcfromtimestamp(i['time']) + gmt_offset).time())
        i['exittime'] = str((datetime.datetime.utcfromtimestamp(i['exittime']) + gmt_offset).time())
        datas.append(i)
        pnl=pnl+i['pnl']
    return datas,pnl#list(data)
@app.route('/historicalbacktest')
@login_required
def historicalbacktest():

    start_selected_date = request.args.get('date')

    if request.args.get('start_date'):
        start_selected_date = request.args.get('start_date')
    else:
        start_selected_date=str(datetime.datetime.now().date())



    # Check if selected_date is not None before parsing it
    if start_selected_date:
        try:
            date = datetime.datetime.strptime(start_selected_date, "%Y-%m-%d")
        except ValueError:
            return "Invalid date format. Please use YYYY-MM-DD."
    else:
        # If selected_date is None, use the current date
        date = datetime.datetime.now()

    end_selected_date = request.args.get('end_date')

    if request.args.get('end_date'):
        end_selected_date = request.args.get('end_date')
    else:
        end_selected_date=str(datetime.datetime.now().date())



    # Check if selected_date is not None before parsing it
    if end_selected_date:
        try:
            date1 = datetime.datetime.strptime(end_selected_date, "%Y-%m-%d")+ datetime.timedelta(days=1)
        except ValueError:
            return "Invalid date format. Please use YYYY-MM-DD."
    else:
        # If selected_date is None, use the current date
        date1 = datetime.datetime.now()+ datetime.timedelta(days=1)

    # Add one day
    currentday = date.timestamp()
    next_day = date + datetime.timedelta(days=1)
    # Convert to timestamp
    timestamp_next_day = next_day.timestamp()

    data ,pnl= historicalbacktestget_data(session['username'], int(date.timestamp()),  int(date1.timestamp()))
    return render_template('historicalbacktest.html', data=data, selected_start_date=start_selected_date, selected_end_date=end_selected_date,pnl=pnl)
def mainhistoricalbacktestget_data(user,cd,nd):

    data = opositions_collection.find({"user":user,"status":"close","time": {"$gte": cd, "$lte": nd} }, {'_id': 0}).sort('_id', -1)#.skip(skip).limit(PER_PAGE)
    #print(data)
    return list(data)
@app.route('/mainhistoricalbacktest')
def mainhistoricalbacktest():

    selected_date = request.args.get('date')

    # Check if selected_date is not None before parsing it
    if request.args.get('date'):
        selected_date = request.args.get('date')
    else:
        selected_date=str(datetime.datetime.now().date())
    if selected_date:
        try:
            date = datetime.datetime.strptime(selected_date, "%Y-%m-%d")
        except ValueError:
            return "Invalid date format. Please use YYYY-MM-DD."
    else:
        # If selected_date is None, use the current date
        date = datetime.datetime.now()
    currentday=date.timestamp()
    next_day = date + datetime.timedelta(days=1)
    timestamp_next_day = next_day.timestamp()
    data = mainhistoricalbacktestget_data("kinguniverse129",currentday,timestamp_next_day)
    return render_template('mainhistoricalbacktest.html', data=data,selected_date=selected_date)



def get_nse_symbols():
    dr = pd.read_csv('sec_bhavdata_full_23082023.csv')
    symbols = list(dr['SYMBOL'])
    return symbols

@app.route("/tank", methods=["POST", "GET"])
def tank():
    print((request.form))
    nse_symbols=[]
    nse_symbols = get_nse_symbols()
    index_symbols=['NIFTY CONSUMPTION',
                    'NIFTY FMCG',                 'INDIA VIX',                 'NIFTY METAL','NIFTY50 PR 1X INV',
                    'NIFTY50 DIV POINT',                'NIFTY100 LIQ 15',
                    'NIFTY50 TR 1X INV',            'NIFTY FINSRV25 50',
                    'NIFTY BANK',                 'NIFTY PSU BANK',
                    'NIFTY SERV SECTOR',                 'NIFTY GS 10YR CLN',
                    'NIFTY GS 11 15YR',                 'NIFTY50 PR 2X LEV',
                    'NIFTY 100',                 'NIFTY SMLCAP 250',
                    'NIFTY SMLCAP 100',                 'NIFTY 500',
                    'NIFTY MID LIQ 15',                 'NIFTY IND DIGITAL',
                    'NIFTY DIV OPPS 50',                 'NIFTY MEDIA',
                    'NIFTY REALTY',                 'NIFTY HEALTHCARE',
                    'NIFTY NEXT 50',                 'NIFTY IT',
                    'NIFTY PSE',                 'NIFTY GS COMPSITE',
                    'NIFTY COMMODITIES',                 'NIFTY100 LOWVOL30',
                    'NIFTY GS 10YR',                 'NIFTY PVT BANK',
                    'NIFTY100 ESG',                 'NIFTY MICROCAP250',
                    'NIFTY M150 QLTY50',                 'NIFTY SMLCAP 50',                 'NIFTY CPSE',                 'NIFTY50 EQL WGT',                 'NIFTY INDIA MFG',                 'NIFTY 50',                 'NIFTY100 EQL WGT',                 'NIFTY200 QUALTY30',                 'NIFTY200MOMENTM30',                 'NIFTY100ESGSECLDR',                 'NIFTY50 VALUE 20',                 'NIFTY100 QUALTY30',                 'NIFTY MIDSML 400',                 'NIFTY ALPHALOWVOL',                 'NIFTY CONSR DURBL',                 'NIFTY GS 8 13YR',                 'NIFTY MIDCAP 50',                 'NIFTY OIL AND GAS',                 'NIFTY MNC',                 'NIFTY MIDCAP 100',                 'NIFTY50 TR 2X LEV',                 'NIFTY GS 15YRPLUS',                 'NIFTY 200',                 'NIFTY PHARMA',                 'NIFTY LARGEMID250',                 'NIFTY MIDCAP 150',                 'NIFTY GS 4 8YR',                 'NIFTY AUTO',                 'NIFTY GROWSECT 15',                 'NIFTY FIN SERVICE',                 'NIFTY INFRA',                 'NIFTY MID SELECT',                 'NIFTY TOTAL MKT',                 'NIFTY500 MULTICAP',                 'NIFTY ENERGY',                 'NIFTY ALPHA 50'
                    ]

    return render_template('test.html',nse_symbols=nse_symbols,index_symbols=index_symbols, action_url=url_for('tank'))



@app.route('/users', methods=['GET'])
@admin_required
def get_users():
    users = list(users_collection.find())
    #users = list(users.find())
    for i in range(0,len(users)):
        users[i]['_id']=str(users[i]['_id'])
    return render_template('users.html', users=users)

# Update operation for users
@app.route('/update_user/<user_id>', methods=['GET', 'POST'])
@admin_required
def update_user(user_id):
    if request.method == 'GET':
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        return render_template('update_user.html', user=user)

    if request.method == 'POST':
        data = {
            "username": request.form['username'],
            "email": request.form['email'],
            "mobile": request.form['mobile']
        }
        users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": data})
        return redirect(url_for('get_users'))

# Delete operation for users
@app.route('/delete_user/<user_id>', methods=['GET'])
@admin_required
def delete_user(user_id):
    users_collection.delete_one({"_id": ObjectId(user_id)})
    return redirect(url_for('get_users'))
@app.route('/apis', methods=['GET'])
@admin_required
def get_apis():
    apis = list(apikeys_collection.find())
    for i in range(0,len(apis)):
        apis[i]['_id']=str(apis[i]['_id'])
    return render_template('apis.html', apis=apis)
@app.route('/strategys', methods=['GET'])
@admin_required
def get_strategy():
    strategy= list(strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}]}))
    for i in range(0,len(strategy)):
        strategy[i]['_id']=str(strategy[i]['_id'])
    return render_template('strategyslist.html', strategy=strategy)

# Update operation for APIs
@app.route('/update_api/<api_id>', methods=['GET', 'POST'])
@admin_required
def update_api(api_id):
    if request.method == 'GET':
        api = apikeys_collection.find_one({"_id": ObjectId(api_id)})
        return render_template('update_api.html', api=api)

    if request.method == 'POST':
        data = {
            "apikey": request.form['apikey'],
            "apisecret": request.form['apisecret'],
            "user": request.form['user']
        }
        if 'auth_code' in request.form:
            data["auth_code"] = request.form['auth_code']
        apikeys_collection.update_one({"_id": ObjectId(api_id)}, {"$set": data})
        return redirect(url_for('get_apis'))

# Delete operation for APIs
@app.route('/delete_api/<api_id>', methods=['GET'])
@admin_required
def delete_api(api_id):
    apikeys_collection.delete_one({"_id": ObjectId(api_id)})
    return redirect(url_for('get_apis'))

@app.route('/admin')
@admin_required
def admin():

    adminco=list(admincontrol_collection.find())
    users=list(users_collection.find())
    strategyco=list(strategyinput_collection.find())
    return render_template('admin.html',controls=adminco,users=users,strategyco=strategyco)


def search_word_in_variable(word):
    client = pymongo.MongoClient('mongodb://localhost:27017', maxPoolSize=5)
    db = client["demo"]
    collections = [
        db["users"],
        db["apis"],
        db['subscriptionperiod']
    ]

    results = {}

    for collection in collections:
        cursor = collection.find()
        results[collection.name] = []

        for document in cursor:
            # Convert ObjectId to a string
            document['_id'] = str(document['_id'])
            for key, value in document.items():
                if isinstance(value, str) and word in value:
                    results[collection.name].append(document)
 
    return results

@app.route('/subscription', methods=['GET', 'POST'])
@admin_required
def get_subscriptions():

    subscriptions = list(subscriptionperiod_collection.find())
    for i in range(0,len(subscriptions)):
        subscriptions[i]['_id']=str(subscriptions[i]['_id'])
    return render_template('subscriptions.html', subscriptions=subscriptions)

# Create operation
@app.route('/create_subscription', methods=['POST'])
@admin_required
def create_subscription():
    user = request.form['user']
    start = request.form['start']
    end = request.form['end']
    subtype = request.form['subtype']

    subscription_data = {
        'user': user,
        'start': start,
        'end': end,
        'subtype': subtype
    }

    subscriptionperiod_collection.insert_one(subscription_data)
    return redirect(url_for('get_subscriptions'))
    

@app.route('/update_subscription/<subscription_id>', methods=['GET', 'POST'])
@admin_required
def update_subscription(subscription_id):

    if request.method == 'GET':
        subscription = subscriptionperiod_collection.find_one({"_id": ObjectId(subscription_id)})
        return render_template('update_subscription.html', subscription=subscription)

    if request.method == 'POST':
        data = {
            "start": request.form['start'],
            "end": request.form['end'],
            "subtype": request.form['subtype']
        }
        subscriptionperiod_collection.update_one({"_id": ObjectId(subscription_id)}, {"$set": data})
        return redirect(url_for('get_subscriptions'))

# Delete operation
@app.route('/delete_subscription/<subscription_id>', methods=['GET'])
@admin_required
def delete_subscription(subscription_id):
    subscriptionperiod_collection.delete_one({'_id': ObjectId(subscription_id)})
    return redirect(url_for('get_subscriptions'))
@app.route('/')
@login_required
def index():
    print("🔵 Route '/' accessed")
    print(f"✅ Username in session: {session['username']}")

    apikey = apikeys_collection.find_one({'user': session['username']})
    userdata = users_collection.find_one({'username': session['username']})
    print("🟢 Retrieved userdata and apikey")

    adminuser = False
    if 'admin' in list(userdata.keys()):
        adminuser = userdata['admin']
        print(f"🔐 Admin user: {adminuser}")

    equity = False
    if 'equity' in list(userdata.keys()):
        equity = True
        print("💼 Equity access: True")

    if not apikey:
        print("🛑 No API key found. Redirecting to add_apikey_form.")
        return redirect(url_for('edit_apikey_form'))

    orders = list(orders_collection.find({'user': session['username']}))
    print(f"📦 Orders retrieved: {len(orders)}")

    ords = []
    closeords = []
    for order in orders:
        if 'exittime' not in list(order.keys()):
            order['exittime'] = int(order['time'])
            orders_collection.update_one({'entry_id': order['entry_id']}, {'$set': order})
            print(f"📝 Updated missing exittime in order {order['entry_id']}")

        order["entry_id"] = datetime.datetime.fromtimestamp(int(order["entry_id"])).strftime('%Y-%m-%d %H:%M:%S')
        if order['status'] == 'opened':
            ords.append(order)
        else:
            closeords.append(order)

    print(f"📘 Open orders: {len(ords)}, 📕 Closed orders: {len(closeords)}")

    j = []
    k = []
    positions = list(positions_collection.find({'user': session['username']}))
    print(f"📊 Positions retrieved: {len(positions)}")

    strategy = list(strategy_collection.find({'user': session['username'], 'status': {'$in': ['opened', 'paused']}}))
    opositions = list(opositions_collection.find({'user': session['username'], 'decision': 'intrade', 'status': {'$in': ['open']}}))
    print(f"🧠 Strategies: {len(strategy)}, 🎯 Open Oppositions: {len(opositions)}")

    userlog = session['username'] in trader.userloggedin
    print(f"👤 User logged in: {userlog}")

    for i in positions:
        if 'exittime' not in list(i.keys()):
            i['exittime'] = int(i['time'])
            positions_collection.update_one({'entry_id': i['entry_id']}, {'$set': i})
            print(f"📝 Updated missing exittime in position {i['entry_id']}")

        i["time"] = datetime.datetime.fromtimestamp(int(i["time"])).strftime('%Y-%m-%d %H:%M:%S')
        if i['status'] == 'open':
            j.append(i)
        else:
            i["exittime"] = datetime.datetime.fromtimestamp(int(i["exittime"])).strftime('%Y-%m-%d %H:%M:%S')
            k.append(i)

    print(f"📘 Open positions: {len(j)}, 📕 Closed positions: {len(k)}")

    sub = subscriptionperiod_collection.find_one({'user': session['username']})
    if not sub:
        today_date = datetime.datetime.now().date()
        future_date = today_date + datetime.timedelta(days=freeday)
        ftoday = today_date.strftime('%Y-%m-%d')
        ffuture = future_date.strftime('%Y-%m-%d')
        ser = {'user': session['username'], 'start': ftoday, 'end': ffuture, 'subtype': "free"}
        subscriptionperiod_collection.insert_one(ser)
        print("🎫 Free subscription created")

    sub = subscriptionperiod_collection.find_one({'user': session['username']})
    usersubscri = datetime.datetime.strptime(sub['end'], '%Y-%m-%d') + datetime.timedelta(days=1) >= datetime.datetime.now()
    print(f"📅 Subscription valid: {usersubscri}, Expires on: {sub['end']}")

    print("✅ Rendering template")
    return render_template(
        'crud_order_table.html',
        orders=ords,
        closedorders=closeords,
        positions=j,
        closedpositions=k,
        user=session['username'],
        strategy=strategy,
        opositions=opositions,
        fixed=False,
        userlog=userlog,
        usersubscri=usersubscri,
        userexpiry=sub['end'],
        adminuser=adminuser,
        equity=equity
    )

@app.route('/api_index', methods=['POST'])
@api_token_required
def api_index(user):
    """API endpoint to get user dashboard data"""
    logger.info(f"API index accessed by user: {user['username']}")
    
    username = user['username']
    def ensure_frontend_user_verified(username):
        if username in trader.userloggedin:
            return True
        broker_info = broker_collection.find_one({'user': username}) or {}
        selected_broker = broker_info.get('selectedbroker')
        if selected_broker != 'aliceblue':
            return False
        api_info = apikeys_collection.find_one({'user': username, 'broker': 'aliceblue'})
        if not api_info:
            return False
        try:
            user_id, instance, session_data = trader._login_aliceblue(dict(api_info))
            trader._update_user_login_state(user_id, instance, session_data, 'alice', 'sessionID')
        except Exception as exc:
            logger.warning(f"AliceBlue verification failed for {username}: {exc}")
        return username in trader.userloggedin

    apikey = apikeys_collection.find_one({'user': username}, projection={'_id': False})
    if not apikey:
        logger.warning(f"API key not found for user: {username}")
        return generate_response(message='API key not found', success=False, status_code=404)
    
    # Get user permissions
    adminuser = user.get('admin', False)
    equity = 'equity' in user

    # Define available brokers
    brokers = {
        'AliceBlue': 'https://ant.aliceblueonline.com',
        'Fyers': 'https://login.fyers.in',
        'Shoonya': 'https://trade.shoonya.com',
        'Zerodha': 'https://kite.zerodha.com',
        'AngelOne': 'https://smartapi.angelbroking.com',
        'Dhan': 'https://web.dhan.co',
        'MOFS': 'https://motilaloswal.com/login',
        'SMC': 'https://www.smctrade.com/login.aspx'
    }
    
    # Define available strategies based on user permissions
    if adminuser:
        addstrategies = {
            'EQUITY OPTIONS FUTURE': 'add_ssequityfno_eq_form',
            'Chartink': 'add_ssequity_eq_form',
            'SSALGO SSAUTO': 'add_rf_form',
            'Equity SSALGO': 'add_eqssalgo_form',
            'New 143': 'add_sstrike_form',
            '143 Options': 'add_ema_form',
            'SSALGOHF Options': 'add_ssalgo_form',
            'Index FUTURE SSALGO': 'add_ssalgo_fut_form',
            'Index FUTURE 143': 'add_ema_fut_form',
            'Hedge Order': 'add_fractalnubiatimehedgeorder_form',
        }
    else:
        addstrategies = {
            'Equity SSALGO': 'add_eqssalgo_form',
            '143 Options': 'add_ema_form',
            'Index FUTURE 143': 'add_ema_fut_form',
            'Hedge Order': 'add_fractalnubiatimehedgeorder_form',
        }

    # Get orders and process them
    orders = list(orders_collection.find(
        {'user': username}, 
        projection={'_id': False}
    ))
    
    ords = []
    closeords = []
    for order in orders:
        if 'exittime' not in order:
            order['exittime'] = int(order['time'])
            orders_collection.update_one({'entry_id': order['entry_id']}, {'$set': {'exittime': order['exittime']}})
        
        order['entry_id'] = datetime.datetime.fromtimestamp(int(order['entry_id'])).strftime('%Y-%m-%d %H:%M:%S')
        if order['status'] == 'opened':
            ords.append(order)
        else:
            closeords.append(order)

    # Get positions and process them
    positions = list(positions_collection.find(
        {'user': username}, 
        projection={'_id': False}
    ))
    
    open_positions = []
    closed_positions = []
    for position in positions:
        if 'exittime' not in position:
            position['exittime'] = int(position['time'])
            positions_collection.update_one({'entry_id': position['entry_id']}, {'$set': {'exittime': position['exittime']}})

        position['time'] = datetime.datetime.fromtimestamp(int(position['time'])).strftime('%Y-%m-%d %H:%M:%S')
        if position['status'] == 'open':
            open_positions.append(position)
        else:
            position['exittime'] = datetime.datetime.fromtimestamp(int(position['exittime'])).strftime('%Y-%m-%d %H:%M:%S')
            closed_positions.append(position)

    # Get strategies and open positions
    strategy = list(strategy_collection.find(
        {'user': username, 'status': {'$in': ['opened', 'paused']}}, 
        projection={'_id': False}
    ))
    
    opositions = list(opositions_collection.find(
        {'user': username, 'decision': 'intrade', 'status': {'$in': ['open']}}, 
        projection={'_id': False}
    ))

    # Get subscription information
    sub = subscriptionperiod_collection.find_one({'user': username}, projection={'_id': False})
    if not sub:
        today_date = datetime.datetime.now().date()
        future_date = today_date + datetime.timedelta(days=freeday)
        ser = {
            'user': username, 
            'start': today_date.strftime('%Y-%m-%d'), 
            'end': future_date.strftime('%Y-%m-%d'), 
            'subtype': "free"
        }
        subscriptionperiod_collection.insert_one(ser)
        sub = ser
    
    usersubscri = datetime.datetime.strptime(sub['end'], '%Y-%m-%d') + datetime.timedelta(days=1) >= datetime.datetime.now()

    # Prepare response data
    data = {
        'orders': ords,
        'closed_orders': closeords,
        'positions': open_positions,
        'closed_positions': closed_positions,
        'user': username,
        'allstrategies': addstrategies,
        'strategy': strategy,
        'opositions': opositions,
        'fixed': False,
        'userlog': ensure_frontend_user_verified(username),
        'user_subscription': usersubscri,
        'user_expiry': sub['end'],
        'adminuser': adminuser,
        'equity': equity,
        'brokers': brokers
    }

    return generate_response(data=data, message="Index data retrieved successfully")
@app.route('/getrecords', methods=['POST'])
def get_records():
    # Get the search term from the request data
    print(request.form)
    search_term = request.form.get('searchTerm', '')
    print(search_term)

    # Call the search function with the provided search term
    search_results = search_word_in_variable(search_term)

    # Convert results to JSON with ObjectId serialization
    json_results = json_util.dumps(search_results)

    # Return the results as JSON
    print(json_results)
    return json_results


@app.route("/get_order_entry_ids")
def get_order_entry_ids():
    return jsonify({"order_entry_ids": list(trader.positions.keys())})

@app.route("/get_all_position")
@login_required
def get_all_position():
    g=list(opositions_collection.find({'user': session['username'], 'status': {'$in': ['open']}}))
    l=[]
    for i in g:
        del i['_id']
        l.append(i)
    return jsonify(g)

@app.route("/get_position_entry_ids")
@login_required
def get_position_entry_ids():

    g=list(opositions_collection.find({'user': session['username'], 'status': {'$in': ['open']}}))
    l=[]
    for i in g:
        l.append(i['entry_id'])
    return jsonify({"position_entry_ids": l})

@app.route("/get_pnl_current_price/<string:entry_id>")
def get_pnl_current_price(entry_id):
    if entry_id in list(trader.positions.keys()):
        if 'XMLHttpRequest' in request.headers.get('X-Requested-With'):
            pnl_value=trader.positions[entry_id]['pnl']
            current_price_value=trader.prices[trader.positions[entry_id]['symbol']]
            option_price_value=trader.positions[entry_id]['optionexit']
            return jsonify({"pnl": pnl_value, "current_price": current_price_value,"option_price":option_price_value})
    elif entry_id in list(trader.closedpositions.keys()):
        pnl_value=trader.closedpositions[entry_id]['pnl']
        current_price_value=trader.prices[trader.closedpositions[entry_id]['symbol']]
        option_price_value=trader.closedpositions[entry_id]['optionexit']
        return jsonify({"pnl": pnl_value, "current_price": current_price_value,"option_price":option_price_value})
    else:
        return jsonify({"pnl": 0, "current_price": 0,"option_price":0})
        

@app.route("/get_position_pnl_current_price/<int:entry_id>")
def get_position_pnl_current_price(entry_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    j=opositions_collection.find_one({'time':entry_id})
    #print('halwa')
    #print(j)
    if j:
        pnl_value=j['pnl']
        current_price_value=j['current_price']
        option_price_value=j['optionexit']
        #print(j)
        return jsonify({"pnl": pnl_value, "current_price": current_price_value,"option_price":option_price_value})
        #return jsonify({"pnl": 3333, "current_price":78888 ,"option_price":44444})
    else:
        return jsonify({"pnl": 0, "current_price": 0,"option_price":0})
   










        
@app.route('/edit_position_form/<string:position_time>')
@login_required
def edit_position_form(position_time):
    order = positions_collection.find_one({'entry_id': position_time})
    return render_template('add_edit_position1.html', order=order, action_url=url_for('edit_position', position_time=position_time))


@app.route('/edit_position/<string:position_time>', methods=['POST'])
def edit_position(position_time):
    k = (dict(request.form))
    del k['_id']
    pos={'user':str(k['user']),'time':int(k['time']),'entry_id':int(k['entry_id']),'symbol':k['symbol'],'entry_price':float(k['entry_price']),'side':k['side'],'tp_1':float(k['tp_1']),
                            'tp_2':float(k['tp_2']),'trail':k['trail'],'comparator_type':k['comparator_type'],'track':k['track'], 'tsl':float(k['tsl']),'sl':float(k['sl']),'status':k['status'],'pnl':float(k['pnl']),'lot':float(k['lot']),'initial_lot':float(k['initial_lot']),
                            'optionentry':float(k['optionentry']),'optionexit':float(k['optionexit']),'optionlot':int(k['optionlot']),
                            'optionname':str(k['optionname']), 'pnlhalf':k['pnlhalf'],"decision":k['decision']}
    trader.positions[int(k['entry_id'])]=pos
    positions_collection.update_one({'entry_id': int(k['entry_id'])}, {'$set': pos})
    return redirect(url_for('index'))
@app.route('/delete_oposition/<string:position_time>')
@login_required
def delete_oposition(position_time):
    opositions_collection.update_one({'entry_id': int(position_time),'user':session['username'],'status':"open"}, {'$set':{'decision':'exitit'}})
    return redirect(url_for('index'))
@app.route('/delete_position/<string:position_time>')
@login_required
def delete_position(position_time):
    positions_collection.update_one(
        {'entry_id': position_time, 'user': session['username']}, 
        {'$set': {'decision': 'exitit'}}
    )
    return redirect(url_for('index'))
@app.route('/api_delete_oposition', methods=['POST'])
@api_token_required
def api_delete_oposition(user):
    try:
        position_time = request.form.get('position_time')
        if not position_time:
            logger.warning("Position time missing in api_delete_oposition request")
            return generate_response(message='Position time is required', success=False, status_code=400)
        
        # User is already validated by the decorator
        username = user['username']
        logger.info(f"Processing delete position request for user {username}, position {position_time}")
        
        # Perform the update to mark the position as 'exitit'
        result = opositions_collection.update_one(
            {'entry_id': int(position_time), 'user': username, 'status': 'open'}, 
            {'$set': {'decision': 'exitit'}}
        )
        
        logger.info(f"Update result: matched={result.matched_count}, modified={result.modified_count}")
        
        # Check if the position was updated
        if result.matched_count == 0:
            logger.warning(f"Position {position_time} not found for user {username}")
            return generate_response(message='Position not found', success=False, status_code=404)
        
        return generate_response(message='Position updated successfully')
    except Exception as e:
        logger.error(f"Error in api_delete_oposition: {str(e)}")
        return generate_response(message='An error occurred while processing the request', 
                               success=False, status_code=500)
@app.route('/add_order_form')
@login_required
def add_order_form():
    return render_template('add_edit_order1.html', action_url=url_for('add_order'))

@app.route('/add_ssalgo_form')
@login_required
def add_ssalgo_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssalgo_form.html', action_url=url_for('add_ssalgo',usersubscri=usersubscri))


@app.route('/add_eqssalgo_form')
@login_required
def add_eqssalgo_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    print(trader.stocks)
    return render_template('add_eqssalgo_form.html',symbols=list(trader.stocks), action_url=url_for('add_eqssalgo',usersubscri=usersubscri))

@app.route('/add_ssalgo_fut_form')
@login_required
def add_ssalgo_fut_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssalgo_fut_form.html', action_url=url_for('add_fut_ssalgo',usersubscri=usersubscri))


@app.route('/add_ssequity_fut_form')
@login_required
def add_ssequity_fut_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssequity_fut_form.html', action_url=url_for('add_fut_ssequity',usersubscri=usersubscri))

@app.route('/add_rf_form')
@login_required
def add_rf_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_rf_form.html', action_url=url_for('add_rf',usersubscri=usersubscri))
@app.route('/add_rf', methods=['POST'])
@login_required
def add_rf():
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=RF_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_ssequity_form')
@login_required
def add_ssequity_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssequity_form.html', action_url=url_for('add_ssequity',usersubscri=usersubscri))


@app.route('/add_ssequity_eq_form')
@login_required
def add_ssequity_eq_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssequity_eq_form.html', action_url=url_for('add_eq_ssequity',usersubscri=usersubscri))

@app.route('/add_ssequityfno_eq_form')
@login_required
def add_ssequityfno_eq_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssequityfno_eq_form.html', action_url=url_for('add_eq_ssequityfno',usersubscri=usersubscri))







@app.route('/add_ssauto_form')
@login_required
def add_ssauto_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssauto_form.html', action_url=url_for('add_ssauto',usersubscri=usersubscri))

@app.route('/add_ssauto_fut_form')
@login_required
def add_ssauto_fut_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ssauto_fut_form.html', action_url=url_for('add_fut_ssauto',usersubscri=usersubscri))


@app.route('/add_sstrike_form')
@login_required
def add_sstrike_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_sstrike_form.html', action_url=url_for('add_sstrike',usersubscri=usersubscri))

@app.route('/add_ema_form')
@login_required
def add_ema_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ema_form.html', action_url=url_for('add_ema',usersubscri=usersubscri))


@app.route('/add_pema_form')
@login_required
def add_pema_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_pema_form.html', action_url=url_for('add_pema',usersubscri=usersubscri))



@app.route('/add_ema1_form')
@login_required
def add_ema1_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ema1_form.html', action_url=url_for('add_ema',usersubscri=usersubscri))

@app.route('/add_ema_fut_form')
@login_required
def add_ema_fut_form():
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    usersubscri=datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+datetime.timedelta(days=1) >=datetime.datetime.now()
    return render_template('add_ema_fut_form.html', action_url=url_for('add_fut_ema',usersubscri=usersubscri))

@app.route('/edit_admin_strategy_form/<string:order_time>')
@admin_required
def edit_admin_strategy_form(order_time):
   
    userdata = users_collection.find_one({'username': session['username']})


    order = strategy_collection.find_one({'botcode': str(order_time)})
    url='edit_admin_'+order['strategy'].lower()
    algo=order['strategy'].lower()
    if not 'equity' in algo:
        if 'onspot' in list(order.keys()):
            page='add_{}_fut_form.html'.format(algo)
        else:
            page='add_{}_form.html'.format(algo)
    else:
        page='add_{}_eq_form.html'.format(algo)
    return render_template(page, order=order, action_url=url_for(url, order_time=order_time))

@app.route('/edit_strategy_form/<string:order_time>')
@login_required
def edit_strategy_form(order_time):
    print(order_time)
    order = strategy_collection.find_one({'botcode': str(order_time),'user':session['username']})
    print(order)
    url='edit_'+order['strategy'].lower()
    algo=order['strategy'].lower()
    if 'ssequity' in algo:
        page='add_{}_eq_form.html'.format(algo)
    else:
        if 'onspot' in list(order.keys()):
            page='add_{}_fut_form.html'.format(algo)
        elif 'FRACTALNUBIATIMEHEDGEORDER'.lower() in algo:
            page="add_edit_fractalnubiatimehedgeorder.html"
        else:
            page='add_{}_form.html'.format(algo)
    return render_template(page, order=order,symbols=list(trader.stocks), action_url=url_for(url, order_time=order_time))



@app.route('/api_edit_strategy_form/<string:order_time>',methods=['POST'])
def api_edit_strategy_form(order_time):

    print(order_time)
    order = strategy_collection.find_one({'botcode': str(order_time),'user':request.form['token']})
    print(order)
    url='edit_'+order['strategy'].lower()

    strategycheck=users_collection.find_one({"username": request.form['token']})
    strategycheck1=10
    if 'StrategyLimit' in strategycheck:
        strategycheck1=strategycheck['StrategyLimit']
    algo=order['strategy'].lower()
    if 'ssequity' in algo:
        page='add_{}_eq_form.html'.format(algo)
    else:
        if 'onspot' in list(order.keys()):
            page='add_{}_fut_form.html'.format(algo)
        else:
            page='add_{}_form.html'.format(algo)
    if order:
        if 'EQSSALGO' ==order['strategy']:
            del order['symbol']
    #return render_template(page, order=order, action_url=url_for(url, order_time=order_time))
    #print(page)
    #print(mform[page])
    strategy = list(strategy_collection.find({'user': request.form['token'], 'status': {'$in': ['opened', 'paused']}}, projection={'_id': False}))
    renamed_count = 0  # Clearer variable name

    #print('***************************$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$UJJJJJJJJJJJJJJJJJJJJJJJJJ')

    # Iterate over strategies
    for i in strategy:
        if 'symbol' in i:  # Check if 'symbol' exists
            if isinstance(i['symbol'], str):
                renamed_count += 1
            elif isinstance(i['symbol'], list):
                renamed_count += len(i['symbol'])
    readonly=['botname','_id','symbol',	'time',	'Expiry',	'BSmode','lot',	'initiallot',	'MultiFactor',	'candle1',	'candle2',]
    if order['status']=='paused':
        readonly=['botname','_id','symbol',	'time',	'Expiry','BSmode',	'MultiFactor',	'candle1',	'candle2']

    del order['_id']
    return {'success':True,'message':"Successfully Fetched Strategy Form",'data':{'page':mform[page],"StrategyLimit":strategycheck1,'StrategyRemaining':int(strategycheck1)-int(renamed_count),'info':order,'action_url':f'/api_edit_{algo}',
    'readonly':readonly}}

@app.route('/api_add_strategy_form/',methods=['POST'])
@api_token_required
def api_add_strategy_form(user):  # Add user parameter here
    strategycheck=users_collection.find_one({"username": request.form['token']})
    strategycheck1=10
    if 'StrategyLimit' in strategycheck:
        strategycheck1=strategycheck['StrategyLimit']
    strategy = list(strategy_collection.find({'user': request.form['token'], 'status': {'$in': ['opened', 'paused']}}, projection={'_id': False}))  # Use request.form['token'] instead of str(token)
    renamed_count = 0  # Clearer variable name

    page=request.form['strategy'].lower()+'.html'
    # Iterate over strategies
    for i in strategy:
        if 'symbol' in i:  # Check if 'symbol' exists
            if isinstance(i['symbol'], str):
                renamed_count += 1
            elif isinstance(i['symbol'], list):
                renamed_count += len(i['symbol'])
            print('time passuhjfudsufhdshfsd')
            print(renamed_count)
    
    k='/'+'api_'+request.form['strategy'].lower().replace('_form','')
    if 'fut' in page:
        k='/'+'api_'+request.form['strategy'].lower().replace('_form','')

    return {'success':True,'data':{'page':mform[page],'StrategyLimit':strategycheck1,'StrategyRemaining':int(strategycheck1)-int(renamed_count),'action_url':k}}


@app.route('/edit_strategyinput_form/<string:order_time>')
@admin_required
def edit_strategyinput_form(order_time):
    order = strategyinput_collection.find_one({'strategy': order_time})
    return render_template('add_strategyinput_form.html', order=order, action_url=url_for('edit_strategyinput', order_time=order_time))
@app.route('/edit_admin_rf/<string:order_time>', methods=['POST'])
@admin_required
def edit_admin_rf(order_time):
    k = (dict(request.form))
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())
    j = RF_mode(k)
    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    return redirect(url_for('get_strategy'))


@app.route('/edit_order_form/<string:order_time>')
@login_required
def edit_order_form(order_time):
    order = orders_collection.find_one({'time': order_time,'user':session['username']})
    return render_template('add_edit_order1.html', order=order, action_url=url_for('edit_order', order_time=order_time))

@app.route('/add_ssalgo', methods=['POST'])
@login_required
def add_ssalgo():
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSALGO_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))
@app.route('/add_eqssalgo', methods=['POST'])
@login_required
def add_eqssalgo():
    if request.form:
        k=dict(request.form)
        
        symbols = request.form.getlist('symbol[]')
        k['symbol']=symbols
        k['user']=session['username']
        print(k)
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=EQSSALGO_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))
@app.route('/api_add_fractalnubiatimehedgeorder', methods=['POST'])
#@api_token_required
def api_add_fractalnubiatimehedgeorder():
    print("Endpoint /api_add_fractalnubiatimehedgeorder called")
    j = dict(request.form)
    token = j['token']
    print(' iam received')
    print(j)
    if not token:
        print("Error: Authentication token is missing")
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    user = users_collection.find_one({'username': token})
    print("User lookup result:", user)

    if not user:
        print("Error: Invalid token or user not found")
        return jsonify({'success': False, 'error': 'Invalid token or user not found'}), 404

    print("Token validated. User found:", user['username'])

    if request.form:
        print('Starting FRACTALNUBIATIMEHEDGEORDER processing')
        k = dict(request.form)

        try:
            options = request.form.getlist('ooption')
            strikes = request.form.getlist('ostrike')
            sides = request.form.getlist('oside')
            expiries = request.form.getlist('oexpiry')
            lots = request.form.getlist('olot')

            # Combine data into a list of dictionaries
            data = [
                {'option': option, 'strike': strike, 'side': side, 'expiry': expiry, 'lot': lot}
                for option, strike, side, expiry, lot in zip(options, strikes, sides, expiries, lots)
            ]

            # Debugging: Print the data
            print(data)

            k['exittime'] = int(datetime.datetime.now().timestamp())
            k['user'] = user['username']
            k['legs'] = data
            
            print(user)
            k['botcode'] = create_botcode(k['user'], k['botname'])
            print(k['botcode'])
            
            j = FRACTALNUBIATIMEHEDGEORDER_mode(k)
            print(j)
            
            # Insert only once
            strategy_collection.insert_one(j.__dict__)
            print("Strategy added to the database successfully")

            return jsonify({'success': True, 'message': 'FRACTALNUBIATIMEHEDGEORDER strategy added successfully'})

        except Exception as e:
            print("Error processing FRACTALNUBIATIMEHEDGEORDER:", str(e))
            return jsonify({'success': False, 'error': 'An error occurred while processing the request'}), 500

    print("Error: No form data provided")
    return jsonify({'success': False, 'error': 'No form data provided'}), 400

def _fractal_reset_update(botcode, user=None, set_fields=None):
    query = {'botcode': botcode}
    if user:
        query['user'] = user
    strategy = strategy_collection.find_one(query)
    update = {'$set': dict(set_fields or {})}
    if strategy and strategy.get('strategy') == 'FRACTALNUBIATIMEHEDGEORDER':
        open_query = {'botcode': botcode, 'status': 'open'}
        if user:
            open_query['user'] = user
        has_open_position = opositions_collection.count_documents(open_query, limit=1) > 0
        if not has_open_position:
            update['$unset'] = {
                'fractal_fire_state': '',
                'fractal_fire_time': '',
                'fractal_fire_reason': '',
            }
            update['$set']['position'] = 'out'
    return update

def _mark_strategy_positions_exitit(botcode, user=None):
    query = {'botcode': botcode, 'status': 'open'}
    if user:
        query['user'] = user
    opositions_collection.update_many(
        query,
        {'$set': {'decision': 'exitit'}}
    )

@app.route('/api_add_eqssalgo', methods=['POST'])
def api_add_eqssalgo():
    print("Endpoint /api_add_eqssalgo called")
    j=dict(request.form)
    token = j['token']#request.form.get('token')
    #print("Received form data:", dict(request.form))
    print(' iam received')
    print(j)
    if not token:
        print("Error: Authentication token is missing")
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    user = users_collection.find_one({'username': token})
    print("User lookup result:", user)

    if not user:
        print("Error: Invalid token or user not found")
        return jsonify({'success': False, 'error': 'Invalid token or user not found'}), 404

    print("Token validated. User found:", user['username'])

    if request.form:
        print('Starting EQSSALGO processing')
        k = dict(request.form)
        #print('Parsed form data for EQSSALGO:', k)

        try:
            symbols = request.form.get('symbol[]').split(',')
            k['symbol'] = symbols
            k['symbol[]']=symbols
            k['user'] = user['username']

            k['botcode'] = create_botcode(k['user'], k['botname'])

            print("Generated botcode:", k['botcode'])

            j = EQSSALGO_mode(k)
            print("EQSSALGO_mode object created:", j.__dict__)
            j=dict(j.__dict__)
            j['symbol[]']=symbols
            #del j['symbol']
            strategy_collection.insert_one(j)
            print("Strategy added to the database successfully")

            return jsonify({'success': True, 'message': 'EQSSALGO strategy added successfully'})

        except Exception as e:
            print("Error processing EQSSALGO:", str(e))
            return jsonify({'success': False, 'error': 'An error occurred while processing the request'}), 500

    print("Error: No form data provided")
    return jsonify({'success': False, 'error': 'No form data provided'}), 400

@app.route('/add_fut_ssequity', methods=['POST'])
def add_fut_ssequity():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSEQUITY_fut_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_ssequity', methods=['POST'])
def add_ssequity():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSEQUITY_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_eq_ssequity', methods=['POST'])
def add_eq_ssequity():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSEQUITY_EQ_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))
@app.route('/add_eq_ssequityfno', methods=['POST'])
def add_eq_ssequityfno():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSEQUITYFNO_EQ_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_sstrike', methods=['POST'])
def add_sstrike():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSTRIKE_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_fut_ssalgo', methods=['POST'])
def add_fut_ssalgo():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSALGO_fut_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_ssauto', methods=['POST'])
def add_ssauto():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSAUTO_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))


@app.route('/add_fut_ssauto', methods=['POST'])
def add_fut_ssauto():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=SSAUTO_fut_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))











@app.route('/add_ema', methods=['POST'])
def add_ema():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=EMA_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_pema', methods=['POST'])
def add_pema():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=PEMA_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))


@app.route('/add_fut_pema', methods=['POST'])
def add_fut_pema():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=PEMA_fut_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))

@app.route('/add_fut_ema', methods=['POST'])
def add_fut_ema():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k=dict(request.form)
        print(k)
        k['user']=session['username']
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        j=EMA_fut_mode(k)

        strategy_collection.insert_one(j.__dict__)
        return redirect(url_for('index'))


def generate_botcode(botname, user_id, mobile):
    created_at_ms = int(datetime.datetime.now().timestamp() * 1000)
    unique_suffix = secrets.token_hex(3)
    return '{}_{}_{}_{}_{}'.format(botname, str(user_id), created_at_ms, mobile, unique_suffix)

@app.route('/api_add_ssalgo', methods=['POST'])
def api_add_ssalgo():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    print(k)
    k['user'] = token
    print(1)
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    print(2)
    j = SSALGO_mode(k)
    print(3)
    strategy_collection.insert_one(j.__dict__)
    print(4)
    return jsonify({'success': True, 'message': 'SSALGO strategy added successfully'})



@app.route('/api_add_rf', methods=['POST'])
def api_add_rf():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    #print(k)
    k['user'] = token
    #print(1)
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    #print(2)
    j = RF_mode(k)
    #print(3)
    strategy_collection.insert_one(j.__dict__)
    #print(4)
    return jsonify({'success': True, 'message': 'RF strategy added successfully'})



@app.route('/api_add_ssequity_fut', methods=['POST'])
def api_add_ssequity_fut():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSEQUITY_fut_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'FUT SSEQUITY strategy added successfully'})


@app.route('/api_add_ssequity', methods=['POST'])
def api_add_ssequity():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSEQUITY_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'SSEQUITY strategy added successfully'})


@app.route('/api_add_ssequity_eq', methods=['POST'])
def api_add_ssequity_eq():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSEQUITY_EQ_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'EQ SSEQUITY strategy added successfully'})


@app.route('/api_add_ssequityfno_eq', methods=['POST'])
def api_add_ssequityfno_eq():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSEQUITYFNO_EQ_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'EQ SSEQUITY FNO strategy added successfully'})


@app.route('/api_add_sstrike', methods=['POST'])
def api_add_sstrike():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSTRIKE_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'SSTRIKE strategy added successfully'})

@app.route('/api_add_ssalgo_fut', methods=['POST'])
def api_add_ssalgo_fut():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSALGO_fut_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'FUT SSALGO strategy added successfully'})


@app.route('/api_add_ssauto', methods=['POST'])
def api_add_ssauto():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSAUTO_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'SSAUTO strategy added successfully'})


@app.route('/api_add_ssauto_fut', methods=['POST'])
def api_add_ssauto_fut():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = SSAUTO_fut_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'FUT SSAUTO strategy added successfully'})


@app.route('/api_add_ema', methods=['POST'])
def api_add_ema():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = EMA_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'EMA strategy added successfully'})


@app.route('/api_add_pema', methods=['POST'])
def api_add_pema():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = PEMA_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'PEMA strategy added successfully'})


@app.route('/api_add_pema_fut', methods=['POST'])
def api_add_pema_fut():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = PEMA_fut_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'FUT PEMA strategy added successfully'})


@app.route('/api_add_ema_fut', methods=['POST'])
def api_add_ema_fut():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    k = dict(request.form)
    users = db['users']
    user = users.find_one({'username': token})
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k['user'] = token
    k['botcode'] = generate_botcode(k['botname'], user['_id'], user['mobile'])
    j = EMA_fut_mode(k)

    strategy_collection.insert_one(j.__dict__)
    
    return jsonify({'success': True, 'message': 'FUT EMA strategy added successfully'})




@app.route('/add_order', methods=['POST'])
def add_order():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k = (dict(request.form))
        k['exittime']=int(datetime.datetime.now().timestamp())
        k['user']=session['username']
        j = WebOrder(k)
        orders_collection.insert_one(j.__dict__)
        j = j.__dict__
        trader.fakeorders[j['time']]=j
        trader.breakoutstrats[j['time']] = HuntLevel(trader, j['trigger_price'], j['trigger_type'], j['symbol'], j['comparator_type'],
                                             j['option_type'], j['strike'], j['lot'], j['trail'], j['trail_stoploss'], j['tp_1'], j['tp_2'], j['sl'], j['strike'], j['time']) 
    return redirect(url_for('index'))

@app.route('/edit_rf/<string:order_time>', methods=['POST'])
def edit_rf(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    print(k)
    j = RF_mode(k)
    print(j.__dict__)
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    return redirect(url_for('index'))
@app.route('/edit_admin_ssalgo/<string:order_time>', methods=['POST'])
def edit_admin_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    k = (dict(request.form))
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = SSALGO_fut_mode(k)
    else:
        j = SSALGO_mode(k)
    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    
    return redirect(url_for('get_strategy'))

@app.route('/edit_admin_ssauto/<string:order_time>', methods=['POST'])
def edit_admin_ssauto(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    k = (dict(request.form))
    print(k)
    #k['user']=session['username']
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = SSAUTO_fut_mode(k)
    else:
        j = SSAUTO_mode(k)
    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    
    return redirect(url_for('get_strategy'))



@app.route('/edit_admin_ema/<string:order_time>', methods=['POST'])
def edit_admin_ema(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    k = (dict(request.form))
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = EMA_fut_mode(k)
    else:
        j = EMA_mode(k)
    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    
    return redirect(url_for('get_strategy'))



@app.route('/edit_admin_pema/<string:order_time>', methods=['POST'])
def edit_admin_pema(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    k = (dict(request.form))
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = PEMA_fut_mode(k)
    else:
        j = PEMA_mode(k)
    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    
    return redirect(url_for('get_strategy'))

@app.route('/edit_admin_sstrike/<string:order_time>', methods=['POST'])
def edit_admin_sstrike(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    k = (dict(request.form))
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())    
    j = SSTRIKE_mode(k)
    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    
    return redirect(url_for('get_strategy'))


@app.route('/edit_admin_ssequityfno/<string:order_time>', methods=['POST'])
def edit_admin_ssequityfno(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    k = (dict(request.form))
    print(k)
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())
    j=SSEQUITYFNO_EQ_mode(k)

    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    
    return redirect(url_for('get_strategy'))



@app.route('/edit_admin_ssequity/<string:order_time>', methods=['POST'])
def edit_admin_ssequity(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    k = (dict(request.form))
    print(k)
    gta=strategy_collection.find_one({'botcode': order_time})
    k['user']=gta['user']
    k['exittime']=int(datetime.datetime.now().timestamp())
    j=SSEQUITY_EQ_mode(k)

    strategy_collection.update_one({'botcode': order_time}, {'$set': j.__dict__})
    
    return redirect(url_for('get_strategy'))




@app.route('/api_edit_rf', methods=['POST'])
def api_edit_rf():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = RF_mode(k)


    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'RF strategy updated successfully'})


##########################################################################################################################################


@app.route('/api_edit_admin_eqssalgo', methods=['POST'])
def api_edit_admin_eqssalgo():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = EQSSALGO_mode(k)
    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'EQSSALGO strategy updated successfully'})




@app.route('/api_edit_admin_ssalgo', methods=['POST'])
def api_edit_admin_ssalgo():
    print('i am deee')
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    k = dict(request.form)
    print(k)
    gta = strategy_collection.find_one({'botcode': request.form.get('botcode')})
    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = SSALGO_fut_mode(k)
    else:
        j = SSALGO_mode(k)

    strategy_collection.update_one({'botcode':request.form.get('botcode') }, {'$set': j.__dict__})

    return generate_response('SSALGO strategy edited successfully')


@app.route('/api_edit_admin_ssauto/', methods=['POST'])
def api_edit_admin_ssauto():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = SSAUTO_fut_mode(k)
    else:
        j = SSAUTO_mode(k)

    strategy_collection.update_one({'botcode': botcode}, {'$set': j.__dict__})
    return generate_response('SSAUTO strategy edited successfully')


@app.route('/api_edit_admin_ema/', methods=['POST'])
def api_edit_admin_ema():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = EMA_fut_mode(k)
    else:
        j = EMA_mode(k)

    strategy_collection.update_one({'botcode': botcode}, {'$set': j.__dict__})
    return generate_response('EMA strategy edited successfully')


@app.route('/api_edit_admin_pema/', methods=['POST'])
def api_edit_admin_pema():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = PEMA_fut_mode(k)
    else:
        j = PEMA_mode(k)

    strategy_collection.update_one({'botcode': botcode}, {'$set': j.__dict__})
    return generate_response('PEMA strategy edited successfully')


@app.route('/api_edit_admin_sstrike/', methods=['POST'])
def api_edit_admin_sstrike():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = SSTRIKE_mode(k)
    strategy_collection.update_one({'botcode': botcode}, {'$set': j.__dict__})
    return generate_response('SSTRIKE strategy edited successfully')


@app.route('/api_edit_admin_ssequityfno/', methods=['POST'])
def api_edit_admin_ssequityfno():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = SSEQUITYFNO_EQ_mode(k)
    strategy_collection.update_one({'botcode': botcode}, {'$set': j.__dict__})
    return generate_response('SSEQUITY FNO strategy edited successfully')


@app.route('/api_edit_admin_ssequity/', methods=['POST'])
def api_edit_admin_ssequity():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = SSEQUITY_EQ_mode(k)
    strategy_collection.update_one({'botcode': botcode}, {'$set': j.__dict__})
    return generate_response('SSEQUITY strategy edited successfully')


@app.route('/api_edit_admin_rf/', methods=['POST'])
def api_edit_admin_rf():
    token = request.form.get('token')

    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    botcode = request.form.get('botcode')
    gta = strategy_collection.find_one({'botcode': botcode})

    if not gta:
        return generate_response('Strategy not found', success=False, status_code=404)

    k = dict(request.form)
    k['user'] = gta['user']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = RF_mode(k)
    strategy_collection.update_one({'botcode': botcode}, {'$set': j.__dict__})
    return generate_response('RF strategy edited successfully')


@app.route('/api_edit_admin_strategy_form/<string:order_time>', methods=['POST'])
def api_edit_admin_strategy_form(order_time):
    token = request.form.get('token')
    
    if not token:
        return generate_response('Authentication token is missing', success=False, status_code=401)

    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return generate_response('User is not an admin', success=False, status_code=403)

    order = strategy_collection.find_one({'botcode': str(order_time)})
    if not order:
        return generate_response('Order not found', success=False, status_code=404)

    
    algo=order['strategy'].lower()
    if 'ssequity' in algo:
        page='add_{}_eq_form.html'.format(algo)
    else:
        if 'onspot' in list(order.keys()):
            page='add_{}_fut_form.html'.format(algo)
        else:
            page='add_{}_form.html'.format(algo)

    del order['_id']

    return {'success':True,'message':"Successfully Fetched Strategy Form",'data':{'page':mform[page],'info':order,'action_url':f'/api_edit_admin_{algo}'}}








@app.route('/edit_ssalgo/<string:order_time>', methods=['POST'])
def edit_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = SSALGO_fut_mode(k)
    else:
        j = SSALGO_mode(k)
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    
    return redirect(url_for('index'))


@app.route('/edit_eqssalgo/<string:order_time>', methods=['POST'])
def edit_eqssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))

    k = (dict(request.form))
    print(k)
    k['user']=session['username']
    symbols = (request.form.getlist('symbol[]'))
    k['symbol']=symbols#eval(k['symbol'])#s
    k['exittime']=int(datetime.datetime.now().timestamp())
    print('iama edtiting ')
    print(k)
    j = EQSSALGO_mode(k)
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    
    return redirect(url_for('index'))




@app.route('/edit_ssauto/<string:order_time>', methods=['POST'])
def edit_ssauto(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    print(k)
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = SSAUTO_fut_mode(k)
    else:
        j = SSAUTO_mode(k)
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    
    return redirect(url_for('index'))

@app.route('/edit_ssequity/<string:order_time>', methods=['POST'])
def edit_ssequity(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    print(k)
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    j=SSEQUITY_EQ_mode(k)

    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    
    return redirect(url_for('index'))

@app.route('/edit_ssequityfno/<string:order_time>', methods=['POST'])
def edit_ssequityfno(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    print(k)
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    j=SSEQUITYFNO_EQ_mode(k)

    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    
    return redirect(url_for('index'))



@app.route('/edit_sstrike/<string:order_time>', methods=['POST'])
def edit_sstrike(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    j = SSTRIKE_mode(k)
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    
    return redirect(url_for('index'))

@app.route('/edit_ema/<string:order_time>', methods=['POST'])
def edit_ema(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = EMA_fut_mode(k)
    else:
        j = EMA_mode(k)
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    return redirect(url_for('index'))


@app.route('/edit_pema/<string:order_time>', methods=['POST'])
def edit_pema(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    if 'onspot' in list(k.keys()):
        j = PEMA_fut_mode(k)
    else:
        j = PEMA_mode(k)
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': j.__dict__})
    return redirect(url_for('index'))



@app.route('/api_edit_ssalgo', methods=['POST'])
def api_edit_ssalgo():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = SSALGO_fut_mode(k)
    else:
        j = SSALGO_mode(k)

    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'SSALGO strategy updated successfully'})
@app.route('/api_edit_fractalnubiatimehedgeorder', methods=['POST'])
def api_edit_fractalnubiatimehedgeorder():
    try:
        token = request.form.get('token')
        botcode = request.form.get('botcode')

        if not token:
            return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

        if not botcode:
            return jsonify({'success': False, 'error': 'botcode is missing'}), 400

        user = get_user_from_token(token)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        existing_strategy = strategy_collection.find_one(
            {'botcode': botcode, 'user': user['username']}
        )

        # Get all form data once
        form_data = dict(request.form)
        method_values = request.form.getlist('method')
        if method_values:
            form_data['method'] = method_values[-1]
        
        # Extract the lists of option data
        options = request.form.getlist('ooption')
        strikes = request.form.getlist('ostrike')
        sides = request.form.getlist('oside')
        expiries = request.form.getlist('oexpiry')
        lots = request.form.getlist('olot')
        
        # Ensure all lists have the same length
        if not (len(options) == len(strikes) == len(sides) == len(expiries) == len(lots)):
            return jsonify({'success': False, 'error': 'Mismatched option data lengths'}), 400
            
        # Create the legs data with proper type conversion
        legs_data = []
        for option, strike, side, expiry, lot in zip(options, strikes, sides, expiries, lots):
            # Handle empty strings for numeric fields
            try:
                strike_val = float(strike) if strike.strip() else 0.0
                lot_val = int(float(lot)) if lot.strip() else 0
            except ValueError:
                return jsonify({'success': False, 'error': f'Invalid numeric value: strike={strike}, lot={lot}'}), 400
                
            legs_data.append({
                'option': option,
                'strike': strike_val,
                'side': side,
                'expiry': expiry,
                'lot': lot_val
            })
        
        # Process any other numeric fields in form_data
        for key in form_data:
            if key in ['quantity', 'price', 'stoploss', 'target', 'trailsl', 'lotsize']:
                try:
                    if form_data[key] and form_data[key].strip():
                        form_data[key] = float(form_data[key])
                    else:
                        form_data[key] = 0.0
                except ValueError:
                    return jsonify({'success': False, 'error': f'Invalid numeric value for {key}: {form_data[key]}'}), 400
        
        # Update the form data with processed information
        form_data['user'] = user['username']
        form_data['legs'] = legs_data
        form_data['exittime'] = int(datetime.datetime.now().timestamp())
        
        # Create the strategy object
        strategy_obj = FRACTALNUBIATIMEHEDGEORDER_mode(form_data)
        strategy_dict = strategy_obj.__dict__
        
        update_doc = {'$set': strategy_dict}
        if existing_strategy:
            old_legs = existing_strategy.get('legs', [])
            old_method = existing_strategy.get('method')
            if (
                old_legs != strategy_dict.get('legs', [])
                or old_method != strategy_dict.get('method')
            ):
                reset_doc = _fractal_reset_update(
                    botcode,
                    user['username'],
                    strategy_dict
                )
                update_doc = reset_doc

        # Update the database
        result = strategy_collection.update_one(
            {'botcode': botcode, 'user': user['username']}, 
            update_doc
        )
        
        # Check if the update was successful
        if result.matched_count == 0:
            return jsonify({'success': False, 'error': 'Strategy not found for this user'}), 404
            
        return jsonify({'success': True, 'message': 'FRACTALNUBIATIMEHEDGEORDER strategy updated successfully'})
        
    except Exception as e:
        # Log the error for debugging
        print(f"Error in api_edit_fractalnubiatimehedgeorder: {str(e)}")
        # Return a generic error message to the client
        return jsonify({'success': False, 'error': f'An internal server error occurred: {str(e)}'}), 500
@app.route('/api_edit_eqssalgo', methods=['POST'])
def api_edit_eqssalgo():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    print('edit edit_eqssalgo')
    print(k)
    k['user'] = user['username']
    k['symbol'] = k['symbol[]'].split(',')#eval(k['symbol'])
    k['symbol[]'] = k['symbol[]'].split(',')
    k['exittime'] = int(datetime.datetime.now().timestamp())
    #print(k)
    j = EQSSALGO_mode(k)
    j=dict(j.__dict__)
    j['symbol[]']=k['symbol[]']#.split(',')
    #del j['symbol']
    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j})

    return jsonify({'success': True, 'message': 'EQSSALGO strategy updated successfully'})

@app.route('/api_edit_ssauto', methods=['POST'])
def api_edit_ssauto():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    #print(k)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = SSAUTO_fut_mode(k)
    else:
        j = SSAUTO_mode(k)

    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'SSAUTO strategy updated successfully'})

@app.route('/api_edit_ssequity', methods=['POST'])
def api_edit_ssequity():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = SSEQUITY_EQ_mode(k)
    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'SSEQUITY strategy updated successfully'})

@app.route('/api_edit_ssequityfno', methods=['POST'])
def api_edit_ssequityfno():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = SSEQUITYFNO_EQ_mode(k)
    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'SSEQUITY FNO strategy updated successfully'})

@app.route('/api_edit_sstrike', methods=['POST'])
def api_edit_sstrike():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    j = SSTRIKE_mode(k)
    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'SSTRIKE strategy updated successfully'})

@app.route('/api_edit_ema', methods=['POST'])
def api_edit_ema():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = EMA_fut_mode(k)
    else:
        j = EMA_mode(k)

    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'EMA strategy updated successfully'})

@app.route('/api_edit_pema', methods=['POST'])
def api_edit_pema():
    token = request.form.get('token')
    botcode = request.form.get('botcode')

    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    k = dict(request.form)
    k['user'] = user['username']
    k['exittime'] = int(datetime.datetime.now().timestamp())

    if 'onspot' in k:
        j = PEMA_fut_mode(k)
    else:
        j = PEMA_mode(k)

    strategy_collection.update_one({'botcode': botcode, 'user': user['username']}, {'$set': j.__dict__})

    return jsonify({'success': True, 'message': 'PEMA strategy updated successfully'})
@app.route('/edit_strategyinput/<string:order_time>', methods=['POST'])
def edit_strategyinput(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    
    strategyinput_collection.update_one({'strategy': order_time}, {'$set': {'strategy':k['strategy']
        ,'r1':float(k['r1']),'k1':float(k['k1']),'r2':float(k['r2']),'k2':float(k['k2']),'timeframe':k['timeframe'] }})
    trader.strategyinputs[order_time]=strategyinput_collection.find_one({'strategy':order_time})
    
    return redirect(url_for('admin'))
@app.route('/api_edit_strategyinput', methods=['POST'])
def api_edit_strategyinput():
    # Retrieve token and botcode from request.form
    token = request.form.get('token')
    botcode = request.form.get('strategy')

    # Ensure the token is provided
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    # Ensure botcode is provided
    if not botcode:
        return jsonify({'success': False, 'error': 'botcode is missing'}), 400

    # Validate the user based on the token
    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # Check if the strategy exists for this user
    strategy = strategyinput_collection.find_one({'strategy': botcode})
    if not strategy:
        return jsonify({'success': False, 'error': 'Strategy not found'}), 404

    # Retrieve form data
    k = dict(request.form)

    # Update the strategy input in the collection
    strategyinput_collection.update_one(
        {'strategy': botcode},
        {'$set': {
            'strategy': k['strategy'],
            'r1': float(k['r1']),
            'k1': float(k['k1']),
            'r2': float(k['r2']),
            'k2': float(k['k2']),
            'timeframe': k['timeframe']
        }}
    )

    # Update the strategy input for the trader in-memory cache
    trader.strategyinputs[botcode] = strategyinput_collection.find_one({'strategy': botcode})

    # Return success response
    return jsonify({'success': True, 'message': 'Strategy input updated successfully'})


@app.route('/api_edit_strategyinput_form', methods=['POST'])
def api_edit_strategyinput_form():
    # Retrieve token and botcode from request.form
    token = request.form.get('token')
    botcode = request.form.get('strategy')

    # Ensure the token is provided
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401

    # Validate the user based on the token
    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # Check if the user has admin privileges
    userdata = users_collection.find_one({'username': user['username']})
    if 'admin' not in userdata:
        return jsonify({'success': False, 'error': 'Not authorized'}), 403

    # Fetch the strategy input by botcode
    order = strategyinput_collection.find_one({'strategy': botcode})

    if not order:
        return jsonify({'success': False, 'error': 'Strategy not found'}), 404

    # Return the strategy data as JSON
    return jsonify({
        'success': True,
        'strategy': order['strategy'],
        'r1': order.get('r1', None),
        'k1': order.get('k1', None),
        'r2': order.get('r2', None),
        'k2': order.get('k2', None),
        'timeframe': order.get('timeframe', None),
        'action_url':'/api_edit_strategyinput'
    })


@app.route('/edit_order/<string:order_time>', methods=['POST'])
def edit_order(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    k['user']=session['username']
    k['exittime']=int(datetime.datetime.now().timestamp())
    j = WebOrder(k)
    orders_collection.update_one({'time': order_time,'user':session['username']}, {'$set': j.__dict__})
    j = j.__dict__
    trader.fakeorders[j['time']]=j
    return redirect(url_for('index'))

@app.route('/stop_ssalgo/<string:order_time>')
def stop_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': {'status':'paused'}})
    _mark_strategy_positions_exitit(order_time, session['username'])
    return redirect(url_for('index'))

@app.route('/start_ssalgo/<string:order_time>')
def start_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))

    strategy_collection.update_one(
        {'botcode': order_time,'user':session['username']},
        _fractal_reset_update(order_time, session['username'], {'status': 'opened'})
    )
    return redirect(url_for('index'))

@app.route('/stop_admin_ssalgo/<string:order_time>')
def stop_admin_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    strategy_collection.update_one({'botcode': order_time}, {'$set': {'status':'paused'}})
    _mark_strategy_positions_exitit(order_time)
    return redirect(url_for('get_strategy'))

@app.route('/start_admin_ssalgo/<string:order_time>')
def start_admin_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    strategy_collection.update_one(
        {'botcode': order_time},
        _fractal_reset_update(order_time, None, {'status': 'opened'})
    )
    return redirect(url_for('get_strategy'))

@app.route('/start_control/<string:order_time>')
def start_control(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'controlmode':True}})
    trader.controls[order_time]=admincontrol_collection.find_one({'symbol':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))
@app.route('/stop_control/<string:order_time>')
def stop_control(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'controlmode':False}})
    trader.controls[order_time]=admincontrol_collection.find_one({'symbol':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))
@app.route('/start_cebuy/<string:order_time>')
def start_cebuy(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'Buytrade':True}})
    trader.controls[order_time]=admincontrol_collection.find_one({'symbol':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))
@app.route('/start_cesell/<string:order_time>')
def start_cesell(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'Buytrade':False}})
    trader.controls[order_time]=admincontrol_collection.find_one({'symbol':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))
@app.route('/start_pebuy/<string:order_time>')
def start_pebuy(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'Selltrade':True}})
    trader.controls[order_time]=admincontrol_collection.find_one({'symbol':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))
@app.route('/start_pesell/<string:order_time>')
def start_pesell(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'Selltrade':False}})
    trader.controls[order_time]=admincontrol_collection.find_one({'symbol':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))




@app.route('/start_strategyco/<string:order_time>')
def start_strategyco(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    #admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'Selltrade':True}})
    strategyinput_collection.update_one({'strategy': order_time}, {'$set': {'update':True}})
    #trader.controls[order_time]=admincontrol_collection.find_one({'symbol':order_time})
    trader.strategyinputs[order_time]=strategyinput_collection.find_one({'strategy':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))
@app.route('/stop_strategyco/<string:order_time>')
def stop_strategyco(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    #admincontrol_collection.update_one({'symbol': order_time}, {'$set': {'Selltrade':False}})
    strategyinput_collection.update_one({'strategy': order_time}, {'$set': {'update':False}})
    trader.strategyinputs[order_time]=strategyinput_collection.find_one({'strategy':order_time})
    #print(trader.controls)
    return redirect(url_for('admin'))







@app.route('/delete_ssalgo/<string:order_time>')
def delete_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    #strategy_collection.delete_one({'time': order_time,'user':session['username']})
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, {'$set': {'status':'closed'}})
    _mark_strategy_positions_exitit(order_time, session['username'])
    return redirect(url_for('index'))

@app.route('/delete_admin_ssalgo/<string:order_time>')
def delete_admin_ssalgo(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    userdata = users_collection.find_one({'username': session['username']})
    if 'admin' in list(userdata.keys()):
        adminuser=userdata['admin']
    else:
        return redirect(url_for('index'))
    #strategy_collection.delete_one({'time': order_time,'user':session['username']})
    strategy_collection.update_one({'botcode': order_time}, {'$set': {'status':'closed'}})
    _mark_strategy_positions_exitit(order_time)
    return redirect(url_for('get_strategy'))

@app.route('/delete_order/<string:order_time>')
def delete_order(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    orders_collection.delete_one({'time': order_time,'user':session['username']})
    return redirect(url_for('index'))



@app.route('/edit_apikey_form',methods=['POST','GET'])
def edit_apikey_form():
    if 'username' not in session:
        return redirect(url_for('login'))

    z = list(broker_collection.find({'user':session['username']}))
    selected_broker = 'aliceblue'
    if z:
        selected_broker = z[0]['selectedbroker']

    # Get all broker data for the user
    broker_data = {}
    brokers = ['aliceblue', 'shoonya', 'fyers', 'angelone', 'dhan','zerodha','mofs','smc','mstock']
    
    for broker in brokers:
        apikey = apikeys_collection.find_one({'user': session['username'], 'broker': broker})
        if apikey:
            broker_data[broker] = apikey
        else:
            # Default empty structure for each broker
            broker_data[broker] = {
                'usr': '',
                'pwd': '',
                'factor2': '',
                'vc': '',
                'app_key': '',
                'imei': '',
                'apikey': '',
                'alice_password': '',
                'auth_code': '',
                'apisecret': '',
                'totp_key': '',
                'client_id': '',
                'secret_key': '',
                'redirect_uri': '',
                'broker': broker
            }
    
    return render_template('add_edit_apikey.html',
                         selected_broker=selected_broker,
                         broker_data=broker_data,
                         action_url=url_for('edit_apikey'),
                         action_url1=url_for('edit_broker'))
@app.route('/edit_broker', methods=['POST'])
#@login_required
def edit_broker():
    if 'username' not in session:
        return redirect(url_for('login'))
    try:
        if request.form:
            # Create a copy of form data
            k = dict(request.form)
            k['user'] = session['username']
            
            # Clean the data
            j = {
                key.strip(): str(value).strip() 
                for key, value in k.items()
                if value is not None
            }
            result = broker_collection.update_one(
                {'user': session['username']},
                {'$set': j},
                upsert=True
            )
            if session['username'] in trader.userloggedin:
                trader.userloggedin.remove(session['username'])
            
            flash('Broker settings updated successfully', 'success')
        else:
            flash('No data received', 'error')
            
    except Exception as e:
        flash(f'Error updating broker settings: {str(e)}', 'error')
        
    return redirect(url_for('edit_apikey_form'))
@app.route('/api_edit_broker', methods=['POST'])
def api_edit_broker():
    token = request.form.get('token')
    
    if not token:
        return jsonify({'success': False, 'error': 'Authentication token is missing'}), 401
    
    user = get_user_from_token(token)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    
    try:
        if request.form:
            # Create a copy of form data
            k = dict(request.form)
            k['user'] = user['username']
            
            # Clean the data
            j = {
                key.strip(): str(value).strip() 
                for key, value in k.items()
                if value is not None and key != 'token'  # Exclude token from being saved
            }
            
            # Update or insert broker information
            result = broker_collection.update_one(
                {'user': token},
                {'$set': j},
                upsert=True
            )
            
            # Remove user from logged in list to force re-login
            if token in trader.userloggedin:
                trader.userloggedin.remove(user['username'])
            
            return jsonify({'success': True, 'message': 'Broker settings updated successfully'})
        else:
            return jsonify({'success': False, 'error': 'No data received'}), 400
            
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error updating broker settings: {str(e)}'}), 500    
@app.route('/add_apikey', methods=['POST'])
def add_apikey():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.form:
        k = (dict(request.form))
        k['user']=session['username']
        print(k)
        del k['_id']
        if not apikeys_collection.find_one({'user':session['username'],'broker':request.form['broker']}):
            apikeys_collection.insert_one(k)
    return redirect(url_for('index'))


@app.route('/edit_apikey', methods=['POST'])
def edit_apikey():
    if 'username' not in session:
        return redirect(url_for('login'))
    k = (dict(request.form))
    k['user']=session['username']
    del k['_id']
    #j = Webapikey(k)

    j={}
    for i in k:
        j[i.replace(' ','')]=k[i].replace(' ','')
    if not apikeys_collection.find_one({'user':session['username'],'broker':str(request.form['broker']).replace(' ','')}):
        apikeys_collection.insert_one(j)
    else:
        apikeys_collection.update_one({'user': session['username'],'broker':str(request.form['broker']).replace(' ','')}, {'$set':j})

    return redirect(url_for('index'))




@app.route("/dash")
def dash():
    return render_template('dark-theme-demo.html', utc_dt=datetime.datetime.utcnow())


def generate_reset_token():
    return secrets.token_urlsafe(32)


backend_services = BackendServices(
    app=app,
    logger=logger,
    mail=mail,
    db=db,
    users_collection=users_collection,
    apikeys_collection=apikeys_collection,
    subscriptionperiod_collection=subscriptionperiod_collection,
    get_user_from_token=get_user_from_token,
    create_access_token_func=create_access_token,
    generate_reset_token=generate_reset_token,
    randint_func=randint,
)


def get_user_collection():
    return backend_services.get_user_collection()


def send_reset_email(email, reset_token):
    backend_services.send_reset_email(email, reset_token)


def send_otp_email(email, otp):
    backend_services.send_otp_email(email, otp)


register_apikey_api_routes(app, backend_services)
register_auth_api_routes(app, backend_services)

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/login', methods=['POST','GET'])
def login():
    global freeday
    if 'username' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        users = db['users']
        #login_user = db['users'].find_one({'username' : request.form['username']})
        login_user = users.find_one({'$or': [{'username': str(request.form['username']).lower() }, {'email': str(request.form['username']).lower() }]})
        print(login_user)
        if login_user:
            if bcrypt.hashpw(request.form['password'].encode('utf-8'), login_user['password']) == login_user['password']:
                session['username'] = login_user['username']#request.form['username']
                subscribe_user = subscriptionperiod_collection.find_one({'user':login_user['username']})
                if not subscribe_user:
                    today_date = datetime.datetime.now().date()
                    future_date = today_date + datetime.timedelta(days=freeday)
                    ftoday=today_date.strftime('%Y-%m-%d')
                    ffuture=future_date.strftime('%Y-%m-%d')
                    ser={'user':login_user['username'],'start':ftoday,'end':ffuture,'subtype':"free"}
                    subscriptionperiod_collection.insert_one(ser)

                return redirect(url_for('index'))

    return render_template('login.html')

@app.route('/register', methods=['POST', 'GET'])
def register():
    print("Entered /register route")  # Checkpoint 1

    if request.method == 'POST':
        print("Request method is POST")  # Checkpoint 2

        users = db['users']
        username = str(request.form['username']).lower()
        email = str(request.form['email']).lower()
        print(f"Received username: {username}, email: {email}")  # Checkpoint 3

        existing_user = users.find_one({'$or': [{'username': username}, {'email': email}]})
        print(f"Existing user check: {existing_user}")  # Checkpoint 4

        if existing_user is None:
            print("No existing user found. Proceeding to register.")  # Checkpoint 5
            hashpass = bcrypt.hashpw(request.form['password'].encode('utf-8'), bcrypt.gensalt())
            users.insert_one({
                'username': username,
                'email': email,
                'mobile': request.form['mobile'],
                'password': hashpass
            })
            print("User inserted into database.")  # Checkpoint 6

            session['username'] = username
            subscribe_user = subscriptionperiod_collection.find_one({'user': session['username']})
            print(f"Subscription record check: {subscribe_user}")  # Checkpoint 7

            if not subscribe_user:
                print("No existing subscription found. Creating free subscription.")  # Checkpoint 8
                today_date = datetime.datetime.now().date()
                future_date = today_date + datetime.timedelta(days=freeday)
                ftoday = today_date.strftime('%Y-%m-%d')
                ffuture = future_date.strftime('%Y-%m-%d')
                ser = {
                    'user': session['username'],
                    'start': ftoday,
                    'end': ffuture,
                    'subtype': "free"
                }
                subscriptionperiod_collection.insert_one(ser)
                print(f"Free subscription created: {ser}")  # Checkpoint 9

            print("Redirecting to index")  # Checkpoint 10
            return redirect(url_for('index'))

        print("User already exists!")  # Checkpoint 11
        return 'That username or email already exists!'

    print("Request method is GET. Rendering registration page.")  # Checkpoint 12
    return render_template('registration.html')



@app.route('/user_profile', methods=['GET', 'POST'])
def user_profile():
    if 'username' not in session:
        # Redirect to login if the user is not logged in
        return redirect(url_for('login'))
    
    user = users_collection.find_one({'username': session['username']})
    
    # Handle form submission for updating limits
    if request.method == 'POST':
        # Get the updated limits from the form
        day_profit_limit = request.form.get('day_profit_limit', '25000')
        day_loss_limit = request.form.get('day_loss_limit', '25000')
        trade_limit = request.form.get('trade_limit', '100')
        
        # Update the user document with the new limits
        users_collection.update_one(
            {'username': session['username']},
            {'$set': {
                'day_profit_limit': day_profit_limit,
                'day_loss_limit': day_loss_limit,
                'trade_limit': trade_limit
            }}
        )
        
        # Update the local user object with the new values
        user['day_profit_limit'] = day_profit_limit
        user['day_loss_limit'] = day_loss_limit
        user['trade_limit'] = trade_limit
        
        flash('Trading limits updated successfully', 'success')
    
    # Set default values for subscription info
    user['end'] = 'None'
    user['subtype'] = 'None'
    
    # Get subscription information
    sub = subscriptionperiod_collection.find_one({'user': session['username']})
    if sub:
        user['end'] = sub['end']
        user['subtype'] = sub['subtype']
    
    # Set default values for trading limits if not present
    if 'day_profit_limit' not in user:
        user['day_profit_limit'] = '25000'
    if 'day_loss_limit' not in user:
        user['day_loss_limit'] = '25000'
    if 'trade_limit' not in user:
        user['trade_limit'] = '100'
    
    return render_template('user_profile.html', user=user)


@app.route('/api_user_profile', methods=['POST', 'GET'])
def api_user_profile():
    if request.method == 'POST':
        token = request.form.get('token', None)
        
        # Ensure token is provided
        if not token:
            return jsonify({'success': False, 'message': 'Token is required'}), 400

        # Find user in the users_collection
        user = users_collection.find_one({'username': token})
        
        if not user:
            return jsonify({'success': False, 'message': 'User not found'}), 404

        # Set default values for trading limits if not present
        if 'day_profit_limit' not in user:
            user['day_profit_limit'] = '25000'
        if 'day_loss_limit' not in user:
            user['day_loss_limit'] = '25000'
        if 'trade_limit' not in user:
            user['trade_limit'] = '100'

        # Set default values for subscription info
        user['end'] = 'None'
        user['subtype'] = 'None'

        # Get subscription information
        sub = subscriptionperiod_collection.find_one({'user': token})
        if sub:
            user['end'] = sub['end']
            user['subtype'] = sub['subtype']

        # Remove sensitive data before returning the profile
        user.pop('_id', None)
        user.pop('password', None)

        # Calculate StrategyRemaining
        strategy = list(strategy_collection.find({'user': token, 'status': {'$in': ['opened', 'paused']}}, projection={'_id': False}))
        renamed = 0
        for i in strategy:
            if 'symbol' in i:
                if isinstance(i['symbol'], str):
                    renamed += 1
                elif isinstance(i['symbol'], list):
                    renamed += len(i['symbol'])
        user['StrategyRemaining'] = int(user.get('StrategyLimit', 10)) - int(renamed)

        # Prepare user_profile dict for API response
        user_profile = {key: str(value) for key, value in user.items()}

        return jsonify({'success': True, 'message': 'Complete User Profile', 'data': user_profile})

    return jsonify({'success': False, 'message': 'Invalid request method'}), 405

@app.route('/termsandconditions')
def termsandconditions():

    return render_template('termandcnd.html')


@app.route('/aboutus')
def aboutus():
    return render_template('aboutus.html')

@app.route('/refundpolicy')
def refundpolicy():
    return render_template('refundpolicy.html')

@app.route('/contactus')
def contactus():
    return render_template('contactus.html')

@app.route('/pricing')
def pricing():
    month_1='1101'
    print(request.form)
    return render_template('subscribeman.html',action_url=url_for('pay', plan=month_1))

@app.route('/api_pricing',methods=['POST'])
def api_pricing():
    #plans={'1 Month':2999,'3 Months': 8547,'6 Months':16200,'12 Months':30600}
    complete_plan={'1 Month':{'Original':2999,'Discounted':2999},
    '3 Months':{'Original':9000,'Discounted':8547},
    '6 Months':{'Original':18000,'Discounted':16200},
    '12 Months':{'Original':36000,'Discounted':30600},
    'LIFETIME':{'Original':360000,'Discounted':99999}}
    lcomplete_plan=[['1 Month',2999,2999],
    ['3 Months',9000,8547],
    ['6 Months',18000,16200],
    ['12 Months',36000,30600],
    ['LIFETIME',360000,99999]
    ]
    #,
    #'Custom':{'Original':0,'Discounted':0}}
    return {'message':'Successfully Fetched Pricing Plans','success':True,'data':lcomplete_plan} #render_template('subscribeman.html',action_url=url_for('pay'))



@app.route("/pay",methods=['POST'])
def pay():

    #global payment, name, units
    if 'username' not in session:
        # Redirect to login if the user is not logged in
        return redirect(url_for('login'))
    #print('fsdfdsfsd')  
    k=dict(request.form)
    if '1month' in k['price']:
        pay=299900
        days=30
    elif '3month' in k['price']:
        pay=854715
        days=90
    elif '6month' in k['price']:
        pay=1620000
        days=180
    elif '12month' in k['price']:
        pay=3060000
        days=365
    elif '12_month' in k['price']:
        pay=599900
        days=365
    elif '13month' in k['price']:
        pay=649900
        days=365
    user =users_collection.find_one({'username' : session['username']})



    name = user['username']#request.form.get("username")
    #units = int(request.form.get("units"))
    client = razorpay.Client(auth=(AppConfig.RAZORPAY_KEY_ID, AppConfig.RAZORPAY_KEY_SECRET))

    data = {"amount": pay, "currency": "INR", "receipt": "#11"}
    payment = client.order.create(data=data)
    user_dets = {
        "name": name,
        "email": user["email"],
        "ph_nm": user["mobile"],
        "duration":days,
        "payment": payment
    }

    return render_template("pay.html", details=user_dets,key=AppConfig.RAZORPAY_KEY_ID)



@app.route("/pay/verify", methods=["GET", "POST"])
def pay_verify():
    client = razorpay.Client(auth=(AppConfig.RAZORPAY_KEY_ID, AppConfig.RAZORPAY_KEY_SECRET))
    payment_id = request.form.get("payment_id")
    order_id = request.form.get("order_id")
    signature = request.form.get("signature")
    duration = request.form.get("duration")
    params_dict = {
        'razorpay_order_id': order_id,
        'razorpay_payment_id': payment_id,
        'razorpay_signature': signature
    }
    # Try and expect block to save the details.
    res = client.utility.verify_payment_signature(params_dict)
    
    print(request.form)
    data = {
    "time":datetime.datetime.now(),
    "user":session['username'],
        "order_id": order_id,
        "payment_id": payment_id,
        "status": res
    }
    print(session['username'])
    print(data)
    payreceipt_collection.insert_one(data)
    sub=subscriptionperiod_collection.find_one({'user':session['username']})
    if sub:
        startdate = datetime.datetime.strptime(sub['start'], '%Y-%m-%d')
        if datetime.datetime.strptime(sub['end'], '%Y-%m-%d') >= datetime.datetime.now():
            enddate = datetime.datetime.strptime(sub['end'], '%Y-%m-%d')+ datetime.timedelta(days=int(duration))
        else:
            enddate = datetime.datetime.now()+ datetime.timedelta(days=int(duration))
        usertye='paid'
        ftoday=startdate.strftime('%Y-%m-%d')
        ffuture=enddate.strftime('%Y-%m-%d')
        ser={'user':session['username'],'start':ftoday,'end':ffuture,'subtype':usertye}
        subscriptionperiod_collection.update_one({'user': session['username']}, {'$set': ser})
    else:
        today_date = datetime.datetime.now().date()
        future_date = today_date + datetime.timedelta(days=int(duration))
        ftoday=today_date.strftime('%Y-%m-%d')
        ffuture=future_date.strftime('%Y-%m-%d')
        ser={'user':session['username'],'start':ftoday,'end':ffuture,'subtype':"paid"}
        subscriptionperiod_collection.insert_one(ser)
    flash("Payment was Successfully")
    return redirect(url_for('index'))
    #return render_template('verification.html', data=data)




@app.route("/pay/fail")
def pay_failure():
    flash("Payment couldn't go through and failed due to some reason.")
    return redirect(url_for('index'))



@app.route('/api_pay', methods=['POST'])
def api_pay():
    # Get the form data
    form_data = dict(request.form)

    # Get the user details from the form data
    username = form_data.get('token')
    if not username:
        return jsonify({'success': False, 'error': 'Username is required'}), 400

    user = users_collection.find_one({'username': username})
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # Calculate the payment amount based on the selected plan
    if '1 Month' in form_data['price']:
        pay = 299900
        days = 30
    elif '3 Months' in form_data['price']:
        pay = 854715
        days = 90
    elif '6 Months' in form_data['price']:
        pay = 1620000
        days = 180
    elif '12 Months' in form_data['price']:
        pay = 3060000
        days = 365
    elif '12_month' in form_data['price']:
        pay = 599900
        days = 365
    elif '13month' in form_data['price']:
        pay = 649900
        days = 365
    elif 'LIFETIME' in form_data['price']:
        pay = 9999900
        days = 3650*5
    else:
        return jsonify({'success': False, 'error': 'Invalid subscription plan'}), 400

    # Create a Razorpay order
    client = razorpay.Client(auth=(AppConfig.RAZORPAY_KEY_ID, AppConfig.RAZORPAY_KEY_SECRET))
    data = {"amount": pay, "currency": "INR", "receipt": "#11"}
    payment = client.order.create(data=data)

    # Prepare the user details
    user_details = {
        "name": user['username'],
        "email": user["email"],
        "ph_nm": user["mobile"],
        "duration": days,
        "payment": payment
    }

    # Return the user details and Razorpay key
    return jsonify({'success': True, 'data': user_details, 'key': AppConfig.RAZORPAY_KEY_ID})


@app.route('/api_pay_verify', methods=['POST'])
def api_pay_verify():
    # Get the payment details from the request
    payment_id = request.form.get("payment_id")
    order_id = request.form.get("order_id")
    signature = request.form.get("signature")
    username = request.form.get("token")
    duration = request.form.get("duration")

    # Verify the payment signature
    client = razorpay.Client(auth=(AppConfig.RAZORPAY_KEY_ID, AppConfig.RAZORPAY_KEY_SECRET))
    params_dict = {
        'razorpay_order_id': order_id,
        'razorpay_payment_id': payment_id,
        'razorpay_signature': signature
    }
    res = client.utility.verify_payment_signature(params_dict)

    # Save the payment details
    data = {
        "time": datetime.datetime.now(),
        "user": username,
        "order_id": order_id,
        "payment_id": payment_id,
        "status": res
    }
    payreceipt_collection.insert_one(data)

    # Update the user's subscription
    sub = subscriptionperiod_collection.find_one({'user': username})
    if sub:
        startdate = datetime.datetime.strptime(sub['start'], '%Y-%m-%d')
        if datetime.datetime.strptime(sub['end'], '%Y-%m-%d') >= datetime.datetime.now():
            enddate = datetime.datetime.strptime(sub['end'], '%Y-%m-%d') + datetime.timedelta(days=int(duration))
        else:
            enddate = datetime.datetime.now() + datetime.timedelta(days=int(duration))
        usertye = 'paid'
        ftoday = startdate.strftime('%Y-%m-%d')
        ffuture = enddate.strftime('%Y-%m-%d')
        ser = {'user': username, 'start': ftoday, 'end': ffuture, 'subtype': usertye}
        subscriptionperiod_collection.update_one({'user': username}, {'$set': ser})
    else:
        today_date = datetime.datetime.now().date()
        future_date = today_date + datetime.timedelta(days=int(duration))
        ftoday = today_date.strftime('%Y-%m-%d')
        ffuture = future_date.strftime('%Y-%m-%d')
        ser = {'user': username, 'start': ftoday, 'end': ffuture, 'subtype': "paid"}
        subscriptionperiod_collection.insert_one(ser)

    # Return a success response
    return jsonify({'success': True, 'message': 'Payment verified successfully'})


@app.route('/api_pay_fail', methods=['POST'])
def api_pay_failure():
    # Get the username from the request
    username = request.form.get('token')
    if not username:
        return jsonify({'success': False, 'error': 'Username is required'}), 400

    # Return a failure response
    return jsonify({'success': False, 'error': 'Payment couldn\'t go through and failed due to some reason.'}), 400

@app.route('/add_fractalnubiatimehedge_order_form')
def add_fractalnubiatimehedge_order_form():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    return render_template('add_edit_fractalnubiatimehedgeorder.html',symbols=trader.Mcxlist,row_count=5, action_url=url_for('add_fractalnubiatimehedge_order'))


@app.route('/add_fractalnubiatimehedge_order', methods=['POST'])
def add_fractalnubiatimehedge_order():
    if 'username' not in session:
        return redirect(url_for('login'))
    print('newww')
    print(dict(request.form))
    print('newww')

    if request.form:
        options = request.form.getlist('ooption')
        strikes = request.form.getlist('ostrike')
        sides = request.form.getlist('oside')
        expiries = request.form.getlist('oexpiry')
        lots = request.form.getlist('olot')

        # Combine data into a list of dictionaries
        data = [
            {'option': option, 'strike': strike, 'side': side, 'expiry': expiry,'lot':lot}
            for option, strike, side, expiry,lot in zip(options, strikes, sides, expiries,lots)
        ]

        # Debugging: Print the data
        print(data)

        k = (dict(request.form))
        k['exittime']=int(datetime.datetime.now().timestamp())
        k['user']=session['username']
        k['legs']=data
        print(users_collection.find_one({'username': k['user']}))
        k['botcode'] = create_botcode(k['user'], k['botname'])
        print(k['botcode'])
        
        j = FRACTALNUBIATIMEHEDGEORDER_mode(k)
        print(j)
        #orders_collection.insert_one(j.__dict__)
        strategy_collection.insert_one(j.__dict__)
        j = j.__dict__
        print(j)
        #trader.fakeorders[j['time']]=j
        #trader.breakoutstrats[j['time']] = HuntLevel(trader, j['trigger_price'], j['trigger_type'], j['symbol'], j['comparator_type'],                                             j['option_type'], j['strike'], j['lot'], j['trail'], j['trail_stoploss'], j['tp_1'], j['tp_2'], j['sl'], j['strike'], j['time']) 
    return redirect(url_for('index'))



@app.route('/edit_fractalnubiatimehedgeorder/<string:order_time>', methods=['POST'])
def edit_fractalnubiatimehedgeorder(order_time):
    if 'username' not in session:
        return redirect(url_for('login'))
    existing_strategy = strategy_collection.find_one(
        {'botcode': order_time, 'user': session['username']}
    )
    options = request.form.getlist('ooption')
    strikes = request.form.getlist('ostrike')
    sides = request.form.getlist('oside')
    expiries = request.form.getlist('oexpiry')
    lots = request.form.getlist('olot')

    # Combine data into a list of dictionaries
    data = [
        {'option': option, 'strike': strike, 'side': side, 'expiry': expiry,'lot':lot}
        for option, strike, side, expiry,lot in zip(options, strikes, sides, expiries,lots)
    ]

    # Debugging: Print the data
    print(data)
    k = (dict(request.form))
    method_values = request.form.getlist('method')
    if method_values:
        k['method'] = method_values[-1]
    #print('###############supreeem###########')
    k['user']=session['username']
    #print(k)
    k['legs']=data
    k['exittime']=int(datetime.datetime.now().timestamp())
    print(k)
    j = FRACTALNUBIATIMEHEDGEORDER_mode(k)
    #print('##############ultimatem###############')
    #print(j.__dict__)
    update_doc = {'$set': j.__dict__}
    if existing_strategy:
        if (
            existing_strategy.get('legs', []) != j.__dict__.get('legs', [])
            or existing_strategy.get('method') != j.__dict__.get('method')
        ):
            update_doc = _fractal_reset_update(
                order_time,
                session['username'],
                j.__dict__
            )
    strategy_collection.update_one({'botcode': order_time,'user':session['username']}, update_doc)
    j = j.__dict__
    #trader.fakeorders[j['time']]=j
    return redirect(url_for('index'))


@app.route('/privacypolicy')
def privacypolicy():
    return render_template('piracypolicy.html')

@app.route('/forgot_reset_password', methods=['GET', 'POST'])
def forgot_reset_password():
    if request.method == 'POST':
        email = str(request.form['email']).lower()
        users = get_user_collection()
        user = users.find_one({'email': email})

        if user:
            if ('reset_token' not in user):
                # Generate a unique token and save it to the user
                reset_token = generate_reset_token()
                users.update_one({'_id': user['_id']}, {'$set': {'reset_token': reset_token}})

                # Send an email with the reset link
                send_reset_email(email, reset_token)

                return 'An email with instructions to reset your password has been sent to your email address.'
            elif ('reset_token'  in user) and user['reset_token']==None:
                # Generate a unique token and save it to the user
                reset_token = generate_reset_token()
                users.update_one({'_id': user['_id']}, {'$set': {'reset_token': reset_token}})

                # Send an email with the reset link
                send_reset_email(email, reset_token)

                return 'An email with instructions to reset your password has been sent to your email address.'
            else:
                reset_token = generate_reset_token()
                users.update_one({'_id': user['_id']}, {'$set': {'reset_token': reset_token}})

                # Send an email with the reset link
                send_reset_email(email, reset_token)
                return 'A password reset email has already been sent. Check your email.'
        else:
            return 'No user found with that email address.'

    return render_template('forgot_reset_password.html')

@app.route('/reset_password/<reset_token>', methods=['GET', 'POST'])
def reset_password(reset_token):
    users = get_user_collection()
    user = users.find_one({'reset_token': reset_token})

    if user:
        if request.method == 'POST':
            new_password = request.form['new_password']
            confirm_password = request.form['confirm_password']

            if new_password == confirm_password:
                # Update the password and remove the reset token
                hashpass = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
                users.update_one({'_id': user['_id']}, {'$set': {'password': hashpass, 'reset_token': None}})
                return 'Your password has been successfully reset. You can now log in with your new password.',401

            return 'Passwords do not match.',405

        return render_template('reset_password.html', reset_token=reset_token)

    return 'Invalid or expired reset token.',401

###########################################################


@app.route('/api_historicalbacktest',methods=['POST'])
def api_historicalbacktest():
    if request.method=='POST':        
        # Get the start date parameter from the request
        start_selected_date = request.args.get('date') or request.args.get('start_date')

        if not start_selected_date:
            start_selected_date = str(datetime.datetime.now().date())

        # Check if start_selected_date is valid before parsing it
        try:
            start_date = datetime.datetime.strptime(start_selected_date, "%Y-%m-%d")
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid start date format. Please use YYYY-MM-DD.'}), 400

        # Get the end date parameter from the request
        end_selected_date = request.args.get('end_date')

        if not end_selected_date:
            end_selected_date = str(datetime.datetime.now().date())

        # Check if end_selected_date is valid before parsing it
        try:
            end_date = datetime.datetime.strptime(end_selected_date, "%Y-%m-%d") + datetime.timedelta(days=1)
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid end date format. Please use YYYY-MM-DD.'}), 400

        # Get data between start and end dates
        data, pnl = historicalbacktestget_data(request.form['token'], int(start_date.timestamp()), int(end_date.timestamp()))
        
        return {'success': True, 'message': 'Successfully Fetched User History', 'data': {
            'history': data,
            'selected_start_date': start_selected_date,
            'selected_end_date': end_selected_date,
            'pnl': pnl
        }}







@app.route('/api_mainhistoricalbacktest')
def api_mainhistoricalbacktest():
    # Get the start date parameter from the request
    start_selected_date = request.args.get('date') or request.args.get('start_date')

    if not start_selected_date:
        start_selected_date = str(datetime.datetime.now().date())

    # Check if start_selected_date is valid before parsing it
    try:
        start_date = datetime.datetime.strptime(start_selected_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid start date format. Please use YYYY-MM-DD.'}), 400

    # Get the end date parameter from the request
    end_selected_date = request.args.get('end_date')

    if not end_selected_date:
        end_selected_date = str(datetime.datetime.now().date())

    # Check if end_selected_date is valid before parsing it
    try:
        end_date = datetime.datetime.strptime(end_selected_date, "%Y-%m-%d") + datetime.timedelta(days=1)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid end date format. Please use YYYY-MM-DD.'}), 400

    # Get data between start and end dates
    data = mainhistoricalbacktestget_data("kinguniverse129", int(start_date.timestamp()), int(end_date.timestamp()))
    
    return {'success': True, 'message': 'Successfully Fetched Main History', 'data': {
        'history': data,
        'selected_start_date': start_selected_date,
        'selected_end_date': end_selected_date
    }}


@app.route('/api_users', methods=['POST'])
def api_get_users():
    if request.method == 'POST':
        token = request.form.get('token')
        
        if not token:
            return jsonify({'success': False, 'message': 'Token is required'}), 401
            
        user = users_collection.find_one({'username': token})
        
        if not user or 'admin' not in user:
            return jsonify({'success': False, 'message': 'Unauthorized Access'}), 403
            
        try:
            users = list(users_collection.find())
            for i in range(len(users)):
                users[i]['id'] = str(users[i]['_id'])
                del users[i]['_id'], users[i]['password']
                
                if 'StrategyLimit' not in users[i]:
                    users[i]['StrategyLimit'] = 10
                    users_collection.update_one(
                        {"username": users[i]['username']}, 
                        {"$set": {"StrategyLimit": 10}}
                    )
                    
            return jsonify({
                'success': True, 
                'message': 'Users fetched successfully', 
                'data': users
            })
            
        except Exception as e:
            return jsonify({
                'success': False, 
                'message': f'Error fetching users: {str(e)}'
            }), 500
    
    return jsonify({'success': False, 'message': 'Method not allowed'}), 405

@app.route('/api_update_user/<user_id>', methods=[ 'POST'])
def api_update_user(user_id):
	if request.method=='POST':
		user =users_collection.find_one({'username' : request.form['token']})
		if user and 'admin' in user:
			data = {
			"username": request.form['username'],
			"email": request.form['email'],

			"mobile": request.form['mobile']
			,"StrategyLimit":request.form.get('StrategyLimit',10)
			}
			if 'StrategyLimit' not in data:
				data['StrategyLimit']=10


			users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": data})
			return {'success':True,'message':'updated Successfully User','data':data}
		return {'success':False,'message':'Unauthorize Access'},401




# Delete operation for users
@app.route('/api_delete_user/<user_id>', methods=['POST'])
def api_delete_user(user_id):
    token = request.form.get('token')

    # Check if token is provided
    if not token:
        return jsonify({'success': False, 'message': 'Token is required'}), 400

    # Find the user by token (username in this case)
    user = users_collection.find_one({'username': token})

    # Check if user exists and is an admin
    if not user or 'admin' not in user:
        return jsonify({'success': False, 'message': 'Unauthorized Access'}), 403

    # Attempt to delete the specified user
    result = users_collection.delete_one({"_id": ObjectId(user_id)})

    if result.deleted_count > 0:
        return jsonify({'success': True, 'message': 'Successfully Deleted User'})
    else:
        return jsonify({'success': False, 'message': 'User not found'}), 404


@app.route('/api_apis', methods=['POST'])
def api_get_apis():

    if request.method=='POST':
        user =users_collection.find_one({'username' : request.form['token']})
        if user and 'admin' in user:
            apis = list(apikeys_collection.find())
            for i in range(0,len(apis)):
                apis[i]['_id']=str(apis[i]['_id'])
            return {'success':True,'message':'Fetched Successfully APIs','data':apis}
    return {'success':False,'message':'Unauthorize Access'},401



@app.route('/api_strategys', methods=['POST'])
def api_get_strategy():
    if request.method=='POST':
        user =users_collection.find_one({'username' : request.form['token']})
        if user and 'admin' in user:
            strategy= list(strategy_collection.find({'$or': [{'status': {'$in': ['opened', 'paused']}}]}))
            for i in range(0,len(strategy)):
                strategy[i]['_id']=str(strategy[i]['_id'])
            return {'success':True,'message':'Fetched Successfully Strategies','data':strategy}
    return {'success':False,'message':'Unauthorize Access'},401

@app.route('/api_get_api', methods=['POST'])
def api_get_api():
    try:
        if request.method == 'POST':
            # Validate the ObjectId
            api = apikeys_collection.find_one({"user": request.form['token']})
            if api is None:
                return jsonify({'success': False, 'message': 'API not found'}), 404
            api['_id'] = str(api['_id'])  # Convert ObjectId to string for JSON serialization
            return jsonify({'success': True, 'message': 'Fetched Successfully API', 'data': api}), 200
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'success': False, 'message': 'Internal Server Error'}), 500

@app.route('/api_update_api', methods=['POST'])
def api_update_api():
    if request.method == 'POST':
        user =users_collection.find_one({'username' : request.form['token']})
        if user and 'admin' in user:
            data = {
                "apikey": request.form['apikey'],
                "apisecret": request.form['apisecret'],
                "user": request.form['token']
            }
            if 'auth_code' in request.form:
                data["auth_code"] = request.form['auth_code']

            if not ObjectId.is_valid(request.form['id']):
                return jsonify({'success': False, 'message': 'Invalid API ID format'}), 400
            apikeys_collection.update_one({"_id": ObjectId(request.form['id'])}, {"$set": data})
            return {'success':True,'message':'Fetched Successfully Updated API'}
        return {'success':False,'message':'Unauthorize Access'},401

@app.route('/api_multi_api', methods=['POST'])
def api_multi_api():
    try:
        if request.method == 'POST':
            # Get the operation type from the request
            operation = request.form.get('operation', 'get')
            
            # GET operation
            if operation == 'get':
                # Validate required parameters
                if 'token' not in request.form or 'broker' not in request.form:
                    return jsonify({'success': False, 'message': 'Missing required parameters (token, broker)'}), 400
                
                # Find the API for the user and broker
                api = apikeys_collection.find_one({"user": request.form['token'], 'broker': request.form['broker']})
                if api is None:
                    return jsonify({'success': False, 'message': 'API not found'}), 404
                
                # Convert ObjectId to string for JSON serialization
                api['_id'] = str(api['_id'])
                return jsonify({'success': True, 'message': 'Fetched Successfully API', 'data': api}), 200
            
            # UPDATE operation
            elif operation == 'update':
                # Validate required parameters
                if 'token' not in request.form or 'broker' not in request.form:
                    return jsonify({'success': False, 'message': 'Missing required parameters (token, broker)'}), 400
                
                # Create a copy of the form data and set the user field
                data = dict(request.form)
                data['user'] = data['token']
                
                # Remove operation field from the data to be updated
                if 'operation' in data:
                    del data['operation']
                
                # Update the API document using token and broker instead of ID
                result = apikeys_collection.update_one(
                    {"user": request.form['token'], "broker": request.form['broker']}, 
                    {"$set": data}
                )
                
                if result.modified_count > 0:
                    return jsonify({'success': True, 'message': 'Successfully Updated API'}), 200
                elif result.matched_count > 0:
                    return jsonify({'success': True, 'message': 'No changes were made to the API'}), 200
                else:
                    # If no document matched, create a new one (upsert)
                    apikeys_collection.insert_one(data)
                    return jsonify({'success': True, 'message': 'Successfully Created API'}), 201
            
            # Invalid operation
            else:
                return jsonify({'success': False, 'message': f'Invalid operation: {operation}'}), 400
                
    except Exception as e:
        print(f"Error in api_multi_api: {e}")
        return jsonify({'success': False, 'message': 'Internal Server Error'}), 500
@app.route('/api_broker_multi_api', methods=['POST'])
def api_broker_multi_api():
    payload = broker_payload()
    broker_requirements = payload['broker_requirements']
    broker_actions = payload['broker_actions']
    broker_display_names = payload['broker_display_names']
    broker_status = payload['broker_status']
    
    # Get user token from request
    token = request.form.get('token')
    
    # Get current broker for the user if token is provided
    current_broker = 'aliceblue'  # Default broker
    if token:
        # Try to find the user's broker settings
        broker_data = broker_collection.find_one({'user': token})
        
        if broker_data and 'selectedbroker' in broker_data:
            current_broker = broker_data['selectedbroker']
        else:
            # Create default broker entry if not found
            default_broker_data = {
                'user': token,
                'selectedbroker': current_broker
            }
            broker_collection.update_one(
                {'user': token},
                {'$set': default_broker_data},
                upsert=True
            )
    
    # Check if a specific broker was requested
    requested_broker = request.form.get('selectedbroker')
    
    # Prepare response data
    response_data = {
        'success': True,
        'message': 'Successfully fetched broker requirements',
        'data': {
            'broker_requirements': broker_requirements,
            'broker_actions': broker_actions,
            'broker_display_names': broker_display_names,
            'broker_status': broker_status,
            'current_broker': current_broker
        }
    }
    
    # If a specific broker was requested, only return that broker's requirements
    if requested_broker and requested_broker in broker_requirements:
        response_data['data'] = {
            'broker_requirements': {requested_broker: broker_requirements[requested_broker]},
            'broker_actions': {requested_broker: broker_actions.get(requested_broker, {})},
            'broker_display_names': {requested_broker: broker_display_names.get(requested_broker, requested_broker)},
            'broker_status': {requested_broker: broker_status.get(requested_broker, {})},
            'current_broker': current_broker
        }
    
    return jsonify(response_data)
@app.route('/api_delete_api', methods=['POST'])
def api_delete_api():
    if request.method == 'POST':
        token = request.form.get('token')
        api_id = request.form.get('id')
        
        if not token or not api_id:
            return jsonify({'success': False, 'message': 'Token and ID are required'}), 400
        
        user = users_collection.find_one({'username': token})
        
        if user and 'admin' in user:
            result = apikeys_collection.delete_one({"_id": ObjectId(api_id)})
            if result.deleted_count > 0:
                return jsonify({'success': True, 'message': 'API Key Deleted Successfully'})
            else:
                return jsonify({'success': False, 'message': 'API ID not found'}), 404
        else:
            return jsonify({'success': False, 'message': 'Unauthorized or invalid token'}), 403

    return jsonify({'success': False, 'message': 'Method not allowed'}), 405


@app.route('/api_admin', methods=['POST'])
def api_admin():
    try:
        # Check if token is provided
        token = request.form.get('token')
        if not token:
            return generate_response('Authentication token is missing', success=False, status_code=401)
        
        # Check if user exists and has admin privileges
        user = get_user_from_token(token)
        #print(f"User fetched: {user}")  # Debugging
        if not user or 'admin' not in user:
            return generate_response('Unauthorized Access', success=False, status_code=403)

        # Fetch admin controls and strategies
        admin_controls = list(admincontrol_collection.find({}, {'_id': 0}))
        #print(f"Admin Controls: {admin_controls}")  # Debugging
        
        strategies = list(strategyinput_collection.find({}, {'_id': 0}))
        #print(f"Strategies: {strategies}")  # Debugging
        
        data = {
            'controls': admin_controls,
            'strategyco': strategies
        }

        # Return the response
        return {'message':'Successfully fetched Admin Page', 'data':data,'success':True}
    
    except Exception as e:
        print(f"Error occurred: {e}")  # Debugging
        return generate_response('Internal Server Error', success=False, status_code=500)
  
@app.route('/api_subscription', methods=['POST'])
def api_get_subscriptions():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()

        if not token:
            return jsonify({'success': False, 'message': 'Token is required.'}), 400

        user = users_collection.find_one({'username': token})
        if user and user.get('admin'):
            subscriptions = list(subscriptionperiod_collection.find({}))
            for subscription in subscriptions:
                subscription['_id'] = str(subscription['_id'])
            
            return jsonify({
                'success': True,
                'message': 'Successfully fetched subscription data.',
                'data': subscriptions
            })

    return jsonify({'success': False, 'message': 'Unauthorized access.'}), 403


@app.route('/api_create_subscription', methods=['POST'])
def api_create_subscription():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        user = request.form.get('user', '').strip()
        start = request.form.get('start', '').strip()
        end = request.form.get('end', '').strip()
        subtype = request.form.get('subtype', '').strip()

        if not token or not user or not start or not end or not subtype:
            return jsonify({'success': False, 'message': 'All fields are required.'}), 400

        admin_user = users_collection.find_one({'username': token})
        if admin_user and 'admin' in admin_user:
            subscription_data = {
                'user': user,
                'start': start,
                'end': end,
                'subtype': subtype
            }

            subscription_id = subscriptionperiod_collection.insert_one(subscription_data).inserted_id
            return jsonify({'success': True, 'message': 'Successfully created subscription.', 'data': str(subscription_id)})

    return jsonify({'success': False, 'message': 'Unauthorized access.'}), 403


@app.route('/api_get_subscription', methods=['POST'])
def api_get_subscription():
    if request.method == 'POST':
        subscription_id = request.form.get('id', '').strip()

        if not subscription_id:
            return jsonify({'success': False, 'message': 'Subscription ID is required.'}), 400

        try:
            subscription = subscriptionperiod_collection.find_one({"_id": ObjectId(subscription_id)})
            if subscription:
                subscription['_id'] = str(subscription['_id'])
                return jsonify({'success': True, 'message': 'Successfully fetched subscription.', 'data': subscription})
            else:
                return jsonify({'success': False, 'message': 'Subscription not found.', 'data': None}), 404
        except Exception as e:
            return jsonify({'success': False, 'message': 'Invalid subscription ID format.'}), 400


@app.route('/api_update_subscription', methods=['POST'])
def api_update_subscription():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        subscription_id = request.form.get('id', '').strip()
        start = request.form.get('start', '').strip()
        end = request.form.get('end', '').strip()
        subtype = request.form.get('subtype', '').strip()

        if not token or not subscription_id or not start or not end or not subtype:
            return jsonify({'success': False, 'message': 'All fields are required.'}), 400

        admin_user = users_collection.find_one({'username': token})
        if admin_user and 'admin' in admin_user:
            try:
                data = {
                    "start": start,
                    "end": end,
                    "subtype": subtype
                }
                result = subscriptionperiod_collection.update_one(
                    {"_id": ObjectId(subscription_id)},
                    {"$set": data}
                )
                if result.matched_count > 0:
                    return jsonify({'success': True, 'message': 'Successfully updated subscription.'})
                else:
                    return jsonify({'success': False, 'message': 'Subscription not found.'}), 404
            except Exception as e:
                return jsonify({'success': False, 'message': 'Invalid subscription ID format.'}), 400

    return jsonify({'success': False, 'message': 'Unauthorized access.'}), 403



@app.route('/api_delete_subscription', methods=['POST'])
def api_delete_subscription():
    if request.method == 'POST':
        token = request.form.get('token')
        api_id = request.form.get('id')
        
        if not token or not api_id:
            return jsonify({'success': False, 'message': 'Token and ID are required'}), 400
        
        user = users_collection.find_one({'username': token})
        
        if user and 'admin' in user:
            result = subscriptionperiod_collection.delete_one({"_id": ObjectId(api_id)})
            if result.deleted_count > 0:
                return jsonify({'success': True, 'message': 'Subscription  Deleted Successfully'})
            else:
                return jsonify({'success': False, 'message': 'Subscription ID not found'}), 404
        else:
            return jsonify({'success': False, 'message': 'Unauthorized or invalid token'}), 403

    return jsonify({'success': False, 'message': 'Method not allowed'}), 405


##########

@app.route('/api_stop_ssalgo', methods=['POST'])
def api_stop_ssalgo():
    if request.method=='POST':
        user =users_collection.find_one({'username' : request.form['token']})
        if user:
            strategy_collection.update_one({'botcode':request.form['id'],'user':request.form['token']}, {'$set': {'status':'paused'}})
            return {'success':True,'message':"Successfully Stop SSALGO Strategy"}
    return {'success':False,'message':'Unauthorize access'} 
@app.route('/api_start_ssalgo', methods=['POST'])
def api_start_ssalgo():
    if request.method == 'POST':
        token = request.form.get('token')
        botcode = request.form.get('id')
        
        if not token or not botcode:
            return {'success': False, 'message': 'Missing token or botcode'}, 400

        user = users_collection.find_one({'username': token})
        if user:
            strategy_collection.update_one(
                {'botcode': botcode, 'user': token}, 
                _fractal_reset_update(botcode, token, {'status': 'opened'})
            )
            return {'success': True, 'message': 'Successfully started SSALGO strategy'}, 200

        return {'success': False, 'message': 'Unauthorized access'}, 401
@app.route('/api_stop_admin_ssalgo', methods=['POST'])
def api_stop_admin_ssalgo():
    if request.method == 'POST':
        token = request.form.get('token')
        botcode = request.form.get('id')

        if not token or not botcode:
            return {'success': False, 'message': 'Missing token or botcode'}, 400

        user = users_collection.find_one({'username': token})
        if user and 'admin' in user:
            # Verify if the strategy exists
            strategy = strategy_collection.find_one({'botcode': botcode})
            if strategy:
                strategy_collection.update_one({'botcode': botcode}, {'$set': {'status': 'paused'}})
                return {'success': True, 'message': 'Successfully stopped SSALGO strategy'}, 200
            else:
                return {'success': False, 'message': 'Strategy not found'}, 404

    return {'success': False, 'message': 'Unauthorized access'}, 401


@app.route('/api_start_admin_ssalgo', methods=['POST'])
def api_start_admin_ssalgo():
    if request.method == 'POST':
        token = request.form.get('token')
        botcode = request.form.get('id')

        if not token or not botcode:
            return {'success': False, 'message': 'Missing token or botcode'}, 400

        user = users_collection.find_one({'username': token})
        if user and 'admin' in user:
            # Verify if the strategy exists
            strategy = strategy_collection.find_one({'botcode': botcode})
            if strategy:
                strategy_collection.update_one(
                    {'botcode': botcode},
                    _fractal_reset_update(botcode, None, {'status': 'opened'})
                )
                return {'success': True, 'message': 'Successfully started SSALGO strategy'}, 200
            else:
                return {'success': False, 'message': 'Strategy not found'}, 404

    return {'success': False, 'message': 'Unauthorized access'}, 401
from flask import request, jsonify

@app.route('/api_start_control', methods=['POST'])
def api_start_control():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        symbol = request.form.get('symbol', '').strip()

        if not token or not symbol:
            return jsonify({"success": False, "message": "Token and symbol are required."}), 400

        user = users_collection.find_one({'username': token})
        if not user or 'admin' not in user:
            return jsonify({"success": False, "message": "Unauthorized access. Admin privileges required."}), 403

        admincontrol_collection.update_one(
            {'symbol': symbol},
            {'$set': {'controlmode': True}}
        )
        trader.controls[symbol] = admincontrol_collection.find_one({'symbol': symbol})

        return jsonify({'success': True, 'message': "Successfully started control."})

    return jsonify({"success": False, "message": "Invalid request method."}), 405


@app.route('/api_stop_control', methods=['POST'])
def api_stop_control():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        symbol = request.form.get('symbol', '').strip()

        if not token or not symbol:
            return jsonify({"success": False, "message": "Token and symbol are required."}), 400

        user = users_collection.find_one({'username': token})
        if not user or 'admin' not in user:
            return jsonify({"success": False, "message": "Unauthorized access. Admin privileges required."}), 403

        admincontrol_collection.update_one(
            {'symbol': symbol},
            {'$set': {'controlmode': False}}
        )
        trader.controls[symbol] = admincontrol_collection.find_one({'symbol': symbol})

        return jsonify({'success': True, 'message': "Successfully stopped control."})

    return jsonify({"success": False, "message": "Invalid request method."}), 405


@app.route('/api_start_cebuy', methods=['POST'])
def api_start_cebuy():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        symbol = request.form.get('symbol', '').strip()

        if not token or not symbol:
            return jsonify({"success": False, "message": "Token and symbol are required."}), 400

        user = users_collection.find_one({'username': token})
        if not user or 'admin' not in user:
            return jsonify({"success": False, "message": "Unauthorized access. Admin privileges required."}), 403

        admincontrol_collection.update_one(
            {'symbol': symbol},
            {'$set': {'Buytrade': True}}
        )
        trader.controls[symbol] = admincontrol_collection.find_one({'symbol': symbol})

        return jsonify({'success': True, 'message': "Successfully triggered CE buy."})

    return jsonify({"success": False, "message": "Invalid request method."}), 405


@app.route('/api_start_cesell', methods=['POST'])
def api_start_cesell():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        symbol = request.form.get('symbol', '').strip()

        if not token or not symbol:
            return jsonify({"success": False, "message": "Token and symbol are required."}), 400

        user = users_collection.find_one({'username': token})
        if not user or 'admin' not in user:
            return jsonify({"success": False, "message": "Unauthorized access. Admin privileges required."}), 403

        admincontrol_collection.update_one(
            {'symbol': symbol},
            {'$set': {'Buytrade': False}}
        )
        trader.controls[symbol] = admincontrol_collection.find_one({'symbol': symbol})

        return jsonify({'success': True, 'message': "Successfully triggered CE sell."})

    return jsonify({"success": False, "message": "Invalid request method."}), 405


@app.route('/api_start_pebuy', methods=['POST'])
def api_start_pebuy():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        symbol = request.form.get('symbol', '').strip()

        if not token or not symbol:
            return jsonify({"success": False, "message": "Token and symbol are required."}), 400

        user = users_collection.find_one({'username': token})
        if not user or 'admin' not in user:
            return jsonify({"success": False, "message": "Unauthorized access. Admin privileges required."}), 403

        admincontrol_collection.update_one(
            {'symbol': symbol},
            {'$set': {'Selltrade': True}}
        )
        trader.controls[symbol] = admincontrol_collection.find_one({'symbol': symbol})

        return jsonify({'success': True, 'message': "Successfully triggered PE buy."})

    return jsonify({"success": False, "message": "Invalid request method."}), 405


@app.route('/api_start_pesell', methods=['POST'])
def api_start_pesell():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        symbol = request.form.get('symbol', '').strip()

        if not token or not symbol:
            return jsonify({"success": False, "message": "Token and symbol are required."}), 400

        user = users_collection.find_one({'username': token})
        if not user or 'admin' not in user:
            return jsonify({"success": False, "message": "Unauthorized access. Admin privileges required."}), 403

        admincontrol_collection.update_one(
            {'symbol': symbol},
            {'$set': {'Selltrade': False}}
        )
        trader.controls[symbol] = admincontrol_collection.find_one({'symbol': symbol})

        return jsonify({'success': True, 'message': "Successfully triggered PE sell."})

    return jsonify({"success": False, "message": "Invalid request method."}), 405

@app.route('/api_start_strategyco', methods=['POST'])
def api_start_strategyco():
    # Get token and strategy from request.form
    token = request.form.get('token')
    strategy = request.form.get('strategy')

    # Ensure token and strategy are provided
    if not token or not strategy:
        return jsonify({'success': False, 'error': 'Missing token or strategy'}), 400

    # Validate the user based on the token
    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return jsonify({'success': False, 'error': 'Unauthorized access'}), 403

    strategy1 = strategyinput_collection.find_one({'strategy': strategy})
    if not strategy1:
        return jsonify({'success': False, 'error': 'Strategy not found'}), 404

    # Update strategy to start
    strategyinput_collection.update_one({'strategy': strategy}, {'$set': {'update': True}})
    trader.strategyinputs[strategy] = strategyinput_collection.find_one({'strategy': strategy})

    return jsonify({'success': True, 'message': "Successfully started the strategy."})

@app.route('/api_stop_strategyco', methods=['POST'])
def api_stop_strategyco():
    # Get token and strategy from request.form
    token = request.form.get('token')
    strategy = request.form.get('strategy')

    # Ensure token and strategy are provided
    if not token or not strategy:
        return jsonify({'success': False, 'error': 'Missing token or strategy'}), 400

    # Validate the user based on the token
    user = get_user_from_token(token)
    if not user or 'admin' not in user:
        return jsonify({'success': False, 'error': 'Unauthorized access'}), 403
    strategy1 = strategyinput_collection.find_one({'strategy': strategy})
    if not strategy1:
        return jsonify({'success': False, 'error': 'Strategy not found'}), 404

    # Update strategy to stop
    strategyinput_collection.update_one({'strategy': strategy}, {'$set': {'update': False}})
    trader.strategyinputs[strategy] = strategyinput_collection.find_one({'strategy': strategy})

    return jsonify({'success': True, 'message': "Successfully stopped the strategy."})



@app.route('/api_delete_admin_ssalgo', methods=['POST'])
def api_delete_admin_ssalgo():
    if request.method == 'POST':
        # Extract and sanitize token and strategy ID
        token = request.form.get('token', '').strip()
        strategy_id = request.form.get('id', '').strip()

        if not token or not strategy_id:
            return {"success": False, "message": "Token and strategy ID are required."}, 400

        # Find the user associated with the token
        user = users_collection.find_one({'username': token})
        if not user or not user.get('admin'):
            return {"success": False, "message": "Unauthorized access. Admin privileges required."}, 403

        # Update the strategy status to 'closed'
        result = strategy_collection.update_one(
            {'botcode': strategy_id},
            {'$set': {'status': 'closed'}}
        )

        if result.matched_count > 0:
            return {"success": True, "message": "Successfully closed the strategy."}
        else:
            return {"success": False, "message": "Strategy not found."}, 404

    return {"success": False, "message": "Invalid request method."}, 405



@app.route('/api_delete_strategy', methods=['POST'])
def api_delete_strategy():
    if request.method == 'POST':
        # Extract and sanitize token and strategy ID
        token = request.form.get('token', '').strip()
        strategy_id = request.form.get('id', '').strip()

        if not token or not strategy_id:
            return {"success": False, "message": "Token and strategy ID are required."}, 400

        # Find the user associated with the token
        user = users_collection.find_one({'username': token})
        if not user:
            return {"success": False, "message": "Unauthorized access. User not found."}, 403

        # Update the strategy status to 'closed'
        result = strategy_collection.update_one(
            {'botcode': strategy_id, 'user': token},
            {'$set': {'status': 'closed'}}
        )

        if result.matched_count > 0:
            return {"success": True, "message": "Successfully closed the strategy."}
        else:
            return {"success": False, "message": "Strategy not found or you don't have permission to close it."}, 404

    return {"success": False, "message": "Invalid request method."}, 405



















if __name__ == '__main__':
    #redine()
    #redine1()
    ssl_context = None
    if AppConfig.SSL_CERT_FILE and AppConfig.SSL_KEY_FILE:
        ssl_context = (AppConfig.SSL_CERT_FILE, AppConfig.SSL_KEY_FILE)
    #app.run(host='0.0.0.0',port=5001,debug=False)
    #app.run(debug=True,port=5000)
    #socketio.run(app)
    socketio.run(app, host='0.0.0.0', port=8443, debug=False, ssl_context=ssl_context)
    
    #app.run(host='0.0.0.0',ssl_context=ssl_context, port=443,debug=False)
    #serve(app, host='0.0.0.0', port=50100, threads=1)
    #ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    #ssl_context.load_cert_chain('cert.pem', 'key.pem')

    # Serve your Flask app using Waitress with SSL
    #serve(app, host='0.0.0.0', port=443)#, ssl_context=ssl_context)
