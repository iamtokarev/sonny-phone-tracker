"""The terminal and the debug file: one loguru stream for Sonny, Pipecat (which logs through loguru) and every library
that uses stdlib `logging` or `warnings`.

The console tells the story: Sonny's own lines from INFO up (one per thing it heard, said or did), and only warnings
and errors from libraries (Pipecat, reachy_mini, httpx, uvicorn, the models). `--verbose` shows everything. Everything
at DEBUG goes to `<log dir>/sonny.log`, rewritten on each start, so only the last run is ever kept.

This is not the session log (`sonny.session_log`): that JSONL is the record of what happened and feeds the Dashboard.
`narrate` turns its records into console lines, so the two never disagree.
"""

import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# The console floor: Sonny's loggers from INFO, libraries from WARNING, and these (longest prefix wins) from their
# level, because their warnings repeat on every start and change nothing. The debug file gets everything.
SONNY, LIBRARIES = "INFO", "WARNING"
QUIET = {
    "rf-detr": "ERROR",  # weight-loading notes
    "transformers": "ERROR",
    "coremltools": "ERROR",  # untested torch and scikit-learn versions
    "warnings": "ERROR",  # deprecations, via `logging.captureWarnings`
    "py.warnings": "ERROR",
    "reachy_mini.media.audio_control_utils": "CRITICAL",  # "No Reachy Mini Audio USB device": Sonny is wireless
}

# The area column, from the logger's name: sonny.<module> → its area, else the library's top-level name.
AREAS = {"main": "sonny", "detector": "vision", "attention": "vision", "framing": "vision", "session_log": "log",
         "dashboard": "dash", "tracing": "trace", "daemon": "robot"}  # fmt: skip
SHORT_LEVELS = {"WARNING": "WARN", "CRITICAL": "CRIT", "SUCCESS": "OK"}

CONSOLE_FORMAT = (
    "<dim>{time:HH:mm:ss.SSS}</dim> <level>{extra[lvl]: <5}</level> <cyan>{extra[area]: <7}</cyan> "
    "<level>{message}</level>\n{exception}"
)
FILE_FORMAT = "{time:HH:mm:ss.SSS} {level: <8} {name}:{function}:{line} - {message}"  # loguru adds the traceback


def area(name: str | None) -> str:
    parts = (name or "?").split(".")
    if parts[0] == "sonny" and len(parts) > 1:
        return AREAS.get(parts[1], parts[1])
    return {"reachy_mini": "reachy", "py": "warn", "warnings": "warn"}.get(parts[0], parts[0][:7])


def console_format(record: Any) -> str:
    """The console line; fills the area (unless bound) and short level in at the last moment, after every patch."""
    record["extra"].setdefault("area", area(record["name"]))
    record["extra"]["lvl"] = SHORT_LEVELS.get(record["level"].name, record["level"].name)
    return CONSOLE_FORMAT


def console_filter(verbose: bool):
    if verbose:
        return lambda record: True
    sonny, libraries = logger.level(SONNY).no, logger.level(LIBRARIES).no
    quiet = sorted(((p, logger.level(level).no) for p, level in QUIET.items()), key=lambda q: -len(q[0]))

    def keep(record: Any) -> bool:
        name = record["name"] or ""
        floor = next((no for prefix, no in quiet if name == prefix or name.startswith(prefix + ".")), None)
        if floor is None:
            floor = sonny if name == "sonny" or name.startswith("sonny.") else libraries
        return record["level"].no >= floor

    return keep


class InterceptHandler(logging.Handler):
    """Hands stdlib records to loguru (loguru's documented recipe), named after the stdlib logger rather than the
    calling module, so `QUIET` and the area column see `reachy_mini.*` and `py.warnings` as they are."""

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "_sonny_logged", False):  # a library logger with our handler that also propagates to root
            return
        record._sonny_logged = True
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame, depth = frame.f_back, depth + 1
        named = logger.patch(lambda r: r.update(name=record.name))
        named.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


INTERCEPT = InterceptHandler()
_add_handler = logging.Logger.addHandler  # the stdlib's own, restored by `restore_stdlib`


def _prints(handler: logging.Handler) -> bool:
    return isinstance(handler, logging.StreamHandler) and not isinstance(
        handler, logging.FileHandler | InterceptHandler
    )


def _add_handler_via_loguru(self: logging.Logger, handler: logging.Handler) -> None:
    _add_handler(self, INTERCEPT if _prints(handler) else handler)


