# SenteFlow AI — v7

**WhatsApp-native AI business memory assistant for small businesses in East Africa.**

Instead of learning ERP software, a business owner keeps doing what they already do — talking to their customers on WhatsApp — and tells SenteFlow, on a separate WhatsApp chat, what happened. SenteFlow remembers, organises, and follows up. The owner never has to leave WhatsApp.

---

## What SenteFlow Does

The owner (or their staff) messages SenteFlow:

- "Brian took 3 bags of cement on credit, 150k, will pay Friday" → debt recorded for Brian, reminder set for Saturday morning
- "Brian paid 50k" → payment recorded; Brian now owes 100k
- A MoMo screenshot or receipt photo → transactions extracted and recorded
- A voice note (English, Luganda, Swahili, mixed) → transcribed and handled like a text
- "Who owes me money?" / "How much does Brian owe?" / "summary" → answered from the records
- Every morning at 7am → a briefing: overdue promises, low stock, quiet customers, revenue trend

Customers never message SenteFlow. **The sender is always the owner; the customer is whoever the owner is talking about.** Each business only sees its own data, and only numbers linked to a business can use it.

---

## Core Architecture Principle

> A message does not become a transaction. A message becomes a **BusinessEvent**.

```
sale · expense · payment_received · payment_promise · debt_created
order_created · customer_inquiry · negotiation · complaint
inventory_update · low_stock_alert · supplier_message
reminder_request · business_query
```

---

## System Flow

```
WhatsApp message to the SenteFlow number
  → Evolution API webhook  (POST /api/webhooks/whatsapp)
  → normalise + de-duplicate + queue (Firestore)            → 200 OK fast
  → which business is this number linked to?  (services/tenancy.py)
        unknown number → "not linked" reply, no data
  → that business's MessageRouter
      pending question?  YES/NO approval · reminder "YES" · clarification answer
      ContextEngine                 owner-level context
      EventExtractor (LLM)          message → BusinessEvent
      business_query?               answer from the ledger, stop
      unsure about money?           ask BEFORE recording
      CustomerResolver              "Brian" → which customer (asks if ambiguous)
      ActionDispatcher              ledger · reminders · orders · profiles
                                    (pauses for YES on big amounts / closing debts)
  → WhatsApp reply (voice note if the owner sent one)

Background (per business):
  reminder_loop        hourly — promises due, follow-ups
  daily_briefing_loop  once a day after BRIEFING_HOUR local time
  webhook recovery     re-runs messages a restart interrupted
```

---

## Folder Structure

```
backend/
├── main.py                ← entry point, background loops
├── bootstrap/             ← app factory + dependency wiring (per-business routers)
├── api/
│   ├── routes/whatsapp.py ← webhook, queue, tenancy routing, recovery
│   ├── routes/org.py      ← link/unlink WhatsApp numbers for a business
│   ├── routes/*.py        ← customers, orders, tasks, insights
│   └── routes_legacy.py   ← /extract, /approve, /summary, ... (dashboard)
├── integrations/whatsapp/ ← Evolution client, normaliser, router, reply sender
├── services/
│   ├── tenancy.py         ← phone number → business
│   ├── llm/               ← provider chain, models, extractor, media/voice
│   ├── actions/           ← ActionDispatcher + tools
│   ├── conversation/      ← customer resolution, state machine, entity links
│   ├── memory/            ← context engine, customer memory, insights
│   ├── responses/         ← WhatsApp reply text, ledger balances
│   └── ledger.py          ← saving extracted transactions
├── tasks/                 ← reminder_sender.py, briefing.py
├── repositories/          ← Firestore persistence
├── domain/                ← pure models (BusinessEvent, Order, permissions, ...)
├── workflows/             ← media_extraction_workflow.py (files → transactions)
└── tests/                 ← unit + end-to-end (in-memory Firestore)
```

---

## Firestore Collections

```
phone_index/{digits}                 ← WhatsApp number → org_id (server-only)
organizations/{org_id}/
  events/{id}             ← BusinessEvents and ledger entries
  customers/{customer_id} ← customers (resolved from message content)
  customer_profiles/{id}  ← rich profile, same id as the customer
  memory/{customer_id}    ← CustomerMemory
  conversations/{id}      ← owner chat state (pending questions) / customer flow state
    interactions/{id}     ← owner ↔ SenteFlow message log
  orders/{order_id}
  reminders/{event_id}    ← due_at drives when the owner is nudged
  promises/{event_id}
  alerts/{event_id}
  webhook_queue/{msg_id}  ← server-only
  meta/briefing_{date}    ← once-a-day briefing marker
  members/{uid}           ← dashboard access
```

Deploy `firestore.rules` with the Firebase CLI after changing it.

---

## Getting Started

```bash
# Backend
cd backend
cp .env_example .env          # fill in your keys + OWNER_PHONES
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
cd frontend
cp .env_example .env          # Firebase config + REACT_APP_ORG_ID (= DEFAULT_ORG_ID)
npm install
npm start
```

Or everything with Docker: `docker compose up` (set the variables in a root `.env`).

### Linking WhatsApp numbers

Only linked numbers can use SenteFlow:

- Default business: put the owner/staff numbers in `OWNER_PHONES` (comma-separated, e.g. `+256700000001,+256700000002`).
- Any business: dashboard → Settings → WhatsApp numbers (or `POST /api/org/phones?org_id=...`).

### Environment variables

See `backend/.env_example` for the full annotated list. The essentials:

```
GROQ_API_KEY                  required
GROQ_MODEL / GROQ_VISION_MODEL / GROQ_WHISPER_MODEL
                              optional — defaults: openai/gpt-oss-120b,
                              qwen/qwen3.8-27b, whisper-large-v3.
                              Groq retired llama-3.3-70b-versatile and the
                              llama vision models; retired names are ignored
                              with a warning.
ANTHROPIC_API_KEY / OPENAI_API_KEY      optional fallbacks
EVOLUTION_API_URL, EVOLUTION_API_TOKEN, EVOLUTION_INSTANCE_NAME
GOOGLE_APPLICATION_CREDENTIALS, DEFAULT_ORG_ID
OWNER_PHONES                  numbers allowed for the default business
BUSINESS_TZ (Africa/Kampala), BRIEFING_HOUR (7)
DEEPGRAM_API_KEY              optional — voice notes (falls back to Groq Whisper)
ELEVENLABS_API_KEY            optional — voice-note replies (falls back to text)
SENTRY_DSN                    optional
ENVIRONMENT                   production (default) enforces auth + webhook checks
```

### Tests

```bash
cd backend && python -m pytest -q
```

`tests/test_end_to_end.py` drives real webhook payloads through the whole app against an in-memory Firestore with the LLM faked.

---

## Voice notes

```
Owner sends a voice note
  → decrypted download via Evolution (getBase64FromMediaMessage)
  → transcription: Deepgram (if configured) → Groq Whisper fallback
     long notes (>30s) are split into utterances with Silero VAD
  → each utterance goes through the normal text flow
     (so voice can record debts, ask "who owes me?", answer YES, ...)
  → reply as a voice note via ElevenLabs (if configured), else text
```

---

## Migration notes

- v7: see `CHANGES_V7.md`. Existing deployments: set `OWNER_PHONES`, clear any retired `GROQ_*` model names from `.env`, and redeploy `firestore.rules`.
- v3 → v4: see `docs/migration_v3_to_v4.md`.
