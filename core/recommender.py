"""배치 대안 비교 (기획서 7.4)와 추천 보류 규칙 (7.7).

1. 대안: A 현재 계획 유지(기준 정책이 도착 시점에 고를 슬롯), B 최초 적치 후보 변경(적격 슬롯 중 상위 2개),
   C 선작업 포함(7.5 선작업 뒤 가장 좋은 슬롯).
2. 몬테카를로: 같은 선박 구역에 아직 도착하지 않은 컨테이너의 도착 시각을 각자의 ETA 구간에서 N번 뽑고,
   표본마다 나머지는 기준 정책으로 쌓은 뒤 작업계획 인출 순서대로 꺼낼 때의 재취급을 셉니다.
   모든 대안에 같은 표본을 씁니다(공통 난수).
3. 점수 = E[선작업 + 재취급] + λ × E[야드 공급 지연(분)], 공급 지연 = 재취급 × 이동 1회 시간.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np

from core.constraints import eligible_stacks, slot_reasons
from core.models import INF_GROUP, Arrival, EtaIssue, Option, Recommendation, Yard, hhmm
from core.policies import baseline_choose
from core.premarshal import plan_premarshal
from core.relocation import retrieve_area

Z90 = 1.2815515655446004


class TimeLeakError(ValueError):
    """추천 계산에 그 시점에 알 수 없던 정보(ATA 등)가 들어왔을 때."""


@dataclass
class Context:
    yard: Yard
    vessel_id: str
    groups: dict
    seqs: dict
    target: str
    target_eta: EtaIssue
    pending: dict                    # 같은 선박의 미도착 컨테이너 → 그 시점 최신 EtaIssue
    now: float
    cutoff: float
    retrieval_start: float
    windows: list = field(default_factory=list)   # 유휴 슬롯 [(시작, 가능 횟수)]
    plan_version: int = 0
    reservations: dict = field(default_factory=dict)  # 다른 컨테이너의 예약 스택 cntr → (bay,row)


def guard_inputs(now: float, etas) -> None:
    for e in etas:
        if isinstance(e, Arrival) or not isinstance(e, EtaIssue):
            raise TimeLeakError(f"추천 계산에는 EtaIssue만 넣을 수 있습니다: {type(e).__name__}")
        if e.issued_at > now + 1e-9:
            raise TimeLeakError(f"{e.cntr_no}: {hhmm(e.issued_at)} 발행 ETA를 {hhmm(now)} 계산에 쓸 수 없습니다.")
        keys = {str(k).lower() for k in (e.inputs or {})}
        if keys & {"gate_in", "ata", "실제 도착", "arrival"}:
            raise TimeLeakError(f"{e.cntr_no}: ETA 입력에 실제 도착 정보가 있습니다.")


def sample_times(etas: list[EtaIssue], n: int, rng: np.random.Generator) -> np.ndarray:
    """p10·p50·p90에 맞춘 비대칭 정규분포에서 도착 시각을 뽑습니다. (n, k)"""
    if not etas:
        return np.zeros((n, 0))
    p10 = np.array([e.eta_p10 for e in etas])
    p50 = np.array([e.eta_p50 for e in etas])
    p90 = np.array([e.eta_p90 for e in etas])
    lo = np.maximum((p50 - p10) / Z90, 0.5)
    hi = np.maximum((p90 - p50) / Z90, 0.5)
    z = rng.standard_normal((n, len(etas)))
    return p50 + np.where(z < 0, z * lo, z * hi)


@dataclass
class SimOut:
    total: np.ndarray
    reloc: np.ndarray
    buried: np.ndarray
    cover_groups: list
    unplaced: np.ndarray
    x_unplaced: np.ndarray
    final_example: Yard | None = None


def simulate(ctx: Context, yard0: Yard, x_stack, times: np.ndarray, names: list[str], swap: bool,
             pm_moves: int = 0, keep_example: int | None = None) -> SimOut:
    n = times.shape[0]
    reloc = np.zeros(n)
    buried = np.zeros(n)
    unplaced = np.zeros(n)
    x_un = np.zeros(n)
    covers = []
    gx = ctx.groups.get(ctx.target, INF_GROUP)
    example = None
    for i in range(n):
        y = yard0.copy()
        if x_stack is not None:
            y.reserved[x_stack] = ctx.target
        order = np.argsort(times[i], kind="stable")
        for j in order:
            c = names[j]
            s = None
            if c == ctx.target and x_stack is not None and not slot_reasons(y, ctx.vessel_id, x_stack, c):
                s = x_stack
            elif c in ctx.reservations and not slot_reasons(y, ctx.vessel_id, ctx.reservations[c], c):
                s = ctx.reservations[c]
            if s is None:
                s = baseline_choose(y, ctx.vessel_id, c, ctx.groups.get(c, INF_GROUP), ctx.groups)
            if s is None:
                unplaced[i] += 1
                if c == ctx.target:
                    x_un[i] = 1
                continue
            y.stacks[s].append(c)
            if y.reserved.get(s) == c:
                del y.reserved[s]
        pos = y.find(ctx.target)
        if pos is not None:
            st = y.stacks[(pos[0], pos[1])]
            above = [ctx.groups.get(c, INF_GROUP) for c in st[pos[2] + 1:]]
            later = [g for g in above if gx < g < INF_GROUP]
            buried[i] = len(later)
            covers.extend(later)
        reloc[i] = retrieve_area(y, ctx.vessel_id, ctx.groups, ctx.seqs, swap).relocations
        if keep_example is not None and i == keep_example:
            example = y
    return SimOut(reloc + pm_moves, reloc, buried, covers, unplaced, x_un, example)


def _rng(ctx: Context, salt: str = "") -> np.random.Generator:
    key = f"{ctx.target}|{int(round(ctx.now))}|{ctx.plan_version}|{salt}"
    return np.random.default_rng(zlib.crc32(key.encode()))


def _score(out: SimOut, lam: float, move_min: float) -> float:
    return float(out.total.mean() + lam * (out.reloc.mean() * move_min))


def screen_stacks(ctx: Context, yard0: Yard, stacks: list, times, names, swap, lam, move_min, pm: int = 0) -> list:
    res = []
    for s in stacks:
        out = simulate(ctx, yard0, s, times, names, swap, pm)
        res.append((_score(out, lam, move_min), s))
    return sorted(res)


def representative_yard(ctx: Context, yard0: Yard, x_stack, swap: bool) -> Yard:
    """모두 ETA 중앙값에 도착한다고 볼 때의 인출 직전 적층(화면의 '예상 단면'용)."""
    names = [ctx.target] + sorted(ctx.pending)
    etas = [ctx.target_eta] + [ctx.pending[c] for c in sorted(ctx.pending)]
    times = np.array([[e.eta_p50 for e in etas]])
    return simulate(ctx, yard0, x_stack, times, names, swap, keep_example=0).final_example


def build_option(key, label, out: SimOut, base: SimOut | None, lam, move_min, stack, slot_name, pm=None, window="") -> Option:
    diff = out.total - (base.total if base is not None else out.total)
    cg = None
    if out.cover_groups:
        vals, cnt = np.unique(out.cover_groups, return_counts=True)
        cg = int(vals[np.argmax(cnt)])
    return Option(key=key, label=label, stack=stack, slot_name=slot_name, premarshal=pm or [], idle_window=window,
                  pm_mean=float(len(pm or [])), reloc_mean=float(out.reloc.mean()), total_mean=float(out.total.mean()),
                  total_p10=float(np.percentile(out.total, 10)), total_p90=float(np.percentile(out.total, 90)),
                  diff_mean=float(diff.mean()), diff_p10=float(np.percentile(diff, 10)), diff_p90=float(np.percentile(diff, 90)),
                  delay_mean=float(out.reloc.mean() * move_min), score=_score(out, lam, move_min),
                  buried_share=float((out.buried > 0).mean()), buried_mean=float(out.buried.mean()), top_cover_group=cg,
                  unplaced_share=float(out.x_unplaced.mean()))


def _narrative(o: Option, n: int, gx: int, a: Option | None) -> list[str]:
    k = int(round(o.buried_share * n))
    lines = []
    if o.key == "A":
        lines.append("기준 정책은 도착 시점에 같은 그룹이 맨 위인 스택을 고르고, 없으면 가장 낮은 스택을 고릅니다.")
    elif o.key == "C":
        lines.append(f"유휴 시간대 {o.idle_window}에 선작업 {len(o.premarshal)}회로 앞선 그룹을 가로막는 컨테이너를 먼저 옮깁니다.")
    else:
        lines.append(f"{o.slot_name}에 놓도록 미리 예약하고, 다른 컨테이너는 이 칸을 쓰지 않습니다.")
    if k > 0 and o.top_cover_group is not None:
        lines.append(f"표본 {n}개 중 {k}개에서 그룹 {o.top_cover_group} 컨테이너가 그룹 {gx}인 대상 위에 쌓입니다"
                     f"(평균 {o.buried_mean:.1f}개).")
    else:
        lines.append(f"표본 {n}개 모두에서 대상 위에 늦은 그룹이 쌓이지 않습니다.")
    if a is not None and o.key != "A":
        d = o.diff_mean
        if abs(d) < 0.05:
            lines.append("유지안과 예상 추가 이동이 같습니다.")
        else:
            lines.append(f"유지안보다 예상 추가 이동이 {abs(d):.1f}회 {'적습니다' if d < 0 else '많습니다'}.")
    if o.unplaced_share > 0:
        lines.append(f"표본의 {o.unplaced_share * 100:.0f}%에서 이 구역에 놓을 칸이 없어 다른 블록으로 보내야 합니다.")
    return lines


def recommend(ctx: Context, params: dict, rec_id: str | None = None) -> Recommendation:
    guard_inputs(ctx.now, [ctx.target_eta] + list(ctx.pending.values()))
    rp = params["recommender"]
    hold = rp["hold"]
    lam, move_min = rp["lambda"], params["crane"]["move_min"]
    swap = params["workplan"]["same_group_swap"]
    n, n_screen = rp["n_samples"], rp["n_screen"]
    names = [ctx.target] + sorted(ctx.pending)
    etas = [ctx.target_eta] + [ctx.pending[c] for c in sorted(ctx.pending)]
    times = sample_times(etas, n, _rng(ctx, "full"))
    times_s = times[:n_screen]
    gx = ctx.groups.get(ctx.target, INF_GROUP)

    yard0 = ctx.yard
    elig = eligible_stacks(yard0, ctx.vessel_id, ctx.target)
    a_slot = baseline_choose(yard0, ctx.vessel_id, ctx.target, gx, ctx.groups, candidates=elig) if elig else None
    out_a = simulate(ctx, yard0, None, times, names, swap)
    opt_a = build_option("A", "현재 계획 유지", out_a, None, lam, move_min, None,
                         yard0.next_slot_name(a_slot) if a_slot else "놓을 칸 없음")
    options = [opt_a]

    # B: 적격 슬롯을 미리 예약. 현재 계획 슬롯을 예약하는 것도 후보(도착 전에 다른 컨테이너가 차지하지 않게 함)
    if elig:
        ranked = screen_stacks(ctx, yard0, elig, times_s, names, swap, lam, move_min)
        for i, (_, s) in enumerate(ranked[:2]):
            out = simulate(ctx, yard0, s, times, names, swap)
            label = "현재 계획 슬롯 예약" if s == a_slot else "최초 적치 후보 변경"
            options.append(build_option(f"B{i + 1}", label, out, out_a, lam, move_min, s, yard0.next_slot_name(s)))

    # C: 선작업 포함
    windows = [w for w in ctx.windows if ctx.now <= w[0] < ctx.retrieval_start]
    pm_note = ""
    if windows:
        avoid = set(ctx.reservations.values())
        y_pm, moves = plan_premarshal(yard0, ctx.vessel_id, ctx.groups, rp["max_premarshal"], windows, avoid)
        if moves:
            elig_pm = eligible_stacks(y_pm, ctx.vessel_id, ctx.target)
            if elig_pm:
                ranked = screen_stacks(ctx, y_pm, elig_pm, times_s, names, swap, lam, move_min, len(moves))
                s = ranked[0][1]
                out = simulate(ctx, y_pm, s, times, names, swap, len(moves))
                ts = sorted({m["t"] for m in moves})
                win = f"{hhmm(ts[0])}~{hhmm(ts[-1] + params['crane']['slot_min'])}"
                options.append(build_option("C", "선작업 포함", out, out_a, lam, move_min, s, y_pm.next_slot_name(s), moves, win))
        else:
            pm_note = "앞선 그룹을 가로막는 컨테이너가 없어 선작업 후보가 없습니다."
    else:
        pm_note = "인출 시작 전에 크레인 유휴 시간대가 없어 선작업 후보가 없습니다."

    for o in options:
        o.reasons = _narrative(o, n, gx, opt_a)
        if o.key != "A" and o.delay_mean - opt_a.delay_mean >= rp["max_delay_increase_min"]:
            o.excluded = f"공급 지연이 유지안보다 {o.delay_mean - opt_a.delay_mean:.0f}분 늘어 추천에서 제외합니다."

    # 보류 규칙
    e = ctx.target_eta
    width = e.width
    stale = ctx.now - e.issued_at
    status, reason, rule, recommended = "추천됨", "", "", "A"
    valid = [o for o in options if o.key != "A" and not o.excluded]
    best = min(valid, key=lambda o: o.score) if valid else None
    if not elig:
        status, rule = "보류", "no_candidate"
        reason = "제약을 만족하는 슬롯이 없어 현재 계획을 유지합니다."
    elif width > hold["max_width_min"]:
        status, rule = "보류", "wide"
        reason = (f"예측 구간이 {width:.0f}분으로 넓어 선작업 추천을 보류합니다. 현재 규칙대로 장치합니다. "
                  f"(기준 {hold['max_width_min']}분)")
    elif stale > hold["max_staleness_min"]:
        status, rule = "보류", "stale"
        reason = (f"도착 정보가 {stale:.0f}분 동안 갱신되지 않아 추천을 보류합니다. (기준 {hold['max_staleness_min']}분)")
    elif best is None or opt_a.score - best.score < hold["min_benefit"]:
        status, rule = "보류", "small_benefit"
        saving = 0.0 if best is None else opt_a.score - best.score
        reason = (f"예상 절감이 {max(saving, 0):.1f}회(이동 환산)로 작아 현재 계획을 유지하는 편이 낫습니다. "
                  f"(기준 {hold['min_benefit']}회)")
    else:
        recommended = best.key
    if status == "보류":
        for o in options:
            if o.key == "C" and rule in ("wide", "stale"):
                o.held = "보류"

    exception = ""
    if e.eta_p50 > ctx.cutoff:
        exception = f"ETA 중앙값 {hhmm(e.eta_p50)}이 반입 마감 {hhmm(ctx.cutoff)}을 넘습니다."
    elif e.eta_p90 > ctx.cutoff:
        exception = f"ETA 구간 끝 {hhmm(e.eta_p90)}이 반입 마감 {hhmm(ctx.cutoff)}을 넘습니다."

    note = pm_note
    if len(elig) == 1:
        note = (note + " " if note else "") + "제약을 만족하는 후보가 1개뿐입니다."
    elif 0 < len(elig) <= 2:
        note = (note + " " if note else "") + f"제약을 만족하는 후보가 {len(elig)}개뿐입니다."
    rid = rec_id or f"R-{ctx.target[-4:]}-{int(round(ctx.now)):04d}-v{ctx.plan_version}"
    return Recommendation(rec_id=rid, cntr_no=ctx.target, created_at=ctx.now, plan_version=ctx.plan_version,
                          data_as_of=e.issued_at, options=options, status=status, hold_reason=reason, hold_rule=rule,
                          recommended=recommended, exception=exception, n_candidates=len(elig), eta=e,
                          vessel_id=ctx.vessel_id, group=gx, note=note.strip())
