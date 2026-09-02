# ==============================================================================
# NOME DO SCRIPT: core_scanner_som.py
# DESCRICAO: Sinal sonoro de acerto/erro na bipagem do Scanner
# FUNCAO: Operador confirma a leitura de ouvido, sem tirar o olho do pacote
# STATUS: ATIVO
# VERSAO: 1.0
# DATA: 02/09/2026
# AUTOR: Terminador (Claude) / J&F Co.
# ==============================================================================
"""Bip de sucesso e de erro.

POR QUE: na bancada o operador esta' de maos ocupadas e olhando o pacote,
nao a tela. Sem som ele precisa parar e conferir visualmente se a leitura
pegou. O ouvido resolve isso sem interromper o movimento.

O som e' sintetizado no proprio navegador (Web Audio API) -- nao ha'
arquivo .mp3/.wav para carregar. Motivos:
  - o app roda tambem na nuvem (jef-co-mobile), onde asset externo e' mais
    uma coisa para faltar em silencio;
  - som gerado tem latencia zero: toca no mesmo instante da leitura.

TRES SINAIS, propositalmente distintos de ouvido:
  OK        2 bips curtos subindo (880 -> 1320 Hz)  -- pode seguir
  ERRO      1 bip longo grave (220 Hz)              -- para e confere
  ATENCAO   3 bips medios (600 Hz)                  -- multi-item/divergencia

⚠️ Navegador bloqueia audio antes do primeiro gesto do usuario. Como o
operador sempre clica ou bipa algo antes da primeira leitura, na pratica
o contexto ja' esta' liberado. Ainda assim o codigo tenta `resume()` no
AudioContext, senao o primeiro bip do dia sairia mudo.
"""

import json

import streamlit.components.v1 as components

OK = "ok"
ERRO = "erro"
ATENCAO = "atencao"

# (frequencia Hz, duracao s, atraso s) por sinal
_PADROES = {
    OK:      [(880, 0.09, 0.0), (1320, 0.11, 0.10)],
    ERRO:    [(220, 0.42, 0.0)],
    ATENCAO: [(600, 0.08, 0.0), (600, 0.08, 0.16), (600, 0.08, 0.32)],
}


def tocar(sinal: str, volume: float = 0.28) -> None:
    """Toca o sinal uma vez. Chamar logo apos processar a leitura.

    `sinal`: OK | ERRO | ATENCAO. Volume 0..1 (0.28 corta o ruido da
    bancada sem incomodar quem esta' do lado).
    """
    notas = _PADROES.get(sinal)
    if not notas:
        return

    # onda quadrada no erro: corta mais o barulho da expedicao que a senoide
    onda = "square" if sinal == ERRO else "sine"

    components.html(
        f"""
        <script>
        (function () {{
          try {{
            const AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return;
            const ctx = new AC();
            // Navegador suspende o contexto ate' o primeiro gesto do usuario.
            if (ctx.state === 'suspended') {{ ctx.resume(); }}
            const notas = {json.dumps(notas)};
            const vol = {volume};
            notas.forEach(function (n) {{
              const freq = n[0], dur = n[1], atraso = n[2];
              const t0 = ctx.currentTime + atraso;
              const osc = ctx.createOscillator();
              const g = ctx.createGain();
              osc.type = {json.dumps(onda)};
              osc.frequency.value = freq;
              // Envelope: sem ele o corte seco estala no alto-falante.
              g.gain.setValueAtTime(0.0001, t0);
              g.gain.exponentialRampToValueAtTime(vol, t0 + 0.012);
              g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
              osc.connect(g); g.connect(ctx.destination);
              osc.start(t0); osc.stop(t0 + dur + 0.02);
            }});
            // Libera o contexto depois do ultimo bip: o Chrome limita quantos
            // AudioContext ficam abertos, e a pagina cria um por leitura.
            setTimeout(function () {{ try {{ ctx.close(); }} catch (e) {{}} }}, 1500);
          }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
    )
