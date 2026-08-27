# ==============================================================================
# NOME DO SCRIPT: core_scanner_auditoria.py
# DESCRICAO: Auditoria cruzada do indice local contra a API do marketplace.
# FUNCAO: Confirmar, em SEGUNDA FONTE, que o pedido mostrado na bancada bate
#         com o que o cliente realmente comprou (itens, SKUs, quantidade).
# AUTOR: Terminador (001) / J&F Co.
# VERSAO: 1.0 | DATA: 2026-08-12
# STATUS: Operacional
# ==============================================================================
"""Verificacao dobrada: indice local x marketplace.

LEI (Jota, 2026-08-12) — nasceu de incidente real: o pedido AP341455035BR tinha
4 itens (12 pares) e o Scanner mostrava 1 item (3 pares). O indice local
"concordava consigo mesmo", entao nada acusou. Se houvesse cruzamento com a API
do canal, a diferenca 1 != 4 teria aparecido na hora.

Tres regras:
  1. Cruzar SEMPRE (indice local x API do marketplace);
  2. Em BACKGROUND -- nunca travar a bancada;
  3. Divergencia FICA NA TELA ate o Comandante dar OK.

A API do canal e' a fonte de verdade do que o cliente comprou; o espelho no
Olist as vezes chega incompleto (provado no mesmo incidente).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading

import core_scanner_db as db

log = logging.getLogger("core_scanner_auditoria")

DB_PATH = db.DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auditoria_divergencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking TEXT NOT NULL,
    canal TEXT,
    tipo TEXT,                       -- 'itens_faltando' | 'sku_divergente' | 'nao_encontrado'
    detalhe TEXT,                    -- texto pronto pra tela
    itens_local TEXT,                -- JSON do que o indice tinha
    itens_canal TEXT,                -- JSON do que o marketplace respondeu
    detectado_em TEXT DEFAULT (datetime('now','localtime')),
    resolvido_em TEXT,               -- NULL enquanto o Comandante nao der OK
    UNIQUE(tracking, tipo)
);
CREATE INDEX IF NOT EXISTS idx_audit_aberta
    ON auditoria_divergencias(resolvido_em);
"""


def init_db() -> None:
    """Cria a tabela de divergencias. Idempotente."""
    try:
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            conn.executescript(_SCHEMA)
    except sqlite3.Error as e:
        log.error("Falha ao inicializar auditoria: %s", e)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


# ------------------------------------------------------------------ #
# Consulta ao marketplace (segunda fonte)
# ------------------------------------------------------------------ #
def _itens_do_canal(tracking: str, canal: str, pedido_ecom: str) -> list[dict] | None:
    """Busca a lista de itens direto no marketplace.

    Devolve None quando nao deu pra consultar (API fora, canal sem suporte) --
    diferente de [] , que significaria "pedido sem item", o que seria erro real.
    """
    canal = (canal or "").lower()
    try:
        if canal == "tiktok":
            import core_tiktokshop_orders as tk
            ped = tk.buscar_por_tracking(tracking)
            if not ped:
                return None
            return [
                {
                    "sku": i.get("sku") or "",
                    "quantidade": int(i.get("quantidade") or 1),
                    "valor": float(i.get("valor") or 0),
                }
                for i in (ped.get("itens") or [])
            ]

        if canal == "shopee":
            from core_shopee import ShopeeClient
            det = ShopeeClient().get_order_detail(pedido_ecom)
            if not det:
                return None
            return [
                {
                    "sku": i.get("model_sku") or i.get("item_sku") or "",
                    "quantidade": int(i.get("model_quantity_purchased") or 1),
                    "valor": float(i.get("model_discounted_price") or 0)
                             * int(i.get("model_quantity_purchased") or 1),
                }
                for i in (det[0].get("item_list") or [])
            ]
    except Exception as e:
        log.warning("Auditoria %s (%s): consulta ao canal falhou: %s", tracking, canal, e)
        return None

    return None  # ML ainda sem suporte


