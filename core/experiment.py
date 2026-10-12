"""기준 정책과 제안 정책의 자동 비교 (기획서 8.1, 8.3).

두 정책은 같은 초기 적층, 같은 도착·계획 변경 사건, 같은 장비 조건에서 돌립니다.
평가 범위는 V1 구역(베이 01~04)이며, 06:00부터 V1 인출 시작까지 반입되는 컨테이너에 정책을 적용합니다.
자동 비교에서는 추천을 모두 승인한 것으로 봅니다.

전체 격자를 미리 돌려 CSV로 저장: python -m core.experiment
"""
from __future__ import annotations

import argparse
import itertools
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from core.constraints import eligible_stacks, slot_reasons
from core.generator import ROOT, generate_history, generate_world, idle_windows, load_params
from core.models import INF_GROUP, EtaIssue, parse_hhmm
from core.policies import baseline_choose
from core.premarshal import plan_premarshal
from core.recommender import Context, sample_times, simulate
from core.relocation import area_stacks, lower_bound, retrieve_area

VID = "V1"
CACHE = ROOT / "data" / "experiment_grid.csv"


def _eta_at(world, c: str, t: float) -> EtaIssue:
    best = None
    for e in world.eta_issues.get(c, []):
        if e.issued_at <= t and (best is None or e.issued_at >= best.issued_at):
            best = e
    if best is None:
        # 첫 ETA 발행 전: 예약 정보(운송사·예약시각)만으로 만든 값과 같으므로 발행 시각만 지금으로 둠
        first = min(world.eta_issues[c], key=lambda e: e.issued_at)
        best = EtaIssue(c, t, first.inputs, first.eta_p10, first.eta_p50, first.eta_p90, first.model)
    return best


def _decision_time(world, c: str) -> float:
    """제안 정책이 슬롯을 정하는 시각: '중간 지점 2' 통과 ETA 발행 시각(없으면 도착 직전 최신 발행)."""
    gate = world.arrivals[c].gate_in
    cps = world.bookings[c].checkpoints
    if len(cps) >= 3 and cps[2][1] < gate:
        return cps[2][1]
    issues = [e.issued_at for e in world.eta_issues.get(c, []) if e.issued_at < gate]
    return max(issues) if issues else gate - 1


def _apply_plan_change(world, rng, share: float) -> dict:
    groups = dict(world.plans[VID].retrieval_groups)
    members = sorted(groups)
    k = int(round(share * len(members)))
    ng = world.params["workplan"]["groups"]
    for c in rng.choice(members, size=k, replace=False):
        g = groups[c]
        groups[c] = int(np.clip(g + rng.choice([-1, 1]), 1, ng))
    return groups


