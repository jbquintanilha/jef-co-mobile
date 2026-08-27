# ==============================================================================
# NOME DO SCRIPT: core_etiqueta_com_cartao.py
# DESCRICAO: Monta 1 PDF unico intercalando a etiqueta de envio 10x15 com o
#            cartao de agradecimento do canal correspondente (Shopee/ML/TikTok).
# FUNCAO: Fluxo NOVO e PARALELO -- nao altera tools/separador_etiquetas.py nem
#         nenhum fluxo em producao. So vira principal depois de testado a
#         exaustao (decisao do Comandante, 2026-08-10).
# AUTOR: Terminador (001) / J&F Co.
# VERSAO: 1.0 | DATA: 2026-08-10
# STATUS: BETA -- em teste, fluxo atual segue intocado
# ==============================================================================
"""Etiqueta de envio + cartao de agradecimento no mesmo PDF.

O PDF que vem do Olist traz pedidos de VARIOS canais misturados (Shopee, ML,
TikTok). Cada canal tem um cartao de pos-venda proprio (cupom diferente), entao
nao da pra anexar um cartao unico -- e' preciso descobrir o canal de cada
etiqueta.

Como o canal e' descoberto: extrai o codigo de rastreio do texto da etiqueta e
resolve pedido/canal via `core_scanner_resolver` (o mesmo motor ja validado do
Scanner). Nao usa OCR de imagem -- o texto do PDF ja tem o dado.

Quando NAO resolve o canal (pedido ainda nao sincronizado, por exemplo), o
fluxo PARA e devolve o caso pra quem chamou decidir: tentar resolver ou seguir
sem cartao naquela etiqueta especifica (decisao do Comandante -- nem silencioso,
nem travar o lote inteiro).

⚠️ Este modulo NAO altera `tools/separador_etiquetas.py`. Ele o importa como
biblioteca e usa `extrair_etiquetas()` sem modificar nada.
"""

from __future__ import annotations
import core_env_loader

import logging
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

_RAIZ = Path(r"C:\JF_Automacoes")
if str(_RAIZ / "tools") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "tools"))
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

log = logging.getLogger("core_etiqueta_com_cartao")

# Cartoes de pos-venda por canal -- PDFs prontos, 1 pagina 10x15 cada.
# Decisao (2026-08-10): usar o PDF estatico em vez de rodar os geradores
# (gerar_cartao_*.py) a cada lote. Os geradores sobem um Chromium headless via
# Playwright e levam segundos por chamada; o conteudo do cartao e' fixo por
# canal (cupom nao muda por pedido), entao regerar e' desperdicio.
# Se o cartao mudar, basta regerar o PDF uma vez -- este modulo nao muda.
def _get_cartao(canal: str) -> Path:
    nome = f"cartao_agradecimento_{canal}_10x15.pdf"
    candidatos = [
        Path(__file__).resolve().parent / "cartoes" / nome,
        Path.cwd() / "cartoes" / nome,
        Path(r"c:\jef-co-mobile\cartoes") / nome,
        Path(r"c:\JF_Automacoes\cartoes") / nome,
        Path(
            r"I:\Meu Drive\000 - ERP E-commerce Moda Íntima  Gestão de Estoque e Finanças"
            r" (File responses)\6 - Pós-Venda"
        ) / nome,
    ]
    for c in candidatos:
        try:
            if c.is_file():
                return c
        except Exception:
            pass
    return candidatos[0]


class _CartoesProxy(dict):
    """Permite acesso dinâmico CARTOES['tiktok'] resolvendo o caminho na hora."""
    def __getitem__(self, key: str) -> Path:
        return _get_cartao(key)

    def get(self, key: str, default=None) -> Path:
        return _get_cartao(key)


CARTOES = _CartoesProxy()

# Rastreios: Correios (TikTok/ML) e Shopee.
_RE_RASTREIO_CORREIOS = re.compile(r"\b([A-Z]{2}\d{9}[A-Z]{2})\b")
_RE_RASTREIO_SHOPEE = re.compile(r"\b(BR[0-9A-Z]{13})\b")

