import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    if not init_data:
        raise ValueError("Missing Telegram initData")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing Telegram initData hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Invalid Telegram initData signature")

    auth_date = int(values.get("auth_date", "0"))
    now = int(time.time())
    if auth_date > now + 30 or now - auth_date > max_age_seconds:
        raise ValueError("Expired Telegram initData")

    user_data = values.get("user")
    if user_data:
        values["user"] = json.loads(user_data)
    return values
