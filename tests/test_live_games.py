import time
import unittest
from types import SimpleNamespace

from app.database import Database
from app.live_games import (LiveGameStore, live_answer_callback, live_round_timeout, restore_live_sessions, register_live_game_handlers)


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



    async def test_anonymous_poll_answers_are_not_assigned_to_a_user(self):
        await self.store.register_poll(
            "anonymous-poll", -100456, "q001", is_anonymous=True
        )

        recorded = await self.store.record_poll_answer(
            "anonymous-poll", self.user.id, 0, True
        )
        rows = await self.store.channel_board()

        self.assertFalse(recorded)
        self.assertEqual(rows, [])

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

    async def test_round_advancement_is_idempotent(self):
        deadline = time.time() - 1
        await self.store.create_session(
            "sessionadvance", -100321, "Advance Group", 101, 3,
            "q001", deadline, "quran", 25
        )

        first = await self.store.advance_session(
            "sessionadvance", "q002", ["q001", "q002"],
            time.time() + 25, expected_round=1, expected_deadline=deadline,
        )
        duplicate = await self.store.advance_session(
            "sessionadvance", "q003", ["q001", "q002", "q003"],
            time.time() + 25, expected_round=1, expected_deadline=deadline,
        )
        session = await self.store.get_session("sessionadvance")

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertEqual(session["current_round"], 2)
        self.assertEqual(session["question_id"], "q002")

    async def test_stale_round_cannot_accept_an_answer(self):
        await self.store.create_session(
            "session001", -100123, "Test Group", 101, 2, "q001", time.time() + 60
        )
        result = await self.store.record_answer("session001", 2, 101, 0, False)
        self.assertEqual(result, "closed")




class _FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return []

    def run_once(self, callback, when, data, name):
        self.jobs.append((callback, when, data, name))


class _FakeEditBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


class LiveRoundHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.initialize()
        self.store = LiveGameStore(self.db)
        await self.store.initialize()
        self.bot = _FakeEditBot()
        self.queue = _FakeJobQueue()
        self.question_one = {
            "id": "q001", "question": "سؤال أول",
            "options": ["أ", "ب"], "answer": 0,
            "explanation": "شرح أول", "source": "مصدر أول",
        }
        self.question_two = {
            "id": "q002", "question": "سؤال ثانٍ",
            "options": ["ج", "د"], "answer": 1,
            "explanation": "شرح ثانٍ", "source": "مصدر ثانٍ",
        }
        class Games:
            def get(inner, question_id):
                return {
                    "q001": self.question_one,
                    "q002": self.question_two,
                }.get(question_id)

            def random_question(inner, category_id="all", exclude_ids=None):
                return self.question_two
        self.games = Games()
        self.application = SimpleNamespace(
            bot_data={"live_store": self.store, "games": self.games},
            job_queue=self.queue,
            bot=self.bot,
        )
        self.context = SimpleNamespace(
            application=self.application,
            job=SimpleNamespace(data="session-timeout"),
        )

    async def asyncTearDown(self):
        await self.db.close()

    async def test_expired_round_advances_and_schedules_next_round(self):
        await self.store.create_session(
            "session-timeout", -100654, "Timer Group", 101, 2,
            "q001", time.time() - 1, "quran", 25
        )
        await self.store.set_message_id("session-timeout", 765)

        await live_round_timeout(self.context)

        session = await self.store.get_session("session-timeout")
        self.assertEqual(session["current_round"], 2)
        self.assertEqual(session["question_id"], "q002")
        self.assertEqual(session["status"], "active")
        self.assertTrue(self.bot.edits)
        self.assertTrue(any(job[0].__name__ == "live_round_timeout" for job in self.queue.jobs))

    async def test_live_answer_is_recorded_once(self):
        user = SimpleNamespace(
            id=202, username="second_player",
            first_name="Second", language_code="ar",
        )
        await self.store.create_session(
            "session-answer", -100656, "Answer Group", 101, 2,
            "q001", time.time() + 30, "quran", 25
        )
        await self.store.set_message_id("session-answer", 767)
        query = SimpleNamespace(
            data="lg:session-answer:1:0",
            from_user=user,
            message=SimpleNamespace(chat_id=-100656),
            answers=[],
        )
        async def answer(text, **kwargs):
            query.answers.append(text)
        query.answer = answer
        update = SimpleNamespace(callback_query=query)
        self.application.bot_data["db"] = self.db

        await live_answer_callback(update, self.context)
        await live_answer_callback(update, self.context)

        stats = await self.store.round_stats("session-answer", 1)
        self.assertEqual(stats, {"answered": 1, "correct": 1})
        self.assertIn("تم تسجيل إجابتك", query.answers[0])
        self.assertIn("من قبل", query.answers[1])

    async def test_restore_live_sessions_reschedules_active_round(self):
        await self.store.create_session(
            "session-restore", -100657, "Restore Group", 101, 2,
            "q001", time.time() + 30, "quran", 25
        )
        await self.store.set_message_id("session-restore", 768)

        await restore_live_sessions(self.application)

        names = [job[3] for job in self.queue.jobs]
        self.assertIn("athar-live-round:session-restore", names)
        self.assertIn("athar-live-tick:session-restore", names)

    async def test_expired_final_round_finishes_and_displays_board(self):
        await self.store.create_session(
            "session-timeout", -100655, "Final Group", 101, 1,
            "q001", time.time() - 1, "quran", 25
        )
        await self.store.set_message_id("session-timeout", 766)

        await live_round_timeout(self.context)

        session = await self.store.get_session("session-timeout")
        self.assertEqual(session["status"], "finished")
        self.assertTrue(self.bot.edits)
        self.assertIn("انتهت بطولة أَثَر", self.bot.edits[-1]["text"])


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
