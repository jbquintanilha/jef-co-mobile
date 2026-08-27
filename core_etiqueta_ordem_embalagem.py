# ==============================================================================
# NOME DO SCRIPT: core_etiqueta_ordem_embalagem.py
# DESCRICAO: Reordena a pilha de etiquetas por produto/kit e carimba o # Olist
# FUNCAO: A pilha saia na ordem do marketplace (aleatoria pra bancada). Agora
#         sai agrupada por SKU: mesma peca junta, embalagem em serie.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 24/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Ordem de embalagem + rodape com o numero sequencial da Olist.

## O problema (relato do Jota, 24/08/2026)

O `separador_etiquetas.py` recorta o grid 2x2 e entrega 1 etiqueta por
pagina -- mas mantem a ordem em que o marketplace mandou. Na bancada isso e'
aleatorio: Kit 6 preta, calcinha, Kit 24 branca, Kit 6 preta de novo. O
operador anda ate' a prateleira, volta, e anda de novo pela mesma peca.

A lista de coleta ja' sai agrupada por SKU (`core_separacao`). A pilha de
etiquetas nao seguia esse agrupamento -- as duas nunca casavam.

## O que este modulo faz

1. Le cada etiqueta e descobre de que pedido ela e' (numero do marketplace
   impresso na propria etiqueta).
2. Cruza com a lista de separacao pra saber SKU e o # sequencial da Olist.
3. **Reordena** as paginas: mesma peca junta, na mesma ordem da lista de
   coleta (familia -> mais unidades primeiro -> sku).
4. Reduz a etiqueta ~9% e carimba o **#459** na faixa limpa do rodape.

⚠️ NAO altera o conteudo da etiqueta. O recorte e o carimbo acontecem numa
pagina nova; a etiqueta original entra inteira, so' menor. Codigo de barras,
QR e DANFE continuam legiveis -- a reducao e' uniforme.

⚠️ Etiqueta que nao casar com nenhum pedido vai pro FIM da pilha, nunca e'
descartada. Sumir com etiqueta e' pior que sair fora de ordem.

Uso:
    from core_etiqueta_ordem_embalagem import ordenar_por_embalagem
    r = ordenar_por_embalagem("etiquetas.pdf", dados_separacao)
"""

from __future__ import annotations
import core_env_loader

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 10x15 cm em pontos PDF (72 dpi)
LABEL_W_PT = 10.0 / 2.54 * 72
LABEL_H_PT = 15.0 / 2.54 * 72

# Faixa reservada no rodape pro carimbo. 34pt ~ 1,2cm: cabe o numero em corpo
# grande sem espremer a etiqueta. A etiqueta ocupa o resto (~91%).
RODAPE_PT = 34.0

# Numero de pedido do marketplace impresso na etiqueta.
# TikTok/Shopee/ML usam blocos longos de digitos ou alfanumerico.
_RE_PEDIDO = re.compile(r"\b(\d{15,20}|[0-9]{6}[A-Z0-9]{8,12})\b")

# ⚠️ A maioria das etiquetas NAO imprime numero de pedido (achado 25/08: num
# lote real de 20, só 2 casaram por pedido). A etiqueta dos Correios traz o
# rastreio -- e com ESPACOS: "AP 400 835 607 BR". O indice do Scanner resolve
# rastreio -> pedido, entao e' por ali que se casa a etiqueta.
_RE_CORREIOS = re.compile(r"\b([A-Z]{2})\s*(\d{3})\s*(\d{3})\s*(\d{3})\s*([A-Z]{2})\b")
_RE_CORREIOS_JUNTO = re.compile(r"\b([A-Z]{2}\d{9}[A-Z]{2})\b")
_RE_SHOPEE = re.compile(r"\b(BR[0-9A-Z]{13})\b")
_RE_ML_PACK = re.compile(r"\b(2000\d{12})\b")


def _texto_da_pagina(pagina) -> str:
    try:
        return pagina.get_text("text") or ""
    except Exception:
        return ""


def _indice_pedidos(dados: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{numero_do_marketplace: {sku, num_olist, ordem}} a partir da separacao.

    `ordem` vem da posicao do SKU na lista de coleta -- que ja' esta'
    agrupada por familia e quantidade. Assim a pilha de etiquetas sai na
    MESMA sequencia que a bancada vai percorrer a prateleira.
    """
    pos_sku = {i["sku"]: n for n, i in enumerate(dados.get("lista_coleta") or [])}

    idx: dict[str, dict[str, Any]] = {}
    for grupo in ("pedidos_simples_1un", "pedidos_simples_multi_un",
                  "pedidos_multi_itens"):
        for ped in dados.get(grupo) or []:
            itens = ped.get("itens") or []
            sku = itens[0].get("sku") if itens else ""
            num = str(ped.get("numero_ecommerce") or "").strip()
            if not num:
                continue
            idx[num.upper()] = {
                "sku": sku,
                "num_olist": str(ped.get("numero_olist") or ""),
                # multi-item por ultimo: exige conferencia, nao entra no
                # ritmo de embalagem em serie
                "ordem": (1 if grupo == "pedidos_multi_itens" else 0,
                          pos_sku.get(sku, 9999)),
            }
    return idx


