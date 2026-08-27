# ==============================================================================
# NOME DO SCRIPT: core_sequencia_embalagem.py
# DESCRICAO: Ordena os pedidos na sequencia mais inteligente de embalagem
# FUNCAO: Coleta traz tudo junto por atomo. Se as caixas forem montadas na
#         mesma ordem, a pilha da bancada e a pilha de etiquetas casam — sem
#         garimpar peca a cada pedido.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
A ordem (definida pelo Jota, 2026-08-16; item 5 invertido em 2026-08-26):

    "tudo da meia media branca, depois tudo da meia media preta, depois tudo da
     meia media sortida... depois invisivel masculina de cor especifica, depois
     as sortidas... o mesmo na colorida. Excecao para pedidos multiplos — esses
     agrupa e faz fora de ordem mesmo."

    "é sempre melhor começar pelo que é mais específico... os kits 3 sempre
     primeiro e depois você ir aumentando a quantidade deles" (26/08/2026)

Traduzido em criterios de ordenacao, nesta ordem de peso:

    1. MULTI-ITEM por ultimo   — pedido com mais de um atomo nao encaixa em
                                 nenhum grupo; vai para o fim, agrupado
    2. FAMILIA                 — MEIAS antes de CALCINHAS etc (ordem do estoque)
    3. LINHA do produto        — MEMED antes de MEINV (SPU + tamanho)
    4. COR, sortida por ultimo — BRA, PRE, CIN... e SOR fecha cada linha
    5. QUANTIDADE crescente    — kit MENOR primeiro (Kit3, Kit6, Kit9...).
                                 ⚠️ INVERTIDO em 26/08 (era maior primeiro,
                                 desde 16/08) — pedido do Jota, o kit mais
                                 especifico/simples de conferir vem primeiro,
                                 o volume vai crescendo ao longo do grupo.

Por que a sortida fecha o grupo: ela e' a que exige escolher pecas do monte
misturado. Deixar por ultimo evita alternar entre "pegar do pacote fechado" e
"garimpar no misto" a cada caixa.

Uso:
    from core_sequencia_embalagem import sequenciar
    r = sequenciar(dados_batch_picking)
    for i, p in enumerate(r["sequencia"], 1):
        print(i, p["atomo_chave"], p["numero_ecommerce"])