# ⚠️ Etiqueta do Mercado Livre nao tem rastreio dos Correios nem da Shopee
# (achado 24/08/2026). Ela traz:
#   Pack ID: 2000014650915375   -> agrupador do pedido
#   47828318513                 -> shipment_id, o code128 GRANDE
#   888002469041186             -> rastreio da transportadora (J&T, Loggi...)
# Nenhum casava com os dois regex acima, entao `resolver_canal` devolvia "" e
# TODA etiqueta do ML saia SEM cartao de agradecimento -- em silencio.
# Ver [[armadilha_ml_etiqueta_codigo_e_shipment]].
_RE_ML_PACK = re.compile(r"PACK\s*ID[:\s]*(\d{10,20})", re.I)
_RE_ML_SHIPMENT = re.compile(r"\b(4\d{10})\b")     # shipment atual: 11 digitos, comeca com 4

# Pack/pedido do ML: 16 digitos iniciados por 2000 (ex: 2000014650915375).
# So' o ML usa esse formato -- serve pra cravar o canal sem consultar nada.
_RE_ML_PACK_NUM = re.compile(r"2000\d{12}")


def intercalar_canal_unico(
    pdf_entrada: str,
    saida: str,
    canal: str,
) -> dict:
    """Intercala cartao quando o canal JA e' conhecido — nao precisa detectar.

    Usado pelos motores que baixam direto da API (core_etiquetas_tiktok_api /
    core_etiquetas_shopee_api): ali todas as etiquetas do lote vem do mesmo
    canal, entao nao ha' o que descobrir por rastreio.

    Resultado: etiqueta -> cartao -> etiqueta -> cartao... 1 cartao por etiqueta,
    na ordem fisica de embalagem.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        from PyPDF2 import PdfReader, PdfWriter  # type: ignore

    canal = _normalizar_canal(canal)
    cartao_path = CARTOES.get(canal)

    if not cartao_path or not Path(cartao_path).is_file():
        return {
            "ok": False,
            "erro": f"Cartao do canal '{canal}' nao encontrado em {cartao_path}",
            "paginas": 0,
            "etiquetas": 0,
        }

    leitor = PdfReader(pdf_entrada)
    cartao = PdfReader(str(cartao_path))
    escritor = PdfWriter()

    for pagina in leitor.pages:
        escritor.add_page(pagina)
        for pag_cartao in cartao.pages:
            escritor.add_page(pag_cartao)

    Path(saida).parent.mkdir(parents=True, exist_ok=True)
    with open(saida, "wb") as fh:
        escritor.write(fh)

    return {
        "ok": True,
        "saida": saida,
        "etiquetas": len(leitor.pages),
        "paginas": len(escritor.pages),
        "canal": canal,
    }


def _e_grid_2x2(pdf_path: str) -> bool:
    """A folha traz 4 etiquetas em grid (Shopee/TikTok) ou 1 so' (ML)?

    ⚠️ `separador_etiquetas.extrair_etiquetas()` picota TODA folha em 4
    quadrantes -- e' o layout do PDF do Olist/Shopee/TikTok. A etiqueta do
    Mercado Livre vem UMA por folha: picotar corta o codigo de barras, a
    DANFE e o endereco no meio, gerando 4 papeis inuteis (achado 24/08/2026).

    Heuristica: numa folha 2x2 os quatro quadrantes tem texto. Numa etiqueta
    unica, o conteudo se concentra e ao menos um quadrante sai praticamente
    vazio. Tambem trata a folha ja' no formato 10x15 (proporcao ~0,67).
    """
    try:
        doc = fitz.open(pdf_path)
        pag = doc[0]
        W, H = pag.rect.width, pag.rect.height
        # folha ja' no tamanho de UMA etiqueta 10x15 -> nunca e' grid
        if abs(W / H - 10.0 / 15.0) < 0.06 and W < 400:
            doc.close()
            return False
        mx, my = W / 2, H / 2
        cheios = 0
        for q in (fitz.Rect(0, 0, mx, my), fitz.Rect(mx, 0, W, my),
                  fitz.Rect(0, my, mx, H), fitz.Rect(mx, my, W, H)):
            if len((pag.get_text("text", clip=q) or "").strip()) > 40:
                cheios += 1
        doc.close()
        return cheios >= 3
    except Exception as e:
        log.warning("Falha ao detectar layout de %s: %s -- assumindo grid", pdf_path, e)
        return True


def _paginas_de_etiqueta(pdf_path: str):
    """Devolve o Document com 1 etiqueta por pagina, respeitando o layout."""
    if _e_grid_2x2(pdf_path):
        from separador_etiquetas import extrair_etiquetas  # sem alterar o modulo
        return extrair_etiquetas(pdf_path)
    # 1 etiqueta por folha (ML): abre como esta', sem picotar
    log.info("%s: 1 etiqueta por folha -- nao vai picotar", Path(pdf_path).name)
    return fitz.open(pdf_path)


def extrair_tracking_da_pagina(pagina) -> str:
    """Le o texto da etiqueta e devolve o rastreio, ou "" se nao achar.

    ⚠️ Busca no texto ORIGINAL, com as quebras de linha preservadas. Colar tudo
    (`re.sub(r"\\s+", "", texto)`) parece limpeza inofensiva mas destroi os
    limites de palavra `\\b` do regex -- o tracking gruda no texto vizinho e
    para de casar. Bug real encontrado no 1o teste com PDF de verdade
    (2026-08-10): 4 etiquetas, 0 trackings detectados.
    """
    try:
        texto = (pagina.get_text("text") or "").upper()
    except Exception as e:
        log.warning("Falha ao ler texto da pagina: %s", e)
        return ""
    m = _RE_RASTREIO_CORREIOS.search(texto) or _RE_RASTREIO_SHOPEE.search(texto)
    if m:
        return m.group(1)
    # Mercado Livre: nao tem rastreio nos formatos acima.
    # ⚠️ Pack ID PRIMEIRO, shipment depois. O Pack (2000...) identifica o canal
    # sozinho, pelo formato; o shipment so' resolve se o envio estiver no
    # indice do Scanner -- e ele so' entra la' quando vira `ready_to_ship`.
    # Preferindo o shipment, etiqueta de envio ainda nao liberado ficava sem
    # cartao a toa.
    m = _RE_ML_PACK.search(texto) or _RE_ML_SHIPMENT.search(texto)
    return m.group(1) if m else ""


def _normalizar_canal(canal_bruto: str) -> str:
    """Mapeia o nome do canal vindo do Olist/indice para a chave de CARTOES."""
    c = (canal_bruto or "").lower()
    if "shopee" in c:
        return "shopee"
    if "mercado" in c or c == "ml":
        return "ml"
    if "tiktok" in c:
        return "tiktok"
    return ""


def _canal_via_olist(tracking: str) -> str:
    """Fallback: procura o rastreio direto no Olist quando o indice nao tem.

    O indice do Scanner tem retencao de 3 dias e poda o que ja foi despachado
    (`core_scanner_populator.DIAS_RETENCAO`). Etiqueta de lote antigo, ou
    reimpressao de pedido ja enviado, simplesmente nao esta la -- sem este
    fallback a etiqueta sairia sem cartao a toa.
    """
    if not tracking:
        return ""
    try:
        from core_olist import OlistClient
        client = OlistClient()
        # varre os pedidos recentes procurando o rastreio (o Olist nao tem
        # busca por codigo de rastreio, so por numero de pedido)
        for situacao in (5, 6, 7, 2, 3):
            for p in client.listar_pedidos_todos(situacao=situacao, max_paginas=3):
                try:
                    det = client.obter_pedido(p["id"])
                except Exception:
                    continue
                tk = ((det.get("transportador") or {}).get("codigoRastreamento") or "").upper()
                if tk and tracking.upper() in tk:
                    return _normalizar_canal((det.get("ecommerce") or {}).get("nome", ""))
    except Exception as e:
        log.warning("Fallback Olist falhou para %s: %s", tracking, e)
    return ""


def resolver_canal(tracking: str, *, usar_olist: bool = True) -> str:
    """Descobre o canal do pedido a partir do rastreio. "" se nao resolver.

    Tenta primeiro o indice local (rapido); se nao achar e ``usar_olist``,
    consulta o Olist (lento, mas cobre pedido fora da janela de retencao).
    """
    if not tracking:
        return ""

    # ⚠️ ANTES de qualquer consulta: o Pack ID do ML (2000 + 12 digitos) e'
    # exclusivo do canal, entao o formato JA' e' a resposta. Deixar essa
    # checagem depois do fallback do Olist fazia toda etiqueta do ML varrer
    # 5 situacoes x 3 paginas de pedidos, um `obter_pedido` por vez -- na
    # pratica o lote inteiro travava.
    if _RE_ML_PACK_NUM.fullmatch(tracking):
        return "ml"

    try:
        from core_scanner_resolver import resolver_codigo
        r = resolver_codigo(tracking)
        if r and r.get("encontrado"):
            canal = _normalizar_canal(r.get("canal", ""))
            if canal:
                return canal
    except Exception as e:
        log.warning("Falha ao resolver canal de %s pelo indice: %s", tracking, e)

    if usar_olist:
        return _canal_via_olist(tracking)
    return ""


def analisar_lote(pdf_entrada: str) -> list[dict]:
    """Le o PDF do Olist e devolve o plano do lote, SEM gerar nada ainda.

    Cada item: {indice, tracking, canal, resolvido}. Permite a UI mostrar os
    problemas e perguntar antes de montar o PDF final.
    """
    doc = _paginas_de_etiqueta(pdf_entrada)
    plano = []
    for i, pagina in enumerate(doc):
        tracking = extrair_tracking_da_pagina(pagina)
        canal = resolver_canal(tracking)
        plano.append({
            "indice": i,
            "tracking": tracking,
            "canal": canal,
            "resolvido": bool(canal),
        })
    doc.close()
    return plano


def montar_pdf(pdf_entrada: str, saida: str,
               canais_por_indice: dict[int, str] | None = None) -> dict:
    """Monta o PDF final intercalando etiqueta + cartao do canal.

    ``canais_por_indice`` sobrescreve o canal detectado (usado quando o operador
    resolve manualmente um caso que nao resolveu sozinho).

    Etiqueta sem canal resolvido entra no PDF mesmo assim, so sem o cartao --
    nunca pular posicao, porque pular desalinha a pilha inteira (mesma regra
    da conferencia de expedicao).
    """
    canais_por_indice = canais_por_indice or {}
    doc_etiquetas = _paginas_de_etiqueta(pdf_entrada)
    saida_doc = fitz.open()

    # cache dos cartoes abertos (evita reabrir o PDF a cada etiqueta)
    cache_cartao: dict[str, fitz.Document] = {}

    com_cartao = 0
    sem_cartao: list[dict] = []

    for i, pagina in enumerate(doc_etiquetas):
        # 1) a etiqueta sempre entra, em ordem
        saida_doc.insert_pdf(doc_etiquetas, from_page=i, to_page=i)

        # 2) descobre o canal (detectado ou informado manualmente)
        tracking = extrair_tracking_da_pagina(pagina)
        canal = canais_por_indice.get(i) or resolver_canal(tracking)

        if not canal:
            sem_cartao.append({"indice": i, "tracking": tracking})
            continue

        caminho = CARTOES.get(canal)
        if not caminho or not caminho.exists():
            log.warning("Cartao do canal %s nao encontrado em %s", canal, caminho)
            sem_cartao.append({"indice": i, "tracking": tracking, "motivo": "cartao ausente"})
            continue

        if canal not in cache_cartao:
            cache_cartao[canal] = fitz.open(str(caminho))
        saida_doc.insert_pdf(cache_cartao[canal])
        com_cartao += 1

    saida_doc.save(saida)
    saida_doc.close()
    for d in cache_cartao.values():
        d.close()
    doc_etiquetas.close()

    return {
        "arquivo": saida,
        "etiquetas": com_cartao + len(sem_cartao),
        "com_cartao": com_cartao,
        "sem_cartao": sem_cartao,
        "ok": not sem_cartao,
    }


def _sku_do_tracking(tracking: str) -> tuple[str, str, str, str]:
    """Devolve (sku, cor, kit, spu) do pedido. Vazios se nao resolver.

    Usa o mesmo motor do Scanner -- ele ja entrega o SKU exato do pedido no
    padrao V5 (`MEMEDMAY1034046-BRA6-PRE6_KIT12`), sem precisar remontar nada.
    """
    if not tracking:
        return "", "", "", ""
    try:
        from core_scanner_resolver import resolver_codigo
        r = resolver_codigo(tracking)
        if r and r.get("encontrado"):
            return (r.get("sku") or "", r.get("cor") or "",
                    r.get("kit") or "", r.get("spu") or "")
    except Exception as e:
        log.warning("Falha ao resolver SKU de %s: %s", tracking, e)
    return "", "", "", ""


def gerar_etiquetas_sku(pdf_entrada: str, saida: str) -> dict:
    """PDF separado com as etiquetas 40x25mm de SKU, 1 por pedido do lote.

    Sai na MESMA ORDEM do PDF de etiquetas de envio -- assim a pilha pequena
    acompanha a pilha grande, e o operador nao precisa caçar qual e' de qual
    (mesma logica de sequencia sincronizada ja acordada para o M2).

    Usa `core_etiquetas.gerar_pdf_etiquetas_40x25`, o motor 40x25mm ja validado
    em impressao fisica real (ver memoria reference_etiqueta_produto_40x25_layout).
    """
    from core_etiquetas import gerar_pdf_etiquetas_40x25

    plano = analisar_lote(pdf_entrada)
    etiquetas = []
    sem_sku = []
    for item in plano:
        tracking = item["tracking"]
        sku, cor, kit, spu = _sku_do_tracking(tracking)
        if not sku:
            sem_sku.append({"indice": item["indice"], "tracking": tracking})
            continue
        etiquetas.append({
            "sku": sku,          # SKU V5 exato do pedido, sem remontar
            "spu": spu,
            "cor": cor or "—",
            "tamanho": "",       # o tamanho ja vem embutido no SKU V5
            "categoria": "",
            "so_barcode": True,  # barcode grande + SKU no rodape
            "quantidade": 1,
        })

    if not etiquetas:
        return {"arquivo": None, "geradas": 0, "sem_sku": sem_sku, "ok": False}

    pdf_bytes = gerar_pdf_etiquetas_40x25(etiquetas, modo_full=False)
    Path(saida).write_bytes(pdf_bytes)
    return {
        "arquivo": saida,
        "geradas": len(etiquetas),
        "sem_sku": sem_sku,
        "ok": not sem_sku,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("uso: python core_etiqueta_com_cartao.py <pdf_do_olist> [saida.pdf]")
        raise SystemExit(1)
    entrada = sys.argv[1]
    destino = sys.argv[2] if len(sys.argv) > 2 else "etiquetas_com_cartao.pdf"

    print("== Analisando lote ==")
    for item in analisar_lote(entrada):
        status = item["canal"] or "❌ NAO RESOLVIDO"
        print(f"  #{item['indice']+1:3d} {item['tracking'] or '(sem tracking)':20s} -> {status}")

    print("\n== Montando PDF (envio + cartao) ==")
    res = montar_pdf(entrada, destino)
    print(f"etiquetas: {res['etiquetas']} | com cartao: {res['com_cartao']} | "
          f"sem cartao: {len(res['sem_cartao'])}")
    print(f"salvo em: {res['arquivo']}")

    print("\n== Gerando etiquetas de SKU 40x25 (PDF separado) ==")
    destino_sku = str(Path(destino).with_name(Path(destino).stem + "_SKU.pdf"))
    res_sku = gerar_etiquetas_sku(entrada, destino_sku)
    if res_sku["geradas"]:
        print(f"geradas: {res_sku['geradas']} | sem SKU: {len(res_sku['sem_sku'])}")
        print(f"salvo em: {res_sku['arquivo']}")
    else:
        print("nenhuma etiqueta de SKU gerada (nenhum pedido resolveu)")
    for s in res_sku["sem_sku"]:
        print(f"  ⚠️ #{s['indice']+1} {s['tracking'] or '(sem tracking)'} — sem SKU")
