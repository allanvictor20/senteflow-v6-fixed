# SenteFlow v6 — Bug-Fix Release

All changes are targeted at the 9 bugs identified in the v5 code review.
No new features added — correctness, completeness, and test coverage only.

---

## BUG 1 — `_daily_briefing_loop` called `operational_intelligence` with wrong arguments
**File**: `backend/main.py`

`detect_overdue_debts`, `detect_lost_customers`, `detect_inventory_risk`, and
`detect_revenue_trends` all accept `list[dict]` as their first argument — not
`(repo, org_id)`. The briefing loop was calling them with `(repo, org_id)`,
causing silent failures swallowed by the `except` blocks.

**Fix**: Fetch the raw data lists from Firestore first (`repo.list_transactions`,
`repo.list_customers`), then pass those lists to the pure functions.

---

## BUG 2 — `_daily_briefing_loop` read `trend["direction"]` but the key is `"trend"`
**File**: `backend/main.py`

`detect_revenue_trends()` returns `{"trend": "up"|"down"|"stable", ...}`.
The briefing loop read `trend.get("direction", "")` — a key that never exists —
so the revenue line in the daily briefing was always silently skipped.

**Fix**: Changed to `trend.get("trend", "")` and also updated the display to
show the `change_percent` figure for a more useful message.

---

## BUG 3 — `_daily_briefing_loop` sleeps before doing any work
**File**: `backend/main.py`

The `while True:` loop started with `await asyncio.sleep(poll_seconds)`, meaning
the first briefing was always sent 24 hours after deploy. Compare `reminder_loop`,
which runs immediately.

**Fix**: Loop body runs first; `asyncio.sleep` moved to the very end of each
iteration. Owner now receives a briefing shortly after deploy, then once per day.

---

## BUG 4 — `BusinessMemoryEngine` missing `surface_proactive_insights` and `get_payment_patterns`
**File**: `backend/services/memory/memory_engine.py`

`ContextEngine._get_insights()` called `memory.surface_proactive_insights(sender_id)`
and `memory.get_payment_patterns(sender_id)` — but neither method existed on
`BusinessMemoryEngine`. Every message processed raised `AttributeError`, silently
caught by the `except` block, causing context enrichment to always return empty.

**Fix**: Added both methods to `BusinessMemoryEngine`:
- `surface_proactive_insights()` — checks overdue promises and open stock alerts
  for the given sender; returns a list of actionable insight dicts.
- `get_payment_patterns()` — derives payment reliability from promise data;
  returns `{reliability, promise_kept_rate, avg_days_to_pay}`.
Both methods are fully defensive (never raise on Firestore errors).

---

## BUG 5 — `PROVIDER_CHAIN` built at module import time, before `load_dotenv()` runs
**File**: `backend/services/llm/llm_provider.py`

`PROVIDER_CHAIN = _build_provider_chain()` executed at import time. `load_dotenv()`
in `main.py` runs only after all imports — so `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
and `OPENAI_API_KEY` were almost always empty when the chain was built. The entire
multi-provider fallback feature (IDEA 06) was effectively disabled in all real deployments.

**Fix**: `PROVIDER_CHAIN` is now an empty sentinel list at module level. A new
`_get_provider_chain()` function builds and caches the real chain lazily on its
first call (which happens inside `complete_with_fallback()`, well after
`load_dotenv()` has run). Backward-compatible: tests that pass `providers=` directly
are unaffected.

---

## BUG 6 — `reminder_sender.py` used ISO-string comparison instead of datetime comparison
**File**: `backend/tasks/reminder_sender.py`

```python
# Before (fragile — different formats, Z suffix, microseconds break comparison)
if created_at > threshold_iso:
    continue
```

Firestore timestamps may have a trailing `Z` while Python's `isoformat()` emits
`+00:00` or no suffix — making lexicographic comparison wrong for any mismatch.

**Fix**: Parse `created_at` with `datetime.fromisoformat()` (handling `Z` → `+00:00`),
normalise to naive UTC, then compare datetime objects directly. Unparseable timestamps
are now logged and skipped rather than causing silent misfires.

---

## BUG 7 — `LoginPage` `error` prop never passed from `AppInner`
**File**: `frontend/src/App.jsx`

`LoginPage` accepted an `error` prop and rendered it, but `AppInner` passed only
`onLogin` — so Firebase auth errors (popup blocked, wrong domain, etc.) were
silently discarded and the user saw nothing.

**Fix**: Added `loginError` state to `AppInner`. The `onLogin` handler now wraps
`signInWithGoogle()` in a try/catch, maps common Firebase error codes to friendly
messages, and stores them in `loginError`, which is passed as `error` to `LoginPage`.

---

## BUG 8 — `useSummary` used `transactions.length` as its sole change trigger
**File**: `frontend/src/hooks/index.js`

If a transaction's status changed (e.g. `pending` → `approved`) while the count
stayed the same, `useSummary` would not re-fetch. The dashboard would show stale
totals until a page reload.

**Fix**: Replaced `transactions.length` with a `transactionFingerprint` that joins
`id:status` for the first 50 transactions. Any status change is now detected and
triggers a fresh summary fetch.

---

## BUG 9 — `ContextEngine._get_open_orders()` still used Python-side collection scan
**File**: `backend/services/memory/context_engine.py`

The IDEA 13 Firestore query fix was applied only to `state_machine._find_open_order()`.
`ContextEngine._get_open_orders()` still loaded up to 50 orders and filtered in Python,
meaning any customer with more than 50 total orders could have their open orders silently
missed from the LLM context.

**Fix**: Applied the same indexed Firestore query (`customer_id == sender_id`,
`status not-in [completed, cancelled, delivered]`) with the same graceful fallback
to Python filter if the compound index doesn't exist yet.

---

## Pre-existing test bug fixed
**File**: `backend/tests/test_context_engine.py`

`TestRiskScoring` called `_get_customer_context()` — a method name that doesn't
exist (the actual name is `_customer_context()`). The tests would fail on `AttributeError`.
Fixed to call `_customer_context()` correctly, and made the three test methods async
since `_customer_context` is a coroutine.

---

## New test files added
- `backend/tests/test_reminder_sender.py` — covers datetime comparison fix (Bug 6),
  Z-suffix handling, bad timestamp skipping, notified-marking after send.
- `backend/tests/test_daily_briefing.py` — covers correct argument types to
  detect_* functions (Bug 1), correct "trend" key (Bug 2), sleep-after-work (Bug 3).

## Existing test files updated
- `backend/tests/test_memory_engine.py` — added `TestSurfaceProactiveInsights`
  and `TestGetPaymentPatterns` for the two new methods (Bug 4).
- `backend/tests/test_llm_provider.py` — added `test_provider_chain_not_built_at_import`
  and `test_get_provider_chain_returns_a_list` for the lazy-chain fix (Bug 5).
- `backend/tests/test_context_engine.py` — fixed `TestRiskScoring` method name and
  made tests properly async.
