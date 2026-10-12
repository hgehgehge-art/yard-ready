"""데이터·가정·한계."""
import pandas as pd
import streamlit as st

from ui.components import ROOT, esc, footer, section
from ui.state import get_params, restricted, topbar

topbar("데이터·가정·한계", "기획서 2.4의 데이터 6종을 실제 확보 방안과 데모 대체 데이터로 나눠 정리합니다.")
if restricted(("야드 플래너", "관제 담당")):
    footer()
    st.stop()

section("데이터", "실제 확보 방안 ↔ 데모에서 쓴 대체 데이터")
rows = [
    ("예약·운송 진행", "반입 예약시각, 트럭·운송사, 출발·구간 통과 시각", "올컨e 기존 예약·운송 진행 정보 연계(협의 필요)",
     "운송사 6곳(+신규 1곳)의 편차 성향을 둔 합성 예약 · 구간 통과 4지점"),
    ("실제 도착 이력", "ETA 모델 학습·검증(게이트 도착, CY 하차 가능, 게이트 아웃)", "터미널 게이트 기록(TOS)",
     "60일 합성 이력 · 앞 45일 학습, 뒤 15일 검증(시간순)"),
    ("야드 현황", "슬롯별 적치 상태, 예약 칸, 장치율", "TOS 야드 현황 조회",
     "블록 1개(베이 8 × 로우 6 × 단 5), 선박별 베이 배정, 선반입 컨테이너"),
    ("선박 일정", "선박 작업 시간대, 반입 마감", "BPA Port-i 선석 일정",
     "Port-i(모의) 2척 · 개별 컨테이너 선적 순서 판단에는 쓰지 않음"),
    ("개별 작업계획", "인출 그룹·순번, 크레인 작업량, 계획 버전", "TOS 작업계획(선사·터미널 협의)",
     "TOS 모의 작업계획 v1~v4 · 그룹 4개 · 15분 슬롯 작업량"),
    ("실행·판단 결과", "승인·보류·다른 대안 사유, 실제 이동", "본 모듈 기록 + TOS 작업 실적",
     "이 시연 세션의 판단 기록과 합성 실행 결과"),
]
st.dataframe(pd.DataFrame(rows, columns=["데이터", "쓰임", "실제 확보 방안", "데모에서 쓴 대체 데이터"]), hide_index=True,
             use_container_width=True)

section("처리 구조", "core 모듈 이름을 함께 표시")
steps = [("입력 수집", "generator"), ("정합성 검사", "recommender.guard_inputs"), ("도착 예측", "eta"),
         ("후보 탐색", "constraints · premarshal"), ("유지안 대비 비교", "recommender · relocation"),
         ("관리자 승인", "engine.decide"), ("기존 작업 체계 반영", "engine · 올컨e 미리보기"), ("결과 기록", "audit · metrics")]
html = []
for i, (nm, mod) in enumerate(steps):
    cls = "st human" if nm == "관리자 승인" else "st"
    html.append(f'<div class="{cls}">{esc(nm)}<small>{esc(mod)}</small></div>')
    if i < len(steps) - 1:
        html.append('<span class="ar">→</span>')
st.markdown('<div class="yr-flow">' + "".join(html) + "</div>", unsafe_allow_html=True)

section("단순화와 한계", "데모에서 하지 않은 것")
st.markdown("""
<div class="ss-card" style="font-size:.86rem;line-height:1.8">
· 모든 컨테이너를 같은 규격(40ft) 일반 드라이 FCL로 단순화했습니다. 위험물·냉동·환적과 본선 적재 안전성 계산은 넣지 않았습니다.<br>
· 날씨·교통 예측은 넣지 않았습니다. 확보 가능한 경우에 한해 추가할 수 있습니다.<br>
· 문제는 예약시각과 실제 도착의 편차입니다. 예약 단위(1시간)가 원인이라고 보지 않습니다.<br>
· 신뢰도 백분율은 만들지 않습니다. 예측 구간 폭, 발행 후 경과시간, 합성 환경 검증 MAE만 보여줍니다.<br>
· 2x2(조기·지연 × 선적 상위·하위)는 상황 설명과 검토 목록 정렬에만 쓰고, 슬롯 선택은 몬테카를로 비교로만 정합니다.<br>
· 판단 기록은 모델을 즉시 다시 학습하는 데 쓰지 않습니다. 정기 검증을 통과한 경우에만 추천 기준에 반영할 예정입니다.<br>
· 자동 정책 비교는 속도 때문에 표본 6개·4시간 앞까지만 내다보며, 화면의 개별 추천(표본 200개)과 계산 규모가 다릅니다.<br>
· 기준 정책은 공개한 단순 규칙이며 실제 TOS 성능을 대표하지 않습니다. 합성 결과를 부산항 실제 성과로 해석하면 안 됩니다.
</div>""", unsafe_allow_html=True)

section("참고자료", "링크는 혜림·가은 초안에서 가져왔습니다")
st.markdown("""
- [올컨e 앱 매뉴얼](https://www.chainportal.co.kr/manual/download?filename=ChainPortal_APP_Manual.pdf)
- [BPA, Port-i 항만이용자 전체로 확대 적용 (2026.3.3)](https://busanpa.com/board/view.do?boardId=BBS_0000031&dataSid=34863&menuCd=DOM_000000105002001000)
- [Boschma 외, 2023 — Approximate dynamic programming for container stacking](https://research.utwente.nl/en/publications/approximate-dynamic-programming-for-container-stacking/)
- [Ikuma 외, 2025](https://www.jstage.jst.go.jp/article/jima/75/4E/75_172/_article/-char/en)
""")

section("가정값 전체", "config/params.yaml · 모든 값은 시연용 가정")
st.code((ROOT / "config" / "params.yaml").read_text(encoding="utf-8"), language="yaml")
with st.expander("시나리오 스크립트 (config/scenarios.yaml)"):
    st.code((ROOT / "config" / "scenarios.yaml").read_text(encoding="utf-8"), language="yaml")
footer()
