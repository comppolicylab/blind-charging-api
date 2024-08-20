import pytest

from app.server.enumerator import RoleEnumerator


def test_role_enumerator_no_init():
    enumerator = RoleEnumerator()
    assert enumerator.next_mask("accused") == "Accused 1"
    assert enumerator.next_mask("accused") == "Accused 2"
    assert enumerator.next_mask("accused") == "Accused 3"
    assert enumerator.next_mask("judge") == "Judge 1"


def test_role_enumerator_init():
    enumerator = RoleEnumerator(["Accused 1", "Accused 2", "Judge 1"])
    assert enumerator.next_mask("accused") == "Accused 3"
    assert enumerator.next_mask("accused") == "Accused 4"
    assert enumerator.next_mask("judge") == "Judge 2"


def test_role_enumerator_invalid_mask():
    with pytest.raises(ValueError):
        RoleEnumerator(["Accused 1", "Accused 2", "Judge 1", "Invalid"])


def test_role_enumerator_normalize():
    enumerator = RoleEnumerator(["Accused 1", " accused 2", "Judge 1"])
    assert enumerator.next_mask(" accused") == "Accused 3"
    assert enumerator.next_mask("Accused ") == "Accused 4"
    assert enumerator.next_mask("judge") == "Judge 2"
    assert enumerator.next_mask("Judge") == "Judge 3"