def _norm(sku: str) -> str:
    return (sku or "").strip().upper()


# Custo por peca, por prefixo de SKU. Espelha `ITENS` de core_analise_ads_diaria
# (mesma fonte de verdade de CMV usada na analise de rentabilidade).
_CUSTO_PECA = {
    "MEMEDMAY103": 1.65,   # Meia Cano Medio
    "MEINVMAY101": 1.50,   # Meia Invisivel
    "CONCLI107": 22.19,    # Trio Renda
    "TOPTAY016": 13.70,    # Top Fitness
}

# Margem de tolerancia: o preco de venda sempre e' varias vezes o custo, entao
# so acusa quando o valor pago fica MUITO acima do esperado pro que a bancada
# esta vendo -- sinal classico de item faltando na lista.
_FATOR_ALERTA = 3.0


def _pecas_do_sku(sku: str) -> int:
    """Quantidade real de pecas do kit, lida do sufixo _KIT{n}."""
    import re
    m = re.search(r"_KIT(\d+)", sku or "")
    return int(m.group(1)) if m else 1


def _cmv_estimado(itens: list[dict]) -> float:
    """CMV da lista de itens, somando custo x pecas do kit. 0 se nao souber."""
    total = 0.0
    for it in itens:
        sku = _norm(it.get("sku"))
        custo = next((c for p, c in _CUSTO_PECA.items() if sku.startswith(p)), None)
        if custo is None:
            return 0.0  # SKU desconhecido: nao da pra estimar, nao acusa
        total += custo * _pecas_do_sku(sku) * int(it.get("quantidade") or 1)
    return total


def _checar_valor(itens_local: list[dict], itens_canal: list[dict]) -> tuple[str, str] | None:
    """🔴 DESATIVADA — comparar preco de venda com CMV gera falso positivo.

    A ideia original era: valor pago muito acima do custo = peca faltando.
    Mas margem de 3x e' o NORMAL do negocio, nao anomalia. Medido em 18/08:

        Kit 3 meia invisivel   venda R$13,99  cmv R$ 4,50  = 3,1x  -> alertava
        Kit 3 sortida          venda R$15,99  cmv R$ 4,50  = 3,6x  -> alertava
        Kit 12 meia media      venda R$37,90  cmv R$19,80  = 1,9x  -> ok
        Kit 24 meia media      venda R$43,87  cmv R$39,60  = 1,1x  -> ok

    Kit PEQUENO sempre passa de 3x — e' a margem dele. Resultado: alerta em
    todo pedido de kit pequeno, sempre. O Jota relatou "muito sensivel" e
    estava certo: os alertas eram ruido, e ruido constante faz ignorar o
    alerta de verdade.

    A checagem que substitui esta e' `_checar_multiplos()`, que compara ITEM
    contra ITEM em vez de adivinhar por preco.
    """
    return None


