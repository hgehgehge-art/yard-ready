"""데이터 모델. 실데이터가 생기면 같은 필드의 CSV로 바꿔 끼울 수 있도록 필드를 고정합니다.

시각은 모두 '시연일 00:00 기준 분(float)'으로 다룹니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

INF_GROUP = 99  # 이번 작업계획에서 인출하지 않는 컨테이너(다음 항차 선반입 등)


# ── 시각 ─────────────────────────────────────────────
def parse_hhmm(s) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    h, m = str(s).strip().split(":")
    return int(h) * 60 + int(m)


def hhmm(m: float | None) -> str:
    if m is None:
        return "—"
    m = int(round(m))
    d, r = divmod(m, 1440)
    s = f"{r // 60:02d}:{r % 60:02d}"
    if d == 0:
        return s
    return ("익일 " if d > 0 else "전일 ") + s


# ── 기획서 2.4 데이터 6종 + 추천·판단·실행 ─────────────────
@dataclass
class Booking:
    """예약·운송 진행 (올컨e 기존 정보)."""
    cntr_no: str
    truck_id: str
    carrier_id: str
    terminal: str
    booking_time: float
    depart_time: float
    checkpoints: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class EtaIssue:
    """도착 예측 발행 이력. inputs에는 issued_at 시점에 알 수 있던 값만 넣습니다."""
    cntr_no: str
    issued_at: float
    inputs: dict
    eta_p10: float
    eta_p50: float
    eta_p90: float
    model: str

    @property
    def width(self) -> float:
        return self.eta_p90 - self.eta_p10


@dataclass
class Arrival:
    """실제 도착 이력. 추천 계산에 들어가면 안 됩니다."""
    cntr_no: str
    gate_in: float          # ATA
    cy_ready: float         # CY 하차 가능 시각
    gate_out: float


@dataclass
class Slot:
    """야드 현황의 한 칸."""
    block: str
    bay: int
    row: int
    tier: int
    cntr_no: str | None = None
    reserved_for: str | None = None


@dataclass
class Vessel:
    vessel_id: str
    name: str
    voyage: str
    berth_window_start: float
    berth_window_end: float
    updated_at: float
    source: str = "Port-i(모의)"


@dataclass
class WorkPlan:
    """개별 작업계획 (TOS 모의)."""
    plan_version: int
    vessel_id: str
    retrieval_groups: dict[str, int]
    retrieval_seq: dict[str, int]
    crane_slots: list[tuple[float, float, int]]
    cutoff: float
    issued_at: float


@dataclass
class Option:
    key: str                         # A, B1, B2, C
    label: str
    stack: tuple[int, int] | None    # None = 기준 정책이 도착 시점에 고름
    slot_name: str
    premarshal: list[dict] = field(default_factory=list)
    idle_window: str = ""
    pm_mean: float = 0.0
    reloc_mean: float = 0.0
    total_mean: float = 0.0
    total_p10: float = 0.0
    total_p90: float = 0.0
    diff_mean: float = 0.0           # 유지안 대비 차이(음수 = 감소)
    diff_p10: float = 0.0
    diff_p90: float = 0.0
    delay_mean: float = 0.0
    score: float = 0.0
    buried_share: float = 0.0        # 대상 위에 늦은 그룹이 쌓이는 표본 비율
    buried_mean: float = 0.0
    top_cover_group: int | None = None
    unplaced_share: float = 0.0
    excluded: str = ""
    held: str = ""
    reasons: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    rec_id: str
    cntr_no: str
    created_at: float
    plan_version: int
    data_as_of: float
    options: list[Option]
    status: str                      # 추천됨, 보류, 승인, 다른 대안 선택, 만료, 실행 완료, 예외 검토
    hold_reason: str = ""
    hold_rule: str = ""
    recommended: str = "A"
    exception: str = ""
    exception_cleared: bool = False
    n_candidates: int = 0
    eta: EtaIssue | None = None
    vessel_id: str = ""
    group: int = 0
    note: str = ""


@dataclass
class Decision:
    rec_id: str
    chosen_option: str
    decision: str                    # 승인, 보류, 다른 대안
    reason_code: str
    memo: str
    decided_at: float
    opened_at: float | None = None


@dataclass
class Move:
    cntr_no: str
    kind: str                        # 최초 적치, 선작업, 재취급, 정상 인출
    frm: str
    to: str
    start: float
    end: float


@dataclass
class Event:
    t: float
    type: str
    data: dict


# ── 야드 ─────────────────────────────────────────────
@dataclass
class Yard:
    block: str
    bays: int
    rows: int
    max_tier: int
    stacks: dict            # (bay, row) -> list[cntr_no] (아래 → 위)
    area: dict              # vessel_id -> list[bay]
    reserved: dict = field(default_factory=dict)   # (bay, row) -> cntr_no (그 스택의 다음 칸)

    @classmethod
    def empty(cls, block, bays, rows, max_tier, area):
        return cls(block, bays, rows, max_tier, {(b, r): [] for b in range(bays) for r in range(rows)},
                   {k: list(v) for k, v in area.items()})

    def copy(self) -> "Yard":
        return Yard(self.block, self.bays, self.rows, self.max_tier, {k: v[:] for k, v in self.stacks.items()},
                    self.area, dict(self.reserved))

    def height(self, s) -> int:
        return len(self.stacks[s])

    def slot_name(self, bay: int, row: int, tier: int) -> str:
        return f"{self.block}-{bay + 1:02d}-{row + 1}-{tier + 1}"

    def next_slot_name(self, s) -> str:
        return self.slot_name(s[0], s[1], len(self.stacks[s]))

    def lane(self, bay: int) -> str:
        return f"{self.block[0]}블록 {bay + 1}레인"

    def bay_stacks(self, bay: int) -> list[list[str]]:
        return [self.stacks[(bay, r)] for r in range(self.rows)]

    def find(self, cntr: str):
        for (b, r), st in self.stacks.items():
            if cntr in st:
                return b, r, st.index(cntr)
        return None

    def count(self) -> int:
        return sum(len(v) for v in self.stacks.values())

    def capacity(self) -> int:
        return self.bays * self.rows * self.max_tier

    def occupancy(self) -> float:
        return self.count() / self.capacity()

    def slots(self) -> list[Slot]:
        out = []
        for (b, r), st in sorted(self.stacks.items()):
            for t in range(self.max_tier):
                res = self.reserved.get((b, r)) if t == len(st) else None
                out.append(Slot(self.block, b, r, t, st[t] if t < len(st) else None, res))
        return out
