"""슬롯 적격성 검사 (기획서 7.3). 안전·용량 제약은 점수와 바꾸지 않는 필수 조건입니다."""
from __future__ import annotations

from core.models import Yard

REASONS = {
    "area": "이 선박에 배정된 베이가 아닙니다.",
    "full": "최대 단에 도달했습니다.",
    "floating": "아래 칸이 비어 있어 공중 적치가 됩니다.",
    "occupied": "이미 컨테이너가 있는 칸입니다.",
    "reserved": "다른 컨테이너에 예약된 칸입니다.",
    "reserve_stack": "재취급용 빈 스택을 남기지 못합니다.",
}


def _empty_reserve_stacks(yard: Yard, bay: int, cntr: str | None) -> list[tuple[int, int]]:
    """재취급 여유공간으로 쓸 수 있는 빈 스택(다른 컨테이너 예약이 없는 빈 스택)."""
    out = []
    for r in range(yard.rows):
        s = (bay, r)
        if yard.stacks[s]:
            continue
        owner = yard.reserved.get(s)
        if owner is not None and owner != cntr:
            continue
        out.append(s)
    return out


def slot_reasons(yard: Yard, vessel_id: str, stack: tuple[int, int], cntr: str | None = None,
                 tier: int | None = None) -> list[str]:
    """해당 스택(필요하면 특정 단)에 cntr를 놓을 때 어기는 제약의 코드 목록. 빈 목록이면 적격입니다."""
    bay, row = stack
    h = yard.height(stack)
    t = h if tier is None else tier
    reasons = []
    if bay not in yard.area.get(vessel_id, []):
        reasons.append("area")
    if t > h:
        reasons.append("floating")
    elif t < h:
        reasons.append("occupied")
    if h >= yard.max_tier or t >= yard.max_tier:
        reasons.append("full")
    owner = yard.reserved.get(stack)
    if owner is not None and owner != cntr:
        reasons.append("reserved")
    if not reasons:
        empties = _empty_reserve_stacks(yard, bay, cntr)
        remaining = [s for s in empties if s != stack]
        if not remaining:
            reasons.append("reserve_stack")
    return reasons


def eligible_stacks(yard: Yard, vessel_id: str, cntr: str | None = None) -> list[tuple[int, int]]:
    out = []
    for bay in yard.area.get(vessel_id, []):
        for row in range(yard.rows):
            if not slot_reasons(yard, vessel_id, (bay, row), cntr):
                out.append((bay, row))
    return out


def explain_stacks(yard: Yard, vessel_id: str, cntr: str | None = None) -> list[dict]:
    """선박 구역의 모든 스택 맨 위 칸에 대해 적격 여부와 사유를 돌려줍니다(화면 표시용)."""
    rows = []
    for bay in yard.area.get(vessel_id, []):
        for row in range(yard.rows):
            s = (bay, row)
            codes = slot_reasons(yard, vessel_id, s, cntr)
            rows.append({"stack": s, "tier": yard.height(s), "slot": yard.next_slot_name(s) if yard.height(s) < yard.max_tier
                         else f"{yard.block}-{bay + 1:02d}-{row + 1}-만단", "ok": not codes,
                         "codes": codes, "reasons": [REASONS[c] for c in codes]})
    return rows
