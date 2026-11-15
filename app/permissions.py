"""Shared Telegram permission helpers."""


def can_publish_to_channel(member) -> bool:
    """Return whether a ChatMember object has effective channel posting rights."""
    status = getattr(member, "status", None)
    if status == "creator":
        return True
    return status == "administrator" and bool(
        getattr(member, "can_post_messages", False)
    )
