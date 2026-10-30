import contextlib
import dataclasses
import queue
import threading
import time

import numpy as np
import pytest

from sonny.config import load_config
from sonny.media import MediaHub
from tests.fakes import FakeMedia, FakeMini

CFG = load_config()
SECONDS = 60
CHUNK = 320  # 20 ms at 16 kHz
CHUNKS = SECONDS * CFG.media.audio_sample_rate // CHUNK


def consume(q: queue.Queue, out: list, done: threading.Event) -> None:
    while not (done.is_set() and q.empty()):
        with contextlib.suppress(queue.Empty):
            out.append(q.get(timeout=0.01))


def test_two_audio_consumers_each_get_every_chunk_as_mono_ch0():
    hub = MediaHub(FakeMini(FakeMedia(chunks=CHUNKS)), CFG.media, CFG.startup)
    wake_q, stt_q = hub.subscribe_audio(), hub.subscribe_audio()
    done = threading.Event()
    got: tuple[list, list] = ([], [])
    pairs = zip((wake_q, stt_q), got, strict=True)
    workers = [threading.Thread(target=consume, args=(q, out, done)) for q, out in pairs]
    for w in workers:
        w.start()
    hub.start()
    deadline = time.monotonic() + 10
    while hub.audio_chunks < CHUNKS and time.monotonic() < deadline:
        time.sleep(0.01)
    hub.stop()
    done.set()
    for w in workers:
        w.join()
    for chunks in got:
        assert len(chunks) == CHUNKS
        assert sum(len(c) for c in chunks) / CFG.media.audio_sample_rate == SECONDS
        assert all(c.ndim == 1 and c.dtype == np.float32 for c in chunks)
        assert [int(c[0]) for c in chunks] == list(range(CHUNKS))  # ch0, in order
    assert hub.dropped == [0, 0]


def test_full_consumer_queue_drops_and_counts_without_blocking_others():
    media = dataclasses.replace(CFG.media, audio_queue_max=10)
    hub = MediaHub(FakeMini(FakeMedia(chunks=50, pace_s=0.001)), media, CFG.startup)
    stuck, live = hub.subscribe_audio(), hub.subscribe_audio()
    got: list = []
    done = threading.Event()
    reader = threading.Thread(target=consume, args=(live, got, done))
    reader.start()
    hub.start()
    deadline = time.monotonic() + 5
    while hub.audio_chunks < 50 and time.monotonic() < deadline:
        time.sleep(0.01)
    hub.stop()
    done.set()
    reader.join()
    assert stuck.qsize() == 10 and hub.dropped[0] == 40
    assert len(got) == 50


def test_video_reader_keeps_only_the_latest_frame():
    hub = MediaHub(FakeMini(FakeMedia(frames=30)), CFG.media, CFG.startup)
    assert hub.latest_frame() == (None, 0.0)
    hub.start()
    deadline = time.monotonic() + 5
    while hub.frames < 30 and time.monotonic() < deadline:
        time.sleep(0.01)
    hub.stop()
    frame, t = hub.latest_frame()
    assert frame[0, 0, 0] == 29 and t > 0


def test_stop_joins_readers_so_nothing_reads_media_after():
    fake = FakeMedia()
    hub = MediaHub(FakeMini(fake), CFG.media, CFG.startup)
    hub.start()
    time.sleep(0.05)
    hub.stop()
    fake.stopped.set()
    time.sleep(0.05)
    assert fake.calls_after_stop == 0
    assert not hub.running


def test_start_opens_recording_and_playing():
    fake = FakeMedia()
    hub = MediaHub(FakeMini(fake), CFG.media, CFG.startup)
    hub.start()
    hub.stop()
    assert fake.calls[:2] == ["start_recording", "start_playing"]


def test_start_refuses_an_unexpected_mic_rate():
    hub = MediaHub(FakeMini(FakeMedia(sample_rate=48000)), CFG.media, CFG.startup)
    with pytest.raises(RuntimeError, match="48000"):
        hub.start()


def fast_startup(timeout_s: float):
    return dataclasses.replace(CFG.startup, speaker_wait_timeout_s=timeout_s, speaker_poll_s=0.01)


def test_wait_speaker_ready_returns_once_the_send_chain_is_up():
    fake = FakeMedia()
    hub = MediaHub(FakeMini(fake), CFG.media, fast_startup(5))
    threading.Timer(0.1, lambda: setattr(fake.audio, "_appsrc", object())).start()
    t0 = time.monotonic()
    assert hub.wait_speaker_ready(threading.Event()) is True
    assert 0.1 <= time.monotonic() - t0 < 1


def test_wait_speaker_ready_times_out():
    hub = MediaHub(FakeMini(FakeMedia()), CFG.media, fast_startup(0.1))
    assert hub.wait_speaker_ready(threading.Event()) is False


def test_wait_speaker_ready_gives_up_on_stop():
    hub = MediaHub(FakeMini(FakeMedia()), CFG.media, fast_startup(5))
    stop = threading.Event()
    stop.set()
    assert hub.wait_speaker_ready(stop) is False
