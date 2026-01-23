import datetime as dt
import json
import logging
from typing import override

LOG_RECORD_BUILTIN_ATTRS = {
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "process",
    "processName",
    "message",
}


class MyJSONFormatter(logging.Formatter):
    def __init__(self, fmt_keys=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fmt_keys = fmt_keys or {}

    @override
    def format(self, record: logging.LogRecord) -> str:
        message = self._prepare_log_dict(record)
        return json.dumps(message, default=str)

    def _prepare_log_dict(self, record: logging.LogRecord) -> dict:
        always_fields = {
            "message": record.getMessage(),
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
        }

        if record.exc_info:
            always_fields["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message = {
            key: msg_val if (msg_val := always_fields.pop(val, None)) is not None else getattr(record, val, None)
            for key, val in self.fmt_keys.items()
        }
        message.update(always_fields)

        for key, value in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS and key not in self.fmt_keys.values():
                message[key] = value

        return message
