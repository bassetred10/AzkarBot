import unittest
from types import SimpleNamespace

from app.permissions import can_publish_to_channel


class ChannelPermissionTests(unittest.TestCase):
    def test_channel_owner_always_has_posting_rights(self):
        owner = SimpleNamespace(status="creator")
        self.assertTrue(can_publish_to_channel(owner))

    def test_channel_admin_needs_post_messages_permission(self):
        allowed = SimpleNamespace(status="administrator", can_post_messages=True)
        denied = SimpleNamespace(status="administrator", can_post_messages=False)
        self.assertTrue(can_publish_to_channel(allowed))
        self.assertFalse(can_publish_to_channel(denied))

    def test_non_admin_cannot_publish_to_channel(self):
        member = SimpleNamespace(status="member", can_post_messages=True)
        self.assertFalse(can_publish_to_channel(member))


if __name__ == "__main__":
    unittest.main()
