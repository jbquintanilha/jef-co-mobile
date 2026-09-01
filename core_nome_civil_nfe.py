# ==============================================================================
# NOME DO SCRIPT: core_nome_civil_nfe.py
# DESCRICAO: Nome civil do comprador a partir da NF-e (XML), por pedido
# FUNCAO: A API do TikTok parou de devolver `cpf_name`. A NF-e ja' carrega o
#         nome como consta no CPF -- e' a fonte primaria, nao um espelho.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 01/09/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Nome civil (e SKU) do pedido, lidos direto do XML da NF-e.

## Por que existir (Jota, 01/09/2026)

    "um outro meio de ter o nome da pessoa e' puxando a nota fiscal... e a
     nota fiscal e' interessante termos pq ela ja' serve para muitos outros
     fins alem de ter tb o sku do pedido para cruzar"

## O problema que motivou

`core_etiqueta_nome_real.mapa_por_pedido_tiktok()` lia `cpf_name` de
`/order/202309/orders`. Medido em 01/09/2026, a API responde `code: 0
Success` mas o pedido vem com apenas duas chaves:

    {"has_updated_recipient_address": false, "packages": []}

Sem `recipient_address`, sem `cpf_name`. Como o codigo tratava mapa vazio
como "nenhum apelido a corrigir", a etiqueta saia com o nick e ninguem era
avisado -- o Jota so' percebeu no papel ("nao esta acrescentando o nome real
da pessoa").

## Por que a NF-e e' fonte melhor

- E' o nome **como consta no CPF** -- o mesmo criterio que os Correios usam
  para entregar. Nao e' um espelho da API, e' o documento fiscal.
- Ja' e' emitida para todo pedido; nao depende de janela de retencao da API.
- Traz junto o **SKU** (`cProd`) e a quantidade, servindo para cruzamento.
- `infAdic/infCpl` carrega `OC: <numero do pedido no marketplace>`, que e' a
  chave para casar com a etiqueta.

Uso:
    from core_nome_civil_nfe import mapa_por_pedido
    m = mapa_por_pedido()                      # {pedido: (impresso, civil)}
    m = mapa_por_pedido(pasta="C:/xmls")       # pasta especifica
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}

# Onde os XMLs de NF-e de saida costumam cair (baixados do Olist).
PASTA_PADRAO = Path.home() / "Downloads" / "xmls_nfes_saida"

# `OC: 585798075611514419` no campo de informacoes complementares -- e' o
# numero do pedido no marketplace, a unica chave que liga NF-e e etiqueta.
#
# ⚠️ `\bOC` e' obrigatorio. Sem a borda, o regex casava com o "oc" no meio
# de outras palavras do texto padrao do Simples Nacional que muitas notas
# trazem, capturando digitos que nao eram pedido nenhum (achado 01/09/2026:
# pedidos Shopee saiam truncados, tipo "260801870").
#
# Sensivel a caixa de proposito: o marcador real e' sempre "OC:" maiusculo.
_RE_OC = re.compile(r"\bOC:\s*(\d{6,25})")


def _texto(no, caminho: str) -> str:
    if no is None:
        return ""
    return (no.findtext(caminho, "", NS) or "").strip()


def dados_da_nfe(xml: str | Path) -> dict[str, Any]:
    """Le um XML de NF-e e devolve o que interessa para a expedicao.

    Retorna {"pedido", "nome", "cpf", "itens": [(sku, qtd)], "arquivo"}.
    `pedido` vazio significa que a nota nao tem `OC:` -- sem ele nao ha' como
    ligar a nota a uma etiqueta, entao quem chama deve descartar.
    """
    caminho = Path(xml)
    try:
        raiz = ET.parse(caminho).getroot()
    except (ET.ParseError, OSError) as e:
        log.warning("XML ilegivel %s: %s", caminho.name, e)
        return {"pedido": "", "nome": "", "cpf": "", "itens": [],
                "arquivo": str(caminho)}

    dest = raiz.find(".//n:dest", NS)
    complemento = _texto(raiz.find(".//n:infAdic", NS), "n:infCpl")
    achado = _RE_OC.search(complemento)

    itens = []
    pedido_xped = ""
    for prod in raiz.findall(".//n:det/n:prod", NS):
        sku = _texto(prod, "n:cProd")
        qtd = _texto(prod, "n:qCom")
        # `xPed` (pedido de compra) tambem carrega o numero, as vezes
        # truncado pelo limite de 15 caracteres do campo. Serve de reserva
        # quando a nota nao traz o `OC:` completo.
        pedido_xped = pedido_xped or _texto(prod, "n:xPed")
        if sku:
            try:
                itens.append((sku, int(float(qtd or 0))))
            except ValueError:
                itens.append((sku, 0))

    return {
        "pedido": achado.group(1) if achado else pedido_xped,
        "nome": _texto(dest, "n:xNome"),
        "cpf": _texto(dest, "n:CPF") or _texto(dest, "n:CNPJ"),
        "itens": itens,
        "arquivo": str(caminho),
    }


