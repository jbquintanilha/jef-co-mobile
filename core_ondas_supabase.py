# ==============================================================================
# NOME DO SCRIPT: core_ondas_supabase.py
# DESCRICAO: Ondas de expedicao em SLOTS FIXOS (1..5), persistidas no Supabase
# FUNCAO: Substitui core_ondas_expedicao (SQLite local), que perdia as ondas
#         sozinho. Onda e' um AGRUPAMENTO estavel, nao um contador diario.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 31/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""Ondas de expedicao — 5 slots fixos, persistentes, na nuvem.

## Por que reescrever (Jota, 31/08/2026)

    "elas nao ficam salvas por muito tempo... as vezes estao as vezes nao,
     nao sei oq zera elas... ideal seria ter a persistencia das ondas ou vc
     fazer slots... tipos ondas de 1 a 5 e eu escolho qual sobrescrever ou
     qual zerar"

Tres bugs reais do modelo antigo (`core_ondas_expedicao.py`), todos
confirmados no codigo:

1. `limpar_ausentes()` rodava a CADA render da pagina e fazia
   `DELETE ... WHERE numero_ecommerce NOT IN (lista_atual)`. Bastava a
   sincronizacao vir parcial (API de um canal falhou, filtro aplicado, cache
   incompleto) para a onda inteira ser apagada — dentro de um `except: pass`,
   ou seja, em silencio. Essa e' a causa principal do "as vezes estao as
   vezes nao".
2. TODA consulta filtrava por `dia = hoje`. Virava meia-noite e a onda sumia
   da tela mesmo continuando gravada.
3. O numero da onda vinha de `MAX(onda)+1` do dia — contador infinito, sem
   como escolher onde gravar nem o que zerar.

## O modelo novo

- **5 slots fixos.** Onda e' um agrupamento: voce escolhe o slot, sobrescreve
  ou reseta. Nao existe "onda 47".
- **Sem coluna de dia.** O slot vive ate' voce zerar. Nada expira sozinho.
- **Nada apaga em massa automaticamente.** `limpar_despachados()` existe, mas
  e' explicita, exige lista nao-vazia e NUNCA remove o slot inteiro.
- **Um pedido, um slot** (decisao do Jota): salvar num slot novo MOVE o
  pedido, com aviso. Evita reimprimir/despachar duplicado.
- **`ordem_impressao`** guarda a posicao real do pedido no PDF impresso. E' o
  que permite "marcar ate' aqui" sem depender da numeracao sequencial da
  Olist, que nao serve porque pedido antigo pode entrar na fila depois
  (pagamento atrasado).

Uso:
    import core_ondas_supabase as ondas
    ondas.salvar_slot(2, pedidos, modo="somar")   # ou "substituir"
    ondas.listar_slots()                          # os 5, com contagem
    ondas.zerar_slot(2)
    ondas.marcar(pedidos)                         # anota `onda` em cada um
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Literal

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")

TABELA = "ondas_expedicao"
TABELA_FASES = "ondas_expedicao_fases"
TABELA_SLOTS = "ondas_expedicao_slots"

# Slots fixos. Mudar isto exige mexer no CHECK da tabela tambem.
SLOT_MIN, SLOT_MAX = 1, 5
SLOTS = list(range(SLOT_MIN, SLOT_MAX + 1))

TOTAL_FASES = 7

_TIMEOUT = 20


class OndasIndisponivel(RuntimeError):
    """Supabase fora do ar / sem credencial.

    ⚠️ Levantada de proposito em vez de devolver vazio: vazio silencioso e'
    exatamente o que fazia a tela achar que nao havia onda nenhuma e seguir
    em frente. Quem chama decide se avisa o operador ou cai pro fallback.
    """


