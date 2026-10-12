"""성과지표 계산 (기획서 8.2)."""
from __future__ import annotations

import numpy as np
import pandas as pd

METRICS = [
    # key, 이름, 단위, 낮을수록 좋음
    ("per100", "총 추가 이동/100개", "회", True),
    ("pm_per100", "선작업 이동/100개", "회", True),
    ("reloc_per100", "작업 중 재취급/100개", "회", True),
    ("lb_per100", "피할 수 없는 방해(하한)/100개", "회", True),
    ("delay_mean", "야드 공급 지연 평균", "분", True),
    ("delay_p95", "야드 공급 지연 P95", "분", True),
    ("dwell_mean", "트럭 터미널 체류 평균", "분", True),
    ("dwell_p95", "트럭 터미널 체류 P95", "분", True),
    ("unfinished", "미완료 작업(구역 만석)", "건", True),
]


def per100(extra: float, n: int) -> float:
    return extra / max(1, n) * 100


def reduction_pct(base: float, prop: float) -> float | None:
    """(기준 − 제안) ÷ 기준 × 100. 기준이 0이면 None(차이만 표시)."""
    if base == 0:
        return None
    return (base - prop) / base * 100


def ci95(x) -> tuple[float, float, float]:
    x = np.asarray(x, dtype=float)
    m = float(x.mean()) if len(x) else float("nan")
    if len(x) < 2:
        return m, m, m
    h = 1.96 * float(x.std(ddof=1)) / np.sqrt(len(x))
    return m, m - h, m + h


def summarize(runs: pd.DataFrame) -> dict:
    """runs: seed × policy 행. 정책별 평균·95% 구간, 악화된 실행 수, 감소율."""
    b = runs[runs["policy"] == "baseline"].set_index("seed").sort_index()
    p = runs[runs["policy"] == "proposed"].set_index("seed").sort_index()
    out = {"n_seeds": int(len(b)), "metrics": {}}
    for key, name, unit, lower in METRICS:
        out["metrics"][key] = {"name": name, "unit": unit, "baseline": ci95(b[key]), "proposed": ci95(p[key]),
                               "lower_is_better": lower}
    diff = p["per100"] - b["per100"]
    out["worse_runs"] = int((diff > 1e-9).sum())
    out["equal_runs"] = int((diff.abs() <= 1e-9).sum())
    out["better_runs"] = int((diff < -1e-9).sum())
    out["reduction_pct"] = reduction_pct(float(b["per100"].mean()), float(p["per100"].mean()))
    out["diff_per100"] = ci95(diff)
    out["unfinished"] = {"baseline": int(b["unfinished"].sum()), "proposed": int(p["unfinished"].sum())}
    out["holds"] = int(p["n_holds"].sum())
    out["recs"] = int(p["n_recs"].sum())
    out["occupancy_start"] = float(b["occupancy_start"].mean())
    return out
