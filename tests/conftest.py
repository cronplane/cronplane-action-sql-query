"""A step of the action, run as Cronplane runs it, against the PostgreSQL of
the kit's fixtures (`SQL_QUERY_POSTGRES` to use another)."""

from __future__ import annotations

import gzip
import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest

from main import main

POSTGRES = os.environ.get(
    "SQL_QUERY_POSTGRES", "postgresql+psycopg://fixture:fixture@127.0.0.1:55432/fixture"
)


@dataclass
class Step:
    root: Path
    monkeypatch: pytest.MonkeyPatch
    url: str = POSTGRES
    allow: str | None = "127.0.0.1:55432"
    inputs: dict[str, Any] = field(default_factory=dict[str, Any])

    def flow_file(self, set_name: str, name: str, content: str) -> None:
        path = self.root / "flow" / set_name / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def run(self) -> int:
        """The exit code of the step: 0 when it succeeds."""
        values = {"params": {}, "timeout": 600, "batch_size": 100, "max_rejected": 0, "lookback": 0}
        values.update(self.inputs)
        (self.root / "with.json").write_text(json.dumps(values))
        secrets = self.root / "secrets"
        secrets.mkdir(exist_ok=True)
        (secrets / "DATABASE_URL").write_text(self.url)
        env = {
            "CRONPLANE_VERSION": "0.1.0",
            "CRONPLANE_WITH": str(self.root / "with.json"),
            "CRONPLANE_OUTPUT": str(self.root / "output.json"),
            "CRONPLANE_MASK": str(self.root / "mask"),
            "CRONPLANE_FILES": str(self.root / "files"),
            "CRONPLANE_FLOW_FILES": str(self.root / "flow"),
            "CRONPLANE_SECRETS": str(secrets),
            "CRONPLANE_STEP_ID": "extract",
            "CRONPLANE_STATE_OUT": str(self.root / "state-out.json"),
        }
        kept = self.root / "state.json"
        if kept.exists():
            env["CRONPLANE_STATE"] = str(kept)
        else:
            self.monkeypatch.delenv("CRONPLANE_STATE", raising=False)
        if self.allow is not None:
            env["CRONPLANE_NETWORK_ALLOW"] = self.allow
        for name, value in env.items():
            self.monkeypatch.setenv(name, value)
        try:
            main()
        except SystemExit as e:
            return int(e.code or 0)
        return 0

    def next_run(self) -> int:
        """Keeps the state this run wrote, as Cronplane does after a run that
        succeeds, and runs the step again with new files."""
        out = self.root / "state-out.json"
        if out.exists():
            out.replace(self.root / "state.json")
        shutil.rmtree(self.root / "files")
        (self.root / "files").mkdir()
        return self.run()

    @property
    def state(self) -> dict[str, Any] | None:
        out = self.root / "state-out.json"
        return json.loads(out.read_text()) if out.exists() else None

    @property
    def output(self) -> dict[str, Any]:
        return json.loads((self.root / "output.json").read_text())

    @property
    def schema(self) -> dict[str, Any]:
        return json.loads((self.root / "files/streams/records/schema.json").read_text())

    def _lines(self, folder: Path) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        for part in sorted(folder.glob("part-*.jsonl.gz")):
            lines += [json.loads(line) for line in gzip.decompress(part.read_bytes()).splitlines()]
        return lines

    @property
    def records(self) -> list[dict[str, Any]]:
        return self._lines(self.root / "files/streams/records")

    @property
    def rejected(self) -> list[dict[str, Any]]:
        return self._lines(self.root / "files/rejected/records/extract")


@pytest.fixture
def step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Step:
    (tmp_path / "files").mkdir()
    return Step(tmp_path, monkeypatch)


@pytest.fixture
def db() -> Iterator[psycopg.Connection[Any]]:
    """A connection to the test database, with the table `customers` of the examples."""
    with psycopg.connect(POSTGRES.replace("+psycopg", ""), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS customers")
        conn.execute(
            "CREATE TABLE customers (id int8 PRIMARY KEY, email varchar(255) NOT NULL, "
            "siret text, updated_at timestamptz NOT NULL, total numeric(12,2), prefs jsonb)"
        )
        yield conn
