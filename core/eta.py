"""도착 예측 (기획서 7.1).

기준모형: 같은 운송사·같은 시간대의 과거 '예약시각 대비 실제 도착 편차' 분포(10·50·90 백분위).
구간 통과 지점을 지나면 '그 지점에서 항만까지의 과거 소요시간' 분포로 바꿉니다.
후보모델(선택): GradientBoostingRegressor(quantile loss).
백분율 신뢰도는 만들지 않습니다. 구간 폭과 검증 MAE만 내보냅니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

QS = (0.1, 0.5, 0.9)
PRE_DEPART_LEAD = 120  # 예약 기반 첫 ETA 발행 시각(예약 120분 전) — params.booking.initial_issue_lead_min과 같게 둠


def hour_band(minute_of_day) -> np.ndarray:
    return (np.asarray(minute_of_day, dtype=float) % 1440 // 180).astype(int)


def history_to_rows(hist: pd.DataFrame, n_cp: int, lead: float = PRE_DEPART_LEAD) -> pd.DataFrame:
    """이력 한 건을 '발행 단계'별 행으로 펼칩니다. stage −1 = 출발 전(예약 기반), 0.. = 구간 통과 지점."""
    parts = []
    base = hist[["day", "dow", "carrier", "booking", "arrival"]].copy()
    pre = base.copy()
    pre["stage"] = -1
    pre["issued_at"] = pre["booking"] - lead
    parts.append(pre)
    for k in range(n_cp):
        r = base.copy()
        r["stage"] = k
        r["issued_at"] = hist[f"cp{k}"].values
        parts.append(r)
    rows = pd.concat(parts, ignore_index=True)
    rows["since_booking"] = rows["issued_at"] - rows["booking"]
    return rows


class BaselineEta:
    name = "기준모형(중앙값)"

    def __init__(self, min_group_n: int = 30, min_carrier_n: int = 40):
        self.min_group_n = min_group_n
        self.min_carrier_n = min_carrier_n

    def fit(self, hist: pd.DataFrame, n_cp: int) -> "BaselineEta":
        dev = hist["arrival"] - hist["booking"]
        h = hist.assign(dev=dev, band=hour_band(hist["booking"]))
        self.group_q = {k: np.quantile(g["dev"], QS) for k, g in h.groupby(["carrier", "band"]) if len(g) >= self.min_group_n}
        self.carrier_q = {k: np.quantile(g["dev"], QS) for k, g in h.groupby("carrier") if len(g) >= self.min_carrier_n}
        self.pooled_q = np.quantile(h["dev"], QS)
        self.carrier_n = h.groupby("carrier").size().to_dict()
        self.stage_q = {}
        self.stage_carrier_q = {}
        for k in range(n_cp):
            rem = hist["arrival"] - hist[f"cp{k}"]
            self.stage_q[k] = np.quantile(rem, QS)
            for c, g in rem.groupby(hist["carrier"]):
                if len(g) >= self.min_carrier_n:
                    self.stage_carrier_q[(c, k)] = np.quantile(g, QS)
        return self

    def source(self, carrier: str, booking: float) -> str:
        band = int(hour_band([booking])[0])
        if (carrier, band) in self.group_q:
            return "운송사×시간대"
        if carrier in self.carrier_q:
            return "운송사 전체"
        return "전체 이력(운송사 이력 부족)"

    def predict_one(self, carrier: str, booking: float, stage: int, issued_at: float) -> tuple[float, float, float]:
        if stage < 0:
            band = int(hour_band([booking])[0])
            q = self.group_q.get((carrier, band))
            if q is None:
                q = self.carrier_q.get(carrier, self.pooled_q)
            p = booking + q
            # 출발 전이라도 지금보다 이르게 도착할 수는 없음
            p = np.maximum(p, issued_at + 5)
        else:
            q = self.stage_carrier_q.get((carrier, stage), self.stage_q[stage])
            p = issued_at + q
        p = np.sort(p)
        return float(p[0]), float(p[1]), float(p[2])

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        return np.array([self.predict_one(c, b, int(s), i) for c, b, s, i in
                         zip(rows["carrier"], rows["booking"], rows["stage"], rows["issued_at"])])


class GbmEta:
    name = "후보모델(GBM)"

    def __init__(self, n_estimators=120, max_depth=3, learning_rate=0.08, seed=0):
        self.kw = dict(n_estimators=n_estimators, max_depth=max_depth, learning_rate=learning_rate, random_state=seed)

    def _x(self, rows: pd.DataFrame) -> np.ndarray:
        cidx = rows["carrier"].map(self.carrier_index).fillna(-1).values
        ontime = rows["carrier"].map(self.ontime).fillna(self.ontime_all).values
        return np.column_stack([
            (rows["booking"].values % 1440) / 60.0, rows["dow"].values, cidx, rows["stage"].values,
            rows["since_booking"].values, ontime])

    def fit(self, rows: pd.DataFrame) -> "GbmEta":
        from sklearn.ensemble import GradientBoostingRegressor
        self.carrier_index = {c: i for i, c in enumerate(sorted(rows["carrier"].unique()))}
        pre = rows[rows["stage"] == -1]
        on = (pre["arrival"] - pre["booking"]).abs() <= 15
        self.ontime = on.groupby(pre["carrier"]).mean().to_dict()
        self.ontime_all = float(on.mean())
        x = self._x(rows)
        # 목표: 발행 시각부터 실제 도착까지 남은 시간
        y = (rows["arrival"] - rows["issued_at"]).values
        self.models = [GradientBoostingRegressor(loss="quantile", alpha=a, **self.kw).fit(x, y) for a in QS]
        return self

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        x = self._x(rows)
        out = np.column_stack([m.predict(x) for m in self.models]) + rows["issued_at"].values[:, None]
        out = np.sort(out, axis=1)
        out[:, 0] = np.maximum(out[:, 0], rows["issued_at"].values + 1)
        return np.maximum.accumulate(out, axis=1)


def evaluate(pred: np.ndarray, actual: np.ndarray) -> dict:
    p10, p50, p90 = pred[:, 0], pred[:, 1], pred[:, 2]
    return {"mae": float(np.mean(np.abs(p50 - actual))),
            "coverage": float(np.mean((actual >= p10) & (actual <= p90))),
            "width": float(np.mean(p90 - p10)),
            "n": int(len(actual))}


def train_and_validate(hist: pd.DataFrame, params: dict) -> dict:
    """60일 이력을 시간순으로 앞 45일 학습, 뒤 15일 검증으로 나눠 두 모델을 비교합니다."""
    hp = params["history"]
    n_cp = len(params["arrival"]["checkpoints"])
    lead = params["booking"]["initial_issue_lead_min"]
    train = hist[hist["day"] < hp["train_days"]]
    test = hist[hist["day"] >= hp["train_days"]]
    test_rows = history_to_rows(test, n_cp, lead)
    base = BaselineEta(hp["min_group_n"], hp["min_carrier_n"]).fit(train, n_cp)
    res = {"baseline": base, "metrics": {}, "by_stage": {}}
    res["metrics"]["baseline"] = evaluate(base.predict(test_rows), test_rows["arrival"].values)
    res["by_stage"]["baseline"] = {int(s): evaluate(base.predict(g), g["arrival"].values) for s, g in test_rows.groupby("stage")}
    chosen = "baseline"
    if params["eta_model"].get("use_gbm", True):
        try:
            em = params["eta_model"]
            gbm = GbmEta(em["gbm_estimators"], em["gbm_depth"], em["gbm_learning_rate"], params["seed"]).fit(
                history_to_rows(train, n_cp, lead))
            res["gbm"] = gbm
            res["metrics"]["gbm"] = evaluate(gbm.predict(test_rows), test_rows["arrival"].values)
            res["by_stage"]["gbm"] = {int(s): evaluate(gbm.predict(g), g["arrival"].values) for s, g in test_rows.groupby("stage")}
            if res["metrics"]["gbm"]["mae"] < res["metrics"]["baseline"]["mae"]:
                chosen = "gbm"
        except ImportError:  # scikit-learn이 없으면 기준모형만 씀
            pass
    # 후보모델이 기준모형보다 나쁘면 기준모형을 씁니다. 운영 발행용 모델은 60일 전체로 다시 맞춥니다.
    res["chosen"] = chosen
    if chosen == "gbm":
        em = params["eta_model"]
        res["serving"] = GbmEta(em["gbm_estimators"], em["gbm_depth"], em["gbm_learning_rate"], params["seed"]).fit(
            history_to_rows(hist, n_cp, lead))
    else:
        res["serving"] = BaselineEta(hp["min_group_n"], hp["min_carrier_n"]).fit(hist, n_cp)
    return res