def _checar_multiplos(itens_local: list[dict],
                      itens_canal: list[dict]) -> tuple[str, str] | None:
    """Caixa que exige conferencia peca a peca. AVISO, nao erro.

    🔴 Dispara em DOIS casos — os dois erram na bancada:

    1. **Mais de um ATOMO distinto** — ex: kit branca+preta. Risco de conferir
       metade e achar que fechou.
    2. **QUANTIDADE > 1 do mesmo item** — ex: pedido 428 (18/08), 4 unidades
       do "Kit 3 Meia Invisivel Cinza" = 12 pecas numa caixa so'. Risco de
       mandar 1 kit e achar que era o pedido inteiro.

    O caso 2 e' o mais traicoeiro: a tela mostra UMA linha de produto, e a
    quantidade "4" passa despercebida. A primeira versao desta funcao so'
    olhava atomos distintos e ficou MUDA no pedido 428.
    """
    try:
        import core_separacao_atomos as csa
    except Exception:
        return None

    itens = itens_canal or itens_local

    atomos: dict[str, int] = {}
    unidades_totais = 0
    for item in itens:
        sku = (item.get("sku") or "").strip()
        qtd = int(item.get("quantidade") or 1)
        unidades_totais += qtd
        for parte in csa.decompor_sku(sku):
            chave = parte["atomo"]
            atomos[chave] = atomos.get(chave, 0) + parte["qtd"] * qtd

    total_pecas = sum(atomos.values())
    detalhe = " · ".join(f"{q}x {a}" for a, q in
                         sorted(atomos.items(), key=lambda x: -x[1]))

    # Caso 2: mesmo item repetido — a quantidade e' o que passa despercebido
    if unidades_totais > 1 and len(atomos) <= 1:
        return (
            "multi_item",
            f"⚠️ {unidades_totais} UNIDADES do mesmo item ({total_pecas} pecas): "
            f"{detalhe}. NAO e' 1 kit — sao {unidades_totais}. "
            "Conferir a quantidade antes de fechar a caixa.",
        )

    # Caso 1: atomos distintos
    if len(atomos) > 1:
        extra = (f" em {unidades_totais} unidades" if unidades_totais > 1 else "")
        return (
            "multi_item",
            f"Pedido com {len(atomos)} atomos distintos{extra} "
            f"({total_pecas} pecas): {detalhe}. "
            "Conferir peca a peca antes de fechar a caixa.",
        )

    return None


def _comparar(itens_local: list[dict], itens_canal: list[dict]) -> tuple[str, str] | None:
    """Compara as duas listas. Devolve (tipo, detalhe) se divergir; None se bate."""
    loc = {_norm(i.get("sku")) for i in itens_local if _norm(i.get("sku"))}
    can = {_norm(i.get("sku")) for i in itens_canal if _norm(i.get("sku"))}

    faltando = can - loc          # o cliente comprou e a bancada NAO ve  -> grave
    sobrando = loc - can          # a bancada ve e o cliente nao comprou  -> grave

    if faltando:
        return (
            "itens_faltando",
            f"O pedido tem {len(can)} item(ns) no marketplace, mas o Scanner mostra "
            f"{len(loc)}. NAO aparecem na bancada: {', '.join(sorted(faltando))}. "
            "Risco de despachar caixa INCOMPLETA.",
        )
    if sobrando:
        return (
            "sku_divergente",
            f"O Scanner mostra item(ns) que nao constam no pedido do marketplace: "
            f"{', '.join(sorted(sobrando))}. Confira antes de separar.",
        )
    return None


# ------------------------------------------------------------------ #
# Registro / leitura das divergencias
# ------------------------------------------------------------------ #
def registrar(tracking: str, canal: str, tipo: str, detalhe: str,
              itens_local: list, itens_canal: list) -> None:
    """Grava a divergencia. Reabre se ja existia e tinha sido resolvida."""
    try:
        with _conn() as conn:
            conn.execute(
                """
                INSERT INTO auditoria_divergencias
                    (tracking, canal, tipo, detalhe, itens_local, itens_canal,
                     detectado_em, resolvido_em)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), NULL)
                ON CONFLICT(tracking, tipo) DO UPDATE SET
                    detalhe      = excluded.detalhe,
                    itens_local  = excluded.itens_local,
                    itens_canal  = excluded.itens_canal,
                    detectado_em = datetime('now','localtime'),
                    resolvido_em = NULL
                """,
                (tracking, canal, tipo, detalhe,
                 json.dumps(itens_local, ensure_ascii=False),
                 json.dumps(itens_canal, ensure_ascii=False)),
            )
        log.error("DIVERGENCIA %s (%s): %s", tracking, tipo, detalhe)
    except sqlite3.Error as e:
        log.error("Falha ao registrar divergencia de %s: %s", tracking, e)


def listar_abertas() -> list[dict]:
    """Divergencias ainda nao liberadas pelo Comandante."""
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM auditoria_divergencias "
                "WHERE resolvido_em IS NULL ORDER BY detectado_em DESC",
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.error("Falha ao listar divergencias: %s", e)
        return []


