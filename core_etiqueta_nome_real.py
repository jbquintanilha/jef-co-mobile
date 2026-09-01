# ==============================================================================
# NOME DO SCRIPT: core_etiqueta_nome_real.py
# DESCRICAO: Acrescenta o nome civil ao lado (ou abaixo) do apelido na etiqueta
# FUNCAO: Quando o TikTok emite a etiqueta com o nick do comprador, escreve
#         o nome real entre parenteses — sem apagar nada.
# STATUS: ATIVO
# VERSAO: 1.1
# DATA: 19/08/2026 (v1.0) — 30/08/2026 (v1.1: 2a linha p/ etiqueta J&T/TikTok)
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Regra do Jota (2026-08-19):

    "mantenha como esta' o nome da pessoa ali e coloque entre parenteses logo
     apos o nome completo da pessoa... essa continuidade da linha tem bastante
     espaco e caberia... isso ate' o limite da margem, sem estourar a margem"

## O problema

O TikTok Shop imprime na etiqueta o NOME DE USUARIO, nao o nome civil:

    etiqueta:  Thata
    cadastro:  Aurora Machado          CPF 425.464.498-18

Os Correios devolvem ao remetente quando o nome do rotulo nao corresponde a
ninguem no endereco. E a NF-e ja' sai com o nome civil — hoje etiqueta e nota
divergem, o que e' justamente o que trava a entrega.

## Por que ACRESCENTAR e nao substituir

Nada do que a plataforma emitiu e' apagado. O rotulo passa a carregar as duas
identidades:

    Thata (Aurora Machado)

O carteiro identifica a pessoa; quem so' conhece o comprador pelo nick ainda
reconhece. E, como o nome civil vem do MESMO cadastro que tem o CPF usado na
NF-e, a informacao acrescentada e' mais correta, nao menos.

## Correcao 30/08/2026 — etiqueta J&T (TikTok) e' mais estreita que Correios

`_cabe()` calculava contra a largura TOTAL da pagina menos uma margem fixa —
mas na etiqueta J&T ha' um codigo de barras VERTICAL numa coluna lateral
fixa (comeca em x≈241pt nessa etiqueta 100x150mm) que o calculo nao
descontava. Resultado real (Jota, 30/08, pedido 999881879488251): "Siomara
Felipetti (Siomara Aparecida Aurieme Felipeti)" foi considerado "cabe" mas
visualmente atropelou o codigo de barras.

Decisao do Jota: em vez de so' abreviar mais agressivo pra caber na mesma
linha, o nome civil desce para uma 2a linha DENTRO da mesma caixa branca de
destinatario, abaixo do endereco — ha' espaco vertical livre ali (a etiqueta
Correios tem endereco mais curto que J&T, entao J&T tinha mais folga
horizontal na v1.0; a v1.1 usa vertical em vez de tentar espremer na mesma
linha). Ver `completar_nomes_segunda_linha()`.

## Quem e' nick e quem nao e'

Medido em 23 pedidos reais (19/08/2026): so' 3 eram nick de verdade.

    Thata               -> Aurora Machado                 NICK
    Silvanasalvador373  -> Silvana de Oliveira Salvador   NICK
    Locutorharoldofar   -> Haroldo Elias Silva de Farias  NICK
    Mateus Capellari    -> Mateus Afonso de Melo Capellari  abreviacao, OK
    Carol Dias          -> Ana Carolina Souza Dias          apelido comum, OK

⚠️ Nome abreviado NAO e' problema: os Correios entregam normalmente. Mexer em
todos poluiria 20 etiquetas para resolver 3. O criterio esta' em `e_apelido()`.

Uso:
    from core_etiqueta_nome_real import completar_nomes
    completar_nomes("etiquetas.pdf", {"Thata": "Aurora Machado"})
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MM = 72 / 25.4
MARGEM_DIREITA = 4 * MM     # nunca escrever alem disto

# Partes que nao identificam a pessoa e atrapalham a comparacao
_LIGACOES = {"de", "da", "do", "das", "dos", "e"}


