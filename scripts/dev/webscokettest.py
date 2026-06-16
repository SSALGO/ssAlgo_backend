import json
import pandas as pd
import time
tokdf=pd.read_csv('https://developers.stocknote.com/doc/ScripMaster.csv')  
print(tokdf['instrument'].unique())
print(tokdf[tokdf['instrument']=='EQ'])
symlist=list(tokdf[(tokdf['exchange']=='NFO') & (tokdf['name']=='BANKNIFTY')]['symbolCode'])
print(symlist)
df_symbols = pd.DataFrame({"symbol": symlist})
# Convert the DataFrame to a JSON string
json_data = df_symbols.to_json(orient="records")
#print(json_data)
print(len(json_data))
#time.sleep(50)
request_body = {
    "streaming_type": "quote",
    "data":{    "symbols": symlist},
    "request_type": "subscribe",
    "response_format": "JSON"
}

# Convert the dictionary to a JSON string
json_request_body = json.dumps(request_body)

#print(json_request_body)
import json
import requests
import rel
import os
headers = {
'Content-Type': 'application/json',
'Accept': 'application/json'
}
requestBody={
"userId": os.getenv("SSLAGO_STOCKNOTE_USER_ID", ""),
"password": os.getenv("SSLAGO_STOCKNOTE_PASSWORD", ""),
"yob": os.getenv("SSLAGO_STOCKNOTE_YOB", "")
}
if not all(requestBody.values()):
    raise SystemExit("Set SSLAGO_STOCKNOTE_USER_ID, SSLAGO_STOCKNOTE_PASSWORD, and SSLAGO_STOCKNOTE_YOB")
prices={}
r = requests.post('https://api.stocknote.com/login'
, data=json.dumps(requestBody)
, headers = headers)

print (r.json())
sessiontoken=r.json()['sessionToken']
import websocket

def on_message(ws, msg):
    k=json.loads(msg)
    #print ("Message Arrived:" + k)
    
    #print(k['response'])
    prices[k['response']['data']['sym']]=float(k['response']['data']['ltp'])
    #print(prices)
    print(len(list(prices.keys())))

def on_error(ws, error):
    print (error)

def on_close(ws):
    print ("Connection Closed")

def on_open(ws):
    print ("Sending json")
    #data='{"request":{"streaming_type":"quote", "data":{"symbols":'+json.dumps(symlist)+'}, "request_type":"subscribe", "response_format":"json"}}'[{"symbol":"26000_NSE"},{"symbol":"426247_MFO"}]
    data = (
        '{"request":{"streaming_type":"quote", "data":{"symbols":'
        +json_data
        + '},"request_type":"subscribe", "response_format":"json"}}'
    )
    #print("data)
    ws.send(data)
    ws.send("\n")

headers = {'x-session-token':sessiontoken}

#websocket.enableTrace(True)

ws = websocket.WebSocketApp("wss://stream.stocknote.com", on_open = on_open, on_message = on_message, on_error = on_error, on_close = on_close, header = headers)
import threading
ws.run_forever(dispatcher=rel,reconnect=5)
#t1=threading.Thread(target=)
#t1.start()
rel.signal(2, rel.abort)  # Keyboard Interrupt
rel.dispatch()
import time
time.sleep(10)
ws.close()
