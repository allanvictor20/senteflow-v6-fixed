# SenteFlow v5 — Post-Evaluation Fixes

All fixes are targeted at issues found during the code review.
No new features added — correctness and security only.

---

## FIX 01 — Missing `domain/models.py` shim
**Files**: `backend/domain/models.py` (new)

Multiple files imported from `domain.models` which did not exist:
- `ai/extractor.py` → `ExtractionResult, FieldConfidence, SourceTrace, Transaction`
- `services/actions/action_dispatcher.py` → `Transaction`
- `services/conversation/entity_linker.py` → `EntityLink`
- `services/conversation/state_machine.py` → `ConversationState, ConversationTimelineEntry, PendingAction`
- `services/responses/reply_generator.py` → `ExtractionResult, FinancialSummary`
- `tests/test_validation.py` → `FieldConfidence`
- `tests/test_context_engine.py` → `FinancialSummary`

These would all raise `ImportError` at startup. The shim provides all classes
from one module, with proper Pydantic models. New code should import from the
specific domain sub-packages instead.

---

## FIX 02 — Import path inconsistency (`core.events` vs `domain.events`)
**Files**: 
- `backend/services/actions/action_dispatcher.py`
- `backend/services/conversation/state_machine.py`
- `backend/workflows/event_extraction_workflow.py`

`BusinessEvent` and `EventType` live in `domain/events/`. Some files imported
from `core.events.*` (which doesn't exist). Normalised all imports to
`domain.events.*`.

---

## FIX 03 — Version string mismatch
**File**: `backend/bootstrap/app_factory.py`

`app_factory.py` declared `version="4.0.0"` in a v5 release.
Fixed to `version="5.0.0"`.

Also: `README.md` heading still said `v4`. Fixed to `v5`.

---

## FIX 04 — CORS wildcard replaced with env-driven allowlist
**File**: `backend/bootstrap/app_factory.py`

`allow_origins=["*"]` is unsafe in production and contradicted the
`ALLOWED_ORIGINS` variable in `docker-compose.yml`. Now reads from
`ALLOWED_ORIGINS` env var (comma-separated), defaulting to localhost only.
`allow_methods` and `allow_headers` are also scoped to what the app needs.

---

## FIX 05 — Comprehensive Firestore security rules
**File**: `firestore.rules`

Previous rules only covered `transactions/` and `audit_logs/`. All other
collections (`events/`, `customers/`, `conversations/`, `memory/`, `orders/`,
`reminders/`, `promises/`, `alerts/`, `profile/`, `members/`) had no rules,
meaning any authenticated user could read any org's data.

New rules:
- All reads/writes require `isMember(orgId)` — org-scoped isolation
- Events are immutable once written (only `processing_status` may update)
- Orders have field-level update restrictions
- Reminders and promises have field-level update restrictions
- Conversation timeline is append-only
- Audit logs remain immutable

---

## FIX 06 — `REQUIRE_AUTH` production safety guard
**File**: `backend/core/auth.py`

`REQUIRE_AUTH=false` is a legitimate dev convenience, but if it leaked to a
production deploy it would disable all authentication. Added a guard: the bypass
is silently ignored (auth enforced) when `ENVIRONMENT=production`. Only takes
effect when `ENVIRONMENT=development` or `staging` is explicitly set.

---

## FIX 07 — Broken tests: `test_action_dispatcher.py`
**File**: `backend/tests/test_action_dispatcher.py` (rewritten)

The test file imported `_action_update_ledger` and `_action_schedule_reminder`
as free module-level functions. These were refactored into `ToolBase` subclasses
(`UpdateLedgerTool`, `ScheduleReminderTool`) in v5. All tests would fail on
import.

Rewritten to use the v5 API:
- Tests call `UpdateLedgerTool().execute()` and `ScheduleReminderTool().execute()`
- Clarification flow tested (missing entities → `ask_clarification=True`)
- Permission system tested (reduce_debt → pending_approval)
- Threshold permission tested (update_ledger > 500k → pending_approval)

---

## FIX 08 — New tests: permission system, LLM provider, language pipeline
**Files**:
- `backend/tests/test_permissions.py` (new)
- `backend/tests/test_llm_provider.py` (new)
- `backend/tests/test_language_pipeline.py` (new)

Three new test files cover the major v5 additions that had no tests:
- Permission system (AUTO / APPROVAL_REQUIRED / THRESHOLD modes, OrgConfig defaults)
- Multi-provider LLM fallback (first succeeds, fallback on failure, all exhausted)
- Language detection (expanded vocabulary, Luganda, Swahili, edge cases)

---

## FIX 09 — `.env.example` updated
**File**: `backend/.env.example`

Added documentation for `ENVIRONMENT` and `ALLOWED_ORIGINS` variables
introduced by fixes 04 and 06.
