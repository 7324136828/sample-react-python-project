from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.store import CounterStore


class CounterStoreTests(unittest.TestCase):
    def test_count_survives_store_recreation_and_can_be_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "counter.db"
            first_store = CounterStore(database)
            first_store.initialize()
            self.assertEqual(first_store.increment().count, 1)
            self.assertEqual(first_store.increment().count, 2)

            restarted_store = CounterStore(database)
            restarted_store.initialize()
            self.assertEqual(restarted_store.get().count, 2)
            self.assertEqual(restarted_store.reset().count, 0)
            self.assertEqual(first_store.get().count, 0)


class CounterApiTests(unittest.TestCase):
    def test_click_read_restart_and_clear_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "counter.db"
            with TestClient(create_app(database)) as client:
                self.assertEqual(client.get("/api/counter").json()["count"], 0)
                self.assertEqual(
                    client.post("/api/counter/click").json()["count"], 1
                )

            with TestClient(create_app(database)) as restarted_client:
                self.assertEqual(
                    restarted_client.get("/api/counter").json()["count"], 1
                )
                self.assertEqual(
                    restarted_client.delete("/api/counter").json()["count"], 0
                )


if __name__ == "__main__":
    unittest.main()