def contar_abertas() -> int:
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM auditoria_divergencias "
                "WHERE resolvido_em IS NULL",
            ).fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error:
        return 0


def dar_ok(divergencia_id: int) -> bool:
    """Comandante deu OK: para de mostrar na tela."""
    try:
        with _conn() as conn:
            conn.execute(
                "UPDATE auditoria_divergencias "
                "SET resolvido_em = datetime('now','localtime') WHERE id = ?",
                (int(divergencia_id),),
            )
        return True
    except sqlite3.Error as e:
        log.error("Falha ao resolver divergencia %s: %s", divergencia_id, e)
        return False


def dar_ok_todas() -> int:
    try:
        with _conn() as conn:
            cur = conn.execute(
                "UPDATE auditoria_divergencias "
                "SET resolvido_em = datetime('now','localtime') "
                "WHERE resolvido_em IS NULL",
            )
            return cur.rowcount or 0
    except sqlite3.Error as e:
        log.error("Falha ao resolver divergencias: %s", e)
        return 0


# ------------------------------------------------------------------ #
# Execucao
# ------------------------------------------------------------------ #
def auditar(tracking: str) -> None:
    """Cruza UM tracking contra o marketplace. Sincrono (use via `auditar_async`)."""
    try:
        reg = db.buscar_por_tracking(tracking)
        if not reg:
            return
        canal = reg.get("canal") or ""
        itens_local = db.desserializar_itens(reg)
        itens_canal = _itens_do_canal(tracking, canal, reg.get("pedido_ecommerce") or "")

        if itens_canal is None:
            return  # nao deu pra consultar: nao inventa divergencia
        if not itens_canal:
            return  # canal sem itens: provavel pedido antigo/arquivado

        # 1a checagem: os SKUs batem entre indice e marketplace?
        achado = _comparar(itens_local, itens_canal)
        if achado:
            tipo, detalhe = achado
            registrar(tracking, canal, tipo, detalhe, itens_local, itens_canal)
            return

        # 2a checagem: pedido com mais de um ATOMO exige conferencia peca a
        # peca. Substituiu a checagem por VALOR (`_checar_valor`), que
        # alertava em todo kit pequeno porque margem 3x e' o normal da casa.
        achado_multi = _checar_multiplos(itens_local, itens_canal)
        if achado_multi:
            tipo, detalhe = achado_multi
            registrar(tracking, canal, tipo, detalhe, itens_local, itens_canal)
    except Exception as e:
        log.warning("Auditoria de %s falhou: %s", tracking, e)


def auditar_pendentes_async(limite: int = 40) -> None:
    """Audita em lote os rastreios do indice, em background.

    Chamado depois da sincronia: pega divergencia em pedido que ainda nem foi
    bipado, entao o erro aparece ANTES de a caixa ser montada.
    """
    def _lote() -> None:
        try:
            for reg in db.listar_rastreios(limite):
                auditar(reg.get("tracking") or "")
        except Exception as e:
            log.warning("Auditoria em lote falhou: %s", e)

    try:
        threading.Thread(target=_lote, daemon=True).start()
    except Exception as e:
        log.warning("Nao foi possivel iniciar auditoria em lote: %s", e)


def auditar_async(tracking: str) -> None:
    """Dispara a auditoria em thread daemon — nao trava a bancada.

    Regra 2 da lei: o operador segue conferindo enquanto o cruzamento roda.
    """
    if not tracking:
        return
    try:
        t = threading.Thread(target=auditar, args=(tracking,), daemon=True)
        t.start()
    except Exception as e:
        log.warning("Nao foi possivel iniciar auditoria de %s: %s", tracking, e)


init_db()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        auditar(sys.argv[1])
        print(f"auditado: {sys.argv[1]}")
    abertas = listar_abertas()
    print(f"{len(abertas)} divergencia(s) aberta(s)")
    for a in abertas:
        print(f"  [{a['tipo']}] {a['tracking']}: {a['detalhe']}")
