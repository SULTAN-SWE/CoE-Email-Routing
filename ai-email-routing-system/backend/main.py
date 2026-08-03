import asyncio
import copy
import email
import imaplib
import json
import logging
import os
import re
import smtplib
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MAIL_DIR = DATA_DIR / "messages"
LOG_FILE = DATA_DIR / "routing_logs.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
DATA_DIR.mkdir(exist_ok=True)
MAIL_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("email-router")

DEPARTMENTS = [
    "Operations Office", "HR & Admin Department", "PMO Office", "Projects Delivery Department",
    "Finance Department", "Strategic Partnerships Development Department", "Business Development Department",
    "Quality and Governance Department", "IT Department", "Procurement & Contracts", "Training Entities Department",
    "Strategic Partnerships Follow-up Department", "Training Solutions Department", "Marketing Department",
    "Facility Management Department", "Account Management Department", "Projects Department",
]
DEFAULT_TARGETS = {name: f"{re.sub(r'[^a-z0-9]+', '.', name.lower()).strip('.')}@company.com" for name in DEPARTMENTS}

SYSTEM_PROMPT = """You are an expert corporate email routing assistant. Read the following email subject and body. Your ONLY task is to categorize it into exactly ONE of the following 17 departments: Operations Office, HR & Admin Department, PMO Office, Projects Delivery Department, Finance Department, Strategic Partnerships Development Department, Business Development Department, Quality and Governance Department, IT Department, Procurement & Contracts, Training Entities Department, Strategic Partnerships Follow-up Department, Training Solutions Department, Marketing Department, Facility Management Department, Account Management Department, Projects Department. Reply ONLY with the exact name of the department. Do not add any explanations or extra text. If the email is spam or unclear, reply with 'IT Department' as a fallback."""

class Settings(BaseModel):
    imap_host: str = Field(default_factory=lambda: os.getenv("IMAP_HOST", ""))
    imap_port: int = Field(default_factory=lambda: int(os.getenv("IMAP_PORT", "993")))
    imap_user: str = Field(default_factory=lambda: os.getenv("IMAP_USER", ""))
    imap_password: str = Field(default_factory=lambda: os.getenv("IMAP_PASSWORD", ""))
    smtp_host: str = Field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = Field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = Field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = Field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    from_email: str = Field(default_factory=lambda: os.getenv("FROM_EMAIL", "") or os.getenv("IMAP_USER", ""))
    ollama_url: str = Field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = Field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b"))
    check_interval: int = Field(default_factory=lambda: int(os.getenv("CHECK_INTERVAL_SECONDS", "300")), ge=30)
    targets: dict[str, str] = DEFAULT_TARGETS.copy()

class ManualRoute(BaseModel):
    department: str

def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback

def get_settings() -> Settings:
    values = read_json(SETTINGS_FILE, {})
    return Settings(**values) if values else Settings()

def save_settings(settings: Settings) -> None:
    SETTINGS_FILE.write_text(settings.model_dump_json(indent=2), encoding="utf-8")

def get_logs() -> list[dict[str, Any]]:
    return read_json(LOG_FILE, [])

def save_logs(logs: list[dict[str, Any]]) -> None:
    tmp = LOG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(logs[-1000:], indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LOG_FILE)

def clean_text(msg: Message) -> str:
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                try: parts.append(part.get_content())
                except Exception: pass
    elif msg.get_content_type() == "text/plain":
        try: parts.append(msg.get_content())
        except Exception: pass
    if not parts and msg.get_content_type() == "text/html":
        raw = msg.get_payload(decode=True) or b""
        parts.append(re.sub(r"<[^>]+>", " ", raw.decode(errors="replace")))
    return "\n".join(parts).strip()[:1500] # تقليل النص للسرعة القصوى

def classify(subject: str, body: str, settings: Settings) -> str:
    prompt = f"Subject: {subject}\n\nBody:\n{body}"

    print("\n========== OLLAMA ==========")
    print("Model:", settings.ollama_model)
    print("URL:", settings.ollama_url)
    print("Subject:", subject)

    response = requests.post(
        f"{settings.ollama_url.rstrip('/')}/api/generate",
        json={
            "model": settings.ollama_model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": 20
            }
        },
        timeout=30
    )

    print("HTTP:", response.status_code)
    print("RAW RESPONSE:")
    print(response.text)

    response.raise_for_status()

    answer = response.json().get("response", "").strip().strip('"').strip()

    print("CLASSIFIED AS:", answer)
    print("============================\n")

    for department in DEPARTMENTS:
        if answer.lower() == department.lower():
            return department

    print("Unknown department, fallback -> IT Department")
    return "IT Department"

