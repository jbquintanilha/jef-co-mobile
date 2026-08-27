# ==============================================================================
# NOME DO SCRIPT: scanner_camera_ao_vivo.py
# DESCRICAO: Componente de camera AO VIVO do Scanner de Conferencia. Le QR e
#            codigo de barras no navegador mediante clique no botao ou foto nativa.
# FUNCAO: Leitura por clique com fallback automatico para camera nativa do celular.
# STATUS: ATIVO
# MOTOR: BarcodeDetector nativo / html5-qrcode fallback / HTML5 File Capture
# VERSAO: 1.2
# DATA: 04/08/2026
# AUTOR: Antigravity (Violino) / J&F Co.
# ==============================================================================
"""Camera ao vivo para o Scanner de Conferencia (Leitura por Clique + Câmera Nativa).

MODO DE OPERACAO:
  1. HTTPS/Localhost: Abre vídeo em tempo real + botão '📸 LER CÓDIGO'.
  2. HTTP no Celular (quando o navegador bloqueia getUserMedia): Exibe o botão
     '📷 TIRAR FOTO DA ETIQUETA', abrindo a câmera nativa do celular direto para
     bipar e decodificar a foto sem necessitar de HTTPS.
"""

from __future__ import annotations

import streamlit.components.v1 as components

ALTURA_PADRAO = 460


