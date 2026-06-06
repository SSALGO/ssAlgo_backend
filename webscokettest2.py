import json
import pandas as pd
import time
import requests
import websocket
import threading
import os

MAX_RETRIES = 500
retry_count = 0

class StockWebSocket:
    def __init__(self):
        self.prices = {}
        self.ws = None
        self.session_token = None

    def login(self):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        requestBody = {
            "userId": os.getenv("SSLAGO_STOCKNOTE_USER_ID", ""),
            "password": os.getenv("SSLAGO_STOCKNOTE_PASSWORD", ""),
            "yob": os.getenv("SSLAGO_STOCKNOTE_YOB", "")
        }
        if not all(requestBody.values()):
            raise RuntimeError("Set SSLAGO_STOCKNOTE_USER_ID, SSLAGO_STOCKNOTE_PASSWORD, and SSLAGO_STOCKNOTE_YOB")

        try:
            r = requests.post('https://api.stocknote.com/login', data=json.dumps(requestBody), headers=headers)
            r.raise_for_status()
            print(r.json())
            self.session_token = r.json()['sessionToken']
        except requests.exceptions.RequestException as req_err:
            print(f"Error during login request: {req_err}")

    def on_message(self, ws, msg):
        k = json.loads(msg)
        #print(k['response']['data'])
        self.prices[k['response']['data']['sym']] = float(k['response']['data']['ltp'])
        print(self.prices)

    def on_error(self, ws, error):
        print(error)

    def on_close(self, ws):
        print("Connection Closed")

    def on_open(self, ws):
        tokdf = pd.read_csv('https://developers.stocknote.com/doc/ScripMaster.csv')  
        tokdf['expiryDate']=pd.to_datetime(tokdf['expiryDate'])
        tokdf['formatted_date'] = tokdf['expiryDate'].dt.strftime('%d%b%y')
        print(tokdf)
        print(tokdf['exchange'].unique())
        print(tokdf[tokdf['exchange']=='NFO'])
        symlist = list(tokdf[(tokdf['exchange']=='NFO') & (tokdf['name']=='BANKNIFTY')]['symbolCode'])
        print(symlist)
        df_symbols = pd.DataFrame({"symbol": symlist})
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
        global retry_count
        while retry_count < MAX_RETRIES:
            print('kkkkkkkk')
            try:
                self.login()
                headers = {'x-session-token': self.session_token}

                self.ws = websocket.WebSocketApp("wss://stream.stocknote.com", on_open=self.on_open, on_message=self.on_message,
                                                on_error=self.on_error, on_close=self.on_close, header=headers)
                self.ws.run_forever(ping_interval=3, reconnect=5)

            except Exception as e:
                print(f"An error occurred: {e}")

            retry_count += 1
            print(f"Retrying... ({retry_count}/{MAX_RETRIES})")
            time.sleep(5)

# Create an instance of StockWebSocket
stock_ws = StockWebSocket()

# Start the WebSocket thread
websocket_thread = threading.Thread(target=stock_ws.run_websocket)
websocket_thread.start()
