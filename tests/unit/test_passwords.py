"""S1-06: password policy and Argon2id (AC-07-3, AC-07-8, AC-07-17 helpers)."""

import asyncio

import pytest

from smarthome.auth import passwords
from smarthome.auth.passwords import WeakPasswordError, check_policy


def test_argon2id_format_and_parameters() -> None:
    hashed = asyncio.run(passwords.hash_password("correct horse battery"))
    assert hashed.startswith("$argon2id$")
    assert passwords.parameters() == {"time_cost": 3, "memory_cost_kib": 65536, "parallelism": 2}
    assert asyncio.run(passwords.verify_password(hashed, "correct horse battery"))
    assert not asyncio.run(passwords.verify_password(hashed, "correct horse batterY"))


def test_hashes_are_salted() -> None:
    a = asyncio.run(passwords.hash_password("same password twice"))
    b = asyncio.run(passwords.hash_password("same password twice"))
    assert a != b


def test_garbage_hash_never_verifies() -> None:
    assert not asyncio.run(passwords.verify_password("not-a-hash", "whatever12345"))
    assert not asyncio.run(passwords.verify_password("", "whatever12345"))


def test_dummy_hash_is_a_real_hash_of_something_nobody_knows() -> None:
    assert passwords.dummy_hash().startswith("$argon2id$")
    assert not asyncio.run(passwords.verify_password(passwords.dummy_hash(), "anything12345"))


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("", False),
        ("Zq9!" * 2 + "Zq9", False),  # 11 characters
        ("Zq9!" * 3, True),  # exactly 12
        ("a" * 127 + "b", True),
        ("a" * 128, True),  # exactly 128
        ("a" * 129, False),
    ],
)
def test_length_bounds(password: str, ok: bool) -> None:
    if ok:
        assert check_policy(password) == password
    else:
        with pytest.raises(WeakPasswordError):
            check_policy(password)


def test_common_passwords_are_blocked_case_insensitively() -> None:
    assert len(passwords.common_passwords()) >= 1000
    for weak in ("password1234", "PASSWORD1234", "Qwerty123456", "123456789012", "iloveyou1234"):
        with pytest.raises(WeakPasswordError, match="phổ biến"):
            check_policy(weak)


def test_password_equal_to_email_or_its_local_part_is_blocked() -> None:
    with pytest.raises(WeakPasswordError, match="email"):
        check_policy("owner.name@example.com", "Owner.Name@Example.com")
    with pytest.raises(WeakPasswordError, match="email"):
        check_policy("ownername-long-ish", "ownername-long-ish@example.com")


def test_unicode_is_normalised_before_checks_and_hashing() -> None:
    fullwidth = "ＰＡＳＳＷＯＲＤ１２３４"  # NFKC -> "PASSWORD1234", a blocked password
    with pytest.raises(WeakPasswordError):
        check_policy(fullwidth)
    composed, decomposed = "mật khẩu rất dài 1", "mật khẩu rất dài 1"
    hashed = asyncio.run(passwords.hash_password(composed))
    assert asyncio.run(passwords.verify_password(hashed, decomposed))