def run_policy(world, policy: str, cond: dict, seed: int, plan_groups_after: dict | None) -> dict:
    params = world.params
    ep = params["experiment"]
    rp = params["recommender"]
    lam, move_min = rp["lambda"], params["crane"]["move_min"]
    swap = params["workplan"]["same_group_swap"]
    yard = world.yard.copy()
    groups = dict(world.groups)
    seqs = world.seqs
    t_ret = world.vessels[VID].berth_window_start
    t_change = parse_hhmm(ep["plan_change_time"])
    pend = sorted([c for c in world.arrivals if world.vessel_of[c] == VID and world.arrivals[c].gate_in >= world.start
                   and c not in world.overflow], key=lambda c: world.arrivals[c].gate_in)
    events = [(world.arrivals[c].gate_in, 1, "arrive", c) for c in pend]
    if plan_groups_after is not None:
        events.append((t_change, 0, "plan", None))
    if policy == "proposed":
        for c in pend:
            events.append((_decision_time(world, c), 2, "decide", c))
        for t, cap in idle_windows(world, world.start, t_ret):
            events.append((t, 3, "pm", cap))
    events.sort(key=lambda e: (e[0], e[1]))
    arrived = set()
    unfinished = 0
    n_recs = n_holds = n_res = 0
    pm_moves = 0
    pm_slot: dict[int, int] = {}
    slot = params["crane"]["slot_min"]
    rng = np.random.default_rng(zlib.crc32(f"{seed}|{policy}|{cond}".encode()))
    n_mc = ep["n_samples"]
    for t, _, kind, c in events:
        if t > t_ret and kind != "arrive":
            continue
        if kind == "plan":
            groups.update(plan_groups_after)
        elif kind == "arrive":
            arrived.add(c)
            target = next((s for s, o in yard.reserved.items() if o == c), None)
            s = None
            if target is not None:
                yard.reserved.pop(target, None)
                if not slot_reasons(yard, VID, target, c):
                    s = target
            if s is None:
                s = baseline_choose(yard, VID, c, groups.get(c, INF_GROUP), groups)
            if s is None:
                unfinished += 1
            else:
                yard.stacks[s].append(c)
        elif kind == "decide":
            if c in arrived:
                continue
            n_recs += 1
            e = _eta_at(world, c, t)
            if e.width > rp["hold"]["max_width_min"]:
                n_holds += 1
                continue
            others = [o for o in pend if o not in arrived and o != c and world.arrivals[o].gate_in > t]
            pending = {o: _eta_at(world, o, t) for o in others}
            look = t + ep["lookahead_min"]
            pending = {o: v for o, v in pending.items() if v.eta_p50 <= look}
            res = {o: s for s, o in yard.reserved.items() if o != c}
            ctx = Context(yard, VID, groups, seqs, c, e, pending, t, world.cutoff(VID), t_ret, [], 0, res)
            names = [c] + sorted(pending)
            times = sample_times([e] + [pending[o] for o in sorted(pending)], n_mc, rng)
            elig = eligible_stacks(yard, VID, c)
            if not elig:
                n_holds += 1
                continue
            g = groups.get(c, INF_GROUP)
            # 후보 줄이기: 늦은 그룹을 가로막지 않고 높이 쌓인 스택 우선
            def h(s):
                st = yard.stacks[s]
                below = min((groups.get(x, INF_GROUP) for x in st), default=10 ** 6)
                return (1 if below < g else 0, -len(st), s)
            cands = sorted(elig, key=h)[: ep["max_candidates"]]
            base = simulate(ctx, yard, None, times, names, swap)
            sb = base.total.mean() + lam * base.reloc.mean() * move_min
            best, best_s = None, None
            for s in cands:
                o = simulate(ctx, yard, s, times, names, swap)
                sc = o.total.mean() + lam * o.reloc.mean() * move_min
                if o.reloc.mean() * move_min - base.reloc.mean() * move_min >= rp["max_delay_increase_min"]:
                    continue
                if best is None or sc < best:
                    best, best_s = sc, s
            if best is None or sb - best < rp["hold"]["min_benefit"]:
                n_holds += 1
                continue
            yard.reserved[best_s] = c
            n_res += 1
        elif kind == "pm":
            cap = min(c, rp["max_premarshal"])
            y2, moves = plan_premarshal(yard, VID, groups, cap, [(t, cap)], set(yard.reserved))
            if not moves:
                continue
            others = [o for o in pend if o not in arrived and world.arrivals[o].gate_in > t]
            pending = {o: _eta_at(world, o, t) for o in others if _eta_at(world, o, t).eta_p50 <= t + ep["lookahead_min"]}
            ctx = Context(yard, VID, groups, seqs, "__none__", EtaIssue("__none__", t, {}, t, t, t, "-"), pending, t,
                          world.cutoff(VID), t_ret, [], 0, {o: s for s, o in yard.reserved.items()})
            names = sorted(pending)
            times = sample_times([pending[o] for o in names], n_mc, rng)
            o0 = simulate(ctx, yard, None, times, names, swap)
            o1 = simulate(ctx, y2, None, times, names, swap, len(moves))
            s0 = o0.total.mean() + lam * o0.reloc.mean() * move_min
            s1 = o1.total.mean() + lam * o1.reloc.mean() * move_min
            if s0 - s1 >= rp["hold"]["min_benefit"]:
                yard = y2
                pm_moves += len(moves)
                k = int(t // slot)
                pm_slot[k] = pm_slot.get(k, 0) + len(moves)
    # 인출
    targets = set(world.plans[VID].retrieval_groups)
    lb = lower_bound(area_stacks(yard, VID).values(), groups, targets)
    r = retrieve_area(yard, VID, groups, seqs, swap)
    delays = np.array(r.cum_reloc, dtype=float) * move_min
    # 트럭 체류: 기본 + 같은 15분 슬롯의 작업(반입·기타·선작업) 1건당 대기
    tp = params["truck"]
    dw = []
    for c in pend:
        k = int(world.arrivals[c].gate_in // slot)
        ld = world.crane_load.get(k, 0) + pm_slot.get(k, 0)
        dw.append(tp["base_dwell_min"] + tp["wait_per_job_min"] * ld)
    return {"seed": seed, "policy": policy, "pm": pm_moves, "reloc": r.relocations, "lb": lb, "retrieved": r.order,
            "delay_mean": float(delays.mean()) if len(delays) else 0.0,
            "delay_p95": float(np.percentile(delays, 95)) if len(delays) else 0.0,
            "dwell_mean": float(np.mean(dw)) if dw else 0.0, "dwell_p95": float(np.percentile(dw, 95)) if dw else 0.0,
            "unfinished": unfinished, "n_recs": n_recs, "n_holds": n_holds, "n_reserved": n_res,
            "occupancy_start": world.yard.occupancy(), "n_policy": len(pend)}


def run_condition(params, model, occ_label: str, eta_err: float, plan_change: bool, urgent: bool, seeds) -> pd.DataFrame:
    ep = params["experiment"]
    occ = ep["occupancy_levels"][occ_label]
    rows = []
    for seed in seeds:
        world = generate_world(params, None, model, seed=seed, start=parse_hhmm(ep["start"]), occupancy=occ,
                               err_scale=eta_err, urgent=urgent, cap_vessels={VID})
        rng = np.random.default_rng(seed + 77)
        after = _apply_plan_change(world, rng, ep["plan_change_share"]) if plan_change else None
        pair = [run_policy(world, pol, {"o": occ_label, "e": eta_err, "p": plan_change, "u": urgent}, seed, after)
                for pol in ("baseline", "proposed")]
        common = set(pair[0]["retrieved"]) & set(pair[1]["retrieved"])
        n = max(1, len(common))
        for r in pair:
            r.pop("retrieved")
            r.update({"n_eval": n, "per100": (r["pm"] + r["reloc"]) / n * 100, "pm_per100": r["pm"] / n * 100,
                      "reloc_per100": r["reloc"] / n * 100, "lb_per100": r["lb"] / n * 100,
                      "occupancy": occ_label, "eta_error": eta_err, "plan_change": plan_change, "urgent": urgent})
            rows.append(r)
    return pd.DataFrame(rows)


def run_grid(params, model, seeds, full: bool = False, log=print) -> pd.DataFrame:
    ep = params["experiment"]
    combos = itertools.product(ep["occupancy_levels"], ep["eta_error_levels"], [False, True] if full else [False],
                               [False, True] if full else [False])
    parts = []
    for occ, err, pc, ug in combos:
        t = time.time()
        df = run_condition(params, model, occ, err, pc, ug, seeds)
        parts.append(df)
        b = df[df.policy == "baseline"].per100.mean()
        p = df[df.policy == "proposed"].per100.mean()
        log(f"{occ} × {err} × 계획변경 {pc} × 긴급 {ug}: 기준 {b:.1f} / 제안 {p:.1f} ({time.time() - t:.1f}s)")
    return pd.concat(parts, ignore_index=True)


def main():
    ap = argparse.ArgumentParser(description="정책 비교 격자를 미리 돌려 CSV로 저장합니다.")
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--full", action="store_true", help="계획 변경·긴급 작업 조합까지 모두 돌림")
    ap.add_argument("--out", type=Path, default=CACHE)
    a = ap.parse_args()
    params = load_params()
    from core.eta import train_and_validate
    model = train_and_validate(generate_history(params), params)["serving"]
    n = a.seeds or params["experiment"]["grid_seeds"]
    seeds = range(1000, 1000 + n)
    df = run_grid(params, model, seeds, a.full)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False)
    print(f"저장: {a.out} ({len(df)}행)")


if __name__ == "__main__":
    main()
