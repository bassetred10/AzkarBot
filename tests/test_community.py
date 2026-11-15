import unittest

from app.community import CommunityStore
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
        await self.store.deactivate_group(-100004)

        config = await self.store.get(-100004)
        self.assertEqual(config["morning_enabled"], 0)
        self.assertEqual(config["evening_enabled"], 0)
        self.assertEqual(config["quiz_enabled"], 0)
        self.assertEqual(config["welcome_enabled"], 0)

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


if __name__ == "__main__":
    unittest.main()