def _codigos_da_etiqueta(texto: str) -> list[str]:
    """Todos os codigos uteis impressos na etiqueta, do mais especifico ao menos.

    Ordem importa: rastreio identifica o envio exato; numero de pedido pode
    aparecer em etiqueta de pack com varios pedidos.
    """
    t = (texto or "").upper()
    cods: list[str] = []
    # rastreio Correios COM espacos -> junta (AP 400 835 607 BR -> AP400835607BR)
    for m in _RE_CORREIOS.finditer(t):
        cods.append("".join(m.groups()))
    cods += [m.group(1) for m in _RE_CORREIOS_JUNTO.finditer(t)]
    cods += [m.group(1) for m in _RE_SHOPEE.finditer(t)]
    cods += [m.group(1) for m in _RE_ML_PACK.finditer(t)]
    cods += [m.group(1) for m in _RE_PEDIDO.finditer(t)]
    # remove duplicata preservando a ordem
    return list(dict.fromkeys(cods))


def _casar(texto: str, idx: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Acha o pedido desta etiqueta.

    1. Por numero de pedido, quando a etiqueta imprime (raro).
    2. Por RASTREIO, via indice do Scanner -- o caminho que funciona na
       pratica, porque a etiqueta dos Correios so' traz o rastreio.
    """
    cods = _codigos_da_etiqueta(texto)

    # 1. bate direto no indice de pedidos (etiqueta que imprime o numero)
    for c in cods:
        if c in idx:
            return idx[c]

    # 2. resolve o rastreio -> pedido pelo indice do Scanner
    try:
        import core_scanner_db as db
        for c in cods:
            reg = db.buscar_por_tracking(c) or db.buscar_por_codigo_ml(c)
            if reg:
                num = str(reg.get("pedido_ecommerce") or "").upper()
                if num in idx:
                    return idx[num]
    except Exception as e:
        log.warning("Indice do Scanner indisponivel para casar etiqueta: %s", e)

    return None


def ordenar_por_embalagem(
    pdf_entrada: str | Path,
    dados_separacao: dict[str, Any],
    *,
    saida: str | Path | None = None,
    carimbar: bool = True,
) -> dict[str, Any]:
    """Reordena a pilha por produto/kit e carimba o # sequencial da Olist.

    Espera o PDF **ja' separado** (1 etiqueta por pagina) --
    `separador_etiquetas.extrair_etiquetas()` faz esse passo antes.

    Retorna {"pdf", "total", "casadas", "sem_casar", "ordem"}.
    """
    import fitz

    pdf_entrada = Path(pdf_entrada)
    src = fitz.open(str(pdf_entrada))
    idx = _indice_pedidos(dados_separacao)

    # 1. descobre de quem e' cada pagina
    paginas: list[tuple[tuple, int, str]] = []
    sem_casar = 0
    for n, pag in enumerate(src):
        info = _casar(_texto_da_pagina(pag), idx)
        if info:
            chave = (0, *info["ordem"], n)
            rotulo = f"#{info['num_olist']}" if info["num_olist"] else ""
        else:
            # nunca descarta: joga pro fim da pilha
            chave = (1, 9999, 9999, n)
            rotulo = ""
            sem_casar += 1
        paginas.append((chave, n, rotulo))

    # 2. reordena
    paginas.sort(key=lambda t: t[0])

    # 3. remonta com a faixa do rodape
    out = fitz.open()
    for _, n_origem, rotulo in paginas:
        nova = out.new_page(width=LABEL_W_PT, height=LABEL_H_PT)
        if carimbar:
            # etiqueta ocupa tudo menos a faixa; a reducao e' uniforme, entao
            # codigo de barras e QR nao distorcem
            area = fitz.Rect(0, 0, LABEL_W_PT, LABEL_H_PT - RODAPE_PT)
        else:
            area = nova.rect
        nova.show_pdf_page(area, src, n_origem)

        if carimbar and rotulo:
            base = LABEL_H_PT - RODAPE_PT
            nova.draw_line(fitz.Point(8, base + 3),
                           fitz.Point(LABEL_W_PT - 8, base + 3),
                           color=(0.75, 0.75, 0.75), width=0.6)
            nova.insert_text(fitz.Point(10, base + 26), rotulo,
                             fontname="hebo", fontsize=23, color=(0, 0, 0))

    destino = Path(saida) if saida else pdf_entrada.with_name(
        pdf_entrada.stem + "_ordem_embalagem.pdf")
    out.save(str(destino))
    total = len(paginas)
    out.close()
    src.close()

    log.info("Pilha reordenada: %d etiquetas, %d casadas, %d no fim",
             total, total - sem_casar, sem_casar)
    return {
        "pdf": str(destino),
        "total": total,
        "casadas": total - sem_casar,
        "sem_casar": sem_casar,
        "ordem": [r for _, _, r in paginas],
    }
