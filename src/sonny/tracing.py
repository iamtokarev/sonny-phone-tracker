"""One local run tree per wake-up; completed trees are exported off the voice thread.

Session identity is decided before export: a successful start_session moves even the
model call that requested it into the new session's LangSmith thread.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any
from uuid import uuid4

import langsmith as ls
from langsmith.wrappers import wrap_openai

logger = logging.getLogger(__name__)
TRACE_KEY = "sonny.wakeup_trace"
_wakeup: ContextVar[WakeupTrace | None] = ContextVar("sonny_wakeup", default=None)


def current_wakeup() -> WakeupTrace | None:
    return _wakeup.get()


@contextmanager
def wakeup_context(trace: WakeupTrace | None) -> Iterator[None]:
    token = _wakeup.set(trace)
    try:
        # Explicit False prevents a wrapped client from tracing outside a wake-up,
        # even when LANGSMITH_TRACING is enabled in the surrounding environment.
        with ls.tracing_context(parent=trace.root if trace else False, enabled="local" if trace else False):
            yield
    finally:
        _wakeup.reset(token)


class WakeupTrace:
    def __init__(self, owner: AgentTracing, message: dict, session: int | None) -> None:
        self.owner, self.message, self.session = owner, message, session
        self.thread_id = owner.session_thread(session) if session is not None else owner.idle_thread
        payload = json.loads(message["content"])
        self.root = ls.RunTree(
            name="sonny.wakeup",
            run_type="chain",
            inputs={
                "messages": [{"role": "user", "content": payload.get("text", message["content"])}],
                "wakeup": payload,
            },
            extra={"metadata": {"thread_id": self.thread_id, "session_number": session}},
            tags=["sonny", *owner.tags],
            ls_client=owner.client,
            address=owner.address,
        )
        self.calls: list[dict] = []
        self.lines: list[dict] = []
        self.generated: list[str] = []
        self._operations = 0
        self._speech_pending = False
        self._reason: str | None = None
        self._error: str | None = None
        self.finished = False

    @contextmanager
    def operation(self) -> Iterator[None]:
        self.retain()
        try:
            with wakeup_context(None if self.finished else self):
                yield
        finally:
            self.release()

    def retain(self) -> None:
        """Reserve queued work before a persistent Pipecat worker receives it."""
        self._operations += 1

    def release(self) -> None:
        self._operations -= 1
        self._export_if_ready()

    def adopt_session(self, session: int) -> None:
        if self.finished or self.session == session:
            return
        if self.session is not None:
            self.root.add_metadata({"previous_session_number": self.session, "previous_thread_id": self.thread_id})
        self.session = session
        self.thread_id = self.owner.session_thread(session)

    def text(self, text: str) -> None:
        if not self.finished and text:
            self.generated.append(text)
            if text.strip():
                self._speech_pending = True

    def call(self, fields: dict) -> None:
        if self.finished:
            return
        self.calls.append(deepcopy(fields))
        if not fields["ok"]:
            self._error = fields["error"]

    def line(self, fields: dict) -> None:
        if self.finished:
            return
        self.lines.append(deepcopy(fields))
        self._speech_pending = False
        self._export_if_ready()

    def cancel_speech(self) -> None:
        self._speech_pending = False
        self._export_if_ready()

    @contextmanager
    def tool(self, name: str, args: dict, tool_call_id: str | None) -> Iterator[dict]:
        result: dict = {}
        run = None
        try:
            if not self.finished:
                run = self.root.create_child(
                    name=name,
                    run_type="tool",
                    inputs=deepcopy(args),
                    extra={"metadata": {"tool_call_id": tool_call_id}},
                )
        except Exception:
            logger.warning("Could not create tool trace", exc_info=False)
        try:
            yield result
        except BaseException as e:
            if run is not None:
                run.end(error=type(e).__name__)
            raise
        else:
            if run is not None:
                outcome = "refused" if "refused" in result else "error" if "error" in result else "ok"
                run.add_metadata({"outcome": outcome})
                run.end(outputs=deepcopy(result))

    def finish(self, reason: str = "completed") -> None:
        if self.finished:
            return
        self._reason = reason
        if reason in {"stuck", "shutdown"}:
            self._speech_pending = False
        self._export_if_ready()

    def _export_if_ready(self, *, force: bool = False) -> None:
        if self.finished or self._reason is None or (not force and (self._operations or self._speech_pending)):
            return
        self.finished = True
        if self._error:
            outcome = self._error if self._error in {"cap", "interrupted"} else "failed"
        else:
            outcome = self._reason if self._reason != "completed" else "spoken" if self.lines else "silent"
        try:
            messages = [{"role": "assistant", "content": line["text"]} for line in self.lines if line.get("text")]
            # Mark only the root after children have been constructed, so tool
            # runs do not inherit agent-like rendering from create_child().
            self.root.add_metadata({"ls_agent_type": "root"})
            self.root.end(
                outputs={
                    "messages": messages,
                    "output": "\n".join(message["content"] for message in messages),
                    "outcome": outcome,
                    "generated_text": "".join(self.generated),
                    "spoken_lines": self.lines,
                    "llm_calls": self.calls,
                },
                error=self._error if self._error != "interrupted" else None,
            )
            pending = [self.root]
            while pending:
                run = pending.pop()
                if force and run.end_time is None:
                    run.end(error=self._reason)
                run.add_metadata({"thread_id": self.thread_id, "session_number": self.session})
                if run.run_type == "llm":
                    run.add_metadata({"ls_provider": "openrouter"})
                pending.extend(run.child_runs)
            self.owner.submit(self.root)
        except Exception:
            logger.warning("Could not finalize wake-up trace", exc_info=False)
        finally:
            self.owner._active.discard(self)


class AgentTracing:
    """Disabled by default; from_env is called only when building the real voice loop."""

    def __init__(self, client: Any = None, *, address: Any = None, tags: tuple[str, ...] = ()) -> None:
        self.client, self.address, self.tags = client, address, tags
        self.idle_thread = str(uuid4())
        self._sessions: dict[int, str] = {}
        self._messages: weakref.WeakValueDictionary[int, WakeupTrace] = weakref.WeakValueDictionary()
        self._active: set[WakeupTrace] = set()
        self._queue: queue.Queue = queue.Queue()
        self._closed = False
        self._worker: threading.Thread | None = None
        if client is not None:
            self._worker = threading.Thread(target=self._export, name="sonny-traces", daemon=True)
            self._worker.start()

    @classmethod
    def from_env(cls, *, tags: tuple[str, ...] = ()) -> AgentTracing:
        if os.getenv("LANGSMITH_TRACING", "").lower() != "true":
            return cls()
        try:
            address = ls.agent_address(os.environ["LANGSMITH_AGENT_ID"], os.environ["LANGSMITH_AGENT_ENVIRONMENT"])
            return cls(ls.Client(timeout_ms=2000), address=address, tags=tags)
        except Exception as e:
            logger.warning("LangSmith configuration failed (%s); continuing without tracing", type(e).__name__)
            return cls()

    def session_thread(self, session: int) -> str:
        return self._sessions.setdefault(session, str(uuid4()))

    def new_idle(self) -> None:
        self.idle_thread = str(uuid4())

    def begin(self, message: dict, session: int | None) -> WakeupTrace | None:
        if self.client is None or self._closed:
            return None
        try:
            trace = WakeupTrace(self, message, session)
            self._messages[id(message)] = trace
            self._active.add(trace)
            return trace
        except Exception:
            logger.warning("Could not begin wake-up trace", exc_info=False)
            return None

    def for_messages(self, messages: list) -> WakeupTrace | None:
        # Pipecat retains message objects in LLMContext. Resolve at queue time,
        # then carry the handle on the frame rather than rereading current state.
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                return self._messages.get(id(message))
        return None

    def wrap_client(self, client: Any) -> Any:
        return wrap_openai(client) if self.client is not None else client

    def submit(self, root: ls.RunTree) -> None:
        if not self._closed:
            self._queue.put(root)

    def _export(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    self.client.flush(timeout=5)
                    return
                if isinstance(item, threading.Event):
                    self.client.flush(timeout=5)
                    item.set()
                else:
                    item.post(exclude_child_runs=False)
            except Exception as e:
                logger.warning("LangSmith export failed (%s); agent execution is unaffected", type(e).__name__)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 5) -> bool:
        if self._worker is None or self._closed:
            return True
        done = threading.Event()
        self._queue.put(done)
        return done.wait(timeout)

    def close(self) -> None:
        if self._closed:
            return
        for trace in list(self._active):
            # The voice loop has cancelled its tasks by this point. A missing
            # callback must not keep a tree alive forever during shutdown.
            trace._reason = "shutdown"
            trace._export_if_ready(force=True)
        self._closed = True
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout=5)