def indexar(pasta: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """{numero_do_pedido: dados_da_nfe} de todos os XMLs da pasta.

    Notas sem `OC:` sao ignoradas -- nao ha' como liga-las a um pedido.
    """
    raiz = Path(pasta) if pasta else PASTA_PADRAO
    if not raiz.is_dir():
        log.warning("Pasta de XMLs nao encontrada: %s", raiz)
        return {}

    indice: dict[str, dict[str, Any]] = {}
    sem_oc = 0
    for arq in raiz.glob("*.xml"):
        d = dados_da_nfe(arq)
        if d["pedido"]:
            indice[d["pedido"]] = d
        else:
            sem_oc += 1

    log.info("NF-e indexadas: %d pedido(s) (%d nota(s) sem OC)",
             len(indice), sem_oc)
    return indice


def mapa_por_pedido(
    pedidos: list[str] | None = None,
    pasta: str | Path | None = None,
    nomes_impressos: dict[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    """{pedido: (nome_impresso, nome_civil)} — mesmo formato da versao TikTok.

    Serve como substituto direto de
    `core_etiqueta_nome_real.mapa_por_pedido_tiktok()`, para o chamador nao
    precisar mudar.

    Args:
        pedidos: restringe a estes numeros de pedido. `None` = todos.
        pasta: onde estao os XMLs.
        nomes_impressos: {pedido: nome_que_esta_na_etiqueta}. Quando
            informado, so' devolve os pares em que `e_apelido()` for
            verdadeiro -- ou seja, onde de fato ha' o que corrigir. Sem ele,
            devolve o nome civil de todos (o chamador filtra).
    """
    from core_etiqueta_nome_real import e_apelido

    indice = indexar(pasta)
    alvo = set(str(p) for p in pedidos) if pedidos else None

    saida: dict[str, tuple[str, str]] = {}
    for pedido, d in indice.items():
        if alvo is not None and pedido not in alvo:
            continue
        civil = d["nome"]
        if not civil:
            continue
        impresso = (nomes_impressos or {}).get(pedido, "")
        if impresso:
            # So' entra quando o nome da etiqueta realmente nao identifica a
            # pessoa -- nome abreviado os Correios entregam normalmente.
            if e_apelido(impresso, civil):
                saida[pedido] = (impresso, civil)
        else:
            saida[pedido] = (civil, civil)
    return saida


def nome_impresso_na_pagina(pagina) -> str:
    """O nome do destinatario como esta' escrito na etiqueta.

    A API do TikTok nao devolve mais `recipient_address`, entao o unico lugar
    onde o nome IMPRESSO existe e' a propria etiqueta -- e ele e' necessario
    para (a) saber se e' apelido e (b) achar onde escrever o nome civil.

    Localiza pela POSICAO, nao pela ordem do texto: medido em etiqueta J&T
    real (01/09/2026), `get_text()` devolve os blocos fora de ordem de
    leitura -- o numero do pedido aparece entre "DESTINATÁRIO" e o nome, e o
    nome do comprador chega a sair depois de "REMETENTE:". Ler sequencial
    pega o campo errado.

    A regra que se sustenta: o nome e' a primeira linha de texto ABAIXO do
    rotulo "DESTINATÁRIO", alinhada a` margem esquerda do bloco.
    """
    try:
        spans = []
        for bloco in pagina.get_text("dict")["blocks"]:
            for linha in bloco.get("lines", []):
                for trecho in linha.get("spans", []):
                    texto = (trecho.get("text") or "").strip()
                    if texto:
                        spans.append((trecho["bbox"], texto))
    except Exception:
        return ""

    rotulo = None
    for bbox, texto in spans:
        if "DESTINAT" in texto.upper():
            rotulo = bbox
            break
    if rotulo is None:
        return ""

    # Primeiro texto abaixo do rotulo, comecando perto da mesma margem
    # esquerda. A tolerancia de 12pt cobre o recuo do bloco de endereco.
    candidatos = [
        (bbox[1], texto) for bbox, texto in spans
        if bbox[1] > rotulo[3] - 2 and abs(bbox[0] - rotulo[0]) < 12
    ]
    if not candidatos:
        return ""
    candidatos.sort()
    return candidatos[0][1]


def sku_do_pedido(pedido: str, pasta: str | Path | None = None) -> list[tuple[str, int]]:
    """[(sku, quantidade)] do pedido, direto da NF-e — para cruzamento."""
    d = indexar(pasta).get(str(pedido))
    return d["itens"] if d else []


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pasta = sys.argv[1] if len(sys.argv) > 1 else None
    idx = indexar(pasta)
    print(f"{len(idx)} NF-e com pedido identificado\n")
    for pedido, d in list(idx.items())[:10]:
        itens = ", ".join(f"{s} x{q}" for s, q in d["itens"])
        print(f"  {pedido}  {d['nome']:32} {d['cpf']:15} {itens}")
