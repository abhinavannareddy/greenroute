from datetime import timedelta

from app.carbon import IntensityPoint, SyntheticProvider, utcnow
from app.quality import QualityChecker
from app.router import Router
from app.scheduler import BatchScheduler, choose_start
from app.schemas import BatchSubmit, ChatRequest, JobState
from app.store import InMemoryJobStore
from tests.conftest import FakeCarbon, FakeClient


def pts(values):
    now = utcnow()
    return now, [IntensityPoint(now + timedelta(hours=h), g) for h, g in enumerate(values)]


def test_choose_start_picks_greenest_before_deadline():
    now, p = pts([40, 35, 20, 30, 10])
    start, g = choose_start(p, now, now + timedelta(hours=3))
    assert g == 20 and start == p[2].at


def test_choose_start_tolerance_prefers_earlier():
    now, p = pts([40, 20.5, 30, 20])
    _, g = choose_start(p, now, now + timedelta(hours=5), tolerance=0.05)
    assert g == 20.5


def test_synthetic_curve_is_diurnal():
    s = SyntheticProvider("SE-SE3")
    day = utcnow().replace(hour=0, minute=0)
    values = {h: s.at(day.replace(hour=h)) for h in range(24)}
    assert min(values, key=values.get) == 5 and max(values, key=values.get) == 17


def make_scheduler(settings, tiers, carbon):
    client = FakeClient({})
    checker = QualityChecker(client, tiers[1].model_id, 1, judge_enabled=False)
    router = Router(client, tiers, checker, carbon, settings)
    return BatchScheduler(InMemoryJobStore(), router, carbon, settings)


async def test_job_deferred_then_runs_early_when_grid_gets_green(settings, tiers):
    carbon = FakeCarbon(now_g=50, forecast=[45, 20, 40])
    sched = make_scheduler(settings, tiers, carbon)
    job = await sched.submit(BatchSubmit(requests=[ChatRequest(prompt="hi")] * 3, deadline_hours=6))
    assert job.state == JobState.scheduled and job.expected_intensity == 20
    assert job.scheduled_for > utcnow()

    assert await sched.tick() == 0          # still dirty: stays queued
    carbon.now_g = 19                       # grid turns green earlier than forecast
    assert await sched.tick() == 1
    await sched.wait_idle()
    done = await sched.store.get(job.id)
    assert done.state == JobState.done and len(done.results) == 3
    assert done.co2_avoided_g > 0 and done.intensity_at_run == 19


async def test_job_runs_now_when_now_is_greenest(settings, tiers):
    sched = make_scheduler(settings, tiers, FakeCarbon(now_g=10, forecast=[30, 40]))
    job = await sched.submit(BatchSubmit(requests=[ChatRequest(prompt="hi")]))
    assert job.expected_intensity == 10
    assert await sched.tick() == 1
