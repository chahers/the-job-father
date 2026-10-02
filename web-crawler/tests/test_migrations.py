"""Static checks on the shared database/ migrations (no DB connection needed)."""

import re

import pytest

from jobfather_crawler.config import find_database_dir, load_runtime

NAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


@pytest.fixture(scope="module")
def migrations_dir():
    return load_runtime().migrations_dir


def test_database_dir_is_discovered_at_repo_root(migrations_dir):
    assert migrations_dir.name == "migrations"
    assert migrations_dir.is_dir()
    assert migrations_dir.parent.name == "database"


def test_migration_filenames_are_numbered(migrations_dir):
    files = sorted(migrations_dir.glob("*.sql"))
    assert files, "no migrations found"
    for path in files:
        assert NAME_RE.match(path.name), f"bad migration name: {path.name}"


def test_migration_prefixes_unique_and_contiguous(migrations_dir):
    prefixes = [
        int(NAME_RE.match(p.name).group(1)) for p in sorted(migrations_dir.glob("*.sql"))
    ]
    assert len(set(prefixes)) == len(prefixes), f"duplicate migration prefix: {prefixes}"
    assert prefixes == sorted(prefixes), f"migrations not ordered: {prefixes}"
    assert prefixes == list(range(1, len(prefixes) + 1)), f"gap in numbering: {prefixes}"


def test_migrations_are_not_empty(migrations_dir):
    for path in sorted(migrations_dir.glob("*.sql")):
        assert path.read_text(encoding="utf-8").strip(), f"{path.name} is empty"


def test_extensions_migration_creates_vector(migrations_dir):
    sql = (migrations_dir / "001_extensions.sql").read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql


def test_crawl_migration_defines_owned_tables(migrations_dir):
    sql = (migrations_dir / "002_crawl.sql").read_text(encoding="utf-8")
    for table in ("jobs", "crawl_runs", "crawl_failures"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    # the extension belongs to 001, not here
    assert "CREATE EXTENSION" not in sql


def test_migration_files_property_matches_glob(migrations_dir):
    runtime = load_runtime()
    assert runtime.migration_files == sorted(migrations_dir.glob("*.sql"))
    assert [p.name for p in runtime.migration_files] == [
        "001_extensions.sql",
        "002_crawl.sql",
    ]


def test_explicit_override_wins(tmp_path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "database" / "migrations").mkdir(parents=True)

    assert find_database_dir(tmp_path, None) == tmp_path / "database"

    custom = tmp_path / "custom-db"
    assert find_database_dir(tmp_path, custom) == custom  # absolute path
    assert find_database_dir(tmp_path, "custom-db") == custom  # relative to project root