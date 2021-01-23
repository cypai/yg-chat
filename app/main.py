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
rep_responses = {}
reps = []
score = {}

templates = Jinja2Templates(directory="templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.team_connections = {}
        self.direct_connections = {}
        self.admin = None

    def get_registrants(self):
        r = {}
        for key in self.direct_connections.keys():
            team = key[0]
            name = key[1:]
            if team in r:
                r[team].append(name)
            else:
                r[team] = [name]
        return r

    async def admin_connect(self, websocket: WebSocket):
        await websocket.accept()
        self.admin = websocket

    async def admin_disconnect(self, websocket: WebSocket):
        self.admin = None

    async def connect(self, team: int, name: str, websocket: WebSocket):
        await websocket.accept()
        print(f"({team}) {name} connected")
        self.active_connections.append(websocket)
        if team in self.team_connections:
            self.team_connections[team].append(websocket)
        else:
            self.team_connections[team] = [websocket]
        if team not in score:
            score[team] = 0
        self.direct_connections[f"{team}{name}"] = websocket

    def disconnect(self, team: int, name: str, websocket: WebSocket):
        print(f"({team}) {name} disconnected")
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
            cmd, args = (data + " ").split(" ", 1)
            await manager.send_admin_message("$ " + data)
            if cmd == "votes":
                await manager.send_admin_message(str(responses))
            elif cmd == "calc":
                await manager.send_admin_message("Team votes")
                await manager.send_admin_message(str(calc_votes()))
                await manager.send_admin_message("Rep votes")
                await manager.send_admin_message(str(rep_responses))
            elif cmd == "disable":
                await manager.broadcast("disable:")
                await manager.send_admin_message("executed")
            elif cmd == "teams":
                await manager.send_admin_message(str(manager.get_registrants()))
            elif cmd == "srep":
                await select_rep()
                await manager.send_admin_message("executed")
            elif cmd == "reps":
                await manager.send_admin_message(str(reps))
            elif cmd == "repexec":
                await handle_rep_and_hide_chatbox()
                await manager.send_admin_message("executed")
            elif cmd == "show":
                await manager.broadcast("show:")
                await manager.send_admin_message("executed")
            elif cmd == "b":
                await manager.broadcast("c:admin: " + args)
                await manager.send_admin_message("executed")
            elif cmd == "bimg":
                await manager.broadcast("img:" + args)
                await manager.send_admin_message("executed")
            elif cmd == "clear":
                await manager.broadcast("clearchat:")
                await manager.send_admin_message("executed")
            elif cmd == "timer":
                await manager.broadcast("timer:" + args)
                await manager.send_admin_message("executed")
            elif cmd == "score":
                await manager.send_admin_message(str(score))
            elif cmd == "iscore":
                a = args.split(" ")
                team = int(a[0])
                score[team] += int(a[1])
                await manager.send_admin_message("executed")
            elif cmd == "sscore":
                a = args.split(" ")
                team = int(a[0])
                score[team] = int(a[1])
                await manager.send_admin_message("executed")
            elif cmd == "bscore":
                score_str = ""
                for t,s in sorted(score.items(), key=lambda x: x[1], reverse=True):
                    score_str += f"Team {t}: {s}<br />"
                await manager.broadcast("score:" + score_str)
                await manager.send_admin_message("executed")

    except WebSocketDisconnect:
        await manager.admin_disconnect(websocket)


async def select_rep():
    registrants = manager.get_registrants()
    for team, members in registrants.items():
        poll = [{
            "question": "Who is the team representative?",
            "options": members
            }]
        await manager.team_broadcast(int(team), "form:" + json.dumps(poll))


def calc_votes():
    vote = {}
    for team, q_resps in responses.items():
        vote[team] = {}
        for q, votes in q_resps.items():
            vote[team][q] = max(votes.items(), key=lambda x: x[1])[0]
    return vote


async def handle_rep_and_hide_chatbox():
    reps.clear()
    for team, vote in calc_votes().items():
        rep = vote["q0"]
        reps.append((int(team), rep))
        await manager.send_direct_message(team, rep, "hide:")


class FormData(BaseModel):
    data: str


@app.post("/admin_form")
async def admin_form(data: FormData):
    responses.clear()
    rep_responses.clear()
    await manager.broadcast(f"form:{data.data}")


@app.post("/form")
async def form(data: FormData, registry: Registry = Depends(require_registry)):
    parsed_data = json.loads(data.data)
    if (registry.team, registry.name) in reps:
        rep_responses[registry.team] = parsed_data
    else:
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
            print(f"({registry.team}) {registry.name}: {data}")
            await manager.team_broadcast(registry.team, f"c:{registry.name}: {data}")
    except WebSocketDisconnect:
        manager.disconnect(registry.team, registry.name, websocket)
        await manager.team_broadcast(registry.team, f"c:{registry.name} has disconnected")
