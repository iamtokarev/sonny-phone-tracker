import logging

import pytest
from loguru import logger

from sonny.logs import restore_stdlib


class Logged(list[str]):
    """The messages logged during a test, one string per record: "LEVEL area message"."""

    @property
    def text(self) -> str:
        return "\n".join(self)


@pytest.fixture
def logs():
    lines = Logged()
    from sonny.logs import area

    sink = logger.add(
        lambda m: lines.append(
            f"{m.record['level'].name} {m.record['extra'].get('area') or area(m.record['name'])} {m.record['message']}"
        ),
        level="DEBUG",
        format="{message}",
    )
    yield lines
    logger.remove(sink)


@pytest.fixture(autouse=True)
def _restore_logging():
    """`sonny.logs.setup` (run by `main()`) swaps loguru's sinks and the stdlib root handlers; undo it after a test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    if root.handlers != handlers:
        logger.remove()
        logger.add(lambda m: None, level="WARNING")  # keep loguru quiet, as a test would see it
        root.handlers[:], root.level = handlers, level
        logging.captureWarnings(False)
        restore_stdlib()
