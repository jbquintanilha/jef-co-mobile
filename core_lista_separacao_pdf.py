# ==============================================================================
# NOME DO SCRIPT: core_lista_separacao_pdf.py
# DESCRICAO: Imprime a lista de separacao em etiqueta 10x15 aproveitando espaco
# FUNCAO: Lista de coleta na mao de quem separa. Espreme o maximo de linhas por
#         etiqueta para gastar o minimo de papel termico.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
⚠️ Recurso OPCIONAL (Jota, 2026-08-16): serve para quando houver terceirizado
na bancada. Hoje o Jota separa sozinho e le' da tela — por isso e' um botao,
nunca um passo obrigatorio do fluxo.

Layout: mesma etiqueta 10x15 (100x150mm) da LABEL 2, para nao trocar de rolo.
Fonte pequena mas legivel a um palmo, linhas compactas, quebra em quantas
etiquetas forem necessarias com "folha X/Y" no rodape.

As duas pilhas (COR UNICA e SORTIDA) sao impressas com cabecalho proprio —
sao separacoes fisicas diferentes e nao podem se misturar na prancheta.

Uso:
    from core_lista_separacao_pdf import gerar_lista_10x15
    r = gerar_lista_10x15(atomos)          # bytes prontos para download
    r = gerar_lista_10x15(atomos, saida="lista.pdf")
"""

from __future__ import annotations
import core_env_loader

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 10x15 cm em pontos — igual a' etiqueta de envio, mesmo rolo na LABEL 2
LARGURA = 100 / 25.4 * 72   # 283.46 pt
ALTURA = 150 / 25.4 * 72    # 425.20 pt

MARGEM = 8.0
FONTE_TITULO = 9.0
FONTE_SECAO = 8.0
FONTE_ITEM = 8.5
FONTE_RODAPE = 6.0

ALTURA_LINHA = 13.0     # espaco por item — compacto mas legivel
ALTURA_SECAO = 15.0


def _quebrar_em_paginas(blocos: list[dict[str, Any]], altura_util: float) -> list[list[dict]]:
    """Distribui os blocos em paginas sem cortar um item ao meio.

    ⚠️ Cabecalho orfao: uma secao no pe' da pagina, com os itens dela na
    seguinte, faz quem separa procurar item que nao esta' ali. Por isso a
    secao so' entra se couber ela E o primeiro item abaixo.
    """
    paginas: list[list[dict]] = []
    atual: list[dict] = []
    usado = 0.0

    for i, bloco in enumerate(blocos):
        e_secao = bloco["tipo"] == "secao"
        preciso = ALTURA_SECAO if e_secao else ALTURA_LINHA

        # Secao arrasta consigo o proximo bloco — nao pode ficar sozinha no pe'
        if e_secao:
            seguintes = blocos[i + 1:i + 2]
            if seguintes:
                preciso += (ALTURA_SECAO if seguintes[0]["tipo"] == "secao"
                            else ALTURA_LINHA)

        if usado + preciso > altura_util and atual:
            paginas.append(atual)
            atual, usado = [], 0.0

        atual.append(bloco)
        # So' consome o proprio espaco; o do vizinho foi apenas previsto acima
        usado += ALTURA_SECAO if e_secao else ALTURA_LINHA

    if atual:
        paginas.append(atual)
    return paginas


def _montar_blocos(atomos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transforma os atomos numa sequencia linear de secoes e itens."""
    import core_separacao_atomos as csa

    # ⚠️ Lista UNICA agrupada por familia. SOR e' so' mais uma cor do atomo.
    blocos: list[dict[str, Any]] = []
    familia = None

    for a in atomos:
        if a["familia"] != familia:
            familia = a["familia"]
            blocos.append({"tipo": "secao", "texto": familia or "SEM FAMILIA"})

        extra = ""
        if csa.e_meia(a["atomo"]):
            p = csa.em_pacotes(a["qtd"])
            if p["pacotes"]:
                extra = f" [{p['pacotes']}x12" + (f"+{p['sobra']}]" if p["sobra"] else "]")

        blocos.append({
            "tipo": "item",
            "qtd": a["qtd"],
            "atomo": a["atomo"],
            "extra": extra,
            "suspeito": bool(a.get("suspeito")),
        })

    return blocos


