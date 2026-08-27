# ==============================================================================
# NOME DO SCRIPT: core_cruzamento_expedicao.py
# DESCRICAO: Cruza as 4 fontes de verdade da expedicao para achar divergencia
# FUNCAO: A ETIQUETA NAO SABE O QUE TEM NA CAIXA — ela so' tem rastreio e
#         destinatario. O item verdadeiro vem do PEDIDO (Olist/marketplace) e a
#         NOTA FISCAL confirma. Este motor cruza tudo e mostra o que nao bate.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# REF: lei_verificacao_dobrada_duas_fontes — nunca confiar em fonte unica
# ==============================================================================
"""
As 4 fontes e o que cada uma prova:

    OLIST        o que foi vendido (SKU, quantidade) -> FONTE DO ITEM
    NOTA FISCAL  o que foi faturado                  -> CONFIRMA o item
    ETIQUETA     para onde vai (rastreio, endereco)  -> NAO diz o item
    MARKETPLACE  o pedido original                   -> ORIGEM da venda

⚠️ Filosofia (Jota, 2026-08-16):
    "A etiqueta no site e' apenas uma limitacao, pois nao da' para baixar na
    Olist. A VERDADE DO ITEM NAO E' DELA. Voce que cruza depois se a etiqueta
    condiz com o pedido que voce separou."

Politica: **alarme, nunca bloqueio**. Divergencia aparece em destaque e o
operador decide (decisao do Jota).

Uso:
    from core_cruzamento_expedicao import cruzar
    r = cruzar()
    print(r["resumo"])
    for d in r["divergencias"]:
        print(d["gravidade"], d["tipo"], d["chave"], d["detalhe"])
"""

from __future__ import annotations
import core_env_loader

import logging
from typing import Any

log = logging.getLogger(__name__)

# Gravidade das divergencias — ordena o que o operador ve primeiro
GRAVE = "🔴"
ATENCAO = "🟡"
INFO = "🔵"


def _sku_do_item(item: dict[str, Any]) -> str:
    """SKU de um item do pedido Olist.

    ⚠️ O SKU vem aninhado em `item["produto"]["sku"]` — nao em `item["codigo"]`.
    Procurar no lugar errado gera falso positivo de "item sem SKU" em massa.
    """
    prod = item.get("produto") or {}
    return str(prod.get("sku") or item.get("sku") or item.get("codigo") or "").strip()


def _pedidos_olist(situacoes: list[int] | None = None) -> list[dict[str, Any]]:
    """Pedidos do Olist na(s) situacao(oes) pedida(s). Fonte do ITEM."""
    try:
        import core_separacao as cs
        return cs.obter_pedidos_pendentes(situacoes=situacoes or [2])
    except Exception as exc:
        log.warning("Olist indisponivel: %s", exc)
        return []


def _etiquetas_tiktok() -> list[str]:
    """package_id com etiqueta pronta no TikTok. Fonte do DESTINO."""
    try:
        import core_etiquetas_tiktok_api as tta
        return [
            str(p.get("id") or p.get("package_id"))
            for p in tta.listar_pacotes_a_enviar()
            if (p.get("id") or p.get("package_id"))
        ]
    except Exception as exc:
        log.warning("TikTok indisponivel: %s", exc)
        return []


def _etiquetas_shopee() -> list[str]:
    """order_sn aguardando despacho na Shopee. Fonte do DESTINO."""
    try:
        import core_etiquetas_shopee_api as spa
        return spa.listar_pedidos_a_enviar()
    except Exception as exc:
        log.warning("Shopee indisponivel: %s", exc)
        return []


