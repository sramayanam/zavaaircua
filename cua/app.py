"""
CUA Dashboard – FastAPI server with WebSocket for real-time agent streaming.

Supports three modes: Browse, Create Complaint, Update Complaint.
Each mode has pre-built scenarios with task steps the agent follows.

Run:
    cd cua
    uvicorn app:app --reload --port 8501
"""

import logging
import os
import json
import asyncio
import base64
import shutil
import re
import hashlib
import uuid
import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, ComputerUsePreviewTool
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("cua")

# ── Configuration ────────────────────────────────────────────────────────────
def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable '{name}' is missing or empty.")
    return value

def _validate_env() -> None:
    missing = [v for v in ("AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_DEPLOYMENT", "ZAVA_AIR_URL")
               if not os.environ.get(v, "").strip()]
    if missing:
        raise RuntimeError(
            f"Server cannot start. Missing required environment variables: {', '.join(missing)}\n"
            "Set them in your .env file or as environment variables before starting."
        )
    for opt in ("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT_NAME"):
        if not os.environ.get(opt, "").strip():
            logger.warning("Optional env var '%s' not set.", opt)

_validate_env()

BASE_URL = _require_env("AZURE_OPENAI_BASE_URL")
MODEL = _require_env("AZURE_OPENAI_DEPLOYMENT")
ZAVA_AIR_URL = _require_env("ZAVA_AIR_URL")
FOUNDRY_PROJECT_ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
FOUNDRY_MODEL_DEPLOYMENT_NAME = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", MODEL).strip()

DISPLAY_WIDTH = 1026
DISPLAY_HEIGHT = 769
MAX_ITERATIONS = 15

SHOTS_DIR = Path(__file__).parent / "screenshots"

# ── Browse mode: known filter values (mirrors FILTER_OPTIONS in computer-use-agent.ts) ──
KNOWN_FILTER_VALUES: dict[str, list[str]] = {
    "status":   ["Open", "Under Review", "Resolved", "Closed", "Escalated"],
    "severity": ["Low", "Medium", "High", "Critical"],
    "category": ["Baggage", "Flight Operations", "Refund", "Safety", "Seating", "Wheelchair"],
}

# Optional lightweight model for browse filter-intent extraction (e.g. "gpt-4o-mini").
# If unset, falls back to regex parsing.  Mirrors the gpt-4o-mini path in computer-use-agent.ts.
BROWSE_INTENT_DEPLOYMENT = os.environ.get("BROWSE_INTENT_DEPLOYMENT", "").strip()

# ── Azure Storage config (optional – enables queue-driven API mode) ──────────
AZURE_STORAGE_ACCOUNT_NAME = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "").strip()
AZURE_STORAGE_QUEUE_NAME = os.environ.get("AZURE_STORAGE_QUEUE_NAME", "cua-agent-jobs").strip()
AZURE_STORAGE_TABLE_NAME = os.environ.get("AZURE_STORAGE_TABLE_NAME", "cuaJobStatus").strip()
AZURE_STORAGE_BLOB_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_BLOB_CONTAINER_NAME", "cua-screenshots").strip()
# AZURE_CLIENT_ID is read automatically by DefaultAzureCredential to select the UAMI
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "").strip()

QUEUE_MODE_ENABLED = bool(AZURE_STORAGE_ACCOUNT_NAME)
BLOB_MODE_ENABLED = bool(AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_BLOB_CONTAINER_NAME)

_QUEUE_URL = f"https://{AZURE_STORAGE_ACCOUNT_NAME}.queue.core.windows.net" if AZURE_STORAGE_ACCOUNT_NAME else ""
_TABLE_URL = f"https://{AZURE_STORAGE_ACCOUNT_NAME}.table.core.windows.net" if AZURE_STORAGE_ACCOUNT_NAME else ""
_BLOB_URL = f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net" if AZURE_STORAGE_ACCOUNT_NAME else ""

if QUEUE_MODE_ENABLED:
    logger.info(
        "Queue mode enabled (DefaultAzureCredential). Account: %s | Queue: %s | Table: %s | Blob container: %s",
        AZURE_STORAGE_ACCOUNT_NAME, AZURE_STORAGE_QUEUE_NAME,
        AZURE_STORAGE_TABLE_NAME, AZURE_STORAGE_BLOB_CONTAINER_NAME,
    )
else:
    logger.warning("AZURE_STORAGE_ACCOUNT_NAME not set – queue/API/blob mode disabled. WebSocket UI still works.")

if AZURE_CLIENT_ID:
    logger.info("UAMI client ID configured: %s", AZURE_CLIENT_ID)
else:
    logger.warning("AZURE_CLIENT_ID not set – DefaultAzureCredential will use first available credential.")

# ── System prompts per mode ───────────────────────────────────────────────────
DEFAULT_SYSTEM = (
    "You are an AI agent that can control a browser via keyboard and mouse. "
    "You take a screenshot after each action to verify the result. "
    "Be direct and efficient — click precisely on UI elements you can see. "
    "Once you have completed the requested task, stop and report your findings."
)

CREATE_SYSTEM = (
    "You are an AI agent that fills in web forms by clicking and typing. "
    "You take a screenshot after each action to verify the result. "
    "All form fields have already been pre-filled for you — your only task is "
    "to type the description text into the Description field, then click "
    "'Submit Complaint' and verify the success message."
)

UPDATE_SYSTEM = (
    "You are an AI agent that updates complaint records in a web application. "
    "You take a screenshot after each action to verify the result. "
    "To update a record: find the complaint row matching the passenger name, flight, and PNR; "
    "click the row to open the detail panel; click the 'Edit' button to open the update modal; "
    "use the Status and Severity dropdowns to set the correct values — click the dropdown, "
    "wait for options to appear, then select the correct option; "
    "set the Agent dropdown to the correct agent name; "
    "set the Satisfaction Score dropdown to the correct value; "
    "type the resolution notes into the Notes textarea; "
    "click 'Save Changes' and verify the success toast appears."
)

# ── Local scenario loading ───────────────────────────────────────────────────
SCENARIOS_DIR = Path(__file__).parent / "scenarios"

DEFAULT_BROWSE_TASKS = [
    {"id": 1, "text": "Observe the page layout: stats bar, filters, and complaint table."},
    {"id": 2, "text": "Set Status as 'Open', Severity as 'Critical', Category as 'All'. Filter date to today."},
    {"id": 3, "text": "Open each complaint row and inspect details in the side panel."},
    {"id": 4, "text": "Summarize notable findings from all the details read from each row."},
]


def _normalize_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, task in enumerate(tasks, start=1):
        text = str(task.get("text", "")).strip()
        if text:
            normalized.append({"id": index, "text": text})
    return normalized


def _build_create_tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    complaint_text = str(
        data.get("complaint_description")
        or data.get("issue_description")
        or data.get("description")
        or ""
    )
    return [
        {"id": 1, "text": "Click '+ New Complaint' to open the create modal."},
        {
            "id": 2,
            "text": (
                f"Set Passenger Name '{data.get('passenger_name', '')}', "
                f"Email '{data.get('passenger_email', '')}', "
                f"Phone '{data.get('passenger_phone', '')}'."
            ),
        },
        {"id": 3, "text": f"Set Flight to '{data.get('flight_number', '')}' and PNR to '{data.get('pnr', '')}'."},
        {"id": 4, "text": f"Set Category '{data.get('category', '')}' and Subcategory '{data.get('subcategory', '')}'."},
        {"id": 5, "text": f"Set Severity '{data.get('severity', '')}' and Assign Agent '{data.get('agent', '')}'."},
        {"id": 6, "text": f"Type Description exactly: '{complaint_text}'."},
        {"id": 7, "text": "Click 'Submit Complaint' and confirm success toast appears."},
    ]