def forward_message(raw: bytes, department: str, settings: Settings) -> None:
    if department not in DEPARTMENTS:
        raise ValueError("Invalid department")
    original = BytesParser(policy=policy.default).parsebytes(raw)

    msg = EmailMessage()

    msg["From"] = settings.from_email
    msg["To"] = settings.targets[department]
    msg["Subject"] = f"[AI Routed - {department}] {original.get('Subject','')}"

    body = clean_text(original)

    msg.set_content(
        f"""This email was automatically routed.

Original Sender:
{original.get('From')}

Department:
{department}

-------------------------

{body}
"""
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def make_log(raw: bytes, department: Optional[str], status: str, error: Optional[str] = None, uid: Optional[str] = None) -> dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    item = {"id": uid or uuid.uuid4().hex, "date": datetime.now(timezone.utc).isoformat(), "sender": msg.get("From", ""), "subject": msg.get("Subject", "(no subject)"), "department": department or "", "status": status, "error": error}
    (MAIL_DIR / f"{item['id']}.eml").write_bytes(raw)
    return item

# ✅ يقرأ كل الإيميلات غير المقروءة بدون أي حدود
def process_unread() -> int:
    settings = get_settings()
    required = [settings.imap_host, settings.imap_user, settings.imap_password, settings.smtp_host]
    if not all(required):
        logger.warning("Email credentials are not configured; worker is idle")
        return 0
    count = 0
    mailbox = None
    try:
        mailbox = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        mailbox.login(settings.imap_user, settings.imap_password)
        mailbox.select("INBOX")
        
        typ, data = mailbox.search(None, '(UNSEEN)')
        print("IMAP SEARCH:", typ, data)
        logger.info(f"Found {len(data[0].split()) if data[0] else 0} unread emails")
        unread_list = data[0].split() if data[0] else []
        
        for num in unread_list:
            item = None
            try:
                typ, fetched = mailbox.fetch(num, "(RFC822)")
                raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
                if not raw: continue
                
                item = make_log(raw, None, "Pending")

                parsed = BytesParser(policy=policy.default).parsebytes(raw)

                print("=" * 60)
                print("Processing:", parsed.get("Subject", "(No Subject)"))
                print("From:", parsed.get("From"))

                department = classify(
                parsed.get("Subject", ""),
                clean_text(parsed),
                settings,
                        )

                print("Department:", department)

                forward_message(raw, department, settings)

                item.update(
                department=department,
                status="Forwarded",
                error=None,
                )

                logs = get_logs()
                logs.append(item)
                save_logs(logs)

                mailbox.store(num, "+FLAGS", "\\Seen")

                print("Forwarded Successfully")
                print("=" * 60)
            except Exception as exc:
                logger.exception("Could not route message")
                if item:
                    item["error"] = str(exc)
                    logs = get_logs(); logs.append(item); save_logs(logs)
            finally:
                pass
            count += 1
    except Exception:
        logger.exception("IMAP polling failed")
    finally:
        if mailbox:
            try: mailbox.logout()
            except Exception: pass
    return count

stop_event = threading.Event()
def worker() -> None:
    while not stop_event.is_set():
        process_unread()
        stop_event.wait(get_settings().check_interval)

@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event.clear(); task = asyncio.create_task(asyncio.to_thread(worker))
    yield
    stop_event.set(); await asyncio.wait_for(task, timeout=10)

app = FastAPI(title="AI Email Routing System", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
def health(): return {"status": "ok", "ollama": get_settings().ollama_url}

@app.get("/api/logs")
def logs(): return get_logs()[::-1]

@app.get("/api/departments")
def departments(): return [{"name": name, "email": get_settings().targets.get(name, DEFAULT_TARGETS[name])} for name in DEPARTMENTS]

@app.get("/api/settings")
def settings_view():
    data = get_settings().model_dump()
    data["imap_password"] = ""; data["smtp_password"] = ""
    return data

@app.put("/api/settings")
def update_settings(settings: Settings):
    old = get_settings()
    if not settings.imap_password: settings.imap_password = old.imap_password
    if not settings.smtp_password: settings.smtp_password = old.smtp_password
    save_settings(settings); return {"ok": True}

@app.post("/api/process")
def process_now(): return {"processed": process_unread()}

@app.post("/api/logs/{log_id}/forward")
def manual_forward(log_id: str, route: ManualRoute):
    if route.department not in DEPARTMENTS: raise HTTPException(400, "Invalid department")
    path = MAIL_DIR / f"{log_id}.eml"
    if not path.exists(): raise HTTPException(404, "Original message not found")
    try: forward_message(path.read_bytes(), route.department, get_settings())
    except Exception as exc: raise HTTPException(502, str(exc))
    records = get_logs()
    for record in records:
        if record["id"] == log_id: record.update(department=route.department, status="Forwarded", error=None, manually_routed=True)
    save_logs(records); return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)