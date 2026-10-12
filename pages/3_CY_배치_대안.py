"""② 대안 산출 — CY 배치 대안 (데모의 중심 화면)."""
import time

import plotly.graph_objects as go
import streamlit as st

from core.constraints import explain_stacks
from core.models import hhmm
from ui.components import C, GROUP_COLORS, FILLER_COLOR, chart, chip, esc, footer, legend, section
from ui.state import engine, get_params, restricted, role, selected, topbar
from ui.yard_view import bay_sections, yard_3d

REASON_CODES = ["장비 배치 사정", "긴급 작업 우선", "예측 신뢰 부족", "현장 경험상 다른 위치가 나음", "추천 근거에 동의", "기타"]

e = engine()
p = get_params()
topbar("CY 배치 대안", "현재 계획 유지안과 후보 변경안, 선작업 포함안의 예상 추가 이동을 같은 표본으로 비교합니다.")
if restricted(("야드 플래너", "관제 담당")):
    footer()
    st.stop()

opts = list(dict.fromkeys(e.world.focus + [r["cntr"] for r in e.review_list()] + [selected()]))
c = st.selectbox("컨테이너", opts, index=opts.index(selected()),
                 format_func=lambda x: f"{x} · {e.vessel_of(x)} · 그룹 {e.group(x)}")
st.session_state.sel_cntr = c
vid = e.vessel_of(c)
st.session_state.opened.setdefault(c, time.time())

rec = e.rec_for(c)
if rec is None and e.is_pending(c):
    with st.spinner("ETA 구간에서 도착 시각을 200번 뽑아 대안을 비교하는 중입니다…"):
        rec = e.create_rec(c)

if rec is None:
    pos = e.world.yard.find(c)
    where = e.world.yard.slot_name(*pos) if pos else "다른 블록"
    st.markdown(f'<div class="ss-callout">이 컨테이너는 이미 게이트를 통과해 <b class="mono">{where}</b>에 장치했습니다. '
                '추천을 새로 계산하지 않습니다.</div>', unsafe_allow_html=True)
    footer()
    st.stop()

why = e.expiry_reason(rec)
expired = bool(why) and rec.status in ("추천됨", "보류", "만료")
status = "만료" if expired else rec.status
hd = (f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:.2rem 0 .6rem">{chip(status)}'
      f'<span style="color:var(--muted);font-size:.88rem">이 추천은 <b class="mono" style="color:var(--text)">{hhmm(rec.created_at)}</b>, '
      f'작업계획 <b class="mono" style="color:var(--text)">v{rec.plan_version}</b> 기준입니다. ETA 발행 {hhmm(rec.data_as_of)} · '
      f'구간 {hhmm(rec.eta.eta_p10)}~{hhmm(rec.eta.eta_p90)} (폭 {rec.eta.width:.0f}분)</span></div>')
st.markdown(hd, unsafe_allow_html=True)

if expired:
    st.markdown(f'<div class="ss-callout danger ss-pulse">{esc(why or rec.note)}</div>', unsafe_allow_html=True)
    if e.is_pending(c) and st.button("↻ 다시 계산", type="primary"):
        with st.spinner("새 작업계획과 최신 ETA로 다시 계산하는 중입니다…"):
            e.create_rec(c)
        st.rerun()
if rec.exception and not rec.exception_cleared:
    st.markdown(f'<div class="ss-callout danger ss-pulse"><b>반입 마감을 넘길 수 있어 예외 검토가 필요합니다.</b> '
                f'{esc(rec.exception)} 배치를 승인해도 예외 검토가 끝날 때까지 완료로 표시하지 않습니다.</div>', unsafe_allow_html=True)
if rec.status == "보류" or (rec.hold_reason and status == "보류"):
    st.markdown(f'<div class="ss-callout warn"><b>추천 보류.</b> {esc(rec.hold_reason)}</div>', unsafe_allow_html=True)
if rec.note and not expired:
    st.markdown(f'<div class="ss-callout">{esc(rec.note)}</div>', unsafe_allow_html=True)

okeys = {o.key: o for o in rec.options}
show_rec = rec.recommended if rec.status == "추천됨" else None
d = e.decision_for(rec.rec_id)
chosen = d.chosen_option if d and d.decision != "보류" else None