def _build_update_tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "text": (
                "Find complaint by all identifiers: "
                f"Passenger '{data.get('target_passenger_name', '')}', "
                f"Flight '{data.get('target_flight_number', '')}', "
                f"PNR '{data.get('target_pnr', '')}'."
            ),
        },
        {"id": 2, "text": "Open detail panel and click 'Edit'."},
        {"id": 3, "text": f"Set Status to '{data.get('new_status', '')}'."},
        {"id": 4, "text": f"Set Severity to '{data.get('new_severity', '')}' and Agent to '{data.get('new_agent', '')}'."},
        {"id": 5, "text": f"Set Satisfaction Score to '{data.get('new_score', '')}'."},
        {"id": 6, "text": f"Type Resolution Notes exactly: '{data.get('new_notes', '')}'."},
        {"id": 7, "text": "Click 'Save Changes' and confirm success toast appears."},
    ]


def _detect_mode(record: dict[str, Any]) -> str:
    explicit_mode = str(record.get("mode") or record.get("operation") or "").strip().lower()
    if explicit_mode in {"browse", "create", "update"}:
        return explicit_mode

    keys = {k.lower() for k in record.keys()}
    update_keys = {"target_passenger_name", "target_flight_number", "target_pnr", "new_status", "new_notes"}
    create_keys = {"passenger_name", "flight_number", "pnr", "category", "subcategory", "severity", "description"}

    if keys & update_keys:
        return "update"
    if keys & create_keys:
        return "create"
    return "browse"


def _materialize_scenario(record: dict[str, Any], scenario_id: str) -> tuple[str, dict[str, Any]]:
    mode = _detect_mode(record)
    tasks = record.get("tasks")
    if isinstance(tasks, list):
        normalized_tasks = _normalize_tasks(tasks)
    elif mode == "create":
        normalized_tasks = _build_create_tasks(record)
    elif mode == "update":
        normalized_tasks = _build_update_tasks(record)
    else:
        normalized_tasks = list(DEFAULT_BROWSE_TASKS)

    scenario = {
        "name": str(record.get("name") or scenario_id.replace("_", " ").title()),
        "description": str(record.get("scenario_description") or record.get("description") or f"Loaded from local scenario file ({mode})."),
        "tasks": normalized_tasks,
        "source": str(record.get("source") or "local"),
    }
    return mode, scenario


def load_scenarios_from_disk() -> dict[str, dict[str, dict[str, Any]]]:
    scenarios: dict[str, dict[str, dict[str, Any]]] = {"browse": {}, "create": {}, "update": {}}

    if SCENARIOS_DIR.exists():
        for file_path in sorted(SCENARIOS_DIR.glob("*.json")):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            records: list[dict[str, Any]]
            if isinstance(payload, list):
                records = [r for r in payload if isinstance(r, dict)]
            elif isinstance(payload, dict) and isinstance(payload.get("scenarios"), list):
                records = [r for r in payload["scenarios"] if isinstance(r, dict)]
            elif isinstance(payload, dict):
                records = [payload]
            else:
                records = []

            for index, record in enumerate(records, start=1):
                sid_base = str(record.get("id") or file_path.stem)
                sid = sid_base if len(records) == 1 else f"{sid_base}_{index}"
                mode, scenario = _materialize_scenario(record, sid)
                scenarios[mode][sid] = scenario

        for file_path in sorted(SCENARIOS_DIR.glob("*.jsonl")):
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            for index, line in enumerate(lines, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if not isinstance(record, dict):
                    continue

                sid = str(record.get("id") or f"{file_path.stem}_{index}")
                mode, scenario = _materialize_scenario(record, sid)
                scenarios[mode][sid] = scenario

    if not scenarios["browse"]:
        scenarios["browse"]["default"] = {
            "name": "Browse & Filter Dashboard",
            "description": "Explore the dashboard, use filters, and inspect complaint details.",
            "tasks": list(DEFAULT_BROWSE_TASKS),
            "source": "builtin",
        }

    return scenarios

# ── Playwright key mapping ───────────────────────────────────────────────────
KEY_MAPPING = {
    "/": "Slash", "\\": "Backslash",
    "alt": "Alt", "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight", "arrowup": "ArrowUp",
    "backspace": "Backspace", "ctrl": "Control", "delete": "Delete",
    "enter": "Enter", "esc": "Escape", "shift": "Shift", "space": " ",
    "tab": "Tab", "win": "Meta", "cmd": "Meta", "super": "Meta", "option": "Alt",
}

# ── FastAPI lifespan ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Startup/shutdown lifecycle: starts queue poller. Screenshots stored in Azure Blob in prod."""
    if not BLOB_MODE_ENABLED:
        # Local dev: keep a clean local screenshots folder
        if SHOTS_DIR.exists():
            shutil.rmtree(SHOTS_DIR)
        SHOTS_DIR.mkdir(exist_ok=True)
    logger.info(
        "CUA Dashboard started. Screenshot storage: %s",
        f"Azure Blob ({AZURE_STORAGE_ACCOUNT_NAME}/{AZURE_STORAGE_BLOB_CONTAINER_NAME})" if BLOB_MODE_ENABLED else "local disk",
    )

    poller_task: asyncio.Task | None = None
    if QUEUE_MODE_ENABLED:
        poller_task = asyncio.create_task(_queue_poller())
        logger.info("Queue poller task started.")

    yield

    if poller_task:
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass
        logger.info("Queue poller stopped.")


# ── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(title="Zava Air AI Complaint Agent", lifespan=lifespan)

# CORS – allow origins from env var; default to same-origin only in production
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
if _raw_origins:
    _allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
else:
    _allowed_origins = ["*"]
    logger.warning("ALLOWED_ORIGINS not set – defaulting to '*'. Set it explicitly for production.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Local-dev screenshots dir (no-op in blob mode)
SHOTS_DIR.mkdir(exist_ok=True)

# Serve the dashboard UI
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/screenshots/{job_id}/{filename}")
async def serve_screenshot(job_id: str, filename: str):
    """Proxy screenshot served from Azure Blob (prod) or local disk (dev)."""
    if BLOB_MODE_ENABLED:
        from azure.storage.blob.aio import BlobServiceClient as _AsyncBSC
        blob_path = f"{job_id}/{filename}"
        try:
            async with _AsyncBSC(account_url=_BLOB_URL, credential=DefaultAzureCredential()) as bsc:
                cc = bsc.get_container_client(AZURE_STORAGE_BLOB_CONTAINER_NAME)
                stream = await cc.download_blob(blob_path)
                data = await stream.readall()
            return Response(content=data, media_type="image/jpeg")
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"Screenshot not found: {exc}")
    # Local dev fallback
    local_path = SHOTS_DIR / filename
    if local_path.exists():
        return FileResponse(str(local_path), media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Screenshot not found")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/defaults")
async def get_defaults():
    """Return all modes, scenarios, and config."""
    all_scenarios = load_scenarios_from_disk()
    return {
        "systemPrompts": {
            "browse": DEFAULT_SYSTEM,
            "create": CREATE_SYSTEM,
            "update": UPDATE_SYSTEM,
        },
        "scenarios": {
            mode: {
                sid: {"name": s["name"], "description": s["description"], "tasks": s["tasks"]}
                for sid, s in scenarios.items()
            }
            for mode, scenarios in all_scenarios.items()
        },
        "targetUrl": ZAVA_AIR_URL,
        "maxIterations": MAX_ITERATIONS,
    }


# ── Agent state ──────────────────────────────────────────────────────────────
_agent_semaphore = asyncio.Semaphore(1)


def build_task_instructions(mode: str, tasks: list[dict]) -> str:
    """Turn the task list into a numbered prompt string."""
    preambles = {
        "browse": (
            "You are looking at the Zava Air Customer Complaints dashboard. "
            "Your task is to explore the dashboard thoroughly:\n"
        ),
        "create": (
            "You are looking at the Zava Air Customer Complaints dashboard. "
            "Your task is to create a new complaint from an IVR (phone) call. "
            "Use only the provided values in the steps below. "
            "Do not ask the user for extra inputs or confirmation. "
            "Follow these steps precisely:\n"
        ),
        "update": (
            "You are looking at the Zava Air Customer Complaints dashboard. "
            "Your task is to update an existing complaint. "
            "Use only the provided values in the steps below. "
            "Do not ask the user for extra inputs or confirmation. "
            "Follow these steps precisely:\n"
        ),
    }
    preamble = preambles.get(mode, preambles["browse"])
    numbered = "\n".join(f"{i+1}. {t['text']}" for i, t in enumerate(tasks))
    return preamble + numbered


def infer_mode_from_tasks(tasks: list[dict[str, Any]], fallback: str = "browse") -> str:
    text_blob = " ".join(str(t.get("text", "")).lower() for t in tasks)
    if any(token in text_blob for token in ["submit complaint", "new complaint", "pnr", "description"]):
        return "create"
    if any(token in text_blob for token in ["save changes", "resolution notes", "edit", "status"]):
        return "update"
    return fallback


# ── Helpers ──────────────────────────────────────────────────────────────────

def validate_coordinates(x, y):
    return max(0, min(x, DISPLAY_WIDTH)), max(0, min(y, DISPLAY_HEIGHT))


def _extract_quoted_values(text: str) -> list[str]:
    return re.findall(r"'([^']*)'", text or "")


def parse_create_scenario_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, str] | None:
    if len(tasks) < 6:
        return None
    task_texts = [str(t.get("text", "")) for t in tasks]

    name_email_phone = _extract_quoted_values(task_texts[1])
    flight_pnr = _extract_quoted_values(task_texts[2])
    category_subcategory = _extract_quoted_values(task_texts[3])
    severity_agent = _extract_quoted_values(task_texts[4])
    description = _extract_quoted_values(task_texts[5])

    if not (len(name_email_phone) >= 3 and len(flight_pnr) >= 2 and len(category_subcategory) >= 2 and len(severity_agent) >= 2 and description):
        return None

    return {
        "passenger_name": name_email_phone[0],
        "passenger_email": name_email_phone[1],
        "passenger_phone": name_email_phone[2],
        "flight_number": flight_pnr[0],
        "pnr": flight_pnr[1],
        "category": category_subcategory[0],
        "subcategory": category_subcategory[1],
        "severity": severity_agent[0],
        "agent": severity_agent[1],
        "description": description[0],
    }


