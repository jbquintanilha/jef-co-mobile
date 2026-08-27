# ==============================================================================
# NOME DO SCRIPT: core_sync.py
# DESCRICAO: Biblioteca principal de funcoes/classes core.
# FUNCAO: 
# STATUS: PENDENTE_REVISAO
# MOTOR: Monge (003)
# VERSAO: 1.0
# DATA: 16/05/2026
# AUTOR: Violino (000)
# ==============================================================================

# ============================================================================
# ARQUIVO: core_sync.py
# VERSAO: 5.1 (PERFORMANCE MANDINGA + PROTEÇÃO ATÔMICA)
# DATA: 18/04/2026
# MOTIVO: Paralelismo de I/O e proteção contra Race Conditions no ecossistema.
# ============================================================================

import os
import pandas as pd
import concurrent.futures
from config import ARQUIVO_CREDENCIAS, PLANILHA_ID, DB_PAI, DB_SKU, DB_FORM
from core_dados import limpar_cache_dados, carregar_dados_legacy

# ---------------------------------------------------------
# CONEXÃO GOOGLE API
# ---------------------------------------------------------
def conectar_sheets():
    """Conecta ao Google Sheets usando credenciais de servico."""
    import gspread
    from google.oauth2.service_account import Credentials
    escopos = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    credenciais = Credentials.from_service_account_file(ARQUIVO_CREDENCIAS, scopes=escopos)
    return gspread.authorize(credenciais).open_by_key(PLANILHA_ID)

# ---------------------------------------------------------
# MOTORES DE SEGURANÇA E PERFORMANCE
# ---------------------------------------------------------
def salvar_csv_atomico(df, caminho_final):
    """
    Salva o arquivo em formato .tmp e depois renomeia nativamente.
    Preve 'Race Conditions' impedindo leitura corrompida.
    """
    caminho_tmp = f"{caminho_final}.tmp"
    df.to_csv(caminho_tmp, index=False)
    # os.replace é uma operacao atomica no sistema operacional
    os.replace(caminho_tmp, caminho_final)

def worker_baixar_aba(planilha, aba_identificador, caminho_destino, log_fn):
    """Download isolado de uma aba para execucao em paralelo."""
    try:
        if isinstance(aba_identificador, int):
            aba = planilha.get_worksheet(aba_identificador)
        else:
            aba = planilha.worksheet(aba_identificador)
            
        df = pd.DataFrame(aba.get_all_records())
        salvar_csv_atomico(df, caminho_destino)
        log_fn(f"[SYNC] [OK] Aba '{aba_identificador}' salva com sucesso (Atomico).")
    except Exception as e:
        log_fn(f"[SYNC] [ERRO] Falha ao baixar aba '{aba_identificador}': {e}")
        raise e

