"""Authorization policy (SPEC §4, §52)."""

from __future__ import annotations

from agent.config.models import RoomConfig, SecurityConfig
from agent.security.authorization import Authorizer, bare_jid, jid_domain


def _authorizer(**kwargs: object) -> Authorizer:
    defaults = dict(
        allowed_jids=["alice@example.com"],
        allowed_domains=["example.com"],
    )
    defaults.update(kwargs)
    return Authorizer(SecurityConfig(**defaults))  # type: ignore[arg-type]


def test_helpers() -> None:
    assert bare_jid("Alice@Example.com/Resource") == "alice@example.com"
    assert jid_domain("alice@example.com/res") == "example.com"
    assert jid_domain("example.com") == "example.com"


def test_exact_jid_allowed() -> None:
    assert _authorizer().is_authorized("alice@example.com/resource")


def test_domain_allowed() -> None:
    assert _authorizer().is_authorized("bob@example.com")


def test_unauthorized_jid_denied() -> None:
    assert not _authorizer().is_authorized("mallory@evil.com")


def test_wildcard_only_when_enabled() -> None:
    authorizer = _authorizer(allowed_domains=["*"])
    assert not authorizer.is_authorized("mallory@evil.com")

    wildcard = _authorizer(allowed_domains=["*"], allow_wildcard=True)
    assert wildcard.is_authorized("mallory@evil.com")


def test_empty_jid_denied() -> None:
    assert not _authorizer().is_authorized("")


def test_dev_requires_exact_jid_not_domain() -> None:
    authorizer = _authorizer(dev_command_jids=["admin@example.com"])
    assert authorizer.is_dev("admin@example.com")
    assert not authorizer.is_dev("bob@example.com")
    assert not authorizer.is_dev("admin@other.com")


def test_room_allowlist_is_authoritative() -> None:
    authorizer = _authorizer()
    room = RoomConfig(
        jid="dev@conference.example.com",
        allowed_jids=["bob@example.com"],
    )
    assert authorizer.check_room(room, "bob@example.com").allowed
    # Alice passes the global policy but is not in the room allowlist.
    assert not authorizer.check_room(room, "alice@example.com").allowed


def test_room_without_list_is_trusted_scope() -> None:
    # A room that was explicitly joined is itself the trust boundary, so any
    # occupant may talk to the bot (needed for public rooms where the real JID
    # is hidden).
    authorizer = _authorizer()
    room = RoomConfig(jid="room@conference.example.com")
    assert authorizer.check_room(room, "alice@example.com").allowed
    assert authorizer.check_room(room, "mallory@evil.com").allowed
    assert authorizer.check_room(room, "room@conference.example.com/Bob", anonymous=True).allowed


def test_room_restrict_to_allowed_denies_others() -> None:
    authorizer = _authorizer()
    room = RoomConfig(jid="room@conference.example.com", restrict_to_allowed_jids=True)
    assert authorizer.check_room(room, "alice@example.com").allowed
    assert not authorizer.check_room(room, "mallory@evil.com").allowed
    # Anonymous occupants cannot be verified.
    assert not authorizer.check_room(
        room, "room@conference.example.com/Bob", anonymous=True
    ).allowed


def test_room_allowlist_denies_anonymous() -> None:
    authorizer = _authorizer()
    room = RoomConfig(jid="room@conference.example.com", allowed_jids=["bob@example.com"])
    assert authorizer.check_room(room, "bob@example.com").allowed
    assert not authorizer.check_room(
        room, "room@conference.example.com/Bob", anonymous=True
    ).allowed
