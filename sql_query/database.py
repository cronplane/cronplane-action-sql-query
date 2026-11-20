"""The database of `DATABASE_URL`: only the drivers and URL options the action allows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from .errors import SqlQueryError

DRIVERS: Final = {"postgresql+psycopg": 5432}
# Each option is safe to take from a secret: none reads a local file nor runs code.
OPTIONS: Final[dict[str, frozenset[str] | None]] = {
    "sslmode": frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}),
    "sslrootcert": frozenset({"system"}),
    "application_name": None,
}


@dataclass(frozen=True)
class Database:
    url: URL
    host: str
    port: int

    @classmethod
    def parse(cls, text: str) -> Database:
        try:
            url = make_url(text.strip())
        except ArgumentError as e:
            raise SqlQueryError(f"DATABASE_URL: not a database URL: {e}") from None
        port = DRIVERS.get(url.drivername)
        if port is None:
            raise SqlQueryError(
                f"DATABASE_URL: the driver {url.drivername} is not one of "
                f"{', '.join(sorted(DRIVERS))}"
            )
        host = url.host or ""
        if not host or host.startswith("/") or "," in host:
            raise SqlQueryError("DATABASE_URL: one host name or address is expected")
        for name, value in url.query.items():
            allowed = OPTIONS.get(name, frozenset())
            values = (value,) if isinstance(value, str) else value
            if name not in OPTIONS or (allowed is not None and not set(values) <= allowed):
                raise SqlQueryError(
                    f"DATABASE_URL: the option {name} is not allowed "
                    f"(allowed: {', '.join(sorted(OPTIONS))}; sslrootcert=system only)"
                )
        return cls(url, host, url.port or port)

    @property
    def allow_line(self) -> str:
        """The host as the step's `network.allow` writes it."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"
