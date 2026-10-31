from __future__ import annotations

from datetime import timedelta

from task_agent.db import now


def test_idempotency_key_returns_existing_job(store):
    first, created = store.enqueue("research", "a", {"question": "q one"}, ["fetch_page"], idempotency_key="k1")
    again, created_again = store.enqueue("research", "b", {"question": "q two"}, ["fetch_page"], idempotency_key="k1")
    assert created and not created_again
    assert first == again
    assert len(store.list()) == 1


def test_claim_order_priority_then_time_and_skips_future(store):
    low, _ = store.enqueue("research", "low", {}, [], priority=0)
    high, _ = store.enqueue("research", "high", {}, [], priority=5)
    store.enqueue("research", "later", {}, [], priority=9, run_at=now() + timedelta(hours=1))
    assert store.claim_next().id == high
    assert store.claim_next().id == low
    assert store.claim_next() is None  # the high-priority job isn't due yet


def test_claim_marks_running_and_counts_attempts(store):
    job_id, _ = store.enqueue("research", "t", {}, [])
    job = store.claim_next()
    assert job.id == job_id and job.status == "running" and job.attempts == 1
    assert store.claim_next() is None  # one at a time: nothing else ready


def test_interrupted_jobs_go_back_in_queue(store):
    job_id, _ = store.enqueue("research", "t", {}, [])
    store.claim_next()
    assert store.requeue_interrupted() == [job_id]
    assert store.get(job_id).status == "queued"


def test_retry_later_delays_the_job(store):
    job_id, _ = store.enqueue("research", "t", {}, [])
    store.claim_next()
    store.retry_later(job_id, "boom", timedelta(minutes=5))
    job = store.get(job_id)
    assert job.status == "queued" and job.error == "boom"
    assert store.claim_next() is None  # not due for 5 minutes


def test_steps_numbering(store):
    job_id, _ = store.enqueue("research", "t", {}, [])
    assert store.next_step_number(job_id) == 1
    store.add_step(job_id, 1, "fetch_page", {"url": "x"}, "ok")
    assert store.next_step_number(job_id) == 2
    assert store.steps(job_id)[0]["tool"] == "fetch_page"


def test_upgrades_first_release_job_table(tmp_path):
    import sqlite3

    from task_agent.db import JobStore

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """CREATE TABLE agent_jobs (id INTEGER PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
               payload TEXT NOT NULL DEFAULT '{}', tools_allowed TEXT NOT NULL DEFAULT '[]',
               priority INTEGER NOT NULL DEFAULT 0, run_at TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'waiting_approval', 'done', 'failed')),
               attempts INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT UNIQUE, checkpoint TEXT, result TEXT,
               error TEXT, created_by TEXT NOT NULL DEFAULT 'cli', created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
           CREATE TABLE agent_steps (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES agent_jobs (id),
               n INTEGER NOT NULL, tool TEXT NOT NULL, args TEXT, result_summary TEXT, screenshot_path TEXT,
               tokens INTEGER, duration_ms INTEGER, created_at TEXT NOT NULL);
           INSERT INTO agent_jobs (type, title, run_at, status, created_at, updated_at, idempotency_key)
               VALUES ('research', 'kept', 't', 'done', 't', 't', 'k1');
           INSERT INTO agent_steps (job_id, n, tool, created_at) VALUES (1, 1, 'web_search', 't');"""
    )
    old.close()
    store = JobStore(path)
    job = store.require(1)
    assert job.title == "kept" and job.status == "done" and job.template_key is None and not job.cancel_requested
    assert store.steps(1)[0]["tool"] == "web_search"
    queued, _ = store.enqueue("research", "new", {}, [])
    assert store.cancel(queued).status == "cancelled"
    JobStore(path)  # opening again is a no-op


def test_cancel_and_retry(store):
    import pytest

    from task_agent.db import JobError

    queued, _ = store.enqueue("research", "q", {}, [])
    assert store.cancel(queued).status == "cancelled"
    with pytest.raises(JobError, match="already finished"):
        store.cancel(queued)
    again = store.retry(queued)
    assert again.status == "queued" and again.attempts == 0 and again.error is None

    running = store.claim_next()
    assert store.cancel(running.id).status == "running" and store.cancel_requested(running.id)
    assert store.requeue_interrupted() == []  # asked to cancel before a crash → cancelled, not resumed
    assert store.require(running.id).status == "cancelled"
    with pytest.raises(JobError, match="Only failed or cancelled"):
        store.retry(store.enqueue("research", "x", {}, [])[0])


def test_search_counts_and_settings(store):
    for title in ("Yoga night", "Meetup prices", "Yoga retreat"):
        store.enqueue("research", title, {}, [])
    jobs, total = store.search(["queued"], "yoga", limit=1)
    assert total == 2 and len(jobs) == 1 and jobs[0].title == "Yoga retreat"
    assert store.counts()["queued"] == 3 and store.counts()["cancelled"] == 0
    store.save_setting_overrides({"paused": True, "max_steps": 30})
    assert store.setting_overrides() == {"paused": True, "max_steps": 30}
    store.heartbeat(1, "Yoga night")
    assert store.worker_state()["current_job_title"] == "Yoga night"
