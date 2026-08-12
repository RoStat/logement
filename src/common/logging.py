"""Journalisation structurée pour les scripts d'ingestion et de transformation."""

import logging
import sys
import time
from contextlib import contextmanager
from typing import Generator


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


@contextmanager
def timed_operation(logger: logging.Logger, operation: str) -> Generator[None, None, None]:
    start = time.monotonic()
    logger.info("Début : %s", operation)
    yield
    elapsed = time.monotonic() - start
    logger.info("Fin : %s (%.1f s)", operation, elapsed)
