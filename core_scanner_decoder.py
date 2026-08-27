# ==============================================================================
# NOME DO SCRIPT: core_scanner_decoder.py
# DESCRICAO: Decodificador de QR/barcode do Scanner de Conferencia J&F Co.
#            Converte os bytes de uma imagem (camera/upload) em texto.
# AUTOR: Conselho J&F Co. - Roo Code (sub-gerente operacional)
# VERSAO: 1.0
# DATA: 2026-08-02
# STATUS: Operacional
# ==============================================================================
"""Decodificador de QR/barcode a partir de bytes de imagem.

Motores (em ordem de tentativa):
  1. zxing-cpp — PRINCIPAL. QR + Code128 + EAN + DataMatrix, wheel autocontido
     (sem DLL externa). Unico que le a etiqueta TikTok/Correios.
  1b. pyzbar — fallback. QUEBRADO neste ambiente (libzbar-64.dll exige
     MSVCR120.dll / VC++ 2013, ausente). Mantido caso o runtime seja instalado.
  2. OpenCV ``QRCodeDetector`` — QR (usar detectAndDecodeMulti, nunca o single:
     etiqueta com varios QRs quebra o detector single em imagem grande).
  3. OpenCV ``barcode.BarcodeDetector`` — codigos 1D. Na pratica nao leu o
     Code128 das etiquetas testadas; fica so como ultima tentativa.

Cobertura validada em 2026-08-02 contra 3 etiquetas reais (Shopee, TikTok,
lista de pedidos Olist), de 150 a 800 DPI.

Uso:
    from core_scanner_decoder import decodificar_imagem
    texto = decodificar_imagem(foto_bytes)
"""

from __future__ import annotations

import io
import logging
import re

log = logging.getLogger("core_scanner_decoder")

# Rastreio dos Correios: 2 letras + 9 digitos + 2 letras (ex: AP296430628BR).
_RE_RASTREIO_CORREIOS = re.compile(r"\b([A-Z]{2}\d{9}[A-Z]{2})\b")

# Chave de acesso da NF-e: 44 digitos. Aparece no Code128 do DANFE
# simplificado da etiqueta -- NAO serve pra identificar o pedido, e' preciso
# descartar senao o scanner "acha" que leu algo util e nao resolve nada.
_RE_CHAVE_NFE = re.compile(r"^\d{44}$")

# Rastreio Shopee: BR + 12 alfanumericos (ex: BR264884133776V, BR2636267229403).
_RE_RASTREIO_SHOPEE = re.compile(r"\b(BR[0-9A-Z]{13})\b")

# Sufixo interno da Shopee que vem COLADO no rastreio quando a pistola le dois
# codigos de uma vez (ex: BR265271600891D + SPXLM16252909 = 28 chars). Achado
# em 2026-08-09: 2 registros da base ja estavam contaminados assim.
_RE_SUFIXO_SPX = re.compile(r"SPX[A-Z]{2}\d+$")

# CEP isolado (8 digitos) -- a etiqueta dos Correios traz um code128 so com o
# CEP do destinatario. Nunca identifica pedido.
_RE_CEP = re.compile(r"^\d{8}$")


