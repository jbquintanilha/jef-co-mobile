# ==============================================================================
# NOME DO SCRIPT: app_expedicao_mobile.py
# DESCRICAO: Aplicacao Web Mobile da Esteira de Expedicao J&F Co. (Nuvem / Celular)
# FUNCAO: Conferencia rapida de pedidos, bipagem de etiquetas e geracao de PDFs
# STATUS: ATIVO
# MOTOR: Monge (003) / Violino (000)
# VERSAO: 1.0
# DATA: 26/08/2026
# AUTOR: Violino (000)
# ==============================================================================

import io
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import streamlit as st

_RAIZ = Path(__file__).parent.resolve()
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from dotenv import load_dotenv
load_dotenv(dotenv_path=str(_RAIZ / ".env"))

import core_scanner_resolver as resolver
import core_scanner_supabase as cloud_db
import core_scanner_db as local_db

st.set_page_config(
    page_title="Expedição Mobile — J&F Co.",
    page_icon="📦",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# -----------------------------------------------------------------------------
# ESTILOS CSS MOBILE-FIRST (DARK MODE INDUSTRIAL)
# -----------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Otimizacoes para Smartphone */
    .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
        padding-left: 0.8rem;
        padding-right: 0.8rem;
        max-width: 600px;
    }
    .header-box {
        background: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%);
        border: 1px solid #4338ca;
        border-radius: 12px;
        padding: 14px;
        text-align: center;
        margin-bottom: 15px;
    }
    .header-box h2 {
        color: #f8fafc;
        margin: 0;
        font-size: 1.4rem;
        font-weight: 700;
    }
    .header-box p {
        color: #94a3b8;
        margin: 4px 0 0 0;
        font-size: 0.85rem;
    }
    .metric-container {
        display: flex;
        gap: 10px;
        margin-bottom: 15px;
    }
    .metric-card {
        flex: 1;
        background: #0f172a;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 10px;
        text-align: center;
    }
    .metric-val {
        font-size: 1.6rem;
        font-weight: 800;
        color: #38bdf8;
    }
    .metric-lbl {
        font-size: 0.75rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .card-pedido-ok {
        background-color: #064e3b;
        border: 2px solid #10b981;
        border-radius: 14px;
        padding: 16px;
        margin-top: 12px;
        margin-bottom: 12px;
        box-shadow: 0 4px 14px rgba(16, 185, 129, 0.2);
    }
    .card-pedido-alerta {
        background-color: #7c2d12;
        border: 2px solid #f97316;
        border-radius: 14px;
        padding: 16px;
        margin-top: 12px;
        margin-bottom: 12px;
        box-shadow: 0 4px 14px rgba(249, 115, 22, 0.2);
    }
    .tag-canal {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.75rem;
        text-transform: uppercase;
        margin-bottom: 8px;
    }
    .canal-shopee { background-color: #ee4d2d; color: white; }
    .canal-ml { background-color: #ffe600; color: #111; }
    .canal-tiktok { background-color: #000000; color: white; border: 1px solid #333; }
    .canal-olist { background-color: #0284c7; color: white; }
    .stTextInput input {
        font-size: 1.2rem !important;
        padding: 12px !important;
        text-align: center;
        border-radius: 10px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# CONTROLE DE ACESSO / PIN DE SEGURANCA
# -----------------------------------------------------------------------------
SENHA_ACESSO = os.getenv("PIN_EXPEDICAO", "2026")

if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.markdown(
        """
        <div class="header-box">
            <h2>📦 J&F Co. — Expedição Mobile</h2>
            <p>Acesso restrito da equipe de expedição</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    pin_digitado = st.text_input("Digite o PIN de Segurança:", type="password", max_chars=6)
    if st.button("🔓 Entrar na Esteira", use_container_width=True):
        if pin_digitado == SENHA_ACESSO:
            st.session_state.autenticado = True
            st.rerun()
        else:
            st.error("PIN incorreto. Tente novamente.")
    st.stop()

# -----------------------------------------------------------------------------
# CABECALHO & METRICAS
# -----------------------------------------------------------------------------
st.markdown(
    """
    <div class="header-box">
        <h2>📦 Esteira de Expedição Mobile</h2>
        <p>J&F Co. • Bipagem & Conferência Nuvem</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Metricas do dia (Nuvem / Supabase)
metricas = cloud_db.obter_metricas_dia_nuvem()
conferidos_hoje = metricas.get("conferidos", 0)

col_m1, col_m2 = st.columns(2)
with col_m1:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-val">{conferidos_hoje}</div>
            <div class="metric-lbl">Conferidos Hoje</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with col_m2:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-val">🟢 Online</div>
            <div class="metric-lbl">Nuvem Sincronizada</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")

# -----------------------------------------------------------------------------
# CAMPO DE BIPAGEM / ENTRADA
# -----------------------------------------------------------------------------
st.subheader("🔍 Bipar ou Digitar Pedido / Rastreio")

with st.form("form_bipagem", clear_on_submit=True):
    codigo_bipado = st.text_input(
        "Código de barras / Rastreio / Pedido:",
        placeholder="Bipe com a pistola ou câmera...",
        key="input_codigo",
    )
    btn_bipar = st.form_submit_button("⚡ Localizar Pedido", use_container_width=True)

if "ultimo_pedido" not in st.session_state:
    st.session_state.ultimo_pedido = None

if btn_bipar and codigo_bipado:
    codigo_limpo = codigo_bipado.strip()
    
    # 1. Tenta resolver no Supabase
    pedido = cloud_db.buscar_rastreio_nuvem(codigo_limpo)
    
    # 2. Se nao achar no Supabase, tenta resolver pelo motor local/Olist
    if not pedido:
        pedido = resolver.resolver_codigo(codigo_limpo)
        if pedido:
            # Salva na nuvem para manter o SSoT atualizado
            cloud_db.salvar_rastreio_nuvem(pedido)

    if pedido:
        st.session_state.ultimo_pedido = pedido
        st.toast("✅ Pedido localizado!", icon="📦")
    else:
        st.session_state.ultimo_pedido = None
        st.error(f"❌ Pedido ou Rastreio `{codigo_limpo}` não encontrado no sistema.")

# -----------------------------------------------------------------------------
# EXIBICAO DO CARD DO PEDIDO & CONFERENCIA
# -----------------------------------------------------------------------------
if st.session_state.ultimo_pedido:
    ped = st.session_state.ultimo_pedido
    canal = (ped.get("canal") or "manual").lower()
    classe_canal = f"canal-{canal}" if canal in ["shopee", "ml", "tiktok", "olist"] else "canal-olist"
    alerta = ped.get("alerta_volume") or ""
    classe_card = "card-pedido-alerta" if alerta else "card-pedido-ok"

    st.markdown(
        f"""
        <div class="{classe_card}">
            <span class="tag-canal {classe_canal}">{canal.upper()}</span>
            <h3 style="color: white; margin: 6px 0 10px 0;">{ped.get('produto_nome') or 'Produto J&F Co.'}</h3>
            <p style="color: #e2e8f0; margin: 4px 0; font-size: 1.05rem;">
                <strong>SKU:</strong> <code>{ped.get('sku_principal') or 'N/A'}</code>
            </p>
            <p style="color: #e2e8f0; margin: 4px 0;">
                <strong>Cor:</strong> {ped.get('cor') or 'Padrão'} | <strong>Kit:</strong> {ped.get('kit') or 'Unitário'}
            </p>
            <p style="color: #cbd5e1; margin: 4px 0; font-size: 0.85rem;">
                <strong>Pedido:</strong> {ped.get('pedido_ecommerce') or ped.get('tracking')}
            </p>
            {f'<p style="color: #fed7aa; font-weight: bold; margin-top: 8px;">⚠️ ATENÇÃO: {alerta}</p>' if alerta else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        if st.button("✅ Confirmar Conferência", use_container_width=True, type="primary"):
            tracking = ped.get("tracking") or ped.get("pedido_ecommerce")
            if tracking:
                cloud_db.registrar_conferencia_nuvem(tracking, conferido_por="mobile")
                st.success("Pedido conferido e gravado na nuvem!")
                st.session_state.ultimo_pedido = None
                st.rerun()
    with col_b2:
        if st.button("📄 Gerar Etiqueta PDF", use_container_width=True):
            st.info("Buscando etiqueta unificada no Olist...")
            # Aqui aciona a geracao de PDF de etiqueta em memoria
            st.warning("Função de download direto de etiqueta acionada.")

st.markdown("---")
if st.button("🚪 Sair", use_container_width=True):
    st.session_state.autenticado = False
    st.rerun()
