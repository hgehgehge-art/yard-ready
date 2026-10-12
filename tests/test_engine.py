"""만료·승인 차단·예외·재현성."""
import pandas as pd

from core.engine import Engine
from core.experiment import run_condition


def test_plan_change_expires_and_blocks_approval(params, scenarios, model):
    e = Engine(params, scenarios, "S4", model)
    e.step()                                   # 13:35 ETA 갱신
    rec = e.create_rec(e.world.focus[0])
    assert rec.status in ("추천됨", "보류")
    e.step()                                   # 13:40 작업계획 v3 → v4
    assert rec.status == "만료"
    ok, why = e.decide(rec.rec_id, "승인", None, "기타")
    assert not ok and "v4" in why
    rec2 = e.create_rec(e.world.focus[0])
    assert rec2.plan_version == 4 and rec2.exception      # 새 마감 15:00을 넘는 ETA → 예외 검토


def test_new_eta_expires_recommendation(params, scenarios, model):
    e = Engine(params, scenarios, "S1", model)
    e.step()
    other = "KMTU7654321"
    rec = e.create_rec(other)
    e.step()                                   # 13:50 KMTU7654321 ETA 갱신
    assert rec.status == "만료"


def test_exception_is_not_completed_until_reviewed(params, scenarios, model):
    e = Engine(params, scenarios, "S4", model)
    e.step(); e.step()
    rec = e.create_rec(e.world.focus[0])
    ok, _ = e.decide(rec.rec_id, "다른 대안", "A", "긴급 작업 우선")
    assert ok and rec.status == "예외 검토"
    while not e.done:
        e.step()
    assert rec.status == "예외 검토"
    e.clear_exception(rec.rec_id)
    assert rec.status == "실행 완료"


def test_approved_slot_is_used_at_gate_in(params, scenarios, model):
    e = Engine(params, scenarios, "S1", model)
    e.step()
    c = e.world.focus[0]
    rec = e.create_rec(c)
    opt = next(o for o in rec.options if o.key.startswith("B"))
    ok, _ = e.decide(rec.rec_id, "다른 대안", opt.key, "현장 경험상 다른 위치가 나음")
    assert ok
    while e.world.yard.find(c) is None:
        e.step()
    b, r, t = e.world.yard.find(c)
    assert (b, r) == tuple(opt.stack)
    assert rec.status == "실행 완료"


def test_same_seed_same_world(params, scenarios, model):
    a = Engine(params, scenarios, "S1", model)
    b = Engine(params, scenarios, "S1", model)
    assert a.world.yard.stacks == b.world.yard.stacks
    assert {c: x.gate_in for c, x in a.world.arrivals.items()} == {c: x.gate_in for c, x in b.world.arrivals.items()}


def test_experiment_csv_is_reproducible(params, model, tmp_path):
    d1 = run_condition(params, model, "보통", 1.0, True, False, range(1000, 1002))
    d2 = run_condition(params, model, "보통", 1.0, True, False, range(1000, 1002))
    p1, p2 = tmp_path / "a.csv", tmp_path / "b.csv"
    d1.to_csv(p1, index=False)
    d2.to_csv(p2, index=False)
    assert p1.read_text() == p2.read_text()


def test_snapshot_restores_previous_state(params, scenarios, model):
    e = Engine(params, scenarios, "S1", model)
    e.step()
    snap = e.snapshot()
    e.step()
    assert snap.idx == 1 and e.idx == 2
    assert snap.now < e.now
