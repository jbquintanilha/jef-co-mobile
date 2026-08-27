# ==============================================================================
# NOME DO SCRIPT: core_sync_expedicao.py
# DESCRICAO: Sincronizacao INCREMENTAL da expedicao — busca so' o que falta
# FUNCAO: Puxar 39 pedidos detalhados leva ~40s (a V3 do Olist exige 1 GET por
#         pedido + rate limit). Se 35 ja' estao no cache, so' os 4 novos
#         precisam de rede. Este motor faz essa conta.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Como funciona:

    1. LISTAR    e' barato — 1 chamada devolve os 100 pedidos resumidos
    2. COMPARAR  os IDs de la' com os que ja' estao no cache
    3. DETALHAR  so' os que faltam (1 GET por pedido, o caro)
    4. GRAVAR    cache atualizado + log do que entrou/saiu

O log fica em `local_db/cache_expedicao/_historico_sync.json` e guarda cada
rodada: quantos vieram do cache, quantos foram baixados, quais entraram e
quais sairam da fila.

⚠️ `reset=True` ignora o cache e rebaixa tudo — o botao vermelho da tela.
Existe porque cache pode ficar desatualizado de formas que a comparacao por ID
nao pega (item editado no Olist mantendo o mesmo ID).

Uso:
    from core_sync_expedicao import sincronizar, historico

    r = sincronizar([2])              # incremental (padrao)
    r = sincronizar([2], reset=True)  # zera e baixa tudo

    print(r["resumo"])
    # "39 pedidos: 35 do cache, 4 baixados (2 novos, 1 saiu) em 4.2s"

Linha de comando:
    python core_sync_expedicao.py            # incremental
    python core_sync_expedicao.py --reset    # baixa tudo
    python core_sync_expedicao.py --historico
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core_cache_expedicao as cache_mod


log = logging.getLogger(__name__)

ARQ_HISTORICO = cache_mod.PASTA / "_historico_sync.json"
MAX_HISTORICO = 200  # rodadas guardadas; o resto e' descartado


def _chave_pedidos(situacoes: list[int]) -> str:
    return "pedidos_sit" + "_".join(str(s) for s in sorted(situacoes))


def _id_do(pedido: dict[str, Any]) -> str:
    return str(pedido.get("id") or pedido.get("numeroPedido") or "")


