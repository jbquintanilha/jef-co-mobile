# ==============================================================================
# NOME DO SCRIPT: core_scanner_foco.py
# DESCRICAO: Mantem o cursor no campo de bipagem do Scanner + barcode de comando
# FUNCAO: Operador bipa um codigo NA TELA e o foco volta, sem tocar no mouse
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 02/09/2026
# AUTOR: Terminador (Claude) / J&F Co.
# ==============================================================================
"""Foco persistente no campo de bipagem.

PROBLEMA REAL (Jota, 02/09): durante a expedicao o cursor sai do campo de
bipagem sozinho -- um rerun do Streamlit, a camera re-armando, um clique
qualquer. A pistola entao digita no vazio. O operador precisa largar o
pacote, ir ate' o PC, achar a janela e clicar no campo.

SOLUCAO EM DUAS CAMADAS:

1. GUARDA AUTOMATICA (resolve sozinho na maioria das vezes)
   Um observador devolve o foco ao campo quando ele se perde para o `body`
   -- que e' exatamente o que acontece num rerun. NAO rouba o foco se o
   operador esta' digitando em outro campo de proposito (busca parcial,
   observacao): so' age quando ninguem tem o foco.

2. BARCODE DE COMANDO (a rede de seguranca que o Jota pediu)
   Barcode impresso na tela, ao lado da ficha. Se a guarda falhar, o
   operador bipa a TELA com a mesma pistola e o foco volta. Zero mouse.

   ⚠️ O barcode e' `*FOCO*` em Code 39 GROSSO: leitor a laser lendo de um
   monitor perde barra fina no pixel do LCD. Codigo curto + barra larga.

O comando e' interceptado no proprio JS (o texto "FOCO" chega no campo,
o script reconhece, limpa e devolve o foco) -- nao chega a virar um
rerun do Streamlit, entao e' instantaneo.
"""

import json

import streamlit.components.v1 as components

from core_barcode_svg import code39_svg

# aria-label do campo alvo -- e' o rotulo que o Streamlit poe no <input>
# a partir do label do st.text_input, mesmo com label_visibility="collapsed".
ALVO_ARIA = "Código da etiqueta"

# Palavras que, se bipadas, valem como comando (nunca como codigo de pedido).
CMD_FOCO = "FOCO"


def barcode_comando_svg(dado: str = CMD_FOCO) -> str:
    """SVG do barcode de comando, dimensionado para leitura em tela."""
    # Altura generosa: o leitor da bancada e' apontado de longe, com a mao
    # ocupada. Barra alta perdoa mira torta -- o feixe atravessa o codigo
    # mesmo fora do eixo.
    return code39_svg(dado, altura=110, estreita=3, razao=3, legenda=True)


