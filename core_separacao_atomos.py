# ==============================================================================
# NOME DO SCRIPT: core_separacao_atomos.py
# DESCRICAO: Decompoe SKU de kit (taxonomia V5) em atomos para a coleta fisica
# FUNCAO: A lista de separacao precisa dizer "24 meias pretas 40/46", nao
#         "4 kits de 6". O SKU V5 ja carrega essa informacao — basta ler.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# REF: taxonomia_sku_kit_v5 — {PROD}{REF}{TAM}-{COR}{qtd}[-{COR}{qtd}]_KIT{total}
# ==============================================================================
"""
Taxonomia V5 (o que o parser assume):

    _  = MACRO  separa blocos (SPU+TAM distintos) e o bloco final KIT{total}
    -  = MICRO  separa pares {COR}{qtd} dentro do mesmo SPU+TAM
    {COR}{qtd} colados, cor com 3 letras (PRE1, BRA2, SOR6)

Exemplos:
    CALCLI3636M-SEN3_KIT3              -> 3x CALCLI3636MSEN
    CALCLI3636G-PRE1-BRA1_KIT2         -> 1x CALCLI3636GPRE + 1x CALCLI3636GBRA
    FIO3845P-PRE2_CALCLI3636P-SEN1_KIT3-> 2x FIO3845PPRE + 1x CALCLI3636PSEN
    CALTAY748GPRE                      -> 1x CALTAY748GPRE (unitario, nao e' kit)

Uso:
    from core_separacao_atomos import decompor_sku, consolidar_atomos
    decompor_sku("MEMEDMAY1034046-PRE6_KIT6")
    # [{"atomo": "MEMEDMAY1034046PRE", "qtd": 6}]
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# {COR}{qtd} -> 3 letras maiusculas + numero. Ex: PRE1, BRA12, SOR6
_RE_COR_QTD = re.compile(r"^([A-Z]{3})(\d+)$")
_RE_KIT_TOTAL = re.compile(r"^KIT(\d+)$", re.IGNORECASE)


def decompor_sku(sku: str) -> list[dict[str, Any]]:
    """Devolve os atomos que compoem o SKU e quantas unidades de cada.

    Cada atomo carrega `padrao_v5` e, quando falso, um `motivo` dizendo o que
    nao casou. O motivo SOBE ate' a tela — SKU mal formado nao pode passar
    despercebido, porque a quantidade pode estar errada e a peca sair faltando
    da caixa (incidente TOPTAY016G_PRE1, 2026-08-16).

    Unitario legitimo (sem `_KIT`) tambem devolve `padrao_v5: False`, mas com
    motivo "unitario" — nao e' erro, e' item de 1 peca so'.
    """
    sku = (sku or "").strip().upper()
    if not sku:
        return []

    blocos = sku.split("_")

    # Ultimo bloco deve ser KIT{n}; se nao for, e' unitario ou fora do padrao.
    if len(blocos) < 2 or not _RE_KIT_TOTAL.match(blocos[-1]):
        if len(blocos) >= 2:
            # ⚠️ Variante conhecida: "_" usado onde a V5 pede "-", sem bloco
            # KIT final. Ex: TOPTAY016G_PRE1 == TOPTAY016G-PRE1 (1 peca).
            # A informacao esta' toda la' — {COR}{qtd} legivel — entao LEMOS
            # em vez de alarmar. So' vira problema se a cor nao casar.
            corrigido = sku.replace("_", "-")
            partes = corrigido.split("-")
            base, cores = partes[0], partes[1:]

            if cores and all(_RE_COR_QTD.match(c) for c in cores):
                return [
                    {
                        "atomo": f"{base}{_RE_COR_QTD.match(c).group(1)}",
                        "qtd": int(_RE_COR_QTD.match(c).group(2)),
                        "padrao_v5": True,
                        "variante": "separador '_' no lugar de '-'",
                    }
                    for c in cores
                ]

            return [{
                "atomo": sku, "qtd": 1, "padrao_v5": False,
                "motivo": (
                    f"tem '_' mas '{blocos[-1]}' nao e' KIT{{n}} nem "
                    "{COR}{qtd} — quantidade assumida: 1"
                ),
            }]
        return [{"atomo": sku, "qtd": 1, "padrao_v5": False, "motivo": "unitario"}]

    atomos: list[dict[str, Any]] = []

    for bloco in blocos[:-1]:                       # ignora o KIT{n} final
        partes = bloco.split("-")
        base = partes[0]                            # {PROD}{REF}{TAM}
        cores = partes[1:]

        if not cores:
            atomos.append({
                "atomo": base, "qtd": 1, "padrao_v5": False,
                "motivo": f"bloco '{bloco}' sem cor — quantidade assumida: 1",
            })
            continue

        for par in cores:
            m = _RE_COR_QTD.match(par)
            if not m:
                # cor fora do padrao: preserva o item em vez de descartar
                atomos.append({
                    "atomo": f"{base}{par}", "qtd": 1, "padrao_v5": False,
                    "motivo": (
                        f"'{par}' nao casa com {{COR}}{{qtd}} — "
                        "quantidade assumida: 1"
                    ),
                })
                continue
            cor, qtd = m.group(1), int(m.group(2))
            atomos.append({"atomo": f"{base}{cor}", "qtd": qtd, "padrao_v5": True})

    if not atomos:
        return [{
            "atomo": sku, "qtd": 1, "padrao_v5": False,
            "motivo": "nao foi possivel decompor — quantidade assumida: 1",
        }]
    return atomos


def validar_kit(sku: str) -> dict[str, Any]:
    """Confere se a soma das qtd bate com o KIT{total} declarado.

    A V5 e' autovalidavel de proposito: se nao bater, o SKU esta errado.
    """
    sku = (sku or "").strip().upper()
    blocos = sku.split("_")
    m = _RE_KIT_TOTAL.match(blocos[-1]) if len(blocos) >= 2 else None

    if not m:
        return {"e_kit": False, "ok": True, "declarado": None, "somado": None}

    declarado = int(m.group(1))
    somado = sum(a["qtd"] for a in decompor_sku(sku))

    return {
        "e_kit": True,
        "ok": declarado == somado,
        "declarado": declarado,
        "somado": somado,
    }


def _spu_do_atomo(atomo: str) -> str:
    """'MEMEDMAY1034046-BRA' -> 'MEMEDMAY1034046'. O produto sem a cor.

    Serve para manter TODAS as cores do mesmo produto juntas na lista de
    coleta: a Meia Media Preta ao lado da Branca, e nao separadas por 5
    linhas de Meia Invisivel so' porque uma tem 48un e a outra 12un.
    """
    a = (atomo or "").strip().upper()
    if not a:
        return ""
    # ⚠️ O atomo consolidado vem SEM hifen ('MEMEDMAY1034046BRA'), diferente
    # do SKU de venda ('MEMEDMAY1034046-BRA6_KIT6'). Entao nao da' para
    # cortar no separador: tira-se o sufixo de COR (3 letras) do fim.
    if "-" in a:
        return a.rsplit("-", 1)[0]
    return a[:-3] if len(a) > 3 and a[-3:].isalpha() else a


def consolidar_atomos(lista_coleta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transforma a lista por SKU de kit numa lista por ATOMO, somada.

    Entrada: itens de core_separacao.processar_batch_picking()["lista_coleta"],
    cada um com `sku`, `total_unidades`, `familia`, `descricao`.

    Saida ordenada por familia e depois por quantidade (maior primeiro), que e'
    a ordem util para quem esta coletando.
    """
    acumulado: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"qtd": 0, "familia": "", "de_kits": set(), "pedidos": set(),
                 "problemas": [], "suspeito": False}
    )

    for item in lista_coleta:
        sku = item.get("sku", "")
        mult = int(item.get("total_unidades", 1) or 1)
        familia = item.get("familia", "")
        pedidos = item.get("pedidos", []) or []

        # Kit que nao fecha: o total declarado nao bate com a soma das cores.
        # Anexa o aviso em TODOS os atomos do kit — qualquer um pode ser o errado.
        v = validar_kit(sku)
        alerta_kit = None
        if v["e_kit"] and not v["ok"]:
            alerta_kit = (
                f"kit {sku} declara KIT{v['declarado']} mas as cores somam "
                f"{v['somado']} — conferir a quantidade real"
            )

        for parte in decompor_sku(sku):
            chave = parte["atomo"]
            reg = acumulado[chave]
            reg["qtd"] += parte["qtd"] * mult
            reg["familia"] = reg["familia"] or familia
            reg["de_kits"].add(sku)
            reg["pedidos"].update(pedidos)

            motivo = parte.get("motivo")
            # "unitario" e' item legitimo de 1 peca, nao problema.
            if motivo and motivo != "unitario":
                reg["problemas"].append(f"{sku}: {motivo}")
                reg["suspeito"] = True
            if alerta_kit:
                reg["problemas"].append(alerta_kit)
                reg["suspeito"] = True

    saida = [
        {
            "atomo": atomo,
            "qtd": dados["qtd"],
            "familia": dados["familia"],
            "kits_origem": sorted(dados["de_kits"]),
            "total_pedidos": len(dados["pedidos"]),
            # `suspeito` = a QUANTIDADE pode estar errada. Sobe para a tela.
            "suspeito": dados["suspeito"],
            "problemas": sorted(set(dados["problemas"])),
        }
        for atomo, dados in acumulado.items()
    ]

    # Suspeitos primeiro: quem coleta precisa ver o problema antes de errar.
    #
    # ⚠️ Depois vem o SPU, NAO a quantidade (Jota, 25/08: "meia media preta e
    # branca nao estao em sequencia").
    # Ordenando por -qtd dentro da familia, a Meia Med Branca (48un) subia
    # para o topo e a Meia Med Preta (12un) caia 5 linhas abaixo, com as
    # Invisiveis no meio. Quem coleta ia ate' a prateleira da meia media,
    # voltava, e tinha de voltar la' de novo.
    #
    # Com o SPU antes, TODA a meia media sai junta; a quantidade so' ordena
    # dentro do mesmo produto.
    saida.sort(key=lambda x: (not x["suspeito"], x["familia"],
                              _spu_do_atomo(x["atomo"]), -x["qtd"], x["atomo"]))
    return saida


