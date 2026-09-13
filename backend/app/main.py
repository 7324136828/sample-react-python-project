"""FastAPI entry point for the persistent click-counter example."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from .store import CounterState, CounterStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CounterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count: int
    updated_at: str


def configured_database_path() -> Path:
    configured = Path(os.environ.get("COUNTER_DB_PATH", "data/counter.db")).expanduser()
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


def configured_cors_origins() -> list[str]:
    value = os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def get_store(request: Request) -> CounterStore:
    return request.app.state.counter_store


def create_app(database_path: Path | None = None) -> FastAPI:
    store = CounterStore(database_path or configured_database_path())

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        application.state.counter_store = store
        yield

    application = FastAPI(
        title="Persistent Click Counter API",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured_cors_origins(),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/counter", response_model=CounterResponse)
    def read_counter(
        counter_store: CounterStore = Depends(get_store),
    ) -> CounterState:
        return counter_store.get()

    @application.post("/api/counter/click", response_model=CounterResponse)
    def record_click(
        counter_store: CounterStore = Depends(get_store),
    ) -> CounterState:
        return counter_store.increment()

    @application.delete("/api/counter", response_model=CounterResponse)
    def clear_counter(
        counter_store: CounterStore = Depends(get_store),
    ) -> CounterState:
        return counter_store.reset()

    return application


app = create_app()
