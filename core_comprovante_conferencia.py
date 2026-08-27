# ==============================================================================
# NOME DO SCRIPT: core_comprovante_conferencia.py
# DESCRICAO: Gera comprovante de conferencia/despacho a partir do log de
#            bipagem — data e hora exatas de cada pedido conferido.
# FUNCAO: Defesa contra falsa denuncia de "nao enviei" enquanto a webcam nao
#         chega. Paliativo do M4 (indice de video) do plano de expedicao.
# AUTOR: Terminador (001) / J&F Co.
# VERSAO: 1.0 | DATA: 2026-08-11
# STATUS: Operacional
# ==============================================================================
"""Comprovante de conferencia — prova de que o pedido foi separado e bipado.

Contexto (2026-08-11): o Comandante recebeu mais 2 falsas denuncias de "nao
recebi / nao enviaram". A webcam que vai permitir gravacao em video chega em
3-4 dias. Ate la, o dado que JA EXISTE no banco resolve boa parte: cada
bipagem grava `conferido_em` com data e hora exatas (precisao de segundo).

Isso nao e' tao forte quanto video, mas e' registro de sistema com carimbo de
tempo -- serve para responder a plataforma com precisao ("pedido X conferido
em 10/08/2026 as 14:26:05") em vez de so afirmar que enviou.

Quando a webcam chegar, este mesmo indice vira a base do M4: basta somar
`video_arquivo` + `video_segundo` as colunas, e o comprovante passa a apontar
o trecho exato da gravacao.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import core_scanner_db as db

log = logging.getLogger("core_comprovante_conferencia")

RAIZ = Path(r"C:\JF_Automacoes")


def _fmt_br(iso: str) -> str:
    """'2026-08-10 14:26:05' -> '10/08/2026 as 14:26:05'."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d/%m/%Y às %H:%M:%S")
    except (ValueError, TypeError):
        return iso


def buscar(termo: str = "", *, dia: str = "", limite: int = 500) -> list[dict]:
    """Busca conferencias por tracking, numero do pedido ou dia.

    ``termo``: casa com tracking OU pedido_ecommerce (busca parcial).
    ``dia``: 'YYYY-MM-DD'. Sem termo e sem dia, devolve as mais recentes.
    """
    try:
        con = sqlite3.connect(db.DB_PATH)
        con.row_factory = sqlite3.Row
        sql = "SELECT * FROM conferencias WHERE 1=1"
        params: list = []
        if termo:
            t = db.normalizar_codigo(termo)
            sql += " AND (UPPER(tracking) LIKE ? OR UPPER(pedido_ecommerce) LIKE ?)"
            params += [f"%{t}%", f"%{t}%"]
        if dia:
            sql += " AND date(conferido_em) = ?"
            params.append(dia)
        sql += " ORDER BY conferido_em DESC LIMIT ?"
        params.append(int(limite))
        rows = con.execute(sql, params).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.error("Falha ao buscar conferencias: %s", e)
        return []


def _hms(segundo) -> str:
    """Segundos -> HH:MM:SS, para apontar o trecho exato da gravacao."""
    try:
        s = int(segundo)
    except (TypeError, ValueError):
        return "00:00:00"
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def texto_defesa(registro: dict) -> str:
    """Texto pronto para colar na resposta a plataforma/comprador.

    Escrito em tom formal e factual -- so afirma o que o sistema registrou,
    sem prometer nada que o dado nao sustenta.
    """
    quando = _fmt_br(registro.get("conferido_em", ""))
    tracking = registro.get("tracking") or "—"
    pedido = registro.get("pedido_ecommerce") or "—"
    sku = registro.get("sku_principal") or "—"
    canal = (registro.get("canal") or "").upper() or "—"

    linhas = [
        "COMPROVANTE DE CONFERÊNCIA E DESPACHO",
        "",
        f"Pedido: {pedido}",
        f"Canal: {canal}",
        f"Código de rastreio: {tracking}",
        f"Produto (SKU): {sku}",
        f"Conferido e despachado em: {quando}",
    ]

    nivel = registro.get("validacao_nivel") or ""
    if nivel and nivel not in ("visual", "pulado"):
        linhas.append(
            f"Conferência: item validado por leitura de código de barras "
            f"({nivel}), cruzando a etiqueta da peça com o SKU do pedido."
        )
    elif nivel == "visual":
        linhas.append("Conferência: item verificado visualmente na expedição.")

    # M4: prova em video. So aparece quando existe -- comprovante antigo,
    # gravado antes da webcam, sai exatamente como antes.
    video_arquivo = registro.get("video_arquivo") or ""
    video_segundo = registro.get("video_segundo")
    print_arquivo = registro.get("print_arquivo") or ""
    if video_arquivo and video_segundo is not None:
        linhas.append(
            f"Registro em vídeo: {video_arquivo}, aos "
            f"{_hms(video_segundo)} de gravação."
        )
    if print_arquivo:
        linhas.append(f"Imagem da etiqueta no momento do despacho: {print_arquivo}")

    linhas += [
        "",
        "Este pedido foi separado, conferido e despachado conforme registro "
        "de sistema com data e hora acima, gerado automaticamente no momento "
        "da conferência na expedição.",
    ]
    if video_arquivo:
        linhas.append(
            "A expedição é gravada em vídeo contínuo, com data e hora impressas "
            "em cada quadro. O trecho correspondente a este pedido pode ser "
            "disponibilizado mediante solicitação."
        )
    return "\n".join(linhas)


