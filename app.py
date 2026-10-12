"""YARD READY — 도착 불확실성을 반영한 CY 배치 지원 데모."""
import streamlit as st

from core.generator import load_params

_P = load_params()
st.set_page_config(page_title=f"{_P['app_name']} · CY 배치 지원", page_icon="assets/icon.svg", layout="wide",
                   initial_sidebar_state="expanded")

from ui.components import inject_theme  # noqa: E402
from ui.state import engine, sidebar, sidebar_controls  # noqa: E402

inject_theme()
sidebar()

pages = {
    "현장 흐름": [
        st.Page("pages/1_통합_관제.py", title="통합 관제", icon=":material/monitoring:", default=True),
        st.Page("pages/2_도착_변경_분석.py", title="도착·변경 분석", icon=":material/schedule:"),
        st.Page("pages/3_CY_배치_대안.py", title="CY 배치 대안", icon=":material/view_in_ar:"),
        st.Page("pages/4_승인_작업_이력.py", title="승인·작업 이력", icon=":material/fact_check:"),
    ],
    "확인": [
        st.Page("pages/5_효과_비교.py", title="효과 비교", icon=":material/compare_arrows:"),
        st.Page("pages/6_올컨e_기사_화면.py", title="올컨e 기사 화면", icon=":material/local_shipping:"),
        st.Page("pages/7_데이터_가정_한계.py", title="데이터·가정·한계", icon=":material/menu_book:"),
    ],
}
nav = st.navigation(pages, position="sidebar")
engine()
sidebar_controls()
nav.run()
