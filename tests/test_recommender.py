"""시간 누수와 보류 규칙."""
import copy

import pytest

from core.engine import Engine
from core.models import Arrival, EtaIssue, Yard
from core.recommender import Context, TimeLeakError, guard_inputs, recommend


def _ctx(eta, yard=None, pending=None, now=800.0):
    y = yard or Yard.empty("A1", 1, 4, 3, {"V1": [0]})
    if yard is None:
        y.stacks[(0, 0)] = ["a1", "a3"]
        y.stacks[(0, 1)] = ["a2"]
    groups = {"a1": 1, "a2": 2, "a3": 3, "X": 1}
    return Context(yard=y, vessel_id="V1", groups=groups, seqs={c: 0 for c in groups}, target="X", target_eta=eta,
                   pending=pending or {}, now=now, cutoff=2000, retrieval_start=1000, windows=[(820.0, 3)], plan_version=3)


def _eta(p10, p50, p90, issued=790.0, cntr="X", inputs=None):
    return EtaIssue(cntr, issued, inputs or {"예약시각": 840}, p10, p50, p90, "test")


def test_arrival_object_is_rejected():
    with pytest.raises(TimeLeakError):
        guard_inputs(800, [Arrival("X", 850, 860, 870)])


def test_future_eta_is_rejected(params):
    ctx = _ctx(_eta(840, 850, 860, issued=805.0))
    with pytest.raises(TimeLeakError):
        recommend(ctx, params)


def test_ata_in_inputs_is_rejected(params):
    ctx = _ctx(_eta(840, 850, 860, inputs={"gate_in": 851}))
    with pytest.raises(TimeLeakError):
        recommend(ctx, params)


def test_hold_wide_interval(params):
    rec = recommend(_ctx(_eta(830, 870, 930)), params)
    assert rec.status == "보류" and rec.hold_rule == "wide"
    assert "100분" in rec.hold_reason and rec.recommended == "A"


def test_hold_stale(params):
    rec = recommend(_ctx(_eta(840, 850, 860, issued=760.0)), params)
    assert rec.status == "보류" and rec.hold_rule == "stale"
    assert "40분" in rec.hold_reason


def test_hold_small_benefit(params):
    # 대상이 그룹 1이고 구역에 늦게 올 컨테이너가 없으면 어떤 대안도 유지안보다 나을 게 없음
    y = Yard.empty("A1", 1, 4, 3, {"V1": [0]})
    y.stacks[(0, 0)] = ["a1"]
    rec = recommend(_ctx(_eta(840, 850, 860), yard=y), params)
    assert rec.status == "보류" and rec.hold_rule == "small_benefit"


def test_hold_no_candidate(params):
    y = Yard.empty("A1", 1, 2, 3, {"V1": [0]})
    y.stacks[(0, 0)] = ["a1", "a2", "a3"]       # 남은 스택은 여유 스택 하나뿐
    rec = recommend(_ctx(_eta(840, 850, 860), yard=y), params)
    assert rec.status == "보류" and rec.hold_rule == "no_candidate"
    assert rec.n_candidates == 0


def test_options_start_with_keep_current_plan(params, scenarios, model):
    e = Engine(params, scenarios, "S1", model)
    e.step()
    rec = e.create_rec(e.world.focus[0])
    assert rec.options[0].key == "A" and rec.options[0].label == "현재 계획 유지"
    assert len(rec.options) >= 3
    assert all(o.total_p10 <= o.total_p90 for o in rec.options)


def test_scenarios_produce_expected_states(params, scenarios, model):
    # S2 보류, S3 후보 부족
    e2 = Engine(params, scenarios, "S2", model)
    e2.step()
    assert e2.create_rec(e2.world.focus[0]).hold_rule == "wide"
    e3 = Engine(params, scenarios, "S3", model)
    e3.step()
    assert e3.create_rec(e3.world.focus[0]).n_candidates <= 1
