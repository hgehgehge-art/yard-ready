"""④ 결과 안내 — 올컨e 기사 화면 미리보기."""
import streamlit as st

from core.models import hhmm
from ui.components import badge, esc, footer, section
from ui.state import engine, selected, topbar

e = engine()
topbar("올컨e 기사 화면", "기존 예약·운송 진행 정보에 승인된 하차 지점만 더해 보여주는 미리보기입니다.")

opts = list(dict.fromkeys(e.world.focus + [selected()]))
c = st.selectbox("운송 건", opts, index=opts.index(selected()), format_func=lambda x: f"{x} · 트럭 {e.world.bookings[x].truck_id}")
b = e.world.bookings[c]
rec = e.rec_for(c)
d = e.decision_for(rec.rec_id) if rec else None
arrived = e.has_arrived(c)

# 운송 진행: 지금 시각까지 지난 지점만
eta = e.latest_eta(c)
steps = []
names = [nm for nm, _ in b.checkpoints]
if c in e.world.scripted:
    # 시나리오 고정 건: 최신 ETA 발행의 '마지막 통과' 지점까지 지난 것으로 표시
    note = str(eta.inputs.get("마지막 통과", "")) if eta is not None else ""
    idx = max([i for i, nm in enumerate(names) if note.startswith(nm)], default=-1)
    for i, nm in enumerate(names):
        done = i <= idx or arrived
        steps.append((nm, hhmm(eta.issued_at) if i == idx else ("통과" if done else "예정"), done))
else:
    for nm, t in b.checkpoints:
        steps.append((nm, hhmm(t) if t <= e.now else "예정", t <= e.now))
steps.append(("터미널 게이트", hhmm(e.world.arrivals[c].gate_in) if arrived else "예정", arrived))

drop = None
if d and d.decision in ("승인", "다른 대안"):
    opt = next(o for o in rec.options if o.key == d.chosen_option)
    if opt.stack is not None:
        drop = (e.world.yard.lane(opt.stack[0]), d.decided_at)
pos = e.world.yard.find(c)

if drop:
    drop_html = (f'<div class="card drop"><div class="lbl">하차 지점 안내 · {hhmm(drop[1])} 발행</div>'
                 f'<div class="big">{esc(drop[0])}으로 진입해 주세요</div>'
                 f'<div class="lbl">게이트 통과 후 표지판을 따라 이동해 주세요.</div></div>')
elif arrived and pos:
    drop_html = (f'<div class="card drop"><div class="lbl">하차 지점</div><div class="big">{esc(e.world.yard.lane(pos[0]))}</div>'
                 f'<div class="lbl">하차를 마쳤습니다.</div></div>')
else:
    drop_html = ('<div class="card"><div class="lbl">하차 지점</div><div class="big" style="color:var(--muted);font-size:1rem">'
                 '하차 지점은 게이트 통과 후 안내해 드립니다.</div></div>')
exc_html = ""
if rec and rec.exception and not rec.exception_cleared:
    exc_html = ('<div class="card" style="border-color:rgba(255,181,71,.6)"><div class="lbl" style="color:var(--warn)">반입 마감 확인</div>'
                '<div style="font-size:.82rem">반입 마감 시각을 넘길 수 있어 터미널에서 확인 중입니다. 확인 결과는 이 화면으로 알려 드립니다.</div></div>')

l, r = st.columns([1, 1.1], gap="large")
with l:
    st.markdown(badge("미리보기 · 연계 협의 필요", "warn"), unsafe_allow_html=True)
    st.markdown(f"""
<div class="ss-phone" style="margin-top:.6rem"><div class="screen"><div class="notch"></div>
<div class="yr-app">
  <div class="bar"><span class="brand">올컨<span>e</span> 미리보기</span><span class="mono" style="font-size:.78rem;color:var(--muted)">{hhmm(e.now)}</span></div>
  <div class="card"><div class="lbl">반입 예약</div><div class="big mono">{hhmm(b.booking_time)}</div>
    <div class="lbl">{esc(b.terminal)} · 컨테이너 {esc(c)} · 트럭 {esc(b.truck_id)}</div></div>
  {drop_html}{exc_html}
  <div class="card"><div class="lbl">운송 진행</div><ul class="steps">
    {''.join(f'<li class="{"" if dn else "todo"}"><span>{"●" if dn else "○"} {esc(nm)}</span><span class="t">{esc(tt)}</span></li>' for nm, tt, dn in steps)}
  </ul></div>
</div></div></div>""", unsafe_allow_html=True)
with r:
    section("화면에 보여주는 것과 숨기는 것", "역할별 정보 제한")
    st.markdown("""
<div class="ss-card" style="font-size:.86rem;line-height:1.75">
<b style="color:var(--safe)">보여줍니다</b><br>
· 예약시각과 운송 진행 상태(출발·구간 통과)<br>
· 플래너가 승인한 <b>하차 지점</b>(블록·레인)과 안내 발행 시각<br><br>
<b style="color:var(--danger)">보여주지 않습니다</b><br>
· 적치 슬롯 번호(예: A1-03-4-2)와 단 정보<br>
· 야드 전체 현황, 다른 컨테이너 정보, 추천 점수<br><br>
<span style="color:var(--muted)">올컨e에 반입 예약 기능을 새로 넣는 것이 아니라, 기존 예약·운송 진행 정보에 확정된 하차 지점 한 줄을 더하는 연계를 가정합니다.
플래너가 승인하기 전에는 "하차 지점은 게이트 통과 후 안내해 드립니다"를 보여줍니다.</span>
</div>""", unsafe_allow_html=True)
    if rec:
        st.caption(f"추천 상태: {rec.status} · 기사 화면에는 상태 대신 하차 지점만 보입니다.")
footer()
