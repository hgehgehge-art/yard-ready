"""사건 재생, 계획 버전, 추천 만료와 예외 (기획서 7.8)."""
from __future__ import annotations

import copy
import time

import numpy as np

from core.audit import AuditLog
from core.constraints import slot_reasons
from core.generator import World, generate_world, idle_windows
from core.models import INF_GROUP, Decision, EtaIssue, Event, Move, Recommendation, hhmm, parse_hhmm
from core.policies import baseline_choose
from core.recommender import Context, recommend, representative_yard
from core.relocation import lower_bound, retrieve_area

PRIORITY = {("지연", "상위"): 0, ("조기", "하위"): 1, ("지연", "하위"): 2, ("조기", "상위"): 3}
OPEN_STATUSES = ("추천됨", "보류")


def classify(params: dict, booking: float, p50: float, group: int) -> tuple[str, str]:
    th = params["classification"]["delay_threshold_min"]
    d = p50 - booking
    arr = "지연" if d > th else ("조기" if d < -th else "정시")
    ship = "상위" if group in params["classification"]["upper_groups"] else "하위"
    return arr, ship


class Engine:
    def __init__(self, params: dict, scenarios: dict, key: str, model):
        self.params = params
        self.key = key
        self.scen = scenarios[key]
        self.model = model
        self.world: World = generate_world(params, self.scen, model)
        self.now = self.world.start
        self.events = sorted([Event(parse_hhmm(e["t"]), e["type"], {k: v for k, v in e.items() if k not in ("t", "type")})
                              for e in self.scen["events"]], key=lambda e: e.t)
        self.idx = 0
        v0 = params["workplan"]["initial_version"]
        self.plan_log = []
        for vid, v in self.world.vessels.items():
            n = len(self.world.plans[vid].retrieval_groups)
            base = self.world.start
            self.plan_log += [
                {"vessel": vid, "version": 1, "t": base - 26 * 60, "change": "선석 배정 초안 수신(Port-i 모의)", "affected": n},
                {"vessel": vid, "version": 2, "t": v.berth_window_start - params["workplan"]["group_assign_before_berth_min"],
                 "change": "인출 그룹 1~4 배정(TOS 모의)", "affected": n},
                {"vessel": vid, "version": v0, "t": base - 90, "change": "같은 그룹 안 인출 순번 확정", "affected": max(1, n // 6)},
            ]
        self.plan_version = {vid: v0 for vid in self.world.vessels}
        self.retrieval_start = {vid: v.berth_window_start for vid, v in self.world.vessels.items()}
        self.recs: dict[str, Recommendation] = {}
        self.rec_order: list[str] = []
        self.decisions: list[Decision] = []
        self.moves: list[Move] = []
        self.feed: list[dict] = []
        self.audit = AuditLog()
        self.pm_queue: list[dict] = []
        self.pm_used: dict[int, int] = {}
        self.retrieved: dict = {}
        self.actual: dict[str, dict] = {}
        self.exceptions: dict[str, str] = {}
        self.arrived_scripted: set = set()
        self.last_alert = None
        self.eta_override: dict[str, list[EtaIssue]] = {}

    # ── 스냅샷(이전 사건으로 되돌리기) ───────────────────
    def snapshot(self) -> "Engine":
        memo = {id(self.model): self.model, id(self.params): self.params}
        return copy.deepcopy(self, memo)

    # ── 조회 ────────────────────────────────────────────
    @property
    def done(self) -> bool:
        return self.idx >= len(self.events)

    @property
    def next_event(self) -> Event | None:
        return None if self.done else self.events[self.idx]

    def vessel_of(self, c: str) -> str:
        return self.world.vessel_of[c]

    def group(self, c: str) -> int:
        return self.world.plans[self.vessel_of(c)].retrieval_groups.get(c, INF_GROUP)

    def cutoff(self, vid: str) -> float:
        return self.world.plans[vid].cutoff

    def placed(self) -> set:
        return {c for st in self.world.yard.stacks.values() for c in st}

    def is_pending(self, c: str) -> bool:
        return c not in self.placed() and c not in self.world.overflow and c in self.world.arrivals and not self.has_arrived(c)

    def has_arrived(self, c: str) -> bool:
        if c in self.world.scripted:
            return c in self.arrived_scripted
        return self.world.arrivals[c].gate_in <= self.now

    def eta_history(self, c: str) -> list[EtaIssue]:
        out = [e for e in self.world.eta_issues.get(c, []) if e.issued_at <= self.now]
        out += [e for e in self.eta_override.get(c, []) if e.issued_at <= self.now]
        return sorted(out, key=lambda e: e.issued_at)

    def latest_eta(self, c: str) -> EtaIssue | None:
        h = self.eta_history(c)
        return h[-1] if h else None

    def provisional_eta(self, c: str) -> EtaIssue:
        """아직 ETA가 발행되지 않은 예약: 지금 시각에 예약 정보만으로 만든 값."""
        b = self.world.bookings[c]
        if hasattr(self.model, "predict_one"):
            p = self.model.predict_one(b.carrier_id, b.booking_time, -1, self.now)
        else:
            import pandas as pd
            rows = pd.DataFrame([{"carrier": b.carrier_id, "booking": b.booking_time, "dow": 0, "stage": -1,
                                  "issued_at": self.now, "since_booking": self.now - b.booking_time}])
            p = tuple(self.model.predict(rows)[0])
        return EtaIssue(c, self.now, {"예약시각": b.booking_time, "운송사": b.carrier_id, "마지막 통과": "출발 전(예약 기반)"},
                        p[0], p[1], p[2], "예약 기반")

    def pending(self, vid: str | None = None) -> list[str]:
        placed = self.placed()
        return [c for c in self.world.arrivals if (vid is None or self.vessel_of(c) == vid) and c not in placed
                and c not in self.world.overflow and not self.has_arrived(c)]

    def windows(self) -> list[tuple[float, int]]:
        return idle_windows(self.world, self.now, max(self.retrieval_start.values()), self.pm_used)

    def reservations(self) -> dict:
        return {c: s for s, c in self.world.yard.reserved.items()}

    # ── 검토 목록 ───────────────────────────────────────
    def review_list(self) -> list[dict]:
        rv = self.params["review"]
        hold = self.params["recommender"]["hold"]
        out = []
        for c in self.pending():
            b = self.world.bookings[c]
            if not (self.now - 60 <= b.booking_time <= self.now + rv["horizon_min"]):
                continue
            e = self.latest_eta(c)
            if e is None:
                continue
            dev = e.eta_p50 - b.booking_time
            stale = self.now - e.issued_at
            vid = self.vessel_of(c)
            over = e.eta_p90 > self.cutoff(vid)
            flagged = abs(dev) > rv["deviation_min"] or e.width > hold["max_width_min"] or over or c in self.world.focus
            if not flagged:
                continue
            g = self.group(c)
            arr, ship = classify(self.params, b.booking_time, e.eta_p50, g)
            rec = self.rec_for(c)
            exp = rec.options[0].total_mean if rec else abs(dev) / 10
            pr = PRIORITY.get((arr, ship), 4)
            out.append({"cntr": c, "vessel": vid, "group": g, "booking": b.booking_time, "eta": e, "dev": dev,
                        "width": e.width, "stale": stale, "arr": arr, "ship": ship, "priority": pr, "expected": exp,
                        "over_cutoff": over, "rec": rec, "carrier": b.carrier_id})
        out.sort(key=lambda r: (r["priority"], -r["expected"], r["booking"]))
        return out

    # ── 추천 ────────────────────────────────────────────
    def context(self, c: str) -> Context:
        vid = self.vessel_of(c)
        e = self.latest_eta(c) or self.provisional_eta(c)
        pend = {}
        for o in self.pending(vid):
            if o == c:
                continue
            pend[o] = self.latest_eta(o) or self.provisional_eta(o)
        res = {k: v for k, v in self.reservations().items() if k != c}
        y = self.world.yard.copy()
        y.reserved = {s: o for s, o in y.reserved.items() if o != c}
        return Context(yard=y, vessel_id=vid, groups=self.world.groups, seqs=self.world.seqs, target=c, target_eta=e,
                       pending=pend, now=self.now, cutoff=self.cutoff(vid), retrieval_start=self.retrieval_start[vid],
                       windows=self.windows(), plan_version=self.plan_version[vid], reservations=res)

    def rec_for(self, c: str) -> Recommendation | None:
        for rid in reversed(self.rec_order):
            if self.recs[rid].cntr_no == c:
                return self.recs[rid]
        return None

    def create_rec(self, c: str) -> Recommendation:
        ctx = self.context(c)
        rid = f"R-{c[-4:]}-{int(round(self.now)):04d}-v{ctx.plan_version}"
        if rid in self.recs:
            rid += f"-{sum(1 for r in self.recs if r.startswith(rid)) + 1}"
        rec = recommend(ctx, self.params, rid)
        if rec.exception:
            self.exceptions[c] = rec.exception
        self.recs[rid] = rec
        self.rec_order.append(rid)
        self.audit.add(self.now, "추천 생성", rid, c, rec.plan_version,
                       f"{rec.status} · 추천 {rec.recommended}" + (f" · {rec.hold_reason}" if rec.hold_reason else ""))
        return rec

    def projected_yard(self, rec: Recommendation, key: str):
        """대안을 적용하고 모두 ETA 중앙값에 도착한다고 볼 때의 인출 직전 적층."""
        ctx = self.context(rec.cntr_no)
        opt = next(o for o in rec.options if o.key == key)
        y0 = ctx.yard
        if opt.premarshal:
            y0 = y0.copy()
            for m in opt.premarshal:
                y0.stacks[tuple(m["src"])].pop()
                y0.stacks[tuple(m["dst"])].append(m["cntr"])
        return representative_yard(ctx, y0, opt.stack, self.params["workplan"]["same_group_swap"])

    def expiry_reason(self, rec: Recommendation) -> str:
        vid = rec.vessel_id
        if self.plan_version[vid] != rec.plan_version:
            return (f"작업계획이 v{self.plan_version[vid]}로 바뀌어 이 추천은 더 이상 유효하지 않습니다. 다시 계산해 주세요.")
        e = self.latest_eta(rec.cntr_no)
        if e is not None and e.issued_at > rec.data_as_of + 1e-9:
            return (f"{hhmm(e.issued_at)}에 ETA가 새로 발행되어 이 추천은 더 이상 유효하지 않습니다. 다시 계산해 주세요.")
        if self.has_arrived(rec.cntr_no):
            return "컨테이너가 이미 게이트를 통과했습니다."
        return ""

    def _expire_open(self):
        for rid in self.rec_order:
            r = self.recs[rid]
            if r.status in OPEN_STATUSES:
                why = self.expiry_reason(r)
                if why:
                    r.status = "만료"
                    r.note = why
                    self.audit.add(self.now, "만료", rid, r.cntr_no, r.plan_version, why)

    def decide(self, rec_id: str, decision: str, option_key: str | None, reason_code: str, memo: str = "",
               opened_at: float | None = None) -> tuple[bool, str]:
        rec = self.recs[rec_id]
        why = self.expiry_reason(rec)
        if why:
            if rec.status in OPEN_STATUSES:
                rec.status = "만료"
                rec.note = why
            self.audit.add(self.now, "승인 차단", rec_id, rec.cntr_no, rec.plan_version, why)
            return False, why
        if rec.status not in OPEN_STATUSES:
            return False, f"이미 '{rec.status}' 상태인 추천입니다."
        if decision == "승인":
            chosen = rec.recommended
        elif decision == "다른 대안":
            chosen = option_key or "A"
        else:
            chosen = "A"
        opt = next(o for o in rec.options if o.key == chosen)
        self.decisions.append(Decision(rec_id, chosen, decision, reason_code, memo, self.now, opened_at))
        # 예약·선작업 반영
        y = self.world.yard
        y.reserved = {s: c for s, c in y.reserved.items() if c != rec.cntr_no}
        if decision != "보류" and opt.stack is not None:
            y.reserved[tuple(opt.stack)] = rec.cntr_no
        if decision != "보류" and opt.premarshal:
            for m in opt.premarshal:
                self.pm_queue.append({**m, "rec_id": rec_id})
                k = int(m["t"] // self.params["crane"]["slot_min"])
                self.pm_used[k] = self.pm_used.get(k, 0) + 1
        if decision == "승인":
            rec.status = "승인"
        elif decision == "다른 대안":
            rec.status = "다른 대안 선택"
        else:
            rec.status = "보류"
            rec.hold_reason = rec.hold_reason or "현장 관리자가 보류했습니다. 현재 규칙대로 장치합니다."
            rec.recommended = "A"
        if rec.exception and decision != "보류" and not rec.exception_cleared:
            rec.status = "예외 검토"
        self.audit.add(self.now, {"승인": "승인", "다른 대안": "다른 대안 선택", "보류": "보류"}[decision], rec_id,
                       rec.cntr_no, rec.plan_version, f"대안 {chosen} · {reason_code}" + (f" · {memo}" if memo else ""))
        return True, ""

    def decision_for(self, rec_id: str) -> Decision | None:
        for d in reversed(self.decisions):
            if d.rec_id == rec_id:
                return d
        return None

    def clear_exception(self, rec_id: str):
        rec = self.recs[rec_id]
        rec.exception_cleared = True
        d = self.decision_for(rec_id)
        if rec.status == "예외 검토":
            rec.status = "실행 완료" if self._executed(rec.cntr_no) else ("승인" if d and d.decision == "승인" else "다른 대안 선택")
        self.audit.add(self.now, "예외 검토 완료", rec_id, rec.cntr_no, rec.plan_version, "관리자가 반입 마감 예외를 확인했습니다.")

    def _executed(self, c: str) -> bool:
        return any(m.cntr_no == c and m.kind == "최초 적치" for m in self.moves)

    # ── 사건 진행 ───────────────────────────────────────
    def _log(self, t, kind, text, sub="", level="info"):
        self.feed.append({"t": t, "kind": kind, "text": text, "sub": sub, "level": level})

    def _place(self, c: str, t: float, kind_note: str = ""):
        y = self.world.yard
        vid = self.vessel_of(c)
        g = self.group(c)
        target = next((s for s, o in y.reserved.items() if o == c), None)
        note = "기준 정책"
        s = None
        if target is not None:
            if not slot_reasons(y, vid, target, c):
                s = target
                note = "승인 슬롯"
            else:
                note = "승인 슬롯 사용 불가 → 기준 정책"
            y.reserved.pop(target, None)
        if s is None:
            s = baseline_choose(y, vid, c, g, self.world.groups)
        if s is None:
            self.world.overflow.add(c)
            self.moves.append(Move(c, "최초 적치", "게이트", "다른 블록(구역 만석)", t, t))
            return None, "다른 블록"
        name = y.next_slot_name(s)
        y.stacks[s].append(c)
        cy = t + (self.world.arrivals[c].cy_ready - self.world.arrivals[c].gate_in)
        self.moves.append(Move(c, "최초 적치", "게이트", name, cy, cy + self.params["crane"]["move_min"]))
        rec = self.rec_for(c)
        if rec and rec.status in ("승인", "다른 대안 선택", "예외 검토", "보류"):
            if rec.status != "예외 검토" and rec.status != "보류":
                rec.status = "실행 완료"
            self.audit.add(t, "실행", rec.rec_id, c, rec.plan_version, f"{name} 적치 · {note}")
        return s, note

    def _run_pm(self, upto: float):
        y = self.world.yard
        keep = []
        for m in sorted(self.pm_queue, key=lambda m: m["t"]):
            if m["t"] > upto:
                keep.append(m)
                continue
            src, dst = tuple(m["src"]), tuple(m["dst"])
            vid = self.vessel_of(m["cntr"])
            if y.stacks[src] and y.stacks[src][-1] == m["cntr"] and not slot_reasons(y, vid, dst, m["cntr"]):
                frm = y.slot_name(src[0], src[1], len(y.stacks[src]) - 1)
                y.stacks[src].pop()
                to = y.next_slot_name(dst)
                y.stacks[dst].append(m["cntr"])
                self.moves.append(Move(m["cntr"], "선작업", frm, to, m["t"], m["t"] + self.params["crane"]["move_min"]))
                self._log(m["t"], "premarshal", f"선작업 · {m['cntr']}", f"{frm} → {to} (유휴 시간대)")
                self.actual.setdefault(m["rec_id"], {"pm": 0})["pm"] = self.actual.get(m["rec_id"], {}).get("pm", 0) + 1
            else:
                self._log(m["t"], "premarshal", f"선작업 취소 · {m['cntr']}", "야드 상태가 바뀌어 계획한 이동을 할 수 없습니다.", "warn")
        self.pm_queue = keep

    def advance_to(self, t: float):
        bg = sorted([c for c in self.world.arrivals if c not in self.world.scripted and c not in self.placed()
                     and c not in self.world.overflow and self.now < self.world.arrivals[c].gate_in <= t],
                    key=lambda c: self.world.arrivals[c].gate_in)
        # 선작업과 반입을 시각순으로 섞어 처리
        for c in bg:
            ta = self.world.arrivals[c].gate_in
            self._run_pm(ta)
            self._place(c, ta)
        self._run_pm(t)
        if bg:
            self._log(t, "gate", f"게이트 반입 {len(bg)}건", f"{hhmm(self.now)}~{hhmm(t)} · 승인 슬롯이 없는 컨테이너는 기준 정책으로 장치")
        self.now = t

    def step(self) -> Event | None:
        if self.done:
            return None
        ev = self.events[self.idx]
        self.advance_to(ev.t)
        d = ev.data
        if ev.type == "eta_update":
            c = d["cntr"]
            b = self.world.bookings[c]
            e = EtaIssue(c, ev.t, {"예약시각": b.booking_time, "운송사": b.carrier_id, "마지막 통과": d.get("note", "")},
                         parse_hhmm(d["p10"]), parse_hhmm(d["p50"]), parse_hhmm(d["p90"]), "시나리오 고정")
            self.eta_override.setdefault(c, []).append(e)
            txt = f"ETA 갱신 · {c} · {hhmm(b.booking_time)} → {hhmm(e.eta_p10)}~{hhmm(e.eta_p90)}"
            self._log(ev.t, "eta", txt, d.get("note", ""), "warn" if e.eta_p50 - b.booking_time > 15 else "info")
            self.last_alert = txt
        elif ev.type == "gate_in":
            c = d["cntr"]
            self.arrived_scripted.add(c)
            self.world.arrivals[c].gate_in = ev.t
            s, note = self._place(c, ev.t)
            where = self.world.yard.slot_name(s[0], s[1], len(self.world.yard.stacks[s]) - 1) if s else "다른 블록"
            txt = f"게이트 도착 · {c} · ATA {hhmm(ev.t)}"
            self._log(ev.t, "gate_in", txt, f"{where} 장치 ({note})")
            self.last_alert = txt
        elif ev.type == "plan_change":
            vid = d["vessel"]
            shift = float(d.get("berth_shift_min", 0))
            old = self.plan_version[vid]
            new = int(d.get("new_version", old + 1))
            self.plan_version[vid] = new
            v = self.world.vessels[vid]
            p = self.world.plans[vid]
            # 크레인 작업량에서 인출 작업을 옮김
            cp = self.params["crane"]
            slot = cp["slot_min"]
            n = len(p.retrieval_groups)
            k_old = int(v.berth_window_start // slot)
            nk = int(np.ceil(n / cp["retrieval_per_slot"]))
            for k in range(k_old, k_old + nk):
                self.world.crane_load[k] = self.world.crane_load.get(k, 0) - cp["retrieval_per_slot"]
            v.berth_window_start += shift
            v.berth_window_end += shift
            v.updated_at = ev.t
            k_new = int(v.berth_window_start // slot)
            for k in range(k_new, k_new + nk):
                self.world.crane_load[k] = self.world.crane_load.get(k, 0) + cp["retrieval_per_slot"]
            p.cutoff += shift
            p.plan_version = new
            p.issued_at = ev.t
            self.retrieval_start[vid] = v.berth_window_start
            over = []
            for c in self.pending(vid):
                e = self.latest_eta(c) or self.provisional_eta(c)
                if e.eta_p90 > p.cutoff:
                    over.append(c)
                    self.exceptions[c] = f"작업계획 v{new} 반입 마감 {hhmm(p.cutoff)} 초과 가능"
            self.plan_log.append({"vessel": vid, "version": new, "t": ev.t,
                                  "change": f"선박 작업 시작 {'+' if shift > 0 else '−'}{abs(shift):.0f}분 ({hhmm(v.berth_window_start)} 시작, 마감 {hhmm(p.cutoff)})",
                                  "affected": len(over)})
            txt = f"작업계획 v{old} → v{new} · {vid} 작업 시작 {abs(shift):.0f}분 {'당겨짐' if shift < 0 else '늦춰짐'}"
            self._log(ev.t, "plan", txt, f"반입 마감 초과 가능 {len(over)}건은 예외 검토 요청", "danger")
            self.last_alert = txt
        elif ev.type == "urgent_job":
            k = int(ev.t // self.params["crane"]["slot_min"])
            self.world.crane_load[k] = self.world.crane_load.get(k, 0) + int(d.get("load", 3))
            self._log(ev.t, "urgent", "긴급 작업 유입", f"{hhmm(ev.t)} 슬롯 작업 +{d.get('load', 3)}건", "warn")
        elif ev.type == "retrieval_start":
            vid = d["vessel"]
            late = [c for c in self.pending(vid)]
            for c in sorted(late, key=lambda c: self.world.arrivals[c].gate_in):
                if c in self.world.scripted:
                    self.arrived_scripted.add(c)
                self._place(c, self.world.arrivals[c].gate_in)
            y = self.world.yard
            groups = self.world.groups
            from core.relocation import area_stacks
            lb = lower_bound(area_stacks(y, vid).values(), groups, set(self.world.plans[vid].retrieval_groups))
            res = retrieve_area(y, vid, groups, self.world.seqs, self.params["workplan"]["same_group_swap"])
            mv = self.params["crane"]["move_min"]
            t = ev.t
            for c, frm, to, kind in res.moves:
                fname = "임시 장소" if frm == ("buffer",) else y.slot_name(frm[0], frm[1], 0)[:-2]
                tname = "본선" if to is None else ("임시 장소" if to == ("buffer",) else y.slot_name(to[0], to[1], 0)[:-2])
                self.moves.append(Move(c, kind, fname, tname, t, t + mv))
                t += mv
            n = len(res.order)
            pm_done = sum(1 for m in self.moves if m.kind == "선작업" and self.vessel_of(m.cntr_no) == vid)
            self.retrieved[vid] = {"relocations": res.relocations, "n": n, "lb": lb, "pm": pm_done, "t": ev.t,
                                   "late": len(late)}
            for rid in self.rec_order:
                r = self.recs[rid]
                if r.vessel_id == vid:
                    a = self.actual.setdefault(rid, {"pm": 0})
                    a["reloc"] = res.relocations
                    a["total"] = res.relocations + pm_done
            txt = f"인출 시작 · {vid} · {n}개"
            self._log(ev.t, "retrieval", txt, f"작업 중 재취급 {res.relocations}회 · 선작업 {pm_done}회 · 하한 {lb}회"
                      + (f" · 마감 후 도착 {len(late)}건 포함" if late else ""))
            self.last_alert = txt
        self.idx += 1
        self._expire_open()
        return ev

    # ── 지표 ────────────────────────────────────────────
    def expected_area(self, vid: str, use_reservations: bool = True) -> dict:
        """모두 ETA 중앙값에 도착하고 나머지를 기준 정책으로 쌓을 때의 구역 재취급(관제 KPI용)."""
        y = self.world.yard.copy()
        if not use_reservations:
            y.reserved = {}
        pend = sorted(self.pending(vid), key=lambda c: (self.latest_eta(c) or self.provisional_eta(c)).eta_p50)
        for c in pend:
            tgt = next((s for s, o in y.reserved.items() if o == c), None)
            s = tgt if tgt is not None and not slot_reasons(y, vid, tgt, c) else baseline_choose(y, vid, c, self.group(c), self.world.groups)
            if s is not None:
                y.stacks[s].append(c)
                y.reserved.pop(s, None)
        n = sum(1 for c in self.world.plans[vid].retrieval_groups if c not in self.world.overflow)
        r = retrieve_area(y, vid, self.world.groups, self.world.seqs, self.params["workplan"]["same_group_swap"])
        return {"reloc": r.relocations, "n": n, "per100": r.relocations / max(1, n) * 100}
