"""두 정책 (기획서 8.1).

기준 정책: 게이트에 도착한 순서대로, 같은 선박 구역에서 같은 인출 그룹이 맨 위인 스택을 고르고,
없으면 가장 낮은 스택에 쌓습니다. 예약시각만 알고 ETA는 쓰지 않으며, 선작업은 하지 않습니다.
제안 정책: ETA 구간과 대안 비교(core.recommender)로 슬롯을 고르고, 보류 규칙에 걸리지 않을 때만 유휴 시간 선작업을 합니다.
자동 비교에서는 추천을 모두 승인한 것으로 봅니다. 제안 정책의 하루 실행은 core.experiment에 있습니다.
"""
from __future__ import annotations

from core.constraints import eligible_stacks
from core.models import INF_GROUP, Yard

BASELINE_LABEL = "기준 정책(공개 단순 규칙)"
PROPOSED_LABEL = "제안 정책(ETA 구간 + 대안 비교)"


def relaxed_stacks(yard: Yard, vessel_id: str, cntr: str | None = None) -> list:
    """과거 이력 재현용: 여유 스택 규칙만 빼고(최대 단·예약은 지킴) 놓을 수 있는 스택."""
    out = []
    for b in yard.area.get(vessel_id, []):
        for r in range(yard.rows):
            s = (b, r)
            owner = yard.reserved.get(s)
            if len(yard.stacks[s]) < yard.max_tier and (owner is None or owner == cntr):
                out.append(s)
    return out


def baseline_choose(yard: Yard, vessel_id: str, cntr: str, group: int, groups: dict, relaxed: bool = False,
                    candidates: list | None = None):
    cands = candidates if candidates is not None else eligible_stacks(yard, vessel_id, cntr)
    if not cands and relaxed:
        cands = relaxed_stacks(yard, vessel_id, cntr)
    if not cands:
        return None
    same = [s for s in cands if yard.stacks[s] and groups.get(yard.stacks[s][-1], INF_GROUP) == group]
    if same:
        return min(same, key=lambda s: (len(yard.stacks[s]), s))
    return min(cands, key=lambda s: (len(yard.stacks[s]), s))
