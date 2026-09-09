from __future__ import annotations

import threading
import time
from uuid import uuid4

import httpx
import pytest

import validator.heartbeat as heartbeat
from utils.system_metrics import SystemMetrics

SESSION = uuid4()


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(heartbeat.config, "MAX_HEARTBEAT_FAILURES", 3)
    monkeypatch.setattr(heartbeat.config, "SEND_HEARTBEAT_INTERVAL_SECONDS", 10)


class Recorder:
    """Records posts, sleeps and exits; stops the sender after ``ticks`` interval sleeps."""

    def __init__(self, ticks: int = 3, fail_with=None, fail_times: int = 0):
        self.posts: list[tuple] = []
        self.sleeps: list[float] = []
        self.exits: list[int] = []
        self.ticks = ticks
        self.fail_with = fail_with
        self.fail_times = fail_times
        self.sender: heartbeat.HeartbeatSender | None = None

    def post(self, session_id, metrics):
        self.posts.append((session_id, metrics))
        if self.fail_with is not None and (self.fail_times < 0 or len(self.posts) <= self.fail_times):
            raise self.fail_with

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if seconds == 10 and len([s for s in self.sleeps if s == 10]) >= self.ticks:
            self.sender.stop()

    def exit(self, code: int) -> None:
        self.exits.append(code)


def make_sender(rec: Recorder, **kwargs) -> heartbeat.HeartbeatSender:
    sender = heartbeat.HeartbeatSender(SESSION, post=rec.post, sleep=rec.sleep, exit_process=rec.exit, **kwargs)
    rec.sender = sender
    return sender


def test_sends_a_heartbeat_every_interval_with_the_session_token() -> None:
    rec = Recorder(ticks=3)
    make_sender(rec, collect_metrics=SystemMetrics).run_forever()

    assert len(rec.posts) == 3
    assert all(session == SESSION for session, _ in rec.posts)
    assert rec.sleeps == [10, 10, 10]
    assert rec.exits == []


def test_transient_errors_are_retried_with_backoff_then_succeed() -> None:
    rec = Recorder(fail_with=httpx.ConnectTimeout("slow"), fail_times=2)
    make_sender(rec, collect_metrics=SystemMetrics).send_once()

    assert len(rec.posts) == 3  # two failures, one success
    assert rec.sleeps == [2.0, 4.0]
    assert rec.exits == []


def test_exhausted_transient_errors_exit_the_process_once() -> None:
    rec = Recorder(fail_with=httpx.ReadTimeout("dead"), fail_times=-1)
    make_sender(rec, collect_metrics=SystemMetrics).run_forever()

    assert len(rec.posts) == 3  # MAX_HEARTBEAT_FAILURES attempts
    assert rec.exits == [1]


def test_a_rejected_session_exits_immediately_without_retries() -> None:
    request = httpx.Request("POST", "https://api.example/validator/heartbeat")
    response = httpx.Response(401, request=request, json={"detail": "unknown session"})
    rec = Recorder(fail_with=httpx.HTTPStatusError("401", request=request, response=response), fail_times=-1)
    make_sender(rec, collect_metrics=SystemMetrics).run_forever()

    assert len(rec.posts) == 1
    assert rec.exits == [1]


def test_a_failing_metrics_collector_never_stops_heartbeats() -> None:
    def broken_metrics():
        raise RuntimeError("docker ps exploded")

    # One pass of the metrics loop: the collector raises, the snapshot stays empty, the loop survives.
    metrics_rec = Recorder(ticks=1)
    metrics_sender = make_sender(metrics_rec, collect_metrics=broken_metrics)
    metrics_sender.refresh_metrics_forever()
    assert metrics_sender.metrics == SystemMetrics()

    # The sender loop keeps beating with that empty snapshot.
    rec = Recorder(ticks=2)
    make_sender(rec, collect_metrics=broken_metrics).run_forever()

    assert len(rec.posts) == 2
    assert rec.posts[0][1] == SystemMetrics()
    assert rec.exits == []


def test_a_hanging_metrics_collector_only_makes_metrics_stale(monkeypatch) -> None:
    monkeypatch.setattr(heartbeat.config, "SEND_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    release = threading.Event()
    posts: list = []

    def hanging_metrics():
        release.wait(5)  # never returns during the test
        return SystemMetrics(cpu_percent=99.0)

    sender = heartbeat.HeartbeatSender(
        SESSION, post=lambda s, m: posts.append(m), collect_metrics=hanging_metrics, exit_process=lambda c: None
    ).start()
    deadline = time.monotonic() + 2
    while len(posts) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    sender.stop()
    release.set()

    assert len(posts) >= 5
    assert all(m == SystemMetrics() for m in posts)


def test_heartbeats_continue_while_the_calling_thread_is_blocked(monkeypatch) -> None:
    """Simulates a blocked event loop: the thread that started the sender sleeps synchronously."""
    monkeypatch.setattr(heartbeat.config, "SEND_HEARTBEAT_INTERVAL_SECONDS", 0.02)
    posts: list = []
    sender = heartbeat.HeartbeatSender(
        SESSION,
        post=lambda s, m: posts.append(time.monotonic()),
        collect_metrics=SystemMetrics,
        exit_process=lambda c: None,
    ).start()

    time.sleep(0.3)  # the "event loop" is stuck
    sender.stop()

    assert len(posts) >= 5
    assert sender.sender_thread.daemon and sender.metrics_thread.daemon
    assert sender.sender_thread.name == "heartbeat"


def test_start_heartbeat_thread_starts_both_threads(monkeypatch) -> None:
    monkeypatch.setattr(heartbeat.config, "SEND_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(heartbeat, "_default_post", lambda s, m: None)
    monkeypatch.setattr(heartbeat, "_default_exit", lambda c: None)
    sender = heartbeat.HeartbeatSender(SESSION, post=lambda s, m: None, exit_process=lambda c: None).start()
    try:
        assert sender.sender_thread.is_alive() and sender.metrics_thread.is_alive()
    finally:
        sender.stop()
