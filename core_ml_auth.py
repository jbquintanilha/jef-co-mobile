# ==============================================================================
# NOME DO SCRIPT: core_ml_auth.py
# DESCRICAO: Biblioteca principal de funcoes/classes core.
# FUNCAO: 
# STATUS: PENDENTE_REVISAO
# MOTOR: Monge (003)
# VERSAO: 1.0
# DATA: 16/05/2026
# AUTOR: Violino (000)
# ==============================================================================

import os
import json
import time
import threading
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

APP_ID       = os.getenv("ML_APP_ID")
SECRET_KEY   = os.getenv("ML_SECRET_KEY")
REDIRECT_URI = os.getenv("ML_REDIRECT_URI", "http://localhost:5000/callback")
TOKEN_FILE   = os.path.join(os.path.dirname(__file__), 'local_db', 'ml_tokens.json')

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

_RENOVACAO_MARGEM_SEG = 1800  # renova quando restar menos de 30 min


# ── Supabase ─────────────────────────────────────────────────────────────────

def _sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

def _salvar_supabase(tokens: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    payload = {
        "id": "mercadolibre",
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in"),
        "user_id": str(tokens.get("user_id", "")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/ml_tokens",
            headers=_sb_headers(),
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 201):
            print("[AUTH] Tokens sincronizados no Supabase.")
        else:
            print(f"[AUTH] Supabase erro {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"[AUTH] Supabase indisponivel: {e}")

def _carregar_supabase() -> dict | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/ml_tokens?id=eq.mercadolibre&select=*",
            headers=_sb_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            rows = r.json()
            if rows:
                return rows[0]
    except Exception as e:
        print(f"[AUTH] Supabase indisponivel ao carregar: {e}")
    return None


# ── Arquivo local ─────────────────────────────────────────────────────────────

def _salvar_local(tokens: dict):
    with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(tokens, f, indent=4)

def _carregar_local() -> dict | None:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


# ── Persistência unificada ────────────────────────────────────────────────────

def salvar_tokens(tokens: dict):
    tokens["salvo_em"] = time.time()
    _salvar_local(tokens)
    _salvar_supabase(tokens)
    print("[AUTH] Tokens persistidos (local + Supabase).")

def carregar_tokens() -> dict | None:
    tokens = _carregar_supabase()
    if tokens:
        _salvar_local(tokens)
        return tokens
    return _carregar_local()


# ── Lógica de expiração ───────────────────────────────────────────────────────

def _token_precisa_renovar(tokens: dict) -> bool:
    salvo_em   = tokens.get("salvo_em", 0)
    expires_in = tokens.get("expires_in", 0)
    if not salvo_em or not expires_in:
        return True
    restam = (salvo_em + expires_in) - time.time()
    print(f"[AUTH] Token expira em {int(restam / 60)} min.")
    return restam < _RENOVACAO_MARGEM_SEG


# ── Renovação ─────────────────────────────────────────────────────────────────

def atualizar_token() -> str | None:
    tokens = carregar_tokens()
    if not tokens or 'refresh_token' not in tokens:
        print("[AUTH] Refresh Token nao encontrado. Requer login inicial.")
        return None

    r = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            'grant_type':    'refresh_token',
            'client_id':     APP_ID,
            'client_secret': SECRET_KEY,
            'refresh_token': tokens['refresh_token'],
        },
        headers={
            'accept':       'application/json',
            'content-type': 'application/x-www-form-urlencoded',
        },
        timeout=15,
    )

    if r.status_code == 200:
        novos = r.json()
        salvar_tokens(novos)
        print("[AUTH] Token renovado com sucesso.")
        return novos['access_token']
    print(f"[AUTH] Erro ao renovar token: {r.text}")
    return None


def get_access_token() -> str | None:
    """Renova se necessário e retorna o token. USO EXCLUSIVO DO AGENDADOR."""
    tokens = carregar_tokens()
    if not tokens:
        print("[AUTH] Nenhum token encontrado. Execute --init.")
        return None
    if _token_precisa_renovar(tokens):
        return atualizar_token()
    return tokens.get('access_token')

def get_token() -> str | None:
    """Leitura pura do token — para usar nos scripts. Nunca renova."""
    tokens = carregar_tokens()
    if not tokens:
        print("[AUTH] Token nao encontrado. Agendador nao iniciado?")
        return None
    return tokens.get('access_token')


# ── Agendador automático ──────────────────────────────────────────────────────

_agendador_ativo = False

def _loop_renovacao(intervalo_seg: int):
    global _agendador_ativo
    while _agendador_ativo:
        print(f"[AGENDADOR] Verificando token...")
        get_access_token()
        time.sleep(intervalo_seg)

def iniciar_agendador(intervalo_horas: float = 5.0):
    global _agendador_ativo
    if _agendador_ativo:
        print("[AGENDADOR] Ja esta rodando.")
        return
    _agendador_ativo = True
    t = threading.Thread(
        target=_loop_renovacao,
        args=(int(intervalo_horas * 3600),),
        daemon=True,
    )
    t.start()
    print(f"[AGENDADOR] Renovacao automatica iniciada (a cada {intervalo_horas}h).")

def parar_agendador():
    global _agendador_ativo
    _agendador_ativo = False
    print("[AGENDADOR] Parado.")


# ── Login inicial ─────────────────────────────────────────────────────────────

def gerar_url_autorizacao():
    url = (
        f"https://auth.mercadolivre.com.br/authorization"
        f"?response_type=code&client_id={APP_ID}&redirect_uri={REDIRECT_URI}"
    )
    print("\n[AUTH] CLIQUE NO LINK ABAIXO PARA AUTORIZAR:")
    print(url)
    print("\nApos autorizar, copie o codigo da URL (apos '?code=') e use --exchange <codigo>.")
    return url

def obter_primeiro_token(code: str) -> bool:
    r = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            'grant_type':    'authorization_code',
            'client_id':     APP_ID,
            'client_secret': SECRET_KEY,
            'code':          code,
            'redirect_uri':  REDIRECT_URI,
        },
        headers={
            'accept':       'application/json',
            'content-type': 'application/x-www-form-urlencoded',
        },
        timeout=15,
    )
    if r.status_code == 200:
        salvar_tokens(r.json())
        print("[AUTH] Primeiro token obtido e salvo.")
        return True
    print(f"[AUTH] Erro ao obter token inicial: {r.text}")
    return False


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if not APP_ID or not SECRET_KEY:
        print("[AVISO] ML_APP_ID ou ML_SECRET_KEY nao configurados no .env")
        sys.exit(1)

    if "--init" in sys.argv:
        gerar_url_autorizacao()
    elif "--exchange" in sys.argv:
        idx = sys.argv.index("--exchange")
        if idx + 1 < len(sys.argv):
            obter_primeiro_token(sys.argv[idx + 1])
    elif "--renovar" in sys.argv:
        atualizar_token()
    elif "--status" in sys.argv:
        t = get_access_token()
        if t:
            print(f"[AUTH] Token ativo: {t[:30]}...")
    else:
        print("[AUTH] Comandos: --init | --exchange <code> | --renovar | --status")
