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

        # 🆕 25/09/2026 — Ordenacao fisica de bancada (aprovado /conselho
        # secao 23, chave de sort do Monge). Antes o multi-item ordenava
        # so' pela string `combinacao` -- coincidencia de alfabeto, sem
        # relacao com o deslocamento real do operador entre prateleiras.
        #
        # ⚠️ CORRECAO 25/09: a proposta original usava `extrair_familia`
        # (MEIAS/CALCINHAS/...), mas essa granularidade e' GROSSEIRA DEMAIS
        # pra bancada -- MEINV (Invisivel) e MEMED (Cano Medio) sao ambas
        # "MEIAS", mas ficam em prateleiras/caixas FISICAS diferentes.
        # A diferenca real de deslocamento e' a LINHA do produto (SPU+
        # tamanho, o mesmo criterio 3 ja usado no grupo unico), nao a
        # familia ampla. Usando linha em vez de familia:
        #   - is_cross_linha: True se os atomos vem de LINHAS diferentes
        #     (custa mais passos que ficar na mesma linha/prateleira)
        #   - peso_familia_dominante: mantido p/ desempate entre combinacoes
        #     cross-linha (ainda usa a familia ampla como 2o criterio)
        #   - tem_sortida: algum atomo da combinacao e' cor sortida
        #     (ainda exige garimpar no monte, mesmo dentro do multi-item)
        import core_separacao as _cs

        def _linha_do_atomo(atomo: str) -> str:
            return atomo[:-3] if len(atomo) > 3 else atomo

        def _modelo_da_linha(linha: str) -> str:
            # Linha sem a grade de tamanho: MEINVMAY1013540 -> MEINVMAY101.
            # Invisivel FEM 35/40 e MASC 40/46 sao linhas diferentes, mas o
            # MESMO modelo -- parentes na prateleira (Jota, 25/09: "sao
            # diferentes, porem mais parecidas que invisivel e cano medio...
            # junta, mas separa").
            import re as _re
            return _re.sub(r"\d{4}$", "", linha)

        linhas_combinacao = {_linha_do_atomo(a) for a in distintos}
        modelos_combinacao = {_modelo_da_linha(l) for l in linhas_combinacao}
        is_cross_linha = len(linhas_combinacao) > 1
        # 0 = mesma linha (1 prateleira) · 1 = mesmo modelo, grades
        # diferentes (prateleiras vizinhas) · 2 = modelos diferentes
        nivel_mistura = (0 if len(linhas_combinacao) == 1
                         else 1 if len(modelos_combinacao) == 1 else 2)
        familias_combinacao = {_cs.extrair_familia(a) for a in distintos}
        peso_familia_dominante = min(
            (_peso_familia(f) for f in familias_combinacao), default=len(ORDEM_FAMILIA)
        )
        tem_sortida = any(a.upper().endswith(COR_SORTIDA) for a in distintos)

        return {
            "multi": True,
            "atomo_chave": combinacao,
            "familia": "MULTI-ITEM",
            "linha": combinacao,        # mantido p/ exibicao (titulo do grupo na lista)
            "cor": "",
            "sortido": False,
            "total_pecas": total_pecas,
            "unidades": unidades,
            "atomos": atomos,
            "is_cross_linha": is_cross_linha,
            "nivel_mistura": nivel_mistura,
            "modelos_chave": " + ".join(sorted(modelos_combinacao)),
            "peso_familia_dominante": peso_familia_dominante,
            "num_distintos": len(distintos),
            "tem_sortida": tem_sortida,
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

    def _chave_sort(p: dict[str, Any]) -> tuple:
        # O 1o elemento (`p["multi"]`) sempre separa os dois grupos antes de
        # qualquer outra comparacao -- Python so' olha o 2o elemento da tupla
        # quando o 1o empata, e como aqui e' sempre False vs True, os dois
        # ramos abaixo NUNCA sao comparados item-a-item entre si (senao
        # comparar bool com string quebraria em runtime).
        if p["multi"]:
            # 🆕 25/09/2026 — chave aprovada /conselho secao 23 (Monge),
            # ajustada apos teste real: familia ampla -> nivel de mistura
            # por linha/modelo (ver _classificar). Substitui a ordenacao por
            # string alfabetica da combinacao -- Sala de Guerra secao 22-24.
            # Combinacoes identicas continuam coladas: compartilham todos os
            # campos antes de `atomo_chave`.
            return (
                True,                              # multi-item sempre depois do grupo unico
                p.get("nivel_mistura", 2),         # mesma linha > mesmo modelo > modelos diferentes
                p.get("peso_familia_dominante", len(ORDEM_FAMILIA)),
                p.get("modelos_chave", ""),        # mesma mistura de modelos fica junta
                p.get("num_distintos", 99),        # menos produtos distintos = mais simples
                p.get("tem_sortida", False),       # sortida fecha o subgrupo
                p["atomo_chave"],                  # a mesma combinacao exata, colada
                p["total_pecas"],                  # peso total crescente, mesmo criterio 5
                str(p.get("numero_ecommerce") or ""),
            )
        return (
            False,                             # 1. multi-item por ultimo
            _peso_familia(p["familia"]),      # 2. familia (ordem do estoque)
            p["linha"],                       # 3. linha do produto
            p["sortido"],                     # 4. sortida fecha a linha
            p["cor"],                         #    cores em ordem alfabetica
            p["total_pecas"],                 # 5. kit MENOR primeiro (invertido 26/08)
            str(p.get("numero_ecommerce") or ""),
        )

    enriquecidos.sort(key=_chave_sort)

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
