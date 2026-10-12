"""① 상황 확인 — 통합 관제."""
import plotly.graph_objects as go
import streamlit as st

from core.models import hhmm
from ui.components import C, chart, chip, esc, footer, kpi_card, kpi_row, quad, section
from ui.state import engine, get_params, restricted, topbar

e = engine()
p = get_params()
topbar("통합 관제", "도착 편차가 큰 컨테이너와 크레인 유휴 시간대를 한 화면에서 확인합니다.")
if restricted(("야드 플래너", "관제 담당")):
    footer()
    st.stop()

st.markdown('<div class="yr-core"><b>YARD READY</b>는 기존 운송·선박·터미널 정보를 활용해 도착 불확실성에 따른 최초 적치와 '
            '선택적 선작업 대안을 제시하고, 현장 관리자가 승인한 결과를 기록하는 AI 의사결정 지원 모듈입니다.</div>',
            unsafe_allow_html=True)

rv = e.review_list()
today = [c for c in e.pending() if 0 <= e.world.bookings[c].booking_time < 1440]
vid = e.vessel_of(e.world.focus[0])
base = e.expected_area(vid)
pure = e.expected_area(vid, use_reservations=False)   # 승인 예약 없이 기준 정책만 쓴 값
d = base["per100"] - pure["per100"]
right = ""
if abs(d) > 1e-9:
    right = f'<span class="ss-badge {"safe" if d < 0 else "danger"}">기준 정책 대비 {d:+.1f}</span>'
occ = e.world.yard.occupancy()
kpi_row([
    kpi_card("오늘 반입 예정", f"{len(today)}", "개", f"게이트 미통과 · 블록 {e.world.yard.block}", delay=0),
    kpi_card("검토 필요", f"{len(rv)}", "건", f"예약 대비 {p['review']['deviation_min']}분 넘는 편차·마감 초과 가능·넓은 구간",
             accent=C["warn"], delay=.05),
    kpi_card(f"예상 총 추가 이동/100개 · {vid}", f"{base['per100']:.1f}", "회",
             f"ETA 중앙값으로 쌓았을 때의 계산값 · 승인 반영", right=right, delay=.1),
    kpi_card("블록 장치율", f"{occ * 100:.0f}", "%", f"{e.world.yard.count()} / {e.world.yard.capacity()}칸",
             accent=C["danger"] if occ > .85 else C["primary"], delay=.15),
])

section("검토 필요 목록", "우선순위: 지연·상위 › 조기·하위 › 지연·하위 › 조기·상위 › 정시")
if not rv:
    st.markdown('<div class="ss-callout">지금 검토가 필요한 컨테이너가 없습니다.</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="yr-rv-head"><span>컨테이너 · 선박</span><span>예약시각</span><span>ETA 구간</span>'
                '<span>편차</span><span>분류</span><span>추천 상태</span></div>', unsafe_allow_html=True)
    for i, r in enumerate(rv):
        rec = r["rec"]
        status = rec.status if rec else "미검토"
        if rec and e.expiry_reason(rec) and status in ("추천됨", "보류"):
            status = "만료"
        if r["over_cutoff"] and status in ("미검토", "추천됨", "보류"):
            st_html = chip(status) + " " + chip("예외 검토")
        else:
            st_html = chip(status)
        dev = r["dev"]
        cls = "late" if dev > 0 else "early"
        c1, c2 = st.columns([10, 1.4], vertical_alignment="center")
        c1.markdown(
            f'<div class="yr-rv {"focus" if r["cntr"] in e.world.focus else ""}" style="animation-delay:{i * .04:.2f}s">'
            f'<span><span class="mono"><b>{esc(r["cntr"])}</b></span><small>{esc(r["vessel"])} · 그룹 {r["group"]}</small></span>'
            f'<span class="mono">{hhmm(r["booking"])}</span>'
            f'<span class="mono">{hhmm(r["eta"].eta_p10)}~{hhmm(r["eta"].eta_p90)}<small>폭 {r["width"]:.0f}분 · {r["stale"]:.0f}분 전 발행</small></span>'
            f'<span class="mono dev {cls}">{dev:+.0f}분</span>'
            f'<span>{quad(r["arr"], r["ship"])}</span><span>{st_html}</span></div>', unsafe_allow_html=True)
        if c2.button("검토하기", key=f"rv_{r['cntr']}", use_container_width=True,
                     type="primary" if r["cntr"] in e.world.focus else "secondary"):
            st.session_state.sel_cntr = r["cntr"]
            st.session_state.opened[r["cntr"]] = __import__("time").time()
            st.switch_page("pages/2_도착_변경_분석.py")

