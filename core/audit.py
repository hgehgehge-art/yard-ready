"""승인·보류·만료 로그."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

import pandas as pd

from core.models import hhmm


@dataclass
class AuditEntry:
    t: float
    kind: str           # 추천 생성, 보류, 승인, 다른 대안 선택, 만료, 승인 차단, 실행, 예외 검토 완료
    rec_id: str
    cntr_no: str
    plan_version: int
    detail: str = ""
    wall: float = field(default_factory=time.time)


class AuditLog:
    def __init__(self):
        self.entries: list[AuditEntry] = []

    def add(self, t, kind, rec_id, cntr_no, plan_version, detail=""):
        self.entries.append(AuditEntry(t, kind, rec_id, cntr_no, plan_version, detail))

    def frame(self) -> pd.DataFrame:
        if not self.entries:
            return pd.DataFrame(columns=["시각", "구분", "추천 ID", "컨테이너", "계획 버전", "내용"])
        df = pd.DataFrame([asdict(e) for e in self.entries])
        return pd.DataFrame({"시각": df["t"].map(hhmm), "구분": df["kind"], "추천 ID": df["rec_id"],
                             "컨테이너": df["cntr_no"], "계획 버전": df["plan_version"].map(lambda v: f"v{v}"),
                             "내용": df["detail"]})
