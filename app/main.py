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

responses = {}

templates = Jinja2Templates(directory="templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.team_connections = {}
        self.direct_connections = {}
        self.admin = None

    async def admin_connect(self, websocket: WebSocket):
        await websocket.accept()
        self.admin = websocket

    async def admin_disconnect(self, websocket: WebSocket):
        self.admin = None

    async def connect(self, team: int, name: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        if team in self.team_connections:
            self.team_connections[team].append(websocket)
        else:
            self.team_connections[team] = [websocket]
        self.direct_connections[f"{team}{name}"] = websocket

    def disconnect(self, team: int, name: str, websocket: WebSocket):
        self.active_connections.remove(websocket)
        self.team_connections[team].remove(websocket)
        del self.direct_connections[f"{team}{name}"]

    async def send_direct_message(self, team: int, name: str, message: str):
        await self.direct_connections[f"{team}{name}"].send_text(message)

    async def send_admin_message(self, message: str):
        await self.admin.send_text(message)

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
    return templates.TemplateResponse("admin.html", {"request": request})


@app.websocket("/ws/admin")
async def admin_endpoint(websocket: WebSocket):
    await manager.admin_connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.send_admin_message("$ " + data)
            if data == "votes":
                await manager.send_admin_message(str(responses))
            elif data == "disable":
                await manager.broadcast("disable:")
            elif data == "select rep":
                await select_rep()
    except WebSocketDisconnect:
        await manager.admin_disconnect(websocket)


class FormData(BaseModel):
    data: str


@app.post("/admin_form")
async def admin_form(data: FormData):
    responses.clear()
    await manager.broadcast(f"form:{data.data}")


@app.post("/form")
async def form(data: FormData, registry: Registry = Depends(require_registry)):
    print(data.data)
    parsed_data = json.loads(data.data)
    if registry.team not in responses:
        responses[registry.team] = {}
    record = responses[registry.team]
    for k,v in parsed_data.items():
        if k in record:
            if v in record[k]:
                record[k][v] += 1
            else:
                record[k][v] = 1
        else:
            record[k] = { v: 1 }


@app.websocket("/ws/chat")
async def team_endpoint(websocket: WebSocket, registry: Registry = Depends(require_registry)):
    await manager.connect(registry.team, registry.name, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            print(f"{registry.name}: {data}")
            await manager.team_broadcast(registry.team, f"c:{registry.name}: {data}")
    except WebSocketDisconnect:
        manager.disconnect(registry.team, registry.name, websocket)
        await manager.team_broadcast(registry.team, f"c:{registry.name} has disconnected")
