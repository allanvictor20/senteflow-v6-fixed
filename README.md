# SenteFlow AI — v5

**WhatsApp-native AI business memory assistant for small businesses in East Africa.**

Instead of learning ERP software, business owners text SenteFlow on WhatsApp the same way they already talk — and SenteFlow remembers, organises, and acts on everything.

---

## What SenteFlow Does

SenteFlow listens to WhatsApp conversations and builds a living memory of your business:

- A customer says "I'll pay Friday" → SenteFlow records the promise and reminds you if they don't
- A supplier sends a delivery note → SenteFlow logs the stock update
- You say "Only 3 bags cement left" → SenteFlow raises a low-stock alert
- You ask "Who owes me money?" → SenteFlow searches its memory and replies instantly

---

## Core Architecture Principle

> A message does not become a transaction. A message becomes a **BusinessEvent**.

Transactions are only one type of business event. SenteFlow understands all of them:

```
sale · expense · payment_received · payment_promised
order_created · order_completed · delivery_requested
customer_inquiry · negotiation · complaint
inventory_update · low_stock_alert
supplier_message · reminder_request
```

---

## System Flow

```
Incoming WhatsApp Message
          ↓
  ContextEngine.build()       ← load customer memory, open orders, history
          ↓
  EventExtractor (Gemini)     ← classify into BusinessEvent
          ↓
  UpdateMemoryWorkflow        ← persist what we learned
          ↓
  ActionDispatcher            ← ledger, reminders, alerts
          ↓
  GenerateReplyWorkflow       ← craft WhatsApp response
          ↓
  WhatsApp Reply
```

---

## Folder Structure

```
backend/
├── domain/               ← Pure data models, no I/O
│   ├── events/           ← BusinessEvent, EventType (the core)
│   ├── customers/        ← Customer, CustomerProfile
│   ├── conversations/    ← BusinessConversation, ConversationStage
│   ├── orders/           ← Order, OrderStatus
│   ├── reminders/        ← Reminder
│   ├── debts/            ← Debt, DebtStatus
│   └── business_memory/  ← CustomerMemory (long-term memory per customer)
│
├── workflows/            ← Business orchestration (the glue)
│   ├── process_message.py    ← MAIN ENTRY POINT for a WhatsApp message
│   ├── build_event.py        ← raw text → BusinessEvent
│   ├── update_memory.py      ← event → memory update
│   ├── generate_reply.py     ← event → WhatsApp reply
│   ├── handle_order.py       ← order-related events → Order record
│   ├── handle_payment.py     ← financial events → ledger
│   └── event_extraction_workflow.py  ← file upload → BusinessEvents
│
├── services/             ← Pure capabilities (no orchestration)
│   ├── llm/              ← All Gemini/AI calls
│   │   ├── event_extractor.py     ← message → BusinessEvent (main LLM call)
│   │   ├── language_pipeline.py   ← Luganda/Swahili normalisation
│   │   ├── media_processor.py     ← audio transcription, image OCR
│   │   └── gemini_live.py         ← streaming Gemini session
│   ├── memory/           ← Business memory management
│   │   ├── context_engine.py         ← assembles context before LLM calls
│   │   ├── memory_engine.py          ← updates CustomerMemory from events
│   │   ├── customer_memory_service.py
│   │   └── operational_intelligence.py  ← detect lost customers, overdue debts
│   └── whatsapp/         ← WhatsApp send primitives
│       ├── client.py
│       └── reply_sender.py
│
├── repositories/         ← Firestore persistence, no business logic
│   ├── event_repository.py
│   ├── customer_repository.py
│   ├── conversation_repository.py
│   ├── memory_repository.py
│   └── order_repository.py
│
├── integrations/
│   └── whatsapp/         ← Evolution API webhook handling + message routing
│
├── bootstrap/            ← App construction + dependency wiring
│   ├── app_factory.py
│   └── dependency_injection.py
│
├── api/routes/           ← FastAPI route handlers
├── prompts/              ← LLM system prompts
├── utils/                ← currency normalisation, helpers
├── tasks/                ← Background tasks (reminder sender)
└── main.py               ← Entry point
```

---

## Firestore Collections

```
organizations/{org_id}/
  events/{event_id}           ← all BusinessEvents (replaces transactions/)
  customers/{customer_id}     ← customer records
  conversations/{sender_id}   ← active conversation state per customer
  memory/{customer_id}        ← CustomerMemory (long-term per-customer facts)
  orders/{order_id}           ← order records
  reminders/{reminder_id}     ← scheduled follow-ups
  promises/{event_id}         ← payment promises (subset of events)
  alerts/{event_id}           ← operational alerts
```

---

## Getting Started

```bash
# Backend
cd backend
cp .env.example .env          # fill in your keys
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
cd frontend
cp .env.example .env          # fill in Firebase config
npm install
npm run dev
```

### Required environment variables

```
GEMINI_API_KEY
WHATSAPP_VERIFY_TOKEN
WHATSAPP_ACCESS_TOKEN
EVOLUTION_API_URL
EVOLUTION_API_TOKEN
EVOLUTION_INSTANCE_NAME
GOOGLE_APPLICATION_CREDENTIALS
DEFAULT_ORG_ID
```

---

## Migration from v3

See `docs/migration_v3_to_v4.md`.

The short version: v3 extracted Transactions. v4 extracts BusinessEvents. Transactions are now just one event type among many.
