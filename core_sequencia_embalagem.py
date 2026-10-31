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
A ordem (definida pelo Jota; REDEFINIDA em 2026-10-29 -- vale esta):

    "feminina primeiro... cores solidas antes dentro de cada genero, apos
     sortida... ordem de invisivel, soquete e cano medio, demais itens siga
     ordem alfabetica"
    "itens multiplos concentre no fim da sua categoria, exemplo 2x kit6 meia
     invisivel"
    "é sempre melhor começar pelo que é mais específico... os kits 3 sempre
     primeiro e depois você ir aumentando a quantidade deles" (26/08/2026)

Traduzido em criterios de ordenacao, nesta ordem de peso:

    1. FAMILIA                 — MEIAS antes de CALCINHAS etc (ordem do estoque)
    2. LINHA / CATEGORIA       — INVISIVEL (feminina 35/40, depois masculina
                                 40/46) -> SOQUETE (cano baixo) -> CANO MEDIO;
                                 as demais linhas seguem ordem alfabetica
                                 (ORDEM_MODELO). ⚠️ Ate' 29/10 a linha ia em
                                 ordem alfabetica pura e a Soquete saia ANTES
                                 de tudo.
    3. MULTIPLOS no FIM da categoria — dentro de CADA categoria os pedidos
                                 multiplos vao depois dos normais: quantidade
                                 maior que 1 (ex.: 2x Kit 6 Invisivel) e pedido
                                 com mais de um produto. Antes iam para o fim
                                 de TUDO.
    4. COR, sortida por ultimo — BRA, PRE, CIN... e SOR fecha a categoria. O
                                 KIT SORTIDO do Cano Medio (BRA+PRE no mesmo
                                 kit, 1 unidade) conta como sortida da linha,
                                 NAO como multi-item.
    5. QUANTIDADE crescente    — kit MENOR primeiro (Kit3, Kit6, Kit9...).

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

# Ordem das CATEGORIAS (modelo = 5 primeiras letras do SPU) dentro da familia.
# Jota, 29/10/2026: invisivel, soquete e cano medio; o resto segue a ordem alfabetica.
ORDEM_MODELO = ["MEINV", "MEBAI", "MEMED"]


def _rank_modelo(linha: str) -> int:
    m = (linha or "")[:5].upper()
    return ORDEM_MODELO.index(m) if m in ORDEM_MODELO else len(ORDEM_MODELO)


def _ordem_linha(linha: str) -> tuple[int, str]:
    """(posicao da categoria, linha). Na mesma categoria o texto desempata, o que poe a
    feminina (...3540) antes da masculina (...4046)."""
    return (_rank_modelo(linha), linha or "")


def _peso_familia(familia: str) -> int:
    familia = (familia or "").strip().upper()
    return ORDEM_FAMILIA.index(familia) if familia in ORDEM_FAMILIA else len(ORDEM_FAMILIA)


def _classificar(pedido: dict[str, Any]) -> dict[str, Any]:
    """Descobre em que grupo de embalagem o pedido entra."""
    import core_separacao as _cs

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

    def _linha_do_atomo(atomo: str) -> str:
        return atomo[:-3] if len(atomo) > 3 else atomo

    linhas = {_linha_do_atomo(a) for a in distintos}

    # KIT SORTIDO: 1 item cujo kit junta 2+ cores da MESMA linha (ex.: BRA3-PRE3_KIT6).
    # Nao e' multi-item: e' a sortida da linha e fecha a categoria (Jota, 29/10/2026).
    kit_sortido = len(itens) == 1 and len(distintos) > 1 and len(linhas) == 1
    # MULTI-ITEM de verdade: produtos diferentes no pedido.
    multi_item = len(distintos) > 1 and not kit_sortido
    # QUANTIDADE > 1 (ex.: 2x Kit 6): tambem vai para o fim da categoria.
    qtd_multipla = unidades > 1
    multiplo = multi_item or qtd_multipla

    if multi_item:
        # Os IGUAIS ficam juntos: `atomo_chave` e' a combinacao ordenada.
        combinacao = " + ".join(sorted(distintos))
        familias = {_cs.extrair_familia(a) for a in distintos}
        peso_familia_dominante = min(
            (_peso_familia(f) for f in familias), default=len(ORDEM_FAMILIA))
        return {
            "multi": True,
            "multi_item": True,
            "multiplo": True,
            "atomo_chave": combinacao,
            "familia": "MULTI-ITEM",
            "linha": combinacao,        # exibicao (titulo do grupo na lista)
            # categoria onde o pedido cai: a PRIMEIRA na ordem da bancada
            "linha_ordem": min(_ordem_linha(l) for l in linhas),
            "cor": "",
            "sortido": False,
            "total_pecas": total_pecas,
            "unidades": unidades,
            "atomos": atomos,
            "peso_familia_dominante": peso_familia_dominante,
            "num_distintos": len(distintos),
            "tem_sortida": any(a.upper().endswith(COR_SORTIDA) for a in distintos),
        }

    if kit_sortido:
        combinacao = " + ".join(sorted(distintos))
        linha = next(iter(linhas))
        return {
            "multi": multiplo,
            "multi_item": False,
            "multiplo": multiplo,
            "atomo_chave": combinacao,
            "familia": _cs.extrair_familia(sorted(distintos)[0]),
            "linha": linha,
            "linha_ordem": _ordem_linha(linha),
            "cor": combinacao,
            "sortido": True,            # fecha a categoria
            "total_pecas": total_pecas,
            "unidades": unidades,
            "atomos": atomos,
        }

    atomo = next(iter(distintos), "")
    # {LINHA}{COR}: a cor sao as 3 ultimas letras do atomo V5
    linha, cor = (atomo[:-3], atomo[-3:]) if len(atomo) > 3 else (atomo, "")

    return {
        "multi": multiplo,
        "multi_item": False,
        "multiplo": multiplo,
        "atomo_chave": atomo,
        "familia": "",          # preenchida por `sequenciar` via core_separacao
        "linha": linha,
        "linha_ordem": _ordem_linha(linha),
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
        # Chave unica (tipos iguais em cada posicao):
        #   familia -> categoria (ordem da bancada) -> NORMAIS antes dos MULTIPLOS ->
        #   (dentro dos multiplos: quantidade>1 antes de multi-item) -> sortida fecha ->
        #   cor -> kit menor primeiro -> numero do pedido (desempate estavel).
        multi_item = bool(p.get("multi_item"))
        fam = (p.get("peso_familia_dominante", len(ORDEM_FAMILIA)) if multi_item
               else _peso_familia(p["familia"]))
        rank_spu, linha = p["linha_ordem"]
        return (
            fam,                               # 1. familia
            rank_spu,                          # 2. categoria = SPU: invisivel, soquete, cano medio...
            bool(p.get("multiplo")),           # 3. multiplos no FIM do SPU (fem + masc juntas)
            multi_item,                        #    qtd>1 antes de multi-item
            linha,                             # 4. dentro do SPU: feminina antes da masculina
            bool(p.get("tem_sortida")) if multi_item else bool(p["sortido"]),  # 5. sortida fecha
            p["atomo_chave"] if multi_item else p["cor"],      # combinacao igual fica colada
            p["total_pecas"],                  # 6. kit MENOR primeiro
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