def render_camera(altura: int = ALTURA_PADRAO, chave_query: str = "cod",
                  botao_submit: str = "Resolver", rearmar: bool = False) -> None:
    """Desenha a camera ao vivo com suporte a foto nativa de celular em HTTP.

    ``botao_submit``: texto do botao que a camera clica depois de preencher o
    campo. A tela principal usa "Resolver"; a conferencia final usa "Conferir".
    Sem isso a camera le o codigo mas nao dispara o submit na outra tela.
    """
    html = """
<div id="wrap">
  <div id="video-box">
    <video id="cam" playsinline muted></video>
    <div id="mira"></div>
    <div id="status">Aponte a câmera e toque em 📸 LER CÓDIGO</div>
  </div>
  <button id="btn-capturar" type="button">📸 LER CÓDIGO</button>
  
  <!-- Fallback de Câmera Nativa para conexões HTTP no Celular -->
  <div id="box-camera-nativa" style="display:none; margin-top:12px; text-align:center;">
    <label for="inp-foto-nativa" id="btn-foto-nativa">
      📷 TIRAR FOTO DA ETIQUETA (CÂMERA DO CELULAR)
    </label>
    <input id="inp-foto-nativa" type="file" accept="image/*" capture="environment" style="display:none;" />
  </div>

  <div id="erro"></div>
</div>

<style>
  html, body { margin:0; padding:0; background:#0b1220; }
  #wrap { font-family: system-ui, -apple-system, sans-serif; }
  #video-box {
    position: relative; width: 100%; background:#000;
    border-radius: 14px; overflow: hidden; border: 2px solid #1e3a5f;
  }
  #cam { width: 100%; height: __ALTURA__px; object-fit: cover; display:block; }
  #mira {
    position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
    width: 78%; height: 45%;
    border: 3px solid #22c55e; border-radius: 12px;
    box-shadow: 0 0 0 9999px rgba(0,0,0,.35);
    pointer-events:none;
  }
  #status {
    position:absolute; left:0; right:0; bottom:0;
    background: rgba(2,6,23,.85); color:#e2e8f0;
    font-size: 15px; font-weight:600; text-align:center; padding:10px 8px;
  }
  #status.ok { background: rgba(22,163,74,.95); color:#fff; }
  #btn-capturar {
    display: block; width: 100%; margin-top: 10px; padding: 16px 20px;
    background: linear-gradient(135deg, #16a34a, #15803d); color: #ffffff;
    font-size: 20px; font-weight: 800; border: 2px solid #22c55e;
    border-radius: 12px; cursor: pointer; text-align: center;
    box-shadow: 0 4px 14px rgba(22, 163, 74, 0.4); letter-spacing: .5px;
    transition: all 0.15s ease;
  }
  #btn-capturar:active {
    transform: scale(0.97); background: #15803d;
  }
  #btn-capturar:disabled {
    opacity: 0.65; cursor: not-allowed; transform: none; background: #334155; border-color: #475569;
  }
  #btn-foto-nativa {
    display: block; width: 100%; padding: 16px 14px;
    background: linear-gradient(135deg, #2563eb, #1d4ed8); color: #ffffff;
    font-size: 18px; font-weight: 800; border: 2px solid #3b82f6;
    border-radius: 12px; cursor: pointer; text-align: center;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4); letter-spacing: .5px;
  }
  #btn-foto-nativa:active { transform: scale(0.97); background: #1d4ed8; }
  #erro {
    color:#fecaca; background:#450a0a; border:1px solid #dc2626;
    border-radius:10px; padding:10px 12px; margin-top:10px;
    font-size:14px; display:none;
  }
</style>

<script>
(function () {
  const video          = document.getElementById('cam');
  const statusEl       = document.getElementById('status');
  const erroEl         = document.getElementById('erro');
  const btnCapturar    = document.getElementById('btn-capturar');
  const boxCamNativa   = document.getElementById('box-camera-nativa');
  const inpFotoNativa  = document.getElementById('inp-foto-nativa');
  const CHAVE          = "__CHAVE__";
  let   parado         = false;
  let   detector       = null;
  let   leitorCdn      = null;

  function falhar(msg) {
    erroEl.style.display = 'block';
    erroEl.innerHTML = msg;
    statusEl.textContent = 'Câmera em tempo real indisponível';
    if (btnCapturar) btnCapturar.style.display = 'none';
    if (boxCamNativa) boxCamNativa.style.display = 'block';
    carregarLeitorCdnParaFoto();
  }

  function bipar() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
      function tom(freq, inicio, dur) {
        const osc = ctx.createOscillator();
        const g = ctx.createGain();
        osc.type = 'square';
        osc.frequency.value = freq;
        g.gain.setValueAtTime(0.0001, ctx.currentTime + inicio);
        g.gain.exponentialRampToValueAtTime(0.35, ctx.currentTime + inicio + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + inicio + dur);
        osc.connect(g); g.connect(ctx.destination);
        osc.start(ctx.currentTime + inicio);
        osc.stop(ctx.currentTime + inicio + dur + 0.02);
      }
      tom(1180, 0,    0.09);
      tom(1560, 0.10, 0.11);
      setTimeout(function(){ try { ctx.close(); } catch(e){} }, 700);
    } catch (e) {}
  }

  function entregar(codigo) {
    if (parado) return;
    parado = true;
    statusEl.className = 'ok';
    statusEl.textContent = '✅ Lido: ' + codigo;
    bipar();
    if (navigator.vibrate) { try { navigator.vibrate(120); } catch(e){} }
    try {
      const stream = video.srcObject;
      if (stream) stream.getTracks().forEach(t => t.stop());
    } catch (e) {}

    setTimeout(function () {
      let entregue = false;
      try {
        const doc = window.parent.document;
        const inputs = [...doc.querySelectorAll('input[type="text"]')];
        const alvo = inputs.find(i =>
          (i.getAttribute('aria-label') || '').toLowerCase().includes('código') ||
          (i.placeholder || '').toUpperCase().includes('AP296')
        ) || inputs[0];

        if (alvo) {
          const setter = Object.getOwnPropertyDescriptor(
            window.parent.HTMLInputElement.prototype, 'value').set;
          setter.call(alvo, codigo);
          alvo.dispatchEvent(new window.parent.Event('input', { bubbles: true }));
          alvo.dispatchEvent(new window.parent.KeyboardEvent('keydown',
            { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));

          setTimeout(function () {
            const botoes = [...doc.querySelectorAll('button')];
            const b = botoes.find(x => (x.innerText || '').includes('__BTN_SUBMIT__'));
            if (b) b.click();
          }, 120);
          entregue = true;

          // REARME: em tela de bipagem continua (conferencia final), o
          // Streamlit faz rerun mas o iframe do componente NAO e' recriado --
          // ele volta com parado=true e o stream ja encerrado, e a camera
          // morria a partir do 2o bip (relato do Comandante, 2026-08-09).
          // Religa sozinha depois de entregar, pronta pra proxima etiqueta.
          if (__REARMAR__) {
            setTimeout(function () {
              parado = false;
              statusEl.className = '';
              statusEl.textContent = 'Aponte a câmera e toque em 📸 LER CÓDIGO';
              // O botao so era reabilitado no caminho de FALHA de leitura
              // (linha do "Codigo nao detectado"). Numa leitura bem-sucedida
              // ele ficava disabled pra sempre -- religar o video sem religar
              // o botao ainda deixava a camera travada no 2o bip.
              if (btnCapturar) {
                btnCapturar.disabled = false;
                btnCapturar.textContent = '📸 LER CÓDIGO';
              }
              try { iniciar(); } catch (e) {}
            }, 350);
          }
        }
      } catch (e) {}

      if (!entregue) {
        statusEl.className = '';
        statusEl.textContent = 'Lido: ' + codigo + ' — toque em Resolver';
        falhar('Código lido: <b>' + codigo + '</b><br>' +
               'Não consegui enviar automaticamente. Abra ' +
               '<b>⌨️ Digitar código</b> abaixo, cole e toque em Resolver.');
      }
    }, 240);
  }

  const RE_RASTREIO = /\\b([A-Z]{2}\\d{9}[A-Z]{2})\\b/;
  function melhor(codigos) {
    const textos = codigos.map(c => (c.rawValue || c.text || '').trim()).filter(Boolean);
    for (const t of textos) if (RE_RASTREIO.test(t) && t.length <= 20) return t;
    for (const t of textos) { const m = t.match(RE_RASTREIO); if (m) return m[1]; }
    for (const t of textos.slice().sort((a,b)=>a.length-b.length)) {
      if (!/^\\d{44}$/.test(t) && t.length <= 40) return t;
    }
    return textos[0] || null;
  }

  async function executarLeituraClique() {
    if (parado) return;
    btnCapturar.disabled = true;
    btnCapturar.textContent = '⏳ Lendo código...';
    statusEl.className = '';
    statusEl.textContent = '🔍 Analisando imagem...';

    const tInicio = Date.now();
    let achou = false;

    while (Date.now() - tInicio < 400 && !achou && !parado) {
      try {
        if (detector && video) {
          const achados = await detector.detect(video);
          if (achados && achados.length) {
            const cod = melhor(achados);
            if (cod) {
              achou = true;
              entregar(cod);
              return;
            }
          }
        } else if (leitorCdn) {
          const cvs = document.createElement('canvas');
          cvs.width = video.videoWidth || 640;
          cvs.height = video.videoHeight || 480;
          const ctx2d = cvs.getContext('2d');
          ctx2d.drawImage(video, 0, 0, cvs.width, cvs.height);

          await new Promise((resolve) => {
            cvs.toBlob(async (blob) => {
              if (blob && leitorCdn) {
                try {
                  const file = new File([blob], "frame.png", { type: "image/png" });
                  const resCdn = await leitorCdn.scanFileV2(file, false);
                  if (resCdn && resCdn.decodedText) {
                    const cod = (resCdn.decodedText || '').trim();
                    if (cod) {
                      achou = true;
                      entregar(cod);
                    }
                  }
                } catch(errCdn) {}
              }
              resolve();
            }, 'image/png');
          });
          if (achou) return;
        }
      } catch (e) {}

      await new Promise(r => setTimeout(r, 60));
    }

    if (!achou && !parado) {
      statusEl.className = '';
      statusEl.textContent = '❌ Código não detectado. Centralize na mira e tente novamente.';
      btnCapturar.disabled = false;
      btnCapturar.textContent = '📸 LER CÓDIGO';
    }
  }

  btnCapturar.addEventListener('click', executarLeituraClique);

  function carregarLeitorCdnParaFoto() {
    if (window.Html5Qrcode) {
      if (!leitorCdn) {
        const dummyDiv = document.createElement('div');
        dummyDiv.id = 'cdn-dummy-photo';
        dummyDiv.style.display = 'none';
        document.body.appendChild(dummyDiv);
        leitorCdn = new window.Html5Qrcode('cdn-dummy-photo');
      }
      return;
    }
    const s = document.createElement('script');
    s.src = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
    s.onload = function () {
      try {
        const dummyDiv = document.createElement('div');
        dummyDiv.id = 'cdn-dummy-photo';
        dummyDiv.style.display = 'none';
        document.body.appendChild(dummyDiv);
        leitorCdn = new window.Html5Qrcode('cdn-dummy-photo');
      } catch(e) {}
    };
    document.head.appendChild(s);
  }

  // Handler para quando o usuário tira foto pela câmera nativa do celular (HTTP fallback)
  inpFotoNativa.addEventListener('change', async function (e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    statusEl.className = '';
    statusEl.textContent = '⏳ Decodificando foto tirada...';
    try {
      if ('BarcodeDetector' in window) {
        const imgEl = new Image();
        imgEl.src = URL.createObjectURL(file);
        await imgEl.decode();
        const det = detector || new window.BarcodeDetector();
        const achados = await det.detect(imgEl);
        if (achados && achados.length) {
          const cod = melhor(achados);
          if (cod) { entregar(cod); return; }
        }
      }
      if (leitorCdn) {
        const resCdn = await leitorCdn.scanFileV2(file, false);
        if (resCdn && resCdn.decodedText) {
          const cod = (resCdn.decodedText || '').trim();
          if (cod) { entregar(cod); return; }
        }
      }
      statusEl.textContent = '❌ Nenhum código encontrado na foto. Tente aproximação.';
    } catch(err) {
      statusEl.textContent = '❌ Erro ao ler foto: ' + err.message;
    }
  });

  function iniciarFallbackCDN() {
    statusEl.textContent = 'Carregando leitor alternativo…';
    carregarLeitorCdnParaFoto();
    statusEl.textContent = 'Aponte a câmera e toque em 📸 LER CÓDIGO';
  }

  async function iniciar() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      falhar("<b>Câmera em tempo real bloqueada pelo navegador.</b><br>" +
             "Dispositivos móveis exigem <b>HTTPS</b> ou <b>localhost</b> para abrir vídeo ao vivo na rede local.<br>" +
             "<b>Solução rápida:</b> Use o botão azul abaixo para abrir a câmera nativa do celular ou acesse via HTTPS.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' },
                 width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false
      });
      video.srcObject = stream;
      await video.play();
      statusEl.textContent = 'Aponte a câmera e toque em 📸 LER CÓDIGO';
    } catch (e) {
      falhar("Não foi possível abrir a câmera: " + e.message +
             "<br>Use o botão azul abaixo para tirar foto com a câmera do celular.");
      return;
    }

    if (!('BarcodeDetector' in window)) {
      iniciarFallbackCDN();
      return;
    }

    try {
      const suportados = await window.BarcodeDetector.getSupportedFormats();
      const querer = ['qr_code','code_128','code_39','ean_13','data_matrix','itf'];
      const usar = querer.filter(f => suportados.includes(f));
      detector = new window.BarcodeDetector({ formats: usar.length ? usar : suportados });
    } catch (e) {
      iniciarFallbackCDN();
    }
  }

  iniciar();
})();
</script>
"""
    html = (html.replace("__ALTURA__", str(altura))
                .replace("__CHAVE__", chave_query)
                .replace("__BTN_SUBMIT__", botao_submit)
                .replace("__REARMAR__", "true" if rearmar else "false"))
    components.html(html, height=altura + 150)
