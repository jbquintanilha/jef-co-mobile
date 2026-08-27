# ==============================================================================
# NOME DO SCRIPT: core_cache_expedicao.py
# DESCRICAO: Cache local dos dados da expedicao — evita repuxar tudo das APIs
# FUNCAO: Puxar Olist + TikTok + Shopee inteiro a cada clique e' lento e queima
#         rate limit. Este cache guarda o resultado por 15 dias e serve do disco.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Guarda em `local_db/cache_expedicao/` — mesma pasta-mae do resto dos dados
locais (`cache_ia/`, `erp_jf_v2.db`), entao ja' esta' coberta pelo .gitignore
do projeto.

Validade padrao: **15 dias** (Jota, 2026-08-16 — "toda hora preciso puxar tudo,
e' impossivel"). Um lote de expedicao vive dias, nao minutos: repuxar a cada
clique so' gasta tempo e rate limit.

Uso:
    from core_cache_expedicao import cache

    # le do disco se estiver fresco; senao chama a funcao e grava
    pedidos = cache("pedidos_sit2", lambda: cs.obter_pedidos_pendentes([2]))

    # forcar atualizacao
    pedidos = cache("pedidos_sit2", buscar, forcar=True)

Linha de comando:
    python core_cache_expedicao.py          # lista o que esta' guardado
    python core_cache_expedicao.py --limpar # apaga o que venceu
    python core_cache_expedicao.py --zerar  # apaga tudo
"""

from __future__ import annotations
import core_env_loader

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

PASTA = Path(__file__).resolve().parent / "local_db" / "cache_expedicao"

# 15 dias — decisao do Jota. Lote de expedicao nao muda de minuto em minuto.
VALIDADE_DIAS = 15
SEGUNDOS_DIA = 86400


def _arquivo(chave: str) -> Path:
    """Caminho do arquivo de uma chave. Sanitiza para nao escapar da pasta."""
    limpo = "".join(c for c in str(chave) if c.isalnum() or c in "-_.")
    return PASTA / f"{limpo or 'sem_nome'}.json"


def ler(chave: str, *, validade_dias: int = VALIDADE_DIAS) -> dict[str, Any] | None:
    """Devolve o registro guardado se ainda estiver fresco, senao None."""
    caminho = _arquivo(chave)
    if not caminho.exists():
        return None

    try:
        registro = json.loads(caminho.read_text(encoding="utf-8"))
    except Exception as exc:
        # Arquivo corrompido nao pode derrubar a expedicao — trata como ausente
        log.warning("Cache '%s' ilegivel (%s) — sera' refeito.", chave, exc)
        return None

    idade = time.time() - registro.get("gravado_em", 0)
    if idade > validade_dias * SEGUNDOS_DIA:
        return None

    registro["idade_horas"] = round(idade / 3600, 1)
    return registro


def gravar(chave: str, dados: Any) -> Path:
    """Grava os dados com carimbo de tempo."""
    PASTA.mkdir(parents=True, exist_ok=True)
    caminho = _arquivo(chave)

    caminho.write_text(
        json.dumps(
            {"chave": chave, "gravado_em": time.time(),
             "gravado_iso": time.strftime("%Y-%m-%d %H:%M:%S"), "dados": dados},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return caminho


def cache(
    chave: str,
    buscar: Callable[[], Any],
    *,
    forcar: bool = False,
    validade_dias: int = VALIDADE_DIAS,
) -> Any:
    """Le do cache; se vencido ou ausente, chama `buscar()` e grava.

    ⚠️ Se `buscar()` falhar mas houver cache vencido no disco, devolve o
    vencido em vez de estourar. Dado velho e' melhor que expedicao parada —
    quem usa decide se confia (mesma politica de alarme-sem-bloqueio).
    """
    if not forcar:
        registro = ler(chave, validade_dias=validade_dias)
        if registro is not None:
            log.info("cache HIT '%s' (%.1fh)", chave, registro["idade_horas"])
            return registro["dados"]

    try:
        dados = buscar()
    except Exception as exc:
        antigo = _arquivo(chave)
        if antigo.exists():
            try:
                registro = json.loads(antigo.read_text(encoding="utf-8"))
                log.warning(
                    "Busca de '%s' falhou (%s) — usando cache VENCIDO de %s.",
                    chave, exc, registro.get("gravado_iso"),
                )
                return registro["dados"]
            except Exception:
                pass
        raise

    gravar(chave, dados)
    log.info("cache MISS '%s' — gravado.", chave)
    return dados


def invalidar(chave: str) -> bool:
    """Apaga uma chave. True se existia."""
    caminho = _arquivo(chave)
    if caminho.exists():
        caminho.unlink()
        return True
    return False


def listar() -> list[dict[str, Any]]:
    """O que esta' guardado, do mais novo para o mais velho."""
    if not PASTA.is_dir():
        return []

    itens: list[dict[str, Any]] = []
    agora = time.time()

    for arq in PASTA.glob("*.json"):
        # `_historico_sync.json` mora na mesma pasta mas e' uma LISTA de
        # rodadas, nao um registro de cache — pular para nao quebrar aqui.
        if arq.name.startswith("_"):
            continue

        try:
            registro = json.loads(arq.read_text(encoding="utf-8"))
        except Exception:
            itens.append({"chave": arq.stem, "erro": "arquivo ilegivel",
                          "kb": round(arq.stat().st_size / 1024, 1)})
            continue

        if not isinstance(registro, dict):
            itens.append({"chave": arq.stem, "erro": "formato inesperado",
                          "kb": round(arq.stat().st_size / 1024, 1)})
            continue

        idade_h = (agora - registro.get("gravado_em", 0)) / 3600
        dados = registro.get("dados")
        itens.append({
            "chave": registro.get("chave", arq.stem),
            "gravado": registro.get("gravado_iso", "?"),
            "idade_horas": round(idade_h, 1),
            "vencido": idade_h > VALIDADE_DIAS * 24,
            "registros": len(dados) if isinstance(dados, (list, dict)) else 1,
            "kb": round(arq.stat().st_size / 1024, 1),
        })

    itens.sort(key=lambda x: x.get("idade_horas", 0))
    return itens


def limpar_vencidos(*, validade_dias: int = VALIDADE_DIAS) -> int:
    """Apaga o que passou da validade. Devolve quantos foram apagados."""
    if not PASTA.is_dir():
        return 0

    agora = time.time()
    apagados = 0

    for arq in PASTA.glob("*.json"):
        if arq.name.startswith("_"):     # historico nao e' cache
            continue

        try:
            registro = json.loads(arq.read_text(encoding="utf-8"))
            gravado = registro.get("gravado_em", 0) if isinstance(registro, dict) else 0
        except Exception:
            gravado = 0  # ilegivel: trata como velho

        if agora - gravado > validade_dias * SEGUNDOS_DIA:
            arq.unlink()
            apagados += 1

    return apagados


def zerar() -> int:
    """Apaga TUDO. Devolve quantos arquivos foram removidos."""
    if not PASTA.is_dir():
        return 0

    total = 0
    for arq in PASTA.glob("*.json"):
        if arq.name.startswith("_"):     # preserva o historico de sync
            continue
        arq.unlink()
        total += 1
    return total


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--zerar" in sys.argv:
        print(f"{zerar()} arquivo(s) apagado(s).")
    elif "--limpar" in sys.argv:
        print(f"{limpar_vencidos()} vencido(s) apagado(s).")
    else:
        itens = listar()
        if not itens:
            print(f"Cache vazio. Pasta: {PASTA}")
        else:
            print(f"Cache em {PASTA} — validade {VALIDADE_DIAS} dias\n")
            for i in itens:
                if i.get("erro"):
                    print(f"  ⚠️  {i['chave']:28s} {i['erro']}")
                    continue
                marca = "VENCIDO" if i["vencido"] else "ok"
                print(f"  {i['chave']:28s} {i['gravado']}  "
                      f"{i['idade_horas']:>6.1f}h  {i['registros']:>4} reg  "
                      f"{i['kb']:>7.1f} KB  {marca}")
