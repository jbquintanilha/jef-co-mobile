# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_amazon_olist.py
# DESCRICAO: Baixa as etiquetas de envio da Amazon (Amazon DBA) pelo Olist
# FUNCAO: Poe a Amazon na esteira de etiquetas, no mesmo contrato de
#         Shopee, TikTok e ML.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 27/09/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Etiquetas da Amazon — mesmo contrato de Shopee, TikTok e ML.

## Por que pelo Olist e nao pela API da Amazon

A API da Amazon (SP-API) recusa etiqueta com 403: o app nao tem a funcao de
envio (Direct-to-Consumer Shipping). O Olist, por outro lado, ja' recebe a
etiqueta da Amazon quando a nota e' autorizada com "Enviar para expedicao:
Sim" — ela fica no agrupamento de expedicao "Amazon DBA".

Validado em prod com o pedido 1072 (Amazon 701-9030945-1234636, 27/09): o PDF
baixado por aqui e' identico byte a byte ao que o Jota baixou pela tela.

## ⚠️ O endpoint devolve URL, nao o PDF

`GET /expedicao/{ag}/expedicao/{exp}/etiquetas` responde

    {"urls": ["https://s3.amazonaws.com/tiny-tmp-us/.../<hash>.pdf"]}

e o PDF e' baixado dessa URL (link temporario). `OlistClient.baixar_etiqueta_olist`
pedia `application/pdf`, recebia esse JSON e devolvia None calado — foi o que
fez parecer que o Olist nao tinha a etiqueta da Amazon.

## O formato do PDF

A4 com 3 paginas por pedido:
  1. a etiqueta (metade de baixo da folha) — e' o que vai para a termica
  2. "Lista de postagem" da Amazon (ASIN, SKU, Id da etiqueta)
  3. termo de coleta (data/assinatura)

So' as paginas de etiqueta seguem; as de "Lista de postagem" saem. O recorte
para 10x15 fica com `core_etiqueta_normalizar`, como na Shopee.

## Rastreio

A etiqueta imprime o codigo da Amazon Logistics (ex: TBR437876278) no code128
grande. O Olist NAO preenche `codigoRastreio` da expedicao, entao o codigo e'
lido do texto da propria etiqueta — e' o que o Scanner bipa.

Uso:
    import core_etiquetas_amazon_olist as amz
    r = amz.baixar_etiquetas()
    # {"pdf": ..., "total": n, "arquivos": [...], "falhas": [(pedido, motivo)]}
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

PASTA_SAIDA = Path(os.path.expanduser("~")) / "Downloads"

# Quantos agrupamentos de expedicao recentes olhar. O Olist cria um por dia e
# por forma de envio; 60 cobre ~uma semana com os quatro canais ativos.
AGRUPAMENTOS_RECENTES = 60

# Codigo da Amazon Logistics no Brasil: "TBR" + digitos.
RE_RASTREIO_AMAZON = re.compile(r"\bTBR\d{6,}\b")

# Rastreio ja' extraido, por nota fiscal. Evita baixar a mesma etiqueta a
# cada ciclo do Scanner (o populador roda a cada 5 min).
_RASTREIO_POR_NOTA: dict[str, str] = {}


def _eh_amazon(nome: str) -> bool:
    return "amazon" in (nome or "").lower()


def listar_pedidos_a_enviar(*, dias: int = 30) -> list[dict[str, Any]]:
    """Pedidos da Amazon abertos no Olist, com nota fiscal emitida.

    Mesma fonte e mesmo filtro de pendencia do ML (`_pedidos_olist` +
    `_pedido_pendente`): o que aparece aqui e' o que esta' na lista de
    separacao.

    ``dias`` existe so' para manter a assinatura dos outros canais.

    ⚠️ (28/09/2026) `idNotaFiscal` NAO vem preenchido na listagem resumida do
    Olist (`_pedidos_olist`/`listar_pedidos_todos`) -- so' aparece no detalhe
    (`obter_pedido`). A 1a versao deste modulo lia direto do resumo e por
    isso NUNCA achava nota nenhuma: zero pedidos Amazon passavam, sempre,
    mesmo com nota emitida. Achado com o pedido real 1072/#1072, que ja
    tinha nota 907 havia 1 dia e seguia invisivel para a esteira. Corrigido
    buscando o detalhe quando o resumo vier vazio.

    Devolve [{"pedido", "id_olist", "id_nota", "cliente"}]. Pedido sem nota
    fica de fora: sem nota nao ha' expedicao, e sem expedicao nao ha' etiqueta.
    """
    del dias
    from core_scanner_populator import _client_olist, _pedido_pendente, _pedidos_olist

    client = None
    pedidos: list[dict[str, Any]] = []
    for p in _pedidos_olist():
        ecom = p.get("ecommerce") or {}
        num = ecom.get("numeroPedidoEcommerce") or ""
        if not _eh_amazon(ecom.get("nome") or "") or not num:
            continue
        if not _pedido_pendente(p):
            continue
        id_nota = p.get("idNotaFiscal")
        if not id_nota:
            # Resumo nao traz o campo -- confere no detalhe antes de
            # descartar (so' para os que ja passaram nos filtros acima,
            # que sao poucos, entao o custo de 1 GET a mais e' baixo).
            client = client or _client_olist()
            try:
                id_nota = client.obter_pedido(p.get("id")).get("idNotaFiscal")
            except Exception as exc:
                log.warning("Amazon %s: falha ao checar nota no detalhe: %s", num, exc)
                id_nota = None
        if not id_nota:
            log.info("Amazon %s sem nota fiscal ainda — sem etiqueta", num)
            continue
        pedidos.append({
            "pedido": num,
            "id_olist": p.get("id"),
            "id_nota": str(id_nota),
            "cliente": (p.get("cliente") or {}).get("nome") or "",
        })
    return pedidos