def gerar_lista_10x15(
    atomos: list[dict[str, Any]],
    saida: str | Path | None = None,
    *,
    titulo: str = "LISTA DE SEPARACAO",
) -> dict[str, Any]:
    """Gera o PDF da lista de coleta em etiquetas 10x15.

    Retorna:
        {"bytes", "paginas", "itens", "total_pecas", "saida"}
    """
    import fitz

    if not atomos:
        raise ValueError("Nenhum átomo para imprimir.")

    blocos = _montar_blocos(atomos)
    total_pecas = sum(a["qtd"] for a in atomos)

    # Reserva espaco do cabecalho (1a pagina) e do rodape (todas)
    altura_util = ALTURA - 2 * MARGEM - 26 - 10

    paginas = _quebrar_em_paginas(blocos, altura_util)
    doc = fitz.open()

    for indice, blocos_pag in enumerate(paginas):
        pag = doc.new_page(width=LARGURA, height=ALTURA)
        y = MARGEM + 10

        # --- cabecalho ---
        pag.insert_text((MARGEM, y), titulo, fontsize=FONTE_TITULO,
                        fontname="hebo")
        y += 11
        pag.insert_text(
            (MARGEM, y),
            f"{datetime.now():%d/%m/%Y %H:%M}  ·  {total_pecas} pecas  ·  "
            f"{len(atomos)} SKUs",
            fontsize=FONTE_RODAPE, fontname="helv",
        )
        y += 6
        pag.draw_line(fitz.Point(MARGEM, y), fitz.Point(LARGURA - MARGEM, y),
                      width=0.6)
        y += 9

        # --- corpo ---
        for bloco in blocos_pag:
            if bloco["tipo"] == "secao":
                y += 3
                pag.insert_text(
                    (MARGEM, y), bloco["texto"],
                    fontsize=FONTE_SECAO - (0.8 if bloco.get("menor") else 0),
                    fontname="hebo",
                )
                y += ALTURA_SECAO - 3
                continue

            # caixa de conferencia
            pag.draw_rect(fitz.Rect(MARGEM, y - 7, MARGEM + 7.5, y + 0.5),
                          width=0.6)

            marca = "!" if bloco["suspeito"] else ""
            texto = f"{bloco['qtd']:>3}x {bloco['atomo']}{bloco['extra']}{marca}"
            pag.insert_text((MARGEM + 11, y), texto,
                            fontsize=FONTE_ITEM, fontname="helv")
            y += ALTURA_LINHA

        # --- rodape ---
        pag.insert_text(
            (MARGEM, ALTURA - MARGEM),
            f"folha {indice + 1}/{len(paginas)}",
            fontsize=FONTE_RODAPE, fontname="helv",
        )

    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    conteudo = buffer.getvalue()

    destino = None
    if saida:
        destino = Path(saida)
        destino.write_bytes(conteudo)

    return {
        "bytes": conteudo,
        "paginas": len(paginas),
        "itens": len([b for b in blocos if b["tipo"] == "item"]),
        "total_pecas": total_pecas,
        "saida": str(destino) if destino else None,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import core_separacao as cs
    import core_separacao_atomos as csa

    pedidos = cs.obter_pedidos_pendentes(situacoes=[2], max_pedidos=100)
    dados = cs.processar_batch_picking(pedidos)
    atomos = csa.consolidar_atomos(dados["lista_coleta"])

    destino = Path.home() / "Downloads" / f"lista_separacao_{datetime.now():%Y%m%d_%H%M}.pdf"
    r = gerar_lista_10x15(atomos, saida=destino)
    print(f"{r['saida']}")
    print(f"  {r['paginas']} etiqueta(s) · {r['itens']} itens · "
          f"{r['total_pecas']} peças")