def _headers() -> dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise OndasIndisponivel(
            "SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes no .env"
        )
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _req(metodo: str, caminho: str, *, prefer: str | None = None, **kw) -> Any:
    """Chamada a` API REST do Supabase.

    `prefer` acrescenta o header Prefer (ex: "resolution=merge-duplicates"
    para upsert). Passar `headers=` direto quebraria, porque este wrapper ja'
    monta os proprios.
    """
    url = f"{SUPABASE_URL}/rest/v1/{caminho}"
    cabecalhos = _headers()
    if prefer:
        cabecalhos["Prefer"] = prefer
    try:
        r = requests.request(metodo, url, headers=cabecalhos, timeout=_TIMEOUT, **kw)
    except requests.RequestException as e:
        raise OndasIndisponivel(f"Supabase inacessivel: {e}") from e
    if r.status_code >= 400:
        raise OndasIndisponivel(f"Supabase {r.status_code}: {r.text[:200]}")
    if not r.content:
        return []
    try:
        return r.json()
    except ValueError:
        return []


def _num(p: dict[str, Any]) -> str:
    return str(p.get("numero_ecommerce") or "").strip().upper()


def _valida_slot(slot: int) -> int:
    s = int(slot)
    if not (SLOT_MIN <= s <= SLOT_MAX):
        raise ValueError(f"slot precisa estar entre {SLOT_MIN} e {SLOT_MAX}: {slot}")
    return s


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #

def mapa() -> dict[str, int]:
    """{numero_ecommerce: slot} de tudo que esta' em alguma onda."""
    linhas = _req("GET", f"{TABELA}?select=numero_ecommerce,slot")
    return {l["numero_ecommerce"]: l["slot"] for l in linhas}


