import logging
import sys
import warnings

from loguru import logger

from sonny.logs import area, narrate, setup


def test_areas_come_from_the_logger_name():
    assert area("sonny.voice.pipeline") == "voice"
    assert area("sonny.detector") == "vision"
    assert area("sonny.main") == "sonny"
    assert area("pipecat.turns.user_stop") == "pipecat"
    assert area("reachy_mini.media.webrtc_client_gstreamer") == "reachy"


def test_the_console_tells_the_story_and_hides_library_chatter(capsys, tmp_path):
    setup(tmp_path)
    narrate({"type": "wake", "score": 0.99})
    logger.patch(lambda r: r.update(name="pipecat.turns.user_stop")).debug("VADUserStartedSpeakingFrame received")
    logger.patch(lambda r: r.update(name="pipecat.audio.vad")).warning("no audio received while speaking")
    logging.getLogger("reachy_mini.media").info("Cleared player queue")
    logging.getLogger("reachy_mini.media").error("Camera lost")
    logging.getLogger("reachy_mini.media.audio_control_utils").error("No Reachy Mini Audio USB device found!")
    logging.getLogger("httpx").info("HTTP Request: POST https://openrouter.ai/api/v1/chat/completions")
    logger.patch(lambda r: r.update(name="sonny.vision")).info("RF-DETR Nano on CoreML")
    warnings.warn("`torch.jit.script` is deprecated", FutureWarning, stacklevel=1)
    logger.complete()

    console = capsys.readouterr().err.splitlines()
    assert [line[13:] for line in console] == [  # after the timestamp
        "INFO  voice   wake 0.99",
        "WARN  pipecat no audio received while speaking",
        "ERROR reachy  Camera lost",
        "INFO  vision  RF-DETR Nano on CoreML",
    ]
    file = (tmp_path / "sonny.log").read_text()
    assert "VADUserStartedSpeakingFrame" in file and "Cleared player queue" in file and "torch.jit" in file
    assert "USB device" in file and "HTTP Request" in file


def test_verbose_shows_everything(capsys):
    setup(None, verbose=True)
    logger.patch(lambda r: r.update(name="pipecat.turns.user_stop")).debug("VADUserStartedSpeakingFrame received")
    assert "VADUserStartedSpeakingFrame" in capsys.readouterr().err


def test_each_run_starts_a_fresh_file(tmp_path):
    (tmp_path / "sonny.log").write_text("the last run\n")
    setup(tmp_path)
    logger.info("this run")
    assert "the last run" not in (tmp_path / "sonny.log").read_text()


def test_session_records_are_narrated(logs):
    for record in [
        {"type": "wake", "score": 0.996},
        {"type": "owner.spoke", "text": "Hey Sonny, let's work on email for five minutes."},
        {"type": "llm_call", "model": "anthropic/claude-haiku-5.5", "ok": True, "latency_s": 1.0384, "attempts": 1},
        {"type": "tool_call", "name": "start_session", "args": {"task": "email", "duration_minutes": 5}},
        {"type": "session.started", "task": "email", "duration_min": 5},
        {"type": "line", "text": "Five minutes on email. Go!", "interrupted": False},
        {"type": "phone_use.started", "excused": True},
        {"type": "blind.started", "reason": "media_drop"},
        {"type": "llm_call", "model": "x/y", "ok": False, "attempts": 3, "error": "timeout"},
        {"type": "session.ended", "reason": "ended_by_owner"},
        {"type": "move_start", "name": "proud3"},
    ]:
        narrate({"ts": 0, **record})
    assert logs == [
        "INFO voice wake 1.00",
        "INFO voice owner “Hey Sonny, let's work on email for five minutes.”",
        "INFO llm claude-haiku-5.5 1.04 s",
        "INFO llm tool start_session(task='email', duration_minutes=5)",
        "INFO focus session started · email · 5 min",
        "INFO voice sonny “Five minutes on email. Go!”",
        "INFO vision phone use started (excused)",
        "WARNING link blind: media drop",
        "WARNING llm y failed after 3 attempts: timeout",
        "INFO focus session ended · ended by owner",
        "DEBUG log move_start name=proud3",
    ]


def test_braces_in_speech_are_text(logs):
    narrate({"type": "owner.spoke", "text": "say {this} literally"})
    assert logs == ["INFO voice owner “say {this} literally”"]


def test_a_library_console_handler_joins_the_stream(capsys):
    setup(None)
    rfdetr = logging.getLogger("rf-detr-test")
    rfdetr.addHandler(logging.StreamHandler(sys.stdout))
    rfdetr.propagate = False
    rfdetr.setLevel(logging.INFO)
    rfdetr.warning("loaded the weights")
    out = capsys.readouterr()
    assert "loaded the weights" not in out.out  # not through its own handler
    assert out.err.count("loaded the weights") == 1 and "WARN  rf-detr" in out.err