# ---------------------------------------------------------
# FLUXO PRINCIPAL DE SINCRONIZAÇÃO
# ---------------------------------------------------------
def sincronizar_nuvem_para_local(log_fn=None):
    """Sincroniza planilhas do Google Sheets para CSVs locais em paralelo."""
    def log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    log("[SYNC] Iniciando protocolo de Sincronizacao Acelerada (Threaded)...")
    
    try:
        planilha = conectar_sheets()
        log("[SYNC] Conexao com Google Sheets estabelecida.")

        # Mapeamento de tarefas (Aba, Destino)
        tarefas_download = [
            ("PRODUTOS_PAI", DB_PAI),
            ("SKUS_VARIACOES", DB_SKU),
            (0, DB_FORM) # Aba 0 (Respostas Form)
        ]

        # 🧠 CTO FIX: Resolve o Gargalo de I/O disparando os downloads simultaneamente.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futuros = {
                executor.submit(worker_baixar_aba, planilha, aba, destino, log): aba 
                for aba, destino in tarefas_download
            }
            
            for futuro in concurrent.futures.as_completed(futuros):
                # Se alguma thread falhar, vai estourar a excecao aqui
                futuro.result() 

        log("[SYNC] Download e Escrita Atomica concluidos.")

        # Limpa o cache (LRU)
        limpar_cache()
        log("[SYNC] Cache LRU expurgado. Sistema pronto para dados frescos.")

        # Atualizacao do Banco SQL via Motor V2.5
        try:
            from local_db.setup_sqlite import semente_sqlite
            log("[SYNC] Forjando Banco SQLite para o Novo Painel...")
            semente_sqlite()
            log("[SYNC] [OK] SQLite Atualizado com Sucesso!")
        except Exception as e_sql:
            log(f"[SYNC] ⚠️ AVISO: Falha ao semear SQLite. Detalhes: {e_sql}")

        # FASE 5 — Espelhar Sheets → Supabase (fonte unica de verdade)
        try:
            from supabase import create_client
            from dotenv import load_dotenv
            import math
            load_dotenv()
            _sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY"))
            import pandas as _pd

            _df_pai = _pd.read_csv(DB_PAI, dtype=str).fillna("")
            for _, row in _df_pai.iterrows():
                spu = str(row.get("spu", row.get("SPU", ""))).strip()
                if not spu: continue
                _sb.table("spus").upsert({
                    "spu": spu,
                    "ref": row.get("ref",""),
                    "fornecedor": row.get("fornecedor",""),
                    "categoria": row.get("categoria",""),
                    "material": row.get("material",""),
                    "titulo_seo": row.get("titulo_seo",""),
                    "desc_marketing": row.get("desc_marketing",""),
                    "foto_capa_id": row.get("FOTO_CAPA_ID",""),
                    "pasta_drive_id": row.get("PASTA_DRIVE_ID",""),
                }).execute()

            _df_sku = _pd.read_csv(DB_SKU, dtype=str).fillna("")
            for _, row in _df_sku.iterrows():
                sku = str(row.get("sku", row.get("SKU", ""))).strip()
                spu = str(row.get("spu", row.get("SPU", ""))).strip()
                if not sku or not spu: continue
                try:
                    _estoque = int(float(row.get("ESTOQUE", row.get("estoque", 0)) or 0))
                    _custo   = float(row.get("PRECO_CUSTO", row.get("preco_custo", 0)) or 0)
                except (ValueError, TypeError):
                    _estoque, _custo = 0, 0.0
                _sb.table("skus").upsert({
                    "sku": sku, "spu": spu,
                    "tamanho": row.get("TAMANHO", row.get("tamanho","")),
                    "cor_especifica": row.get("COR_ESPECIFICA", row.get("cor_especifica","")),
                    "estoque": _estoque,
                    "preco_custo": _custo,
                }).execute()

            log(f"[SYNC] [OK] Supabase espelhado: {len(_df_pai)} SPUs, {len(_df_sku)} SKUs.")
        except Exception as e_sb:
            log(f"[SYNC] ⚠️ AVISO: Falha ao espelhar Supabase (nao critico): {e_sb}")

    except Exception as e:
        log("\n--- FALHA CRITICA DE SINCRONIZACAO ---")
        log(f"[SYNC] ERRO: {str(e)}")
        log("--------------------------------------\n")

# Funcoes auxiliares
def carregar_banco_local(): return carregar_dados_legacy()
def limpar_cache(): limpar_cache_dados()