def injetar_guarda_foco(alvo_aria: str = ALVO_ARIA,
                        comando: str = CMD_FOCO) -> None:
    """Injeta o JS que mantem/recupera o foco. Chamar uma vez por render."""
    components.html(
        f"""
        <script>
        (function () {{
          const doc = window.parent.document;
          const ALVO = {json.dumps(alvo_aria)};
          const CMD  = {json.dumps(comando)};

          // ⚠️ GUARD DE INSTANCIA UNICA.
          // Cada rerun do Streamlit injeta um iframe NOVO rodando este mesmo
          // script -- e o iframe velho continua vivo com seus intervals e
          // observers. Sem este guard acumulam varias guardas, cada uma com
          // sua propria variavel de tregua: a instancia velha (tregua=0)
          // roubava o cursor de quem estava digitando na busca. Diagnosticado
          // no navegador em 02/09 (o stack apontou o MutationObserver).
          // Heartbeat em vez de flag simples: se o iframe dono for descartado
          // num rerun, ele para de renovar e a proxima instancia assume.
          // Sem isso a guarda morreria junto com o iframe que a criou.
          const AGORA = Date.now();
          if (doc.__jf_foco_hb && (AGORA - doc.__jf_foco_hb) < 4000) return;
          doc.__jf_foco_hb = AGORA;
          setInterval(function () {{ doc.__jf_foco_hb = Date.now(); }}, 1000);

          function campo() {{
            return doc.querySelector('input[aria-label="' + ALVO + '"]');
          }}

          function focar() {{
            const el = campo();
            if (!el) return false;
            el.focus();
            try {{ el.setSelectionRange(el.value.length, el.value.length); }}
            catch (e) {{}}
            return true;
          }}

          // ---- 1. GUARDA: devolve o foco quando ele cai no vazio ----
          // Só age se NINGUEM tem foco (body/null). Se o operador clicou em
          // outro input de proposito, nao roubamos o cursor dele.
          function ninguemFocado() {{
            const a = doc.activeElement;
            if (!a) return true;
            const tag = (a.tagName || '').toUpperCase();
            return tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT';
          }}

          // ⚠️ Tregua: quando o operador clica em OUTRO campo de proposito
          // (busca parcial, observacao), a guarda cala a boca por um tempo.
          // Sem isso ela rouba o cursor no meio da digitacao -- um rerun do
          // Streamlit troca o DOM, o observer dispara e o texto vai pro
          // campo errado. Medido em teste 02/09.
          // Mora no document PAI: sobrevive a troca de iframe dono, senao a
          // tregua zeraria toda vez que a guarda mudasse de instancia.
          function armarTregua() {{ doc.__jf_tregua = Date.now() + 15000; }}
          function emTregua() {{ return Date.now() < (doc.__jf_tregua || 0); }}

          function ehOutroCampo(el) {{
            if (!el || el === campo()) return false;
            const tag = (el.tagName || '').toUpperCase();
            return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
          }}

          // mousedown vem ANTES do focus: arma a tregua ja' no aperto do
          // botao, senao a guarda rouba o cursor no mesmo instante em que
          // o operador clica no outro campo (medido em teste 02/09).
          doc.addEventListener('mousedown', function (ev) {{
            if (ehOutroCampo(ev.target)) armarTregua();
          }}, true);

          doc.addEventListener('focusin', function (ev) {{
            if (ehOutroCampo(ev.target)) armarTregua();
          }}, true);

          doc.addEventListener('keydown', function () {{
            if (!ninguemFocado() && doc.activeElement !== campo()) {{
              armarTregua();
            }}
          }}, true);

          function guarda() {{
            if (!emTregua() && ninguemFocado()) focar();
          }}

          // Rerun do Streamlit troca o DOM inteiro: o observer repoe o foco
          // assim que o campo novo aparece.
          try {{
            new MutationObserver(guarda).observe(
              doc.body, {{ childList: true, subtree: true }});
          }} catch (e) {{}}

          setInterval(guarda, 1200);
          setTimeout(focar, 150);
          doc.addEventListener('click', function (ev) {{
            if (ehOutroCampo(ev.target)) return;   // clicou noutro campo: deixa
            setTimeout(guarda, 400);
          }});

          // ---- 2. BARCODE DE COMANDO: bipar a tela devolve o foco ----
          // A pistola digita "FOCO" onde quer que o cursor esteja e manda
          // Enter. Interceptamos ANTES do Enter virar submit: limpa o campo
          // e joga o cursor no lugar certo.
          if (!doc.__jf_foco_hook) {{
            doc.__jf_foco_hook = true;
            let buf = '';
            let ultimo = 0;

            doc.addEventListener('keydown', function (ev) {{
              const agora = Date.now();
              // Pistola digita rapido (<80ms entre teclas). Digitacao humana
              // e' mais lenta -- assim "FOCO" datilografado nao dispara.
              if (agora - ultimo > 300) buf = '';
              ultimo = agora;

              if (ev.key && ev.key.length === 1) {{
                buf = (buf + ev.key).toUpperCase().slice(-12);
                return;
              }}

              if (ev.key === 'Enter' && buf.indexOf(CMD) !== -1) {{
                ev.preventDefault();
                ev.stopPropagation();
                buf = '';
                const alvo = doc.activeElement;
                // Apaga o "FOCO" que a pistola acabou de digitar no campo,
                // senao ele seguiria como se fosse um codigo de pedido.
                if (alvo && 'value' in alvo) {{
                  const setter = Object.getOwnPropertyDescriptor(
                    window.parent.HTMLInputElement.prototype, 'value').set;
                  setter.call(alvo, '');
                  alvo.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
                // O comando e' explicito: cancela a tregua na hora, senao
                // bipar a tela nao adiantaria justamente quando o cursor
                // esta' preso em outro campo.
                doc.__jf_tregua = 0;
                setTimeout(focar, 60);
              }}
            }}, true);
          }}
        }})();
        </script>
        """,
        height=0,
    )
