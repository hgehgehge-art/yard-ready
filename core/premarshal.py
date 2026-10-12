"""유휴 시간 선작업 후보 (기획서 7.5).

- 크레인 유휴 시간대(작업량 < 상한의 50%) 안에서만 만듭니다.
- 인출 그룹이 더 늦은 컨테이너가 더 빠른 컨테이너 위에 있으면, 위 컨테이너를
  '스택 안의 가장 빠른 그룹이 자기보다 크거나 같은 스택'으로 옮기는 후보를 만듭니다.
- 한 번에 제안하는 선작업은 최대 3회입니다(단계적 승인).
"""
from __future__ import annotations

from core.constraints import slot_reasons
from core.models import INF_GROUP, Yard
from core.relocation import EMPTY_MIN, stack_min_group


def _is_blocker_top(stack: list[str], groups: dict) -> bool:
    if len(stack) < 2:
        return False
    g = groups.get(stack[-1], INF_GROUP)
    return g < INF_GROUP and g > stack_min_group(stack[:-1], groups)


def plan_premarshal(yard: Yard, vessel_id: str, groups: dict, max_moves: int, windows: list[tuple[float, int]],
                    avoid: set | None = None) -> tuple[Yard, list[dict]]:
    """선작업 계획과 적용 후 야드를 돌려줍니다. windows = [(슬롯 시작, 쓸 수 있는 횟수)]."""
    y = yard.copy()
    moves: list[dict] = []
    slots = [[t, n] for t, n in windows if n > 0]
    bays = y.area.get(vessel_id, [])
    avoid = avoid or set()
    while len(moves) < max_moves and slots:
        best = None
        for b in bays:
            for r in range(y.rows):
                src = (b, r)
                st = y.stacks[src]
                if src in avoid or not _is_blocker_top(st, groups):
                    continue
                box = st[-1]
                g = groups[box]
                below_min = stack_min_group(st[:-1], groups)
                for r2 in range(y.rows):
                    dst = (b, r2)
                    if dst == src or dst in avoid:
                        continue
                    m = stack_min_group(y.stacks[dst], groups)
                    if m < g:
                        continue
                    if slot_reasons(y, vessel_id, dst, box):
                        continue
                    # 가장 빠른 그룹을 가로막는 것부터, 목적지는 빈 스택보다 그룹이 맞는 스택 우선
                    key = (below_min, -g, 1 if m == EMPTY_MIN else 0, len(y.stacks[dst]), src, dst)
                    if best is None or key < best[0]:
                        best = (key, src, dst, box)
        if best is None:
            break
        _, src, dst, box = best
        t = slots[0][0]
        slots[0][1] -= 1
        if slots[0][1] <= 0:
            slots.pop(0)
        frm = y.slot_name(src[0], src[1], len(y.stacks[src]) - 1)
        to = y.next_slot_name(dst)
        y.stacks[src].pop()
        y.stacks[dst].append(box)
        moves.append({"cntr": box, "from": frm, "to": to, "src": src, "dst": dst, "t": t, "group": groups[box]})
    return y, moves
