"""야드 2D 단면(필수)과 3D 보조 뷰(StowSync Mesh3d 방식)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import INF_GROUP, Yard
from core.relocation import blockers_above
from ui.components import C, FILLER_COLOR, GROUP_COLORS

GAP = 1.4   # 베이 사이 간격(칸)


def _gcol(g: int) -> str:
    return GROUP_COLORS.get(g, FILLER_COLOR)


def bay_sections(yard: Yard, vessel_id: str, groups: dict, target: str | None = None, current: set | None = None,
                 candidates: set | None = None, recommended=None, ineligible: dict | None = None,
                 reserved: dict | None = None, height: int = 400, per_row: int = 2) -> go.Figure:
    """선박 구역의 베이별 단면(로우 × 단). 가려진 컨테이너도 모두 보입니다.

    current: 지금 실제로 놓인 컨테이너 집합. 여기에 없는 칸은 '예상'으로 보고 점선·반투명으로 그립니다.
    """
    bays = yard.area[vessel_id]
    shapes, tx, ty, tt, hx, hy, ht = [], [], [], [], [], [], []
    ann = []
    gx = groups.get(target, INF_GROUP) if target else None
    tpos = yard.find(target) if target else None
    n_rows_fig = (len(bays) + per_row - 1) // per_row
    band = yard.max_tier + 1.6
    for bi, b in enumerate(bays):
        x0 = (bi % per_row) * (yard.rows + GAP)
        y0 = (n_rows_fig - 1 - bi // per_row) * band
        ann.append(dict(x=x0 + yard.rows / 2 - .5, y=y0 - 0.95, text=f"<b>{yard.block}-{b + 1:02d}</b> <span style='color:#8BA3C7'>베이</span>",
                        showarrow=False, font=dict(family="JetBrains Mono", color=C["primary"], size=12)))
        for t in range(yard.max_tier):
            ann.append(dict(x=x0 - .75, y=y0 + t + .5, text=f"{t + 1}", showarrow=False, font=dict(color=C["muted"], size=9)))
        for r in range(yard.rows):
            ann.append(dict(x=x0 + r, y=y0 - 0.45, text=f"R{r + 1}", showarrow=False, font=dict(color=C["muted"], size=9)))
            st = yard.stacks[(b, r)]
            for t in range(yard.max_tier):
                xa, xb, ya, yb = x0 + r - .45, x0 + r + .45, y0 + t + .05, y0 + t + .95
                if t < len(st):
                    c = st[t]
                    g = groups.get(c, INF_GROUP)
                    is_new = current is not None and c not in current
                    line = dict(color="rgba(10,22,40,.9)", width=1)
                    if is_new:
                        line = dict(color=C["muted"], width=1.4, dash="dot")
                    if c == target:
                        line = dict(color="#FFFFFF", width=3)
                    elif tpos and (b, r) == (tpos[0], tpos[1]) and t > tpos[2] and gx is not None and gx < g < INF_GROUP:
                        line = dict(color=C["danger"], width=2.5)
                    shapes.append(dict(type="rect", x0=xa, x1=xb, y0=ya, y1=yb, fillcolor=_gcol(g), line=line,
                                       opacity=.5 if is_new else 1, layer="below"))
                    lab = c[-4:] if g < INF_GROUP else "····"
                    gl = f"G{g}" if g < INF_GROUP else "선반입"
                    tx.append(x0 + r)
                    ty.append(y0 + t + .5)
                    tt.append(f"<b>{lab}</b><br><span style='font-size:9px'>{gl}</span>")
                    blk = blockers_above(st, t, groups) if g < INF_GROUP else 0
                    hx.append(x0 + r)
                    hy.append(y0 + t + .5)
                    ht.append(f"<b>{c}</b>{' · 대상' if c == target else ''}{' · 예상' if is_new else ''}<br>"
                              f"슬롯 {yard.slot_name(b, r, t)}<br>"
                              + (f"인출 그룹 {g}<br>위에 늦은 그룹 {blk}개" if g < INF_GROUP else "다음 항차 선반입(이번 인출 없음)"))
                elif t == len(st):
                    s = (b, r)
                    name = yard.slot_name(b, r, t)
                    if recommended is not None and s == tuple(recommended):
                        shapes.append(dict(type="rect", x0=xa, x1=xb, y0=ya, y1=yb, fillcolor="rgba(0,212,255,.22)",
                                           line=dict(color=C["primary"], width=2.5)))
                        tx.append(x0 + r); ty.append(y0 + t + .5); tt.append("<b>추천</b>")
                        hx.append(x0 + r); hy.append(y0 + t + .5); ht.append(f"<b>{name}</b><br>선택한 대안의 적치 슬롯")
                    elif reserved and s in reserved:
                        shapes.append(dict(type="rect", x0=xa, x1=xb, y0=ya, y1=yb, fillcolor="rgba(46,230,166,.12)",
                                           line=dict(color=C["safe"], width=1.5, dash="dash")))
                        tx.append(x0 + r); ty.append(y0 + t + .5); tt.append("예약")
                        hx.append(x0 + r); hy.append(y0 + t + .5); ht.append(f"<b>{name}</b><br>{reserved[s]} 예약 칸")
                    elif candidates and s in candidates:
                        shapes.append(dict(type="rect", x0=xa, x1=xb, y0=ya, y1=yb, fillcolor="rgba(0,212,255,.05)",
                                           line=dict(color=C["primary"], width=1.4, dash="dash")))
                        hx.append(x0 + r); hy.append(y0 + t + .5); ht.append(f"<b>{name}</b><br>제약을 만족하는 후보 슬롯")
                    elif ineligible and s in ineligible:
                        shapes.append(dict(type="rect", x0=xa, x1=xb, y0=ya, y1=yb, fillcolor="rgba(139,163,199,.10)",
                                           line=dict(color="rgba(139,163,199,.45)", width=1)))
                        xm, ym = (xa + xb) / 2, (ya + yb) / 2
                        for (p0, p1) in (((xa, ya), (xb, yb)), ((xa, ym), (xm, yb)), ((xm, ya), (xb, ym))):
                            shapes.append(dict(type="line", x0=p0[0], y0=p0[1], x1=p1[0], y1=p1[1],
                                               line=dict(color="rgba(139,163,199,.45)", width=1)))
                        hx.append(x0 + r); hy.append(y0 + t + .5)
                        ht.append(f"<b>{name}</b> · 제외<br>" + "<br>".join(ineligible[s]))
                    else:
                        shapes.append(dict(type="rect", x0=xa, x1=xb, y0=ya, y1=yb, fillcolor="rgba(0,0,0,0)",
                                           line=dict(color="rgba(30,58,95,.55)", width=1)))
                else:
                    shapes.append(dict(type="rect", x0=xa, x1=xb, y0=ya, y1=yb, fillcolor="rgba(0,0,0,0)",
                                       line=dict(color="rgba(30,58,95,.35)", width=1)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hx, y=hy, mode="markers", marker=dict(size=26, symbol="square", opacity=0),
                             hovertext=ht, hoverinfo="text", showlegend=False))
    fig.add_trace(go.Scatter(x=tx, y=ty, mode="text", text=tt, hoverinfo="skip", showlegend=False,
                             textfont=dict(family="JetBrains Mono", color="#F2F6FC", size=11)))
    w = min(per_row, len(bays)) * (yard.rows + GAP) - GAP
    fig.update_layout(shapes=shapes, annotations=ann, height=height, margin=dict(l=8, r=8, t=8, b=10),
                      xaxis=dict(visible=False, range=[-1.1, w - .3], fixedrange=True),
                      yaxis=dict(visible=False, range=[-1.3, n_rows_fig * band - 1.4], fixedrange=True),
                      hovermode="closest")
    return fig


# ── 3D (StowSync Mesh3d 방식) ─────────────────────────
_CX = np.array([0, 0, 1, 1, 0, 0, 1, 1])
_CY = np.array([0, 1, 1, 0, 0, 1, 1, 0])
_CZ = np.array([0, 0, 0, 0, 1, 1, 1, 1])
_I = [7, 0, 0, 0, 4, 4, 6, 6, 4, 0, 3, 2]
_J = [3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 3]
_K = [0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6]
_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
BAY_P, ROW_P, TIER_H, LEN, WID, HGT = 3.1, 1.4, 1.3, 2.85, 1.22, 1.18


def yard_3d(yard: Yard, vessel_id: str, groups: dict, target: str | None = None, current: set | None = None,
            height: int = 480) -> go.Figure:
    layers = {"now": dict(X=[], Y=[], Z=[], I=[], J=[], K=[], FC=[], T=[], n=0),
              "new": dict(X=[], Y=[], Z=[], I=[], J=[], K=[], FC=[], T=[], n=0)}
    ex, ey, ez = [], [], []
    for bi, b in enumerate(yard.area[vessel_id]):
        for r in range(yard.rows):
            for t, c in enumerate(yard.stacks[(b, r)]):
                L = layers["new" if current is not None and c not in current else "now"]
                vx = bi * BAY_P + _CX * LEN
                vy = r * ROW_P + _CY * WID
                vz = t * TIER_H + _CZ * HGT
                L["X"] += vx.tolist(); L["Y"] += vy.tolist(); L["Z"] += vz.tolist()
                L["I"] += [i + L["n"] for i in _I]; L["J"] += [j + L["n"] for j in _J]; L["K"] += [k + L["n"] for k in _K]
                g = groups.get(c, INF_GROUP)
                col = "#FFFFFF" if c == target else (GROUP_COLORS.get(g, FILLER_COLOR))
                L["FC"] += [col] * 12
                L["T"] += [f"<b>{c}</b><br>{yard.slot_name(b, r, t)}<br>" + (f"그룹 {g}" if g < INF_GROUP else "선반입")] * 8
                L["n"] += 8
                for a, d in _EDGES:
                    ex += [vx[a], vx[d], None]; ey += [vy[a], vy[d], None]; ez += [vz[a], vz[d], None]
    fig = go.Figure()
    for key, op in (("now", 1.0), ("new", .45)):
        L = layers[key]
        if L["X"]:
            fig.add_trace(go.Mesh3d(x=L["X"], y=L["Y"], z=L["Z"], i=L["I"], j=L["J"], k=L["K"], facecolor=L["FC"],
                                    flatshading=True, opacity=op, text=L["T"], hovertemplate="%{text}<extra></extra>",
                                    lighting=dict(ambient=.62, diffuse=.75, specular=.25, roughness=.6),
                                    lightposition=dict(x=100, y=-200, z=300)))
    fig.add_trace(go.Scatter3d(x=ex, y=ey, z=ez, mode="lines", line=dict(color="rgba(10,22,40,.85)", width=2),
                               hoverinfo="skip", showlegend=False))
    axis = dict(visible=False, showbackground=False)
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      scene=dict(xaxis=axis, yaxis=axis, zaxis=dict(axis, range=[0, yard.max_tier * TIER_H + .5]),
                                 aspectmode="data", bgcolor="rgba(0,0,0,0)",
                                 camera=dict(eye=dict(x=-1.3, y=-1.6, z=1.0))),
                      uirevision="yard3d")
    return fig
