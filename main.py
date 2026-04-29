from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import requests
import os

app = FastAPI()

# CORS (important)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("API_KEY")
ORG_ID = os.getenv("ORG_ID")

# ---------------- HOME (UI) ----------------
@app.get("/")
def home():
    return FileResponse("index.html")


# ---------------- CALL LOGS ----------------
@app.get("/call-logs")
def call_logs():
    url = "https://www.tabbly.io/dashboard/agents/endpoints/call-logs-v2"

    params = {
        "api_key": API_KEY,
        "organization_id": ORG_ID,
        "limit": 50,
        "offset": 0
    }

    response = requests.get(url, params=params)
    return response.json()


# ---------------- CREATE CAMPAIGN ----------------
@app.post("/create-campaign")
def create_campaign(data: dict):
    url = "https://www.tabbly.io/dashboard/agents/endpoints/create-campaign"

    payload = {
        "api_key": API_KEY,
        "campaign_name": data["campaign_name"],
        "agent_id": data["agent_id"],
        "start_time": data["start_time"],
        "end_time": data["end_time"],
        "time_zone": "IST"
    }

    response = requests.post(url, json=payload)
    return response.json()