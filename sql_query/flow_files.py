"""The files of the flow a set gives the step (`$CRONPLANE_FLOW_FILES/<set>/`)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .errors import SqlQueryError


def one_file(environ: Mapping[str, str], name: str, suffixes: tuple[str, ...]) -> Path | None:
    """The one file of the set `name`; `None` when the step gives no such set."""
    root = environ.get("CRONPLANE_FLOW_FILES")
    if not root:
        return None
    folder = Path(root) / name
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
    if len(files) != 1 or files[0].suffix not in suffixes:
        found = ", ".join(str(p.relative_to(folder)) for p in files) or "none"
        raise SqlQueryError(
            f"flow_files.{name}: one {' or '.join(suffixes)} file is expected (given: {found})"
        )
    return files[0]
