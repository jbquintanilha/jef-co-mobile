# ==============================================================================
# NOME DO SCRIPT: core_lista_montagem_pdf.py
# DESCRICAO: Lista de separacao em PAPEL A4 — por SKU e por pedido agrupado
# FUNCAO: A lista 10x15 existente so' diz o que PEGAR no estoque. Falta a
#         segunda metade: como MONTAR, pedido a pedido, agrupado por produto.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 25/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Lista de separacao/montagem em A4, para levar a' bancada.

## Por que existe

Sao dois momentos diferentes da bancada:

1. **Pegar no estoque** — quanto de cada atomo. Ja' existia em
   `core_lista_separacao_pdf.gerar_lista_10x15` (etiqueta 10x15).
2. **Montar os pedidos** — qual pedido leva o que, na ordem em que as
   pecas ja' estao na mesa. **Nao existia.**

Este modulo entrega os dois numa folha A4 so', porque a bancada le' os dois
ao mesmo tempo: pega o monte, depois vai casando pedido por pedido.

⚠️ Isto e' LISTA DE PAPEL, nao etiqueta. As etiquetas vem depois, e ja' saem
na mesma ordem (`core_etiquetas_na_esteira`), entao a pilha de papel e a
pilha de etiquetas conversam.

## A ordem

