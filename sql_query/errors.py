"""The errors of the action: each says what to change, in the flow or in the query."""


class SqlQueryError(Exception):
    """A step that cannot run: its message is the step's error."""


class Rejected(Exception):
    """A value that no record of the stream can hold: its row is rejected."""
