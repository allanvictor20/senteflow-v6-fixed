"""
In-memory stand-ins for Firestore and the Evolution WhatsApp client, good
enough to run the real repositories, router and dispatcher end to end.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Optional


class AlreadyExists(Exception):
    pass


class NotFound(Exception):
    pass


def _apply_value(current: Any, value: Any) -> Any:
    # google.cloud.firestore.ArrayUnion → append missing values
    if type(value).__name__ == "ArrayUnion":
        base = list(current or [])
        for v in getattr(value, "values", []):
            if v not in base:
                base.append(v)
        return base
    return copy.deepcopy(value)


class FakeSnapshot:
    def __init__(self, ref: "FakeDocRef", data: Optional[dict]):
        self.reference = ref
        self.id = ref.id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, db: "FakeFirestore", path: tuple):
        self._db = db
        self.path = path
        self.id = path[-1]

    def collection(self, name: str) -> "FakeQuery":
        return FakeQuery(self._db, self.path + (name,))

    def get(self, transaction=None) -> FakeSnapshot:
        return FakeSnapshot(self, self._db.docs.get(self.path))

    def set(self, data: dict, merge: bool = False) -> None:
        existing = self._db.docs.get(self.path) if merge else None
        new = dict(existing or {})
        for k, v in data.items():
            new[k] = _apply_value(new.get(k), v)
        self._db.docs[self.path] = new

    def create(self, data: dict) -> None:
        if self.path in self._db.docs:
            raise AlreadyExists(str(self.path))
        self.set(data)

    def update(self, data: dict) -> None:
        if self.path not in self._db.docs:
            raise NotFound(str(self.path))
        self.set(data, merge=True)

    def delete(self) -> None:
        self._db.docs.pop(self.path, None)


class FakeQuery:
    def __init__(self, db: "FakeFirestore", path: tuple, filters=None, order=None, limit_n=None):
        self._db = db
        self.path = path
        self._filters = filters or []
        self._order = order
        self._limit = limit_n

    def document(self, doc_id: Optional[str] = None) -> FakeDocRef:
        return FakeDocRef(self._db, self.path + (doc_id or uuid.uuid4().hex,))

    def where(self, field: str, op: str, value: Any) -> "FakeQuery":
        return FakeQuery(self._db, self.path, self._filters + [(field, op, value)], self._order, self._limit)

    def order_by(self, field: str, direction: str = "ASCENDING") -> "FakeQuery":
        return FakeQuery(self._db, self.path, self._filters, (field, direction), self._limit)

    def limit(self, n: int) -> "FakeQuery":
        return FakeQuery(self._db, self.path, self._filters, self._order, n)

    def _match(self, data: dict) -> bool:
        for field, op, value in self._filters:
            actual = data.get(field)
            if op == "==" and actual != value:
                return False
            if op == "in" and actual not in value:
                return False
            if op == "not-in" and (field not in data or actual in value):
                return False
        return True

    def get(self, transaction=None) -> list[FakeSnapshot]:
        depth = len(self.path) + 1
        rows = [
            (path, data) for path, data in self._db.docs.items()
            if len(path) == depth and path[:-1] == self.path and self._match(data)
        ]
        if self._order:
            field, direction = self._order
            rows = [r for r in rows if field in r[1]]
            rows.sort(key=lambda r: str(r[1].get(field)), reverse=direction.upper().startswith("DESC"))
        if self._limit is not None:
            rows = rows[: self._limit]
        return [FakeSnapshot(FakeDocRef(self._db, path), data) for path, data in rows]

    stream = get


class FakeFirestore:
    def __init__(self):
        self.docs: dict[tuple, dict] = {}

    def collection(self, name: str) -> FakeQuery:
        return FakeQuery(self, (name,))

    def transaction(self):
        # Not a real google Transaction → the repository falls back to its
        # non-transactional path, which is what we want in tests.
        return object()

    # Helpers for assertions
    def collection_docs(self, *path: str) -> list[dict]:
        depth = len(path) + 1
        return [
            {**data, "_id": p[-1]} for p, data in self.docs.items()
            if len(p) == depth and p[:-1] == tuple(path)
        ]


class FakeWhatsApp:
    """Records everything SenteFlow sends."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.base_url = "http://evolution.test"
        self.session = "test"
        self.api_key = "k"
        self.timeout = 5

    async def send_text(self, chat_id: str, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"key": {"id": uuid.uuid4().hex}}

    async def get_session_status(self):
        return {"status": "open", "connected": True}

    async def health_check(self):
        return {"status": "ok"}

    async def get_media_bytes(self, message_id, remote_jid=None):
        return None

    async def download_media(self, url):
        return None

    def texts_to(self, chat_id: str) -> list[str]:
        return [t for c, t in self.sent if c == chat_id]
