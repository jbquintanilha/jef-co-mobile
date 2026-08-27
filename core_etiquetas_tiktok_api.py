# ==============================================================================
# NOME DO SCRIPT: core_etiquetas_tiktok_api.py
# DESCRICAO: Baixa etiquetas de envio do TikTok Shop direto pela API oficial
# FUNCAO: Substituir o fluxo do Olist que abre 1 pop-up por etiqueta (14 pedidos
#         = 14 pop-ups bloqueados pelo Chrome). Aqui: 1 chamada por pacote,
#         download silencioso, tudo unificado num PDF unico pronto para a LABEL 2.
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# REF: partner.tiktokshop.com/docv2/page/get-package-shipping-document-202309
#      Escopo exigido: seller.fulfillment.basic (ja ativo no app J&F)
# ==============================================================================
"""
Fluxo:
    1. listar_pacotes_a_enviar()  -> pega os pacotes prontos para despacho
    2. url_etiqueta(package_id)   -> pega a URL do PDF de cada etiqueta
    3. baixar_etiquetas(...)      -> baixa todos e junta num PDF unico

Uso rapido:
    from core_etiquetas_tiktok_api import baixar_etiquetas
    res = baixar_etiquetas()          # todos os pacotes pendentes
    print(res["pdf"], res["total"])

Pela linha de comando:
    python core_etiquetas_tiktok_api.py            # baixa pendentes
    python core_etiquetas_tiktok_api.py --listar   # so lista, nao baixa
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)

log = logging.getLogger(__name__)

BASE = "https://api.tiktok-shops.com"
APP_KEY = os.getenv("TIKTOK_APP_KEY", "")
APP_SECRET = os.getenv("TIKTOK_APP_SECRET", "")
ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "")
SHOP_CIPHER = os.getenv("TIKTOK_SHOP_CIPHER", "")

# Pasta de saida: Downloads com timestamp (padrao da casa para export final)
PASTA_SAIDA = Path(os.path.expanduser("~")) / "Downloads"

# Downloads simultaneos. 6 acelera ~4x sem estourar o rate limit do TikTok.
MAX_PARALELO = 6


class TikTokEtiquetaError(RuntimeError):
    """Falha ao falar com a API de fulfillment do TikTok."""


# --------------------------------------------------------------------------- #
# Assinatura HMAC (mesmo esquema ja validado em scratch/verificar_logistica_*)
# --------------------------------------------------------------------------- #
def _assinar(path: str, qp: dict, body_str: str = "") -> str:
    chaves = sorted(k for k in qp if k not in ("sign", "access_token"))
    base = (
        f"{APP_SECRET}{path}"
        f"{''.join(f'{k}{qp[k]}' for k in chaves)}"
        f"{body_str}{APP_SECRET}"
    )
    return hmac.new(APP_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()


def _qp_base(extra: dict | None = None) -> dict:
    qp = {
        "app_key": APP_KEY,
        "access_token": ACCESS_TOKEN,
        "shop_cipher": SHOP_CIPHER,
        "timestamp": str(int(time.time())),
    }
    if extra:
        qp.update(extra)
    return qp


def _get(path: str, params: dict | None = None) -> dict:
    qp = _qp_base(params)
    qp["sign"] = _assinar(path, qp)
    r = requests.get(
        f"{BASE}{path}",
        params=qp,
        headers={"x-tts-access-token": ACCESS_TOKEN},
        timeout=30,
    )
    try:
        return r.json()
    except Exception as exc:  # resposta nao-JSON = erro de infra
        raise TikTokEtiquetaError(f"Resposta invalida em {path}: {r.text[:200]}") from exc


def _post(path: str, params: dict | None = None, body: dict | None = None) -> dict:
    body_str = json.dumps(body or {}, separators=(",", ":"))
    qp = _qp_base(params)
    qp["sign"] = _assinar(path, qp, body_str)
    r = requests.post(
        f"{BASE}{path}",
        params=qp,
        headers={
            "Content-Type": "application/json",
            "x-tts-access-token": ACCESS_TOKEN,
        },
        data=body_str,
        timeout=30,
    )
    try:
        return r.json()
    except Exception as exc:
        raise TikTokEtiquetaError(f"Resposta invalida em {path}: {r.text[:200]}") from exc


def _checar_credenciais() -> None:
    faltando = [
        nome
        for nome, val in (
            ("TIKTOK_APP_KEY", APP_KEY),
            ("TIKTOK_APP_SECRET", APP_SECRET),
            ("TIKTOK_ACCESS_TOKEN", ACCESS_TOKEN),
            ("TIKTOK_SHOP_CIPHER", SHOP_CIPHER),
        )
        if not val
    ]
    if faltando:
        raise TikTokEtiquetaError(
            "Credenciais ausentes no .env: " + ", ".join(faltando)
        )


# --------------------------------------------------------------------------- #
# 1. Pacotes prontos para despacho
# --------------------------------------------------------------------------- #
# Status com etiqueta imprimivel. Depois que o pacote e' coletado o TikTok
# recusa o documento com: "Documents couldn't be printed after the package
# has been pickup" (code 21042102). Medido em producao 2026-08-16.
STATUS_IMPRIMIVEL = {"PROCESSING"}


def listar_pacotes_a_enviar(
    max_paginas: int = 10,
    *,
    somente_imprimivel: bool = True,
) -> list[dict[str, Any]]:
    """Pacotes que ja tem etiqueta emitida e aguardam despacho.

    somente_imprimivel=True (padrao) devolve so os PROCESSING — os unicos que
    ainda aceitam impressao de etiqueta. FULFILLING/COMPLETED ja foram
    coletados; CANCELLED nao interessa.
    """
    _checar_credenciais()
    path = "/fulfillment/202309/packages/search"
    pacotes: list[dict[str, Any]] = []
    cursor = ""

    for _ in range(max_paginas):
        params = {"page_size": "50"}
        if cursor:
            params["page_token"] = cursor
        resp = _post(path, params=params, body={})

        if resp.get("code") not in (0, None):
            raise TikTokEtiquetaError(
                f"search falhou: code={resp.get('code')} msg={resp.get('message')}"
            )

        data = resp.get("data") or {}
        lote = data.get("packages") or []
        pacotes.extend(lote)

        cursor = data.get("next_page_token") or ""
        if not cursor or not lote:
            break

    if somente_imprimivel:
        pacotes = [p for p in pacotes if p.get("status") in STATUS_IMPRIMIVEL]

    return pacotes


# --------------------------------------------------------------------------- #
# 2. URL da etiqueta de um pacote
# --------------------------------------------------------------------------- #
def url_etiqueta(
    package_id: str,
    *,
    tipo: str = "SHIPPING_LABEL",
    formato: str = "PDF",
) -> str | None:
    """URL do PDF da etiqueta. None se o pacote nao tiver documento disponivel.

    tipo:    SHIPPING_LABEL | PACKING_SLIP | SHIPPING_LABEL_AND_PACKING_SLIP
    formato: PDF (padrao)
    """
    _checar_credenciais()
    path = f"/fulfillment/202309/packages/{package_id}/shipping_documents"
    resp = _get(path, {"document_type": tipo, "document_size": "A6", "document_format": formato})

    if resp.get("code") not in (0, None):
        log.warning(
            "Etiqueta indisponivel para %s: code=%s msg=%s",
            package_id, resp.get("code"), resp.get("message"),
        )
        return None

    return (resp.get("data") or {}).get("doc_url")


# --------------------------------------------------------------------------- #
# 3. Baixar e unificar
# --------------------------------------------------------------------------- #
def baixar_etiquetas(
    package_ids: list[str] | None = None,
    *,
    saida: str | Path | None = None,
    unificar: bool = True,
) -> dict[str, Any]:
    """Baixa as etiquetas e devolve um PDF unico pronto para impressao.

    package_ids=None  -> busca sozinho os pacotes pendentes de despacho.

    Retorna:
        {
          "pdf": caminho do PDF unificado (ou None se unificar=False),
          "total": quantas etiquetas entraram,
          "arquivos": [caminhos individuais],
          "falhas": [(package_id, motivo)],
        }
    """
    _checar_credenciais()

    if package_ids is None:
        pacotes = listar_pacotes_a_enviar()
        package_ids = [
            str(p.get("id") or p.get("package_id"))
            for p in pacotes
            if (p.get("id") or p.get("package_id"))
        ]

    if not package_ids:
        return {"pdf": None, "total": 0, "arquivos": [], "falhas": [],
                "aviso": "Nenhum pacote pendente de despacho no TikTok Shop."}

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    tmp_dir = PASTA_SAIDA / f"_tiktok_etiquetas_{datetime.now():%Y%m%d_%H%M%S}"
    tmp_dir.mkdir(exist_ok=True)

    arquivos: list[Path] = []
    falhas: list[tuple[str, str]] = []

    # ⚡ Em paralelo: cada pacote faz 2 idas a' rede (pegar a URL assinada +
    # baixar o PDF), quase tudo espera. Sequencial, 15 pacotes levavam ~1min.
    def _uma(pid: str) -> tuple[str, bytes | None, str | None]:
        try:
            url = url_etiqueta(pid)
            if not url:
                return pid, None, "sem documento disponivel"
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            return pid, resp.content, None
        except Exception as exc:
            return pid, None, f"{type(exc).__name__}: {exc}"[:120]

    with ThreadPoolExecutor(max_workers=min(MAX_PARALELO, len(package_ids))) as executor:
        for pid, conteudo, erro in executor.map(_uma, package_ids):
            if erro:
                falhas.append((pid, erro))
            elif not conteudo:
                falhas.append((pid, "documento vazio"))
            else:
                destino = tmp_dir / f"{pid}.pdf"
                destino.write_bytes(conteudo)
                arquivos.append(destino)

    # Mantem a etiqueta na mesma ordem dos pacotes pedidos
    ordem = {pid: i for i, pid in enumerate(package_ids)}
    arquivos.sort(key=lambda p: ordem.get(p.stem, 999))

    resultado: dict[str, Any] = {
        "pdf": None,
        "total": len(arquivos),
        "arquivos": [str(a) for a in arquivos],
        "falhas": falhas,
    }

    if unificar and arquivos:
        resultado["pdf"] = _unificar_pdfs(
            arquivos,
            saida or PASTA_SAIDA / f"etiquetas_tiktok_{datetime.now():%Y%m%d_%H%M}.pdf",
        )

    return resultado


def _unificar_pdfs(arquivos: list[Path], saida: str | Path) -> str:
    """Junta os PDFs na ordem recebida. Usa pypdf; cai para PyPDF2 se preciso."""
    try:
        from pypdf import PdfWriter
    except ImportError:  # instalacoes antigas
        from PyPDF2 import PdfWriter  # type: ignore

    writer = PdfWriter()
    for arq in arquivos:
        writer.append(str(arq))

    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with open(saida, "wb") as fh:
        writer.write(fh)
    return str(saida)


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--listar" in sys.argv:
        pcts = listar_pacotes_a_enviar()
        print(f"Pacotes pendentes: {len(pcts)}")
        for p in pcts[:20]:
            print("  ", p.get("id") or p.get("package_id"), "|", p.get("status", "?"))
    else:
        res = baixar_etiquetas()
        if res.get("aviso"):
            print(res["aviso"])
        else:
            print(f"Etiquetas baixadas: {res['total']}")
            if res["pdf"]:
                print(f"PDF unificado: {res['pdf']}")
            for pid, motivo in res["falhas"]:
                print(f"  FALHA {pid}: {motivo}")