def sanitizar_codigo(bruto: str) -> str:
    """Limpa uma leitura, descartando o que comprovadamente NAO e' pedido.

    A pistola le o que estiver no campo de visao e digita tudo junto -- sem
    isso, o operador precisa tapar com a mao os codigos que nao quer (relato do
    Comandante, 2026-08-09).

    **Estrategia: LISTA NEGRA, nao lista branca** (decisao do Comandante,
    2026-08-09). Filtrar por "so aceito o formato que conheco" quebra calado
    quando entra transportadora nova ou o marketplace muda o padrao do rastreio
    -- e a falha seria invisivel: o codigo bom seria descartado como lixo.
    Aqui e' o contrario: descarta so o que sabemos ser inutil (chave de NF-e,
    CEP) e deixa passar o desconhecido. Se nao resolver, o scanner diz
    "nao encontrado" -- falha visivel, que o operador entende e reporta.

      * ``33290695746398...`` (44 dig)   -> ``""``  (chave NF-e: descarta)
      * ``30644340``                     -> ``""``  (CEP: descarta)
      * ``BR265271600891DSPXLM16252909`` -> ``BR265271600891D`` (tira SPX colado)
      * payload do DataMatrix            -> rastreio de dentro dele
      * ``XX123456789YY`` de canal novo  -> passa inteiro (tenta resolver)

    Devolve "" so quando o codigo e' reconhecidamente inutil.
    """
    if not bruto:
        return ""
    s = re.sub(r"\s+", "", str(bruto)).strip().upper()
    if not s:
        return ""

    # ---- LISTA NEGRA: descarta apenas o que comprovadamente não identifica pedido ----
    if _RE_CEP.match(s):
        return ""          # CEP do destinatario (8 digitos)

    # ---- LIMPEZA: leitura suja com dois codigos colados ----
    # Achado 2026-08-09: 2 registros da base tinham rastreio Shopee + sufixo
    # interno SPX grudado (28 chars). O sufixo e' ruido, o rastreio e' valido.
    s = _RE_SUFIXO_SPX.sub("", s) or s

    # ---- EXTRACAO: rastreio embutido em payload maior (DataMatrix) ----
    # So entra aqui se a string for claramente um envelope (bem maior que um
    # rastreio); nao mexe em codigo curto de formato desconhecido.
    if len(s) > 30:
        m = _RE_RASTREIO_CORREIOS.search(s) or _RE_RASTREIO_SHOPEE.search(s)
        if m:
            return m.group(1)

    # Qualquer outra coisa passa como veio -- inclusive formato que ainda nao
    # conhecemos. O resolver tenta por tracking e por numero de pedido.
    return s


def _escolher_melhor(achados: list[tuple[str, str]]) -> str | None:
    """Escolhe o codigo mais util entre os varios lidos numa mesma etiqueta.

    Uma etiqueta traz varios codigos ao mesmo tempo (a da Shopee tem 5, a do
    TikTok tem 4). Devolver "o primeiro que achou" e' loteria: pode vir a chave
    da NF-e (44 digitos, inutil pra achar o pedido) ou o DataMatrix cru dos
    Correios (string longa com o rastreio enterrado no meio).

    Ordem de preferencia, validada 2026-08-02 com as 3 etiquetas reais:
      1. Rastreio dos Correios isolado (AP296430628BR) -- casa direto no indice.
      2. Rastreio extraido de dentro de um payload maior (o DataMatrix do
         TikTok carrega o rastreio embutido).
      3. Qualquer codigo curto que nao seja chave de NF-e (n do pedido Shopee).
      4. Ultimo recurso: o primeiro achado, seja la o que for.
    """
    if not achados:
        return None

    textos = [t for t, _ in achados]

    # 1. rastreio isolado
    for t in textos:
        if _RE_RASTREIO_CORREIOS.fullmatch(t):
            return t

    # 2. rastreio embutido num payload maior (DataMatrix dos Correios)
    for t in textos:
        m = _RE_RASTREIO_CORREIOS.search(t)
        if m:
            return m.group(1)

    # 3. codigo curto que nao seja chave de NF-e
    for t in sorted(textos, key=len):
        if not _RE_CHAVE_NFE.match(t) and len(t) <= 40:
            # passa pela sanitizacao pra tirar sufixo colado (SPX) e afins
            return sanitizar_codigo(t) or t

    return textos[0]


