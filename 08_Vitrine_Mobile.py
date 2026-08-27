# ==============================================================================
# NOME DO SCRIPT: 08_Vitrine_Mobile.py
# DESCRICAO: Hub Mobile Oficial da Esteira de Expedição J&F Co. (Nuvem 24/7)
# FUNCAO: Conexão direta com a Esteira de Separação, Conferência e Etiquetas
# STATUS: ATIVO
# VERSAO: 2.1
# DATA: 26/08/2026
# AUTOR: Violino (000)
# ==============================================================================

import os
import sys
from pathlib import Path

_RAIZ = Path(__file__).parent.resolve()
sys.path.insert(0, str(_RAIZ))
sys.path.insert(0, str(_RAIZ / "pages"))
os.chdir(str(_RAIZ))

import streamlit as st

st.set_page_config(
    page_title="Esteira Expedição — J&F Co.",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# -----------------------------------------------------------------------------
# CAMADA DE SEGURANÇA J&F CO. (PIN 2026)
# -----------------------------------------------------------------------------
if "autenticado_esteira" not in st.session_state:
    st.session_state["autenticado_esteira"] = False

if not st.session_state["autenticado_esteira"]:
    st.markdown(
        """
        <div style="background: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%);
                    border: 1px solid #4338ca; border-radius: 12px; padding: 20px;
                    text-align: center; margin: 20px auto; max-width: 450px;">
            <h2 style="color: #f8fafc; margin: 0; font-size: 1.5rem;">📦 Expedição J&F Co.</h2>
            <p style="color: #94a3b8; margin-top: 6px; font-size: 0.9rem;">Acesso Seguro — Nuvem 24/7</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pin = st.text_input("🔑 Digite o PIN de Acesso:", type="password", placeholder="PIN de 4 dígitos")
        if st.button("🔓 ENTRAR NA ESTEIRA", use_container_width=True, type="primary"):
            if pin == "2026":
                st.session_state["autenticado_esteira"] = True
                st.rerun()
            else:
                st.error("❌ PIN incorreto. Acesso negado.")
    st.stop()

# -----------------------------------------------------------------------------
# EXECUTA A ESTEIRA DE EXPEDIÇÃO OFICIAL
# -----------------------------------------------------------------------------
pagina_esteira = _RAIZ / "pages" / "17_Lista_Separacao.py"

if pagina_esteira.exists():
    with open(pagina_esteira, "r", encoding="utf-8") as f:
        codigo = f.read()
    exec(codigo, globals())
else:
    st.error("Arquivo da Esteira não encontrado.")