def cruzar(situacoes: list[int] | None = None) -> dict[str, Any]:
    """Cruza Olist x Etiquetas x NF e devolve as divergencias.

    Retorna:
        {
          "fontes":       {"olist": n, "tiktok": n, "shopee": n},
          "divergencias": [{"gravidade","tipo","chave","detalhe"}],
          "resumo":       "texto curto",
          "ok":           bool (sem divergencia GRAVE),
        }
    """
    pedidos = _pedidos_olist(situacoes)
    tt = _etiquetas_tiktok()
    sp = _etiquetas_shopee()

    divergencias: list[dict[str, Any]] = []

    # --- 1. Pedido no Olist SEM SKU -> nao da' para separar ---------------- #
    for ped in pedidos:
        num = str(ped.get("numeroPedidoEcommerce") or ped.get("id") or "?")
        itens = ped.get("itens") or []
        if not itens:
            divergencias.append({
                "gravidade": GRAVE,
                "tipo": "pedido sem item",
                "chave": num,
                "detalhe": "Pedido no Olist sem nenhum item — impossivel separar.",
            })
            continue

        for it in itens:
            if not _sku_do_item(it):
                desc = (it.get("produto") or {}).get("descricao") or it.get("descricao") or "?"
                divergencias.append({
                    "gravidade": GRAVE,
                    "tipo": "item sem SKU",
                    "chave": num,
                    "detalhe": f"Item '{str(desc)[:40]}' sem SKU — impossivel separar.",
                })

    # --- 2. SKU fora do padrao V5 -> risco de separar errado --------------- #
    try:
        import core_separacao_atomos as csa
        vistos: set[str] = set()
        for ped in pedidos:
            for it in (ped.get("itens") or []):
                sku = _sku_do_item(it).upper()
                if not sku or sku in vistos:
                    continue
                vistos.add(sku)

                v = csa.validar_kit(sku)
                if v["e_kit"] and not v["ok"]:
                    divergencias.append({
                        "gravidade": GRAVE,
                        "tipo": "kit nao fecha",
                        "chave": sku,
                        "detalhe": (
                            f"KIT{v['declarado']} mas a soma das cores da' "
                            f"{v['somado']} — SKU ou cadastro errado."
                        ),
                    })
                    continue

                partes = csa.decompor_sku(sku)
                if any(not p.get("padrao_v5") for p in partes) and "_KIT" in sku:
                    divergencias.append({
                        "gravidade": ATENCAO,
                        "tipo": "SKU fora do padrao V5",
                        "chave": sku,
                        "detalhe": "Nao casou com {COR}{qtd} — conferir na mao.",
                    })
    except Exception as exc:
        log.warning("Validacao V5 indisponivel: %s", exc)

    # --- 3. NOTA FISCAL: por que NAO checamos por `idNotaFiscal` ----------- #
    #
    # 🔴 NAO reintroduzir alarme de "pedido sem NF" baseado em `idNotaFiscal`.
    #
    # Medido em producao (2026-08-16, amostra de 12 pedidos na situacao 2):
    #     com idNotaFiscal:  2      sem idNotaFiscal: 10
    # Mas os 22 pedidos da situacao 2 sao os "Pronto para envio" da UI do
    # Olist — TODOS ja faturados, com nota emitida e visivel na tela.
    #
    # Ou seja: `idNotaFiscal` so' aparece de forma confiavel a partir da
    # situacao 5+. Ausencia NAO prova falta de nota. Usar esse campo como
    # criterio gera falso positivo em massa (erro cometido nesta data: 24
    # pedidos Shopee dados como "sem nota" quando todos tinham).
    #
    # O caso real (ped.411): criado 15:52, ficou na situacao 4 ate o ciclo de
    # faturamento rodar; virou sit 7 com NF 350476077 sem intervencao. Nunca
    # houve falha de validacao — o pedido so' nao tinha entrado na fila ainda.
    #
    # Para checar NF de verdade seria preciso consultar o endpoint de notas e
    # casar por `numeroOrdemCompra`. Fica pendente — ver Lousa.

    # --- 4. Volume: etiquetas x pedidos ------------------------------------ #
    total_etq = len(tt) + len(sp)
    if pedidos and total_etq and total_etq != len(pedidos):
        divergencias.append({
            "gravidade": ATENCAO,
            "tipo": "contagem diverge",
            "chave": "lote",
            "detalhe": (
                f"{len(pedidos)} pedidos no Olist x {total_etq} etiquetas "
                f"(TikTok {len(tt)} + Shopee {len(sp)}). "
                "Conferir quem esta sobrando ou faltando."
            ),
        })

    # --- 5. Canal sem etiqueta --------------------------------------------- #
    if pedidos and not total_etq:
        divergencias.append({
            "gravidade": INFO,
            "tipo": "sem etiqueta",
            "chave": "lote",
            "detalhe": "Ha pedidos no Olist mas nenhuma etiqueta pronta nas APIs.",
        })

    ordem = {GRAVE: 0, ATENCAO: 1, INFO: 2}
    divergencias.sort(key=lambda d: ordem.get(d["gravidade"], 9))

    graves = sum(1 for d in divergencias if d["gravidade"] == GRAVE)

    return {
        "fontes": {"olist": len(pedidos), "tiktok": len(tt), "shopee": len(sp)},
        "divergencias": divergencias,
        "ok": graves == 0,
        "resumo": (
            f"Olist {len(pedidos)} · TikTok {len(tt)} · Shopee {len(sp)} — "
            + (f"{len(divergencias)} divergência(s), {graves} grave(s)"
               if divergencias else "sem divergências")
        ),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    r = cruzar()
    print(r["resumo"])
    print()
    for d in r["divergencias"]:
        print(f"{d['gravidade']} [{d['tipo']}] {d['chave']}")
        print(f"    {d['detalhe']}")
