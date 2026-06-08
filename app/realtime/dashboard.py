import json
from typing import Dict, Set

from fastapi import WebSocket


class DashboardConnectionManager:
    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, username: str, websocket: WebSocket):
        await websocket.accept()
        self._connections.setdefault(username, set()).add(websocket)

    def disconnect(self, username: str, websocket: WebSocket):
        connections = self._connections.get(username)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(username, None)

    async def send_user(self, username: str, payload):
        stale = []
        for websocket in self._connections.get(username, set()):
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(username, websocket)

    @staticmethod
    def parse_message(message: str):
        if message == "ping":
            return {"type": "ping"}
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return {"type": "unknown", "raw": message}
        return payload if isinstance(payload, dict) else {"type": "unknown", "raw": payload}
