# SenteFlow v5 — Changes from v4

All changes are based on the stress-test analysis in `senteflow_ideas_stress_test.md`.
Only ideas rated P1, P2, and P3 are implemented here. P4+ are left for future sprints.

---

## P1 — Foundation (implemented)

### IDEA 03 — Memory Compaction (15-line change, immediate cost + quality win)
**File**: `backend/services/memory/context_engine.py`

`ContextEngine._customer_context()` now reads `CustomerMemory` first (1 Firestore read,
one formatted summary line). Raw transaction recomputation is the fallback only for
first-time senders with no memory yet.

- Before: 50 raw Firestore reads recomputing what `CustomerMemory` already stored
- After: 1 read, instant context, `summary` field passed to LLM

### IDEA 10 — Parallel Context Loading (30-minute change)
**File**: `backend/services/memory/context_engine.py`

`get_context()` now fires all 6 sub-fetches simultaneously with `asyncio.gather()`.

- Before: 6 sequential reads ≈ 480ms overhead per message
- After: all 6 run in parallel ≈ 80ms (slowest single read)

### IDEA 06 — Multi-Provider LLM Abstraction
**Files**: `backend/services/llm/llm_provider.py` (new), `backend/services/llm/event_extractor.py`

Provider chain: Gemini → Claude Haiku → OpenAI GPT-4o-mini.
When Gemini quota is exhausted, the next provider is tried silently.

- Before: Gemini hardcoded in `event_extractor.py`. Quota exhaustion = complete offline
- After: Silent failover. Owner sees no disruption.
- Configure: set `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` in `.env`

### IDEA 08 — Business Profile Memory
**Files**: `backend/domain/business_profile/` (new), `backend/repositories/business_profile_repository.py` (new), `backend/services/llm/event_extractor.py`

Per-org `BusinessProfile` (name, products, credit policy, operating hours) is injected
into every LLM system prompt. Gemini now reasons about THIS specific business.

- Before: Generic "Ugandan SME" prompt. "Give him the usual" → UNKNOWN event
- After: Profile injected. "The usual" resolves to the org's actual default product.
- Store profile: write to Firestore `organizations/{org_id}/profile/config`

---

## P2 — Correctness and Trust (implemented)

### IDEA 01 — Business Tool System (typed registry + entity validation)
**File**: `backend/services/actions/action_dispatcher.py`

All actions are now typed `ToolBase` subclasses with `required_entities`.
If required entities are missing, the tool returns a clarification question
instead of a broken confirmation.

- Before: `ScheduleReminderTool` would fire with `debtor="Unknown"` and `due_date="soon"`
- After: Returns "Who should I remind, and by when? e.g. 'Remind Bruno to pay by Friday'"

### IDEA 09 — Permission System
**Files**: `backend/domain/permissions/model.py` (new), `backend/services/actions/action_dispatcher.py`

`ActionDispatcher.dispatch()` checks `OrgConfig` before executing each action.
High-risk actions (`reduce_debt`, large `update_ledger`) ask the owner for confirmation.

- Before: `reduce_debt` fired automatically — could clear partial payments silently
- After: "Should I clear this debt? Reply YES to confirm." 
- Defaults: `reduce_debt` = `APPROVAL_REQUIRED`, `update_ledger` > 500k = threshold

### IDEA 12 — Luganda Detection Vocabulary Gap
**File**: `backend/services/llm/language_pipeline.py`

Vocabulary expanded from 6 words to 50+ per language (Luganda, Runyankole, Ateso, Luo, Swahili).
Added two-stage detection: keywords first, then LLM-based language detection as fallback.

- Before: "Bambi nkuwe omubare wange" → classified as English → UNKNOWN event
- After: Detected as Luganda → Sunbird translates → correct event extracted

---

## P3 — Capability (implemented)

### IDEA 07 — Proactive Agents (wired up)
**Files**: `backend/main.py`, `backend/tasks/reminder_sender.py` (unchanged)

`reminder_loop()` and `daily_briefing_loop()` now start on FastAPI startup.
The operational intelligence functions existed in v4 but were "Option C" comments.

- Before: Owner had to message in to see any insights. System was silent.
- After: Owner gets hourly overdue reminders + daily morning briefing automatically.
- Requires: `DEFAULT_OWNER_PHONE` set in `.env`

### IDEA 13 — list_orders() Collection Scan Bug
**File**: `backend/services/conversation/state_machine.py`

`_find_open_order()` now uses a direct Firestore query filtered by `customer_id` + `status`
instead of loading 25 docs and filtering in Python.

- Before: Silent correctness bug. Orders at position 26+ in Firestore return were missed.
- After: Indexed query. Correct regardless of order count.
- Note: Includes safe fallback to Python filter if Firestore index doesn't exist yet.

---

## New files
- `backend/services/llm/llm_provider.py` — Multi-provider LLM abstraction
- `backend/domain/business_profile/model.py` — BusinessProfile domain model
- `backend/domain/business_profile/__init__.py`
- `backend/domain/permissions/model.py` — Permission system
- `backend/domain/permissions/__init__.py`
- `backend/repositories/business_profile_repository.py` — Firestore persistence for profile

## Modified files
- `backend/services/memory/context_engine.py` — IDEA 03 + IDEA 10
- `backend/services/llm/event_extractor.py` — IDEA 06 + IDEA 08
- `backend/services/llm/language_pipeline.py` — IDEA 12
- `backend/services/actions/action_dispatcher.py` — IDEA 01 + IDEA 09
- `backend/services/conversation/state_machine.py` — IDEA 13
- `backend/main.py` — IDEA 07
- `backend/bootstrap/dependency_injection.py` — wire new repos + OrgConfig
- `backend/integrations/whatsapp/message_router.py` — thread new deps
- `backend/workflows/process_message.py` — thread new deps
- `backend/requirements.txt` — add anthropic, openai
- `backend/.env.example` — document new env vars

## Not implemented (P4+)
- IDEA 02 — Planning Layer (P5, 3-4 weeks effort)
- IDEA 04 — Org-Specific Plugins (P6, v3 feature)
- IDEA 05 — Business Timeline narrative (P4, v1.5)
- IDEA 11 — Pre/Post Hook System (P5, v2)