def marcar(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anota `onda` em cada pedido (None = ainda nao entrou em onda).

    Se o Supabase estiver fora, marca todos como None e avisa no log — a
    esteira continua utilizavel, so' sem a informacao de onda.
    """
    try:
        m = mapa()
    except OndasIndisponivel as e:
        log.warning("Ondas indisponiveis, seguindo sem marcar: %s", e)
        m = {}
    for p in pedidos:
        p["onda"] = m.get(_num(p))
    return pedidos


def pedidos_do_slot(slot: int) -> set[str]:
    """Os `numero_ecommerce` de um slot — o filtro que as fases aplicam."""
    s = _valida_slot(slot)
    linhas = _req("GET", f"{TABELA}?slot=eq.{s}&select=numero_ecommerce")
    return {l["numero_ecommerce"] for l in linhas}


def fases_do_slot(slot: int) -> dict[int, bool]:
    """{fase: concluida} do slot."""
    s = _valida_slot(slot)
    linhas = _req("GET", f"{TABELA_FASES}?slot=eq.{s}&select=fase,concluida")
    return {l["fase"]: bool(l["concluida"]) for l in linhas}


def listar_slots() -> list[dict[str, Any]]:
    """Os 5 slots, sempre — inclusive os vazios.

    Devolver os vazios tambem e' proposital: o operador precisa ver que o
    slot 4 existe e esta' livre, senao nao sabe onde pode gravar.
    """
    try:
        pedidos = _req("GET", f"{TABELA}?select=slot,numero_ecommerce,criado_em")
        fases = _req("GET", f"{TABELA_FASES}?concluida=is.true&select=slot,fase")
        rotulos = {r["slot"]: r.get("rotulo")
                   for r in _req("GET", f"{TABELA_SLOTS}?select=slot,rotulo")}
    except OndasIndisponivel as e:
        log.warning("Nao foi possivel listar os slots: %s", e)
        raise

    por_slot: dict[int, list[dict]] = {s: [] for s in SLOTS}
    for p in pedidos:
        por_slot.setdefault(p["slot"], []).append(p)

    feitas: dict[int, set[int]] = {}
    for f in fases:
        feitas.setdefault(f["slot"], set()).add(f["fase"])

    saida = []
    for s in SLOTS:
        itens = por_slot.get(s, [])
        f = feitas.get(s, set())
        quando = min((i.get("criado_em") or "" for i in itens), default="")
        saida.append({
            "slot": s,
            "rotulo": rotulos.get(s) or "",
            "pedidos": len(itens),
            "vazio": not itens,
            "fases_feitas": sorted(f),
            "total_fases": len(f),
            "concluida": len(f) >= TOTAL_FASES,
            "quando": quando,
        })
    return saida


# --------------------------------------------------------------------------- #
# Escrita
# --------------------------------------------------------------------------- #

def salvar_slot(
    slot: int,
    pedidos: list[dict[str, Any]],
    *,
    modo: Literal["somar", "substituir"] = "somar",
    com_ordem: bool = True,
) -> dict[str, Any]:
    """Grava pedidos num slot.

    Args:
        slot: 1..5.
        pedidos: os pedidos a gravar (na ordem em que serao impressos).
        modo: "somar" acrescenta aos que ja' estao no slot; "substituir"
            esvazia o slot antes. A tela pergunta ao operador quando o slot
            ja' tem conteudo — nunca decide sozinha.
        com_ordem: grava `ordem_impressao` conforme a posicao na lista. E' o
            que permite "marcar ate' aqui" pela ordem real de impressao em
            vez da numeracao sequencial da Olist.

    ⚠️ Um pedido vive em UM slot (decisao do Jota, 31/08). Se ele ja' estava
    em outro, e' MOVIDO para este e a mudanca vem reportada em `movidos` —
    a tela deve mostrar isso, para nao parecer que sumiu da onda antiga.
    """
    s = _valida_slot(slot)
    limpos = [p for p in pedidos if _num(p)]
    if not limpos:
        return {"slot": s, "gravados": 0, "movidos": [], "removidos": 0}

    removidos = 0
    if modo == "substituir":
        removidos = zerar_slot(s, apagar_fases=False)["removidos"]

    antes = mapa()
    movidos = [
        {"numero_ecommerce": _num(p), "de": antes[_num(p)], "para": s}
        for p in limpos
        if antes.get(_num(p)) is not None and antes[_num(p)] != s
    ]

    linhas = []
    for i, p in enumerate(limpos, start=1):
        linhas.append({
            "slot": s,
            "numero_ecommerce": _num(p),
            "numero_olist": str(p.get("numero_olist") or ""),
            "canal": str(p.get("canal") or ""),
            "ordem_impressao": i if com_ordem else None,
        })

    # on_conflict + merge-duplicates: reenviar o mesmo pedido atualiza o slot
    # em vez de estourar a UNIQUE — e' assim que o "mover de slot" funciona.
    _req(
        "POST",
        f"{TABELA}?on_conflict=numero_ecommerce",
        json=linhas,
        prefer="resolution=merge-duplicates",
    )

    log.info("Slot %d: %d pedido(s) gravados (modo=%s, %d movidos de outro slot)",
             s, len(linhas), modo, len(movidos))
    return {
        "slot": s,
        "gravados": len(linhas),
        "movidos": movidos,
        "removidos": removidos,
        "modo": modo,
    }


def zerar_slot(slot: int, *, apagar_fases: bool = True) -> dict[str, Any]:
    """Esvazia um slot. Os pedidos voltam a ser pendentes.

    `apagar_fases=False` e' uso interno de `salvar_slot(modo="substituir")`:
    ali o slot continua sendo o mesmo lote de trabalho, so' troca de conteudo.
    """
    s = _valida_slot(slot)
    antes = len(pedidos_do_slot(s))
    _req("DELETE", f"{TABELA}?slot=eq.{s}")
    if apagar_fases:
        # Sem isto o progresso ficaria orfao e o proximo lote nasceria
        # "meio pronto" — mesmo bug que o modelo antigo tinha.
        _req("DELETE", f"{TABELA_FASES}?slot=eq.{s}")
    log.info("Slot %d zerado: %d pedido(s) liberados", s, antes)
    return {"slot": s, "removidos": antes}


def remover_pedidos(numeros: Iterable[str]) -> int:
    """Tira pedidos especificos de qualquer slot (volta a pendente)."""
    alvo = [str(n).strip().upper() for n in numeros if str(n).strip()]
    if not alvo:
        return 0
    lista = ",".join(f'"{n}"' for n in alvo)
    _req("DELETE", f"{TABELA}?numero_ecommerce=in.({lista})")
    return len(alvo)


def marcar_fase(slot: int, fase: int, feita: bool = True) -> None:
    """Registra que uma fase do slot foi concluida (ou desfaz)."""
    s = _valida_slot(slot)
    f = int(fase)
    if not (0 <= f < TOTAL_FASES):
        raise ValueError(f"fase precisa estar entre 0 e {TOTAL_FASES - 1}: {fase}")
    _req(
        "POST",
        f"{TABELA_FASES}?on_conflict=slot,fase",
        json=[{"slot": s, "fase": f, "concluida": bool(feita),
               "quando": "now()" if feita else None}],
        prefer="resolution=merge-duplicates",
    )


def concluir_slot(slot: int) -> dict[str, Any]:
    """Marca as 7 fases do slot de uma vez."""
    s = _valida_slot(slot)
    for f in range(TOTAL_FASES):
        marcar_fase(s, f, True)
    return {"slot": s, "fases": TOTAL_FASES}


def reabrir_slot(slot: int, fase: int | None = None) -> dict[str, Any]:
    """Zera o progresso de fases do slot (ou de uma fase). Pedidos ficam."""
    s = _valida_slot(slot)
    if fase is None:
        _req("DELETE", f"{TABELA_FASES}?slot=eq.{s}")
    else:
        _req("DELETE", f"{TABELA_FASES}?slot=eq.{s}&fase=eq.{int(fase)}")
    return {"slot": s, "fase": fase}


def renomear_slot(slot: int, rotulo: str) -> dict[str, Any]:
    """Da um nome ao slot ("manha", "correios 14h") para achar depois."""
    s = _valida_slot(slot)
    _req(
        "POST",
        f"{TABELA_SLOTS}?on_conflict=slot",
        json=[{"slot": s, "rotulo": (rotulo or "").strip()[:60]}],
        prefer="resolution=merge-duplicates",
    )
    return {"slot": s, "rotulo": rotulo}


def limpar_despachados(numeros_ainda_na_fila: set[str]) -> int:
    """Remove pedidos que JA' sairam do Olist de vez.

    ⚠️ Esta funcao e' a versao segura do `limpar_ausentes()` antigo, que era
    a causa nº 1 das ondas sumindo: aquele rodava a cada render e apagava
    tudo que nao estivesse na lista recebida — inclusive quando a lista vinha
    parcial por falha de API.

    Travas adicionadas:
    - lista vazia NAO apaga nada (antes, esvaziava o banco);
    - NUNCA remove todos os pedidos de um slot de uma vez: se a lista sugere
      que um slot inteiro sumiu, e' quase certo que a sincronizacao veio
      incompleta, entao aborta e loga;
    - deve ser chamada explicitamente, nunca dentro do render da pagina.
    """
    if not numeros_ainda_na_fila:
        log.warning("limpar_despachados chamada com lista vazia — ignorada "
                    "(protecao contra sincronizacao parcial)")
        return 0

    atual = mapa()
    if not atual:
        return 0

    sumiram = {n for n in atual if n not in numeros_ainda_na_fila}
    if not sumiram:
        return 0

    por_slot: dict[int, int] = {}
    total_slot: dict[int, int] = {}
    for n, s in atual.items():
        total_slot[s] = total_slot.get(s, 0) + 1
        if n in sumiram:
            por_slot[s] = por_slot.get(s, 0) + 1

    for s, qtd in por_slot.items():
        if qtd == total_slot.get(s) and total_slot.get(s, 0) > 1:
            log.warning(
                "Slot %d perderia TODOS os %d pedidos de uma vez — abortando "
                "a limpeza (sincronizacao provavelmente parcial).", s, qtd)
            return 0

    return remover_pedidos(sumiram)


def diagnostico() -> dict[str, Any]:
    """Estado atual — util pra depurar 'cade minha onda?'."""
    try:
        slots = listar_slots()
        return {
            "ok": True,
            "url": SUPABASE_URL[:40] + "..." if SUPABASE_URL else "(sem URL)",
            "slots": slots,
            "total_pedidos": sum(s["pedidos"] for s in slots),
        }
    except OndasIndisponivel as e:
        return {"ok": False, "erro": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = diagnostico()
    if not d["ok"]:
        print(f"❌ {d['erro']}")
        raise SystemExit(1)
    print(f"✅ Supabase: {d['url']}")
    for s in d["slots"]:
        marca = "✅" if s["concluida"] else ("⚪" if s["vazio"] else "🔵")
        rot = f" ({s['rotulo']})" if s["rotulo"] else ""
        print(f"  {marca} Slot {s['slot']}{rot}: {s['pedidos']} pedido(s), "
              f"{s['total_fases']}/{TOTAL_FASES} fases")
