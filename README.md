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
  EventExtractor (Groq)       ← classify into BusinessEvent
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
│   ├── llm/              ← All Groq/AI calls
│   │   ├── event_extractor.py     ← message → BusinessEvent (main LLM call)
│   │   ├── language_pipeline.py   ← Luganda/Swahili normalisation
│   │   ├── media_processor.py     ← Deepgram STT + Silero VAD segmentation
│   │   ├── llm_provider.py        ← Groq → Claude → OpenAI fallback chain
│   │   └── gemini_live.py         ← DEPRECATED — legacy realtime voice, unused
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
GROQ_API_KEY
GROQ_MODEL                    # optional, defaults to llama-3.3-70b-versatile
GROQ_VISION_MODEL             # optional, defaults to llama-3.2-90b-vision-preview
GROQ_WHISPER_MODEL            # optional, defaults to whisper-large-v3
WHATSAPP_VERIFY_TOKEN
WHATSAPP_ACCESS_TOKEN
EVOLUTION_API_URL
EVOLUTION_API_TOKEN
EVOLUTION_INSTANCE_NAME
GOOGLE_APPLICATION_CREDENTIALS
DEFAULT_ORG_ID

# ── Voice note pipeline (recommended) ─────────────────────────────────────
DEEPGRAM_API_KEY              # https://console.deepgram.com/ — STT for voice notes
                              # Better Swahili/Luganda accuracy than Whisper,
                              # auto-detects language, adds punctuation.
                              # Falls back to Groq Whisper if not set.
ELEVENLABS_API_KEY            # https://elevenlabs.io/ — TTS for voice replies
                              # When the customer sends a voice note, the bot
                              # replies with a synthesized voice note. Falls
                              # back to plain text if not set.
ELEVENLABS_VOICE              # voice name (e.g. Rachel, Adam, Bella) — default Rachel

# Optional — legacy Gemini Live voice API
# The /ws/live route has been removed. GEMINI_API_KEY is now unused; the
# legacy module is preserved at backend/services/llm/gemini_live.py for
# reference. Voice interaction now happens via WhatsApp voice notes.
```

### Voice notes via WhatsApp

Voice interaction happens entirely inside WhatsApp — no browser needed.

```
Customer sends voice note
  → Evolution API webhook
  → webhook_handler.py normalises it
  → message_router detects audio
  → media_extraction_workflow
  → media_processor.transcribe_audio_bytes()
      ├─ Deepgram nova-2 (primary — Swahili/Luganda/Sheng)
      └─ Groq Whisper (fallback)
  → ai/extractor.extract_from_audio()
      ├─ Short audio (≤30s): single-pass transcription + one extraction call
      └─ Long audio (>30s):  Silero VAD segments into utterances,
                              each segment transcribed + extracted separately,
                              results merged → multiple events from one memo
  → reply via reply_sender.send_voice_aware()
      ├─ If inbound was voice: ElevenLabs TTS → voice note reply
      └─ Else: plain text reply
```

**Why Deepgram + Silero + ElevenLabs (instead of one provider):**

- **Deepgram nova-2** has dedicated Swahili support and `language="multi"`
  auto-detection — critical for Sheng (Swahili/English code-switching).
  Whisper was trained mostly on English; East African accuracy is poor.
- **Silero VAD** segments long voice memos into separate utterances so a
  3-minute end-of-day recap can produce multiple distinct BusinessEvents
  instead of one merged event. Without VAD, the LLM would parse everything
  in one shot and miss details.
- **ElevenLabs turbo** produces natural-sounding voice replies — voice-in,
  voice-out feels like talking to a person, not a chatbot. Especially
  valuable for low-literacy users.

All three providers are optional — if any is missing, SenteFlow falls back
gracefully (Whisper for STT, single-pass for short audio, text for replies).

### Optional fallback LLM providers

```
ANTHROPIC_API_KEY             # Claude Haiku — used if Groq quota is exhausted
OPENAI_API_KEY                # OpenAI gpt-4o-mini — last-resort fallback
```

---

## Migration from v3

See `docs/migration_v3_to_v4.md`.

The short version: v3 extracted Transactions. v4 extracts BusinessEvents. Transactions are now just one event type among many.