"""

from __future__ import annotations
import core_env_loader

import logging
from typing import Any

import core_separacao_atomos as csa

log = logging.getLogger(__name__)

# Ordem das familias na bancada — espelha a ordem fisica do estoque.
# Familia fora desta lista vai para o fim, mas nunca some.
ORDEM_FAMILIA = ["MEIAS", "CALCINHAS", "TOPS & SUTIAS", "CONJUNTOS", "OUTROS"]

# Cor sortida fecha cada linha: e' a unica que exige garimpar no monte misto.
COR_SORTIDA = "SOR"


def _peso_familia(familia: str) -> int:
    familia = (familia or "").strip().upper()
    return ORDEM_FAMILIA.index(familia) if familia in ORDEM_FAMILIA else len(ORDEM_FAMILIA)


def _classificar(pedido: dict[str, Any]) -> dict[str, Any]:
    """Descobre em que grupo de embalagem o pedido entra."""
    itens = pedido.get("itens") or []

    atomos: list[dict[str, Any]] = []
    # ⚠️ Multiplicar pela QUANTIDADE do item. Sem isto, um pedido de 4x
    # "Kit 3 Meia" contava 3 pecas em vez de 12 — a caixa sairia com 1 kit
    # (incidente do pedido 428, 18/08/2026).
    unidades = 0
    for item in itens:
        qtd = int(item.get("quantidade") or 1)
        unidades += qtd
        for parte in csa.decompor_sku(item.get("sku", "")):
            atomos.append({**parte, "qtd": parte["qtd"] * qtd})

    distintos = {a["atomo"] for a in atomos}
    total_pecas = sum(a["qtd"] for a in atomos)

    # Multi-item = mais de um atomo distinto. Nao pertence a grupo nenhum.
    # ⚠️ Quantidade > 1 do MESMO item continua no grupo do atomo (a coleta e'
    # a mesma prateleira), mas a bancada precisa ver o numero de unidades —
    # por isso `unidades` sobe no registro.
    multi = len(distintos) > 1

    if multi:
        # ⚠️ Multi-item vai para o fim, mas os IGUAIS ficam juntos: quem monta
        # 3 caixas de branca+preta faz as tres seguidas. `atomo_chave` e' a
        # combinacao ordenada, entao pedidos com a mesma mistura se agrupam.
        combinacao = " + ".join(sorted(distintos))
        return {
            "multi": True,
            "atomo_chave": combinacao,
            "familia": "MULTI-ITEM",
            "linha": combinacao,        # ordena as combinacoes entre si
            "cor": "",
            "sortido": False,
            "total_pecas": total_pecas,
            "unidades": unidades,
            "atomos": atomos,
        }

    atomo = next(iter(distintos), "")
    # {LINHA}{COR}: a cor sao as 3 ultimas letras do atomo V5
    linha, cor = (atomo[:-3], atomo[-3:]) if len(atomo) > 3 else (atomo, "")

    return {
        "multi": False,
        "atomo_chave": atomo,
        "familia": "",          # preenchida por `sequenciar` via core_separacao
        "linha": linha,
        "cor": cor,
        "sortido": cor.upper() == COR_SORTIDA,
        "total_pecas": total_pecas,
        "unidades": unidades,
        "atomos": atomos,
    }


def sequenciar(dados: dict[str, Any]) -> dict[str, Any]:
    """Devolve os pedidos na ordem de embalagem.

    Args:
        dados: saida de core_separacao.processar_batch_picking().

    Retorna:
        {"sequencia": [...], "grupos": [...], "total", "multi_itens"}
        Cada pedido ganha `posicao`, `atomo_chave` e `grupo`.
    """
    import core_separacao as cs

    pedidos: list[dict[str, Any]] = []
    for chave in ("pedidos_simples_1un", "pedidos_simples_multi_un",
                  "pedidos_multi_itens"):
        pedidos.extend(dados.get(chave) or [])

    enriquecidos: list[dict[str, Any]] = []
    for pedido in pedidos:
        info = _classificar(pedido)
        # A familia vem do core_separacao, a mesma que a lista de coleta usa
        if not info["familia"]:
            info["familia"] = cs.extrair_familia(info["atomo_chave"])
        enriquecidos.append({**pedido, **info})

    enriquecidos.sort(key=lambda p: (
        p["multi"],                       # 1. multi-item por ultimo
        _peso_familia(p["familia"]),      # 2. familia (ordem do estoque)
        p["linha"],                       # 3. linha do produto
        p["sortido"],                     # 4. sortida fecha a linha
        p["cor"],                         #    cores em ordem alfabetica
        p["total_pecas"],                 # 5. kit MENOR primeiro (invertido 26/08)
        str(p.get("numero_ecommerce") or ""),
    ))

    # Numera e agrupa para a tela
    grupos: list[dict[str, Any]] = []
    atual: dict[str, Any] | None = None

    for posicao, pedido in enumerate(enriquecidos, start=1):
        pedido["posicao"] = posicao
        rotulo = (f"MULTI: {pedido['atomo_chave']}" if pedido["multi"]
                  else pedido["atomo_chave"])
        pedido["grupo"] = rotulo

        if atual is None or atual["rotulo"] != rotulo:
            atual = {"rotulo": rotulo, "familia": pedido["familia"],
                     "pedidos": [], "pecas": 0, "de": posicao, "ate": posicao}
            grupos.append(atual)

        atual["pedidos"].append(pedido)
        atual["pecas"] += pedido["total_pecas"]
        atual["ate"] = posicao

    return {
        "sequencia": enriquecidos,
        "grupos": grupos,
        "total": len(enriquecidos),
        "multi_itens": sum(1 for p in enriquecidos if p["multi"]),
        "total_pecas": sum(p["total_pecas"] for p in enriquecidos),
    }


def resumo_texto(resultado: dict[str, Any]) -> str:
    """Sequencia em texto — para conferir na tela ou imprimir."""
    linhas: list[str] = []

    for grupo in resultado["grupos"]:
        linhas.append("")
        span = (f"#{grupo['de']}" if grupo["de"] == grupo["ate"]
                else f"#{grupo['de']}-{grupo['ate']}")
        linhas.append(f"--- {grupo['rotulo']}  ({len(grupo['pedidos'])} caixas, "
                      f"{grupo['pecas']} pecas)  {span} ---")

        for pedido in grupo["pedidos"]:
            canal = (pedido.get("canal") or {})
            canal = canal.get("nome", "") if isinstance(canal, dict) else str(canal)
            # ⚠️ Quantidade > 1 em destaque: e' o que passa despercebido na
            # bancada (pedido 428 tinha 4 unidades e saiu sem aviso)
            un = pedido.get("unidades") or 1
            marca = f"  <<< {un} UNIDADES" if un > 1 else ""
            linhas.append(
                f"  {pedido['posicao']:>3}. {pedido['total_pecas']:>3}pc  "
                f"{str(pedido.get('cliente') or '')[:24]:24s} {canal}{marca}"
            )

    linhas.append("")
    linhas.append(f"TOTAL: {resultado['total']} caixas · "
                  f"{resultado['total_pecas']} pecas"
                  + (f" · {resultado['multi_itens']} multi-item no fim"
                     if resultado["multi_itens"] else ""))
    return "\n".join(linhas).strip()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import core_cache_expedicao as cm
    import core_separacao as cs

    registro = cm.ler("pedidos_sit7")
    if not registro:
        print("Sem cache — rode a sincronização na página primeiro.")
        raise SystemExit(1)

    r = sequenciar(cs.processar_batch_picking(registro["dados"]))
    print(resumo_texto(r))