def parse_update_scenario_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, str] | None:
    if len(tasks) < 6:
        return None
    task_texts = [str(t.get("text", "")) for t in tasks]

    target = _extract_quoted_values(task_texts[0])
    status = _extract_quoted_values(task_texts[2])
    severity_agent = _extract_quoted_values(task_texts[3])
    score = _extract_quoted_values(task_texts[4])
    notes = _extract_quoted_values(task_texts[5])

    if not (len(target) >= 3 and status and len(severity_agent) >= 2 and score and notes):
        return None

    return {
        "target_passenger_name": target[0],
        "target_flight_number": target[1],
        "target_pnr": target[2],
        "new_status": status[0],
        "new_severity": severity_agent[0],
        "new_agent": severity_agent[1],
        "new_score": score[0],
        "new_notes": notes[0],
    }


def parse_browse_filters_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    status = ""
    severities: list[str] = []
    categories: list[str] = []
    date_from = ""
    date_to = ""
    today_iso = datetime.date.today().isoformat()

    for task in tasks:
        text = str(task.get("text", ""))
        lower = text.lower()

        if not text:
            continue

        if not status and "status" in lower:
            match = re.search(r"status\s*(?:to|as)?\s*'([^']+)'", text, flags=re.IGNORECASE)
            if match:
                status = match.group(1).strip()

        if not severities and "severit" in lower:
            if "select severities" in lower:
                after_kw = text[lower.find("select severities"):]
                severities = [v.strip() for v in _extract_quoted_values(after_kw) if v.strip()]
            else:
                match = re.search(r"severit(?:y|ies)\s*(?:to|as)?\s*'([^']+)'", text, flags=re.IGNORECASE)
                if match:
                    severities = [match.group(1).strip()]

        if not categories and "categor" in lower:
            if "select categories" in lower:
                after_kw = text[lower.find("select categories"):]
                categories = [v.strip() for v in _extract_quoted_values(after_kw) if v.strip()]
            else:
                match = re.search(r"categor(?:y|ies)\s*(?:to|as)?\s*'([^']+)'", text, flags=re.IGNORECASE)
                if match:
                    categories = [match.group(1).strip()]

        if not date_from:
            if "today" in lower:
                date_from = today_iso
                date_to = today_iso
            else:
                m = re.search(r"date\s+(?:from\s+)?'?(\d{4}-\d{2}-\d{2})'?", text, flags=re.IGNORECASE)
                if m:
                    date_from = m.group(1)
                    m2 = re.search(r"(?:to|until)\s+'?(\d{4}-\d{2}-\d{2})'?", text, flags=re.IGNORECASE)
                    if m2:
                        date_to = m2.group(1)

    if not status and not severities and not categories and not date_from:
        return None

    return {
        "status": status,
        "severities": severities,
        "categories": categories,
        "date_from": date_from,
        "date_to": date_to,
    }