def _route_library_consoles() -> None:
    """rf-detr, transformers and uvicorn give their loggers a console handler of their own (and stop propagation), so
    their lines would bypass the console format and `QUIET`. Swap such handlers, now and whenever one is added later,
    for `INTERCEPT`."""
    logging.Logger.addHandler = _add_handler_via_loguru  # type: ignore[method-assign]
    for item in list(logging.root.manager.loggerDict.values()):
        if isinstance(item, logging.Logger) and any(_prints(h) for h in item.handlers):
            for h in [h for h in item.handlers if _prints(h)]:
                item.removeHandler(h)
            _add_handler(item, INTERCEPT)


def restore_stdlib() -> None:
    logging.Logger.addHandler = _add_handler  # type: ignore[method-assign]


def setup(log_dir: Path | str | None, verbose: bool = False, console: Any = None) -> None:
    """Route every logger to the console (INFO, or DEBUG when verbose) and, with a `log_dir`, to `sonny.log` there."""
    logger.remove()  # loguru's default prints everything from DEBUG up: Pipecat's flood
    keep = console_filter(verbose)
    logger.add(
        console or sys.stderr, level="DEBUG", format=console_format, filter=keep, backtrace=False, diagnose=False
    )
    logging.basicConfig(handlers=[INTERCEPT], level=logging.DEBUG if verbose else logging.INFO, force=True)
    logging.captureWarnings(True)
    _route_library_consoles()
    if log_dir is not None:
        log_to_file(log_dir)


def log_to_file(log_dir: Path | str) -> Path:
    """Everything at DEBUG to `sonny.log` in `log_dir`, emptied first: only the last run is kept. Call it only once
    this process owns the log folder (`sonny.main.claim_instance`), or it empties a running Sonny's file."""
    path = Path(log_dir) / "sonny.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(path, level="DEBUG", format=FILE_FORMAT, mode="w", backtrace=False, diagnose=False)
    return path


# ---------------------------------------------------------------- the session log, narrated

ATTENTION = {"phone_use.started", "phone_use.continuing", "away.started", "away.continuing", "focused.resumed"}


def _quote(text: Any) -> str:
    return f"“{text}”"


def narrate(record: dict[str, Any]) -> None:
    """One console line for a session-log record: what Sonny heard, said and did. Bookkeeping records go to DEBUG."""
    kind, r, say = record.get("type", "?"), record, logger
    if kind == "wake":
        say.bind(area="voice").info("wake {:.2f}", r.get("score", 0))
    elif kind == "owner.spoke":
        say.bind(area="voice").info("owner {}", _quote(r.get("text", "")))
    elif kind == "line":
        cut = " (interrupted)" if r.get("interrupted") else ""
        say.bind(area="voice").info("sonny {}{}", _quote(r.get("text", "")), cut)
    elif kind == "barge_in":
        say.bind(area="voice").info("barge-in: owner talked over Sonny")
    elif kind == "llm_call":
        model = str(r.get("model", "?")).rsplit("/", 1)[-1]
        retries = f" after {r['attempts']} attempts" if (r.get("attempts") or 1) > 1 else ""
        if r.get("ok"):
            say.bind(area="llm").info("{} {:.2f} s{}", model, r.get("latency_s") or 0, retries)
        else:
            say.bind(area="llm").warning("{} failed{}: {}", model, retries, r.get("error"))
    elif kind == "tool_call":
        args = ", ".join(f"{k}={v!r}" for k, v in (r.get("args") or {}).items())
        say.bind(area="llm").info("tool {}({})", r.get("name"), args)
    elif kind == "session.started":
        say.bind(area="focus").info("session started · {} · {} min", r.get("task"), r.get("duration_min"))
    elif kind == "session.ended":
        say.bind(area="focus").info("session ended · {}", str(r.get("reason", "?")).replace("_", " "))
    elif kind in ATTENTION:
        excused = " (excused)" if r.get("excused") else ""
        say.bind(area="vision").info("{}{}", kind.replace(".", " ").replace("_", " "), excused)
    elif kind == "nudge":
        say.bind(area="focus").info("nudge · {}", r.get("intensity"))
    elif kind in {"session.remaining", "timer", "excuse.expired", "pause.expired"}:
        say.bind(area="focus").info("{}", kind.replace(".", " ").replace("_", " "))
    elif kind == "blind.started":
        say.bind(area="link").warning("blind: {}", str(r.get("reason", "?")).replace("_", " "))
    elif kind == "blind.ended":
        say.bind(area="link").info("blind ended after {:.0f} s", r.get("seconds") or 0)
    elif kind == "mac_woke":
        say.bind(area="sonny").info("Mac woke after {:.0f} s asleep; checking Sonny's link", r.get("slept_s") or 0)
    elif kind == "wakeup_dropped":
        say.bind(area="focus").warning("wake-up dropped: {} ({})", r.get("event"), r.get("reason"))
    else:
        fields = " ".join(f"{k}={v}" for k, v in r.items() if k not in {"ts", "type"})
        say.bind(area="log").debug("{} {}", kind, fields)
