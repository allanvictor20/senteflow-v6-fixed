# Migration: v3 → v4

## Why the architecture changed

v3 was built around a SACCO (savings cooperative) mental model. The core object was `Transaction`. Every message became a Transaction. The system then tried to classify which *type* of transaction it was.

That worked for loan repayments and contributions. It failed for:
- "Is that cement still available?" (inquiry — no transaction)
- "I'll pay you on Friday" (promise — not yet a transaction)
- "We're running low on bags" (stock alert — not a transaction at all)
- "The delivery is on its way" (logistics — never a transaction)

v4 replaces the Transaction mental model with BusinessEvent.

## What changed

| v3 | v4 |
|---|---|
| `Transaction` | `BusinessEvent` |
| `TransactionRepository` | `EventRepository` |
| `TransactionService` | `EventService` + workflows |
| `transaction_parser.py` | `services/llm/event_extractor.py` |
| `transaction_validator.py` | removed (validation in extractor) |
| `extraction_workflow.py` | `event_extraction_workflow.py` |
| `OperationalIntelligence.member_risk_scores()` | `detect_lost_customers()`, `detect_overdue_debts()` |
| `MembersView.jsx` | `CustomersView.jsx` |
| `TransactionsView.jsx` | `ActivityFeedView.jsx` |

## Files deleted in v4

```
# Deprecated files (no longer present):
repositories/_deprecated_transaction_repository.py
services/business/_deprecated_transaction_service.py
validators/_deprecated_transaction_validator.py
workflows/_deprecated_extraction_workflow.py
domain/activities.py (shim — fully removed)
domain/models.py (SACCO models stripped; live classes moved to domain/*)
```

## Firestore note

Old records in `organizations/{org_id}/transactions/` still exist and are readable. The dashboard routes that read transactions still work. New events are written to `organizations/{org_id}/events/`.

When you are ready to migrate old records, run:
```python
# (future migration script)
# scripts/migrate_transactions_to_events.py
```
