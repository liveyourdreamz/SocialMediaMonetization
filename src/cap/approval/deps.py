"""The web layer's way to the store the app was built on (cap.server.create_app)."""

from __future__ import annotations

from fastapi import Request

from cap.store import PostStore


def app_store(request: Request) -> PostStore:
    """A route dependency: the app's store."""
    store: PostStore = request.app.state.store
    return store
