from pathlib import Path

import pytest
from pydantic import ValidationError

from retail_agent.users import UnknownUser, User, load_user

ROOT = Path(__file__).parents[1]


def write_users(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "users.yaml"
    path.write_text(text)
    return path


def test_a_manager_gets_only_their_brands():
    assert load_user(ROOT / "config" / "users.yaml", "denim_lead") == User(name="denim_lead", brands={"Levi's", "Calvin Klein"})


def test_all_means_every_brand():
    assert load_user(ROOT / "config" / "users.yaml", "ceo").brands is None


def test_an_unknown_user_names_the_known_ones():
    with pytest.raises(UnknownUser, match="Unknown user 'bob'. Known users: ceo, levis_manager, denim_lead"):
        load_user(ROOT / "config" / "users.yaml", "bob")


@pytest.mark.parametrize(
    "entry",
    [
        "brands: []",  # no brands at all
        "brands: Levi's",  # a single name instead of a list
        "brands: everything",  # only `all` means every brand
        "brand: [Levi's]",  # a typo in the key
    ],
)
def test_a_bad_entry_fails_at_startup(tmp_path, entry):
    path = write_users(tmp_path, f"users:\n  alice:\n    {entry}\n")

    with pytest.raises(ValidationError):
        load_user(path, "alice")
