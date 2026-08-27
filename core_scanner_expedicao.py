# ==============================================================================
# NOME DO SCRIPT: core_scanner_expedicao.py
# DESCRICAO: Conferencia FINAL da expedicao — bipa todas as etiquetas antes de
#            fechar a bolsa e cruza com a lista de pendentes do Olist. Acusa o
#            que faltou etiquetar, o que veio duplicado e o que nao deveria sair.
# AUTOR: Terminador (001)
# VERSAO: 1.0 | DATA: 2026-08-09
# STATUS: Operacional
# ==============================================================================
"""Conferencia final da expedicao (ultima barreira antes do despacho).

Por que existe: hoje nao ha como saber se um pedido ficou pra tras. O Scanner
de conferencia valida pedido a pedido, mas ninguem fecha a conta no fim -- se
uma etiqueta nunca foi impressa, o pedido simplesmente nao sai e so se descobre
pela reclamacao do cliente dias depois.

**E' independente do Scanner do dia, de proposito.** Refaz tudo do zero, mesmo
o que ja foi bipado na bancada. Uma conferencia final que confia no resultado
anterior nao confere nada: se o operador bipou errado de manha, o erro se
propaga. Decisao do Comandante em 2026-08-09.

Fonte da verdade: pedidos do Olist na **situacao 7** ("pronto para envio") —
confirmado em 2026-08-09 batendo com o painel do Olist (29 pedidos: Shopee 17 +
TikTok 12). Pedidos do **ML Full ficam de fora**: quem despacha e' o Mercado
Livre, nao a J&F, entao nao ha etiqueta pra bipar
(ver memoria `lei_ml_full_mundo_a_parte`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import core_scanner_db as db
from core_scanner_decoder import sanitizar_codigo

log = logging.getLogger("core_scanner_expedicao")

RAIZ = Path(r"C:\JF_Automacoes")
RELATORIO_PATH = RAIZ / "EXPEDICAO_RELATORIOS.md"

# Situacao do Olist que corresponde a "pronto para envio" no painel.
SITUACAO_PRONTO_ENVIO = "7"

# Depositos do ML Full: pedido sai do centro do Mercado Livre, nao daqui.
_MARCADORES_FULL = ("full",)


def _e_ml_full(pedido_detalhe: dict) -> bool:
    """True se o pedido sai de um deposito do ML Full (nao e' nossa expedicao)."""
    dep = (pedido_detalhe.get("deposito") or {}).get("nome") or ""
    return any(m in dep.lower() for m in _MARCADORES_FULL)


def carregar_esperados() -> list[dict]:
    """Monta a lista do que deve sair hoje, direto do Olist.

    Prefere o indice local (ja tem tracking resolvido); cai pro Olist para
    pegar quem esta pronto para envio mas ainda nao entrou no indice.
    """
    esperados: dict[str, dict] = {}

    # 1) Indice local: ja traz tracking + produto resolvidos.
    try:
        for r in db.listar_rastreios(500):
            tk = db.normalizar_codigo(r.get("tracking") or "")
            if not tk:
                continue
            esperados[tk] = {
                "tracking": tk,
                "canal": r.get("canal") or "",
                "pedido_ecommerce": r.get("pedido_ecommerce") or "",
                "sku": r.get("sku_principal") or "",
                "produto": r.get("produto_nome") or "",
                "cliente": r.get("cliente_nome") or "",
                "origem": "indice",
            }
    except Exception as e:
        log.error("Falha ao ler o indice local: %s", e)

    return list(esperados.values())


def carregar_esperados_olist() -> tuple[list[dict], list[dict]]:
    """Lista os pedidos 'pronto para envio' no Olist.

    Retorna (para_expedir, ignorados_full). Chamada separada porque bate na
    API e demora -- a UI deve mostrar spinner.
    """
    para_expedir: list[dict] = []
    ignorados: list[dict] = []
    try:
        from core_olist import OlistClient
        from core_scanner_populator import _pedidos_olist

        client = OlistClient()
        brutos = _pedidos_olist()
        prontos = [p for p in brutos
                   if str(p.get("situacao")) == SITUACAO_PRONTO_ENVIO]

        # O indice local ja tem tracking/produto resolvidos pela API de cada
        # canal. Usar ele evita 1 chamada ao Olist por pedido -- com 29 pedidos
        # isso e' a diferenca entre ~2min e ~2s de espera na bancada.
        idx_local = {}
        try:
            for r in db.listar_rastreios(500):
                num = str(r.get("pedido_ecommerce") or "")
                if num:
                    idx_local[num] = r
        except Exception as e:
            log.warning("Indice local indisponivel: %s", e)

        for p in prontos:
            ecom = p.get("ecommerce") or {}
            canal = ecom.get("nome") or ""
            num_ecom = str(ecom.get("numeroPedidoEcommerce") or "")
            registro = {
                "id_olist": p.get("id"),
                "canal": canal,
                "pedido_ecommerce": num_ecom,
                "numero_pedido": p.get("numeroPedido"),
                "cliente": (p.get("cliente") or {}).get("nome") or "",
                "tracking": "",
            }

            local = idx_local.get(num_ecom)
            if local:
                # Caminho rapido: ja temos tudo, sem bater no Olist.
                registro["tracking"] = db.normalizar_codigo(local.get("tracking") or "")
                registro["sku"] = local.get("sku_principal") or ""
                registro["produto"] = local.get("produto_nome") or ""
                registro["cliente"] = registro["cliente"] or local.get("cliente_nome") or ""
                para_expedir.append(registro)
                continue

            # Sem correspondencia local: precisa do detalhe (tracking + deposito).
            # So aqui vale checar ML Full -- Shopee e TikTok nunca sao Full.
            try:
                d = client.obter_pedido(p["id"])
                if "mercado" in canal.lower() and _e_ml_full(d):
                    registro["motivo_ignorado"] = "ML Full (despacho do Mercado Livre)"
                    ignorados.append(registro)
                    continue
                tk = (d.get("transportador") or {}).get("codigoRastreamento") or ""
                registro["tracking"] = db.normalizar_codigo(tk)
                itens = d.get("itens") or []
                if itens:
                    prod = (itens[0].get("produto") or {})
                    registro["sku"] = prod.get("sku") or ""
                    registro["produto"] = prod.get("descricao") or ""
                registro["qtd_itens"] = len(itens)
            except Exception as e:
                log.warning("Detalhe do pedido %s: %s", p.get("id"), e)
            para_expedir.append(registro)
    except Exception as e:
        log.error("Falha ao carregar pedidos do Olist: %s", e)
    return para_expedir, ignorados


def conferir(codigo_lido: str, esperados: list[dict],
             ja_bipados: dict[str, int]) -> dict:
    """Cruza uma bipagem com a lista de esperados.

    ``ja_bipados`` mapeia tracking -> quantas vezes ja foi lido nesta sessao
    (o chamador atualiza depois de tratar o retorno).

    Retorna dict com ``status``:
        ok         -> estava na lista, primeira leitura
        duplicado  -> ja tinha sido bipado nesta conferencia
        fora_lista -> nao consta entre os pedidos a expedir
        invalido   -> codigo lido nao identifica pedido (chave NF-e, CEP...)
    """
    bruto = db.normalizar_codigo(codigo_lido)
    limpo = sanitizar_codigo(bruto)

    if not limpo:
        return {
            "status": "invalido",
            "codigo": bruto,
            "titulo": "🟠 CÓDIGO ERRADO DA ETIQUETA",
            "detalhe": "Isso é a chave da nota fiscal ou o CEP. Bipe o código de RASTREIO.",
        }

    por_tracking = {e["tracking"]: e for e in esperados if e.get("tracking")}
    por_pedido = {e["pedido_ecommerce"]: e for e in esperados
                  if e.get("pedido_ecommerce")}

    item = por_tracking.get(limpo) or por_pedido.get(limpo)

    if item is None:
        return {
            "status": "fora_lista",
            "codigo": limpo,
            "titulo": "🔴 NÃO ESTÁ NA LISTA DE HOJE",
            "detalhe": "Esta etiqueta não corresponde a nenhum pedido pronto "
                       "para envio. Confira se não é de outro dia ou já despachado.",
        }

    chave = item.get("tracking") or item.get("pedido_ecommerce")
    if ja_bipados.get(chave):
        return {
            "status": "duplicado",
            "codigo": limpo,
            "item": item,
            "titulo": "⚠️ ETIQUETA DUPLICADA",
            "detalhe": f"Este pedido já foi conferido nesta sessão "
                       f"({ja_bipados[chave]}ª leitura). Pode ser etiqueta "
                       f"impressa em duplicidade.",
        }

    return {
        "status": "ok",
        "codigo": limpo,
        "item": item,
        "titulo": "🟢 CONFERIDO",
        "detalhe": f"{item.get('produto') or item.get('sku') or ''}",
    }


def _conferidos_hoje() -> set[str]:
    """Trackings ja registrados no Scanner do dia (fora desta conferencia).

    Serve pra distinguir "nunca passou por lugar nenhum" de "passou no Scanner
    da bancada mas nao foi bipado na conferencia final" -- sem isso o relatorio
    lista como faltante algo que ja foi conferido antes e assusta a toa.
    """
    try:
        import sqlite3
        con = sqlite3.connect(db.DB_PATH)
        rows = con.execute(
            "SELECT tracking FROM conferencias "
            "WHERE date(conferido_em) = date('now','localtime')"
        ).fetchall()
        con.close()
        return {db.normalizar_codigo(r[0]) for r in rows if r and r[0]}
    except Exception as e:  # pragma: no cover
        log.warning("Nao consegui ler as conferencias do dia: %s", e)
        return set()


def montar_relatorio(esperados: list[dict], ja_bipados: dict[str, int],
                     fora_lista: list[dict], ignorados: list[dict] | None = None) -> dict:
    """Consolida o resultado da conferencia. Nao grava nada.

    Cruza tres fontes: o que se espera expedir, o que foi bipado NESTA
    conferencia e o que ja passou pelo Scanner hoje. Faltantes saem separados
    em dois grupos, porque a acao do operador e' diferente em cada caso.
    """
    conferidos_antes = _conferidos_hoje()

    faltando = []          # nao bipado aqui NEM no Scanner do dia
    faltando_visto = []    # nao bipado aqui, mas ja conferido antes hoje
    for e in esperados:
        chave = e.get("tracking") or e.get("pedido_ecommerce")
        if ja_bipados.get(chave):
            continue
        if db.normalizar_codigo(e.get("tracking") or "") in conferidos_antes:
            faltando_visto.append(e)
        else:
            faltando.append(e)

    # Duplicados COM identificacao: sem produto/cliente o operador nao acha a
    # caixa na bolsa (pedido do Comandante, 2026-08-09).
    por_chave = {(e.get("tracking") or e.get("pedido_ecommerce")): e
                 for e in esperados}
    duplicados = []
    for k, v in ja_bipados.items():
        if v > 1:
            it = por_chave.get(k, {})
            duplicados.append({
                "chave": k,
                "vezes": v,
                "canal": it.get("canal", ""),
                "pedido_ecommerce": it.get("pedido_ecommerce", ""),
                "sku": it.get("sku", ""),
                "produto": it.get("produto", ""),
                "cliente": it.get("cliente", ""),
            })

    return {
        "total_esperado": len(esperados),
        "conferidos": len([1 for e in esperados
                           if ja_bipados.get(e.get("tracking") or e.get("pedido_ecommerce"))]),
        "faltando": faltando,
        "faltando_ja_visto": faltando_visto,
        "duplicados": duplicados,
        "fora_lista": fora_lista,
        "ignorados": ignorados or [],
        "ok": not faltando and not faltando_visto and not duplicados and not fora_lista,
    }


def ler_relatorios() -> str:
    """Devolve o conteudo do arquivo de relatorios (ou "" se nao existir)."""
    if not RELATORIO_PATH.exists():
        return ""
    try:
        return RELATORIO_PATH.read_text(encoding="utf-8")
    except Exception as e:  # pragma: no cover
        log.error("Falha ao ler relatorios: %s", e)
        return ""


def contar_relatorios() -> int:
    """Quantos relatorios existem no arquivo."""
    conteudo = ler_relatorios()
    return conteudo.count("\n## ") if conteudo else 0


def limpar_relatorios(*, manter_backup: bool = True) -> bool:
    """Zera o arquivo de relatorios (mantendo so o cabecalho).

    Usar depois de sanar as pendencias -- senao o arquivo vira lixo acumulado
    (pedido do Comandante, 2026-08-09). Com ``manter_backup`` o conteudo antigo
    vai para ``EXPEDICAO_RELATORIOS_backup_<data>.md`` antes de zerar: relatorio
    de expedicao pode virar prova numa disputa, entao apagar sem copia e' risco
    desnecessario.
    """
    try:
        if RELATORIO_PATH.exists() and manter_backup:
            conteudo = RELATORIO_PATH.read_text(encoding="utf-8")
            # So faz backup se houver relatorio de fato (alem do cabecalho).
            if conteudo.count("\n## ") > 0:
                selo = datetime.now().strftime("%Y%m%d_%H%M%S")
                bkp = RELATORIO_PATH.with_name(
                    f"{RELATORIO_PATH.stem}_backup_{selo}.md")
                bkp.write_text(conteudo, encoding="utf-8")
                log.info("Backup dos relatorios em %s", bkp)

        RELATORIO_PATH.write_text(
            "# 📦 Relatórios de Conferência Final da Expedição\n\n"
            "> Gerado pelo Scanner antes de fechar a bolsa de despacho.\n\n---\n",
            encoding="utf-8")
        return True
    except Exception as e:  # pragma: no cover
        log.error("Falha ao limpar relatorios: %s", e)
        return False


def salvar_relatorio(rel: dict, observacao: str = "") -> bool:
    """Grava o relatorio em Markdown (mais recente no topo)."""
    try:
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cab = ("# 📦 Relatórios de Conferência Final da Expedição\n\n"
               "> Gerado pelo Scanner antes de fechar a bolsa de despacho.\n\n---\n")
        conteudo = (RELATORIO_PATH.read_text(encoding="utf-8")
                    if RELATORIO_PATH.exists() else cab)

        status = "✅ TUDO CONFERIDO" if rel["ok"] else "⚠️ COM PENDÊNCIAS"
        linhas = [
            f"\n## {status} — {agora}\n",
            f"\n- **Esperados:** {rel['total_esperado']}",
            f"\n- **Conferidos nesta sessão:** {rel['conferidos']}",
            f"\n- **Faltando (nunca bipados hoje):** {len(rel['faltando'])}",
            f"\n- **Não bipados aqui, mas já vistos hoje:** "
            f"{len(rel.get('faltando_ja_visto', []))}",
            f"\n- **Duplicados:** {len(rel['duplicados'])}",
            f"\n- **Fora da lista:** {len(rel['fora_lista'])}\n",
        ]
        if observacao:
            linhas.append(f"\n**Observação:** {observacao}\n")

        if rel["faltando"]:
            linhas.append("\n### 🔴 Não foram conferidos (verificar antes de fechar)\n\n")
            linhas.append("| Canal | Pedido | Tracking | Produto | Cliente |\n")
            linhas.append("|---|---|---|---|---|\n")
            for f in rel["faltando"]:
                linhas.append(
                    f"| {f.get('canal','')} | {f.get('pedido_ecommerce','')} | "
                    f"`{f.get('tracking','') or '—'}` | "
                    f"{(f.get('produto') or f.get('sku') or '')[:45]} | "
                    f"{(f.get('cliente') or '').split(' ')[0]} |\n")

        if rel.get("faltando_ja_visto"):
            linhas.append(
                f"\n### 🟡 Não bipados aqui, mas já conferidos hoje "
                f"({len(rel['faltando_ja_visto'])})\n\n"
                "_Passaram pelo Scanner da bancada. Confirme se estão na bolsa._\n\n")
            linhas.append("| Canal | Pedido | Tracking | Produto | Cliente |\n")
            linhas.append("|---|---|---|---|---|\n")
            for f in rel["faltando_ja_visto"]:
                linhas.append(
                    f"| {f.get('canal','')} | {f.get('pedido_ecommerce','')} | "
                    f"`{f.get('tracking','') or '—'}` | "
                    f"{(f.get('produto') or f.get('sku') or '')[:45]} | "
                    f"{(f.get('cliente') or '').split(' ')[0]} |\n")

        if rel["duplicados"]:
            linhas.append("\n### ⚠️ Bipados mais de uma vez — RETIRAR DA BOLSA\n\n")
            linhas.append("| Leituras | Tracking | Pedido | Produto | SKU | Cliente |\n")
            linhas.append("|---|---|---|---|---|---|\n")
            for d in rel["duplicados"]:
                linhas.append(
                    f"| {d['vezes']}x | `{d['chave']}` | "
                    f"{d.get('pedido_ecommerce','—')} | "
                    f"{(d.get('produto') or '')[:40] or '—'} | "
                    f"`{d.get('sku','') or '—'}` | "
                    f"{(d.get('cliente') or '').split(' ')[0] or '—'} |\n")

        if rel["fora_lista"]:
            linhas.append("\n### 🟠 Etiquetas fora da lista de hoje\n\n")
            for f in rel["fora_lista"]:
                linhas.append(f"- `{f.get('codigo')}`\n")

        if rel["ignorados"]:
            linhas.append("\n### ⚪ Ignorados (ML Full — despacho do Mercado Livre)\n\n")
            for i in rel["ignorados"]:
                linhas.append(
                    f"- {i.get('canal','')} · pedido {i.get('pedido_ecommerce','')}\n")

        linhas.append("\n<details><summary>dados completos (json)</summary>\n\n")
        linhas.append(f"```json\n{json.dumps(rel, ensure_ascii=False, indent=2, default=str)}\n```\n")
        linhas.append("</details>\n\n---\n")

        bloco = "".join(linhas)
        marcador = "---\n"
        pos = conteudo.find(marcador)
        novo = (conteudo[:pos + len(marcador)] + bloco + conteudo[pos + len(marcador):]
                if pos != -1 else conteudo + bloco)
        RELATORIO_PATH.write_text(novo, encoding="utf-8")
        return True
    except Exception as e:  # pragma: no cover
        log.error("Falha ao salvar relatorio: %s", e)
        return False
