"""The state feed's wire types (spec §4), shared with the page as `web/src/protocol.gen.ts`.

Pydantic models → JSON Schema (`schema_json()`) → json-schema-to-typescript (`npm run gen:protocol` in web/).
`DashboardFeed` builds plain dicts; these models say what they hold, and the tests validate the feed against them.
An `Envelope` is a union discriminated on `type`, the log record's or `EventType`'s string verbatim. Records
the page only lists (moves, hooks, wake-up payloads) carry their fields untyped.

Run `python -m sonny.dashboard.protocol` to print the schema.
"""

import hashlib
import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, RootModel
from pydantic.json_schema import GenerateJsonSchema

from sonny.attention import AttentionState
from sonny.events import EndReason

# The enums' values, so the wire names cannot drift from Sonny's own.
AttentionStateName = Literal[tuple(s.value for s in AttentionState)]  # type: ignore[valid-type]
EndReasonName = Literal[tuple(r.value for r in EndReason)]  # type: ignore[valid-type]
AgentPhase = Literal["idle", "listening", "thinking", "speaking"]


# ---------------------------------------------------------------- envelope data


class Empty(BaseModel):
    pass


class SessionStartedData(BaseModel):
    task: str
    duration_min: float


class SessionEndedData(BaseModel):
    reason: EndReasonName


class AttentionData(BaseModel):
    state: AttentionStateName
    was: AttentionStateName


class EpisodeOpenedData(BaseModel):
    number: int
    kind: AttentionStateName


class EpisodeClosedData(BaseModel):
    number: int
    kind: AttentionStateName
    seconds: float | None = None
    included_away: bool | None = None
    nudges: list[str] = []
    away_nudges: int | None = None
    reason: str | None = None  # "mac_sleep" when it closed because the Mac slept


class DistractionData(BaseModel):
    """`phone_use.started`, `away.started`, `focused.resumed`."""

    episode: int
    wakes: bool
    excused: bool


class HookEventData(BaseModel):
    """A hook that fired: `timer`, `session.remaining`, `phone_use.continuing`, `away.continuing`."""

    hook_id: int
    note: str
    episode: int | None = None


class ReasonData(BaseModel):
    reason: str


class LinkRestoredData(BaseModel):
    seconds: float


class BlindEndedData(BaseModel):
    reason: str | None = None
    seconds: float
    session_ended: bool = False


class WakeData(BaseModel):
    score: float
    ignored: str | None = None  # "self_wake": Sonny heard itself


class TextData(BaseModel):
    """`owner.spoke`, `bare_wake`."""

    text: str


class LineData(BaseModel):
    """A line Sonny said: `tts_start`/`tts_end` are epoch seconds."""

    text: str
    tts_start: float
    tts_end: float
    interrupted: bool


class LlmCallData(BaseModel):
    model: str
    ok: bool
    latency_s: float
    first_text_s: float | None
    attempts: int
    error: str | None


class ToolCallData(BaseModel):
    name: str
    args: dict[str, Any]
    result: dict[str, Any]


class NudgeData(BaseModel):
    wakeup: str | None
    intensity: str
    episode: int | None


class DetectionsData(BaseModel):
    """The detector over the last second."""

    phone_conf: float | None = None
    phone_inside: float | None = None
    phone_motion: float | None = None
    person_conf: float | None = None
    person_x: float | None = None
    head_moving: bool | None = None
    frames: int
    detected: int
    detect_ms: float


class ReconnectData(BaseModel):
    ok: bool
    attempt: int
    error: str | None


class Health(BaseModel):
    """Link and pipeline health, sent every `health_every_s` and carried as the snapshot's `link`."""

    down: str | None  # `LinkWatch.down`: media_drop, daemon_unreachable, daemon_error; None while the link is up
    camera_fps: float | None
    detect_ms: float | None
    stream_lag_s: float | None  # since the newest camera frame
    llm_latency_s: float | None
    reconnects: int
    blind_spells: int


class AgentPhaseData(BaseModel):
    phase: AgentPhase


# ---------------------------------------------------------------- envelopes


class _Envelope(BaseModel):
    seq: int
    t: float  # epoch seconds


class SessionStarted(_Envelope):
    type: Literal["session.started"]
    data: SessionStartedData


class SessionEnded(_Envelope):
    type: Literal["session.ended"]
    data: SessionEndedData


class AttentionChanged(_Envelope):
    type: Literal["attention"]
    data: AttentionData


class EpisodeOpened(_Envelope):
    type: Literal["episode_opened"]
    data: EpisodeOpenedData


class EpisodeClosed(_Envelope):
    type: Literal["episode_closed"]
    data: EpisodeClosedData


