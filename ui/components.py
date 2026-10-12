"""공통 UI: 테마 주입, 상단 바(시뮬레이션 시계), KPI 카드, 상태 칩, 2x2 배지, Plotly 템플릿."""
from __future__ import annotations

import html
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent

C = {
    "bg": "#0A1628", "surface": "#111F38", "border": "#1E3A5F", "primary": "#00D4FF", "safe": "#2EE6A6",
    "warn": "#FFB547", "danger": "#FF4D6D", "observed": "#C9B6FF", "text": "#E6EDF7", "muted": "#8BA3C7",
    "base": "#8193AD",
}
# 인출 그룹 색(어두운 표면에서 밝기 밴드·색각이상 분리 검증 통과). 칸 안에 그룹 번호를 함께 적어 색만으로 구분하지 않음
GROUP_COLORS = {1: "#3D7FE6", 2: "#1E9E8F", 3: "#B98328", 4: "#8670D6"}
FILLER_COLOR = "#22324B"
POLICY_COLORS = {"baseline": C["base"], "proposed": C["primary"]}

STATUS_CLASS = {"추천됨": "rec", "보류": "hold", "승인": "ok", "다른 대안 선택": "alt", "만료": "exp",
                "실행 완료": "done", "예외 검토": "exc", "미검토": "none"}
QUAD_TIP = "상황 설명용 분류이며 위치를 고정하지 않습니다."


# ── 테마 ──────────────────────────────────────────────
def inject_theme():
    css = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
    st.html(f"<style>{css}</style>")
    register_plotly_template()


def register_plotly_template():
    if "yardready" in pio.templates:
        pio.templates.default = "yardready"
        return
    t = go.layout.Template()
    ax = dict(gridcolor=C["border"], linecolor=C["border"], zerolinecolor=C["border"], tickcolor=C["border"],
              tickfont=dict(color=C["muted"], family="JetBrains Mono, monospace", size=11),
              title=dict(font=dict(color=C["muted"], size=12)))
    t.layout = go.Layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Pretendard Variable, Pretendard, Apple SD Gothic Neo, sans-serif", color=C["text"], size=13),
        colorway=[GROUP_COLORS[i] for i in (1, 2, 3, 4)], xaxis=ax, yaxis=ax,
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=C["muted"], size=12), orientation="h", y=1.1, x=0),
        hoverlabel=dict(bgcolor=C["surface"], bordercolor=C["primary"],
                        font=dict(family="JetBrains Mono, monospace", color=C["text"], size=12)),
        margin=dict(l=48, r=18, t=36, b=40), title=dict(font=dict(size=14, color=C["text"]), x=0.01), bargap=0.25)
    pio.templates["yardready"] = t
    pio.templates.default = "yardready"


PLOTLY_CONFIG = {"displayModeBar": False, "displaylogo": False}


def chart(fig, height=None, key=None):
    if height:
        fig.update_layout(height=height)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG, key=key)


# ── 작은 조각 ────────────────────────────────────────
def esc(x) -> str:
    return html.escape(str(x))


def badge(text: str, kind: str = "primary") -> str:
    return f'<span class="ss-badge {kind}">{esc(text)}</span>'


def chip(status: str) -> str:
    return f'<span class="yr-chip {STATUS_CLASS.get(status, "none")}">{esc(status)}</span>'


def ver_chip(text: str) -> str:
    return f'<span class="yr-chip ver">{esc(text)}</span>'


def quad(arr: str, ship: str) -> str:
    cls = {"지연": "late", "조기": "early"}.get(arr, "")
    return f'<span class="yr-quad {cls}" data-tip="{QUAD_TIP}" title="{QUAD_TIP}">{esc(arr)} · 선적 {esc(ship)}</span>'


def section(title: str, hint: str = ""):
    st.markdown(f'<div class="ss-section"><h3>{esc(title)}</h3><span class="hint">{esc(hint)}</span></div>',
                unsafe_allow_html=True)


def callout(text_html: str, kind: str = ""):
    st.markdown(f'<div class="ss-callout {kind}">{text_html}</div>', unsafe_allow_html=True)


def legend(items: list[tuple[str, str]], dashed: set | None = None):
    parts = []
    for t, c in items:
        style = f"background:{c}"
        if dashed and t in dashed:
            style = f"background:transparent;border:1.5px dashed {c}"
        parts.append(f'<span><i style="{style}"></i>{esc(t)}</span>')
    st.markdown('<div class="ss-legend">' + "".join(parts) + "</div>", unsafe_allow_html=True)


def kpi_card(label: str, value: str, unit: str = "", sub: str = "", accent: str = C["primary"], right: str = "",
             delay: float = 0) -> str:
    return (f'<div class="ss-card ss-kpi" style="--accent:{accent};animation-delay:{delay:.2f}s;min-height:128px">'
            f'<div class="label"><span>{esc(label)}</span>{right}</div>'
            f'<div class="value">{value}<span class="unit">{esc(unit)}</span></div>'
            f'<div class="sub">{sub}</div></div>')


def kpi_row(cards: list[str]):
    st.markdown('<div class="ss-kpi-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def footer():
    st.markdown('<div class="yr-foot"><span class="dot"></span>합성 데이터 기반 시연입니다. 화면의 시각·수치는 시드를 고정한 '
                '가정값에서 계산한 결과이며 부산항 실제 성과가 아닙니다.</div>', unsafe_allow_html=True)
