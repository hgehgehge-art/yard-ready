"""합성 야드·예약·이력·선박 일정 (기획서 6장). 같은 시드에서는 항상 같은 결과가 나옵니다."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from core.eta import history_to_rows
from core.models import INF_GROUP, Arrival, Booking, EtaIssue, Vessel, WorkPlan, Yard, parse_hhmm
from core.constraints import eligible_stacks
from core.policies import baseline_choose, relaxed_stacks

ROOT = Path(__file__).resolve().parent.parent
PREFIXES = ["HDMU", "SMLU", "TCLU", "NYKU", "MSCU", "EGLV", "HMMU", "KMTU", "SKLU", "PCIU", "ONEU", "CMAU"]


def load_params(path: Path | None = None) -> dict:
    with open(path or ROOT / "config" / "params.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_scenarios(path: Path | None = None) -> dict:
    with open(path or ROOT / "config" / "scenarios.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def carriers(params) -> dict:
    return {c["id"]: c for c in params["carriers"]}


def congestion(params, minute_of_day: float) -> float:
    return float(params["arrival"]["congestion_by_hour"].get(int(minute_of_day % 1440 // 60), 0))


def _legs(params, rng, err: float) -> np.ndarray:
    a = params["arrival"]
    fr = [c["frac"] for c in a["checkpoints"]] + [1.0]
    travel = max(30.0, rng.normal(a["travel_mean_min"], a["travel_sd_min"]))
    d = np.diff(fr) * travel
    return d * np.exp(err * a["leg_noise"] * rng.standard_normal(len(d)))


# ── 과거 이력 (ETA 학습·검증용) ───────────────────────────
def generate_history(params, seed: int | None = None) -> pd.DataFrame:
    seed = params["seed"] if seed is None else seed
    rng = np.random.default_rng(seed + 1000)
    hp = params["history"]
    cs = params["carriers"]
    ids = [c["id"] for c in cs if c["share"] > 0]
    share = np.array([c["share"] for c in cs if c["share"] > 0])
    share = share / share.sum()
    cmap = carriers(params)
    n_cp = len(params["arrival"]["checkpoints"])
    rows = []
    for day in range(hp["days"]):
        day_carriers = list(rng.choice(ids, size=hp["trips_per_day"], p=share))
        # 이력이 짧은 운송사(신규)는 최근 며칠만 소량 운행
        for c in cs:
            if c["share"] == 0 and day >= hp["days"] - c["history_days"]:
                day_carriers += [c["id"]] * 3
        for cid in day_carriers:
            c = cmap[cid]
            booking = float(rng.integers(5 * 6, 23 * 6) * 10)       # 05:00~23:00, 10분 단위
            dev = c["mean"] + congestion(params, booking) + c["sd"] * rng.standard_normal()
            arrival = booking + dev
            legs = _legs(params, rng, 1.0)
            depart = arrival - legs.sum()
            cps = depart + np.concatenate([[0], np.cumsum(legs)[:-1]])
            r = {"day": day, "dow": day % 7, "carrier": cid, "booking": booking, "arrival": arrival, "depart": depart}
            for k in range(n_cp):
                r[f"cp{k}"] = cps[k]
            rows.append(r)
    return pd.DataFrame(rows)


# ── 시연일 세계 ───────────────────────────────────────────
@dataclass
class World:
    params: dict
    seed: int
    start: float
    yard: Yard
    vessels: dict
    plans: dict
    bookings: dict
    arrivals: dict
    vessel_of: dict
    carrier_of: dict
    eta_issues: dict
    crane_load: dict
    focus: list = field(default_factory=list)
    scripted: set = field(default_factory=set)
    overflow: set = field(default_factory=set)
    fillers: set = field(default_factory=set)
    err_scale: float = 1.0

    @property
    def groups(self) -> dict:
        g = {}
        for p in self.plans.values():
            g.update(p.retrieval_groups)
        return g

    @property
    def seqs(self) -> dict:
        s = {}
        for p in self.plans.values():
            s.update(p.retrieval_seq)
        return s

    def cutoff(self, vessel_id: str) -> float:
        return self.plans[vessel_id].cutoff

    def pending(self, now: float, vessel_id: str | None = None) -> list[str]:
        placed = {c for st in self.yard.stacks.values() for c in st}
        out = [c for c, a in self.arrivals.items() if c not in placed and c not in self.overflow
               and c not in self.fillers and (vessel_id is None or self.vessel_of[c] == vessel_id)]
        return sorted(out, key=lambda c: self.bookings[c].booking_time)

    def latest_eta(self, cntr: str, now: float) -> EtaIssue | None:
        best = None
        for e in self.eta_issues.get(cntr, []):
            if e.issued_at <= now and (best is None or e.issued_at >= best.issued_at):
                best = e
        return best


def _cntr_numbers(rng, n: int, taken: set) -> list[str]:
    out = []
    while len(out) < n:
        c = f"{PREFIXES[rng.integers(len(PREFIXES))]}{rng.integers(1_000_000, 9_999_999)}"
        if c not in taken:
            taken.add(c)
            out.append(c)
    return out


def _booking_times(params, rng, cutoff: float, n: int) -> np.ndarray:
    bp = params["booking"]
    rush = rng.random(n) < bp["rush_share"]
    hrs = np.where(rush, rng.uniform(0.05, bp["rush_hours"], n), bp["window_hours"] * rng.beta(1.0, bp["tail_beta"], n))
    t = np.floor((cutoff - hrs * 60) / 10) * 10
    # 시간대별 예약 용량 상한: 넘치면 앞 시간대로 밀어냄
    t = np.sort(t)[::-1]
    count: dict[int, int] = {}
    out = []
    for x in t:
        h = int(x // 60)
        while count.get(h, 0) >= bp["hourly_cap"]:
            h -= 1
            x = h * 60 + (x % 60)
        count[h] = count.get(h, 0) + 1
        out.append(x)
    return np.array(out)


def generate_world(params: dict, scenario: dict | None, eta_model, seed: int | None = None, start: float | None = None,
                   occupancy: float | None = None, err_scale: float = 1.0, urgent: bool = False,
                   cap_vessels: set | None = None) -> World:
    scenario = scenario or {}
    seed = scenario.get("seed", params["seed"]) if seed is None else seed
    rng = np.random.default_rng(seed)
    start = parse_hhmm(scenario.get("start", "13:30")) if start is None else start
    occ = occupancy if occupancy is not None else scenario.get("occupancy", params["yard"]["occupancy"])
    cmap = carriers(params)
    cs_ids = [c["id"] for c in params["carriers"] if c["share"] > 0]
    share = np.array([cmap[c]["share"] for c in cs_ids])
    share = share / share.sum()
    yp = params["yard"]
    n_groups = params["workplan"]["groups"]

    # 선박 일정 (Port-i 모의)
    vessels, cutoffs = {}, {}
    for v in params["vessels"]:
        ov = scenario.get("vessel_overrides", {}).get(v["vessel_id"], {})
        bs = parse_hhmm(ov.get("berth_start", v["berth_start"]))
        be = parse_hhmm(ov.get("berth_end", v["berth_end"]))
        vessels[v["vessel_id"]] = Vessel(v["vessel_id"], v["name"], v["voyage"], bs, be, start - 25)
        cutoffs[v["vessel_id"]] = bs - v["cutoff_lead_min"]

    taken = {c["cntr"] for c in scenario.get("containers", [])}
    bookings, arrivals, vessel_of, carrier_of, groups, seqs = {}, {}, {}, {}, {}, {}
    actual_override = {}
    for v in params["vessels"]:
        vid = v["vessel_id"]
        n = int(rng.integers(v["n_min"], v["n_max"] + 1))
        nums = _cntr_numbers(rng, n, taken)
        bt = _booking_times(params, rng, cutoffs[vid], n)
        g = np.repeat(np.arange(1, n_groups + 1), math.ceil(n / n_groups))[:n]
        rng.shuffle(g)
        car = rng.choice(cs_ids, size=n, p=share)
        for c, b, gg, cc in zip(nums, bt, g, car):
            bookings[c] = float(b)
            vessel_of[c] = vid
            groups[c] = int(gg)
            carrier_of[c] = str(cc)
    for sc in scenario.get("containers", []):
        c = sc["cntr"]
        bookings[c] = parse_hhmm(sc["booking"])
        vessel_of[c] = sc["vessel"]
        groups[c] = int(sc["group"])
        carrier_of[c] = sc["carrier"]
        if "actual" in sc:
            actual_override[c] = parse_hhmm(sc["actual"])
    # 같은 그룹 안의 계획상 순번
    for vid in vessels:
        for gg in range(1, n_groups + 1):
            members = sorted([c for c in groups if vessel_of[c] == vid and groups[c] == gg])
            order = rng.permutation(len(members))
            for c, o in zip(members, order):
                seqs[c] = int(o)

    # 실제 도착 · 구간 통과 · 예약 정보
    n_cp = len(params["arrival"]["checkpoints"])
    cp_names = [c["name"] for c in params["arrival"]["checkpoints"]]
    book_objs = {}
    a = params["arrival"]
    for i, c in enumerate(sorted(bookings)):
        cr = cmap[carrier_of[c]]
        b = bookings[c]
        dev = cr["mean"] + congestion(params, b) + err_scale * cr["sd"] * rng.standard_normal()
        arr = actual_override.get(c, b + dev)
        legs = _legs(params, rng, err_scale)
        depart = arr - legs.sum()
        cps = depart + np.concatenate([[0], np.cumsum(legs)[:-1]])
        book_objs[c] = Booking(c, f"T{rng.integers(1000, 9999)}", carrier_of[c], "부산신항 모의 터미널", b, float(depart),
                               [(cp_names[k], float(cps[k])) for k in range(n_cp)])
        cy = arr + max(3.0, rng.normal(a["gate_to_cy_mean_min"], a["gate_to_cy_sd_min"]))
        arrivals[c] = Arrival(c, float(arr), float(cy), float(cy + a["handling_min"]))

    # ETA 발행 이력 (시나리오 고정 컨테이너는 예약 기반 첫 발행만 만들고 이후는 사건으로 받음)
    scripted = set(actual_override) | {sc["cntr"] for sc in scenario.get("containers", [])}
    lead = params["booking"]["initial_issue_lead_min"]
    recs = []
    for c, bo in book_objs.items():
        recs.append({"cntr": c, "carrier": carrier_of[c], "booking": bo.booking_time, "dow": 0, "stage": -1,
                     "issued_at": bo.booking_time - lead})
        if c in scripted:
            continue
        for k, (nm, t) in enumerate(bo.checkpoints):
            recs.append({"cntr": c, "carrier": carrier_of[c], "booking": bo.booking_time, "dow": 0, "stage": k, "issued_at": t})
    rows = pd.DataFrame(recs)
    rows["since_booking"] = rows["issued_at"] - rows["booking"]
    pred = eta_model.predict(rows)
    model_name = getattr(eta_model, "name", "model")
    eta_issues: dict[str, list[EtaIssue]] = {}
    for r, p in zip(rows.itertuples(index=False), pred):
        st = int(r.stage)
        inputs = {"예약시각": r.booking, "운송사": r.carrier,
                  "마지막 통과": "출발 전(예약 기반)" if st < 0 else cp_names[st],
                  "통과 시각": None if st < 0 else r.issued_at}
        eta_issues.setdefault(r.cntr, []).append(EtaIssue(r.cntr, float(r.issued_at), inputs, float(p[0]), float(p[1]),
                                                          float(p[2]), model_name))

    # 야드: 다음 항차 선반입(필러)을 선박 구역별로 깔고 → 시작 전 도착분을 기준 정책으로 적치
    yard = Yard.empty(yp["block"], yp["bays"], yp["rows"], yp["max_tier"], yp["vessel_bays"])
    arrived = sorted([c for c in arrivals if arrivals[c].gate_in < start], key=lambda c: arrivals[c].gate_in)
    reserve_rows = {b: int(rng.integers(yp["rows"])) for b in range(yp["bays"])}
    fillers = set()
    for vid, bays in yp["vessel_bays"].items():
        area_cap = len(bays) * yp["rows"] * yp["max_tier"]
        n_arr = sum(1 for c in arrived if vessel_of[c] == vid)
        n_fill = max(0, int(round(occ * area_cap)) - n_arr)
        in_window = vessels[vid].berth_window_start <= start + 6 * 60 if cap_vessels is None else vid in cap_vessels
        if not scenario.get("layout") and occ < 0.85 and in_window:
            # 시연 범위 안에서 작업하는 선박은 오늘 들어올 컨테이너까지 받을 수 있도록 선반입 수를 제한(여유 스택 규칙 기준 용량)
            usable = len(bays) * (yp["rows"] - 1) * yp["max_tier"]
            n_total = sum(1 for c in vessel_of if vessel_of[c] == vid)
            n_fill = max(0, min(n_fill, usable - n_total))
        names = _cntr_numbers(rng, n_fill, taken | set(bookings) | fillers)
        open_stacks = [(b, r) for b in bays for r in range(yp["rows"]) if r != reserve_rows[b]]
        all_stacks = [(b, r) for b in bays for r in range(yp["rows"])]
        cap = min(yp["max_tier"], max(yp["filler_max_height"], math.ceil(n_fill / max(1, len(open_stacks)))))
        for nm in names:
            pool = [s for s in open_stacks if len(yard.stacks[s]) < cap]
            if not pool:
                pool = [s for s in all_stacks if len(yard.stacks[s]) < yp["max_tier"]]
            if not pool:
                break
            s = pool[int(rng.integers(len(pool)))]
            yard.stacks[s].append(nm)
            fillers.add(nm)
    overflow = set()
    # 인출 그룹은 작업계획 v2(선박 작업 시작 10시간 전)에 배정됨. 그 전에 들어온 컨테이너는 그룹을 모른 채 선박 구역에 쌓였음
    lead = params["workplan"]["group_assign_before_berth_min"]
    for c in arrived:
        if arrivals[c].gate_in < vessels[vessel_of[c]].berth_window_start - lead:
            cands = eligible_stacks(yard, vessel_of[c], c) or relaxed_stacks(yard, vessel_of[c], c)
            s = cands[int(rng.integers(len(cands)))] if cands else None
        else:
            s = baseline_choose(yard, vessel_of[c], c, groups[c], groups, relaxed=True)
        if s is None:
            overflow.add(c)
        else:
            yard.stacks[s].append(c)
    # 시나리오가 구역 단면(스택 높이)을 고정하면 그대로 맞춤(공간 부족 시연용).
    # 높으면 위 컨테이너를 다른 블록 장치(overflow)로 돌리고, 낮으면 바닥에 선반입 컨테이너를 채움.
    for vid, lay in (scenario.get("layout") or {}).items():
        for bi, b in enumerate(yp["vessel_bays"][vid]):
            for r, hgt in enumerate(lay[bi]):
                st = yard.stacks[(b, r)]
                while len(st) > hgt:
                    c = st.pop()
                    if c in fillers:
                        fillers.discard(c)
                    else:
                        overflow.add(c)
                while len(st) < hgt:
                    nm = _cntr_numbers(rng, 1, taken | set(bookings) | fillers)[0]
                    st.insert(0, nm)
                    fillers.add(nm)

    # 크레인 작업량 (15분 슬롯): 기타 작업 + 게이트 반입 + 선적 인출
    cp = params["crane"]
    slot = cp["slot_min"]
    load: dict[int, int] = {}
    for k in range(int(start // slot) - 8, int(32 * 60 // slot)):
        load[k] = int(rng.poisson(cp["housekeeping_rate"]))
    for c, av in arrivals.items():
        k = int(av.gate_in // slot)
        load[k] = load.get(k, 0) + 1
    if urgent:
        ep = params["experiment"]
        for k in list(load):
            if rng.random() < ep["urgent_slot_share"]:
                load[k] += ep["urgent_extra_load"]
    for vid, v in vessels.items():
        n = sum(1 for c in vessel_of if vessel_of[c] == vid)
        k0 = int(v.berth_window_start // slot)
        for k in range(k0, k0 + math.ceil(n / cp["retrieval_per_slot"])):
            load[k] = load.get(k, 0) + cp["retrieval_per_slot"]

    plans = {}
    ver = params["workplan"]["initial_version"]
    for vid, v in vessels.items():
        members = [c for c in groups if vessel_of[c] == vid]
        cs = [(k * slot, (k + 1) * slot, load.get(k, 0)) for k in sorted(load)]
        plans[vid] = WorkPlan(ver, vid, {c: groups[c] for c in members}, {c: seqs[c] for c in members}, cs,
                              cutoffs[vid], start - 90)
    return World(params, seed, start, yard, vessels, plans, book_objs, arrivals, vessel_of, carrier_of, eta_issues, load,
                 [scenario["focus_cntr"]] if scenario.get("focus_cntr") else [], scripted, overflow, fillers, err_scale)


def idle_windows(world: World, t0: float, t1: float, used: dict | None = None) -> list[tuple[float, int]]:
    """[t0, t1) 안의 유휴 15분 슬롯과 그 슬롯에서 쓸 수 있는 선작업 횟수."""
    cp = world.params["crane"]
    slot = cp["slot_min"]
    thr = cp["cap_per_slot"] * cp["idle_ratio"]
    out = []
    for k in range(int(math.ceil(t0 / slot)), int(t1 // slot)):
        ld = world.crane_load.get(k, 0) + (used or {}).get(k, 0)
        if ld < thr:
            out.append((k * slot, int(math.ceil(thr - ld))))
    return out
