# ==============================================================================
# NOME DO SCRIPT: core_ondas_expedicao.py
# DESCRICAO: Marca quais pedidos ja' foram processados em cada ONDA do dia
# FUNCAO: A bancada nem sempre despacha tudo de uma vez. Sem registrar o que
#         ja' saiu, a segunda leva reimprime as etiquetas da primeira.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 25/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Ondas de expedicao — o que ja' foi processado, mesmo ainda pendente no ERP.

## O problema (Jota, 25/08)

    "hoje ele imprime todas... porem as vezes fazemos outra onda... se eu
     clicar em onda salva, entenda q aquele pedidos, mesmo constando como
     pendente de envio ja' foram processados naquela onda... os novos serao
     uma segunda onda"

O Olist so' muda a situacao do pedido quando ele e' de fato despachado. Entre
imprimir a etiqueta e dar baixa passam horas — e nesse meio a lista continua
mostrando tudo como pendente. Quem imprime de novo gasta etiqueta e se perde.

## Como funciona

`salvar_onda(pedidos)` grava o numero de e-commerce de cada pedido com o
numero da onda. A partir dai `marcar(pedidos)` devolve cada pedido com
`onda` preenchido, e a tela mostra o marcador.

**Decisoes do Jota (25/08):**
- Pedido processado **continua visivel**, so' que marcado — nao some da tela.
- A marca **dura ate' o pedido sair do Olist**, nao zera por dia. Se ele
  volta a aparecer pendente semana que vem, continua marcado.

## Por que SQLite e nao session_state

O `session_state` do Streamlit morre quando a aba recarrega. Onda precisa
sobreviver a F5, a reinicio do dashboard e a troca de maquina na bancada.

Uso:
    import core_ondas_expedicao as ondas
    ondas.salvar_onda(pedidos)              # fecha a onda atual
    pedidos = ondas.marcar(pedidos)         # anota `onda` em cada um
    ondas.proxima_onda()                    # numero da proxima
    ondas.desfazer_ultima()                 # tira a marca da ultima onda
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_db")
DB_PATH = os.path.join(_DIR, "ondas_expedicao.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ondas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_ecommerce  TEXT NOT NULL,
    numero_olist      TEXT,
    canal             TEXT,
    onda              INTEGER NOT NULL,
    dia               TEXT NOT NULL,
    criado_em         TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(numero_ecommerce)
);
CREATE INDEX IF NOT EXISTS idx_ondas_onda ON ondas(onda);
CREATE INDEX IF NOT EXISTS idx_ondas_dia  ON ondas(dia);
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
        log.error("Falha ao criar o banco de ondas: %s", e)


def _num(p: dict[str, Any]) -> str:
    return str(p.get("numero_ecommerce") or "").strip().upper()


def proxima_onda() -> int:
    """Numero da proxima onda do DIA. Recomeça em 1 a cada dia."""
    init_db()
    hoje = datetime.now().strftime("%Y-%m-%d")
    try:
        with _conn() as c:
            r = c.execute("SELECT MAX(onda) FROM ondas WHERE dia = ?",
                          (hoje,)).fetchone()
        return int((r[0] or 0)) + 1
    except sqlite3.Error as e:
        log.error("Erro ao ler a proxima onda: %s", e)
        return 1


def salvar_onda(pedidos: list[dict[str, Any]]) -> dict[str, Any]:
    """Fecha uma onda com os pedidos dados. Devolve {"onda", "gravados", ...}.

    Pedido que JA' esta' em outra onda nao e' regravado — a primeira marca
    vale (senao reimprimir moveria o pedido para a onda nova e o historico
    perderia o sentido).
    """
    init_db()
    onda = proxima_onda()
    hoje = datetime.now().strftime("%Y-%m-%d")
    gravados = ja_tinha = 0
    try:
        with _conn() as c:
            for p in pedidos:
                n = _num(p)
                if not n:
                    continue
                r = c.execute("SELECT onda FROM ondas WHERE numero_ecommerce = ?",
                              (n,)).fetchone()
                if r:
                    ja_tinha += 1
                    continue
                c.execute(
                    "INSERT INTO ondas (numero_ecommerce, numero_olist, canal, "
                    "onda, dia) VALUES (?, ?, ?, ?, ?)",
                    (n, str(p.get("numero_olist") or ""),
                     str(p.get("canal") or ""), onda, hoje),
                )
                gravados += 1
    except sqlite3.Error as e:
        log.error("Erro ao salvar a onda: %s", e)
        return {"onda": onda, "gravados": 0, "ja_tinha": 0, "erro": str(e)}

    log.info("Onda %d fechada: %d pedido(s) (%d ja' estavam em onda)",
             onda, gravados, ja_tinha)
    return {"onda": onda, "gravados": gravados, "ja_tinha": ja_tinha}


