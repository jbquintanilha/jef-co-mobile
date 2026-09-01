# ==============================================================================
# NOME DO SCRIPT: core_etiqueta_normalizar.py
# DESCRICAO: Normaliza PDF de etiqueta para pagina 10x15cm cheia (sem sobra)
# FUNCAO: A Shopee devolve a etiqueta 105x148mm desenhada no canto de uma folha
#         A4 — sobra 2/3 de papel em branco. Este motor recorta a area util e
#         entrega paginas 10x15 iguais as do cartao de agradecimento.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Medido em producao (2026-08-16), CORRIGIDO em 2026-08-25:

    TikTok  -> pagina 298x420pt (105.1x148.2mm) — NAO e' 10x15 real, e'
               5.1mm mais LARGA que o alvo (283.5x425.2pt = 100x150mm).
               A tolerancia antiga (`< 20pt`) deixava passar sem corrigir
               por estar "quase" certo — na impressora os 5.1mm de sobra
               cortam a lateral da etiqueta (achado real, 25/08, PDF do
               Jota saiu com a etiqueta TikTok cortada).
    Shopee  -> pagina 595x842pt (210x297mm)  = A4, mas a TINTA ocupa so'
               (0,1)-(297,419) = 105x148mm no canto superior esquerdo

Ou seja: NENHUM dos dois canais nasce no tamanho exato 100x150mm — os dois
passam pelo recorte. Por isso RECORTAMOS em vez de escalar — recorte
preserva a nitidez do QR code e do codigo de barras, escala reamostra e
degrada.

O alvo (10x15cm = 283.5x425.2pt) e' o mesmo tamanho do cartao de agradecimento,
para que etiqueta e cartao saiam identicos na LABEL 2.

