"""제약: 공중 적치, 최대 단 초과, 예약 칸, 여유 스택 없음, 배정 베이."""
from core.constraints import eligible_stacks, slot_reasons
from core.models import Yard


def _yard(cols, max_tier=3):
    y = Yard.empty("A1", 2, len(cols), max_tier, {"V1": [0], "V2": [1]})
    for r, c in enumerate(cols):
        y.stacks[(0, r)] = list(c)
    return y


def test_floating_is_rejected():
    y = _yard([["a"], [], []])
    assert "floating" in slot_reasons(y, "V1", (0, 0), "x", tier=2)
    assert "occupied" in slot_reasons(y, "V1", (0, 0), "x", tier=0)
    assert slot_reasons(y, "V1", (0, 0), "x", tier=1) == []


def test_max_tier_is_rejected():
    y = _yard([["a", "b", "c"], [], []])
    assert "full" in slot_reasons(y, "V1", (0, 0), "x")


def test_reserved_slot_is_rejected_for_others():
    y = _yard([["a"], [], []])
    y.reserved[(0, 0)] = "HMMU1234567"
    assert "reserved" in slot_reasons(y, "V1", (0, 0), "OTHER")
    assert slot_reasons(y, "V1", (0, 0), "HMMU1234567") == []


def test_must_keep_one_empty_stack():
    y = _yard([["a"], [], []])
    # 빈 스택이 2개라 하나는 써도 됨
    assert slot_reasons(y, "V1", (0, 1), "x") == []
    y.stacks[(0, 1)] = ["b"]
    # 남은 빈 스택은 (0,2) 하나 → 거기에 놓으면 여유 스택이 없어짐
    assert "reserve_stack" in slot_reasons(y, "V1", (0, 2), "x")
    assert slot_reasons(y, "V1", (0, 0), "x") == []
    # 빈 스택이 아예 없으면 어디에도 놓을 수 없음
    y.stacks[(0, 2)] = ["c"]
    assert eligible_stacks(y, "V1", "x") == []


def test_other_vessel_bay_is_rejected():
    y = _yard([["a"], [], []])
    assert "area" in slot_reasons(y, "V2", (0, 0), "x")
    assert all(s[0] == 0 for s in eligible_stacks(y, "V1", "x"))