def mapa_expedicoes_amazon(client=None) -> dict[str, tuple[int, int]]:
    """{id_nota: (id_agrupamento, id_expedicao)} dos agrupamentos Amazon recentes.

    A listagem de agrupamentos traz so' o cabecalho (e `quantidadeObjetos`
    sempre 0, medido em 27/09) — as expedicoes de cada um exigem um GET
    proprio. Por isso filtra antes pela forma de envio: so' os da Amazon.
    """
    from core_olist import OlistClient

    client = client or OlistClient()
    mapa: dict[str, tuple[int, int]] = {}
    agrupamentos = client.listar_expedicoes(limit=AGRUPAMENTOS_RECENTES)
    for ag in agrupamentos:
        if not _eh_amazon((ag.get("formaEnvio") or {}).get("nome") or ""):
            continue
        try:
            det = client.obter_expedicao(ag["id"])
        except Exception as exc:
            log.warning("Agrupamento Amazon %s ilegivel: %s", ag.get("id"), exc)
            continue
        for exp in det.get("expedicoes") or []:
            if exp.get("tipoObjeto") != "notafiscal":
                continue
            id_nota = str(exp.get("idObjeto") or "")
            # Se a nota aparecer em mais de um agrupamento, fica o primeiro
            # da lista — a listagem vem do mais novo para o mais antigo.
            if id_nota and id_nota not in mapa:
                mapa[id_nota] = (ag["id"], exp["id"])
    return mapa


def baixar_pdf_expedicao(id_agrupamento: int, id_expedicao: int,
                         client=None) -> bytes | None:
    """PDF bruto (A4, 3 paginas) da etiqueta de UMA expedicao."""
    from core_olist import OlistClient

    client = client or OlistClient()
    resp = client.request(
        "GET", f"/expedicao/{id_agrupamento}/expedicao/{id_expedicao}/etiquetas")
    urls = (resp or {}).get("urls") or []
    if not urls:
        return None
    r = requests.get(urls[0], timeout=30)
    r.raise_for_status()
    return r.content if r.content[:4] == b"%PDF" else None


def so_etiqueta(pdf: bytes) -> tuple[bytes, str]:
    """Tira as paginas de "Lista de postagem" e le o rastreio da etiqueta.

    Retorna (pdf_so_com_etiqueta, rastreio). Rastreio vazio quando a
    etiqueta nao traz o codigo TBR — o chamador decide o que fazer.
    """
    import fitz

    origem = fitz.open(stream=pdf, filetype="pdf")
    novo = fitz.open()
    rastreio = ""
    for i, pagina in enumerate(origem):
        texto = pagina.get_text()
        if "Lista de postagem" in texto:
            continue
        novo.insert_pdf(origem, from_page=i, to_page=i)
        if not rastreio:
            achado = RE_RASTREIO_AMAZON.search(texto)
            rastreio = achado.group(0) if achado else ""
    if len(novo) == 0:
        # Formato mudou e nenhuma pagina passou no filtro: melhor imprimir o
        # PDF inteiro do que perder a etiqueta.
        log.warning("Nenhuma pagina de etiqueta reconhecida; mantendo o PDF inteiro")
        return pdf, rastreio
    return novo.tobytes(garbage=3, deflate=True), rastreio