DIAS_RETENCAO = 30


def limpar_antigos(dias: int = DIAS_RETENCAO) -> int:
    """Apaga comprovantes com mais de ``dias``. Retorna quantos saíram.

    Decisao do Comandante (2026-08-11): guardar 30 dias e depois pode apagar.
    A janela de disputa nas plataformas costuma abrir bem antes disso, entao
    30 dias cobre o risco real sem deixar a base crescer para sempre.

    ATENCAO: e' diferente do `limpar_antigos` de core_scanner_populator, que
    limpa `rastreio_pedidos` em 3 dias. Aquele mexe na fila de expedicao (dado
    operacional, descartavel); este mexe no COMPROVANTE (prova de despacho).
    Nunca unificar os dois prazos.
    """
    try:
        con = sqlite3.connect(db.DB_PATH, timeout=30)
        cur = con.execute(
            "DELETE FROM conferencias "
            "WHERE conferido_em < datetime('now','localtime', ?)",
            (f"-{int(dias)} days",),
        )
        n = cur.rowcount or 0
        con.commit()
        con.close()
        if n:
            log.info("Comprovantes com mais de %s dias removidos: %s", dias, n)
        return n
    except Exception as e:
        log.error("Falha ao limpar comprovantes antigos: %s", e)
        return 0


def contar_por_idade() -> dict:
    """Quantos comprovantes existem e quantos ja passaram da retencao."""
    try:
        con = sqlite3.connect(db.DB_PATH)
        total = con.execute("SELECT COUNT(*) FROM conferencias").fetchone()[0]
        vencidos = con.execute(
            "SELECT COUNT(*) FROM conferencias "
            "WHERE conferido_em < datetime('now','localtime', ?)",
            (f"-{DIAS_RETENCAO} days",),
        ).fetchone()[0]
        con.close()
        return {"total": total, "vencidos": vencidos,
                "dias_retencao": DIAS_RETENCAO}
    except Exception as e:
        log.error("Falha ao contar comprovantes: %s", e)
        return {"total": 0, "vencidos": 0, "dias_retencao": DIAS_RETENCAO}


def exportar_csv(registros: list[dict], destino: str | None = None) -> str | None:
    """Exporta os registros para CSV (Downloads, com timestamp no nome)."""
    if not registros:
        return None
    import csv
    if destino is None:
        selo = datetime.now().strftime("%Y%m%d_%H%M")
        destino = str(Path.home() / "Downloads" / f"comprovante_conferencia_{selo}.csv")
    try:
        campos = ["conferido_em", "tracking", "pedido_ecommerce", "canal",
                  "sku_principal", "status", "validacao_nivel", "sku_validado"]
        with open(destino, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            for r in registros:
                w.writerow(r)
        return destino
    except Exception as e:
        log.error("Falha ao exportar CSV: %s", e)
        return None


if __name__ == "__main__":
    import sys
    termo = sys.argv[1] if len(sys.argv) > 1 else ""
    achados = buscar(termo, limite=10)
    print(f"{len(achados)} registro(s)\n")
    for r in achados:
        print(f"  {_fmt_br(r['conferido_em'])} | {r['tracking']} | "
              f"{(r.get('sku_principal') or '')[:32]}")
    if achados:
        print("\n--- texto de defesa (1o registro) ---")
        print(texto_defesa(achados[0]))