def _extract_browse_filter_intent_llm(client, task_text: str) -> dict[str, str]:
    """
    Cheap structured call to extract filter intent from natural-language task text.
    Mirrors the extractFilters() pattern in computer-use-agent.ts — uses a lightweight
    chat completions model (BROWSE_INTENT_DEPLOYMENT) so we don't burn computer-use-preview
    tokens on a simple classification step.

    Returns {"status", "severity", "category", "date_from", "date_to", "reasoning"}.
    "All" means no filter applied for enum fields; "" means no date filter.
    Always falls back to defaults on any error.
    """
    today_iso = datetime.date.today().isoformat()
    system_prompt = (
        "You are a filter-intent extractor for a customer complaints dashboard.\n"
        f"Today's date is {today_iso}.\n"
        "The page has three dropdown filters and two date inputs:\n"
        f"  - Status:    {' | '.join(['All'] + KNOWN_FILTER_VALUES['status'])}\n"
        f"  - Severity:  {' | '.join(['All'] + KNOWN_FILTER_VALUES['severity'])}\n"
        f"  - Category:  {' | '.join(['All'] + KNOWN_FILTER_VALUES['category'])}\n"
        "  - date_from: YYYY-MM-DD (inclusive lower bound on complaint_date), or \"\" for no filter\n"
        "  - date_to:   YYYY-MM-DD (inclusive upper bound on complaint_date), or \"\" for no filter\n\n"
        "Given the user's task text, return ONLY valid JSON in this exact shape:\n"
        '{"status":"<exact value or All>","severity":"<exact value or All>",'
        '"category":"<exact value or All>","date_from":"<YYYY-MM-DD or empty>",'
        '"date_to":"<YYYY-MM-DD or empty>","reasoning":"<one sentence>"}\n'
        "If the user mentions 'today', set date_from and date_to both to today's date.\n"
        "Return nothing else — no markdown, no explanation outside the JSON."
    )
    _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    try:
        completion = client.chat.completions.create(
            model=BROWSE_INTENT_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": task_text},
            ],
            temperature=0,
            max_tokens=200,
        )
        raw = (completion.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        status   = parsed.get("status",   "All") if parsed.get("status")   in KNOWN_FILTER_VALUES["status"]   else "All"
        severity = parsed.get("severity", "All") if parsed.get("severity") in KNOWN_FILTER_VALUES["severity"] else "All"
        category = parsed.get("category", "All") if parsed.get("category") in KNOWN_FILTER_VALUES["category"] else "All"
        date_from = str(parsed.get("date_from") or "").strip()
        date_to   = str(parsed.get("date_to")   or "").strip()
        if date_from and not _date_re.match(date_from):
            date_from = ""
        if date_to and not _date_re.match(date_to):
            date_to = ""
        return {
            "status": status, "severity": severity, "category": category,
            "date_from": date_from, "date_to": date_to,
            "reasoning": str(parsed.get("reasoning", "")),
        }
    except Exception as exc:
        logger.warning("Browse intent extraction failed (%s) – falling back to regex parsing", exc)
        return {"status": "All", "severity": "All", "category": "All", "date_from": "", "date_to": "", "reasoning": "parse error"}


async def set_select_by_label(page, selector: str, label: str):
    success = await page.evaluate(
        """
        ({ selector, label }) => {
            const sel = document.querySelector(selector);
            if (!sel) return false;
            const opt = Array.from(sel.options).find(o => o.textContent.trim() === label);
            if (!opt) return false;
            sel.value = opt.value;
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        """,
        {"selector": selector, "label": label},
    )
    if not success:
        raise RuntimeError(f"Could not set {selector} by label '{label}'")


async def set_select_by_value(page, selector: str, value: str):
    success = await page.evaluate(
        """
        ({ selector, value }) => {
            const sel = document.querySelector(selector);
            if (!sel) return false;
            const hasValue = Array.from(sel.options).some(o => o.value === value);
            if (!hasValue) return false;
            sel.value = value;
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        """,
        {"selector": selector, "value": value},
    )
    if not success:
        raise RuntimeError(f"Could not set {selector} by value '{value}'")


async def prefill_create_from_tasks(page, scenario: dict[str, str]):
    modal_active = await page.evaluate("document.querySelector('#newComplaintModal.active') !== null")
    if not modal_active:
        create_button = page.get_by_role("button", name="+ New Complaint")
        await create_button.wait_for(timeout=10000)
        await create_button.click()
        await page.wait_for_selector("#newComplaintModal.active", timeout=10000)

    flight_value = await page.evaluate(
        """
        (flightNumber) => {
          const sel = document.getElementById('fFlight');
          if (!sel) return '';
          const opt = Array.from(sel.options).find(o => o.textContent.trim().startsWith(flightNumber));
          return opt ? opt.value : '';
        }
        """,
        scenario["flight_number"],
    )
    if not flight_value:
        raise RuntimeError(f"Flight '{scenario['flight_number']}' not found")

    await set_select_by_value(page, "#fFlight", str(flight_value))
    await set_select_by_label(page, "#fCategory", scenario["category"])
    await page.wait_for_function(
        """() => {
          const sub = document.getElementById('fSubcategory');
          return !!sub && sub.options.length > 1;
        }""",
        timeout=8000,
    )
    await set_select_by_label(page, "#fSubcategory", scenario["subcategory"])
    await set_select_by_label(page, "#fSeverity", scenario["severity"])
    await set_select_by_label(page, "#fAgent", scenario["agent"])

    await page.fill("#fPassengerName", scenario["passenger_name"])
    await page.fill("#fPassengerEmail", scenario["passenger_email"])
    await page.fill("#fPassengerPhone", scenario["passenger_phone"])
    await page.fill("#fPnr", scenario["pnr"])
    await page.fill("#fDescription", "")

    await page.evaluate(
        """
        () => {
            const modal = document.querySelector('#newComplaintModal.active .modal');
            if (modal) {
                modal.scrollTop = modal.scrollHeight;
            }
            const desc = document.querySelector('#fDescription');
            if (desc) desc.focus();
        }
        """
    )


async def prefill_update_from_tasks(page, scenario: dict[str, str]):
    rows = page.locator("#complaintsBody tr")
    row_count = await rows.count()

    target_row = None
    for index in range(row_count):
        row = rows.nth(index)
        cells = row.locator("td")
        if await cells.count() < 4:
            continue

        passenger_text = (await cells.nth(1).inner_text()).strip()
        flight_text = (await cells.nth(2).inner_text()).strip()
        pnr_text = (await cells.nth(3).inner_text()).strip()

        if (
            scenario["target_passenger_name"] in passenger_text
            and scenario["target_flight_number"] in flight_text
            and scenario["target_pnr"] in pnr_text
        ):
            target_row = row
            break

    if target_row is None:
        raise RuntimeError("Target update complaint row not found")

    await target_row.click()
    await page.wait_for_selector("#detailPanel.active", timeout=10000)
    await page.get_by_role("button", name="Edit").click()
    await page.wait_for_selector("#updateModal.active", timeout=10000)

    await set_select_by_label(page, "#uStatus", scenario["new_status"])
    await set_select_by_label(page, "#uSeverity", scenario["new_severity"])
    await set_select_by_label(page, "#uAgent", scenario["new_agent"])
    await set_select_by_value(page, "#uScore", str(scenario["new_score"]))
    await page.fill("#uNotes", scenario["new_notes"])


async def prefill_browse_filters_from_tasks(page, filters: dict[str, Any]) -> list[str]:
    """Playwright prefills Status, Severity, Category, and date range. Nothing left for the CUA model to touch."""
    notes: list[str] = []
    status = str(filters.get("status", "")).strip()
    severities = [str(v).strip() for v in filters.get("severities", []) if str(v).strip()]
    categories = [str(v).strip() for v in filters.get("categories", []) if str(v).strip()]
    date_from = str(filters.get("date_from", "")).strip()
    date_to   = str(filters.get("date_to",   "")).strip()

    await page.wait_for_selector("#filterStatus", timeout=10000)
    await page.wait_for_selector("#filterCategory", timeout=10000)
    await page.wait_for_function(
        """() => {
            const sel = document.getElementById('filterCategory');
            return !!sel && sel.options.length >= 1;
        }""",
        timeout=10000,
    )

    if status:
        await set_select_by_label(page, "#filterStatus", status)
        notes.append(f"Status='{status}'")
        await asyncio.sleep(0.15)

    if severities:
        await set_select_by_label(page, "#filterSeverity", severities[0])
        notes.append(f"Severity='{severities[0]}'")
        await asyncio.sleep(0.15)

    if categories:
        await set_select_by_label(page, "#filterCategory", categories[0])
        notes.append(f"Category='{categories[0]}'")
        await asyncio.sleep(0.15)

    if date_from:
        await page.fill("#filterDateFrom", date_from)
        await page.dispatch_event("#filterDateFrom", "change")
        notes.append(f"DateFrom='{date_from}'")
    if date_to:
        await page.fill("#filterDateTo", date_to)
        await page.dispatch_event("#filterDateTo", "change")
        notes.append(f"DateTo='{date_to}'")

    return notes


async def success_toast_visible(page) -> bool:
    return await page.evaluate(
        """
        () => {
          const toast = document.querySelector('#toast');
          const text = (toast?.textContent || '').trim().toLowerCase();
          return text.includes('complaint submitted successfully') || text.includes('complaint updated');
        }
        """
    )


async def finalize_with_fallback(page, mode: str, create_scenario: dict[str, str] | None = None) -> bool:
    try:
        if mode == "create" and create_scenario is not None:
            description_text = await page.evaluate(
                """
                () => (document.querySelector('#fDescription')?.value || '').trim()
                """
            )
            if not description_text:
                await page.fill("#fDescription", create_scenario.get("description", ""))

        button_name = "Submit Complaint" if mode == "create" else "Save Changes"
        await page.get_by_role("button", name=button_name).click()
        await asyncio.sleep(1.2)
        return await success_toast_visible(page)
    except Exception:
        return False


async def handle_action(page, action):
    action_type = action.type
    desc = ""

    if action_type == "drag":
        desc = "drag (unsupported, skipped)"
    elif action_type == "click":
        button = getattr(action, "button", "left")
        x, y = validate_coordinates(action.x, action.y)
        desc = f"click ({x}, {y}) button={button}"
        if button == "back":
            await page.go_back()
        elif button == "forward":
            await page.go_forward()
        elif button == "wheel":
            await page.mouse.wheel(x, y)
        else:
            btn = {"left": "left", "right": "right", "middle": "middle"}.get(button, "left")
            await page.mouse.click(x, y, button=btn)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except PwTimeout:
                pass
    elif action_type == "double_click":
        x, y = validate_coordinates(action.x, action.y)
        desc = f"double-click ({x}, {y})"
        await page.mouse.dblclick(x, y)
    elif action_type == "scroll":
        sx = getattr(action, "scroll_x", 0)
        sy = getattr(action, "scroll_y", 0)
        x, y = validate_coordinates(action.x, action.y)
        desc = f"scroll ({x},{y}) dx={sx} dy={sy}"
        await page.mouse.move(x, y)
        step_x = int(sx / 2) if sx else 0
        step_y = int(sy / 2) if sy else 0
        await page.mouse.wheel(step_x, step_y)
        await asyncio.sleep(0.12)
        await page.mouse.wheel(sx - step_x, sy - step_y)
        await page.evaluate(
            """
            ({x, y, sx, sy}) => {
                const modal = document.querySelector('#newComplaintModal.active .modal, #updateModal.active .modal');
                if (!modal) {
                    window.scrollBy({ left: sx, top: sy, behavior: 'auto' });
                    return;
                }

                const rect = modal.getBoundingClientRect();
                const insideModal = x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
                if (insideModal) {
                    modal.scrollBy({ left: sx, top: sy, behavior: 'auto' });
                } else {
                    window.scrollBy({ left: sx, top: sy, behavior: 'auto' });
                }
            }
            """,
            {"x": x, "y": y, "sx": sx, "sy": sy},
        )
        await asyncio.sleep(0.35)
    elif action_type == "keypress":
        keys = getattr(action, "keys", [])
        desc = f"keypress {keys}"
        mapped = [KEY_MAPPING.get(k.lower(), k) for k in keys]
        if len(mapped) > 1:
            for k in mapped:
                await page.keyboard.down(k)
            await asyncio.sleep(0.1)
            for k in reversed(mapped):
                await page.keyboard.up(k)
        else:
            for k in mapped:
                await page.keyboard.press(k)
    elif action_type == "type":
        text = getattr(action, "text", "")
        desc = f'type "{text}"'
        await page.keyboard.type(text, delay=20)
    elif action_type == "wait":
        ms = getattr(action, "ms", 1000)
        desc = f"wait {ms}ms"
        await asyncio.sleep(ms / 1000)
    elif action_type == "screenshot":
        desc = "screenshot (model request)"
    else:
        desc = f"unknown: {action_type}"

    return desc


async def take_screenshot(page, label: str, job_id: str = "") -> tuple[str, str, str]:
    """Return (base64_image, saved_filename, mime_subtype).

    In blob mode:  uploads raw JPEG to Azure Blob at {job_id}/{fname}.
    In local mode: writes to SHOTS_DIR for dev/fallback.
    """
    raw = await page.screenshot(full_page=False, type="jpeg", quality=25)
    b64 = base64.b64encode(raw).decode()
    fname = f"{label}.jpg"

    if BLOB_MODE_ENABLED and job_id:
        from azure.storage.blob.aio import BlobServiceClient as _AsyncBSC
        from azure.storage.blob import ContentSettings
        blob_path = f"{job_id}/{fname}"
        try:
            async with _AsyncBSC(account_url=_BLOB_URL, credential=DefaultAzureCredential()) as bsc:
                cc = bsc.get_container_client(AZURE_STORAGE_BLOB_CONTAINER_NAME)
                await cc.upload_blob(
                    blob_path, raw, overwrite=True,
                    content_settings=ContentSettings(content_type="image/jpeg"),
                )
        except Exception as exc:
            logger.warning("Blob upload failed for %s: %s – falling back to local disk", blob_path, exc)
            SHOTS_DIR.mkdir(exist_ok=True)
            (SHOTS_DIR / fname).write_bytes(raw)
    else:
        SHOTS_DIR.mkdir(exist_ok=True)
        (SHOTS_DIR / fname).write_bytes(raw)

    return b64, fname, "jpeg"


# ── WebSocket agent runner ───────────────────────────────────────────────────

async def send(ws: WebSocket, msg_type: str, **kwargs):
    await ws.send_json({"type": msg_type, **kwargs})


def _get_openai_client_and_agent() -> tuple[OpenAI, dict[str, str] | None]:
    """
    Returns (openai_client, agent_reference).
    If Foundry endpoint is configured, uses Foundry Agent Computer Use SDK pattern.
    Otherwise falls back to direct Azure OpenAI base-url usage.
    """
    if FOUNDRY_PROJECT_ENDPOINT:
        credential = DefaultAzureCredential()
        project_client = AIProjectClient(endpoint=FOUNDRY_PROJECT_ENDPOINT, credential=credential)
        openai_client = project_client.get_openai_client()

        computer_use_tool = ComputerUsePreviewTool(
            display_width=DISPLAY_WIDTH,
            display_height=DISPLAY_HEIGHT,
            environment="browser",
        )

        agent = project_client.agents.create_version(
            agent_name="ZavaAirComputerUseAgent",
            definition=PromptAgentDefinition(
                model=FOUNDRY_MODEL_DEPLOYMENT_NAME,
                instructions="Computer-use agent for ZavaAir complaint operations.",
                tools=[computer_use_tool],
            ),
            description="Runtime-created CUA agent for screenshot/action loop.",
        )

        return openai_client, {"name": agent.name, "type": "agent_reference"}

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    direct_client = OpenAI(base_url=BASE_URL, api_key=token_provider)
    return direct_client, None


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket):
    await ws.accept()

    if not _agent_semaphore._value:  # noqa: SLF001
        await send(ws, "error", message="Agent is already running.")
        await ws.close()
        return

    async with _agent_semaphore:
        await _run_agent(ws)


