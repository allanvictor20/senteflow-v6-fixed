# SenteFlow v7 — Review fixes

Fixes from a line-by-line review of v6. SenteFlow is the business owner's
private assistant: the WhatsApp sender is always the owner (or staff), and the
customer is whoever the owner is talking about. Several v6 components assumed
the sender was the customer; v7 fixes that throughout.

## Blocking bugs

- **App could not start.** `TransactionRepository` defined a method called
  `list`, and a later `-> list[dict]` annotation in the same class raised
  `TypeError` at import. Added `from __future__ import annotations`.
- **Groq models retired.** `llama-3.3-70b-versatile` (text, retired 2026-08-16)
  and `llama-3.2-90b-vision-preview` (vision, retired 2025-04-14) are gone.
  Defaults are now `openai/gpt-oss-120b` and `qwen/qwen3.8-27b`, configured in
  one place (`services/llm/models.py`). Retired names set in `.env` are ignored
  with a warning.
- **Voice notes always failed.** `asyncio.get_event_loop()` raises inside
  `asyncio.to_thread` worker threads. Replaced with `_run_coro_blocking`.
- **Media downloads fetched encrypted bytes.** The webhook `url` points at
  WhatsApp's encrypted CDN file. Media is now downloaded decrypted through
  Evolution's `getBase64FromMediaMessage`; encrypted CDN URLs are never saved.
- **Deepgram / ElevenLabs never worked.** The code used old SDK APIs while
  requirements allowed newer, incompatible majors (Deepgram), and passed a
  voice *name* where an *ID* is required (ElevenLabs). Both now use their REST
  APIs over httpx.
- **docker-compose: the bot could not reply.** Dependency injection read
  `EVOLUTION_API_URL/TOKEN`; compose set `EVOLUTION_BASE_URL/API_KEY`. Both
  names are accepted now.

## Security

- **Only linked numbers can use SenteFlow.** New `services/tenancy.py` maps
  WhatsApp numbers to businesses (`OWNER_PHONES` env for the default business,
  `phone_index` in Firestore for others, managed from Settings → WhatsApp
  numbers / `/api/org/phones`). Unknown numbers get a "not linked" reply and no
  data. One SenteFlow number now serves many businesses.
- `/approve` and `/extract` now check org membership; the approver is taken
  from the verified token, not the request body; uploads are capped
  (`MAX_UPLOAD_MB`, default 20).
- Firestore rules: `allow write` + `allow delete: if false` still allowed
  deletes — protected collections now grant `create, update` only. Clients can
  no longer edit or remove the conversation `timeline`. New server-only
  `phone_index` and `meta`.
- `.dockerignore` keeps `.env` and service-account keys out of the image.
- Group chats, broadcasts and status updates are ignored.

## Correctness

- **Large transactions (> UGX 500k) were never saved but the owner was told
  "Recorded".** The router overwrote the dispatcher's approval question, and
  nothing handled YES. There is now a real approval flow: the paused event is
  stored, YES resumes exactly where it stopped, NO cancels it.
- **Clarification recorded transactions twice.** Low-confidence money
  messages are now clarified *before* anything is saved.
- **Customers are resolved from the message**, not the sender: "Brian paid 50k"
  links to customer Brian (asks "Which Brian?" when several match; matches on
  first name or surname). Ledger entries, orders, reminders, customer memory
  and profiles are all keyed on that customer. Before, every customer's data
  collapsed into one record for the owner.
- **"Who owes me?" always said nobody.** Debts are now computed from the ledger
  (`debt_created` − payments), across the full history, with open promises.
- **Payments now close promises/reminders** when they settle them (asks first;
  partial payments leave them open without asking). Reminder replies of YES
  close them and record the outstanding amount.
- **Reminders fire on the promised date** (the morning after), not 24h after
  the message. They are only marked sent when the send actually succeeded.
- **Receipts:** a MoMo "payment received" screenshot is income, not an
  expense; WhatsApp entries are approved (the owner is the approver) so they
  appear on the dashboard; duplicate receipts are skipped.
- **Orders** are created as proper `Order` documents (the old shape failed to
  parse in `/api/orders`) and are created deterministically for order events.
- **Tools run deterministically by event type.** The LLM's action suggestions
  (which usually omitted `create_order` and named non-existent tools) are kept
  for audit only.
- Owners can ask questions in natural language (`business_query` event type),
  including by voice.
- The extraction prompt gets today's date and returns ISO due dates.
- `Ask SenteFlow` on the dashboard crashed on "today" questions (an `async`
  method called without `await`); unpaid customers now come from the ledger.
- "recent" crashed when a transaction had no date.

## Reliability & operations

- Morning briefing: once per business per day at `BRIEFING_HOUR` (default 7)
  in `BUSINESS_TZ` (default Africa/Kampala), de-duplicated across restarts and
  instances with an atomic Firestore marker; sent to every linked number.
- Webhook recovery loop re-runs messages left `processing` by a restart or
  `failed` with attempts left; the processing claim is transactional.
- Sentry is initialised after `.env` is loaded.
- Blocking Firestore calls on the hot path run in worker threads; the context
  engine's "parallel" fetches are actually concurrent now.
