"""Basic logging setup.

Policy: log metadata only (method, path, status, duration, lengths). Never log raw
request bodies, user messages or secrets.
"""

import logging
from logging.config import dictConfig


def configure_logging(level: str = "INFO") -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "loggers": {
                "smarthome": {"handlers": ["console"], "level": level.upper(), "propagate": False},
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"smarthome.{name}")
