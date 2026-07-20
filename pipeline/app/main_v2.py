"""원본 앱 파일을 수정하지 않는 legal-rag v2 Streamlit 진입점."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "app"
for path in (str(ROOT), str(APP_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import streamlit as st
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

V2_ENV_KEYS = (
    "OPENAI_API_KEY",
    "DB_URL",
    "LEGAL_RAG_V2_MODEL_ID",
    "LEGAL_RAG_V2_MODEL_REVISION",
    "LEGAL_RAG_V2_EXPERIMENT_ID",
    "LEGAL_RAG_V2_DEVICE",
    "LEGAL_RAG_V2_GRADE_WEAK",
    "LEGAL_RAG_V2_GRADE_STRONG",
)
try:
    for key in V2_ENV_KEYS:
        if key in st.secrets and st.secrets[key] not in (None, ""):
            os.environ[key] = str(st.secrets[key])
except Exception:
    pass

from pipeline.app.bootstrap import bootstrap_graph

bootstrap_graph()

from ui import render_logo, render_sidebar_footer

st.set_page_config(
    page_title="전·월세 분쟁 팩트체커",
    page_icon="⚖️",
    layout="centered",
    initial_sidebar_state="expanded",
)

pages = [
    st.Page(str(APP_DIR / "pages/landing.py"), title="메인", icon="🏠", default=True),
    st.Page(str(APP_DIR / "pages/pre.py"), title="계약 전 (예방)", icon="🔍"),
    st.Page(str(APP_DIR / "pages/post.py"), title="계약 후 (분쟁)", icon="⚖️"),
]
page_group = st.navigation(pages, position="hidden")

with st.sidebar:
    render_logo(width=240)
    st.markdown(
        """
        <style>
        [data-testid="stSidebarUserContent"] [data-testid="stCaptionContainer"] p {
            margin-bottom: 0;
        }
        [data-testid="stSidebarUserContent"] [data-testid="stPageLink-NavLink"] p {
            font-size: 20px !important;
            font-weight: bold !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.divider()
    for page in pages:
        st.page_link(page)
    render_sidebar_footer()

page_group.run()
