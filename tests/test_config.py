import tomllib
from pathlib import Path

import pytest

from sonny.config import DEFAULT_CONFIG_PATH, ConfigError, load_config


def write_config(tmp_path: Path, edit) -> Path:
    data = DEFAULT_CONFIG_PATH.read_text()
    path = tmp_path / "sonny.toml"
    path.write_text(edit(data))
    return path


def test_defaults_match_spec():
    cfg = load_config()
    assert cfg.llm.model == "anthropic/claude-haiku-5.5"
    assert (cfg.llm.sdk_max_retries, cfg.llm.retry_timeout_s, cfg.llm.wakeup_cap_s) == (2, 3, 15)
    assert (cfg.wake.threshold, cfg.wake.window_s) == (0.9, 6)
    assert cfg.robot.volume == 62
    assert cfg.phone.min_inside_person == 0.30
    assert (cfg.away.start_s, cfg.away.end_presence_s) == (60, 3)
    assert cfg.reframe.gain_per_deg == 0.0082
    assert (cfg.reframe.offcentre, cfg.reframe.hold_s) == (0.15, 3)
    assert (cfg.sweep.after_no_person_s, cfg.sweep.speed_dps) == (10, 40)
    assert (cfg.session.duration_min_min, cfg.session.duration_max_min) == (5, 120)
    assert (cfg.hooks.max_active, cfg.hooks.min_refire_s) == (5, 60)
    assert cfg.media.audio_sample_rate == 16000


def test_every_file_key_reaches_the_typed_object():
    raw = tomllib.loads(DEFAULT_CONFIG_PATH.read_text())
    cfg = load_config()
    for section, values in raw.items():
        for key, value in values.items():
            assert getattr(getattr(cfg, section), key) == value


def test_int_is_accepted_for_float_fields():
    cfg = load_config()
    assert isinstance(cfg.robot.http_timeout_s, float)


def test_override_file_via_env(tmp_path, monkeypatch):
    path = write_config(tmp_path, lambda s: s.replace("volume = 62", "volume = 40"))
    monkeypatch.setenv("SONNY_CONFIG", str(path))
    assert load_config().robot.volume == 40


def test_unknown_key_is_rejected(tmp_path):
    path = write_config(tmp_path, lambda s: s.replace("volume = 62", "volume = 62\nvolumee = 1"))
    with pytest.raises(ConfigError, match=r"robot\.volumee"):
        load_config(path)


def test_missing_key_is_rejected(tmp_path):
    path = write_config(tmp_path, lambda s: s.replace("volume = 62\n", ""))
    with pytest.raises(ConfigError, match=r"robot\.volume"):
        load_config(path)


def test_missing_section_is_rejected(tmp_path):
    path = write_config(tmp_path, lambda s: s.replace("[hooks]", "[hookz]"))
    with pytest.raises(ConfigError, match="hook"):
        load_config(path)


def test_wrong_type_is_rejected(tmp_path):
    path = write_config(tmp_path, lambda s: s.replace("volume = 62", 'volume = "loud"'))
    with pytest.raises(ConfigError, match=r"robot\.volume"):
        load_config(path)


def test_bool_is_not_a_number(tmp_path):
    path = write_config(tmp_path, lambda s: s.replace("volume = 62", "volume = true"))
    with pytest.raises(ConfigError, match=r"robot\.volume"):
        load_config(path)
