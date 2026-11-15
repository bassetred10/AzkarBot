import unittest
from types import SimpleNamespace

from app.community import CommunityStore, _send_community_quiz
from app.database import Database


class CommunityStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.initialize()
        self.store = CommunityStore(self.db)
        await self.store.initialize()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_group_schedules_start_disabled_and_are_independent(self):
        first = await self.store.get(-100001)
        second = await self.store.get(-100002)

        self.assertEqual(first["morning_enabled"], 0)
        self.assertEqual(first["evening_enabled"], 0)
        self.assertEqual(first["quiz_enabled"], 0)
        self.assertEqual(first["hadith_enabled"], 0)
        self.assertEqual(first["hadith_time"], "14:00")
        self.assertEqual(first["timezone"], "Africa/Algiers")
        self.assertEqual(second["morning_enabled"], 0)

        updated = await self.store.toggle(-100001, "morning", 10)
        unchanged = await self.store.get(-100002)

        self.assertEqual(updated["morning_enabled"], 1)
        self.assertEqual(unchanged["morning_enabled"], 0)

    async def test_group_times_are_validated_and_persisted(self):
        await self.store.ensure_group(-100003, "Test group")
        updated = await self.store.set_time(-100003, "quiz", "21:15", 10)

        self.assertEqual(updated["quiz_time"], "21:15")
        with self.assertRaises(ValueError):
            await self.store.set_time(-100003, "quiz", "25:99", 10)

    async def test_removed_group_schedules_are_disabled(self):
        await self.store.ensure_group(-100004, "Removed group")
        await self.store.toggle(-100004, "morning", 10)
        await self.store.toggle(-100004, "evening", 10)
        await self.store.toggle(-100004, "quiz", 10)
        await self.store.toggle(-100004, "hadith", 10)
        await self.store.deactivate_group(-100004)

        config = await self.store.get(-100004)
        self.assertEqual(config["morning_enabled"], 0)
        self.assertEqual(config["evening_enabled"], 0)
        self.assertEqual(config["quiz_enabled"], 0)
        self.assertEqual(config["hadith_enabled"], 0)
        self.assertEqual(config["welcome_enabled"], 0)



    async def test_hadith_schedule_is_independent_and_time_is_validated(self):
        await self.store.get(-100007)
        enabled = await self.store.toggle(-100007, "hadith", 10)
        updated = await self.store.set_time(-100007, "hadith", "15:30", 10)
        untouched = await self.store.get(-100008)

        self.assertEqual(enabled["hadith_enabled"], 1)
        self.assertEqual(updated["hadith_time"], "15:30")
        self.assertEqual(untouched["hadith_enabled"], 0)
        with self.assertRaises(ValueError):
            await self.store.set_time(-100007, "hadith", "30:70", 10)

    async def test_delivery_statistics_are_group_scoped(self):
        await self.store.ensure_group(-100005, "One group")
        await self.store.ensure_group(-100006, "Another group")
        await self.db.log_delivery(-100005, "manual_morning", "sent", telegram_id=123)
        await self.db.log_delivery(-100005, "scheduled_quiz", "failed", "Forbidden")

        first = await self.store.group_stats(-100005)
        second = await self.store.group_stats(-100006)

        self.assertEqual(first["sent"], 1)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["failed"], 0)


class _FakeQuizBot:
    def __init__(self, chat_type):
        self.chat_type = chat_type
        self.poll_kwargs = None

    async def get_chat(self, chat_id):
        return SimpleNamespace(type=self.chat_type)

    async def send_poll(self, **kwargs):
        self.poll_kwargs = kwargs
        return SimpleNamespace(poll=SimpleNamespace(id="poll-test-001"))


class _FakeLiveStore:
    def __init__(self):
        self.registered = []

    async def register_poll(self, poll_id, chat_id, question_id, is_anonymous=False):
        self.registered.append((poll_id, chat_id, question_id, is_anonymous))


class CommunityQuizDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, chat_type):
        bot = _FakeQuizBot(chat_type)
        live_store = _FakeLiveStore()
        games = SimpleNamespace(random_question=lambda: {
            "id": "q-test",
            "question": "ما أول سورة في المصحف؟",
            "options": ["الفاتحة", "البقرة", "الإخلاص"],
            "answer": 0,
            "explanation": "الفاتحة هي أول سور المصحف.",
        })
        application = SimpleNamespace(bot_data={"games": games, "live_store": live_store})
        return SimpleNamespace(bot=bot, application=application), bot, live_store

    async def test_group_quiz_does_not_require_broadcast_target_registration(self):
        context, bot, live_store = self._context("supergroup")

        sent = await _send_community_quiz(-100001, context)

        self.assertTrue(sent)
        self.assertFalse(bot.poll_kwargs["is_anonymous"])
        self.assertEqual(live_store.registered, [
            ("poll-test-001", -100001, "q-test", False)
        ])

    async def test_channel_quiz_uses_anonymous_poll(self):
        context, bot, live_store = self._context("channel")

        sent = await _send_community_quiz(-100002, context)

        self.assertTrue(sent)
        self.assertTrue(bot.poll_kwargs["is_anonymous"])
        self.assertEqual(live_store.registered, [
            ("poll-test-001", -100002, "q-test", True)
        ])


if __name__ == "__main__":
    unittest.main()
