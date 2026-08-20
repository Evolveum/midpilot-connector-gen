# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Credentials in settings must never render in a repr, log line or error dump."""

import pytest
from pydantic import SecretStr

from src.config.database import DatabaseSettings
from src.config.langfuse import LangfuseSettings
from src.config.llm import LLMSettings
from src.config.search import BraveSettings

SECRET = "super-secret-value"


@pytest.mark.parametrize(
    ("settings", "accessor"),
    [
        (LLMSettings(openai_api_key=SecretStr(SECRET)), lambda s: s.openai_api_key),
        (LangfuseSettings(secret_key=SecretStr(SECRET)), lambda s: s.secret_key),
        (BraveSettings(api_key=SecretStr(SECRET)), lambda s: s.api_key),
        (DatabaseSettings(password=SecretStr(SECRET)), lambda s: s.password),
    ],
    ids=["llm-api-key", "langfuse-secret-key", "brave-api-key", "database-password"],
)
def test_secret_is_redacted_in_repr_and_dump(settings, accessor) -> None:
    assert accessor(settings).get_secret_value() == SECRET

    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)
    assert SECRET not in str(settings.model_dump())
    assert SECRET not in settings.model_dump_json()


def test_assembled_database_url_hides_the_password() -> None:
    settings = DatabaseSettings(host="db", int_port=5432, name="connector", user="app", password=SecretStr(SECRET))

    assert settings.database_url() == f"postgresql+asyncpg://app:{SECRET}@db:5432/connector"
    assert SECRET not in repr(settings)
    assert SECRET not in settings.model_dump_json()


def test_explicit_database_url_with_placeholders_is_reassembled() -> None:
    settings = DatabaseSettings(
        url=SecretStr("postgresql+asyncpg://${DATABASE__USER}:${DATABASE__PASSWORD}@x:1/y"),
        host="db",
        int_port=5432,
        name="connector",
        user="app",
        password=SecretStr(SECRET),
    )

    assert settings.database_url() == f"postgresql+asyncpg://app:{SECRET}@db:5432/connector"


def test_unset_secret_is_falsy() -> None:
    """An unset secret must stay falsy so "is it configured?" guards keep working.

    ``SecretStr`` defines ``__len__``, so ``bool(SecretStr(""))`` is False and the
    existing guards (e.g. the Brave "not configured" check) behave unchanged.
    """
    assert not BraveSettings().api_key
    assert not DatabaseSettings().password
    assert BraveSettings(api_key=SecretStr(SECRET)).api_key


def test_secret_str_does_not_leak_through_str_or_format() -> None:
    """Interpolating a secret yields the mask, so an accidental log line is safe."""
    key = LLMSettings(openai_api_key=SecretStr(SECRET)).openai_api_key

    assert str(key) == "**********"
    assert f"{key}" == "**********"
    assert SECRET not in "%s" % (key,)
    assert key.get_secret_value() == SECRET