def problemas_da_coleta(atomos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lista achatada dos problemas encontrados, para exibir no fim da pagina.

    ⚠️ Existe porque antes o SKU mal formado entrava na lista mudo, com
    quantidade assumida como 1. Se o kit era de 2, a peca saía faltando da
    caixa e ninguem via. Agora o problema aparece — o operador decide.
    """
    return [
        {"atomo": a["atomo"], "qtd": a["qtd"], "familia": a["familia"],
         "problemas": a["problemas"], "kits_origem": a["kits_origem"]}
        for a in atomos
        if a.get("suspeito")
    ]


# Meia sai do fornecedor em pacote fechado de 12 — quem coleta pensa em
# pacotes, nao em pares soltos (Jota, 2026-08-16).
PECAS_POR_PACOTE = 12

# Sufixo de cor que marca produto SORTIDO. Vai para uma pilha separada porque
# sortida nao se conta junto com cor unica: o pacote ja' vem misturado.
COR_SORTIDA = "SOR"


def e_sortido(atomo: str) -> bool:
    """True se o atomo e' de cor SORTIDA (pacote ja' vem misturado)."""
    return (atomo or "").strip().upper().endswith(COR_SORTIDA)


def e_meia(atomo: str) -> bool:
    """True se o atomo e' meia — as unicas que vem em pacote de 12."""
    return (atomo or "").strip().upper().startswith("ME")


