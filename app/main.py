from datetime import datetime
import random
import string
import json
from pydantic import BaseModel

from cachetools import TTLCache
from fastapi import Cookie, Depends, FastAPI, Form, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

team_progress = TTLCache(100, 36000)

templates = Jinja2Templates(directory="templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.team_connections = {}

    async def connect(self, team: int, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        if team in self.team_connections:
            self.team_connections[team].append(websocket)
        else:
            self.team_connections[team] = [websocket]

    def disconnect(self, team: int, websocket: WebSocket):
        self.active_connections.remove(websocket)
        self.team_connections[team].remove(websocket)

    async def send_direct_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def team_broadcast(self, team: int, message: str):
        for connection in self.team_connections[team]:
            await connection.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()

class Registry:
    def __init__(self, name: str, team: int):
        self.name = name
        self.team = team


@app.get("/register")
async def register(name: str, team: int):
    response = RedirectResponse(url="/")
    response.set_cookie(key="name", value=name)
    response.set_cookie(key="team", value=team)
    return response


@app.get("/unregister")
async def unregister():
    response = RedirectResponse(url="/")
    response.delete_cookie(key="name")
    response.delete_cookie(key="team")
    return response


@app.get("/")
async def root(request: Request, name: str = Cookie(None), team: int = Cookie(None)):
    data = {
        "registered": name is not None and team is not None,
        "name": name,
        "team": team
    }
    return templates.TemplateResponse("index.html", {"request": request, "data": data})


class Registry:
    def __init__(self, name: str, team: int):
        self.name = name
        self.team = team


class UnregisteredException(Exception):
    pass


async def require_registry(name: str = Cookie(None), team: int = Cookie(None)):
    if name is None or team is None:
        raise UnregisteredException()
    else:
        registry = Registry(name, team)
        return registry


@app.exception_handler(UnregisteredException)
async def unregistered_handler(request: Request, ex: UnregisteredException):
    return RedirectResponse(url="/")


@app.get("/chat")
async def chat(request: Request, registry: Registry = Depends(require_registry)):
    return templates.TemplateResponse("chat.html", {"request": request, "data": {"team": registry.team}})


@app.get("/admin")
async def admin(request: Request):
    q = [
            {
                "question": "What's pi?",
                "options": [
                    "3.14", "2"
                ]
            },
            {
                "question": "What's phi?",
                "options": [
                    "123", "1"
                ]
            }
        ]
    msg = "form:" + json.dumps(q)
    await manager.broadcast(msg)
    return ""


class FormData(BaseModel):
    data: str


@app.post("/form")
async def form(data: FormData, registry: Registry = Depends(require_registry)):
    print(data.data)


@app.websocket("/ws/chat")
async def team_endpoint(websocket: WebSocket, registry: Registry = Depends(require_registry)):
    await manager.connect(registry.team, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.team_broadcast(registry.team, f"c:{registry.name}: {data}")
    except WebSocketDisconnect:
        manager.disconnect(registry.team, websocket)
        await manager.team_broadcast(registry.team, f"c:{registry.name} has disconnected")
