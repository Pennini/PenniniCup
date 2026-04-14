LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "src.common.logging_filters.RequestIdFilter",
        },
    },
    "formatters": {
        "standard": {"format": "%(asctime)s [%(levelname)s] [request_id=%(request_id)s] %(name)s: %(message)s"},
        "json": {
            "()": "src.config.settings.jsonlogger.MyJSONFormatter",
            "fmt_keys": {
                "level": "levelname",
                "message": "message",
                "asctime": "asctime",
                "request_id": "request_id",
                "logger": "name",
                "function": "funcName",
                "line": "lineno",
            },
        },
    },
    "handlers": {
        "stderr": {
            "level": "WARNING",
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stderr",
            "filters": ["request_id"],
        },
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
            "filters": ["request_id"],
        },
        "file": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": "logs/penninicup.jsonl",
            "maxBytes": 1024 * 1024 * 5,  #
            "backupCount": 5,
            "encoding": "utf8",
            "filters": ["request_id"],
        },
    },
    "loggers": {
        logger_name: {"level": "WARNING", "propagate": True}
        for logger_name in [
            "django",
            "django.request",
            "django.db.backends",
            "django.template",
            "src",
        ]
    },
    "root": {
        "level": "DEBUG",
        "handlers": ["console", "file"],
    },
}
