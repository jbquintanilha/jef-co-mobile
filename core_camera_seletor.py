# ==============================================================================
# NOME DO SCRIPT: core_camera_seletor.py
# DESCRICAO: Divide as cameras por funcao — gravacao na USB, bipagem na do note
# FUNCAO: A webcam USB e' uma so'. Se a gravacao e a bipagem disputarem, o
#         navegador falha com "Could not start video source".
# STATUS: ATIVO
# VERSAO: 2.0
# DATA: 16/08/2026
# AUTOR: Terminador (001) / Claude
# ==============================================================================
"""
Medido no PC do Jota (2026-08-16):

    Integrated Camera   <- a da tela; era essa que o leitor abria
    WEB CAMER           <- a USB projetada na bancada, a correta

Causa: `scanner_camera_ao_vivo.py` pede so' `facingMode: environment`. No
celular isso acerta a traseira; no desktop o navegador ignora e devolve a
primeira da lista — que podia ser justamente a USB ocupada pela gravacao.

🔴 A v1 nao funcionou e o motivo importa: `components.html` cria um IFRAME
SANDBOX de origem diferente. Um script fora dele nao alcanca o <video> de
dentro (`contentDocument` bloqueado) — a troca nunca acontecia.

Solucao da v2: injetar o JS DENTRO do mesmo HTML do leitor, reescrevendo a
chamada `getUserMedia` para nascer na camera certa. Nada de cruzar iframe.

🔴 NAO alterar `scanner_camera_ao_vivo.py`: e' compartilhado com o Scanner da
pagina 14, que e' o backup. Aqui o HTML dele e' so' lido e adaptado.

No celular nada muda: o codigo detecta e mantem `facingMode`.

Uso:
    from core_camera_seletor import render_camera_bipagem
    render_camera_bipagem(chave_query="bip", botao_submit="Bipar")
"""

from __future__ import annotations
import core_env_loader

import logging
import re

log = logging.getLogger(__name__)

# 🔴 DIVISAO FIXA DE CAMERAS (Jota, 2026-08-16):
#
#     WEB CAMER (USB)      -> SEMPRE a gravacao de prova (fase 5)
#     Integrated Camera    -> SEMPRE a bipagem no PC (fase 6)
#     Camera do celular    -> bipagem pelo celular, nao disputa nada
#
# Por que: a webcam USB e' UMA so'. Enquanto o ffmpeg grava, o navegador nao
# consegue abri-la e falha com "Could not start video source". Separando por
# funcao, as duas rodam ao mesmo tempo sem nunca brigar.
#
# Por isso a BIPAGEM prefere a INTEGRADA — o inverso do que parecia certo:
# a integrada esta livre e fica virada para quem segura a etiqueta.
PISTAS_INTEGRADA = ["integrated", "built-in", "builtin", "internal", "embutida",
                    "frontal", "front", "hd webcam"]
PISTAS_USB = ["web camer", "usb", "external", "logitech", "microdia",
              "c920", "c270"]

# Substitui a chamada original por uma que escolhe o device antes de abrir.
_ESCOLHER_JS = """
      // ⚡ Bipagem no PC usa a camera do notebook; a USB fica com a gravacao.
      // No desktop `facingMode` nao decide nada — quem decide e' o deviceId.
      const _PISTAS_USB = __USB__;
      const _PISTAS_INT = __INTEGRADA__;
      const _ehCelular = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

      function _bate(label, pistas) {
        const l = (label || '').toLowerCase();
        return pistas.some(p => l.includes(p));
      }

      let _constraints = { video: { facingMode: { ideal: 'environment' },
                                    width: { ideal: 1280 }, height: { ideal: 720 } },
                           audio: false };

      if (!_ehCelular) {
        try {
          // Precisa de permissao concedida para os labels virem preenchidos
          const _tmp = await navigator.mediaDevices.getUserMedia({ video: true });
          _tmp.getTracks().forEach(t => t.stop());

          const _cams = (await navigator.mediaDevices.enumerateDevices())
                          .filter(d => d.kind === 'videoinput');

          // Bipagem no PC usa a INTEGRADA: a USB fica reservada a gravacao.
          let _alvo = _cams.find(c => _bate(c.label, _PISTAS_INT));
          if (!_alvo) _alvo = _cams.find(c => !_bate(c.label, _PISTAS_USB));

          if (_alvo) {
            _constraints = { video: { deviceId: { exact: _alvo.deviceId },
                                      width: { ideal: 1280 }, height: { ideal: 720 } },
                             audio: false };
            window.__jefCamEscolhida = _alvo.label || _alvo.deviceId;
          }
          window.__jefCams = _cams.map(c => c.label || '(sem label)');
        } catch (e) {
          console.warn('Selecao de camera falhou, usando a padrao:', e);
        }
      }

      const stream = await navigator.mediaDevices.getUserMedia(_constraints);
"""