left, right = st.columns([1.55, 1], gap="large")
with left:
    section("2D 적층 단면", f"{vid} 구역 베이 4개 · 로우(R) × 단 · 칸 = 번호 끝 4자리와 인출 그룹")
    views = ["현재 야드"] + [f"{o.key} 적용 후 예상" for o in rec.options]
    default = f"{chosen or 'A'} 적용 후 예상"
    view = st.radio("보기", views, index=views.index(default) if default in views else 0, horizontal=True,
                    label_visibility="collapsed", key=f"view_{rec.rec_id}")
    placed_now = e.placed()
    if view == "현재 야드":
        y = e.world.yard
        ex = explain_stacks(y, vid, c)
        cands = {r["stack"] for r in ex if r["ok"]}
        inel = {r["stack"]: r["reasons"] for r in ex if not r["ok"]}
        recslot = okeys[show_rec].stack if show_rec and okeys[show_rec].stack else None
        res = {s: o for s, o in y.reserved.items()}
        fig = bay_sections(y, vid, e.world.groups, target=c, current=None, candidates=cands, recommended=recslot,
                           ineligible=inel, reserved=res)
        cap = (f"후보 슬롯 {len(cands)}개(시안 점선) · 회색 빗금은 제약 위반으로 제외한 칸입니다(마우스를 올리면 사유가 보입니다). "
               f"대상 {c}는 아직 도착 전입니다.")
    else:
        key = view.split()[0]
        y = e.projected_yard(rec, key)
        fig = bay_sections(y, vid, e.world.groups, target=c, current=placed_now)
        o = okeys[key]
        cap = (f"모든 미도착 컨테이너가 ETA 중앙값에 도착한다고 볼 때 인출 직전의 예상 적층입니다(점선·반투명 = 예상). "
               f"흰 테두리가 대상, 빨간 테두리가 대상 위에 쌓인 늦은 그룹입니다. 200개 표본 중 "
               f"{o.buried_share * 100:.0f}%에서 대상 위에 늦은 그룹이 쌓입니다.")
    chart(fig, 560, key=f"bay_{view}")
    legend([(f"그룹 {g}", col) for g, col in GROUP_COLORS.items()] + [("선반입(이번 인출 없음)", FILLER_COLOR),
           ("후보 슬롯", C["primary"])], dashed={"후보 슬롯"})
    st.caption(cap)
    if st.toggle("3D 보기", key="t3d"):
        chart(yard_3d(y, vid, e.world.groups, target=c, current=None if view == "현재 야드" else placed_now), 460,
              key=f"y3d_{view}")