async def _run_agent_core(config: dict, job_id: str, send_fn) -> None:
    """Core agent loop. send_fn(msg_type, **kwargs) abstracts WebSocket vs queue output."""
    mode = config.get("mode", "browse")
    tasks = config.get("tasks", [])
    target_url = config.get("targetUrl", "").strip()

    if mode not in {"browse", "create", "update"}:
        await send_fn("error", message=f"Invalid mode '{mode}'. Must be browse, create, or update.")
        return
    if not isinstance(tasks, list) or not tasks:
        await send_fn("error", message="'tasks' must be a non-empty list.")
        return
    if not target_url:
        await send_fn("error", message="'targetUrl' is required.")
        return

    system_prompt = config.get("systemPrompt", DEFAULT_SYSTEM)
    max_iter = int(config.get("maxIterations", MAX_ITERATIONS))
    create_scenario: dict[str, str] | None = None

    task_instructions = build_task_instructions(mode, tasks)
    await send_fn("log", message=f"Mode: {mode} | {len(tasks)} task steps | job_id: {job_id}")
    await send_fn("log", message=f"Screenshot storage: {'blob:' + job_id if BLOB_MODE_ENABLED else 'local'}")

    # Auth
    await send_fn("status", state="authenticating")
    client, agent_reference = _get_openai_client_and_agent()
    if agent_reference:
        await send_fn("log", message="Authenticated with Foundry project and created CUA agent version")
    else:
        await send_fn("log", message="Authenticated with Azure OpenAI (direct mode)")

    # Launch browser
    await send_fn("status", state="launching")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[f"--window-size={DISPLAY_WIDTH},{DISPLAY_HEIGHT}", "--disable-extensions"],
        )
        context = await browser.new_context(
            viewport={"width": DISPLAY_WIDTH, "height": DISPLAY_HEIGHT},
            accept_downloads=True,
        )
        page = await context.new_page()

        await page.goto(target_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        try:
            if mode == "create":
                create_scenario = parse_create_scenario_from_tasks(tasks)
                if create_scenario:
                    await prefill_create_from_tasks(page, create_scenario)
                    await send_fn("log", message="Playwright prefilled create identity fields and focused Description")
                    # Narrow the agent's instructions to just the remaining steps
                    task_instructions = build_task_instructions("create", [
                        {"id": 1, "text": f"The Description field is focused and empty. Type this text exactly into it: '{create_scenario['description']}'."},
                        {"id": 2, "text": "Click 'Submit Complaint' and confirm the success toast appears."},
                    ])
            elif mode == "update":
                update_scenario = parse_update_scenario_from_tasks(tasks)
                if update_scenario:
                    await prefill_update_from_tasks(page, update_scenario)
                    await send_fn("log", message="Playwright prefilled update fields from scenario tasks")
                    task_instructions = build_task_instructions("update", [
                        {"id": 1, "text": "The Notes field is pre-filled. Click 'Save Changes' and confirm the success toast appears."},
                    ])
            elif mode == "browse":
                # Phase 1 — extract filter intent (cheap LLM call if BROWSE_INTENT_DEPLOYMENT is set,
                # otherwise fall back to regex).  Mirrors the two-step pattern in computer-use-agent.ts:
                #   step 1: gpt-4o-mini extracts {severity, status} from natural language
                #   step 2: Playwright applies the filters (saves the AI ~10 steps & tokens)
                if BROWSE_INTENT_DEPLOYMENT:
                    task_text = " ".join(str(t.get("text", "")) for t in tasks)
                    intent = _extract_browse_filter_intent_llm(client, task_text)
                    if intent.get("reasoning") and intent["reasoning"] != "parse error":
                        await send_fn("log", message=f"Browse intent: {intent['reasoning']}")
                    browse_filters: dict | None = None
                    if any(v != "All" for v in [intent["status"], intent["severity"], intent["category"]]) or intent.get("date_from") or intent.get("date_to"):
                        browse_filters = {
                            "status":     intent["status"] if intent["status"] != "All" else "",
                            "severities": [intent["severity"]] if intent["severity"] != "All" else [],
                            "categories": [intent["category"]] if intent["category"] != "All" else [],
                            "date_from":  intent.get("date_from", ""),
                            "date_to":    intent.get("date_to", ""),
                        }
                else:
                    browse_filters = parse_browse_filters_from_tasks(tasks)

                # Phase 2 — Playwright applies the filters; model only observes and summarises.
                if browse_filters:
                    applied = await prefill_browse_filters_from_tasks(page, browse_filters)
                    if applied:
                        await send_fn("log", message=f"Playwright prefilled filters: {', '.join(applied)}")

                    # Build an explicit label showing all three filters (unset ones display as All).
                    _date_label = ""
                    if browse_filters.get("date_from"):
                        _date_label = f", Date={browse_filters['date_from']}"
                        if browse_filters.get("date_to") and browse_filters["date_to"] != browse_filters["date_from"]:
                            _date_label += f"–{browse_filters['date_to']}"
                    filter_label = (
                        f"Status={browse_filters.get('status') or 'All'}, "
                        f"Severity={(browse_filters.get('severities') or ['All'])[0]}, "
                        f"Category={(browse_filters.get('categories') or ['All'])[0]}"
                        + _date_label
                    )

                    # Mirrors the task description in computer-use-agent.ts: tell the model
                    # what is ALREADY done and explicitly forbid touching the dropdowns.
                    task_instructions = build_task_instructions("browse", [
                        {
                            "id": 1,
                            "text": (
                                f"The page is ALREADY filtered — {filter_label} — "
                                "set by automation. DO NOT touch the filter dropdowns."
                            ),
                        },
                        {"id": 2, "text": "Read ALL complaint records currently visible on screen."},
                        {"id": 3, "text": "Scroll down if there are more records below the fold."},
                        {"id": 4, "text": "Open each complaint row and inspect details in the side panel."},
                        {
                            "id": 5,
                            "text": (
                                "Return a concise summary: total count, common themes or issues, "
                                "notable details (flight numbers, passenger names if visible)."
                            ),
                        },
                    ])
        except Exception as prefill_error:
            await send_fn("log", message=f"Prefill assist note: {prefill_error}")

        await send_fn("log", message=f"Page loaded: {target_url}")
        await send_fn("status", state="running")

        # Initial screenshot
        b64, fname, mime = await take_screenshot(page, "initial", job_id=job_id)
        await send_fn("screenshot", image=f"{job_id}/{fname}", iteration=0, action="initial page load")

        # First model call
        if agent_reference:
            initial_payload = {
                "input": [{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": task_instructions},
                        {"type": "input_image", "image_url": f"data:image/{mime};base64,{b64}"},
                    ],
                }],
                "truncation": "auto",
                "extra_body": {"agent_reference": agent_reference},
            }
        else:
            initial_payload = {
                "model": MODEL,
                "tools": [{
                    "type": "computer_use_preview",
                    "display_width": DISPLAY_WIDTH,
                    "display_height": DISPLAY_HEIGHT,
                    "environment": "browser",
                }],
                "instructions": system_prompt,
                "input": [{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": task_instructions},
                        {"type": "input_image", "image_url": f"data:image/{mime};base64,{b64}"},
                    ],
                }],
                "reasoning": {"generate_summary": "concise"},
                "truncation": "auto",
            }

        response = client.responses.create(**initial_payload)

        await send_fn("log", message="Model received initial screenshot")
        last_shot_hash = hashlib.sha256(base64.b64decode(b64)).hexdigest()
        unchanged_frames = 0

        # ── Iteration loop ────────────────────────────────────────────
        for iteration in range(1, max_iter + 1):
            if not hasattr(response, "output") or not response.output:
                await send_fn("log", message="No output from model.")
                break

            response_id = getattr(response, "id", "unknown")

            # Gather reasoning / text
            reasoning_parts = []
            model_text = None
            for item in response.output:
                if hasattr(item, "type") and item.type == "text":
                    model_text = item.text
                if hasattr(item, "type") and item.type == "reasoning":
                    if hasattr(item, "summary") and item.summary:
                        for s in item.summary:
                            txt = s if isinstance(s, str) else getattr(s, "text", "")
                            if txt and txt.strip():
                                reasoning_parts.append(txt)

            if reasoning_parts:
                await send_fn("reasoning", text=" | ".join(reasoning_parts), iteration=iteration)

            # Extract computer calls
            computer_calls = [
                it for it in response.output
                if hasattr(it, "type") and it.type == "computer_call"
            ]

            if not computer_calls:
                # Agent finished
                final_text = model_text or ""
                if not final_text:
                    for item in response.output:
                        if hasattr(item, "type") and item.type == "message":
                            for part in item.content:
                                t = getattr(part, "text", None) or getattr(part, "refusal", None) or ""
                                if t:
                                    final_text = t

                # Browse mode: stop immediately — no form submission needed
                if mode == "browse":
                    await send_fn("done", summary=(final_text or "Browse complete."), iteration=iteration)
                    break

                if await success_toast_visible(page):
                    await send_fn("done", summary=final_text, iteration=iteration)
                    break

                fallback_ok = await finalize_with_fallback(page, mode, create_scenario)
                if fallback_ok:
                    summary = (final_text + "\n\nFallback: final submit/save was executed by Playwright.").strip()
                    await send_fn("done", summary=summary, iteration=iteration)
                    break

                await send_fn("done", summary=(final_text or "Run finished without success toast."), iteration=iteration)
                break

            cc = computer_calls[0]
            call_id = cc.call_id
            action = cc.action

            # Auto-acknowledge safety checks
            acknowledged = []
            if hasattr(cc, "pending_safety_checks") and cc.pending_safety_checks:
                acknowledged = cc.pending_safety_checks
                checks_text = "; ".join(f"{c.code}: {c.message}" for c in acknowledged)
                await send_fn("log", message=f"Safety check auto-acknowledged: {checks_text}")

            # Execute action
            try:
                await page.bring_to_front()
                action_desc = await handle_action(page, action)

                if action.type == "click":
                    await asyncio.sleep(1.5)

                    all_pages = page.context.pages
                    if len(all_pages) > 1:
                        newest = all_pages[-1]
                        if newest != page and newest.url not in ("about:blank", ""):
                            page = newest
                            action_desc += f" → new tab: {newest.url}"
                elif action.type != "wait":
                    await asyncio.sleep(0.5)
            except Exception as e:
                action_desc = f"error: {e}"
                await send_fn("log", message=f"Action error: {e}")

            # Screenshot
            b64, fname, mime = await take_screenshot(page, f"iter{iteration}", job_id=job_id)

            current_hash = hashlib.sha256(base64.b64decode(b64)).hexdigest()
            if current_hash == last_shot_hash:
                unchanged_frames += 1
            else:
                unchanged_frames = 0

            should_scroll_rescue = (
                current_hash == last_shot_hash
                and action.type == "scroll"
            )

            if should_scroll_rescue:
                await page.evaluate(
                    """
                    () => {
                        const modal = document.querySelector('#newComplaintModal.active .modal, #updateModal.active .modal');
                        if (modal) {
                            modal.scrollBy({ left: 0, top: 420, behavior: 'auto' });
                        } else {
                            window.scrollBy({ left: 0, top: 420, behavior: 'auto' });
                        }
                    }
                    """
                )
                await asyncio.sleep(0.35)
                b64, fname, mime = await take_screenshot(page, f"iter{iteration}_scroll_rescue", job_id=job_id)
                current_hash = hashlib.sha256(base64.b64decode(b64)).hexdigest()
                unchanged_frames = 0 if current_hash != last_shot_hash else unchanged_frames
                await send_fn("log", message="Applied scroll-rescue because screenshot showed no visible change")

            last_shot_hash = current_hash
            await send_fn(
                "screenshot",
                image=f"{job_id}/{fname}", iteration=iteration, action=action_desc,
            )

            # Build next request
            input_content = [{
                "type": "computer_call_output",
                "call_id": call_id,
                "output": {
                    "type": "computer_screenshot",
                    "image_url": f"data:image/{mime};base64,{b64}",
                },
            }]

            if acknowledged:
                input_content[0]["acknowledged_safety_checks"] = [
                    {"id": c.id, "code": c.code, "message": c.message}
                    for c in acknowledged
                ]

            try:
                url = page.url
                if url and url != "about:blank":
                    input_content[0]["current_url"] = url
            except Exception:
                pass

            try:
                if agent_reference:
                    follow_up_payload = {
                        "previous_response_id": response_id,
                        "input": input_content,
                        "truncation": "auto",
                        "extra_body": {"agent_reference": agent_reference},
                    }
                else:
                    follow_up_payload = {
                        "model": MODEL,
                        "previous_response_id": response_id,
                        "tools": [{
                            "type": "computer_use_preview",
                            "display_width": DISPLAY_WIDTH,
                            "display_height": DISPLAY_HEIGHT,
                            "environment": "browser",
                        }],
                        "input": input_content,
                        "truncation": "auto",
                    }

                response = client.responses.create(**follow_up_payload)
            except Exception as e:
                await send_fn("error", message=f"API error: {e}")
                break
        else:
            if mode == "browse":
                await send_fn("done", summary=f"Reached max iterations ({max_iter}).", iteration=max_iter)
            else:
                fallback_ok = await finalize_with_fallback(page, mode, create_scenario)
                if fallback_ok:
                    await send_fn(
                        "done",
                        summary=f"Reached max iterations ({max_iter}) and completed via Playwright fallback submit/save.",
                        iteration=max_iter,
                    )
                else:
                    await send_fn("done", summary=f"Reached max iterations ({max_iter}).", iteration=max_iter)

        await context.close()
        await browser.close()