# Mostra qual camera entrou, para dar para conferir na hora
_AVISO_JS = """
      try {
        const _t = stream.getVideoTracks()[0];
        const _nome = (_t && _t.label) || window.__jefCamEscolhida || 'câmera';
        const _cx = document.getElementById('jef-cam-atual');
        if (_cx) _cx.innerHTML = '📹 <b>' + _nome + '</b>';
      } catch (e) {}
"""


def _adaptar_html(html: str) -> tuple[str, bool]:
    """Troca o getUserMedia original pelo que escolhe a camera certa.

    Devolve (html_adaptado, deu_certo). Se o trecho esperado nao for
    encontrado — porque o Scanner mudou — devolve o HTML intacto e avisa,
    em vez de quebrar a leitura.
    """
    import json

    escolher = (_ESCOLHER_JS
                .replace("__USB__", json.dumps(PISTAS_USB))
                .replace("__INTEGRADA__", json.dumps(PISTAS_INTEGRADA)))

    # O original e': const stream = await navigator.mediaDevices.getUserMedia({...});
    padrao = re.compile(
        r"const stream = await navigator\.mediaDevices\.getUserMedia\(\s*\{.*?\}\s*\);",
        re.DOTALL,
    )

    novo, trocas = padrao.subn(lambda _m: escolher.strip(), html, count=1)
    if not trocas:
        return html, False

    # Depois de abrir, mostra o nome da camera ativa
    novo = novo.replace(
        "video.srcObject = stream;",
        "video.srcObject = stream;" + _AVISO_JS,
        1,
    )

    # Caixinha com o nome da camera, logo acima do video
    novo = novo.replace(
        '<div id="video-box">',
        '<div id="jef-cam-atual" style="font:12px sans-serif;color:#0a0;'
        'margin-bottom:6px">📹 abrindo câmera…</div>\n  <div id="video-box">',
        1,
    )
    return novo, True


def render_camera_bipagem(altura: int = 380, chave_query: str = "cod",
                          botao_submit: str = "Resolver") -> None:
    """Leitor de codigo na camera do notebook (a USB fica com a gravacao)."""
    import streamlit as st
    import streamlit.components.v1 as components

    import scanner_camera_ao_vivo as scam

    # Le o HTML do leitor original sem executar o render dele
    fonte = _extrair_html(scam)
    if fonte is None:
        st.caption("⚠️ Usando o leitor padrão (pode disputar a webcam USB).")
        scam.render_camera(altura=altura, chave_query=chave_query,
                           botao_submit=botao_submit)
        return

    html = (fonte.replace("__ALTURA__", str(altura))
                 .replace("__CHAVE__", chave_query)
                 .replace("__BTN_SUBMIT__", botao_submit)
                 .replace("__REARMAR__", "false"))

    html, ok = _adaptar_html(html)
    if not ok:
        log.warning("Trecho getUserMedia nao encontrado — leitor sem selecao de camera.")
        st.caption("⚠️ Seleção de câmera indisponível nesta versão do leitor.")

    components.html(html, height=altura + 170)


def _extrair_html(modulo) -> str | None:
    """Pega o HTML de dentro de `render_camera` sem renderizar.

    Le o codigo-fonte da funcao e recupera a string literal — assim o modulo
    do Scanner segue intocado.
    """
    import ast
    import inspect

    try:
        fonte = inspect.getsource(modulo.render_camera)
        arvore = ast.parse(fonte.lstrip())
        for no in ast.walk(arvore):
            if (isinstance(no, ast.Assign)
                    and isinstance(no.value, ast.Constant)
                    and isinstance(no.value.value, str)
                    and "video-box" in no.value.value):
                return no.value.value
    except Exception as exc:
        log.warning("Nao foi possivel extrair o HTML do leitor: %s", exc)
    return None