def _normalizar(texto: str) -> str:
    """Minusculas, sem acento, so' letras e espaco."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", texto.lower()).strip()


def _tokens(nome: str) -> set[str]:
    return {t for t in _normalizar(nome).split() if t and t not in _LIGACOES}


def e_apelido(na_etiqueta: str, nome_civil: str) -> bool:
    """True quando o nome da etiqueta NAO identifica a pessoa do cadastro.

    A regra: se todo pedaco do nome impresso aparece no nome civil, e' so'
    uma abreviacao ("Mateus Capellari" dentro de "Mateus Afonso de Melo
    Capellari") — os Correios entregam e nao ha' o que corrigir.

    Basta um pedaco NAO existir no cadastro para ser apelido:
    "Thata" nao esta' em "Aurora Machado"; "Silvanasalvador373" tampouco
    (grudado + numero nunca casa token a token).
    """
    if not na_etiqueta or not nome_civil:
        return False

    impressos = _tokens(na_etiqueta)
    civis = _tokens(nome_civil)
    if not impressos or not civis:
        return False

    # ⚠️ LEI do Jota (01/09/2026): "necessario sempre ter no minimo 2
    # nomes... 1 nome so' considere apelido".
    #
    # Um nome sozinho nao identifica ninguem para o carteiro, mesmo quando
    # confere com o cadastro: "Aninha" casa dentro de "Ana Paula Souza" pela
    # regra de subconjunto abaixo e passava batido, deixando a etiqueta so'
    # com o primeiro nome. Exigir dois nomes vale tanto para nick declarado
    # quanto para nome civil abreviado a uma palavra.
    if len(impressos) < 2:
        return len(civis) >= 2

    # Toda palavra impressa precisa existir no cadastro. Comparacao por
    # token, nao por substring: "Ana" nao pode casar dentro de "Joana".
    return not impressos.issubset(civis)


def _cabe(texto: str, x_inicio: float, largura_pagina: float,
          fonte: str, tamanho: float) -> bool:
    import fitz

    largura = fitz.get_text_length(texto, fontname=fonte, fontsize=tamanho)
    return (x_inicio + largura) <= (largura_pagina - MARGEM_DIREITA)


def encurtar_para_caber(nome: str, x_inicio: float, largura_pagina: float,
                        fonte: str, tamanho: float) -> str | None:
    """Abrevia os nomes DO MEIO ate' o texto caber na linha.

    Regra do Jota (2026-08-19):

        "nomes grandes q estouram o limite de caracteres ate' a margem... vc
         abrevia o segundo nome... e terceiro se necessario... para apenas a
         letra inicial... mantendo sempre dentro dos parenteses"

    Primeiro e ultimo nome NUNCA sao abreviados — sao os que identificam a
    pessoa para o carteiro. O corte avanca do segundo nome em diante:

        Glaudenubia Santos do Nascimento Paulino
        Glaudenubia S. do Nascimento Paulino
        Glaudenubia S. do N. Paulino

    Devolve o texto pronto (com parenteses) ou `None` se nem a forma mais
    curta couber — melhor nao escrever do que vazar para fora do papel.
    """
    partes = nome.split()
    if not partes:
        return None

    texto = f" ({nome})"
    if _cabe(texto, x_inicio, largura_pagina, fonte, tamanho):
        return texto

    # Abrevia do segundo nome para a frente, um por vez, preservando o ultimo.
    # ⚠️ O range vai ate' `len(partes) - 1` INCLUSIVE (dai o +1): com
    # `range(1, len-1)` o ultimo nome do meio nunca era abreviado e a funcao
    # pulava direto para "primeiro + ultimo", perdendo as formas intermediarias
    # ("Glaudenubia S. do N. Paulino" nem chegava a ser testada).
    for corte in range(1, max(2, len(partes) - 1) + 1):
        tentativa = list(partes)
        # `min` protege o ULTIMO nome: ele nunca vira inicial, por mais que o
        # espaco aperte. "Glaudenubia S. do N. P." nao identifica ninguem.
        for i in range(1, min(corte + 1, len(partes) - 1)):
            palavra = tentativa[i]
            # Ligacoes ("de", "da", "dos") nao viram inicial: encurtam pouco
            # e picotam a leitura do nome.
            if palavra.lower() in _LIGACOES:
                continue
            tentativa[i] = f"{palavra[0].upper()}."

        texto = f" ({' '.join(tentativa)})"
        if _cabe(texto, x_inicio, largura_pagina, fonte, tamanho):
            return texto

    # Ultimo recurso: so' primeiro e ultimo nome
    if len(partes) > 1:
        texto = f" ({partes[0]} {partes[-1]})"
        if _cabe(texto, x_inicio, largura_pagina, fonte, tamanho):
            return texto

    return None


def _limite_direito_real(pagina, y0: float, y1: float,
                          x_ignorar_ate: float) -> float:
    """Onde a linha (y0..y1) PODE de fato escrever ate', sem invadir outro
    conteudo da pagina (ex: coluna do codigo de barras).

    `_cabe()` original so' olhava a largura da pagina inteira — em etiquetas
    J&T/TikTok isso ignora que ha' um codigo de barras VERTICAL ocupando uma
    faixa lateral fixa, deixando bem menos espaco real do que o calculo
    assumia (achado real 30/08/2026, ver docstring do modulo).

    Varre todo texto da pagina que comece depois de `x_ignorar_ate` (ou
    seja, nao e' o proprio nome que estamos medindo) e que se sobreponha
    verticalmente a` faixa (y0, y1); o limite e' o x0 do achado mais a`
    esquerda entre eles. Sem nenhum achado, cai no rodape da pagina inteira.
    """
    limite = pagina.rect.width
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for trecho in linha.get("spans", []):
                bx0, by0, bx1, by1 = trecho["bbox"]
                if bx0 <= x_ignorar_ate:
                    continue  # o proprio texto do nome, ou algo a esquerda
                sobrepoe_y = by0 < y1 and by1 > y0
                if sobrepoe_y and bx0 < limite:
                    limite = bx0
    return limite


def _linha_livre_abaixo(pagina, caixa_nome,
                        folga_min: float = 8.0) -> tuple[float, float] | None:
    """Acha uma 2a linha livre logo abaixo do nome/endereco, dentro da
    mesma caixa de destinatario, sem colidir com o proximo bloco de
    conteudo (ex: o numero grande do pedido, tipo "319111").

    Junta todas as linhas de texto que comecam perto da margem esquerda
    da caixa do nome (mesmo bloco de destinatario) para achar onde o
    endereco termina verticalmente; devolve o `y` de base pra escrever
    logo abaixo, e o `limite` horizontal disponivel ali. `None` se nao
    houver folga vertical suficiente (`folga_min` em pontos) antes do
    proximo elemento da pagina.
    """
    x_esq = caixa_nome.x0
    y_topo_bloco = caixa_nome.y0

    # Ultima linha do MESMO bloco de destinatario (endereco costuma vir
    # logo abaixo do nome, alinhado a` mesma margem esquerda).
    fim_bloco = caixa_nome.y1
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for trecho in linha.get("spans", []):
                bx0, by0, bx1, by1 = trecho["bbox"]
                mesma_coluna = abs(bx0 - x_esq) < 3
                logo_abaixo = by0 >= y_topo_bloco
                if mesma_coluna and logo_abaixo:
                    fim_bloco = max(fim_bloco, by1)

    # Proximo elemento QUALQUER (mesma pagina) abaixo do fim do bloco —
    # define ate' onde ha' espaco vertical livre antes de colidir.
    proximo_y = pagina.rect.height
    limite_h = pagina.rect.width
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for trecho in linha.get("spans", []):
                bx0, by0, bx1, by1 = trecho["bbox"]
                if by0 > fim_bloco + 0.5:
                    if by0 < proximo_y:
                        proximo_y = by0
                        limite_h = pagina.rect.width
                    if by0 < proximo_y + 2 and bx0 < limite_h:
                        limite_h = min(limite_h, pagina.rect.width)

    linha_altura = 9.0  # aproximacao pro tamanho de fonte tipico (8-8.3pt)
    if (proximo_y - fim_bloco) < (linha_altura + folga_min):
        return None

    y_escrita = fim_bloco + linha_altura
    return (y_escrita, limite_h - 4.0)  # 4pt de folga na direita


def completar_nomes(pdf: str | Path,
                    mapa: dict[str, str],
                    saida: str | Path | None = None) -> dict[str, Any]:
    """Escreve "(Nome Civil)" apos o apelido, em cada pagina que casar.

    Tenta primeiro a mesma linha (ao lado do nick). Se o espaco real ate' o
    proximo conteudo da pagina (ex: coluna do codigo de barras em etiquetas
    J&T) nao comportar nem a forma mais abreviada, cai para uma 2a linha
    logo abaixo do endereco, dentro da mesma caixa de destinatario — ver
    `_linha_livre_abaixo()`.

    Args:
        pdf: etiquetas ja' normalizadas.
        mapa: {nome_impresso: nome_civil}. So' os pares em que
            `e_apelido()` for verdadeiro sao usados.
        saida: destino; `None` sobrescreve.

    Retorna:
        {"paginas", "corrigidas", "corrigidas_2a_linha", "nao_coube", "detalhe"}
    """
    import fitz

    pdf = Path(pdf)
    doc = fitz.open(pdf)

    # So' os que realmente sao apelido
    alvos = {imp: civ for imp, civ in mapa.items() if e_apelido(imp, civ)}
    if not alvos:
        doc.close()
        return {"paginas": 0, "corrigidas": 0, "corrigidas_2a_linha": 0,
                "nao_coube": 0, "detalhe": []}

    corrigidas = 0
    corrigidas_2a_linha = 0
    nao_coube = 0
    detalhe: list[str] = []

    for pagina in doc:
        for impresso, civil in alvos.items():
            # `search_for` acha a caixa exata do texto na pagina
            achados = pagina.search_for(impresso)
            if not achados:
                continue

            # A 1a ocorrencia e' a linha do destinatario; ignorar repeticoes
            caixa = achados[0]

            # Descobre a fonte real daquele trecho para o acrescimo casar
            fonte, tamanho = "helv", 8.3
            for bloco in pagina.get_text("dict")["blocks"]:
                for linha in bloco.get("lines", []):
                    for trecho in linha.get("spans", []):
                        if impresso in trecho["text"]:
                            tamanho = trecho["size"]
                            # Helvetica cobre a metrica da Arial da etiqueta;
                            # embutir a fonte original inflaria o PDF a toa.
                            fonte = ("hebo" if "Bold" in trecho["font"]
                                     else "helv")
                            break

            x = caixa.x1          # logo apos o nome impresso
            y = caixa.y1          # mesma linha de base

            # Limite real ate' onde da' pra escrever nessa linha (desconta
            # coluna de codigo de barras ou qualquer outro conteudo).
            limite = _limite_direito_real(pagina, caixa.y0, caixa.y1, caixa.x1)

            # Abrevia os nomes do MEIO ate' caber, preservando primeiro e
            # ultimo. `None` = nem a forma mais curta cabe no espaco real.
            texto = encurtar_para_caber(civil, x, limite, fonte, tamanho)

            if texto is not None:
                pagina.insert_text(fitz.Point(x, y), texto, fontsize=tamanho,
                                   fontname=fonte, color=(0, 0, 0),
                                   overlay=True)
                corrigidas += 1
                detalhe.append(f"{impresso} -> {impresso}{texto}")
                continue

            # Nao coube na mesma linha (etiqueta estreita, ex: J&T/TikTok) —
            # tenta uma 2a linha logo abaixo do endereco, dentro da mesma
            # caixa de destinatario.
            pos = _linha_livre_abaixo(pagina, caixa)
            if pos is not None:
                y2, limite2 = pos
                texto2 = encurtar_para_caber(
                    civil, caixa.x0, limite2, fonte, tamanho)
                if texto2 is not None:
                    texto2 = texto2.strip()  # sem o espaco inicial de "(...)"
                    pagina.insert_text(
                        fitz.Point(caixa.x0, y2), texto2, fontsize=tamanho,
                        fontname=fonte, color=(0, 0, 0), overlay=True)
                    corrigidas_2a_linha += 1
                    detalhe.append(
                        f"{impresso} -> 2a linha: {texto2}")
                    continue

            nao_coube += 1
            detalhe.append(f"{impresso}: nao coube nem na 2a linha")

    destino = Path(saida) if saida else pdf
    if destino == pdf:
        tmp = pdf.with_suffix(".__nome__.pdf")
        doc.save(tmp)
        doc.close()
        tmp.replace(pdf)
    else:
        doc.save(destino)
        doc.close()

    log.info("Nomes completados: %d (nao coube: %d)", corrigidas, nao_coube)
    return {"paginas": len(alvos), "corrigidas": corrigidas,
            "nao_coube": nao_coube, "detalhe": detalhe}


def mapa_do_tiktok(order_ids: list[str]) -> dict[str, str]:
    """{nome_na_etiqueta: nome_civil} direto da API do TikTok Shop.

    ⚠️ NAO usar para aplicar na pagina certa quando ha' mais de 1 pedido no
    lote — nomes impressos curtos ("jo", "Dani") colidem por `search_for`
    (substring) com texto de OUTRAS paginas, aplicando o nome civil ERRADO
    (achado real 25/08: "jo" -> "Jocinete Neri De Lima" vazou pra 4 outras
    etiquetas de destinatarios diferentes so' por "jo" aparecer em algum
    canto do texto delas). Use `mapa_por_pedido_tiktok()` para aplicacao —
    esta funcao serve so' para inspecao/relatorio.

    Descoberto em 19/08: `/order/202309/orders` ja' devolve `cpf_name` — o
    nome exatamente como consta na declaracao do CPF do comprador. Mais
    confiavel que buscar no Olist: e' a fonte primaria, sem risco de erro de
    digitacao de quem cadastrou o pedido no ERP, e uma unica chamada em lote
    substitui uma chamada por pedido (era o gargalo dos ~200s do caminho via
    Olist — ver `mapa_do_olist`, mantida como fallback).
    """
    mapa: dict[str, str] = {}
    for _oid, (impresso, civil) in mapa_por_pedido_tiktok(order_ids).items():
        mapa[impresso] = civil
    return mapa


def mapa_por_pedido_tiktok(order_ids: list[str]) -> dict[str, tuple[str, str]]:
    """{order_id: (nome_na_etiqueta, nome_civil)} — 1 entrada por PEDIDO.

    Correcao de 25/08: `mapa_do_tiktok()` devolvia {impresso: civil} sem
    ligacao com o pedido de origem. Quando aplicado pagina a pagina contra o
    mapa inteiro (como `core_etiquetas_na_esteira.py` fazia), um nick curto
    de UM pedido ("jo") casava via `search_for` (substring) dentro do texto
    de paginas de OUTROS pedidos, escrevendo o nome civil errado nelas.
    Mantendo o vinculo por `order_id`, cada pagina so' pode ser testada
    contra o PRORIO par dela — nunca contra o mapa inteiro.
    """
    import core_etiquetas_tiktok_api as tt

    mapa: dict[str, tuple[str, str]] = {}
    if not order_ids:
        return mapa

    for i in range(0, len(order_ids), 50):  # a API aceita ate' 50 ids/chamada
        lote = order_ids[i:i + 50]
        try:
            resp = tt._get("/order/202309/orders", {"ids": ",".join(lote)})
        except Exception as exc:
            log.warning("Lote de pedidos TikTok falhou: %s", exc)
            continue

        for pedido in ((resp.get("data") or {}).get("orders") or []):
            oid = str(pedido.get("id") or "")
            impresso = ((pedido.get("recipient_address") or {})
                       .get("name") or "").strip()
            # cpf_name vem em CAIXA ALTA; Title() casa com o resto da etiqueta
            civil = (pedido.get("cpf_name") or "").strip().title()
            if oid and impresso and civil and e_apelido(impresso, civil):
                mapa[oid] = (impresso, civil)

    return mapa


def mapa_do_olist(situacoes: list[int] | None = None) -> dict[str, str]:
    """{nome_na_etiqueta: nome_civil} a partir dos pedidos do Olist.

    ⚠️ Fallback. Prefira `mapa_do_tiktok()` — mesma informacao, uma chamada
    em lote em vez de uma por pedido (~200s -> poucos segundos).

    O Olist guarda os dois: `enderecoEntrega.nomeDestinatario` e' o que a
    plataforma mandou (o nick), e `cliente.nome` e' o nome civil do cadastro
    — o mesmo que tem o CPF usado na NF-e.
    """
    from concurrent.futures import ThreadPoolExecutor

    from core_olist import OlistClient

    cliente = OlistClient()
    mapa: dict[str, str] = {}

    ids: list[Any] = []
    for situacao in (situacoes or [4]):
        try:
            ids += [r["id"] for r in
                    (cliente.listar_pedidos(situacao=situacao, limit=100) or [])]
        except Exception as exc:
            log.warning("Situacao %s indisponivel: %s", situacao, exc)

    def _detalhe(pedido_id):
        try:
            return cliente.obter_pedido(pedido_id)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        for pedido in pool.map(_detalhe, ids):
            if not pedido:
                continue
            impresso = ((pedido.get("enderecoEntrega") or {})
                        .get("nomeDestinatario") or "").strip()
            civil = ((pedido.get("cliente") or {}).get("nome") or "").strip()
            if impresso and civil and e_apelido(impresso, civil):
                mapa[impresso] = civil

    return mapa


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("uso: python core_etiqueta_nome_real.py <etiquetas.pdf>")
        raise SystemExit(1)

    mapa = mapa_do_olist([4, 5])
    print(f"apelidos detectados: {len(mapa)}")
    for imp, civ in mapa.items():
        print(f"  {imp:24} -> {civ}")

    if mapa:
        r = completar_nomes(sys.argv[1], mapa)
        print(f"\n  {r['corrigidas']} etiqueta(s) completada(s)")
        for d in r["detalhe"]:
            print(f"    {d}")
