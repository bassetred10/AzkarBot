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



    async def test_anonymous_channel_poll_tracks_aggregate_totals(self):
        await self.db.add_target(-100456, "ATHAR Channel", "channel")
        await self.store.register_poll(
            "poll001", -100456, "q001", is_anonymous=True
        )
        poll = SimpleNamespace(
            id="poll001",
            total_voter_count=9,
            is_closed=True,
            correct_option_ids=(1,),
            options=[
                SimpleNamespace(voter_count=3),
                SimpleNamespace(voter_count=6),
                SimpleNamespace(voter_count=0),
                SimpleNamespace(voter_count=0),
            ],
        )

        updated = await self.store.record_poll_state(poll)
        rows = await self.store.channel_poll_board(10)

        self.assertTrue(updated)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chat_title"], "ATHAR Channel")
        self.assertEqual(rows[0]["total_voters"], 9)
        self.assertEqual(rows[0]["correct_votes"], 6)
        self.assertEqual(rows[0]["is_closed"], 1)



    async def test_category_is_persisted_for_group_session(self):
        await self.store.create_session(
            "sessiontopic", -100789, "Topic Group", 101, 4,
            "q042", time.time() + 60, "seerah"
        )
        session = await self.store.active_for_chat(-100789)
        self.assertEqual(session["category_id"], "seerah")



    async def test_speed_round_duration_is_persisted(self):
        await self.store.create_session(
            "sessionfast", -100789, "Fast Group", 101, 5,
            "q042", time.time() + 12, "seerah", 12
        )
        session = await self.store.active_for_chat(-100789)
        self.assertEqual(session["category_id"], "seerah")
        self.assertEqual(session["round_seconds"], 12)

    async def test_poll_can_store_a_custom_answer_index(self):
        await self.store.register_poll(
            "truefalse001", -100789, "q001",
            is_anonymous=False, answer_index=1,
            question_text="صح أم خطأ: مثال",
        )
        cursor = await self.store.conn.execute(
            "SELECT answer_index, question_text FROM channel_quiz_polls WHERE poll_id=?",
            ("truefalse001",),
        )
        row = await cursor.fetchone()
        self.assertEqual(row["answer_index"], 1)
        self.assertEqual(row["question_text"], "صح أم خطأ: مثال")



    async def test_group_board_includes_named_quiz_poll_scores(self):
        await self.store.register_poll(
            "group-poll-001", -100123, "q001", is_anonymous=False
        )
        await self.store.record_poll_answer(
            "group-poll-001", self.user.id, 2, True
        )

        board = await self.store.group_board(-100123)

        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["score"], 10)
        self.assertEqual(board[0]["correct"], 1)
        self.assertEqual(board[0]["answered"], 1)

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
