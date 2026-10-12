"""⑤ 효과 확인 — 기준 정책 vs 제안 정책."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.experiment import run_condition
from core.metrics import METRICS, summarize
from core.policies import BASELINE_LABEL, PROPOSED_LABEL
from ui.components import C, chart, esc, footer, legend, section
from ui.state import engine, get_eta, get_grid, get_params, restricted, topbar

e = engine()
p = get_params()
ep = p["experiment"]
topbar("효과 비교", "같은 초기 적층·도착·계획 변경·장비 조건에서 두 정책의 총 추가 이동을 비교합니다.")
if restricted(("야드 플래너", "관제 담당")):
    footer()
    st.stop()
st.markdown('<div class="ss-callout warn">합성 데이터 시뮬레이션 결과이며, 실제 터미널 성과는 현장 실증에서 확인합니다. '
            '기준 정책은 공개한 단순 규칙이며 실제 TOS 성능을 대표하지 않습니다. 자동 비교에서는 추천을 모두 승인한 것으로 봅니다.</div>',
            unsafe_allow_html=True)

with st.form("cond"):
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1.3])
    occ = c1.selectbox("장치율", list(ep["occupancy_levels"]), index=1)
    err = c2.selectbox("ETA 오차 배율", ep["eta_error_levels"], format_func=lambda x: f"×{x}")
    pc = c3.selectbox("계획 변경", [False, True], format_func=lambda x: f"있음({ep['plan_change_share'] * 100:.0f}% 그룹 변경)" if x else "없음")
    ug = c4.selectbox("긴급 작업 유입", [False, True], format_func=lambda x: "있음" if x else "없음")
    n = c5.slider("반복 시드 수", 4, 20, ep["default_seeds"])
    run = st.form_submit_button("▶ 실행", type="primary")

key = (occ, err, pc, ug)
if run:
    with st.spinner(f"시드 {n}개로 두 정책을 돌리는 중입니다(시드당 약 1초)…"):
        df = run_condition(p, get_eta()["serving"], occ, err, pc, ug, range(1000, 1000 + n))
    st.session_state.exp = {"key": key, "df": df, "src": f"방금 실행 · 시드 {n}개"}
res = st.session_state.get("exp")
grid = get_grid()
if res is None or res["key"] != key:
    if grid is not None:
        g = grid[(grid.occupancy == occ) & (grid.eta_error == err) & (grid.plan_change == pc) & (grid.urgent == ug)]
        res = {"key": key, "df": g, "src": f"미리 계산한 결과 · 시드 {g.seed.nunique()}개 (python -m core.experiment)"} if len(g) else None
if res is None:
    st.info("▶ 실행을 눌러 결과를 계산해 주세요.")
    footer()
    st.stop()

df = res["df"]
s = summarize(df)
st.caption(f"{res['src']} · 평가 범위 V1 구역 · 실제 시작 장치율 {s['occupancy_start'] * 100:.0f}%(블록) · "
           f"공통 평가 대상 평균 {df.n_eval.mean():.0f}개")

red = s["reduction_pct"]
dm, dlo, dhi = s["diff_per100"]
if red is not None and red < 0:
    st.markdown(f'<div class="ss-callout danger">이 조건에서는 제안 정책의 총 추가 이동/100개가 기준 정책보다 {abs(red):.1f}% 많습니다. '
                '결과를 조정하지 않고 그대로 표시합니다.</div>', unsafe_allow_html=True)

cards = []
for k, name, unit, _ in METRICS:
    m = s["metrics"][k]
    (bm, bl, bh), (pm, pl, ph) = m["baseline"], m["proposed"]
    cards.append(f'<div class="ss-card yr-mcard"><div class="nm">{esc(name)}</div><div class="pair">'
                 f'<div><div class="tag">기준</div><div class="v base">{bm:.1f}<small style="font-size:.7rem"> {unit}</small></div>'
                 f'<div class="ci">95% {bl:.1f}~{bh:.1f}</div></div>'
                 f'<div><div class="tag">제안</div><div class="v prop">{pm:.1f}<small style="font-size:.7rem"> {unit}</small></div>'
                 f'<div class="ci">95% {pl:.1f}~{ph:.1f}</div></div></div></div>')
rtxt = "기준 0 · 차이만 표시" if red is None else f"{red:+.1f}%"
rcol = "var(--danger)" if (red is not None and red < 0) else "var(--safe)"
cards.insert(1, f'<div class="ss-card yr-mcard"><div class="nm">총 추가 이동 감소율 (기준 − 제안) ÷ 기준</div>'
                f'<div class="v" style="color:{rcol}">{rtxt}</div><div class="ci">차이 {dm:+.1f}/100개 (95% {dlo:+.1f}~{dhi:+.1f})</div>'
                f'<div class="ci" style="margin-top:.3rem">실증 잠정 목표 {ep["target_reduction_pct"]}% 감소와 별개인 합성 환경 값</div></div>')
cards.insert(2, f'<div class="ss-card yr-mcard" style="border-color:rgba(255,77,109,.5)"><div class="nm">악화된 실행 수</div>'
                f'<div class="v" style="color:var(--danger)">{s["worse_runs"]} <small style="font-size:.75rem">/ {s["n_seeds"]}회</small></div>'
                f'<div class="ci">같음 {s["equal_runs"]} · 개선 {s["better_runs"]} · 미완료 작업 기준 {s["unfinished"]["baseline"]} / 제안 {s["unfinished"]["proposed"]}건</div>'
                f'<div class="ci">추천 {s["recs"]}건 중 보류 {s["holds"]}건</div></div>')
st.markdown('<div class="yr-mgrid">' + "".join(cards) + "</div>", unsafe_allow_html=True)

l, r = st.columns(2)
b = df[df.policy == "baseline"].sort_values("seed")
pr = df[df.policy == "proposed"].sort_values("seed")
with l:
    section("총 추가 이동/100개", "점 = 평균 · 수염 = 95% 구간 · 막대 안 = 선작업/재취급")
    fig = go.Figure()
    for pol, lab, col in (("baseline", "기준 정책", C["base"]), ("proposed", "제안 정책", C["primary"])):
        mm, lo, hi = s["metrics"]["per100"][pol]
        sub = df[df.policy == pol]
        fig.add_bar(x=[lab], y=[sub.reloc_per100.mean()], name="작업 중 재취급", marker_color=col, opacity=.45, width=.38,
                    showlegend=pol == "baseline", hovertemplate="재취급 %{y:.1f}<extra></extra>")
        fig.add_bar(x=[lab], y=[sub.pm_per100.mean()], name="선작업", marker_color=col, marker_pattern_shape="/", width=.38,
                    opacity=.8, showlegend=pol == "baseline", hovertemplate="선작업 %{y:.1f}<extra></extra>")
        fig.add_scatter(x=[lab], y=[mm], mode="markers", marker=dict(size=13, color=col, line=dict(color=C["bg"], width=2)),
                        error_y=dict(type="data", symmetric=False, array=[hi - mm], arrayminus=[mm - lo], color=C["text"], width=10),
                        showlegend=False, hovertemplate=f"{lab} 평균 {mm:.1f} (95% {lo:.1f}~{hi:.1f})<extra></extra>")
    fig.update_layout(barmode="stack", yaxis=dict(title="회 / 100개"))
    chart(fig, 340, key="per100")
with r:
    section("시드별 쌍 비교", "대각선 위 = 제안 정책이 더 나쁨(악화)")
    worse = pr.per100.values > b.per100.values + 1e-9
    mx = max(b.per100.max(), pr.per100.max()) * 1.1 + 1
    fig = go.Figure()
    fig.add_scatter(x=[0, mx], y=[0, mx], mode="lines", line=dict(color=C["muted"], dash="dot", width=1), showlegend=False,
                    hoverinfo="skip")
    fig.add_scatter(x=b.per100, y=pr.per100, mode="markers", showlegend=False,
                    marker=dict(size=11, color=[C["danger"] if w else C["primary"] for w in worse],
                                symbol=["x" if w else "circle" for w in worse], line=dict(color=C["bg"], width=1)),
                    customdata=b.seed, hovertemplate="시드 %{customdata}<br>기준 %{x:.1f} · 제안 %{y:.1f}<extra></extra>")
    fig.add_annotation(x=mx * .25, y=mx * .9, text="악화", showarrow=False, font=dict(color=C["danger"]))
    fig.add_annotation(x=mx * .85, y=mx * .15, text="개선", showarrow=False, font=dict(color=C["primary"]))
    fig.update_layout(xaxis=dict(title="기준 정책 (회/100개)", range=[0, mx]), yaxis=dict(title="제안 정책 (회/100개)", range=[0, mx]))
    chart(fig, 340, key="pair")

section("조건별 감소율", f"장치율 × ETA 오차 · 계획 변경 {'있음' if pc else '없음'} · 긴급 작업 {'있음' if ug else '없음'} · 미리 계산한 격자")
if grid is not None:
    g = grid[(grid.plan_change == pc) & (grid.urgent == ug)]
    occs = list(ep["occupancy_levels"])
    errs = ep["eta_error_levels"]
    z, txt = [], []
    for o in occs:
        zr, tr = [], []
        for er in errs:
            sub = g[(g.occupancy == o) & (g.eta_error == er)]
            bm = sub[sub.policy == "baseline"].per100.mean()
            pm = sub[sub.policy == "proposed"].per100.mean()
            rr = (bm - pm) / bm * 100 if bm else np.nan
            ww = int(((sub[sub.policy == "proposed"].sort_values("seed").per100.values
                       - sub[sub.policy == "baseline"].sort_values("seed").per100.values) > 1e-9).sum())
            zr.append(rr)
            tr.append(f"{rr:+.0f}%<br><span style='font-size:10px'>악화 {ww}회</span>")
        z.append(zr)
        txt.append(tr)
    zmax = max(10, np.nanmax(np.abs(z)))
    fig = go.Figure(go.Heatmap(z=z, x=[f"×{x}" for x in errs], y=occs, text=txt, texttemplate="%{text}", zmin=-zmax, zmax=zmax,
                               colorscale=[[0, C["danger"]], [.5, "#2A3346"], [1, C["primary"]]],
                               colorbar=dict(title="감소율 %", tickfont=dict(color=C["muted"])),
                               hovertemplate="장치율 %{y} · 오차 %{x}<br>감소율 %{z:.1f}%<extra></extra>", xgap=3, ygap=3))
    fig.update_layout(xaxis=dict(title="ETA 오차 배율"), yaxis=dict(title="장치율"))
    chart(fig, 300, key="heat")
    st.caption("ETA 오차가 커지면 제안 정책의 이점이 줄고 보류가 늘어나는 것이 정상이며, 숨기지 않고 그대로 표시합니다. "
               "오차 배율마다 합성 도착이 달라지므로 기준 정책 값도 함께 바뀝니다.")

l2, r2 = st.columns(2)
with l2:
    section("ETA 오차·구간 품질", "합성 환경 검증값")
    m = get_eta()["metrics"]
    st.dataframe(pd.DataFrame([{"모델": {"baseline": "기준모형(중앙값)", "gbm": "후보모델(GBM)"}[k], "MAE(분)": round(v["mae"], 1),
                                "구간 포함률": f"{v['coverage'] * 100:.0f}%", "평균 폭(분)": round(v["width"], 1)} for k, v in m.items()]),
                 hide_index=True, use_container_width=True)
with r2:
    section("시연 세션 참고지표", "사람이 고른 결과는 자동 비교와 따로 표시")
    recs = list(e.recs.values())
    valid = [x for x in recs if x.status != "만료"]
    appr = [x for x in recs if e.decision_for(x.rec_id) and e.decision_for(x.rec_id).decision in ("승인", "다른 대안")]
    done = [x for x in appr if x.status == "실행 완료"]
    st.markdown(f'<div class="ss-card" style="font-size:.86rem;line-height:1.8">유효 추천 {len(valid)}건 중 승인·다른 대안 선택 '
                f'<b class="mono">{len(appr)}</b>건 · 그중 실행 완료 <b class="mono">{len(done)}</b>건<br>'
                f'<span style="color:var(--muted)">추천 수용·실행 비율은 참고지표로만 씁니다.</span></div>', unsafe_allow_html=True)

st.download_button("결과 CSV 다운로드", df.to_csv(index=False).encode("utf-8-sig"), "yard_ready_policy_compare.csv", "text/csv")
footer()
