# ==============================================================================
# NOME DO SCRIPT: core_etiqueta_numerar.py
# DESCRICAO: Carimba um numero de ordem no canto de cada etiqueta do PDF
# FUNCAO: Da' ao operador uma referencia visual para saber, de relance, se
#         pulou alguma etiqueta na hora de embalar.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 19/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Regra do Jota (2026-08-19):

    "vc consegue em algum rodape de topo esquerdo superior colocar um numeral
     de ordem da etiqueta no PDF? na hora da impressao... facilita ver
     visualmente se pulei alguma... claro fora da janela da impressao"

## Onde carimba

Canto SUPERIOR ESQUERDO, dentro da margem de 3mm que o normalizador ja'
deixa em volta do conteudo. A etiqueta da transportadora nunca ocupa esse
canto — e' area de respiro do proprio layout.

⚠️ "fora da janela de impressao" = fora da AREA UTIL da etiqueta, nao fora da
pagina. Carimbar fora da pagina simplesmente nao imprime. O numero fica na
borda, onde ha papel mas nao ha conteudo da transportadora.

## Por que passo separado, e nao dentro do normalizador

`core_etiqueta_normalizar.py` ja' esta' validado em producao (recorte que
preserva a nitidez do QR). Numerar e' outra responsabilidade: quem quiser
etiqueta sem numero so' nao chama esta funcao. Misturar as duas faria o
recorte carregar um estado que nao e' dele.

Uso:
    from core_etiqueta_numerar import numerar_pdf
    numerar_pdf("etiquetas.pdf")            # sobrescreve
    numerar_pdf("in.pdf", "out.pdf")        # arquivo novo
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MM = 72 / 25.4

# Canto INFERIOR DIREITO (Jota, 2026-08-19: "mt grande... pode ser menor e
# vamos mudar isso para o canto inferior direito").
#
# ⚠️ No topo esquerdo o numero encostava no logo do canal — a 22pt ele
# invadia a arte da transportadora. Embaixo a direita ha' faixa livre em
# Correios e Shopee, e a fonte menor cabe folgada.
MARGEM_X = 3 * MM           # distancia da borda DIREITA
MARGEM_Y = 3 * MM           # distancia da borda INFERIOR

TAMANHO_FONTE = 12          # legivel na bancada sem competir com a etiqueta
COR = (0.85, 0.10, 0.10)    # vermelho: nao se confunde com nada da etiqueta


def _ponto_inferior_direito(pagina, texto: str):
    """Ancora do texto no canto inferior direito da pagina.

    ⚠️ `insert_text` ancora na ESQUERDA e na BASE do texto. Para encostar na
    direita e' preciso DESCONTAR a largura da string — sem isso o "#12" nasce
    na margem e vaza para fora da pagina (e some na impressao).
    """
    import fitz

    largura = fitz.get_text_length(texto, fontname="helv",
                                   fontsize=TAMANHO_FONTE)
    return fitz.Point(pagina.rect.width - MARGEM_X - largura,
                      pagina.rect.height - MARGEM_Y)


def numerar_pdf(entrada: str | Path,
                saida: str | Path | None = None,
                *,
                inicio: int = 1,
                total: int | None = None) -> dict[str, Any]:
    """Carimba `1`, `2`, `3`... no canto de cada pagina.

    Args:
        entrada: PDF com as etiquetas ja' normalizadas em 10x15.
        saida: destino. `None` sobrescreve a entrada.
        inicio: primeiro numero (util ao numerar lotes em sequencia).
        total: aceito por compatibilidade, mas NAO entra no texto — ver abaixo.

    Retorna:
        {"paginas", "arquivo"}
    """
    import fitz  # PyMuPDF

    entrada = Path(entrada)
    doc = fitz.open(entrada)

    n_total = total if total is not None else doc.page_count

    for i, pagina in enumerate(doc, start=inicio):
        # ⚠️ Formato `#N`, IDENTICO ao da etiqueta 40x25
        # (`core_etiquetas_pedido.py:178` -> `f"#{sequencia}"`).
        # Regra do Jota (2026-08-19): "#numeral... igual tem na etiqueta
        # pequena... assim casaria elas".
        # Nao usar "3/9": a etiqueta pequena imprime so' "#3", e dois formatos
        # diferentes para a mesma ordem obrigam o operador a traduzir de
        # cabeca na hora de parear caixa com envelope.
        pagina.insert_text(_ponto_inferior_direito(pagina, f"#{i}"), f"#{i}",
                           fontsize=TAMANHO_FONTE, fontname="helv",
                           color=COR, overlay=True)

    # ⚠️ PyMuPDF nao sobrescreve um arquivo que ele mesmo tem aberto: grava
    # num temporario e troca. Mesma armadilha do normalizador.
    destino = Path(saida) if saida else entrada
    if destino == entrada:
        tmp = entrada.with_suffix(".__num__.pdf")
        doc.save(tmp)
        doc.close()
        tmp.replace(entrada)
    else:
        doc.save(destino)
        doc.close()

    log.info("Numeradas %d etiquetas em %s", n_total, destino.name)
    return {"paginas": n_total, "arquivo": str(destino)}


def numerar_na_ordem(entrada: str | Path,
                     ordem_paginas: list[int],
                     saida: str | Path | None = None) -> dict[str, Any]:
    """Numera E REORDENA o PDF para casar com a sequencia de embalagem.

    Regra do Jota (2026-08-19): *"lembrando que as etiquetas sao geradas em
    ordem de facilitar embalar... conforme separacao"*.

    ⚠️ Numerar na ordem em que a API devolve NAO serve: medido em 19/08, a
    etiqueta #9 do PDF era o 2o pedido da sequencia de embalagem. O operador
    teria que garimpar a etiqueta a cada caixa — pior do que nao ter numero.

    Aqui o PDF e' REMONTADO na ordem da esteira e so' entao numerado, de modo
    que a pilha de etiquetas e a pilha de caixas andem juntas.

    Args:
        entrada: PDF das etiquetas.
        ordem_paginas: indices 0-based na ordem desejada. Ex: [2,0,1] poe a
            3a pagina em primeiro. Paginas fora da lista sao DESCARTADAS —
            passe todas as que quiser manter.
        saida: destino; `None` sobrescreve.
    """
    import fitz

    entrada = Path(entrada)
    origem = fitz.open(entrada)

    if not ordem_paginas:
        origem.close()
        return numerar_pdf(entrada, saida)

    novo = fitz.open()
    for idx in ordem_paginas:
        if 0 <= idx < origem.page_count:
            novo.insert_pdf(origem, from_page=idx, to_page=idx)

    for i, pagina in enumerate(novo, start=1):
        pagina.insert_text(_ponto_inferior_direito(pagina, f"#{i}"), f"#{i}",
                           fontsize=TAMANHO_FONTE, fontname="helv",
                           color=COR, overlay=True)

    destino = Path(saida) if saida else entrada
    if destino == entrada:
        tmp = entrada.with_suffix(".__num__.pdf")
        novo.save(tmp)
        novo.close()
        origem.close()
        tmp.replace(entrada)
    else:
        novo.save(destino)
        novo.close()
        origem.close()

    log.info("Reordenadas e numeradas %d etiquetas em %s",
             len(ordem_paginas), destino.name)
    return {"paginas": len(ordem_paginas), "arquivo": str(destino)}


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("uso: python core_etiqueta_numerar.py <arquivo.pdf> [saida.pdf]")
        raise SystemExit(1)

    r = numerar_pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f"  {r['paginas']} etiquetas numeradas -> {r['arquivo']}")