Uso:
    from core_etiqueta_normalizar import normalizar_10x15
    r = normalizar_10x15("etiquetas_shopee.pdf")
    print(r["saida"], r["paginas"])
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 10x15 cm em pontos PostScript (1 pt = 1/72") — mesmo tamanho do cartao
LARGURA_10X15 = 100 / 25.4 * 72   # 283.46 pt
ALTURA_10X15 = 150 / 25.4 * 72    # 425.20 pt

# Margem branca deixada em volta da etiqueta ao encaixar na pagina final.
# 2pt (~0,7mm) era apertado demais -- impressora termica corta/desalinha
# perto da borda fisica do rolo (Jota, 31/08/2026: "coloque uma margem de
# seguranca... evite impressao nos limites"). 6pt (~2,1mm) da folga real sem
# encolher a etiqueta de forma perceptivel.
MARGEM_PT = 6.0

# Abaixo disso consideramos que a pagina esta' em branco (ruido de render)
AREA_MINIMA_PT2 = 100.0

# DPI do achatamento de transparencia. 300 e' o nativo da LABEL 2.
ACHATAR_DPI = 300


def achatar_transparencia(pdf: str | Path, saida: str | Path | None = None,
                          *, dpi: int = ACHATAR_DPI) -> Path:
    """Resolve alpha/SMask rasterizando cada pagina sobre branco OPACO.

    ⚠️ Achado real 31/08/2026 (Jota: "sumiu o logo da marca, aparece no pdf,
    mas na impressao nao"): tanto o logo do cartao de agradecimento quanto o
    logo no topo da etiqueta sao imagens RGB praticamente PRETAS cuja forma
    existe apenas no canal alpha (SMask). Visualizador de PDF compoe o alpha
    e mostra certo; driver de termica e' 1-bit monocromatico, nao faz
    blending -- descarta a imagem e o logo some no papel.

    Rasterizando com `alpha=False`, a composicao acontece AQUI e o que chega
    na impressora ja' e' pixel solido.
    """
    import fitz

    entrada = Path(pdf)
    destino = Path(saida) if saida else entrada

    origem = fitz.open(str(entrada))
    novo = fitz.open()
    for pno in range(origem.page_count):
        pag = origem[pno]
        pix = pag.get_pixmap(dpi=dpi, alpha=False)
        nova = novo.new_page(width=pag.rect.width, height=pag.rect.height)
        nova.insert_image(nova.rect, pixmap=pix)

    # deflate+garbage: sem isso o raster de 300 DPI sai ~6 MB por pagina.
    temporario = destino.with_name(f"{destino.stem}.__flat__.pdf")
    novo.save(str(temporario), deflate=True, garbage=4)
    novo.close()
    origem.close()

    if destino.exists():
        destino.unlink()
    temporario.replace(destino)
    return destino


def _bbox_tinta(pagina) -> Any:
    """Retangulo que envolve todo o conteudo visivel da pagina.

    Junta texto, vetores e imagens. E' o que separa a etiqueta do papel em
    branco em volta dela.
    """
    import fitz

    blocos: list[Any] = []

    for b in pagina.get_text("blocks"):
        blocos.append(fitz.Rect(b[:4]))

    for g in pagina.get_drawings():
        blocos.append(g["rect"])

    for img in pagina.get_images(full=True):
        try:
            blocos.extend(pagina.get_image_rects(img[0]))
        except Exception:  # imagem sem retangulo resolvivel — ignora
            pass

    # Descarta retangulos degenerados ou maiores que a propria pagina
    pag = pagina.rect
    validos = [
        r for r in blocos
        if r.width > 0 and r.height > 0 and r.get_area() > AREA_MINIMA_PT2
        and r.x0 >= pag.x0 - 1 and r.y0 >= pag.y0 - 1
        and r.x1 <= pag.x1 + 1 and r.y1 <= pag.y1 + 1
    ]

    if not validos:
        return None

    return fitz.Rect(
        min(r.x0 for r in validos),
        min(r.y0 for r in validos),
        max(r.x1 for r in validos),
        max(r.y1 for r in validos),
    )


def normalizar_10x15(
    pdf_entrada: str | Path,
    saida: str | Path | None = None,
    *,
    forcar: bool = False,
    achatar: bool = False,
) -> dict[str, Any]:
    """Reescreve o PDF com todas as paginas em 10x15cm, sem sobra de papel.

    Args:
        pdf_entrada: PDF original (Shopee A4 ou TikTok A6).
        saida: destino. Default = `<entrada>_10x15.pdf`.
        forcar: reprocessa mesmo paginas que ja' estao no tamanho certo.
        achatar: resolve alpha/SMask rasterizando sobre branco opaco (ver
            `achatar_transparencia`). Default False — o PDF final ja passa
            por `core_etiqueta_termica.blindar_para_termica`, que rasteriza
            tudo em 1-bit e resolve o alpha junto. Achatar aqui tambem so'
            gastaria tempo rasterizando duas vezes.

    Retorna:
        {"saida", "paginas", "recortadas", "ja_ok", "vazias"}
    """
    import fitz

    entrada = Path(pdf_entrada)
    if not entrada.exists():
        raise FileNotFoundError(f"PDF nao encontrado: {entrada}")

    destino = Path(saida) if saida else entrada.with_name(f"{entrada.stem}_10x15.pdf")

    origem = fitz.open(entrada)
    novo = fitz.open()

    recortadas = ja_ok = vazias = 0

    for i, pagina in enumerate(origem):
        bbox = _bbox_tinta(pagina)

        if bbox is None:
            vazias += 1
            log.warning("Pagina %d sem conteudo detectavel — copiada inteira.", i + 1)
            bbox = pagina.rect

        # Ja' esta' em 10x15 e ocupa a pagina toda? Passa direto.
        # ⚠️ Tolerancia apertada de 20pt (~7mm) para 2pt (~0.7mm) em 25/08:
        # a pagina do TikTok (298x420pt) tem 14.5pt de excesso de largura,
        # cabia dentro do "< 20" antigo e passava sem recorte — a sobra
        # cortava na impressora. 2pt so' deixa passar erro de arredondamento
        # real, nao mais uma pagina 5mm maior que o alvo.
        quase_certo = (
            abs(pagina.rect.width - LARGURA_10X15) < 2
            and abs(pagina.rect.height - ALTURA_10X15) < 2
        )
        if quase_certo and not forcar:
            ja_ok += 1
            novo.insert_pdf(origem, from_page=i, to_page=i)
            continue

        # Encaixa a tinta na pagina 10x15 preservando a proporcao (sem esticar)
        util_l = LARGURA_10X15 - 2 * MARGEM_PT
        util_a = ALTURA_10X15 - 2 * MARGEM_PT
        escala = min(util_l / bbox.width, util_a / bbox.height)

        largura = bbox.width * escala
        altura = bbox.height * escala
        alvo = fitz.Rect(
            (LARGURA_10X15 - largura) / 2,
            (ALTURA_10X15 - altura) / 2,
            (LARGURA_10X15 - largura) / 2 + largura,
            (ALTURA_10X15 - altura) / 2 + altura,
        )

        destino_pag = novo.new_page(width=LARGURA_10X15, height=ALTURA_10X15)
        destino_pag.show_pdf_page(alvo, origem, i, clip=bbox)
        recortadas += 1

    # ⚠️ O PyMuPDF nao sobrescreve um arquivo que ainda esta' aberto para
    # leitura (Permission denied no Windows). Grava num temporario e so'
    # depois troca — assim `saida == entrada` funciona.
    temporario = destino.with_name(f"{destino.stem}.__tmp__.pdf")
    novo.save(temporario)
    novo.close()
    origem.close()

    if destino.exists():
        destino.unlink()
    temporario.replace(destino)

    if achatar:
        try:
            achatar_transparencia(destino)
        except Exception as exc:  # nunca derrubar o lote por causa disso
            log.warning("Achatamento de transparencia falhou em %s: %s",
                        destino.name, exc)

    log.info(
        "Normalizado %s -> %s (%d recortadas, %d ja ok, %d vazias)",
        entrada.name, destino.name, recortadas, ja_ok, vazias,
    )

    return {
        "saida": str(destino),
        "paginas": recortadas + ja_ok + vazias,
        "recortadas": recortadas,
        "ja_ok": ja_ok,
        "vazias": vazias,
    }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("uso: python core_etiqueta_normalizar.py <arquivo.pdf>")
        raise SystemExit(1)

    r = normalizar_10x15(sys.argv[1])
    print(f"{r['saida']}")
    print(f"  {r['paginas']} paginas — {r['recortadas']} recortadas, "
          f"{r['ja_ok']} ja ok, {r['vazias']} vazias")
