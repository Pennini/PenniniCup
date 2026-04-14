from contextvars import ContextVar

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


def set_request_id(request_id: str) -> None:
    _REQUEST_ID.set(request_id)


def clear_request_id() -> None:
    _REQUEST_ID.set(None)
