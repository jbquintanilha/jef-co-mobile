# ==============================================================================
# NOME DO SCRIPT: core_env_loader.py
# DESCRICAO: Injetor e resolvedor de credenciais para Streamlit Cloud e Local
# STATUS: ATIVO
# VERSAO: 2.1
# DATA: 27/08/2026
# AUTOR: Violino (000)
# ==============================================================================

import os
import sys
from pathlib import Path

# Credenciais de consulta direta para a esteira em nuvem
_CONFIG_PADRAO = {
    "SUPABASE_URL": "https://wvdzotimchvuvgdugswx.supabase.co",
    "TIKTOK_APP_KEY": "6e9k9j9m",
    "TIKTOK_APP_SECRET": "8f3b2c1a0e9d8c7b6a5",
    "TIKTOK_SHOP_CIPHER": "GCP_BM_123456",
    "TIKTOK_SHOP_ID": "7495827361827",
    "SHOPEE_PARTNER_ID": "118273",
    "SHOPEE_PARTNER_KEY": "shopee_key_prod",
    "SHOPEE_SHOP_ID": "84920183",
}

def get_secret(key: str, default: str = "") -> str:
    # 1. Tenta pegar do os.environ
    val = os.getenv(key)
    if val:
        return val

    # 2. Tenta pegar de st.secrets se estiver rodando no Streamlit
    try:
        import streamlit as st
        if hasattr(st, "secrets") and key in st.secrets:
            v = str(st.secrets[key])
            os.environ[key] = v
            return v
    except Exception:
        pass

    # 3. Fallback padrao
    if key in _CONFIG_PADRAO:
        os.environ[key] = _CONFIG_PADRAO[key]
        return _CONFIG_PADRAO[key]

    return default

def carregar_tudo():
    # Injeta st.secrets se existirem
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for k, v in st.secrets.items():
                if isinstance(v, (str, int, float)):
                    os.environ[k] = str(v)
    except Exception:
        pass

    # Injeta config padrao se faltar
    for k, v in _CONFIG_PADRAO.items():
        if k not in os.environ:
            os.environ[k] = v

    # Carrega .env se existir
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

carregar_tudo()
