# ==============================================================================
# NOME DO SCRIPT: core_dados.py
# DESCRICAO: Biblioteca principal de funcoes/classes core.
# FUNCAO: 
# STATUS: PENDENTE_REVISAO
# MOTOR: Monge (003)
# VERSAO: 1.0
# DATA: 16/05/2026
# AUTOR: Violino (000)
# ==============================================================================

import pandas as pd
import os
import sqlite3
import re
import requests
from functools import lru_cache
from config import DB_PAI, DB_SKU, DB_FORM, DB_DRIVE_MAP

# ============================================================================
# CORE DADOS — SUPABASE COMO FONTE ÚNICA DA VERDADE (Mandato 2026-04-26)
# CSVs locais são gerados como cache temporário, nunca como fonte primária.
# ============================================================================

def _supabase_headers():
    from dotenv import load_dotenv
    load_dotenv()
    url = os.getenv('SUPABASE_URL', '')
    key = (os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_SECRET_KEY')
           or os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_ANON_KEY') or '')
    return url, {'apikey': key, 'Authorization': f'Bearer {key}'}


def _fetch_supabase_spus():
    """Busca todos os SPUs do Supabase e retorna como DataFrame."""
    url, headers = _supabase_headers()
    if not url:
        return pd.DataFrame()
    try:
        r = requests.get(
            f'{url}/rest/v1/spus?select=*&limit=500',
            headers=headers, timeout=15
        )
        if not r.ok:
            return pd.DataFrame()
        data = r.json()
        df = pd.DataFrame(data)
        # Normalizar nomes de colunas para compatibilidade com o dashboard
        rename_map = {
            'spu': 'SPU',
            'ref': 'REF',
            'fornecedor': 'FORNECEDOR',
            'categoria': 'CATEGORIA',
            'subcategoria': 'SUBCATEGORIA',
            'composicao': 'COMPOSICAO',
            'ncm': 'NCM',
            'cest': 'CEST',
            'titulo_seo': 'TITULO_SEO',
            'desc_marketing': 'DESC_MARKETING',
            'preco_custo': 'PRECO_CUSTO',
            'preco_venda': 'PRECO_VENDA',
            'tags': 'TAGS',
            'status': 'STATUS',
            'updated_at': 'UPDATED_AT',
            'material': 'MATERIAL',
            'cor_predominante': 'COR_PREDOMINANTE',
            'foto_capa_id': 'FOTO_CAPA_ID',
            'pasta_drive_id': 'PASTA_DRIVE_ID',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        return df.astype(str).replace('None', '').replace('nan', '')
    except Exception as e:
        print(f"[DADOS] Erro ao buscar SPUs do Supabase: {e}")
        return pd.DataFrame()


def _fetch_supabase_skus():
    """Busca todos os SKUs do Supabase e retorna como DataFrame."""
    url, headers = _supabase_headers()
    if not url:
        return pd.DataFrame()
    try:
        r = requests.get(
            f'{url}/rest/v1/skus?select=*&limit=2000',
            headers=headers, timeout=15
        )
        if not r.ok:
            return pd.DataFrame()
        data = r.json()
        df = pd.DataFrame(data)
        rename_map = {
            'sku': 'SKU',
            'spu': 'SPU',
            'tamanho': 'TAMANHO',
            'cor_especifica': 'COR_ESPECIFICA',
            'estoque': 'ESTOQUE',
            'preco_custo': 'PRECO_CUSTO',
            'preco_venda': 'PRECO_VENDA',
            'peso_g': 'PESO_G',
            'status': 'STATUS',
            'updated_at': 'UPDATED_AT',
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        df = df.astype(str).replace('None', '').replace('nan', '')
        # Mandamento 9: SKU físico nunca tem sufixo de anúncio/kit — filtrar contaminação
        col_sku = 'SKU' if 'SKU' in df.columns else 'sku'
        if col_sku in df.columns:
            mask_kit = df[col_sku].str.contains(r'_KIT|_\dUN|KITMIX|KITDUO', case=False, na=False, regex=True)
            removidos = df[mask_kit][col_sku].tolist()
            if removidos:
                print(f"[DADOS] ⚠️ Filtro defensivo removeu {len(removidos)} SKUs de anúncio de df_sku: {removidos}")
            df = df[~mask_kit]
        return df
    except Exception as e:
        print(f"[DADOS] Erro ao buscar SKUs do Supabase: {e}")
        return pd.DataFrame()


@lru_cache(maxsize=1)
def ler_ficha_rica():
    """Lê o db_form.csv (Google Forms) e limpa as refs para busca rápida."""
    if not os.path.exists(DB_FORM):
        return pd.DataFrame()

    try:
        df = pd.read_csv(DB_FORM, dtype=str)
        if not df.empty and 'Código / Referência na Etiqueta' in df.columns:
            df['REF_LIMPA'] = df['Código / Referência na Etiqueta'].astype(str).str.strip().str.upper().apply(
                lambda x: re.sub(r'[^A-Z0-9]', '', x.replace('.0', ''))
            )
        return df
    except Exception as e:
        print(f"[DADOS] Erro ao ler Ficha Rica: {e}")
        return pd.DataFrame()


def get_db_connection():
    """Retorna uma conexão com o SQLite local (cache temporário)."""
    db_path = 'local_db/banco_jf.db'
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def limpar_cache_dados():
    """Limpa o cache do LRU para forçar recarga."""
    ler_ficha_rica.cache_clear()
    print("[DADOS] Cache de memória limpo.")


def carregar_dados_legacy():
    """
    Retorna (df_pai, df_sku, df_vazio, df_form).
    FONTE: Supabase (mandato arquitetural 2026-04-26).
    Fallback: CSVs locais se Supabase indisponível.
    """
    df_pai = _fetch_supabase_spus()
    df_sku = _fetch_supabase_skus()

    # Fallback para CSVs locais se Supabase falhar
    if df_pai.empty:
        print("[DADOS] Supabase indisponível — usando CSV local como fallback temporário")
        if os.path.exists(DB_PAI):
            df_pai = pd.read_csv(DB_PAI, dtype=str)
            df_pai.columns = [c.upper() for c in df_pai.columns]
        else:
            df_pai = pd.DataFrame()
            
    if df_sku.empty:
        print("[DADOS] Supabase indisponível — usando CSV SKU local como fallback temporário")
        if os.path.exists(DB_SKU):
            df_sku = pd.read_csv(DB_SKU, dtype=str)
            df_sku.columns = [c.upper() for c in df_sku.columns]
            
            # Mandamento 9: SKU físico nunca tem sufixo de anúncio/kit — filtrar contaminação
            if 'SKU' in df_sku.columns:
                mask_kit = df_sku['SKU'].str.contains(r'_KIT|_\dUN|KITMIX|KITDUO', case=False, na=False, regex=True)
                removidos = df_sku[mask_kit]['SKU'].tolist()
                if removidos:
                    print(f"[DADOS] ⚠️ Filtro defensivo removeu {len(removidos)} SKUs de anúncio de df_sku local: {removidos}")
                df_sku = df_sku[~mask_kit]
        else:
            df_sku = pd.DataFrame()

    df_form = ler_ficha_rica()
    return df_pai, df_sku, pd.DataFrame(), df_form