left, right = st.columns([1.6, 1])
with left:
    section("크레인 작업량", "15분 단위 · 연한 초록 = 유휴 시간대(상한의 50% 미만, 선작업 가능)")
    cp = p["crane"]
    slot = cp["slot_min"]
    k0, k1 = int((e.now - 60) // slot), int((e.retrieval_start[vid] + 90) // slot)
    xs = [k * slot for k in range(k0, k1)]
    loads = [e.world.crane_load.get(k, 0) for k in range(k0, k1)]
    pm = [e.pm_used.get(k, 0) for k in range(k0, k1)]
    thr = cp["cap_per_slot"] * cp["idle_ratio"]
    fig = go.Figure()
    for x, ld in zip(xs, loads):
        if ld < thr:
            fig.add_vrect(x0=x, x1=x + slot, fillcolor="rgba(46,230,166,.10)", line_width=0, layer="below")
    lab = [hhmm(x) for x in xs]
    fig.add_bar(x=[x + slot / 2 for x in xs], y=loads, width=slot * .8, name="계획 작업", marker_color=C["base"],
                customdata=lab, hovertemplate="%{customdata} · 작업 %{y}건<extra></extra>")
    if any(pm):
        fig.add_bar(x=[x + slot / 2 for x in xs], y=pm, width=slot * .8, name="승인한 선작업", marker_color=C["primary"],
                    customdata=lab, hovertemplate="%{customdata} · 선작업 %{y}건<extra></extra>")
    fig.add_hline(y=cp["cap_per_slot"], line=dict(color=C["danger"], dash="dot", width=1),
                  annotation_text="상한 6회", annotation_font_color=C["muted"])
    fig.add_vline(x=e.now, line=dict(color=C["text"], width=1.5))
    fig.add_annotation(x=e.now, y=cp["cap_per_slot"] + 1.4, text=f"현재 {hhmm(e.now)}", showarrow=False,
                       font=dict(color=C["text"], size=11))
    for v, t in [(vid, e.retrieval_start[vid])]:
        fig.add_vline(x=t, line=dict(color=C["warn"], width=1, dash="dash"))
        fig.add_annotation(x=t, y=cp["cap_per_slot"] + .6, text=f"{v} 인출 시작", showarrow=False,
                           font=dict(color=C["warn"], size=11))
    ticks = [x for x in xs if x % 60 == 0]
    fig.update_layout(barmode="stack", xaxis=dict(tickvals=ticks, ticktext=[hhmm(x) for x in ticks]),
                      yaxis=dict(title="작업 수", range=[0, cp["cap_per_slot"] + 2.2]), showlegend=any(pm))
    chart(fig, 300, key="crane")
with right:
    section("사건 피드", "최근 순")
    items = []
    for f in reversed(e.feed[-14:]):
        lv = {"danger": "rollover", "warn": "weight_fix"}.get(f["level"], "add")
        items.append(f'<div class="item {lv}"><span class="time">{hhmm(f["t"])}</span>'
                     f'<span class="msg">{esc(f["text"])}<small>{esc(f["sub"])}</small></span></div>')
    st.markdown('<div class="ss-feed">' + "".join(items) + "</div>", unsafe_allow_html=True)

footer()
