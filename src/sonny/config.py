"""Typed config loaded from one TOML file (spec §12).

The dataclasses have no defaults on purpose: every value comes from the file, and a file
missing a key, carrying an unknown one, or holding the wrong type fails at startup.
"""

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, get_type_hints

DEFAULT_CONFIG_PATH = Path(__file__).with_name("sonny.toml")
CONFIG_ENV = "SONNY_CONFIG"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Robot:
    host: str
    port: int
    http_timeout_s: float
    volume: int


@dataclass(frozen=True)
class Startup:
    speaker_wait_timeout_s: float
    speaker_poll_s: float


@dataclass(frozen=True)
class Media:
    audio_sample_rate: int
    audio_queue_max: int
    reader_idle_sleep_s: float
    reader_join_timeout_s: float


@dataclass(frozen=True)
class Log:
    dir: str


@dataclass(frozen=True)
class Llm:
    model: str
    api_key_env: str
    temperature: float
    sdk_max_retries: int
    retry_timeout_s: float
    wakeup_cap_s: float


@dataclass(frozen=True)
class Stt:
    model: str
    language: str
    ttfs_p99_s: float


@dataclass(frozen=True)
class Tts:
    voice: str
    queue_ahead_s: float


@dataclass(frozen=True)
class Wake:
    model_repo: str
    model_revision: str
    model_file: str
    threshold: float
    window_s: float
    frame_s: float
    refractory_s: float


@dataclass(frozen=True)
class Voice:
    stop_poll_s: float
    input_poll_s: float


@dataclass(frozen=True)
class Vad:
    end_of_turn_s: float


@dataclass(frozen=True)
class Phone:
    min_conf: float
    min_inside_person: float
    parked_motion: float
    parked_window_s: float


@dataclass(frozen=True)
class PhoneUse:
    start_share: float
    start_window_s: float
    end_share: float
    end_window_s: float


@dataclass(frozen=True)
class Window:
    min_frame_share: float


@dataclass(frozen=True)
class Away:
    start_s: float
    end_presence_s: float
    max_nudges_per_episode: int


@dataclass(frozen=True)
class Blind:
    freeze_s: float
    reconnect_every_s: float
    restored_event_after_s: float


@dataclass(frozen=True)
class Frame:
    pitch_deg: float


@dataclass(frozen=True)
class Reframe:
    offcentre: float
    hold_s: float
    gain_per_deg: float


@dataclass(frozen=True)
class Sweep:
    after_no_person_s: float
    speed_dps: float
    yaw_range: str


@dataclass(frozen=True)
class Session:
    duration_default_min: int
    duration_min_min: int
    duration_max_min: int
    extend_default_min: int
    extend_grace_min: int
    pause_max_min: int


@dataclass(frozen=True)
class Excuse:
    default_min: int


@dataclass(frozen=True)
class Nudge:
    min_gap_s: float


@dataclass(frozen=True)
class Followup:
    min_delay_s: float


@dataclass(frozen=True)
class Hooks:
    max_active: int
    min_refire_s: float


@dataclass(frozen=True)
class Config:
    robot: Robot
    startup: Startup
    media: Media
    log: Log
    llm: Llm
    stt: Stt
    tts: Tts
    wake: Wake
    voice: Voice
    vad: Vad
    phone: Phone
    phone_use: PhoneUse
    window: Window
    away: Away
    blind: Blind
    frame: Frame
    reframe: Reframe
    sweep: Sweep
    session: Session
    excuse: Excuse
    nudge: Nudge
    followup: Followup
    hooks: Hooks


def load_config(path: Path | str | None = None) -> Config:
    """Load `path`, else $SONNY_CONFIG, else the packaged defaults."""
    path = Path(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH)
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    return _build(Config, raw, path, "")


def _build(cls: type, table: dict[str, Any], path: Path, prefix: str) -> Any:
    hints = get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    if unknown := sorted(set(table) - names):
        raise ConfigError(f"{path}: unknown key(s) {', '.join(prefix + k for k in unknown)}")
    if missing := sorted(names - set(table)):
        raise ConfigError(f"{path}: missing key(s) {', '.join(prefix + k for k in missing)}")
    values = {}
    for name in names:
        kind, value, key = hints[name], table[name], prefix + name
        if isinstance(kind, type) and hasattr(kind, "__dataclass_fields__"):
            if not isinstance(value, dict):
                raise ConfigError(f"{path}: [{key}] must be a table")
            values[name] = _build(kind, value, path, key + ".")
        else:
            values[name] = _coerce(kind, value, path, key)
    return cls(**values)


def _coerce(kind: type, value: Any, path: Path, key: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, (kind, int) if kind is float else kind):
        raise ConfigError(f"{path}: {key} must be {kind.__name__}, got {value!r}")
    return float(value) if kind is float else value
