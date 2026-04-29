from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
import os
import csv
import io
import re
from openpyxl import load_workbook

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TABBLY_API_KEY = os.getenv("API_KEY")
TABBLY_ORG_ID = os.getenv("ORG_ID")

AGENT_ID = 5566
CAMPAIGN_ID = 2302
AGENT_NAME = "Call & Cut"

BATCH_SIZE = 25
TABBLY_TIMEOUT = 120


class CallRequest(BaseModel):
    phone: str
    name: str
    instruction: str


def get_custom_first_line(name: str) -> str:
    clean_name = str(name).strip()
    return (
        f"Hellooo {clean_name}"
    )


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def clean_phone(value):
    if value is None:
        return ""

    s = str(value).strip()

    if s.lower() == "none":
        return ""

    if s.endswith(".0"):
        s = s[:-2]

    s = s.replace(" ", "")
    s = s.replace("-", "")
    s = s.replace("(", "")
    s = s.replace(")", "")

    s = re.sub(r"[^\d]", "", s)

    if not s:
        return ""

    if len(s) != 12 or not s.startswith("91"):
        return ""

    return s


def normalize_key(key):
    if key is None:
        return ""
    k = str(key).strip().lower()
    k = k.replace("_", " ").replace("-", " ")
    k = " ".join(k.split())
    return k


def normalize_row(row):
    normalized = {}

    for key, value in row.items():
        normalized[normalize_key(key)] = value

    phone = (
        normalized.get("phone numbers")
        or normalized.get("phone number")
        or normalized.get("phone")
        or normalized.get("mobile")
        or normalized.get("mobile number")
        or normalized.get("contact number")
        or normalized.get("contact")
    )

    name = (
        normalized.get("name")
        or normalized.get("customer name")
        or normalized.get("full name")
    )

    instruction = (
        normalized.get("custom instruction")
        or normalized.get("instruction")
        or normalized.get("custom text")
        or normalized.get("notes")
    )

    phone = clean_phone(phone)
    name = clean_text(name)
    instruction = clean_text(instruction)

    return phone, name, instruction


def build_contact(phone, name, instruction):
    return {
        "phone_number": phone,
        "campaign_id": CAMPAIGN_ID,
        "participant_identity": name,
        "use_agent_id": AGENT_ID,
        "creator_by": "api",
        "custom_first_line": get_custom_first_line(name),
        "custom_instruction": instruction,
        "sip_call_id": "NA"
    }


def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/agents")
def get_agents():
    return {
        "status": "success",
        "data": [
            {
                "id": AGENT_ID,
                "agent_name": AGENT_NAME,
                "campaign_id": CAMPAIGN_ID
            }
        ]
    }


@app.post("/call")
def make_call(data: CallRequest):
    if not TABBLY_API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY is missing")

    phone = clean_phone(data.phone)
    name = clean_text(data.name)
    instruction = clean_text(data.instruction)

    if not phone:
        raise HTTPException(
            status_code=400,
            detail="Phone must be in 91XXXXXXXXXX format without +"
        )

    if not name or not instruction:
        raise HTTPException(
            status_code=400,
            detail="Name and instruction are required"
        )

    url = "https://www.tabbly.io/dashboard/agents/endpoints/add-campaign-contacts"

    payload = {
        "api_key": TABBLY_API_KEY,
        "contacts": [
            build_contact(phone, name, instruction)
        ]
    }

    try:
        response = requests.post(url, json=payload, timeout=60)
        result = response.json()
    except requests.exceptions.ReadTimeout:
        raise HTTPException(
            status_code=504,
            detail="Tabbly request timed out while adding single contact"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=result)

    return {
        "message": "Single contact added successfully",
        "selected_agent_id": AGENT_ID,
        "mapped_campaign_id": CAMPAIGN_ID,
        "custom_first_line_used": get_custom_first_line(name),
        "tabbly_response": result
    }