- Owner ↔ SenteFlow message log moved from an unbounded array to one document
  per message (`conversations/{id}/interactions`).
- Background loops start/stop via FastAPI lifespan.

## Cleanup

- Removed dead code: unused `workflows/*` (except media extraction),
  duplicate `services/whatsapp/`, `models/`, `gemini_live.py`,
  `intent_detector.py`, `ai/intent_classifier.py`, `ai/clarification_handler.py`,
  `ai/invoice_extractor.py`, unused dependencies (`groq`, `deepgram-sdk`,
  `elevenlabs`, `slowapi`, `prometheus-fastapi-instrumentator`).
- Version strings aligned at 7.0.0; README rewritten for the current flow.

## Business profile settings

- Settings → Business profile is now stored on the server
  (`organizations/{org}/profile/config`, `GET/PUT /api/org/profile`) instead of
  the browser. It was previously saved only to localStorage, so the backend
  never saw it.
- The AI already reads this profile on every message; the page now also edits
  what you sell, location, credit policy and notes.
- The default currency is applied to amounts the owner doesn't label and used
  in summaries, "who owes me?", reminders and the briefing (UGX, KES, TZS,
  RWF, USD).
- The owner-number field and briefing toggle (also browser-only, so they did
  nothing) were replaced by the server-backed WhatsApp numbers section.

## Tests

197 passing, including `tests/test_end_to_end.py`, which sends real webhook
payloads through the whole app against an in-memory Firestore.

## Upgrading an existing deployment

1. Set `OWNER_PHONES` (or link numbers in Settings) — otherwise nobody can use the bot.
2. Remove retired `GROQ_MODEL` / `GROQ_VISION_MODEL` values from `.env`.
3. Deploy `firestore.rules` (`firebase deploy --only firestore:rules`).
4. Firestore may ask for composite indexes on first use (the link in the error creates them).
5. Old `customers` documents keyed by a WhatsApp id (`…@s.whatsapp.net`) were
   written by the v6 bug; they are ignored and can be deleted.

---

# 7.1 — Corrections over WhatsApp, and Ugandan languages with Sunbird AI

## Fixing mistakes from the chat

The owner can now fix anything SenteFlow recorded without leaving WhatsApp:

| Owner says | What happens |
|---|---|
| `undo` / `delete that` / `sazaamu` | Shows the last record, asks YES/NO, removes it |
| `Brian paid 40k not 50k` | Before/after, YES → amount fixed |
| `it was Kato, not Brian` | Moves the record to Kato (asks "which Kato?" if ambiguous) |
| `Brian will pay Monday not Friday` | Promise and reminder move to the new date |
| `that was an expense, not a payment` | Changes the record type |
| `remove Peter's payment` (no match) | Numbered list of recent records → "reply with the number" |

- Nothing changes without a YES, and nothing is deleted: undone ledger entries
  get `status: "rejected"` with `rejected_by` / `rejected_at`; promises,
  reminders, alerts and orders get `status: "cancelled"`; edited receipt
  entries keep a `corrections` history.
- A record made from a WhatsApp message is corrected by reverting everything
  that message did and dispatching a corrected copy through the normal tools,
  so balances, reminders and closed promises come out as if it had been
  right the first time. Undoing a payment that settled a debt reopens the
  promise/reminder it closed (`resolved_by_event` is now stored on them).
- Customer running totals (memory, profiles) are recomputed from the ledger
  after a correction.
- The dashboard hides rejected entries (it lists approved ones) and marks
  corrected entries "✏️ corrected".
- Code: `backend/services/corrections.py`, router `_start_correction` /
  `_handle_correction_reply` / `_handle_correction_choice`, new event type
  `correction`. Tests: `tests/test_corrections.py`.

## Languages (Sunbird AI)

- `services/llm/sunbird.py`: client for Sunbird's current API
  (`/tasks/language_id`, `/tasks/translate`, `/tasks/audio/transcriptions`).
- Every text message: language detected (keywords, then Sunbird), translated
  to English when it isn't English, and the AI is given **both** the original
  and the translation, with instructions to trust the original for names and
  amounts. The event stores `language` and `translated_text`.
- Voice notes are transcribed by Sunbird in the business's language
  (Settings → "Language you speak", default Luganda); Deepgram/Whisper remain
  the fallback and the path for English.
- Optional replies in the owner's language (Settings → Replies → "In the
  language I write in"). A translated reply is only sent if every number in
  it survives translation; otherwise the English reply goes out. Translated
  replies are sent as text (ElevenLabs voices don't speak Luganda well).
- The old v5 Sunbird call used an endpoint shape that no longer exists and was
  never called; it is replaced. "balance", "aha", "leo" and a few other
  English-looking words were removed from the keyword lists.
- Tests: `tests/test_sunbird_language.py` (HTTP mocked).

## Upgrading

1. Set `SUNBIRD_API_KEY` (an access token from `POST https://api.sunbird.ai/auth/token`).
   `SUNBIRD_API_URL` is now the base URL (default `https://api.sunbird.ai`) —
   remove any old value that ends in a path.
2. Set each business's language in Settings → Business profile (default Luganda).
3. Redeploy the backend; no Firestore index or rules changes are needed.
