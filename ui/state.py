"""캐시된 모델·설정과 세션(엔진) 상태, 사이드바, 상단 바."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.engine import Engine
from core.eta import train_and_validate
from core.experiment import CACHE
from core.generator import generate_history, load_params, load_scenarios
from core.models import hhmm
from ui.components import ROOT, chip, esc, ver_chip

ROLES = ["야드 플래너", "관제 담당", "운송기사"]


@st.cache_resource(show_spinner="가정값을 불러오는 중입니다…")
def get_params() -> dict:
    return load_params()


@st.cache_resource(show_spinner=False)
def get_scenarios() -> dict:
    return load_scenarios()


@st.cache_resource(show_spinner="합성 이력 60일로 ETA 모델을 학습하고 검증하는 중입니다…")
def get_eta() -> dict:
    p = get_params()
    hist = generate_history(p)
    return train_and_validate(hist, p)


@st.cache_data(show_spinner=False)
def get_grid() -> pd.DataFrame | None:
    if CACHE.exists():
        return pd.read_csv(CACHE)
    return None


# ── 엔진 세션 ─────────────────────────────────────────
def _new_engine(key: str) -> Engine:
    e = Engine(get_params(), get_scenarios(), key, get_eta()["serving"])
    e.step()   # 첫 사건까지 재생해 시연 시작 화면을 맞춤
    return e


def engine() -> Engine:
    key = st.session_state.get("scenario", "S1")
    if st.session_state.get("engine_key") != key or "engine" not in st.session_state:
        with st.spinner("가상 야드를 만들고 사건을 재생하는 중입니다…"):
            st.session_state.engine = _new_engine(key)
        st.session_state.engine_key = key
        st.session_state.snapshots = []
        st.session_state.sel_cntr = st.session_state.engine.world.focus[0]
        st.session_state.alert = st.session_state.engine.last_alert
        st.session_state.opened = {}
    return st.session_state.engine


def step_next():
    e = engine()
    if e.done:
        return
    st.session_state.snapshots.append(e.snapshot())
    with st.spinner("다음 사건을 재생하는 중입니다…"):
        e.step()
    st.session_state.alert = e.last_alert


def step_back():
    if st.session_state.get("snapshots"):
        st.session_state.engine = st.session_state.snapshots.pop()
        st.session_state.alert = None


def reset():
    st.session_state.pop("engine", None)
    st.session_state.pop("engine_key", None)


def selected() -> str:
    e = engine()
    c = st.session_state.get("sel_cntr") or e.world.focus[0]
    return c if c in e.world.bookings else e.world.focus[0]


def role() -> str:
    return st.session_state.get("role", ROLES[0])


# ── 사이드바 ─────────────────────────────────────────
def sidebar():
    p = get_params()
    st.logo(str(ROOT / "assets" / "logo.svg"), size="large", icon_image=str(ROOT / "assets" / "icon.svg"))
    sc = get_scenarios()
    with st.sidebar:
        st.markdown(f'<div class="ss-logo"><div class="tag">{esc(p["tagline"])}</div></div>', unsafe_allow_html=True)
        st.selectbox("역할", ROLES, key="role")
        keys = list(sc)
        st.selectbox("시나리오", keys, key="scenario", format_func=lambda k: f"{k} · {sc[k]['title']}", on_change=reset)


def sidebar_controls():
    e = engine()
    with st.sidebar:
        st.markdown('<div style="font-size:.78rem;color:var(--muted);letter-spacing:.05em;margin:.6rem 0 .2rem">시간 조작</div>',
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.button("◀ 이전", use_container_width=True, on_click=step_back, disabled=not st.session_state.get("snapshots"),
                  help="이전 사건 시점으로 되돌립니다.")
        c2.button("▶ 다음", use_container_width=True, on_click=step_next, disabled=e.done, type="primary",
                  help="다음 사건을 재생합니다.")
        c3.button("↺ 처음", use_container_width=True, on_click=reset, help="시나리오를 처음부터 다시 재생합니다.")
        nxt = e.next_event
        txt = (f"다음 사건 <b class='mono'>{hhmm(nxt.t)}</b> · {esc(_event_name(nxt.type))}" if nxt else "모든 사건을 재생했습니다.")
        st.markdown(f'<div style="font-size:.76rem;color:var(--muted);margin-top:.3rem">{txt}</div>', unsafe_allow_html=True)
        st.markdown('<div class="ss-side-foot"><span class="dot"></span><b>합성 데이터 기반 시연</b><br>'
                    '시드를 고정한 합성 데이터입니다. 가정값은 <code>config/params.yaml</code>에 있습니다.</div>',
                    unsafe_allow_html=True)


def _event_name(t: str) -> str:
    return {"eta_update": "ETA 갱신", "gate_in": "게이트 도착", "plan_change": "작업계획 변경", "urgent_job": "긴급 작업",
            "retrieval_start": "인출 시작"}.get(t, t)


# ── 상단 바 ──────────────────────────────────────────
def topbar(title: str, desc: str):
    e = engine()
    p = get_params()
    focus_v = e.vessel_of(selected())
    issued = [x.issued_at for c in e.world.bookings for x in e.eta_history(c)[-1:]]
    fresh = e.now - max(issued) if issued else 0
    sc = get_scenarios()[e.key]
    st.markdown(f"""
<div class="yr-top">
  <div class="l">
    <div class="eyebrow"><span class="live"></span>{esc(p['app_name'])} · {esc(e.key)} {esc(sc['title'])}</div>
    <h1>{esc(title)}</h1>
    <p>{esc(desc)}</p>
  </div>
  <div class="r">
    <div class="clock"><small>시뮬레이션 시각</small>{hhmm(e.now)}</div>
    <div class="chips">{ver_chip(f"작업계획 v{e.plan_version[focus_v]}")}{ver_chip(f"{fresh:.0f}분 전 갱신")}</div>
  </div>
</div>""", unsafe_allow_html=True)
    a = st.session_state.get("alert")
    if a:
        kind = "danger" if "작업계획" in a else ""
        st.markdown(f'<div class="yr-alert {kind}"><b>{hhmm(e.now)}</b> 새 사건 · {esc(a)}</div>', unsafe_allow_html=True)
        st.session_state.alert = None


def restricted(page_role_ok: tuple[str, ...]) -> bool:
    """역할별 정보 제한. 막히면 True."""
    if role() not in page_role_ok:
        st.markdown('<div class="ss-callout warn">운송기사 역할에서는 올컨e 기사 화면만 볼 수 있습니다. '
                    '사이드바에서 역할을 바꾸거나 올컨e 기사 화면으로 이동해 주세요.</div>', unsafe_allow_html=True)
        return True
    return False