async def _run_agent(ws: WebSocket) -> None:
    """WebSocket entry point: receives config JSON from client, delegates to _run_agent_core."""
    try:
        try:
            raw = await ws.receive_text()
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            await send(ws, "error", message=f"Invalid JSON payload: {exc}")
            return
        # Use caller-supplied job_id or generate a fresh one for this WebSocket session
        job_id = str(config.pop("job_id", None) or uuid.uuid4())
        await ws.send_json({"type": "job_id", "job_id": job_id})
        await _run_agent_core(config, job_id, lambda t, **kw: send(ws, t, **kw))
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error("Unhandled error in agent run: %s", e, exc_info=True)
        try:
            await send(ws, "error", message=str(e))
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ── Queue-driven API mode ─────────────────────────────────────────────────────

class JobTableClient:
    """Thin wrapper around Azure Table Storage for job status persistence."""

    def __init__(self, table_url: str, table_name: str) -> None:
        from azure.data.tables import TableServiceClient as _TSC
        self._svc = _TSC(endpoint=table_url, credential=DefaultAzureCredential())
        self._table_name = table_name
        self._tbl = self._svc.get_table_client(table_name)
        try:
            self._svc.create_table_if_not_exists(table_name)
        except Exception as exc:
            logger.warning("Table init warning: %s", exc)

    def upsert_job(self, job_id: str, fields: dict) -> None:
        entity: dict = {"PartitionKey": "jobs", "RowKey": job_id}
        for k, v in fields.items():
            entity[k] = json.dumps(v) if isinstance(v, list) else v
        try:
            self._tbl.upsert_entity(entity)
        except Exception as exc:
            logger.warning("Table upsert error for job %s: %s", job_id, exc)

    def get_job(self, job_id: str) -> dict | None:
        try:
            raw = dict(self._tbl.get_entity("jobs", job_id))
            return self._deserialize(raw)
        except Exception:
            return None

    def list_recent_jobs(self, n: int = 50) -> list[dict]:
        try:
            rows = list(self._tbl.query_entities("PartitionKey eq 'jobs'"))
            jobs = [self._deserialize(dict(r)) for r in rows]
            jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return jobs[:n]
        except Exception as exc:
            logger.warning("Table list error: %s", exc)
            return []

    def _deserialize(self, entity: dict) -> dict:
        for k, v in entity.items():
            if isinstance(v, str) and v.startswith("["):
                try:
                    entity[k] = json.loads(v)
                except Exception:
                    pass
        return entity