with right:
    section("배치 대안", "첫 카드는 항상 현재 계획 유지")
    a = okeys["A"]
    for o in rec.options:
        best = o.key == show_rec
        tag = chip("추천됨") if best else ""
        if o.held:
            tag = chip("보류")
        if chosen == o.key:
            tag = chip("승인" if d.decision == "승인" else "다른 대안 선택")
        pm = ", ".join(f'{m["cntr"][-4:]} {m["from"]}→{m["to"]}' for m in o.premarshal) or "없음"
        dcls = "good" if o.diff_mean < -0.05 else ("bad" if o.diff_mean > 0.05 else "")
        diff = "기준" if o.key == "A" else f'{o.diff_mean:+.1f}회 <span style="color:var(--muted);font-weight:400">(P10~P90 {o.diff_p10:+.0f}~{o.diff_p90:+.0f})</span>'
        extra = ""
        if o.excluded:
            extra += f'<div class="ss-callout danger" style="margin:.5rem 0 0">{esc(o.excluded)}</div>'
        if o.held and rec.hold_reason:
            extra += f'<div class="ss-callout warn" style="margin:.5rem 0 0">보류 사유: {esc(rec.hold_reason)}</div>'
        slot_txt = o.slot_name if o.stack is not None else f"{o.slot_name} <span style='font-size:.75rem;color:var(--muted)'>(도착 시점에 기준 정책으로 결정)</span>"
        st.markdown(f"""
<div class="ss-card yr-opt {'best' if best or chosen == o.key else ''} {'dim' if o.excluded else ''}">
  <div class="hd"><span class="key">대안 {o.key} · {esc(o.label)}</span>{tag}</div>
  <div class="slot">{slot_txt}</div>
  <div class="eq">예상 추가 이동 {o.total_mean:.1f}회 <span>= 선작업 {o.pm_mean:.0f}회 + 작업 중 재취급 기대값 {o.reloc_mean:.1f}회 · P10~P90 {o.total_p10:.0f}~{o.total_p90:.0f}회</span></div>
  <div class="row"><span>유지안 대비</span><b class="{dcls}">{diff}</b></div>
  <div class="row"><span>필요한 선작업</span><b>{esc(pm)}</b></div>
  <div class="row"><span>선작업 유휴 시간대</span><b>{esc(o.idle_window or '—')}</b></div>
  <div class="row"><span>예상 야드 공급 지연</span><b>{o.delay_mean:.1f}분</b></div>
  <ul>{''.join(f'<li>{esc(x)}</li>' for x in o.reasons)}</ul>{extra}
</div>""", unsafe_allow_html=True)
    st.caption(f"{vid} 구역(베이 {', '.join(f'{b + 1:02d}' for b in e.world.yard.area[vid])}) 인출 전체 기준 값입니다. "
               f"점수 = 예상 추가 이동 + λ({p['recommender']['lambda']}) × 공급 지연(분)이며, 모든 대안에 같은 표본 200개를 씁니다.")

    # ── 판단 ───────────────────────────────────────
    @st.dialog("판단 기록")
    def decide_dialog(rec_id: str, kind: str):
        r = e.recs[rec_id]
        why2 = e.expiry_reason(r)
        st.markdown(f'<div class="ss-callout">재확인: 현재 작업계획 <b class="mono">v{e.plan_version[r.vessel_id]}</b> '
                    f'(추천 기준 v{r.plan_version}) · 최신 ETA 발행 <b class="mono">{hhmm((e.latest_eta(r.cntr_no) or r.eta).issued_at)}</b> '
                    f'(추천 기준 {hhmm(r.data_as_of)})</div>', unsafe_allow_html=True)
        if why2:
            st.markdown(f'<div class="ss-callout danger">{esc(why2)}</div>', unsafe_allow_html=True)
            if st.button("만료 · 다시 계산", type="primary", use_container_width=True):
                e.decide(rec_id, "승인", None, "기타")   # 승인 차단을 기록
                e.create_rec(r.cntr_no)
                st.rerun()
            return
        opt_key = None
        if kind == "다른 대안":
            others = [o.key for o in r.options]
            opt_key = st.radio("선택할 대안", others, horizontal=True,
                               format_func=lambda k: f"{k} · {next(o for o in r.options if o.key == k).slot_name}")
        else:
            st.write(f"{'승인할' if kind == '승인' else '보류 후 적용할'} 대안: **{r.recommended if kind == '승인' else 'A'}**")
        code = st.selectbox("사유 코드", REASON_CODES, index=4 if kind == "승인" else 0)
        memo = st.text_input("메모(선택)")
        if st.button(f"{kind} 확정", type="primary", use_container_width=True):
            ok, msg = e.decide(rec_id, kind, opt_key, code, memo, st.session_state.opened.get(r.cntr_no))
            if ok:
                st.session_state.decided_wall = st.session_state.get("decided_wall", {})
                st.session_state.decided_wall[rec_id] = time.time()
                st.rerun()
            else:
                st.error(msg)

    can = role() == "야드 플래너"
    open_ = rec.status in ("추천됨", "보류") and not expired
    b1, b2, b3 = st.columns(3)
    if b1.button("승인", type="primary", use_container_width=True, disabled=not (can and open_)):
        decide_dialog(rec.rec_id, "승인")
    if b2.button("보류", use_container_width=True, disabled=not (can and open_)):
        decide_dialog(rec.rec_id, "보류")
    if b3.button("다른 대안 선택", use_container_width=True, disabled=not (can and open_)):
        decide_dialog(rec.rec_id, "다른 대안")
    if not can:
        st.caption("승인·보류는 야드 플래너 역할에서 할 수 있습니다.")
    elif d:
        st.markdown(f'<div class="ss-callout">{hhmm(d.decided_at)}에 <b>{esc(d.decision)}</b> · 대안 {esc(d.chosen_option)} · '
                    f'{esc(d.reason_code)}로 기록했습니다. 올컨e 미리보기에서 하차 안내를 확인할 수 있습니다.</div>',
                    unsafe_allow_html=True)

section("대안 비교", "점 = 예상 추가 이동 기대값 · 수염 = 표본 P10~P90")
fig = go.Figure()
xs = [f"{o.key} · {o.label}" for o in rec.options]
for o, x in zip(rec.options, xs):
    col = C["primary"] if o.key in (show_rec, chosen) else C["base"]
    fig.add_scatter(x=[x], y=[o.total_mean], mode="markers", marker=dict(size=14, color=col, line=dict(color=C["bg"], width=2)),
                    error_y=dict(type="data", symmetric=False, array=[max(0, o.total_p90 - o.total_mean)],
                                 arrayminus=[max(0, o.total_mean - o.total_p10)], color=col, thickness=2, width=8),
                    showlegend=False, hovertemplate=f"대안 {o.key}<br>기대값 {o.total_mean:.2f}회<br>P10~P90 {o.total_p10:.0f}~{o.total_p90:.0f}회<br>"
                                                    f"선작업 {o.pm_mean:.0f} + 재취급 {o.reloc_mean:.2f}<extra></extra>")
    fig.add_annotation(x=x, y=o.total_p90, text=f"{o.total_mean:.1f}회" + ("" if o.key == "A" else f" ({o.diff_mean:+.1f})"),
                       showarrow=False, yshift=16, font=dict(color=C["text"], size=12, family="JetBrains Mono"))
ymax = max(o.total_p90 for o in rec.options)
fig.update_layout(yaxis=dict(title="예상 추가 이동(회)", range=[0, ymax * 1.25 + 1]), xaxis=dict(tickfont=dict(family="Pretendard")))
chart(fig, 300, key="cmp")
footer()
