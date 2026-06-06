import pandas as pd
import json
import os
import requests
import websocket  # Add this line to import the websocket module

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
    raise SystemExit("Set SSLAGO_STOCKNOTE_USER_ID, SSLAGO_STOCKNOTE_PASSWORD, and SSLAGO_STOCKNOTE_YOB")

r = requests.post('https://api.stocknote.com/login', data=json.dumps(requestBody), headers=headers)

print(r.json())
sessiontoken = r.json()['sessionToken']

def on_message(ws, msg):
    print("Message Arrived:" + msg)

def on_error(ws, error):
    print(error)

def on_close(ws):
    print("Connection Closed")

def on_open(ws):
    print("Sending json")
    data = '{"request":{"streaming_type":"quote", "data":{"symbols":[{"symbol":"532826_BSE"},{"symbol":"26009_NSE"},{"symbol":"26000_NSE"}]}, "request_type":"subscribe", "response_format":"json"}}'
    ws.send(data)
    ws.send("\n")

headers = {'x-session-token': sessiontoken}

websocket.enableTrace(True)
ws = websocket.WebSocketApp("wss://stream.stocknote.com", on_open = on_open, on_message = on_message, on_error = on_error, on_close = on_close, header = headers)

ws.run_forever()
