"""재취급 계산: 손으로 계산한 3×3 예제 (로우 3 × 단 3, 한 베이)."""
from core.relocation import lower_bound, retrieve


def _setup(cols):
    stacks = {(0, r): list(c) for r, c in enumerate(cols)}
    groups, seqs = {}, {}
    for col in cols:
        for c in col:
            groups[c] = int(c[1])          # 이름 두 번째 글자 = 그룹
            seqs[c] = int(c[3]) if len(c) > 3 else 0
    return stacks, groups, seqs


def test_example1_single_blocker():
    # S0=[g1, g3]  S1=[g2]  S2=[]
    # g1 인출 → 위의 g3을 빈 S2로 옮김(1회). 이후 g2, g3은 바로 인출. 합계 1회.
    stacks, groups, seqs = _setup([["g1_0", "g3_0"], ["g2_0"], []])
    r = retrieve(stacks, groups, seqs, max_tier=3)
    assert r.relocations == 1
    assert r.order == ["g1_0", "g2_0", "g3_0"]
    assert lower_bound(stacks.values(), groups) == 1


def test_example2_two_blockers_good_destinations():
    # S0=[g1, g2, g3]  S1=[g2]  S2=[g3, g3]
    # g1 인출: g3 → S2(최소 그룹 3, 2단) 1회, g2 → S1(최소 그룹 2) 2회. 이후 추가 이동 없음. 합계 2회.
    stacks, groups, seqs = _setup([["g1_0", "g2_1", "g3_2"], ["g2_0"], ["g3_0", "g3_1"]])
    r = retrieve(stacks, groups, seqs, max_tier=3)
    assert r.relocations == 2
    assert lower_bound(stacks.values(), groups) == 2


def test_example3_fallback_to_lowest_stack_and_cascade():
    # S0=[g2, g1]  S1=[g1, g3]  S2=[g3, g2, g4] (만단)
    # g1_0: S0 맨 위라 바로 인출.
    # g1_1: 위의 g3 이동. 늦은 그룹 스택 없음 → 가장 낮은 S0([g2])로(1회).
    # g2_0: S0 위 g3 → 빈 S1로(2회).  g2_1: S2 위 g4 → 빈 S0로(3회).  나머지 바로 인출. 합계 3회.
    # S2의 g2는 g3 위에 있지만 먼저 인출되므로 방해가 아님.
    stacks, groups, seqs = _setup([["g2_0", "g1_0"], ["g1_1", "g3_0"], ["g3_1", "g2_1", "g4_0"]])
    r = retrieve(stacks, groups, seqs, max_tier=3)
    assert r.relocations == 3
    assert [m[2] for m in r.moves if m[3] == "재취급"] == [(0, 0), (0, 1), (0, 0)]
    # 하한: S1의 g3(아래 g1), S2의 g4(아래 g2) = 2. 실제 3회 중 1회는 g3을 g2 위에 올려 생긴 연쇄 재취급.
    assert lower_bound(stacks.values(), groups) == 2


def test_same_group_swap_is_not_relocation():
    stacks, groups, seqs = _setup([["g1_0", "g1_1"], [], []])
    assert retrieve(stacks, groups, seqs, max_tier=3).relocations == 0
    assert retrieve(stacks, groups, seqs, max_tier=3, same_group_swap=False).relocations == 1


def test_original_not_mutated():
    stacks, groups, seqs = _setup([["g1_0", "g3_0"], ["g2_0"], []])
    before = {k: v[:] for k, v in stacks.items()}
    retrieve(stacks, groups, seqs, max_tier=3)
    assert stacks == before