def rastreios_pendentes(client=None) -> list[dict[str, Any]]:
    """[{"pedido", "rastreio", ...}] dos pedidos Amazon pendentes — para o Scanner.

    So' baixa a etiqueta das notas cujo rastreio ainda nao esta' em cache.
    """
    from core_olist import OlistClient

    client = client or OlistClient()
    pedidos = listar_pedidos_a_enviar()
    faltam = [p for p in pedidos if p["id_nota"] not in _RASTREIO_POR_NOTA]
    if faltam:
        mapa = mapa_expedicoes_amazon(client)
        for p in faltam:
            alvo = mapa.get(p["id_nota"])
            if not alvo:
                continue
            try:
                pdf = baixar_pdf_expedicao(*alvo, client=client)
                if pdf:
                    _, rastreio = so_etiqueta(pdf)
                    if rastreio:
                        _RASTREIO_POR_NOTA[p["id_nota"]] = rastreio
            except Exception as exc:
                log.warning("Amazon %s: rastreio nao lido: %s", p["pedido"], exc)
    saida = []
    for p in pedidos:
        rastreio = _RASTREIO_POR_NOTA.get(p["id_nota"])
        if rastreio:
            saida.append({**p, "rastreio": rastreio})
    return saida


def _unificar_pdfs(arquivos: list[Path], saida: str | Path) -> str:
    import fitz

    doc = fitz.open()
    for arq in arquivos:
        parcial = fitz.open(arq)
        doc.insert_pdf(parcial)
        parcial.close()
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    doc.save(saida)
    doc.close()
    return str(saida)


def baixar_etiquetas(
    pedidos: list[str] | None = None,
    *,
    dias: int = 30,
    saida: str | Path | None = None,
    unificar: bool = True,
) -> dict[str, Any]:
    """Baixa as etiquetas da Amazon e devolve um PDF unico.

    pedidos=None -> todos os pedidos Amazon pendentes no Olist.
    pedidos=[...] -> so' esses numeros de pedido Amazon (filtro da onda).

    Cada arquivo individual se chama `<numero do pedido Amazon>.pdf` — e' pelo
    nome que a pilha da esteira sabe de qual pedido e' a etiqueta.

    Retorna (mesmo formato dos outros canais):
        {"pdf": caminho, "total": n, "arquivos": [...], "falhas": [(pedido, motivo)]}
    """
    from core_olist import OlistClient

    client = OlistClient()
    lista = listar_pedidos_a_enviar(dias=dias)
    if pedidos is not None:
        alvo = {str(x) for x in pedidos}
        lista = [p for p in lista if p["pedido"] in alvo]

    if not lista:
        return {"pdf": None, "total": 0, "arquivos": [], "falhas": [],
                "aviso": "Nenhum pedido da Amazon aguardando despacho."}

    mapa = mapa_expedicoes_amazon(client)

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    tmp_dir = PASTA_SAIDA / f"_amazon_etiquetas_{datetime.now():%Y%m%d_%H%M%S}"
    tmp_dir.mkdir(exist_ok=True)

    arquivos: list[Path] = []
    falhas: list[tuple[str, str]] = []
    for p in lista:
        alvo_exp = mapa.get(p["id_nota"])
        if not alvo_exp:
            falhas.append((p["pedido"], "nota sem agrupamento de expedição "
                           "Amazon no Olist"))
            continue
        try:
            pdf = baixar_pdf_expedicao(*alvo_exp, client=client)
        except Exception as exc:
            falhas.append((p["pedido"], f"{type(exc).__name__}: {exc}"[:120]))
            continue
        if not pdf:
            falhas.append((p["pedido"], "etiqueta indisponível no Olist"))
            continue
        etiqueta, rastreio = so_etiqueta(pdf)
        if rastreio:
            _RASTREIO_POR_NOTA[p["id_nota"]] = rastreio
        destino = tmp_dir / f"{p['pedido']}.pdf"
        destino.write_bytes(etiqueta)
        arquivos.append(destino)

    resultado: dict[str, Any] = {
        "pdf": None,
        "total": len(arquivos),
        "arquivos": [str(a) for a in arquivos],
        "falhas": falhas,
        "pedidos": lista,
    }
    if unificar and arquivos:
        resultado["pdf"] = _unificar_pdfs(
            arquivos,
            saida or PASTA_SAIDA / f"etiquetas_amazon_{datetime.now():%Y%m%d_%H%M}.pdf",
        )
    return resultado


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for p in listar_pedidos_a_enviar():
        print(f"  pedido={p['pedido']:22} nota={p['id_nota']:>11}  {p['cliente'][:30]}")
