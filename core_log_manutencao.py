# ==============================================================================
# NOME DO SCRIPT: core_log_manutencao.py
# DESCRICAO: Captura e persiste notas de operacao, erros e snapshots da tela
# FUNCAO: Permite ao operador registrar bugs/melhorias gravando todo o contexto
#         da esteira para analise posterior por IA (Violino / Claude)
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 28/08/2026
# AUTOR: Violino (000)
# ==============================================================================

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_db")
DB_PATH = os.path.join(_DIR, "manutencao_snapshots.db")
JSONL_PATH = os.path.join(_DIR, "manutencao_snapshots.jsonl")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs_manutencao (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    data_hora     TEXT NOT NULL,
    tipo          TEXT NOT NULL,
    nota_usuario  TEXT NOT NULL,
    fase_atual    TEXT,
    onda_travada  TEXT,
    total_erros   INTEGER DEFAULT 0,
    erros_json    TEXT,
    snapshot_json TEXT,
    criado_em     TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_logs_tipo ON logs_manutencao(tipo);
CREATE INDEX IF NOT EXISTS idx_logs_data ON logs_manutencao(data_hora);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    try:
        with _conn() as c:
            c.executescript(_SCHEMA)
    except sqlite3.Error as e:
        log.error("Falha ao inicializar banco de logs de manutencao: %s", e)


def extrair_resumo_seguro(obj: Any, profundidade: int = 0) -> Any:
    """Sanitiza objetos para JSON sem expor tokens ou estourar memoria."""
    if profundidade > 3:
        return "<profundidade_maxima>"
    if obj is None or isinstance(obj, (bool, int, float, str)):
        if isinstance(obj, str) and len(obj) > 1000:
            return obj[:1000] + "... [truncado]"
        return obj
    if isinstance(obj, (list, tuple, set)):
        return [extrair_resumo_seguro(x, profundidade + 1) for x in list(obj)[:50]]
    if isinstance(obj, dict):
        res = {}
        for k, v in obj.items():
            sk = str(k).lower()
            if any(s in sk for s in ["token", "secret", "password", "senha", "key", "auth"]):
                res[k] = "<token_protegido>"
            else:
                res[k] = extrair_resumo_seguro(v, profundidade + 1)
        return res
    return str(obj)[:200]


def capturar_snapshot(session_state: Any, nota: str, tipo: str = "Erro / Bug") -> dict[str, Any]:
    """Extrai todas as informacoes da tela e monta o registro completo."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    # Captura informacoes de estado
    fase_atual = session_state.get("fase_atual")
    onda_travada = session_state.get("onda_travada")
    erros = session_state.get("erros", [])
    ultimo_sync = session_state.get("ultimo_sync", {})
    dados_sep = session_state.get("dados_separacao", {})
    cruzamento = session_state.get("resultado_cruzamento", {})

    # Resumo da fila
    resumo_fila = {}
    if isinstance(dados_sep, dict):
        resumo_fila = {
            "simples_1un": len(dados_sep.get("pedidos_simples_1un", [])),
            "simples_multi_un": len(dados_sep.get("pedidos_simples_multi_un", [])),
            "multi_itens": len(dados_sep.get("pedidos_multi_itens", [])),
            "total_pedidos": len(dados_sep.get("pedidos_simples_1un", []))
                             + len(dados_sep.get("pedidos_simples_multi_un", []))
                             + len(dados_sep.get("pedidos_multi_itens", [])),
            "total_atomos_coleta": len(dados_sep.get("lista_coleta", [])),
        }

    # Resumo do sync
    resumo_sync = {}
    if isinstance(ultimo_sync, dict):
        resumo_sync = {
            "total_pedidos": ultimo_sync.get("total", 0),
            "novos": ultimo_sync.get("novos", 0),
            "do_cache": ultimo_sync.get("do_cache", 0),
            "baixados": ultimo_sync.get("baixados", 0),
            "sairam": ultimo_sync.get("sairam", 0),
            "falhas": len(ultimo_sync.get("falhas", [])),
            "falhas_ids": ultimo_sync.get("falhas", [])[:20],
            "segundos": ultimo_sync.get("segundos", 0),
        }

    # Snapshot geral das chaves da sessao (sanitizadas)
    chaves_sessao = {}
    for k in list(session_state.keys()):
        if k not in ["autenticado_esteira"]:
            chaves_sessao[k] = extrair_resumo_seguro(session_state[k])

    registro = {
        "data_hora": agora,
        "tipo": tipo,
        "nota_usuario": nota.strip(),
        "fase_atual": str(fase_atual),
        "onda_travada": str(onda_travada) if onda_travada is not None else "Fila livre",
        "total_erros": len(erros),
        "erros": extrair_resumo_seguro(erros),
        "resumo_fila": resumo_fila,
        "resumo_sync": resumo_sync,
        "divergencias_cruzamento": extrair_resumo_seguro(cruzamento.get("divergencias", [])) if isinstance(cruzamento, dict) else [],
        "chaves_sessao": chaves_sessao,
    }
    return registro


def salvar_log(registro: dict[str, Any]) -> bool:
    """Grava o registro no SQLite local e em formato JSONL legivel para IAs."""
    init_db()
    try:
        with _conn() as c:
            c.execute(
                """
                INSERT INTO logs_manutencao (
                    data_hora, tipo, nota_usuario, fase_atual, onda_travada,
                    total_erros, erros_json, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    registro.get("data_hora"),
                    registro.get("tipo"),
                    registro.get("nota_usuario"),
                    str(registro.get("fase_atual")),
                    str(registro.get("onda_travada")),
                    int(registro.get("total_erros", 0)),
                    json.dumps(registro.get("erros", []), ensure_ascii=False),
                    json.dumps(registro, ensure_ascii=False),
                ),
            )
        
        # Salva append-only em JSONL para facilitar leitura direta por ferramentas CLI/Violino
        try:
            with open(JSONL_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        except Exception as err_file:
            log.warning("Nao foi possivel escrever no JSONL: %s", err_file)

        return True
    except sqlite3.Error as e:
        log.error("Erro ao salvar log de manutencao: %s", e)
        return False


def listar_logs(limite: int = 15) -> list[dict[str, Any]]:
    """Devolve os ultimos logs gravados."""
    init_db()
    try:
        with _conn() as c:
            rows = c.execute(
                """
                SELECT id, data_hora, tipo, nota_usuario, fase_atual, onda_travada,
                       total_erros, erros_json, snapshot_json, criado_em
                FROM logs_manutencao
                ORDER BY id DESC
                LIMIT ?
                """,
                (limite,),
            ).fetchall()
            res = []
            for r in rows:
                item = dict(r)
                try:
                    item["snapshot"] = json.loads(item["snapshot_json"]) if item["snapshot_json"] else {}
                except Exception:
                    item["snapshot"] = {}
                res.append(item)
            return res
    except sqlite3.Error as e:
        log.error("Erro ao listar logs de manutencao: %s", e)
        return []
