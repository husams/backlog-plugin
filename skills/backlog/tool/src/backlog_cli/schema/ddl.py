"""Database DDL for the current schema."""

from importlib.resources import files


SCHEMA_SQL = files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")
