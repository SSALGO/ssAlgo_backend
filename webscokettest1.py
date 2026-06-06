import json
import pandas as pd
import time
import requests
import websocket
import rel
import threading
import os

MAX_RETRIES = 500
retry_count = 0
g=''
def run_websocket():
    global retry_count,g
    while retry_count < MAX_RETRIES:
        print('kkkkkkkk')
        try:
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

            request_body = {
                "streaming_type": "quote",
                "data": {"symbols": symlist},
                "request_type": "subscribe",
                "response_format": "JSON"
            }

            # Convert the dictionary to a JSON string
            json_request_body = json.dumps(request_body)

            # print(json_request_body)

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

            prices = {}
            try:
                r = requests.post('https://api.stocknote.com/login', data=json.dumps(requestBody), headers=headers)
                r.raise_for_status()
                print(r.json())
                sessiontoken = r.json()['sessionToken']

                def on_message(ws, msg):
                    k = json.loads(msg)
                    print(k['response']['data'])
                    prices[k['response']['data']['sym']] = float(k['response']['data']['ltp'])
                    #print(len(list(prices.keys())))
                    print(prices)

                def on_error(ws, error):
                    print(error)

                def on_close(ws):
                    print("Connection Closed")

                def on_open(ws):
                    print("Sending json")
                    data = (
                        '{"request":{"streaming_type":"quote", "data":{"symbols":'
                        + json_data
                        + '},"request_type":"subscribe", "response_format":"json"}}'
                    )
                    ws.send(data)
                    ws.send("\n")

                headers = {'x-session-token': sessiontoken}

                ws = websocket.WebSocketApp("wss://stream.stocknote.com", on_open=on_open, on_message=on_message,
                                            on_error=on_error, on_close=on_close, header=headers)
                ws.run_forever(ping_interval=3, reconnect=5)

            except requests.exceptions.RequestException as req_err:
                print(f"Error during login request: {req_err}")

        except Exception as e:
            print(f"An error occurred: {e}")

        retry_count += 1
        print(f"Retrying... ({retry_count}/{MAX_RETRIES})")
        time.sleep(5)

# Start the WebSocket thread
websocket_thread = threading.Thread(target=run_websocket)
websocket_thread.start()