_job_table: JobTableClient | None = (
    JobTableClient(_TABLE_URL, AZURE_STORAGE_TABLE_NAME)
    if QUEUE_MODE_ENABLED else None
)


async def background_agent_run(job_id: str, payload: dict) -> None:
    """Run the CUA agent for a queued job, writing status to Table Storage."""
    log_lines: list[str] = []

    async def send_fn(msg_type: str, **kwargs) -> None:
        if msg_type == "log":
            log_lines.append(str(kwargs.get("message", "")))
            if _job_table:
                _job_table.upsert_job(job_id, {"status": "running", "log": log_lines})
        elif msg_type == "done":
            if _job_table:
                _job_table.upsert_job(job_id, {
                    "status": "completed",
                    "log": log_lines,
                    "summary": str(kwargs.get("summary", "")),
                    "iterations": int(kwargs.get("iteration", 0)),
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
        elif msg_type == "error":
            if _job_table:
                _job_table.upsert_job(job_id, {
                    "status": "failed",
                    "log": log_lines,
                    "error": str(kwargs.get("message", "")),
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })

    async with _agent_semaphore:
        if _job_table:
            _job_table.upsert_job(job_id, {"status": "running", "log": []})
        try:
            await _run_agent_core(payload, job_id, send_fn)
        except Exception as exc:
            logger.error("background_agent_run error for job %s: %s", job_id, exc, exc_info=True)
            if _job_table:
                _job_table.upsert_job(job_id, {
                    "status": "failed",
                    "error": str(exc),
                    "log": log_lines,
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })


async def _queue_poller() -> None:
    """Background coroutine: dequeues messages from Azure Storage Queue and runs agent jobs."""
    from azure.storage.queue import QueueClient

    queue_client = QueueClient(
        account_url=_QUEUE_URL,
        queue_name=AZURE_STORAGE_QUEUE_NAME,
        credential=DefaultAzureCredential(),
    )
    try:
        queue_client.create_queue()
        logger.info("Created queue '%s'.", AZURE_STORAGE_QUEUE_NAME)
    except Exception:
        pass  # Already exists

    logger.info("Queue poller listening on '%s'.", AZURE_STORAGE_QUEUE_NAME)
    while True:
        try:
            messages = queue_client.receive_messages(
                messages_per_page=1,
                visibility_timeout=300,
            )
            msg = next(iter(messages), None)
            if msg is None:
                await asyncio.sleep(3)
                continue

            # Dead-letter after 3 failed attempts
            if msg.dequeue_count > 3:
                logger.warning("Dead-lettering message after %d attempts: %s", msg.dequeue_count, msg.id)
                if _job_table:
                    _job_table.upsert_job(str(uuid.uuid4()), {
                        "status": "failed",
                        "error": "Exceeded max dequeue attempts (3). Message discarded.",
                        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })
                queue_client.delete_message(msg)
                continue

            try:
                body = json.loads(msg.content)
            except json.JSONDecodeError as exc:
                logger.error("Queue message JSON parse error: %s", exc)
                queue_client.delete_message(msg)
                continue

            job_id = str(body.pop("job_id", None) or uuid.uuid4())
            created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            # Convert scenario payload → agent config using existing helpers
            mode, scenario = _materialize_scenario(body, job_id)
            config = {
                "mode": mode,
                "tasks": scenario["tasks"],
                "targetUrl": ZAVA_AIR_URL,
                "systemPrompt": {
                    "browse": DEFAULT_SYSTEM,
                    "create": CREATE_SYSTEM,
                    "update": UPDATE_SYSTEM,
                }.get(mode, DEFAULT_SYSTEM),
                "maxIterations": MAX_ITERATIONS,
            }

            if _job_table:
                _job_table.upsert_job(job_id, {
                    "status": "queued",
                    "mode": mode,
                    "created_at": created_at,
                    "log": [],
                })

            logger.info("Dequeued job %s (mode=%s).", job_id, mode)
            try:
                await background_agent_run(job_id, config)
                queue_client.delete_message(msg)
                logger.info("Job %s completed and message deleted.", job_id)
            except Exception as exc:
                logger.error("Job %s failed: %s", job_id, exc)
                # Don't delete – let visibility timeout expire for retry

        except asyncio.CancelledError:
            logger.info("Queue poller cancelled.")
            break
        except Exception as exc:
            logger.error("Queue poller unexpected error: %s", exc, exc_info=True)
            await asyncio.sleep(5)


# ── REST API endpoints ────────────────────────────────────────────────────────

class AgentRunRequest(BaseModel):
    model_config = {"extra": "allow"}
    job_id: str | None = None


@app.post("/api/run", status_code=202)
async def api_run(request: AgentRunRequest):
    """
    Enqueue a create or update scenario for the CUA agent.
    Returns job_id immediately; poll GET /api/status/{job_id} for result.
    Body: same fields as sim_create_safety.json (create) or simulated_update.json (update).
    Optional 'job_id' field; one is generated if absent.
    """
    if not QUEUE_MODE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Queue mode is disabled. Set AZURE_STORAGE_CONNECTION_STRING to enable.",
        )

    from azure.storage.queue import QueueClient

    payload = request.model_dump(exclude_none=True)
    job_id = str(payload.pop("job_id", None) or uuid.uuid4())
    payload["job_id"] = job_id

    queue_client = QueueClient(
        account_url=_QUEUE_URL,
        queue_name=AZURE_STORAGE_QUEUE_NAME,
        credential=DefaultAzureCredential(),
    )
    try:
        queue_client.create_queue()
    except Exception:
        pass  # Already exists

    queue_client.send_message(json.dumps(payload))
    logger.info("Enqueued job %s via POST /api/run.", job_id)

    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if _job_table:
        _job_table.upsert_job(job_id, {
            "status": "queued",
            "created_at": created_at,
            "log": [],
        })

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "queued",
            "poll_url": f"/api/status/{job_id}",
        },
        headers={
            "Location": f"/api/status/{job_id}",
            "Retry-After": "5",
        },
    )


@app.get("/api/status/{job_id}")
async def api_status(job_id: str):
    """Poll job status. Returns the full job record from Table Storage."""
    if not QUEUE_MODE_ENABLED:
        raise HTTPException(status_code=503, detail="Queue mode is disabled.")
    if not _job_table:
        raise HTTPException(status_code=503, detail="Table storage not initialized.")
    job = _job_table.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    for key in ("PartitionKey", "RowKey", "etag", "Timestamp"):
        job.pop(key, None)
    return job


@app.get("/api/jobs")
async def api_jobs():
    """List the 50 most recent jobs from Table Storage."""
    if not QUEUE_MODE_ENABLED:
        raise HTTPException(status_code=503, detail="Queue mode is disabled.")
    if not _job_table:
        raise HTTPException(status_code=503, detail="Table storage not initialized.")
    jobs = _job_table.list_recent_jobs(50)
    for job in jobs:
        for key in ("PartitionKey", "RowKey", "etag", "Timestamp"):
            job.pop(key, None)
    return {"jobs": jobs, "count": len(jobs)}
