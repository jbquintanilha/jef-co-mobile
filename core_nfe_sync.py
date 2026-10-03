# ==============================================================================
# NOME DO SCRIPT: core_nfe_sync.py
# DESCRICAO: Baixa os XMLs das NF-e emitidas pela API do Olist
# FUNCAO: Mantem `~/Downloads/xmls_nfes_saida` atualizada, que e' de onde
#         `core_nome_civil_nfe` tira o nome como consta no CPF para corrigir
#         apelido em etiqueta (TikTok manda nick, Correios precisa do nome).
# STATUS: ATIVO
# VERSAO: 1.0 | DATA: 23/09/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Sincroniza os XMLs de NF-e de saida.

**Por que existe:** a pasta de XMLs era populada **na mao**. Parou em
31/08/2026 e ninguem percebeu -- por 23 dias as etiquetas sairam com o
apelido do TikTok (`deliciasdapri77`) em vez do nome civil, porque
`core_nome_civil_nfe.mapa_por_pedido()` simplesmente nao achava o pedido
e devolvia vazio, sem erro nenhum.

Falha silenciosa e' o pior tipo: o PDF sai bonito, so' com o nome errado.
Por isso este modulo existe e por isso `sincronizar()` devolve um resumo
em vez de so' rodar -- quem chama consegue avisar na tela.
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

log = logging.getLogger("core_nfe_sync")

PASTA_XMLS = Path.home() / "Downloads" / "xmls_nfes_saida"

# So' nota AUTORIZADA tem valor fiscal. As demais situacoes (pendente,
# cancelada, denegada) nao servem para tirar o nome civil.
SITUACAO_AUTORIZADA = "6"


def _ja_baixados() -> set[str]:
    """Chaves de acesso que ja' estao em disco (o nome do arquivo comeca por ela)."""
    if not PASTA_XMLS.is_dir():
        return set()
    return {arq.name.split("-")[0] for arq in PASTA_XMLS.glob("*.xml")}


def sincronizar(dias: int = 30, limite_paginas: int = 20) -> dict:
    """Baixa os XMLs que faltam e devolve um resumo do que aconteceu.

    Args:
        dias: quantos dias para tras buscar (por data de emissao).
        limite_paginas: teto de paginacao, para nao varrer a base inteira
            se a data vier vazia por algum motivo.

    Returns:
        {"baixados", "ja_tinha", "sem_xml", "erros", "total_analisado",
         "mais_recente"}
    """
    from core_olist import OlistClient

    PASTA_XMLS.mkdir(parents=True, exist_ok=True)
    existentes = _ja_baixados()
    corte = _dt.date.today() - _dt.timedelta(days=dias)

    cli = OlistClient()
    baixados = ja_tinha = sem_xml = erros = total = 0
    mais_recente: str | None = None

    for pagina in range(limite_paginas):
        try:
            notas = cli.listar_notas(limit=100, offset=pagina * 100)
        except Exception as exc:
            log.warning("Falha ao listar notas (pagina %d): %s", pagina, exc)
            erros += 1
            break

        if not notas:
            break

        # A listagem vem da mais nova para a mais antiga; quando toda a
        # pagina ja' esta' fora da janela, nao ha' o que buscar adiante.
        fora_da_janela = True

        for nota in notas:
            total += 1
            emissao = (nota.get("dataEmissao") or "").strip()
            try:
                data = _dt.date.fromisoformat(emissao)
            except ValueError:
                continue                      # "0000-00-00" e afins

            if data < corte:
                continue
            fora_da_janela = False

            if str(nota.get("situacao")) != SITUACAO_AUTORIZADA:
                continue

            chave = (nota.get("chaveAcesso") or "").strip()
            if not chave:
                continue
            if chave in existentes:
                ja_tinha += 1
                continue

            id_nota = nota.get("id")
            if not id_nota:
                continue

            try:
                xml = (cli.xml_nota(id_nota) or {}).get("xmlNfe")
            except Exception as exc:
                log.warning("XML da nota %s indisponivel: %s", id_nota, exc)
                erros += 1
                continue

            if not xml:
                sem_xml += 1
                continue

            (PASTA_XMLS / f"{chave}-nfe.xml").write_text(xml, encoding="utf-8")
            existentes.add(chave)
            baixados += 1
            if mais_recente is None or emissao > mais_recente:
                mais_recente = emissao

        if fora_da_janela:
            break

    log.info("NF-e sync: %d baixada(s), %d ja' tinha, %d sem XML, %d erro(s)",
             baixados, ja_tinha, sem_xml, erros)

    return {
        "baixados": baixados,
        "ja_tinha": ja_tinha,
        "sem_xml": sem_xml,
        "erros": erros,
        "total_analisado": total,
        "mais_recente": mais_recente,
    }


def idade_do_acervo() -> tuple[int | None, str | None]:
    """(dias_desde_o_xml_mais_novo, data_iso) — para alertar acervo velho.

    Usa a data de modificacao do arquivo, que e' quando ele entrou na pasta.
    Devolve (None, None) quando a pasta esta' vazia ou nao existe.
    """
    if not PASTA_XMLS.is_dir():
        return None, None
    arquivos = list(PASTA_XMLS.glob("*.xml"))
    if not arquivos:
        return None, None

    mais_novo = max(arquivos, key=lambda a: a.stat().st_mtime)
    data = _dt.date.fromtimestamp(mais_novo.stat().st_mtime)
    return (_dt.date.today() - data).days, data.isoformat()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    dias_atras, quando = idade_do_acervo()
    if dias_atras is not None:
        print(f"Acervo atual: XML mais novo e' de {quando} ({dias_atras} dia(s) atras)")
    print("Sincronizando...")
    print(sincronizar())