class Distraction(_Envelope):
    type: Literal["phone_use.started", "away.started", "focused.resumed"]
    data: DistractionData


class HookEvent(_Envelope):
    type: Literal["timer", "session.remaining", "phone_use.continuing", "away.continuing"]
    data: HookEventData


class Reasoned(_Envelope):
    type: Literal["excuse.expired", "blind.started"]
    data: ReasonData


class Bare(_Envelope):
    type: Literal["pause.expired", "idle_conversation_started", "barge_in"]
    data: Empty


class LinkRestored(_Envelope):
    type: Literal["link.restored"]
    data: LinkRestoredData


class BlindEnded(_Envelope):
    type: Literal["blind.ended"]
    data: BlindEndedData


class Wake(_Envelope):
    type: Literal["wake"]
    data: WakeData


class Spoken(_Envelope):
    type: Literal["owner.spoke", "bare_wake"]
    data: TextData


class Line(_Envelope):
    type: Literal["line"]
    data: LineData


class LlmCall(_Envelope):
    type: Literal["llm_call"]
    data: LlmCallData


class ToolCall(_Envelope):
    type: Literal["tool_call"]
    data: ToolCallData


class Nudge(_Envelope):
    type: Literal["nudge"]
    data: NudgeData


class DetectionsReport(_Envelope):
    type: Literal["detections"]
    data: DetectionsData


class Reconnect(_Envelope):
    type: Literal["reconnect"]
    data: ReconnectData


class HealthTick(_Envelope):
    type: Literal["health"]
    data: Health


class AgentPhaseChanged(_Envelope):
    type: Literal["agent.phase"]
    data: AgentPhaseData


class Listed(_Envelope):
    """Records the page only lists by type and time (not `Record`: that would shadow TypeScript's)."""

    type: Literal[
        "wakeup",
        "wakeup_dropped",
        "mac_woke",
        "move_start",
        "move_end",
        "move_skipped",
        "hook_set",
        "hook_fired",
        "hook_cancelled",
        "hook_expired",
        "hook_replaced",
    ]
    data: dict[str, Any]


ENVELOPES = (
    SessionStarted,
    SessionEnded,
    AttentionChanged,
    EpisodeOpened,
    EpisodeClosed,
    Distraction,
    HookEvent,
    Reasoned,
    Bare,
    LinkRestored,
    BlindEnded,
    Wake,
    Spoken,
    Line,
    LlmCall,
    ToolCall,
    Nudge,
    DetectionsReport,
    Reconnect,
    HealthTick,
    AgentPhaseChanged,
    Listed,
)


class Envelope(RootModel[Annotated[Union[ENVELOPES], Field(discriminator="type")]]):  # noqa: UP007
    """`sonny.event`'s params, and each of the snapshot's `recent`."""


def envelope_types() -> set[str]:
    return {t for model in ENVELOPES for t in model.model_fields["type"].annotation.__args__}


# ---------------------------------------------------------------- snapshot


class SessionView(BaseModel):
    """Raw times, so the page ticks the time remaining itself: remaining = duration_s - (now - started_at -
    paused_total_s), with now held at paused_since during a Pause."""

    task: str
    started_at: float  # epoch seconds
    duration_s: float
    paused_since: float | None
    paused_total_s: float
    time_up_at: float | None  # time's up: the session waits out its extend grace
    blind: str | None  # why the session is Blind, if it is


class EpisodeView(BaseModel):
    number: int
    kind: AttentionStateName
    started_at: float
    nudges: list[str]  # the intensity of each Nudge in it, in order


class AgentView(BaseModel):
    phase: AgentPhase


class Snapshot(BaseModel):
    """`sonny.snapshot`'s result."""

    seq: int  # the last envelope's seq; replay buffered envelopes with a higher one
    server_now: float
    session: SessionView | None
    attention: AttentionStateName | None
    episode: EpisodeView | None
    agent: AgentView
    link: Health
    recent: list[Envelope]
    recent_max: int  # how many envelopes `recent` keeps; the page keeps as many as it follows


class DashboardProtocol(BaseModel):
    """The root the TypeScript is generated from; the page imports its parts."""

    envelope: Envelope
    snapshot: Snapshot


class _NoFieldTitles(GenerateJsonSchema):
    """Without a title per field, json-schema-to-typescript inlines field types instead of naming each one."""

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False


def schema() -> dict[str, Any]:
    return DashboardProtocol.model_json_schema(schema_generator=_NoFieldTitles)


def schema_json() -> str:
    return json.dumps(schema(), indent=2, sort_keys=True) + "\n"


def schema_hash() -> str:
    return hashlib.sha256(schema_json().encode()).hexdigest()


if __name__ == "__main__":
    print(schema_json(), end="")
