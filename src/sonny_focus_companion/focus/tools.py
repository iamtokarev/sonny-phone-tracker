"""The agent's session tools (spec §7 "Tools"): schemas for the LLM, and handlers that call the clock.

A refused call returns `{"refused": reason}`, and malformed arguments return `{"error": ...}`, so the
agent always learns why. Every call goes to the log with its arguments and result.

A tool that changes the session runs at most once per wake-up: one request is one change. Haiku sometimes
follows its tool results with the same call again, empty, and `extend({})` would add the default.

The Nudge limits (`NudgeGuard`) hear of every wake-up, move and spoken line here: a `play_emotion` that would
start a Nudge inside the gap is refused before the head owner is asked.
"""

from collections.abc import Callable
from typing import Any, Protocol

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from sonny_focus_companion.config import Config
from sonny_focus_companion.events import EventType
from sonny_focus_companion.focus.clock import AGENT_HOOKS, FocusClock, Refused
from sonny_focus_companion.head import MOVES
from sonny_focus_companion.tracing import current_wakeup


class Head(Protocol):
    """The head owner as the tools see it."""

    def enter_session(self) -> None: ...
    def request(self, name: str) -> dict: ...


def _schemas(cfg: Config) -> list[FunctionSchema]:
    s, h, f = cfg.session, cfg.hooks, cfg.followup
    return [
        FunctionSchema(
            "start_session",
            f"Start a Focus Session: a Task and a Duration. Duration is clamped to {s.duration_min_min}-"
            f"{s.duration_max_min} min; code uses {s.duration_default_min} if omitted. During a session this "
            "switches Tasks: the running session ends and a new one starts.",
            {"task": {"type": "string"}, "duration_minutes": {"type": "integer"}},
            ["task"],
        ),
        FunctionSchema(
            "extend",
            f"Add time to the running session (default {s.extend_default_min} min). Also works for "
            f"{s.extend_grace_min} min after time's up, continuing the same session.",
            {"minutes": {"type": "integer"}},
            [],
        ),
        FunctionSchema(
            "pause",
            f"Pause the session: freezes the clock. After {s.pause_max_min} min paused the session ends.",
            {},
            [],
        ),
        FunctionSchema("resume", "Resume a paused session.", {}, []),
        FunctionSchema("end_session", "End the session now, because the owner asked to stop.", {}, []),
        FunctionSchema(
            "grant_excuse",
            f"Grant an Excuse: no Nudges for a while; the clock keeps running. Default {cfg.excuse.default_min} "
            "min. Always granted.",
            {"reason": {"type": "string"}, "minutes": {"type": "integer"}},
            ["reason"],
        ),
        FunctionSchema(
            "set_hook",
            "Schedule a wake-up for yourself; `note` comes back to you when it fires. `timer` fires after "
            "`after_seconds` of session time; `session.remaining` fires when `remaining_minutes` are left. "
            f"At most {h.max_active} active; each must fire at least {h.min_refire_s:g} s out. "
            "`phone_use.continuing` / `away.continuing` is a follow-up on the current Distraction Episode, of "
            f"its kind: it fires after `after_seconds` (at least {f.min_delay_s:g}) only if the episode is still "
            "going and not Excused. One per episode: a new one replaces the old; code cancels it when the owner "
            f"is focused again; it doesn't count toward the {h.max_active}. All hooks expire with the session.",
            {
                "event": {"type": "string", "enum": [str(e) for e in AGENT_HOOKS]},
                "note": {"type": "string"},
                "after_seconds": {"type": "integer"},
                "remaining_minutes": {"type": "integer"},
            },
            ["event", "note"],
        ),
        FunctionSchema("list_hooks", "List the active hooks.", {}, []),
        FunctionSchema("cancel_hook", "Cancel a hook by its id.", {"id": {"type": "integer"}}, ["id"]),
        FunctionSchema(
            "play_emotion",
            "Play a move on your head and antennas. Moves and your lines never overlap: they play in the "
            "order you call and say them. One move at a time: during another head move this returns `busy` "
            "and nothing is queued; with the motors down it returns `unavailable`. Intensity runs subtle, "
            "medium, strong. The moves:\n"
            + "\n".join(f"- {name} ({m.intensity}): {m.meaning}" for name, m in MOVES.items()),
            {"name": {"type": "string", "enum": list(MOVES)}},
            ["name"],
        ),
    ]


ONCE_PER_WAKEUP = {"start_session", "extend", "pause", "resume", "end_session", "grant_excuse"}


class SessionTools:
    def __init__(self, cfg: Config, clock: FocusClock, write: Callable[..., None], *, head: Head) -> None:
        self.schemas = _schemas(cfg)
        self.tools = ToolsSchema(standard_tools=self.schemas)
        self._write = write  # a log record: write(type, **fields)
        self._clock = clock
        self._handlers: dict[str, Callable[..., dict]] = {
            "start_session": lambda **args: self._start(clock, head, **args),
            "extend": clock.extend,
            "pause": clock.pause,
            "resume": clock.resume,
            "end_session": clock.end,
            "grant_excuse": clock.grant_excuse,
            "set_hook": clock.set_hook,
            "list_hooks": clock.list_hooks,
            "cancel_hook": clock.cancel_hook,
            "play_emotion": lambda name: self._play_emotion(head, name),
        }
        assert set(self._handlers) == {f.name for f in self.schemas}
        self._done: set[str] = set()  # session-changing tools that ran in this wake-up
        self._nudges = clock.nudges

    @staticmethod
    def _start(clock: FocusClock, head: Head, task: str, duration_minutes: float | None = None) -> dict:
        """The head stops daemon tracking before the session starts (spec §3)."""
        head.enter_session()
        return clock.start(task, duration_minutes)

    def _play_emotion(self, head: Head, name: str) -> dict:
        if (reason := self._nudges.move_refusal()) is not None:
            raise Refused(reason)
        result = head.request(name)
        if "playing" in result:
            self._nudges.moved(result["intensity"])
        return result

    @property
    def names(self) -> list[str]:
        return list(self._handlers)

    def begin_wakeup(self, event: EventType | str) -> None:
        """A wake-up for `event` starts (`owner.spoke` for an owner turn)."""
        self._done.clear()
        self._nudges.begin(event)

    def spoke(self) -> None:
        """Sonny said a line in this wake-up."""
        self._nudges.spoke()

    def call(self, name: str, args: dict[str, Any], *, tool_call_id: str | None = None) -> dict:
        trace = current_wakeup()
        if trace is None:
            return self._call(name, args)
        with trace.operation(), trace.tool(name, args, tool_call_id) as captured:
            result = self._call(name, args)
            captured.update(result)
            if name == "start_session" and result.get("started") and self._clock.number is not None:
                trace.adopt_session(self._clock.number)
            return result

    def _call(self, name: str, args: dict[str, Any]) -> dict:
        handler = self._handlers.get(name)
        try:
            if handler is None:
                raise TypeError(f"no tool {name}")
            if name in self._done:
                raise Refused(f"{name} already ran in this wake-up; nothing more to do")
            result = handler(**args)
            if name in ONCE_PER_WAKEUP:
                self._done.add(name)
        except Refused as e:
            result = {"refused": str(e)}
        except (TypeError, ValueError) as e:
            result = {"error": f"bad arguments: {e}"}
        self._write("tool_call", name=name, args=args, result=result)
        return result

    async def handle(self, params: FunctionCallParams) -> None:
        """The Pipecat handler for every session tool."""
        await params.result_callback(
            self.call(params.function_name, dict(params.arguments), tool_call_id=params.tool_call_id)
        )