def em_pacotes(qtd: int, por_pacote: int = PECAS_POR_PACOTE) -> dict[str, Any]:
    """Quebra a quantidade em pacotes fechados + sobra solta.

    48 -> 4 pacotes de 12
    50 -> 4 pacotes de 12 + 2 soltas
    """
    if por_pacote <= 0:
        return {"pacotes": 0, "sobra": qtd, "texto": f"{qtd} un"}

    pacotes, sobra = divmod(qtd, por_pacote)

    partes: list[str] = []
    if pacotes:
        partes.append(f"{pacotes} pacote{'s' if pacotes > 1 else ''} de {por_pacote}")
    if sobra:
        partes.append(f"{sobra} solta{'s' if sobra > 1 else ''}")

    return {
        "pacotes": pacotes,
        "sobra": sobra,
        "texto": " + ".join(partes) if partes else "0 un",
    }


def separar_sortidas(atomos: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Divide a coleta em duas pilhas: cor unica e sortida.

    Sao separacoes fisicas diferentes — a sortida ja' vem misturada no pacote,
    entao nao se soma nem se confere junto com cor unica.
    """
    unica = [a for a in atomos if not e_sortido(a["atomo"])]
    sortida = [a for a in atomos if e_sortido(a["atomo"])]
    return {"cor_unica": unica, "sortida": sortida}


def resumo_texto(atomos: list[dict[str, Any]]) -> str:
    """Lista de coleta em texto — para imprimir ou colar na prancheta."""
    if not atomos:
        return "Nenhum item para separar."

    # ⚠️ LISTA UNICA. SOR (sortida) e' so' mais uma cor do atomo — a meia
    # invisivel existe em SOR e tambem em PRE/BRA/CIN. Separar em duas listas
    # quebrava o agrupamento por familia sem ganho nenhum (Jota, 2026-08-16).
    linhas: list[str] = []
    total_pecas = 0
    familia_atual = None

    for a in atomos:
        if a["familia"] != familia_atual:
            familia_atual = a["familia"]
            linhas.append("")
            linhas.append(f"--- {familia_atual or 'SEM FAMILIA'} ---")

        detalhe = ""
        if e_meia(a["atomo"]):
            detalhe = f"  ({em_pacotes(a['qtd'])['texto']})"
        marca = "  <<< CONFERIR" if a.get("suspeito") else ""
        linhas.append(f"  [ ] {a['qtd']:>3}x  {a['atomo']}{detalhe}{marca}")
        total_pecas += a["qtd"]

    linhas.append("")
    linhas.append(f"TOTAL: {total_pecas} peças em {len(atomos)} SKUs distintos")

    # Os problemas vao no FIM, onde nao se perdem no meio da lista.
    achados = problemas_da_coleta(atomos)
    if achados:
        linhas.append("")
        linhas.append("=" * 52)
        linhas.append(f"!!! {len(achados)} ITEM(NS) A CONFERIR — quantidade pode estar errada")
        linhas.append("=" * 52)
        for p in achados:
            linhas.append(f"  {p['atomo']} (contado como {p['qtd']} un)")
            for msg in p["problemas"]:
                linhas.append(f"      - {msg}")

    return "\n".join(linhas).strip()


if __name__ == "__main__":
    testes = [
        "MEMEDMAY1034046-PRE6_KIT6",
        "CALCLI3636M-SEN3_KIT3",
        "CALCLI3636G-PRE1-BRA1_KIT2",
        "FIO3845P-PRE2_CALCLI3636P-SEN1_KIT3",
        "CALTAY748GPRE",
        "CALMAY201GG-SOR12_KIT12",
    ]
    for t in testes:
        v = validar_kit(t)
        marca = "" if v["ok"] else f"  ⚠️ declarado {v['declarado']} != somado {v['somado']}"
        print(f"{t:38s} -> {decompor_sku(t)}{marca}")