@app.post("/bulk-upload")
async def bulk_upload(file: UploadFile = File(...)):
    if not TABBLY_API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY is missing")

    filename = (file.filename or "").lower()
    content = await file.read()
    rows = []

    if filename.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="CSV file must be UTF-8 encoded"
            )

        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

    elif filename.endswith(".xlsx"):
        workbook = load_workbook(io.BytesIO(content), data_only=True)
        sheet = workbook.active
        data = list(sheet.values)

        if not data:
            raise HTTPException(status_code=400, detail="Excel file is empty")

        headers = [str(h).strip() if h is not None else "" for h in data[0]]

        for values in data[1:]:
            row = dict(zip(headers, values))
            rows.append(row)
    else:
        raise HTTPException(
            status_code=400,
            detail="Only CSV and XLSX files are supported"
        )

    contacts = []
    skipped = []

    for idx, row in enumerate(rows, start=2):
        phone, name, instruction = normalize_row(row)

        if not phone or not name or not instruction:
            skipped.append({
                "row": idx,
                "reason": "Missing or invalid phone / name / custom instruction. Phone must be 91XXXXXXXXXX",
                "data": row
            })
            continue

        contacts.append(build_contact(phone, name, instruction))

    if not contacts:
        raise HTTPException(status_code=400, detail={
            "message": "No valid contacts found in file",
            "skipped": skipped
        })

    url = "https://www.tabbly.io/dashboard/agents/endpoints/add-campaign-contacts"

    batch_results = []
    total_success = 0
    total_failed = 0

    for batch_no, batch in enumerate(chunk_list(contacts, BATCH_SIZE), start=1):
        payload = {
            "api_key": TABBLY_API_KEY,
            "contacts": batch
        }

        try:
            response = requests.post(url, json=payload, timeout=TABBLY_TIMEOUT)
            result = response.json()
        except requests.exceptions.ReadTimeout:
            batch_results.append({
                "batch_no": batch_no,
                "status": "timeout",
                "batch_size": len(batch)
            })
            total_failed += len(batch)
            continue
        except Exception as e:
            batch_results.append({
                "batch_no": batch_no,
                "status": "error",
                "batch_size": len(batch),
                "error": str(e)
            })
            total_failed += len(batch)
            continue

        if response.status_code >= 400:
            batch_results.append({
                "batch_no": batch_no,
                "status": "failed",
                "batch_size": len(batch),
                "response": result
            })
            total_failed += len(batch)
        else:
            batch_results.append({
                "batch_no": batch_no,
                "status": "success",
                "batch_size": len(batch),
                "response": result
            })

            if isinstance(result, dict) and "summary" in result:
                total_success += result["summary"].get("success", 0)
                total_failed += result["summary"].get("failed", 0)
            else:
                total_success += len(batch)

    return {
        "message": "Bulk upload processed in batches",
        "selected_agent_id": AGENT_ID,
        "mapped_campaign_id": CAMPAIGN_ID,
        "valid_contacts": len(contacts),
        "skipped_rows": skipped,
        "batch_size": BATCH_SIZE,
        "total_success": total_success,
        "total_failed": total_failed,
        "batch_results": batch_results,
        "sample_custom_first_line": get_custom_first_line("Sample User")
    }


@app.get("/call-logs")
def get_logs():
    if not TABBLY_API_KEY or not TABBLY_ORG_ID:
        raise HTTPException(status_code=500, detail="API_KEY or ORG_ID is missing")

    url = "https://www.tabbly.io/dashboard/agents/endpoints/call-logs-v2"

    params = {
        "api_key": TABBLY_API_KEY,
        "organization_id": TABBLY_ORG_ID,
        "campaign_id": str(CAMPAIGN_ID),
        "limit": 50,
        "offset": 0
    }

    response = requests.get(url, params=params, timeout=30)

    try:
        result = response.json()
    except Exception:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=result)

    return result