A mesma de `core_sequencia_embalagem`: familia -> produto -> cor, com a
**sortida fechando cada grupo** (e' a que exige garimpar no monte misto).
Pedido multi-item vai para o fim, agrupado — sao os que exigem conferencia.

Uso:
    from core_lista_montagem_pdf import gerar_lista_montagem
    r = gerar_lista_montagem(dados_separacao)     # bytes prontos
"""

from __future__ import annotations
import core_env_loader

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# A4 em pontos
LARGURA, ALTURA = 595.0, 842.0
MARGEM = 32.0

PRETO = (0, 0, 0)
CINZA = (0.45, 0.45, 0.45)
FUNDO_SECAO = (0.90, 0.90, 0.92)
FUNDO_ZEBRA = (0.965, 0.965, 0.97)
# Mesma cor do carimbo #N na etiqueta fisica (core_etiqueta_numerar.COR) —
# consistencia visual entre a lista e a etiqueta ao conferir o #m.
VERMELHO = (0.85, 0.10, 0.10)


def nome_legivel(sku: str) -> str:
    """'MEINVMAY1013540-SOR3_KIT3' -> 'Meia Inv FEM 35/40 Kit 3 Sortido'.

    A bancada precisa saber O QUE PEGAR, nao decorar codigo. Reusa o mesmo
    tradutor do Scanner (`core_scanner_resolver`), que ja' entende a
    taxonomia V5 -- inclusive kit misto ('3 Branco + 3 Preto').
    """
    import re
    if not sku:
        return ""
    try:
        import core_scanner_resolver as rs
        modelo, _spu, _gen = rs.extrair_modelo(sku)
        cor = rs.extrair_cores_detalhadas(sku)
        tam = rs.extrair_tamanho(sku)
    except Exception as e:
        log.warning("Tradutor de SKU indisponivel (%s)", e)
        return sku

    m = re.search(r"_KIT(\d+)", sku.upper())
    kit = f"Kit {m.group(1)}" if m else ""

    partes = [p for p in (modelo, tam, kit, cor) if p]
    # sem modelo reconhecido, o SKU cru e' melhor que uma linha vazia
    return " ".join(partes) if modelo else sku


def _canal_curto(canal: str) -> str:
    c = (canal or "").upper()
    if "SHOPEE" in c:
        return "SHP"
    if "TIKTOK" in c:
        return "TTK"
    if "MERCADO" in c or c == "ML":
        return "ML"
    return (c[:3] or "?")


def _linhas_por_sku(dados: dict[str, Any]) -> list[tuple]:
    """(qtd, atomo/sku, descricao, n_pedidos) — o que pegar no estoque."""
    try:
        import core_separacao_atomos as csa
        atomos = csa.consolidar_atomos(dados.get("lista_coleta") or [])
        return [(a.get("qtd") or a.get("total_unidades") or 0,
                 a.get("atomo") or a.get("sku") or "",
                 a.get("familia", ""),
                 a.get("pedidos") or a.get("total_pedidos") or 0)
                for a in atomos]
    except Exception as e:
        # Sem a decomposicao em atomos, cai para o SKU de venda -- pior,
        # mas nunca deixa a bancada sem lista.
        log.warning("Decomposicao em atomos indisponivel (%s); usando SKU de venda", e)
        return [(i["total_unidades"], i["sku"], i.get("familia", ""),
                 i.get("total_pedidos", 0))
                for i in (dados.get("lista_coleta") or [])]


def _linhas_por_pedido(dados: dict[str, Any]) -> list[dict[str, Any]]:
    """Pedidos na ordem de montagem da bancada."""
    try:
        import core_sequencia_embalagem as seq
        r = seq.sequenciar(dados)
        return r.get("sequencia") or []
    except Exception as e:
        log.warning("Sequenciador indisponivel (%s); ordem original", e)
        saida = []
        for g in ("pedidos_simples_1un", "pedidos_simples_multi_un",
                  "pedidos_multi_itens"):
            saida.extend(dados.get(g) or [])
        return saida


def checar_gaps_sequencia(dados: dict[str, Any]) -> dict[str, Any]:
    """Confere se algum numero da sequencia da Olist "pulou" na lista.

    Jota (26/08): "eles são sequenciais... do primeiro ao último deve estar
    aí na lista, dentro da sequência". Como `numero_olist` e' sequencial
    (443, 496, 528...), um buraco no meio do intervalo pode ser normal
    (pedido ja despachado antes, cancelado, ou de outro canal) OU um
    pedido esquecido/perdido. So' o segundo caso interessa avisar.

    Botao SEPARADO (nao roda sempre): bate o Olist de novo pelas
    situacoes 0-4 (ainda nao chegou em "pronto pra envio") pra saber se
    cada numero faltante ainda esta' pendente segundo o Olist.

    ⚠️ CORRIGIDO 26/08 (achado real: pedidos #527 e #544, cancelados pelo
    comprador no TikTok ha' dias, apareciam aqui como "pendente" porque a
    `situacao` da API do Olist nao reflete cancelamento — so' a TELA do
    Olist sabia. "Terá que ver nas API de todos... pois todos cancelam"
    (Jota) — o mesmo bug existe em ML. Por isso cada candidato agora passa
    por `verificar_cancelamento()`, que consulta a API do PROPRIO canal
    (TikTok/Shopee/ML) antes de entrar na lista de alerta.

    Retorna:
        {"min", "max", "presentes", "faltando_pendente", "verificados"}
    """
    import core_olist as ol
    import core_scanner_resolver as resolver

    presentes: set[int] = set()
    for chave in ("pedidos_simples_1un", "pedidos_simples_multi_un",
                  "pedidos_multi_itens"):
        for p in dados.get(chave) or []:
            num = str(p.get("numero_olist") or "").strip()
            if num.isdigit():
                presentes.add(int(num))

    if not presentes:
        return {"min": None, "max": None, "presentes": 0,
                "faltando_pendente": [], "verificados": 0}

    minimo, maximo = min(presentes), max(presentes)
    intervalo = set(range(minimo, maximo + 1))
    ausentes = sorted(intervalo - presentes)

    if not ausentes:
        return {"min": minimo, "max": maximo, "presentes": len(presentes),
                "faltando_pendente": [], "verificados": 0}

    # So' bate a API pelos que realmente faltam -- nao pelo intervalo inteiro.
    client = ol.OlistClient()
    pendentes_abertos: dict[str, dict] = {}
    for situacao in (0, 1, 2, 3, 4):
        try:
            for p in client.listar_pedidos(situacao=situacao, limit=100):
                num = str(p.get("numeroPedido") or p.get("numero") or "")
                if num:
                    pendentes_abertos[num] = p
        except Exception as exc:
            log.warning("Falha ao checar situacao %s p/ gap: %s", situacao, exc)

    faltando_pendente = []
    for n in ausentes:
        p = pendentes_abertos.get(str(n))
        if not p:
            continue
        ecom = p.get("ecommerce") or {}
        num_ecommerce = ecom.get("numeroPedidoEcommerce") or ""
        canal_nome = ecom.get("nome") or ""

        # Cruza com o cancelamento REAL do canal antes de alertar — a
        # `situacao` do Olist sozinha ja' se provou insuficiente (achado
        # real 26/08, ver docstring acima).
        if num_ecommerce and canal_nome:
            try:
                info_cancel = resolver.verificar_cancelamento(num_ecommerce, canal_nome)
                if info_cancel.get("cancelado"):
                    continue  # cancelado de verdade no canal -- nao e' alerta
            except Exception as exc:
                log.warning("Falha ao checar cancelamento real de #%s: %s", n, exc)

        faltando_pendente.append({
            "numero_olist": n,
            "cliente": (p.get("cliente") or {}).get("nome") or "",
            "situacao": ol.SITUACAO_PEDIDO.get(str(p.get("situacao")), ""),
            "canal": canal_nome,
        })

    return {"min": minimo, "max": maximo, "presentes": len(presentes),
            "faltando_pendente": faltando_pendente,
            "verificados": len(ausentes)}


def gerar_montagem_10x15(
    dados: dict[str, Any],
    saida: str | Path | None = None,
) -> dict[str, Any]:
    """So' a PARTE 2 (montar os pedidos), em etiqueta 10x15.

    Mesmo rolo da LABEL 2 — nao precisa trocar papel. A lista de coleta ja'
    tinha versao 10x15 (`core_lista_separacao_pdf`); esta e' a outra metade,
    para quem monta ler do lado da pilha de etiquetas (Jota, 25/08).

    Uma linha por pedido, agrupada por produto/cor, na MESMA ordem da pilha.
    """
    import fitz

    # ⚠️ 100/150 sao MILIMETROS: divide por 25.4, nao 2.54. Com 2.54 a pagina
    # saiu 100x150 CM (a folha inteira virou um cartaz) -- erro pego no
    # preview em 25/08.
    L, A = 100 / 25.4 * 72, 150 / 25.4 * 72     # 10x15 cm = 283.5 x 425.2 pt
    M = 10.0
    por_pedido = _linhas_por_pedido(dados)

    doc = fitz.open()
    pag = doc.new_page(width=L, height=A)
    y = M

    def nova():
        nonlocal pag, y
        pag = doc.new_page(width=L, height=A)
        y = M

    pag.insert_text((M, y + 9), "MONTAR OS PEDIDOS", fontname="hebo", fontsize=9)
    pag.insert_text((L - M, y + 9), datetime.now().strftime("%d/%m %H:%M"),
                    fontname="helv", fontsize=6, color=CINZA)
    y += 14
    pag.insert_text((M, y + 7), f"{len(por_pedido)} pedidos · ordem da bancada",
                    fontname="helv", fontsize=6.5, color=CINZA)
    y += 12
    pag.draw_line(fitz.Point(M, y), fitz.Point(L - M, y), color=PRETO, width=0.8)
    y += 9

    grupo = None
    for i, p in enumerate(por_pedido, 1):
        g = str(p.get("combinacao") or p.get("grupo") or "")
        itens = p.get("itens") or []
        multi = len(itens) > 1
        alt = 11 + (8 * (len(itens) - 1) if multi else 0)

        if g != grupo:
            if y + alt + 16 > A - M:
                nova()
            grupo = g
            _leg = nome_legivel(g) if g else ""
            _tit = _leg if (_leg and _leg != g) else (g or "OUTROS")
            pag.draw_rect(fitz.Rect(M, y, L - M, y + 11), color=None, fill=FUNDO_SECAO)
            pag.insert_text((M + 3, y + 8), _tit[:44], fontname="hebo", fontsize=6.5)
            y += 13
        elif y + alt > A - M:
            nova()

        pag.draw_rect(fitz.Rect(M + 1, y + 1, M + 7, y + 7), color=PRETO, width=0.5)
        if multi:
            pag.insert_text((M + 11, y + 7), f"⚠ {len(itens)} itens",
                            fontname="hebo", fontsize=6.5)
            yy = y + 7
            for it in itens:
                yy += 8
                pag.insert_text((M + 16, yy),
                                f"{it.get('quantidade')}x {nome_legivel(str(it.get('sku') or ''))[:42]}",
                                fontname="helv", fontsize=6)
        else:
            it = itens[0] if itens else {}
            pag.insert_text((M + 11, y + 7), f"{it.get('quantidade', 1)}x",
                            fontname="hebo", fontsize=7)
            pag.insert_text((M + 24, y + 7),
                            nome_legivel(str(it.get("sku") or ""))[:44],
                            fontname="helv", fontsize=6.5)
        # ⚠️ #m = numero SEQUENCIAL da Olist (Jota, 26/08): conferir esta
        # lista contra o pedido de venda/NF sem trocar de tela — mesma logica
        # do "#N #m" que ja' vai carimbado na etiqueta fisica
        # (core_etiquetas_na_esteira.py). Sem match (pedido fora do Olist,
        # numero vazio) nao escreve nada, nunca quebra a linha.
        _num_olist = str(p.get("numero_olist") or "").strip()
        _rotulo_pedido = f"#{_num_olist}" if _num_olist else ""
        pag.insert_text((L - M - 42, y + 7), _rotulo_pedido,
                        fontname="hebo", fontsize=6, color=VERMELHO)
        pag.insert_text((L - M - 16, y + 7), _canal_curto(p.get("canal", "")),
                        fontname="helv", fontsize=5.5, color=CINZA)
        y += alt

    dados_pdf = doc.tobytes()
    paginas = len(doc)
    doc.close()
    if saida:
        Path(saida).write_bytes(dados_pdf)
    return {"bytes": dados_pdf, "paginas": paginas,
            "pedidos": len(por_pedido), "arquivo": str(saida) if saida else None}


def gerar_lista_montagem(
    dados: dict[str, Any],
    saida: str | Path | None = None,
    *,
    titulo: str = "LISTA DE SEPARACAO E MONTAGEM",
) -> dict[str, Any]:
    """PDF A4 com as duas visoes. Devolve {"bytes", "paginas", ...}."""
    import fitz

    por_sku = _linhas_por_sku(dados)
    por_pedido = _linhas_por_pedido(dados)

    doc = fitz.open()
    pag = doc.new_page(width=LARGURA, height=ALTURA)
    y = MARGEM

    def nova_pagina():
        nonlocal pag, y
        pag = doc.new_page(width=LARGURA, height=ALTURA)
        y = MARGEM

    def espaco(preciso: float):
        nonlocal y
        if y + preciso > ALTURA - MARGEM - 14:
            nova_pagina()

    # ---------------- cabecalho ----------------
    pag.insert_text((MARGEM, y + 14), titulo, fontname="hebo", fontsize=15)
    total_pecas = dados.get("total_pecas", 0)
    pag.insert_text((LARGURA - MARGEM, y + 14),
                    datetime.now().strftime("%d/%m/%Y %H:%M"),
                    fontname="helv", fontsize=9, color=CINZA)
    y += 24
    pag.insert_text((MARGEM, y + 10),
                    f"{dados.get('total_pedidos', 0)} pedidos  ·  "
                    f"{total_pecas} pecas  ·  "
                    f"{dados.get('total_skus_distintos', 0)} SKUs",
                    fontname="helv", fontsize=9, color=CINZA)
    y += 22
    pag.draw_line(fitz.Point(MARGEM, y), fitz.Point(LARGURA - MARGEM, y),
                  color=PRETO, width=1.2)
    y += 16

    # ---------------- 1. POR SKU ----------------
    pag.insert_text((MARGEM, y + 11), "1. PEGAR NO ESTOQUE (por SKU)",
                    fontname="hebo", fontsize=11)
    y += 22

    familia = None
    for qtd, atomo, fam, npeds in por_sku:
        if fam != familia:
            espaco(30)
            familia = fam
            pag.draw_rect(fitz.Rect(MARGEM, y, LARGURA - MARGEM, y + 15),
                          color=None, fill=FUNDO_SECAO)
            pag.insert_text((MARGEM + 5, y + 11), (fam or "SEM FAMILIA").upper(),
                            fontname="hebo", fontsize=8.5)
            y += 19

        espaco(16)
        # quadradinho para marcar a mao
        pag.draw_rect(fitz.Rect(MARGEM + 2, y + 2, MARGEM + 11, y + 11),
                      color=PRETO, width=0.7)
        pag.insert_text((MARGEM + 18, y + 10), f"{qtd:>4} un",
                        fontname="hebo", fontsize=10)
        _nome = nome_legivel(str(atomo))
        if _nome and _nome != str(atomo):
            pag.insert_text((MARGEM + 68, y + 10), _nome[:52],
                            fontname="helv", fontsize=9.5)
        else:
            pag.insert_text((MARGEM + 68, y + 10), str(atomo)[:52],
                            fontname="helv", fontsize=9)
        if npeds:
            pag.insert_text((LARGURA - MARGEM - 52, y + 10),
                            f"{npeds} ped", fontname="helv", fontsize=8,
                            color=CINZA)
        y += 15

    # ---------------- 2. POR PEDIDO ----------------
    y += 12
    espaco(40)
    pag.draw_line(fitz.Point(MARGEM, y), fitz.Point(LARGURA - MARGEM, y),
                  color=PRETO, width=1.2)
    y += 16
    pag.insert_text((MARGEM, y + 11),
                    "2. MONTAR OS PEDIDOS (na ordem da bancada)",
                    fontname="hebo", fontsize=11)
    y += 20
    pag.insert_text((MARGEM, y + 9),
                    "mesma ordem em que as etiquetas saem — pilha de papel e "
                    "pilha de etiqueta conversam",
                    fontname="helv", fontsize=8, color=CINZA)
    y += 18

    grupo_atual = None
    for i, p in enumerate(por_pedido, 1):
        g = str(p.get("combinacao") or p.get("grupo") or "")
        if g != grupo_atual:
            espaco(28)
            grupo_atual = g
            pag.draw_rect(fitz.Rect(MARGEM, y, LARGURA - MARGEM, y + 15),
                          color=None, fill=FUNDO_SECAO)
            # O grupo vem como SPU+cor cru ('MEINVMAY1013540SOR'). Traduz
            # para o nome de bancada; se nao reconhecer, mostra o cru.
            _leg = nome_legivel(g) if g else ""
            _tit = _leg if (_leg and _leg != g) else (g or "OUTROS")
            pag.insert_text((MARGEM + 5, y + 11), _tit[:70],
                            fontname="hebo", fontsize=8.5)
            y += 19

        itens = p.get("itens") or []
        multi = len(itens) > 1
        alt = 15 + (11 * (len(itens) - 1) if multi else 0)
        espaco(alt + 4)

        if i % 2 == 0:
            pag.draw_rect(fitz.Rect(MARGEM, y, LARGURA - MARGEM, y + alt),
                          color=None, fill=FUNDO_ZEBRA)

        # ⚠️ Sem numero de pedido: a bancada nao usa (decisao do Jota, 25/08).
        # O que importa e' O QUE PEGAR. O numero fica na etiqueta, que vem
        # depois e na mesma ordem.
        pag.draw_rect(fitz.Rect(MARGEM + 2, y + 3, MARGEM + 11, y + 12),
                      color=PRETO, width=0.7)
        pag.insert_text((MARGEM + 18, y + 11), f"{i}.",
                        fontname="hebo", fontsize=9, color=CINZA)

        if multi:
            pag.insert_text((MARGEM + 44, y + 11),
                            f"⚠ {len(itens)} itens — conferir",
                            fontname="hebo", fontsize=9)
            yy = y + 11
            for it in itens:
                yy += 11
                pag.insert_text((MARGEM + 56, yy),
                                f"{it.get('quantidade')}x  "
                                f"{nome_legivel(str(it.get('sku') or ''))[:62]}",
                                fontname="helv", fontsize=8.5)
        else:
            it = itens[0] if itens else {}
            q = it.get("quantidade", 1)
            pag.insert_text((MARGEM + 44, y + 11), f"{q}x",
                            fontname="hebo", fontsize=9.5)
            pag.insert_text((MARGEM + 68, y + 11),
                            nome_legivel(str(it.get("sku") or ""))[:64],
                            fontname="helv", fontsize=9.5)

        # #m = numero SEQUENCIAL da Olist -- confere esta lista contra o
        # pedido de venda/NF sem trocar de tela (Jota, 26/08). Mesma logica
        # do "#N #m" que ja' vai na etiqueta fisica.
        _num_olist = str(p.get("numero_olist") or "").strip()
        if _num_olist:
            pag.insert_text((LARGURA - MARGEM - 108, y + 11), f"#{_num_olist}",
                            fontname="hebo", fontsize=8, color=VERMELHO)
        pag.insert_text((LARGURA - MARGEM - 62, y + 11),
                        _canal_curto(p.get("canal", "")),
                        fontname="helv", fontsize=8, color=CINZA)
        if p.get("etiqueta_emitida"):
            pag.insert_text((LARGURA - MARGEM - 28, y + 11), "etiq",
                            fontname="helv", fontsize=7, color=CINZA)
        y += alt

    # rodape com numero de pagina
    for n, pg in enumerate(doc, 1):
        pg.insert_text((LARGURA / 2 - 18, ALTURA - 20),
                       f"{n} / {len(doc)}", fontname="helv", fontsize=8,
                       color=CINZA)

    dados_pdf = doc.tobytes()
    paginas = len(doc)
    doc.close()

    if saida:
        Path(saida).write_bytes(dados_pdf)

    return {
        "bytes": dados_pdf,
        "paginas": paginas,
        "skus": len(por_sku),
        "pedidos": len(por_pedido),
        "total_pecas": total_pecas,
        "arquivo": str(saida) if saida else None,
    }
