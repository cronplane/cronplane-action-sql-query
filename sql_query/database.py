"""The database of `DATABASE_URL`: only the drivers and URL options the action allows."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from .errors import SqlQueryError


class Kind(StrEnum):
    POSTGRES = "PostgreSQL"
    MYSQL = "MySQL"


DRIVERS: Final = {"postgresql+psycopg": (Kind.POSTGRES, 5432), "mysql+pymysql": (Kind.MYSQL, 3306)}
# Each option is safe to take from a secret: none reads a local file nor runs code.
OPTIONS: Final[dict[Kind, dict[str, frozenset[str] | None]]] = {
    Kind.POSTGRES: {
        "sslmode": frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}),
        "sslrootcert": frozenset({"system"}),
        "application_name": None,
    },
    Kind.MYSQL: {
        # The action's own option: verify-full checks the server's certificate
        # against the system's authorities, and its name.
        "sslmode": frozenset({"disable", "verify-full"}),
        "charset": frozenset({"utf8mb4"}),
    },
}


@dataclass(frozen=True)
class Database:
    #: The URL SQLAlchemy reads: without the action's own options.
    url: URL
    kind: Kind
    host: str
    port: int
    tls: bool = False

    @classmethod
    def parse(cls, text: str) -> Database:
        try:
            url = make_url(text.strip())
        except ArgumentError as e:
            raise SqlQueryError(f"DATABASE_URL: not a database URL: {e}") from None
        driver = DRIVERS.get(url.drivername)
        if driver is None:
            raise SqlQueryError(
                f"DATABASE_URL: the driver {url.drivername} is not one of "
                f"{', '.join(sorted(DRIVERS))}"
            )
        kind, port = driver
        host = url.host or ""
        if not host or host.startswith("/") or "," in host:
            raise SqlQueryError("DATABASE_URL: one host name or address is expected")
        options = OPTIONS[kind]
        for name, value in url.query.items():
            allowed = options.get(name, frozenset())
            values = (value,) if isinstance(value, str) else value
            if name not in options or (allowed is not None and not set(values) <= allowed):
                shown = ", ".join(
                    f"{o}={'|'.join(sorted(v))}" if v else o for o, v in sorted(options.items())
                )
                raise SqlQueryError(
                    f"DATABASE_URL: the option {name} is not allowed for {kind} (allowed: {shown})"
                )
        if kind is Kind.MYSQL:
            tls = url.query.get("sslmode") == "verify-full"
            return cls(url.difference_update_query(["sslmode"]), kind, host, url.port or port, tls)
        return cls(url, kind, host, url.port or port)

    @property
    def allow_line(self) -> str:
        """The host as the step's `network.allow` writes it."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def connect(self, address: str, timeout: int) -> tuple[URL, dict[str, Any]]:
        """The URL and the driver's arguments to reach `address`, the checked
        address of the host, without a second resolution of its name."""
        if self.kind is Kind.POSTGRES:
            return self.url, {"hostaddr": address, "connect_timeout": timeout}
        if self.tls:
            # The name is kept for the certificate: another address fails its check.
            return self.url, {"ssl": ssl.create_default_context(), "connect_timeout": timeout}
        return self.url.set(host=address), {"connect_timeout": timeout}
