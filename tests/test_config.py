import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_required_variable_fails(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "DATABASE_URL", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_allowed_ids_parsing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1, 2")
    s = Settings(_env_file=None, anthropic_api_key="k", database_url="d", telegram_bot_token="t")
    assert s.telegram_allowed_user_ids == [1, 2]
