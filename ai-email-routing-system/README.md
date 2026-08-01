# AI Email Routing System

A small internal digital receptionist that reads unread reception emails, classifies each one with **local Ollama**, forwards the original MIME message (including attachments) to the selected department, and records the result in a JSON log.

## Architecture

- **Backend:** Python 3.11+, FastAPI, `imaplib`, `smtplib`, `email`, `requests`
- **AI:** Ollama running locally; default model `qwen2.5`
- **Storage:** `backend/data/routing_logs.json` plus the original `.eml` message files required for manual retries
- **Frontend:** React + Vite + TailwindCSS + lucide-react
- **Security model:** deliberately no login or roles, as requested. Keep the service on a trusted internal network and protect ports with a firewall.

## 1. Install Ollama

Install Ollama from <https://ollama.com/download>, then start it. Pull the model:

```bash
ollama pull qwen2.5
# Optional alternative:
# ollama pull llama3
```

Verify the local API:

```bash
curl http://localhost:11434/api/tags
```

## 2. Configure the backend

```bash
cd backend
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with the reception mailbox and SMTP values:

```dotenv
IMAP_HOST=imap.example.com
IMAP_PORT=993
IMAP_USER=reception@company.com
IMAP_PASSWORD=your-password
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=reception@company.com
SMTP_PASSWORD=your-password
FROM_EMAIL=reception@company.com
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5
CHECK_INTERVAL_SECONDS=300
```

The initial routing targets are generated as placeholders such as `finance.department@company.com`. Change them in the dashboard's **Settings** modal. Settings are stored in `backend/data/settings.json` and password fields are never returned to the browser; leaving a password blank preserves the saved value.

Start the API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The worker starts automatically with the API and checks unread mail every `CHECK_INTERVAL_SECONDS`. Use the dashboard's **Check inbox now** button to trigger an immediate pass.

## 3. Configure and run the frontend

In another terminal:

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Open <http://localhost:5173>. To use a different backend URL, create `frontend/.env`:

```dotenv
VITE_API_URL=http://localhost:8000/api
```

For a production preview:

```bash
npm run build
npm run preview -- --host 0.0.0.0
```

## How routing works

1. The IMAP worker searches the Inbox for `UNSEEN` messages.
2. It extracts subject and readable text parts (HTML is reduced to text; attachments are excluded from classification).
3. The exact required system prompt is sent to Ollama with temperature `0`.
4. The response is validated against the 17 allowed department names. Any non-exact answer falls back to **IT Department**.
5. The original parsed MIME message is copied, addressed to the configured department, and sent over SMTP. This preserves multipart content and attachments.
6. The message is marked as read and both the routing log and original `.eml` are saved locally.
7. Failed messages remain in the log as `Failed`; the dashboard can manually forward the saved original.

## API endpoints

- `GET /api/health` — health check
- `GET /api/logs` — newest routing logs first
- `GET /api/departments` — departments and target addresses
- `GET /api/settings` — non-secret settings
- `PUT /api/settings` — update settings
- `POST /api/process` — process unread messages immediately
- `POST /api/logs/{id}/forward` with `{ "department": "IT Department" }` — manual route

## The 17 departments

Operations Office; HR & Admin Department; PMO Office; Projects Delivery Department; Finance Department; Strategic Partnerships Development Department; Business Development Department; Quality and Governance Department; IT Department; Procurement & Contracts; Training Entities Department; Strategic Partnerships Follow-up Department; Training Solutions Department; Marketing Department; Facility Management Department; Account Management Department; Projects Department.

## Operational notes

- Test with a dedicated mailbox before connecting a production inbox.
- Confirm the SMTP provider permits sending to all department addresses.
- Some providers require an app password or OAuth-specific SMTP/IMAP configuration.
- The included SMTP code uses STARTTLS for ports other than 25. If your provider requires implicit TLS on port 465, adapt `forward_message` to use `smtplib.SMTP_SSL`.
- Back up `backend/data` if routing history matters. The app retains the newest 1,000 logs.
- No paid AI or cloud AI API is used.
