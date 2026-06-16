from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pymongo import MongoClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

app = FastAPI()
client = MongoClient("mongodb://localhost:27017")
db = client["demo"]
users_collection = db["users"]

# Initialize Jinja2 environment
templates_env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(['html', 'xml'])
)
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
@app.get("/users", response_class=HTMLResponse)
async def get_users(request: Request):
    users = list(users_collection.find())
    for user in users:
        user['_id'] = str(user['_id'])
    template = templates_env.get_template("users.html")
    return template.render(request=request, users=users)