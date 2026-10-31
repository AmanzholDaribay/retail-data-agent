from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class User(BaseModel):
    """The person asking questions, picked with --user at startup."""

    model_config = ConfigDict(frozen=True)

    name: str
    # None means every brand (the CEO).
    brands: frozenset[str] | None


class UnknownUser(Exception):
    """The --user name is not in the users file."""


class _Entry(BaseModel):
    # extra="forbid": a typo like `brand:` fails at startup instead of being ignored.
    model_config = ConfigDict(extra="forbid")

    brands: Literal["all"] | Annotated[list[str], Field(min_length=1)]


class _UsersFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: dict[str, _Entry]


def load_user(path: Path, name: str) -> User:
    """Read the users file and return `name` with their brands. Raises pydantic's ValidationError on a bad file."""
    users = _UsersFile.model_validate(yaml.safe_load(path.read_text())).users
    if name not in users:
        raise UnknownUser(f"Unknown user '{name}'. Known users: {', '.join(users)}")
    brands = users[name].brands
    return User(name=name, brands=None if brands == "all" else frozenset(brands))