def registrar_log(evento: dict[str, Any]) -> None:
    """Anexa uma rodada ao historico, mantendo as ultimas MAX_HISTORICO."""
    cache_mod.PASTA.mkdir(parents=True, exist_ok=True)

    historico: list[dict[str, Any]] = []
    if ARQ_HISTORICO.exists():
        try:
            historico = json.loads(ARQ_HISTORICO.read_text(encoding="utf-8"))
        except Exception:
            historico = []  # corrompido: recomeca em vez de travar

    evento["quando"] = time.strftime("%Y-%m-%d %H:%M:%S")
    historico.append(evento)

    ARQ_HISTORICO.write_text(
        json.dumps(historico[-MAX_HISTORICO:], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def historico(limite: int = 30) -> list[dict[str, Any]]:
    """Ultimas rodadas de sincronizacao, mais recente primeiro."""
    if not ARQ_HISTORICO.exists():
        return []
    try:
        return list(reversed(json.loads(ARQ_HISTORICO.read_text(encoding="utf-8"))))[:limite]
    except Exception:
        return []


def sincronizar(
    situacoes: list[int] | None = None,
    *,
    reset: bool = False,
    max_pedidos: int = 100,
    validade_dias: int = cache_mod.VALIDADE_DIAS,
) -> dict[str, Any]:
    """Traz os pedidos da fila baixando so' o que ainda nao esta' no cache.

    Args:
        situacoes: situacoes do Olist. Default [4] (Preparando envio) — a
            fila do que ainda NAO teve etiqueta emitida. A situacao 2
            ("Em separacao") acumula pedido velho e cancelado.
        reset: ignora o cache e rebaixa tudo (o botao vermelho).
        max_pedidos: teto do LISTAR. ⚠️ O Olist recusa acima de 100.

    Retorna:
        {"pedidos", "total", "do_cache", "baixados", "novos", "sairam",
         "segundos", "resumo", "reset"}
    """
    import core_separacao as cs
    from core_olist import OlistClient

    situacoes = situacoes or [4]
    max_pedidos = min(max_pedidos, 100)  # trava dura: a API rejeita >100
    chave = _chave_pedidos(situacoes)
    inicio = time.time()

    # ---- 1. O que ja' temos ------------------------------------------------ #
    if reset:
        cache_mod.invalidar(chave)
        guardados: dict[str, dict[str, Any]] = {}
    else:
        registro = cache_mod.ler(chave, validade_dias=validade_dias)
        guardados = {_id_do(p): p for p in (registro or {}).get("dados", [])}

    # ---- 2. LISTAR: barato, 1 chamada por situacao ------------------------- #
    # 🔴 OlistClient() autentica no construtor. Token morto estourava aqui e
    # derrubava a Esteira inteira com "invalid_grant" (incidente 23/08/2026),
    # mesmo havendo pedidos guardados. Como `guardados` ja' foi lido acima,
    # servimos o cache e avisamos — a bancada continua trabalhando.
    try:
        cli = OlistClient()
    except Exception as exc:
        if not cs._e_falha_de_token(exc):
            raise
        pedidos = list(guardados.values())
        log.warning("Token do Olist expirou — servindo %d pedido(s) do cache. "
                    "Rode: python scripts/olist_reauth.py", len(pedidos))
        return {
            "pedidos": pedidos,
            "total": len(pedidos),
            "do_cache": len(pedidos),
            "baixados": 0,
            "novos": [],
            "sairam": [],
            "falhas": ["token do Olist expirou"],
            "segundos": round(time.time() - inicio, 1),
            "reset": reset,
            "chave": chave,
            "ultimo_pedido": None,
            "ultimo_ecommerce": None,
            "ultimo_canal": None,
            "ultimo_data": None,
            "resumo": (f"⚠️ Token do Olist expirou — {len(pedidos)} pedido(s) "
                       f"do cache. Rode: python scripts/olist_reauth.py"),
            "token_expirado": True,
        }
    resumidos: list[dict[str, Any]] = []
    for sit in situacoes:
        try:
            lote = cli.listar_pedidos(situacao=sit, limit=max_pedidos) or []
            # Guarda de qual situacao o pedido veio: o detalhe as vezes volta
            # sem ela, e a bancada precisa saber se a etiqueta JA' saiu (7).
            for p in lote:
                p["_situacao_origem"] = sit
            resumidos.extend(lote)
        except Exception as exc:
            log.error("Falha ao listar situacao %s: %s", sit, exc)

    ids_agora = {_id_do(p) for p in resumidos if _id_do(p)}

    # ---- 3. DETALHAR so' o que falta: a parte cara ------------------------- #
    faltantes = [p for p in resumidos if _id_do(p) not in guardados]
    sairam = [i for i in guardados if i not in ids_agora]

    baixados: list[dict[str, Any]] = []
    falhas: list[str] = []

    for resumo in faltantes:
        pid = _id_do(resumo)
        try:
            det = cli.obter_pedido(resumo["id"])
            # `obter_pedido` devolve um dict NOVO: sem recopiar, o marcador
            # de origem se perde e o 🏷️ (etiqueta ja' emitida) nunca aparece.
            if isinstance(det, dict) and resumo.get("_situacao_origem"):
                det["_situacao_origem"] = resumo["_situacao_origem"]
            baixados.append(det)
            time.sleep(0.3)  # respeita o rate limit da V3
        except Exception as exc:
            log.warning("Falha ao detalhar %s: %s", pid, exc)
            falhas.append(pid)
            baixados.append(resumo)  # guarda o resumido: melhor que perder

    # ---- 4. Monta a fila final e grava ------------------------------------- #
    #
    # ⚠️ AUTOLIMPEZA (Jota, 25/08: "precisa se autolimpar verificando se ja'
    # mudou para status enviado ... nao preciso saber os q ja' foram").
    #
    # `ids_agora` e' a verdade do momento: sao os pedidos que AINDA estao nas
    # situacoes consultadas. Assim que o pedido vira Enviado (5) ou Entregue
    # (6), ele some dessa lista e o filtro abaixo o descarta do cache.
    # Nao adianta so' confiar no cache: ele guarda o pedido antigo com os
    # dados de quando entrou, e sem este corte a fila cresceria para sempre.
    atual = {**guardados, **{_id_do(p): p for p in baixados}}
    pedidos = [p for pid, p in atual.items() if pid in ids_agora]

    # Rede de seguranca: se o DETALHE do pedido ja' veio com situacao de
    # despachado, tira mesmo que a listagem ainda o mostre (a listagem do
    # Olist as vezes atrasa em relacao ao detalhe).
    _JA_FOI = {"5", "6", "9"}       # 5 Enviado · 6 Entregue · 9 Cancelado
    antes = len(pedidos)
    pedidos = [p for p in pedidos
               if str(p.get("situacao") or "") not in _JA_FOI]
    if len(pedidos) < antes:
        log.info("Autolimpeza: %d pedido(s) ja' despachado(s)/cancelado(s) "
                 "saíram da fila", antes - len(pedidos))

    cache_mod.gravar(chave, pedidos)

    segundos = round(time.time() - inicio, 1)
    do_cache = len(pedidos) - len(baixados)

    # ⚠️ O Olist NAO devolve os pedidos em ordem. Sem isto nao da' para saber
    # se a base esta' em dia — o Jota pediu para ver "o ultimo pedido
    # sincronizado" e comparar com a tela do Olist (2026-08-16).
    def _num(p: dict[str, Any]) -> int:
        try:
            return int(p.get("numeroPedido") or 0)
        except (TypeError, ValueError):
            return 0

    ultimo = max(pedidos, key=_num) if pedidos else None

    resultado = {
        "pedidos": pedidos,
        "total": len(pedidos),
        "do_cache": max(do_cache, 0),
        "baixados": len(baixados),
        "novos": [_id_do(p) for p in faltantes],
        "sairam": sairam,
        "falhas": falhas,
        "segundos": segundos,
        "reset": reset,
        "chave": chave,
        # Referencia visual para conferir contra a tela do Olist
        "ultimo_pedido": _num(ultimo) if ultimo else None,
        "ultimo_ecommerce": (
            (ultimo.get("ecommerce") or {}).get("numeroPedidoEcommerce")
            or ultimo.get("numeroOrdemCompra") if ultimo else None
        ),
        "ultimo_canal": (ultimo.get("ecommerce") or {}).get("nome") if ultimo else None,
        "ultimo_data": ultimo.get("data") if ultimo else None,
    }

    partes = [f"{len(pedidos)} pedidos"]
    if reset:
        partes.append(f"RESET — {len(baixados)} baixados")
    else:
        partes.append(f"{max(do_cache, 0)} do cache, {len(baixados)} baixados")
    if sairam:
        partes.append(f"{len(sairam)} saiu/sairam da fila")
    if falhas:
        partes.append(f"⚠️ {len(falhas)} falha(s)")
    if ultimo:
        partes.append(f"último: pedido {_num(ultimo)}")
    resultado["resumo"] = " · ".join(partes) + f" em {segundos}s"

    registrar_log({
        "situacoes": situacoes,
        "reset": reset,
        "total": len(pedidos),
        "do_cache": max(do_cache, 0),
        "baixados": len(baixados),
        "novos": len(faltantes),
        "sairam": len(sairam),
        "falhas": len(falhas),
        "segundos": segundos,
    })

    log.info(resultado["resumo"])
    return resultado


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--historico" in sys.argv:
        linhas = historico()
        if not linhas:
            print("Sem histórico de sincronização ainda.")
        for h in linhas:
            marca = "RESET " if h.get("reset") else ""
            print(f"  {h['quando']}  {marca}{h['total']:>3} peds  "
                  f"cache {h['do_cache']:>3}  baixou {h['baixados']:>3}  "
                  f"novos {h['novos']:>3}  saiu {h['sairam']:>3}  "
                  f"{h['segundos']:>5}s")
    else:
        r = sincronizar([2], reset="--reset" in sys.argv)
        print(r["resumo"])