def decodificar_imagem(bytes_img: bytes) -> str | None:
    """Decodifica QR/barcode de uma imagem (bytes) e retorna o texto (ou None)."""
    if not bytes_img:
        return None
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(bytes_img)).convert("RGB")
    except Exception:
        return None

    try:
        import cv2
        import numpy as np
    except Exception:
        return None

    arr = np.array(img)

    # Amostras a testar: original, grayscale e upscale (QR/barcode pequenos na foto).
    amostras = [arr, cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)]
    h, w = arr.shape[:2]
    if min(h, w) < 300:
        escala = max(2.0, 600.0 / max(1, min(h, w)))
        arr_up = cv2.resize(arr, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
        amostras.append(arr_up)

    # DOWNSCALE para fotos grandes de celular. Validado 2026-08-02 com etiqueta
    # Shopee real: a 600 DPI (7017x4959) o detector falha em qualquer modo, mas
    # reduzindo a imagem le normalmente. Camera de celular moderno (12MP+) cai
    # exatamente nessa faixa -- sem isso o scanner falharia justo no uso real.
    lado_maior = max(h, w)
    for alvo in (2500, 1600):
        if lado_maior > alvo:
            f = alvo / lado_maior
            amostras.append(
                cv2.resize(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY), None,
                           fx=f, fy=f, interpolation=cv2.INTER_AREA)
            )

    # 1. zxing-cpp — motor PRINCIPAL. Le QR, Code128, EAN e DataMatrix, e vem
    # como wheel autocontido (sem DLL externa).
    # Por que ele e nao o pyzbar: o pyzbar deste ambiente esta QUEBRADO --
    # a libzbar-64.dll do wheel exige MSVCR120.dll (VC++ 2013), ausente neste
    # Windows (so ha o 140/2015+). O import falhava silencioso no `except`,
    # entao na pratica NENHUM Code128 era lido -- e e' justamente o formato do
    # rastreio dos Correios/TikTok. Validado 2026-08-02 com as 3 etiquetas
    # reais: zxing le Shopee (QR+Code128) e TikTok (Code128 AP296430628BR +
    # DataMatrix), que o caminho antigo nao lia de jeito nenhum.
    try:
        import zxingcpp

        for a in amostras:
            try:
                achados = [
                    ((r.text or "").strip(), str(r.format))
                    for r in zxingcpp.read_barcodes(a)
                ]
            except Exception:
                continue
            achados = [(t, f) for t, f in achados if t]
            if achados:
                return _escolher_melhor(achados)
    except Exception:
        pass

    # 1b. pyzbar — fallback, so funciona se o VC++ 2013 estiver instalado.
    try:
        from pyzbar.pyzbar import decode as zb_decode

        for a in amostras:
            for d in zb_decode(a):
                t = (d.data or b"").decode("utf-8", errors="ignore").strip()
                if t:
                    return t
    except Exception:
        pass

    # 2. OpenCV QRCodeDetector
    # ATENCAO: usar detectAndDecodeMulti PRIMEIRO, nao detectAndDecode.
    # Validado com etiqueta Shopee real (2026-08-02): a etiqueta tem 3 QR codes
    # e o detector "single" falha silenciosamente quando a imagem e' grande --
    # lia a 200 DPI mas retornava vazio a 300/450 DPI. Como foto de celular
    # chega em resolucao alta, o single quebraria justamente no uso real.
    # O multi le em todos os DPIs testados. O single fica so como fallback.
    try:
        det_qr = cv2.QRCodeDetector()
        for a in amostras:
            try:
                ok, decoded, *_ = det_qr.detectAndDecodeMulti(a)
                if ok and decoded:
                    for d in decoded:
                        t = str(d).strip()
                        if t:
                            return t
            except Exception:
                pass
        # fallback: detector single (imagens pequenas com 1 QR so)
        for a in amostras:
            data, *_ = det_qr.detectAndDecode(a)
            if data:
                return data.strip()
    except Exception:
        pass

    # 3. OpenCV barcode (Code128/EAN/UPC — etiquetas logisticas)
    try:
        det_bc = cv2.barcode.BarcodeDetector()
        for a in amostras:
            ok, decoded, _ = det_bc.detectAndDecode(a)
            if ok and decoded:
                t = str(decoded[0]).strip() if isinstance(decoded, (list, tuple)) else str(decoded).strip()
                if t:
                    return t
    except Exception:
        pass

    return None