def mapa() -> dict[str, int]:
    """{numero_ecommerce: onda} de tudo que ja' foi processado."""
    init_db()
    try:
        with _conn() as c:
            return {r["numero_ecommerce"]: r["onda"]
                    for r in c.execute("SELECT numero_ecommerce, onda FROM ondas")}
    except sqlite3.Error as e:
        log.error("Erro ao ler o mapa de ondas: %s", e)
        return {}


def marcar(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anota `onda` em cada pedido (None quando ainda nao foi processado)."""
    m = mapa()
    for p in pedidos:
        p["onda"] = m.get(_num(p))
    return pedidos


def pendentes(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """So' os que ainda NAO entraram em nenhuma onda."""
    m = mapa()
    return [p for p in pedidos if _num(p) not in m]


def salvar_ate_pedido(pedidos: list[dict[str, Any]],
                      ultimo_olist: int | str) -> dict[str, Any]:
    """Marca como processado tudo com numero da Olist ATE' `ultimo_olist`.

    Serve para quando a onda foi impressa antes deste mecanismo existir (ou
    fora do sistema): em vez de conferir pedido a pedido, o operador olha a
    ultima etiqueta da pilha, ve' o numero e informa aqui.

    Exemplo real (25/08): a pilha impressa ia ate' o pedido **530**
    (NF 414). Informando 530, os pedidos 494-530 entram na onda 1 e os
    531+ ficam como pendentes para a onda 2.

    ⚠️ Compara pelo numero SEQUENCIAL da Olist (#530), nao pelo numero do
    marketplace — e' o unico que tem ordem cronologica confiavel.
    """
    try:
        limite = int(str(ultimo_olist).strip().lstrip("#"))
    except (TypeError, ValueError):
        return {"onda": None, "gravados": 0,
                "erro": f"numero invalido: {ultimo_olist!r}"}

    def _n_olist(p: dict[str, Any]) -> int:
        try:
            return int(str(p.get("numero_olist") or 0).strip().lstrip("#"))
        except (TypeError, ValueError):
            return 0

    alvo = [p for p in pedidos if 0 < _n_olist(p) <= limite]
    sem_numero = [p for p in pedidos if not _n_olist(p)]

    r = salvar_onda(alvo)
    r["limite"] = limite
    r["sem_numero_olist"] = len(sem_numero)
    return r


def desfazer_ultima() -> dict[str, Any]:
    """Apaga a ultima onda do dia — para quando o operador salvou por engano."""
    init_db()
    hoje = datetime.now().strftime("%Y-%m-%d")
    try:
        with _conn() as c:
            r = c.execute("SELECT MAX(onda) FROM ondas WHERE dia = ?",
                          (hoje,)).fetchone()
            ultima = r[0] if r else None
            if not ultima:
                return {"onda": None, "removidos": 0}
            n = c.execute("DELETE FROM ondas WHERE dia = ? AND onda = ?",
                          (hoje, ultima)).rowcount
        log.info("Onda %s desfeita: %d pedido(s) voltaram", ultima, n)
        return {"onda": ultima, "removidos": n}
    except sqlite3.Error as e:
        log.error("Erro ao desfazer a onda: %s", e)
        return {"onda": None, "removidos": 0, "erro": str(e)}


def limpar_ausentes(numeros_atuais: set[str]) -> int:
    """Remove marcas de pedido que SAIU do Olist (ja' despachado de vez).

    A marca dura "ate' o pedido sair do Olist" (decisao do Jota): quando o
    pedido some da fila pendente, a linha aqui nao serve mais para nada e so'
    faria o banco crescer.
    """
    init_db()
    if not numeros_atuais:
        return 0
    try:
        with _conn() as c:
            marcadores = ",".join("?" * len(numeros_atuais))
            n = c.execute(
                f"DELETE FROM ondas WHERE numero_ecommerce NOT IN ({marcadores})",
                tuple(numeros_atuais),
            ).rowcount
        if n:
            log.info("Limpeza de ondas: %d pedido(s) saíram do Olist", n)
        return n
    except sqlite3.Error as e:
        log.error("Erro ao limpar ondas antigas: %s", e)
        return 0


def resumo() -> list[dict[str, Any]]:
    """Ondas do dia com a contagem de cada uma — para mostrar na tela."""
    init_db()
    hoje = datetime.now().strftime("%Y-%m-%d")
    try:
        with _conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT onda, COUNT(*) AS pedidos, MIN(criado_em) AS quando "
                "FROM ondas WHERE dia = ? GROUP BY onda ORDER BY onda", (hoje,))]
    except sqlite3.Error as e:
        log.error("Erro ao resumir as ondas: %s", e)
        return []
