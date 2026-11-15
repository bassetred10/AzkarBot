import time
import unittest
from types import SimpleNamespace

from app.database import Database
from app.live_games import LiveGameStore, register_live_game_handlers


class LiveGameStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.initialize()
        self.store = LiveGameStore(self.db)
        await self.store.initialize()
        self.user = SimpleNamespace(
            id=101,
            username="player_one",
            first_name="Player",
            language_code="ar",
        )
        await self.db.upsert_user(self.user)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_one_active_game_per_group(self):
        created = await self.store.create_session(
            "session001", -100123, "Test Group", 101, 3, "q001", time.time() + 60
        )
        blocked = await self.store.create_session(
            "session002", -100123, "Test Group", 101, 3, "q002", time.time() + 60
        )
        self.assertTrue(created)
        self.assertFalse(blocked)
        active = await self.store.active_for_chat(-100123)
        self.assertEqual(active["session_id"], "session001")

    async def test_answer_is_recorded_once_and_scored(self):
        await self.store.create_session(
            "session001", -100123, "Test Group", 101, 2, "q001", time.time() + 60
        )
        first = await self.store.record_answer("session001", 1, 101, 2, True)
        duplicate = await self.store.record_answer("session001", 1, 101, 2, True)
        stats = await self.store.round_stats("session001", 1)
        board = await self.store.session_board("session001")

        self.assertEqual(first, "ok")
        self.assertEqual(duplicate, "duplicate")
        self.assertEqual(stats, {"answered": 1, "correct": 1})
        self.assertEqual(len(board), 1)
        self.assertGreaterEqual(board[0]["score"], 10)
        self.assertEqual(board[0]["correct"], 1)

    async def test_stale_round_cannot_accept_an_answer(self):
        await self.store.create_session(
            "session001", -100123, "Test Group", 101, 2, "q001", time.time() + 60
        )
        result = await self.store.record_answer("session001", 2, 101, 0, False)
        self.assertEqual(result, "closed")




class LiveGameHandlerRegistrationTests(unittest.TestCase):
    def test_live_handlers_register_without_missing_callbacks(self):
        class FakeApplication:
            def __init__(self):
                self.handlers = []

            def add_handler(self, handler, group=0):
                self.handlers.append((handler, group))

        application = FakeApplication()
        register_live_game_handlers(application)
        self.assertGreaterEqual(len(application.handlers), 10)
        self.assertTrue(any(group == -1 for _, group in application.handlers))


if __name__ == "__main__":
    unittest.main()
