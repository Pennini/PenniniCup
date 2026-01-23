LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
        "json": {
            "()": "src.config.settings.jsonlogger.MyJSONFormatter",
            "fmt_keys": {
                "level": "levelname",
                "message": "message",
                "asctime": "asctime",
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
            "filters": [],
        },
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
            "filters": [],
        },
        "file": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": "logs/penninicup.jsonl",
            "maxBytes": 1024 * 1024 * 5,  #
            "backupCount": 5,
            "encoding": "utf8",
            "filters": [],
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
