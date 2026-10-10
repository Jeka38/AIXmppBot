"""Ignoring JIDs/nicks and auto-approving roster subscriptions."""

from __future__ import annotations

from agent.config.models import RoomConfig, SecurityConfig
from agent.security.authorization import Authorizer


def _authorizer(**overrides: object) -> Authorizer:
    defaults: dict[str, object] = dict(
        allowed_jids=["alice@example.com"],
        allowed_domains=["example.com"],
        ignored_jids=["spammer@example.com", "evil.com"],
        ignored_nicks=["Flooder"],
    )
    defaults.update(overrides)
    return Authorizer(SecurityConfig(**defaults))  # type: ignore[arg-type]


# -- ignore matching ---------------------------------------------------------


def test_ignored_jid_is_denied_even_when_allowlisted() -> None:
    auth = _authorizer(allowed_jids=["spammer@example.com"], ignored_jids=["spammer@example.com"])
    assert not auth.is_authorized("spammer@example.com")
    assert auth.is_ignored("spammer@example.com")


def test_ignored_domain_denies_all_its_users() -> None:
    auth = _authorizer()
    assert auth.is_ignored("someone@evil.com")
    assert not auth.is_authorized("someone@evil.com")


def test_bare_entry_is_a_domain_not_a_jid() -> None:
    auth = _authorizer(ignored_jids=["evil.com"])
    assert auth.is_ignored("bob@evil.com")
    # A bare localpart is not treated as a JID.
    assert not auth.is_ignored("bob@example.com")


def test_ignored_nick_detected() -> None:
    auth = _authorizer()
    assert auth.is_ignored("room@conference.example.com/x", nick="Flooder")
    assert not auth.is_ignored("room@conference.example.com/x", nick="Alice")


def test_non_ignored_sender_allowed() -> None:
    auth = _authorizer()
    assert auth.is_authorized("alice@example.com")
    assert not auth.is_ignored("alice@example.com")


def test_ignore_denies_in_room() -> None:
    auth = _authorizer()
    room = RoomConfig(jid="room@conference.example.com")  # trusted scope normally
    assert auth.check_room(room, "someone@evil.com").allowed is False


def test_ignore_only_for_jid_domain_not_substring() -> None:
    auth = _authorizer(ignored_jids=["bad.com"])
    assert not auth.is_ignored("user@notbad.com")
    assert auth.is_ignored("user@bad.com")


# -- subscription decision ---------------------------------------------------


def test_subscription_allowed_for_authorized() -> None:
    auth = _authorizer()
    assert auth.is_authorized("alice@example.com") is True


def test_subscription_denied_for_ignored() -> None:
    auth = _authorizer(allowed_domains=["example.com", "evil.com"])
    # evil.com is both allowed and ignored: ignore wins.
    assert auth.is_authorized("x@evil.com") is False


def test_subscription_denied_for_unknown() -> None:
    auth = _authorizer()
    assert auth.is_authorized("stranger@unknown.example") is False