def rodar_guardiao_fotos():
    import subprocess
    import sys
    caminho_07 = os.path.join(os.path.dirname(__file__), '07_Organizador_Fotos.py')
    if os.path.exists(caminho_07):
        try:
            subprocess.run([sys.executable, caminho_07], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[ERRO] [ERRO] Guardião de fotos falhou: {e}")

# ============================================================
# FASE 3 — SYNC SUPABASE (Fonte Unica de Verdade)
# Adicionado em 2026-04-26. NAO remover funcoes existentes.
# ============================================================
def sincronizar_supabase_para_local(log_fn=None):
    """Pull Supabase → CSVs locais + SQLite. Fonte canonica."""
    from supabase import create_client
    from dotenv import load_dotenv
    import os as _os, pandas as _pd
    load_dotenv()
    
    url = _os.getenv("SUPABASE_URL")
    key = _os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        if log_fn: log_fn("[SYNC-SB] [ERRO] Credenciais Supabase ausentes.")
        return

    sb = create_client(url, key)

    def log(msg):
        print(msg)
        if log_fn: log_fn(msg)

    log("[SYNC-SB] Iniciando pull Supabase -> local...")
    try:
        # 1. SPUs
        r_spu = sb.table("spus").select("*").execute()
        df_spus = _pd.DataFrame(r_spu.data)
        if df_spus.empty:
            log("[SYNC-SB] [ERRO] Supabase retornou 0 SPUs. Abortando.")
            return

        # Normalizar nomes para o Dashboard (Maiúsculo)
        df_spus.columns = [c.upper() for c in df_spus.columns]
        
        # Mapeamento específico para colunas críticas
        mapeamento_spu = {
            'FOTO_CAPA_ID': 'FOTO_CAPA_ID',
            'PASTA_DRIVE_ID': 'PASTA_DRIVE_ID'
        }
        df_spus = df_spus.rename(columns=mapeamento_spu)
        
        salvar_csv_atomico(df_spus, DB_PAI)
        log(f"[SYNC-SB] [OK] SPUs: {len(df_spus)} -> {os.path.basename(DB_PAI)}")

        # 2. SKUs
        r_sku = sb.table("skus").select("*").execute()
        df_skus = _pd.DataFrame(r_sku.data)
        if df_skus.empty:
            log("[SYNC-SB] [ERRO] Supabase retornou 0 SKUs.")
            return

        # Normalizar nomes para o Dashboard (Maiúsculo)
        df_skus.columns = [c.upper() for c in df_skus.columns]
        
        salvar_csv_atomico(df_skus, DB_SKU)
        log(f"[SYNC-SB] [OK] SKUs: {len(df_skus)} -> {os.path.basename(DB_SKU)}")

        # 3. Kits
        r_kits = sb.table("kits").select("*").execute()
        df_kits = _pd.DataFrame(r_kits.data)
        if not df_kits.empty:
            df_kits.columns = [c.upper() for c in df_kits.columns]
            caminho_kits_raiz   = _os.path.join(_os.path.dirname(DB_PAI), 'db_kits.csv')
            caminho_kits_local  = _os.path.join(_os.path.dirname(DB_PAI), 'local_db', 'db_kits.csv')
            salvar_csv_atomico(df_kits, caminho_kits_raiz)
            salvar_csv_atomico(df_kits, caminho_kits_local)
            log(f"[SYNC-SB] [OK] Kits: {len(df_kits)} -> db_kits.csv (raiz + local_db)")

        # 4. Itens do Kit
        r_itens = sb.table("itens_kit").select("*").execute()
        df_itens = _pd.DataFrame(r_itens.data)
        if not df_itens.empty:
            df_itens.columns = [c.upper() for c in df_itens.columns]
            caminho_itens_raiz  = _os.path.join(_os.path.dirname(DB_PAI), 'db_itens_kit.csv')
            caminho_itens_local = _os.path.join(_os.path.dirname(DB_PAI), 'local_db', 'db_itens_kit.csv')
            salvar_csv_atomico(df_itens, caminho_itens_raiz)
            salvar_csv_atomico(df_itens, caminho_itens_local)
            log(f"[SYNC-SB] [OK] Itens Kit: {len(df_itens)} -> db_itens_kit.csv (raiz + local_db)")

        # 5. Limpar cache + atualizar SQLite
        limpar_cache()
        try:
            from local_db.setup_sqlite import semente_sqlite
            log("[SYNC-SB] Atualizando SQLite...")
            semente_sqlite()
            log("[SYNC-SB] [OK] SQLite atualizado.")
        except Exception as e_sql:
            log(f"[SYNC-SB] AVISO SQLite: {e_sql}")

        log("[SYNC-SB] Concluido. Supabase -> CSV -> SQLite OK.")
    except Exception as e:
        log(f"[SYNC-SB] ERRO CRITICO: {e}")