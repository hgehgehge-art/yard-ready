"""① 상황 확인 — 도착·변경 분석."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.engine import classify
from core.models import hhmm
from ui.components import C, chart, esc, footer, quad, section
from ui.state import engine, get_eta, get_params, restricted, selected, topbar

e = engine()
p = get_params()
topbar("도착·변경 분석", "예약시각과 실제 도착의 편차, 선박 작업 시간대, 계획 변경 이력을 함께 봅니다.")
if restricted(("야드 플래너", "관제 담당")):
    footer()
    st.stop()

opts = list(dict.fromkeys(e.world.focus + [r["cntr"] for r in e.review_list()] + [selected()]))
c = st.selectbox("컨테이너", opts, index=opts.index(selected()),
                 format_func=lambda x: f"{x} · {e.vessel_of(x)} · 그룹 {e.group(x)}")
st.session_state.sel_cntr = c
b = e.world.bookings[c]
vid = e.vessel_of(c)
v = e.world.vessels[vid]
hist = e.eta_history(c)
last = hist[-1] if hist else e.provisional_eta(c)
arrived = e.has_arrived(c)
ata = e.world.arrivals[c].gate_in if arrived else None
arr, ship = classify(p, b.booking_time, last.eta_p50, e.group(c))

st.markdown(f'{quad(arr, ship)} &nbsp;<span style="color:var(--muted);font-size:.85rem">운송사 {esc(b.carrier_id)} · '
            f'트럭 {esc(b.truck_id)} · 반입 마감 <span class="mono">{hhmm(e.cutoff(vid))}</span> · '
            f'선박 {esc(v.name)} {esc(v.voyage)}</span>', unsafe_allow_html=True)

section("도착 타임라인", "실선·시안 = 예상(ETA 구간) · 점선·보라 = 관측(ATA) · 음영 = 선박 작업 시간대(Port-i 모의)")
fig = go.Figure()
rows = ["예약"] + [f"ETA {hhmm(x.issued_at)} 발행" for x in hist]
y_of = {r: i for i, r in enumerate(reversed(rows))}
fig.add_vrect(x0=v.berth_window_start, x1=v.berth_window_end, fillcolor="rgba(255,181,71,.08)", line_width=0)
fig.add_annotation(x=v.berth_window_start, y=len(rows) - .3, text=f"선박 작업 {hhmm(v.berth_window_start)}~", xanchor="left",
                   showarrow=False, font=dict(color=C["warn"], size=11))
fig.add_vline(x=e.cutoff(vid), line=dict(color=C["danger"], width=1.5))
fig.add_annotation(x=e.cutoff(vid), y=-.85, text=f"반입 마감 {hhmm(e.cutoff(vid))}", showarrow=False,
                   font=dict(color=C["danger"], size=11))
fig.add_vline(x=e.now, line=dict(color=C["text"], width=1, dash="dot"))
fig.add_annotation(x=e.now, y=len(rows) - .3, text=f"현재 {hhmm(e.now)}", showarrow=False, xanchor="right",
                   font=dict(color=C["text"], size=11))
fig.add_scatter(x=[b.booking_time], y=[y_of["예약"]], mode="markers", marker=dict(size=12, color=C["muted"], symbol="diamond"),
                name="예약시각", hovertemplate=f"예약 {hhmm(b.booking_time)}<extra></extra>")
for i, x in enumerate(hist):
    yy = y_of[rows[i + 1]]
    latest = i == len(hist) - 1
    col = C["primary"] if latest else "rgba(0,212,255,.38)"
    fig.add_scatter(x=[x.eta_p10, x.eta_p90], y=[yy, yy], mode="lines", line=dict(color=col, width=8 if latest else 5),
                    showlegend=False, hovertemplate=f"{hhmm(x.eta_p10)}~{hhmm(x.eta_p90)} · 폭 {x.width:.0f}분<extra></extra>")
    fig.add_scatter(x=[x.eta_p50], y=[yy], mode="markers", marker=dict(size=11, color=col, line=dict(color=C["bg"], width=2)),
                    name="ETA 중앙값" if latest else None, showlegend=latest,
                    hovertemplate=f"중앙값 {hhmm(x.eta_p50)}<extra></extra>")
if ata is not None:
    fig.add_vline(x=ata, line=dict(color=C["observed"], width=1.5, dash="dash"))
    fig.add_scatter(x=[ata], y=[y_of[rows[-1]]], mode="markers", marker=dict(size=13, color=C["observed"], symbol="circle-open",
                    line=dict(width=3)), name="ATA(관측)", hovertemplate=f"ATA {hhmm(ata)}<extra></extra>")
lo = min([b.booking_time - 30, e.now - 20] + [x.eta_p10 for x in hist])
hi = max([e.cutoff(vid) + 30, v.berth_window_start + 40] + [x.eta_p90 + 20 for x in hist])
ticks = list(range(int(lo // 15 * 15), int(hi) + 15, 15))
fig.update_layout(xaxis=dict(range=[lo, hi], tickvals=ticks, ticktext=[hhmm(t) if t % 30 == 0 else "" for t in ticks]),
                  yaxis=dict(tickvals=list(y_of.values()), ticktext=list(y_of.keys()), range=[-1.2, len(rows) - .1]),
                  margin=dict(l=110, r=18, t=30, b=30))
chart(fig, 120 + 46 * len(rows), key="tl")
if hist:
    dev = last.eta_p50 - b.booking_time
    overlap = last.eta_p90 > v.berth_window_start - 120
    st.markdown(f'<div class="ss-callout {"warn" if dev > 15 else ""}">예약 <b class="mono">{hhmm(b.booking_time)}</b> 대비 최신 ETA '
                f'구간은 <b class="mono">{hhmm(last.eta_p10)}~{hhmm(last.eta_p90)}</b>이며 중앙값 편차는 '
                f'<b class="mono">{dev:+.0f}분</b>입니다. 구간 폭은 {last.width:.0f}분이고 {e.now - last.issued_at:.0f}분 전에 발행했습니다.'
                + (f' 선박 작업 시작 {hhmm(v.berth_window_start)} 전 2시간 안에 도착하므로 앞선 인출 그룹과 겹칠 수 있습니다.' if overlap else "")
                + '</div>', unsafe_allow_html=True)

l, r = st.columns([1.35, 1])
with l:
    section("ETA 발행 이력", "그 시점에 알던 입력만 사용")
    df = pd.DataFrame([{
        "발행 시각": hhmm(x.issued_at),
        "입력": f"예약 {hhmm(x.inputs.get('예약시각'))} · {x.inputs.get('마지막 통과', '')}"
                + (f" {hhmm(x.inputs['통과 시각'])}" if x.inputs.get("통과 시각") else ""),
        "출발 시각": hhmm(b.depart_time) if x.issued_at >= b.depart_time else "출발 전",
        "ETA 구간": f"{hhmm(x.eta_p10)}~{hhmm(x.eta_p90)}", "구간 폭(분)": round(x.width), "모델": x.model} for x in hist])
    st.dataframe(df, hide_index=True, use_container_width=True)
with r:
    section("예측 품질", "합성 환경 검증값(뒤 15일) · 백분율 신뢰도는 쓰지 않음")
    m = get_eta()
    names = {"baseline": "기준모형(중앙값)", "gbm": "후보모델(GBM)"}
    cards = []
    for k in ("baseline", "gbm"):
        if k not in m["metrics"]:
            continue
        mm = m["metrics"][k]
        use = " · 운영 발행" if m["chosen"] == k else ""
        cards.append(f'<div class="ss-card" style="padding:.8rem .9rem"><div style="font-size:.8rem;color:var(--muted)">'
                     f'{names[k]}{use}</div><div class="mono" style="font-size:1.05rem;margin-top:.3rem">MAE {mm["mae"]:.1f}분</div>'
                     f'<div style="font-size:.8rem;color:var(--muted);line-height:1.6">구간 포함률 {mm["coverage"] * 100:.0f}% · '
                     f'평균 폭 {mm["width"]:.0f}분<br>검증 {mm["n"]:,}건</div></div>')
    st.markdown('<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">' + "".join(cards) + "</div>",
                unsafe_allow_html=True)
    st.caption("검증은 시간순 분할(앞 45일 학습, 뒤 15일 검증)로 계산했습니다. 후보모델이 기준모형보다 나쁘면 기준모형으로 발행합니다. "
               "시나리오 대상 컨테이너의 ETA는 시연용으로 고정한 값입니다.")

section("계획 변경 이력", f"{vid} 작업계획(TOS 모의) 버전")
pl = [x for x in e.plan_log if x["vessel"] == vid]
st.dataframe(pd.DataFrame([{"버전": f"v{x['version']}", "변경 시각": hhmm(x["t"]), "변경 내용": x["change"],
                            "영향 컨테이너 수": x["affected"]} for x in pl]), hide_index=True, use_container_width=True)

if st.button("CY 배치 대안 보기 →", type="primary"):
    st.switch_page("pages/3_CY_배치_대안.py")
footer()
