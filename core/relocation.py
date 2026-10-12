"""재취급 계산 (기획서 7.6).

작업계획의 인출 순서(그룹 순, 같은 그룹은 계획상 순번)대로 꺼내고, 대상 위의 컨테이너를
같은 베이의 다른 스택으로 옮길 때마다 재취급 1회로 셉니다.
가정(params.workplan.same_group_swap): 같은 그룹 컨테이너가 위에 있으면 순번을 바꿔 먼저 인출하며 재취급으로 세지 않습니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.models import INF_GROUP

EMPTY_MIN = 10 ** 6


def _g(groups: dict, c: str) -> int:
    return groups.get(c, INF_GROUP)


def stack_min_group(stack: list[str], groups: dict) -> int:
    return min((_g(groups, c) for c in stack), default=EMPTY_MIN)


def blockers_above(stack: list[str], idx: int, groups: dict) -> int:
    """stack[idx] 위에 있으면서 그보다 늦게 인출되는(그룹 번호가 큰) 컨테이너 수."""
    g = _g(groups, stack[idx])
    return sum(1 for c in stack[idx + 1:] if _g(groups, c) > g)


def lower_bound_stack(stack: list[str], groups: dict) -> int:
    """아래에 더 빠른 그룹이 있는 컨테이너 수. 어떤 방법으로도 최소 한 번은 옮겨야 합니다."""
    lb, run_min = 0, EMPTY_MIN
    for c in stack:
        g = _g(groups, c)
        if g > run_min:
            lb += 1
        run_min = min(run_min, g)
    return lb


def lower_bound(stacks, groups: dict, targets: set | None = None) -> int:
    total = 0
    for st in stacks:
        if targets is not None and not any(c in targets for c in st):
            continue
        total += lower_bound_stack(st, groups)
    return total


@dataclass
class RetrievalResult:
    relocations: int = 0
    order: list[str] = field(default_factory=list)
    cum_reloc: list[int] = field(default_factory=list)     # 각 인출 완료 시점까지 누적 재취급 수
    moves: list[tuple[str, tuple, tuple, str]] = field(default_factory=list)  # (cntr, from, to, kind)


def choose_destination(stacks: dict, src, moving: str, groups: dict, max_tier: int, swap: bool):
    """옮길 스택: 그 스택의 가장 빠른 그룹이 옮기는 컨테이너보다 늦은 곳을 우선, 없으면 가장 낮은 스택."""
    g = _g(groups, moving)
    room = [s for s in stacks if s != src and s[0] == src[0] and len(stacks[s]) < max_tier]
    if not room:
        return None
    good = []
    for s in room:
        m = stack_min_group(stacks[s], groups)
        if m > g or (swap and m == g):
            good.append(s)
    if good:
        return min(good, key=lambda s: (stack_min_group(stacks[s], groups), len(stacks[s]), s))
    return min(room, key=lambda s: (len(stacks[s]), s))


def retrieve(stacks: dict, groups: dict, seqs: dict, max_tier: int, same_group_swap: bool = True,
             targets: set | None = None) -> RetrievalResult:
    """stacks: (bay, row) -> list. 원본을 바꾸지 않습니다. targets가 없으면 groups에 있는 컨테이너를 모두 인출합니다."""
    st = {k: v[:] for k, v in stacks.items()}
    loc = {}
    for s, lst in st.items():
        for c in lst:
            loc[c] = s
    tset = set(targets) if targets is not None else {c for c in groups if c in loc}
    remaining = sorted((c for c in tset if c in loc), key=lambda c: (_g(groups, c), seqs.get(c, 0), c))
    rem_set = set(remaining)
    res = RetrievalResult()
    buffer: set = set()   # 같은 베이에 빈 곳이 없을 때 임시로 빼 둔 컨테이너
    i = 0
    guard = 0
    while i < len(remaining):
        guard += 1
        if guard > 100000:
            raise RuntimeError("retrieve loop guard")
        t = remaining[i]
        if t not in rem_set:
            i += 1
            continue
        if t in buffer:
            buffer.discard(t)
            rem_set.discard(t)
            res.order.append(t)
            res.cum_reloc.append(res.relocations)
            res.moves.append((t, ("buffer",), None, "정상 인출"))
            i += 1
            continue
        s = loc[t]
        top = st[s][-1]
        if top == t:
            st[s].pop()
            rem_set.discard(t)
            res.order.append(t)
            res.cum_reloc.append(res.relocations)
            res.moves.append((t, s, None, "정상 인출"))
            i += 1
            continue
        if same_group_swap and top in rem_set and _g(groups, top) == _g(groups, t):
            st[s].pop()
            rem_set.discard(top)
            res.order.append(top)
            res.cum_reloc.append(res.relocations)
            res.moves.append((top, s, None, "정상 인출"))
            continue
        dest = choose_destination(st, s, top, groups, max_tier, same_group_swap)
        st[s].pop()
        res.relocations += 1
        if dest is None:
            buffer.add(top)
            del loc[top]
            res.moves.append((top, s, ("buffer",), "재취급"))
        else:
            st[dest].append(top)
            loc[top] = dest
            res.moves.append((top, s, dest, "재취급"))
    return res


def area_stacks(yard, vessel_id: str) -> dict:
    return {s: v for s, v in yard.stacks.items() if s[0] in yard.area.get(vessel_id, [])}


def retrieve_area(yard, vessel_id: str, groups: dict, seqs: dict, same_group_swap: bool = True) -> RetrievalResult:
    stacks = area_stacks(yard, vessel_id)
    targets = {c for lst in stacks.values() for c in lst if c in groups and groups[c] < INF_GROUP}
    return retrieve(stacks, groups, seqs, yard.max_tier, same_group_swap, targets)
