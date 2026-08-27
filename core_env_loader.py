# ==============================================================================
# NOME DO SCRIPT: core_env_loader.py
# DESCRICAO: Injetor de Variaveis de Ambiente para a Nuvem J&F Co.
# FUNCAO: Garante que os modulos de API tenham acesso a chaves do Olist, Supabase, TikTok e Shopee
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 27/08/2026
# AUTOR: Violino (000)
# ==============================================================================

import os
import sys

def carregar_ambiente():
    # 1. Se st.secrets estiver presente (Streamlit Cloud), injeta no os.environ
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for k, v in st.secrets.items():
                if isinstance(v, str):
                    os.environ[k] = v
    except Exception:
        pass

    # 2. Carrega do .env local se existir
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

# Executa automaticamente na importação
carregar_ambiente()
