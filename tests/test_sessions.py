from __future__ import annotations

import time

from mediaforge_p1.sessions import InMemoryBrowserSessionStore


def test_memory_browser_session_store_consumes_pkce_state_once_and_expires_records() -> None:
    store = InMemoryBrowserSessionStore()
    store.create_state("state", "verifier", time.time() + 60)
    assert store.consume_state("state") == "verifier"
    assert store.consume_state("state") is None
    store.create_session("session", {"subject": "user"}, time.time() + 60)
    assert store.get_session("session") == {"subject": "user"}
    store.delete_session("session")
    assert store.get_session("session") is None
    store.create_state("expired", "verifier", time.time() - 1)
    store.prune()
    assert store.consume_state("expired") is None
