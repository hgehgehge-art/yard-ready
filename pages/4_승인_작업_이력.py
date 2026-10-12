"""③ 현장 판단 — 승인·작업 이력."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.models import hhmm
from ui.components import C, chart, chip, esc, footer, section
from ui.state import engine, restricted, role, topbar

e = engine()
topbar("승인·작업 이력", "추천, 현장 판단, 실행 결과를 예상과 실제로 나눠 기록합니다.")
if restricted(("야드 플래너", "관제 담당")):
    footer()
    st.stop()

rows = []
for rid in e.rec_order:
    r = e.recs[rid]
    d = e.decision_for(rid)
    status = r.status
    if status in ("추천됨", "보류") and e.expiry_reason(r):
        status = "만료"
    chosen = d.chosen_option if d else r.recommended
    opt = next((o for o in r.options if o.key == chosen), r.options[0])
    act = e.actual.get(rid, {})
    executed = any(m.cntr_no == r.cntr_no and m.kind == "최초 적치" for m in e.moves)
    if executed:
        mv = next(m for m in e.moves if m.cntr_no == r.cntr_no and m.kind == "최초 적치")
        run = f"적치 {mv.to}"
    else:
        run = "도착 전"
    rows.append({"rid": rid, "t": r.created_at, "cntr": r.cntr_no, "rec": r.recommended, "decision": d.decision if d else "—",
                 "chosen": chosen, "reason": (d.reason_code + (f" · {d.memo}" if d.memo else "")) if d else (r.hold_reason if r.status == "보류" else ""),
                 "ver": r.plan_version, "status": status, "run": run, "exp": opt.total_mean, "exp_rng": f"{opt.total_p10:.0f}~{opt.total_p90:.0f}",
                 "act": act.get("total"), "act_pm": act.get("pm", 0), "act_reloc": act.get("reloc"), "exc": r.exception and not r.exception_cleared})

section("이력", "시안 = 추천 시점 예상 · 보라 = 실행 후 관측(구역 전체 기준)")
if not rows:
    st.markdown('<div class="ss-callout">아직 계산한 추천이 없습니다. 통합 관제에서 검토할 컨테이너를 골라 주세요.</div>',
                unsafe_allow_html=True)
else:
    body = []
    for x in rows:
        act = "인출 전" if x["act"] is None else f'{x["act"]:.0f}회 <small style="color:var(--muted)">(선작업 {x["act_pm"]} + 재취급 {x["act_reloc"]})</small>'
        body.append(f'<tr><td class="mono">{hhmm(x["t"])}</td><td class="mono">{esc(x["cntr"])}</td><td>{esc(x["rec"])}</td>'
                    f'<td>{esc(x["decision"])}{" · " + esc(x["chosen"]) if x["decision"] != "—" else ""}</td>'
                    f'<td style="max-width:260px;font-size:.78rem;color:var(--muted)">{esc(x["reason"])}</td>'
                    f'<td class="mono">v{x["ver"]}</td><td>{chip(x["status"])}</td><td style="font-size:.8rem">{esc(x["run"])}</td>'
                    f'<td class="exp">{x["exp"]:.1f}회 <small>({x["exp_rng"]})</small></td><td class="obs">{act}</td></tr>')
    st.markdown('<table class="ss-table"><thead><tr><th>시각</th><th>컨테이너</th><th>추천 대안</th><th>결정</th><th>사유</th>'
                '<th>계획 버전</th><th>상태</th><th>실행</th><th class="num">예상 추가 이동</th><th class="num">실제 추가 이동</th>'
                '</tr></thead><tbody>' + "".join(body) + "</tbody></table>", unsafe_allow_html=True)
    df = pd.DataFrame([{"시각": hhmm(x["t"]), "컨테이너": x["cntr"], "추천 대안": x["rec"], "결정": x["decision"], "선택 대안": x["chosen"],
                        "사유": x["reason"], "계획 버전": f"v{x['ver']}", "상태": x["status"], "실행": x["run"],
                        "예상 추가 이동": round(x["exp"], 2), "예상 P10~P90": x["exp_rng"],
                        "실제 추가 이동": x["act"]} for x in rows])
    st.download_button("이력 CSV 다운로드", df.to_csv(index=False).encode("utf-8-sig"), "yard_ready_history.csv", "text/csv")

    exc = [x for x in rows if x["exc"] and x["decision"] in ("승인", "다른 대안")]
    if exc:
        section("예외 검토", "반입 마감 초과 가능 · 관리자 확인 전에는 완료로 표시하지 않음")
        for x in exc:
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.markdown(f'<div class="ss-callout danger"><b class="mono">{esc(x["cntr"])}</b> · {esc(e.recs[x["rid"]].exception)}</div>',
                        unsafe_allow_html=True)
            if c2.button("예외 검토 완료", key=f"exc_{x['rid']}", disabled=role() != "야드 플래너", use_container_width=True):
                e.clear_exception(x["rid"])
                st.rerun()

l, r = st.columns([1, 1.2])
with l:
    section("선택 사유별 건수", "보류·다른 대안 선택 포함")
    ds = [d for d in e.decisions]
    if ds:
        cnt = pd.Series([d.reason_code for d in ds]).value_counts().sort_values()
        fig = go.Figure(go.Bar(x=cnt.values, y=cnt.index, orientation="h", marker_color=C["primary"], width=.55,
                               hovertemplate="%{y}: %{x}건<extra></extra>"))
        fig.update_layout(xaxis=dict(title="건수", dtick=1), margin=dict(l=170, r=18, t=10, b=40))
        chart(fig, 60 + 46 * len(cnt), key="reasons")
        took = [d.decided_at for d in ds]
        wall = st.session_state.get("decided_wall", {})
        secs = [wall[d.rec_id] - d.opened_at for d in ds if d.opened_at and d.rec_id in wall]
        if secs:
            st.caption(f"의사결정 소요시간(검토 화면 진입 → 확정, 이번 시연 세션 기록): 평균 {sum(secs) / len(secs):.0f}초")
    else:
        st.caption("아직 기록한 판단이 없습니다.")
    st.markdown('<div class="ss-callout">다른 대안 선택 기록은 실제 결과와 함께 축적하고, 정기 검증을 통과한 경우에만 추천 기준에 '
                '반영합니다. 판단 기록으로 모델을 즉시 다시 학습하지 않습니다.</div>', unsafe_allow_html=True)
with r:
    section("실행 기록", "최초 적치 · 선작업 · 재취급 · 정상 인출")
    mv = [m for m in e.moves if m.kind != "정상 인출" or m.cntr_no in e.world.focus]
    if mv:
        st.dataframe(pd.DataFrame([{"시각": hhmm(m.start), "컨테이너": m.cntr_no, "구분": m.kind, "출발": m.frm, "도착": m.to}
                                   for m in mv[-60:]][::-1]), hide_index=True, use_container_width=True, height=300)
    for vid, rt in e.retrieved.items():
        st.markdown(f'<div class="ss-callout"><b>{vid} 인출 결과(관측)</b> · <span class="yr-obs">작업 중 재취급 {rt["relocations"]}회 · '
                    f'선작업 {rt["pm"]}회</span> · 하한 {rt["lb"]}회 · 인출 {rt["n"]}개</div>', unsafe_allow_html=True)

with st.expander("감사 로그(추천 생성·승인·보류·만료·실행)"):
    st.dataframe(e.audit.frame(), hide_index=True, use_container_width=True)
footer()
