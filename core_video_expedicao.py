# ==============================================================================
# NOME DO SCRIPT: core_video_expedicao.py
# DESCRICAO: Gravacao continua da bancada de expedicao e indice por tracking
# FUNCAO: Prover video de defesa para disputas de envio
# AUTOR: O Monge (003) / Terminador (001) / Violino (000) - J&F Co.
# VERSAO: 1.1 | DATA: 2026-08-16
# STATUS: ATIVO
# ==============================================================================

from __future__ import annotations
import core_env_loader

import atexit
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import cv2
except Exception as _erro_cv2:
    cv2 = None
    ERRO_IMPORTACAO_CV2 = str(_erro_cv2)
else:
    ERRO_IMPORTACAO_CV2 = ""


CONFIG_FILE = Path(r"C:\JF_Automacoes\camera_config.json")
PASTA_VIDEO_DEFAULT = r"E:\Videos de PROVA - JEFCO"
CAMINHO_BANCO_DEFAULT = r"C:\JF_Automacoes\local_db\rastreio_pedidos.db"

MIN_ESPACO_GB_DEFAULT = 10
DURACAO_MAXIMA_SESSAO_HORAS = 4.0
TIMEOUT_SEM_ATIVIDADE_DEFAULT = 10 * 60

RESOLUCAO_DEFAULT = (1280, 720)
FPS_DEFAULT = 15
CRF_DEFAULT = 28
# 🔴 1, nao 0. No PC da bancada o indice 0 e' a camera integrada (tampada,
# quadro preto) e o 1 e' a webcam USB. Alem disso a integrada fica reservada
# a' bipagem no navegador — a gravacao usa a USB. `abrir_camera` ainda testa
# os outros indices se este falhar.
INDICE_CAMERA_DEFAULT = 1


def carregar_camera_config() -> Dict[str, Any]:
    cfg = {
        "pasta": PASTA_VIDEO_DEFAULT,
        "resolucao": RESOLUCAO_DEFAULT,
        "fps": FPS_DEFAULT,
        "crf": CRF_DEFAULT,
        "timeout_sem_atividade": TIMEOUT_SEM_ATIVIDADE_DEFAULT,
        "minimo_espaco_gb": MIN_ESPACO_GB_DEFAULT,
        "duracao_maxima_horas": DURACAO_MAXIMA_SESSAO_HORAS,
        "camera_nome": "WEB CAMER",
        "indice_camera": INDICE_CAMERA_DEFAULT,
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if "pasta_gravacoes" in dados:
                    cfg["pasta"] = dados["pasta_gravacoes"]
                if "largura" in dados and "altura" in dados:
                    cfg["resolucao"] = (int(dados["largura"]), int(dados["altura"]))
                if "fps" in dados:
                    cfg["fps"] = float(dados["fps"])
                if "camera_nome" in dados:
                    cfg["camera_nome"] = dados["camera_nome"]
        except Exception:
            pass
    return cfg


# Abaixo deste brilho medio (0-255) o quadro e' considerado preto: lente
# tampada ou camera errada. Medido: integrada tampada = 0.4, USB boa = 112.
LIMIAR_QUADRO_PRETO = 8.0


def _quadro_preto(frame: Any, limiar: float = LIMIAR_QUADRO_PRETO) -> bool:
    """True se o quadro nao tem imagem util (praticamente todo preto)."""
    try:
        import numpy as _np

        return float(_np.mean(frame)) < limiar
    except Exception:
        return False        # na duvida, aceita o quadro


class _LeitorPipe:
    """Le quadros crus do pipe do ffmpeg com a interface do cv2.VideoCapture.

    Existe para o resto da classe continuar chamando `.read()` e `.release()`
    sem saber que a captura deixou de ser OpenCV.
    """

    def __init__(self, processo: subprocess.Popen, largura: int, altura: int) -> None:
        self.processo = processo
        self.largura = int(largura)
        self.altura = int(altura)
        self.bytes_por_quadro = self.largura * self.altura * 3
        self._fechado = False

    def read(self) -> Tuple[bool, Any]:
        if self._fechado or self.processo.stdout is None:
            return False, None
        try:
            bruto = self.processo.stdout.read(self.bytes_por_quadro)
        except Exception:
            return False, None

        if not bruto or len(bruto) < self.bytes_por_quadro:
            return False, None      # pipe fechou ou quadro incompleto

        try:
            import numpy as _np

            # ⚡ `bytearray` evita o `.copy()` de 2.7 MB por quadro: o array
            # ja' nasce gravavel, e o carimbo de hora precisa escrever nele.
            quadro = _np.frombuffer(bytearray(bruto), dtype=_np.uint8).reshape(
                (self.altura, self.largura, 3))
            return True, quadro
        except Exception:
            return False, None

    def isOpened(self) -> bool:      # noqa: N802 (nome espelha o cv2)
        return not self._fechado and self.processo.poll() is None

    def release(self) -> None:
        self._fechado = True
        try:
            if self.processo.stdout:
                self.processo.stdout.close()
        except Exception:
            pass
        try:
            self.processo.terminate()
            self.processo.wait(timeout=5)
        except Exception:
            try:
                self.processo.kill()
            except Exception:
                pass


_LOCK = threading.RLock()
_SESSAO: Optional["SessaoGravacao"] = None


def listar_cameras_dshow() -> list:
    """Nomes reais das cameras no Windows, via DirectShow.

    Copiado da estrutura do `tools/gravador_webcam_prova.py`, que funciona ha'
    meses (Jota, 2026-08-16: "copia essa estrutura, funciona perfeita").
    Nome nao troca entre execucoes; indice troca — foi o que fez a gravacao
    cair na camera errada varias vezes.
    """
    cams: list = []
    try:
        res = subprocess.run(
            ["ffmpeg", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stderr=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", errors="ignore", timeout=15,
        )
        for linha in res.stderr.splitlines():
            if "(video)" in linha and '"' in linha:
                nome = linha.split('"')[1]
                if nome not in cams:
                    cams.append(nome)
    except Exception:
        pass
    return cams


def melhor_modo(cam_nome: str, teto_altura: int = 720) -> tuple:
    """Descobre o melhor modo que a camera REALMENTE suporta.

    ⚠️ Antes o script forcava 1280x720 MJPEG em qualquer camera. A camera
    da bancada ("Dispositivo de video USB") so' entrega 640x480 yuyv422 --
    o ffmpeg respondia "Could not set video options" e o gravador morria
    sem gravar nada (incidente 02/09/2026).

    Devolve (codec, largura, altura, fps), onde codec e' "mjpeg" ou o
    pixel_format cru. Prefere MJPEG (mais nitido e leve) e a MAIOR
    resolucao -- etiqueta precisa ficar legivel na prova de expedicao.
    """
    modos = []
    try:
        cmd = ["ffmpeg", "-f", "dshow", "-list_options", "true",
               "-i", f"video={cam_nome}"]
        res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="ignore",
                             timeout=25)
        for linha in res.stderr.splitlines():
            if "max s=" not in linha:
                continue
            try:
                dims = linha.split("max s=")[1].split()[0]
                w, h = (int(v) for v in dims.split("x"))
                fps = int(float(linha.split("fps=")[-1].strip().rstrip(")")))
            except Exception:
                continue
            if "vcodec=mjpeg" in linha:
                modos.append(("mjpeg", w, h, fps))
            elif "pixel_format=" in linha:
                pf = linha.split("pixel_format=")[1].split()[0]
                modos.append((pf, w, h, fps))
    except Exception as e:
        print(f"⚠️ Nao consegui listar modos da camera: {e}")

    if not modos:
        print("⚠️ Nenhum modo detectado — tentando 640x480 yuyv422")
        return ("yuyv422", 640, 480, 30)

    # ⚠️ TETO de proposito (Jota, 02/09): a gravacao roda 2-3h por dia.
    # Em 1080p o arquivo fica gigante sem ganho real -- 720p ja' deixa a
    # etiqueta legivel na prova de expedicao.
    cabem = [m for m in modos if m[2] <= teto_altura]
    if cabem:
        modos = cabem

    # MJPEG primeiro, depois maior area, depois mais fps
    modos.sort(key=lambda m: (m[0] == "mjpeg", m[1] * m[2], m[3]), reverse=True)
    return modos[0]


def escolher_camera_gravacao(preferida: str = "") -> str:
    """Nome da camera que a gravacao deve usar.

    Prioriza a preferida do config (`WEB CAMER`), depois qualquer USB, e por
    ultimo a primeira que existir. A integrada fica para a bipagem.
    """
    cams = listar_cameras_dshow()
    if not cams:
        return preferida or "WEB CAMER"

    if preferida:
        for cam in cams:
            if cam.strip().lower() == preferida.strip().lower():
                return cam
        for cam in cams:
            if preferida.strip().lower() in cam.strip().lower():
                return cam

    # ⚠️ ORDEM IMPORTA (Jota, 02/09/2026): "WEB CAMER" e' a webcam NATIVA do
    # PC; a camera da bancada, apontada pra mesa, chama "Dispositivo de video
    # USB". A regra antiga procurava "web camer" PRIMEIRO e por isso acendia
    # a nativa quando a config se perdia. USB vem antes de proposito.
    for chave in ("dispositivo de v", "usb", "logitech", "external", "web camer"):
        for cam in cams:
            if chave in cam.lower():
                return cam

    return cams[0]


def _matar_ffmpeg_orfaos() -> int:
    """Encerra ffmpeg orfao que ainda segura a webcam da expedicao.

    ⚠️ So' mata processo cuja linha de comando aponta para a PASTA DE VIDEOS
    da expedicao. Nunca toca em ffmpeg de outra tarefa (Vicent, conversao de
    anuncio, etc) — matar ffmpeg alheio interromperia trabalho do Jota.

    Nao faz nada se ha' sessao ativa neste processo: ai' o ffmpeg e' legitimo.
    """
    with _LOCK:
        if _SESSAO is not None and _SESSAO.ativa:
            return 0

    pasta = str(carregar_camera_config().get("pasta") or PASTA_VIDEO_DEFAULT)
    alvo = os.path.basename(pasta.rstrip("\\/")).lower()
    if not alvo:
        return 0

    mortos = 0
    try:
        # ⚠️ `errors="replace"`: a saida do wmic vem no codepage do Windows e
        # estoura UnicodeDecodeError com acento no caminho.
        saida = subprocess.run(
            ["wmic", "process", "where", "name='ffmpeg.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        ).stdout
    except Exception:
        return 0

    for linha in saida.splitlines():
        if alvo not in linha.lower() or "expedicao_" not in linha.lower():
            continue
        pid = linha.rsplit(",", 1)[-1].strip()
        if not pid.isdigit():
            continue
        try:
            subprocess.run(["taskkill", "/PID", pid, "/F"],
                           capture_output=True, timeout=10)
            mortos += 1
        except Exception:
            pass

    if mortos:
        time.sleep(1.0)     # da' tempo do Windows liberar a webcam
    return mortos


def verificar_ffmpeg() -> Dict[str, Any]:
    """Retorna se o ffmpeg esta disponivel e o caminho encontrado."""
    caminho = shutil.which("ffmpeg")
    return {"disponivel": bool(caminho), "caminho": caminho or ""}


def segundo_atual() -> Optional[int]:
    """Segundo indexavel da sessao ativa."""
    with _LOCK:
        if _SESSAO is None or not _SESSAO.ativa:
            return None
        return _SESSAO.video_segundo_atual()


class SessaoGravacao:
    def __init__(
        self,
        indice_camera: int = INDICE_CAMERA_DEFAULT,
        resolucao: Tuple[int, int] = RESOLUCAO_DEFAULT,
        fps: float = FPS_DEFAULT,
        crf: int = CRF_DEFAULT,
        timeout_sem_atividade: Optional[float] = TIMEOUT_SEM_ATIVIDADE_DEFAULT,
        pasta: str = PASTA_VIDEO_DEFAULT,
        minimo_espaco_gb: float = MIN_ESPACO_GB_DEFAULT,
        duracao_maxima_horas: float = DURACAO_MAXIMA_SESSAO_HORAS,
    ) -> None:
        self.indice_camera = int(indice_camera)
        self.resolucao = (int(resolucao[0]), int(resolucao[1]))
        self.fps = float(fps)
        if self.fps <= 0:
            raise ValueError("fps deve ser maior que zero")

        self.crf = str(int(crf))
        self.timeout_sem_atividade = (
            float(timeout_sem_atividade)
            if timeout_sem_atividade is not None
            else None
        )
        self.pasta = str(pasta or PASTA_VIDEO_DEFAULT)
        self.minimo_espaco_bytes = (
            int(float(minimo_espaco_gb or MIN_ESPACO_GB_DEFAULT))
            * 1024
            * 1024
            * 1024
        )
        self.duracao_maxima_horas = float(duracao_maxima_horas or 0)
        self.preset = "veryfast"
        self.keyint = max(5, int(self.fps) * 5)

        self.nome_base = "expedicao_" + datetime.datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        self.arquivo_video = self.nome_base + ".mp4"
        self.caminho_video = os.path.join(self.pasta, self.arquivo_video)

        self.arquivo_log = self.nome_base + "_ffmpeg.log"
        self.caminho_log = os.path.join(self.pasta, self.arquivo_log)

        self.captura: Any = None
        self.processo: Optional[subprocess.Popen] = None
        self._logf: Any = None
        self.thread: Optional[threading.Thread] = None

        self.parar_evento = threading.Event()
        self.primeira_frame_event = threading.Event()
        # Pausa: para de GRAVAR sem soltar a camera nem fechar o arquivo.
        self.pausado_evento = threading.Event()
        self.segundos_pausados = 0.0
        self._pausa_iniciada_em: Optional[float] = None

        self.inicio_monotonic: Optional[float] = None
        self.inicio_iso: Optional[str] = None
        self.ultima_atividade_monotonic: Optional[float] = None

        self.quadros_gravados = 0
        self.ativa = False
        self.motivo_parada: Optional[str] = None
        self.dimensao_real: Optional[Tuple[int, int]] = None
        self.ultimo_frame: Any = None
        # Preenchido por `abrir_camera` — a captura agora e' por nome
        self.camera_nome: str = ""

    def abrir_camera(self) -> Any:
        """Abre a webcam pelo NOME, via pipe do ffmpeg.

        🔴 Estrutura copiada do `tools/gravador_webcam_prova.py`, que funciona
        ha' meses (Jota, 2026-08-16: "copia essa estrutura, funciona perfeita").

        Por que abandonei o `cv2.VideoCapture(indice)`:
          - INDICE TROCA entre execucoes; o Windows reordena os dispositivos.
            Medido no mesmo PC: idx1 = USB HD numa hora, camera errada na
            seguinte. NOME nao troca.
          - O OpenCV negociava 640x480 com a USB, que e' nativa 1280x720.
          - `nobuffer` + `low_delay` do ffmpeg eliminam o atraso acumulado
            que fazia o preview ficar lento e travado.

        Devolve um objeto com `.read()` compativel com o resto da classe.
        """
        cam_nome = escolher_camera_gravacao(
            carregar_camera_config().get("camera_nome", "WEB CAMER"))
        largura, altura = self.resolucao

        # ⚠️ A webcam so' aceita os modos que o sensor anuncia. Pedir um fps
        # que ela nao tem faz o ffmpeg abortar com "Could not set video
        # options" — foi o que aconteceu ao baixar para 15 (a WEB CAMER exige
        # 30). Por isso tenta o fps pedido e cai para os padroes conhecidos.
        # ⚠️ PERGUNTA A' CAMERA em vez de impor o modo (Jota, 02/09/2026).
        # Antes forcava mjpeg 1280x720 em qualquer camera: com a USB da
        # bancada ("Dispositivo de video USB", que so' entrega yuyv422
        # 640x480) o ffmpeg respondia "Could not set video options" ->
        # "I/O error" e a Fase 5 nao gravava nada. A config guarda o ultimo
        # uso, nao a verdade do hardware -- so' o sensor sabe o que aceita.
        codec = "mjpeg"
        fps_ok = int(self.fps)
        try:
            codec, l_ok, a_ok, fps_ok = melhor_modo(cam_nome)
            if (l_ok, a_ok) != (largura, altura):
                print(f"[video] '{cam_nome}' nao faz {largura}x{altura} — "
                      f"usando {l_ok}x{a_ok} {codec}")
                largura, altura = l_ok, a_ok
        except Exception as exc:                     # nunca impede de gravar
            print(f"[video] deteccao de modo falhou ({exc}) — "
                  f"tentando {largura}x{altura} mjpeg")

        # MJPEG entra como -vcodec; formato cru (yuyv422 etc) como
        # -pixel_format. Passar o errado e' o mesmo "Could not set video
        # options" de novo.
        entrada_formato = (["-vcodec", "mjpeg"] if codec == "mjpeg"
                           else ["-pixel_format", codec])

        candidatos: list = []
        for taxa in (int(fps_ok), int(self.fps), 30, 15, 0):
            if taxa not in candidatos:
                candidatos.append(taxa)

        ultimo_erro = ""
        for taxa in candidatos:
            cmd = [
                "ffmpeg",
                "-f", "dshow",
                "-rtbufsize", "150M",
                *entrada_formato,               # modo REAL desta camera
                "-video_size", f"{largura}x{altura}",
            ]
            if taxa:                            # 0 = deixa a camera decidir
                cmd += ["-framerate", str(taxa)]
            cmd += [
                "-fflags", "nobuffer",          # sem acumulo -> sem lag
                "-flags", "low_delay",
                "-i", f"video={cam_nome}",
                "-f", "image2pipe",
                "-pix_fmt", "bgr24",
                "-vcodec", "rawvideo",
                "-",
            ]

            pipe = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=largura * altura * 3 * 2,
            )
            leitor = _LeitorPipe(pipe, largura, altura)

            ok, quadro = leitor.read()
            if ok and quadro is not None:
                self.camera_nome = cam_nome
                self.fps_real = taxa or self.fps
                self.dimensao_real = (quadro.shape[1], quadro.shape[0])
                return leitor

            leitor.release()
            ultimo_erro = f"{taxa or 'auto'} fps recusado"

        raise RuntimeError(
            f"Camera '{cam_nome}' nao entregou imagem em {largura}x{altura} "
            f"({ultimo_erro}). Confira se ela esta conectada e se nenhum "
            "outro programa a usa."
        )

    def _verificar_espaco_inicial(self) -> None:
        try:
            livre = shutil.disk_usage(self.pasta).free
        except OSError as exc:
            raise RuntimeError(
                f"Nao foi possivel inspecionar o disco de videos: {exc}"
            ) from exc

        if livre < self.minimo_espaco_bytes:
            raise RuntimeError(
                "Espaco minimo em disco nao atingido para iniciar gravacao"
            )

    def _espaco_abaixo_minimo(self) -> bool:
        try:
            return shutil.disk_usage(self.pasta).free < self.minimo_espaco_bytes
        except OSError:
            return False

    def _criar_processo_ffmpeg(self) -> None:
        if self._logf is not None:
            try:
                self._logf.close()
            except Exception:
                pass
            self._logf = None

        self._logf = open(self.caminho_log, "ab")

        dim = self.dimensao_real or self.resolucao
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{dim[0]}x{dim[1]}",
            "-framerate",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            self.preset,
            "-crf",
            self.crf,
            "-pix_fmt",
            "yuv420p",
            "-x264opts",
            (
                f"keyint={self.keyint}:"
                f"min-keyint={max(1, self.keyint // 2)}:"
                "scenecut=0"
            ),
            "-movflags",
            "+empty_moov+frag_keyframe",
            "-f",
            "mp4",
            self.caminho_video,
        ]

        try:
            self.processo = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._logf,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            self._logf.close()
            self._logf = None
            raise

    def iniciar(self) -> "SessaoGravacao":
        if not shutil.which("ffmpeg"):
            raise RuntimeError("Ffmpeg nao foi encontrado no PATH")

        os.makedirs(self.pasta, exist_ok=True)
        self._verificar_espaco_inicial()

        self.captura = self.abrir_camera()

        try:
            self._criar_processo_ffmpeg()
        except Exception:
            self._encerrar_recursos()
            raise

        self.inicio_monotonic = time.monotonic()
        self.inicio_iso = datetime.datetime.now().isoformat(timespec="seconds")
        self.ultima_atividade_monotonic = self.inicio_monotonic
        self.ativa = True
        self.parar_evento.clear()
        self.primeira_frame_event.clear()

        self.thread = threading.Thread(
            target=self._loop_gravacao,
            name="video-expedicao",
            daemon=True,
        )
        self.thread.start()

        if not self.primeira_frame_event.wait(5.0):
            motivo = self.motivo_parada or "nenhum_frame_recebido"
            self.parar_evento.set()
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(timeout=2)
            self._encerrar_recursos()
            self.ativa = False
            raise RuntimeError(f"Falha ao iniciar gravacao: {motivo}")

        return self

    def _escrever_frame(self, frame: Any) -> bool:
        if self.processo is None or self.processo.poll() is not None:
            return False

        try:
            # ⚡ `frame.data` evita a copia de 2.7 MB que `tobytes()` faz a
            # cada quadro. Se o array nao for contiguo (recorte/slice), cai
            # para o caminho antigo.
            try:
                self.processo.stdin.write(frame.data)
            except (TypeError, ValueError, BufferError):
                self.processo.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError, ValueError):
            return False

        if self.processo.poll() is not None:
            return False

        return True

    def _loop_gravacao(self) -> None:
        falhas_consecutivas = 0
        ultimo_espaco_verificado = time.monotonic()
        primeiro_frame_enviado = False
        ultimo_preview = 0.0        # quando o quadro do preview foi atualizado

        try:
            while not self.parar_evento.is_set():
                agora = time.monotonic()

                if (
                    self.timeout_sem_atividade is not None
                    and self.ultima_atividade_monotonic is not None
                    and agora - self.ultima_atividade_monotonic
                    > self.timeout_sem_atividade
                ):
                    self.motivo_parada = "inatividade"
                    self.parar_evento.set()
                    break

                if (
                    self.duracao_maxima_horas > 0
                    and self.inicio_monotonic is not None
                    and agora - self.inicio_monotonic
                    > (self.duracao_maxima_horas * 3600)
                ):
                    self.motivo_parada = "duracao_maxima"
                    self.parar_evento.set()
                    break

                if agora - ultimo_espaco_verificado > 30:
                    if self._espaco_abaixo_minimo():
                        self.motivo_parada = "espaco_insuficiente"
                        self.parar_evento.set()
                        break
                    ultimo_espaco_verificado = agora

                if self.captura is None:
                    self.motivo_parada = "captura_indisponivel"
                    self.parar_evento.set()
                    break

                ret, frame = self.captura.read()
                if not ret or frame is None:
                    falhas_consecutivas += 1
                    if falhas_consecutivas >= 30:
                        self.motivo_parada = "falha_camera"
                        self.parar_evento.set()
                        break
                    time.sleep(0.05)
                    continue

                falhas_consecutivas = 0

                # 🔴 SEM descarte por tempo. O pipe do ffmpeg ja' entrega no
                # ritmo pedido (`-framerate 30`), entao todo quadro que chega
                # deve ser gravado. O antigo `if agora < proximo_quadro:
                # continue` — herdado da captura por OpenCV, que enche buffer —
                # jogava fora ~metade dos quadros: a gravacao rodava a ~11 fps
                # com 30 configurados (Jota, 2026-08-16).

                # Adiciona carimbo de data/hora no frame gravado
                h_f, w_f = frame.shape[:2]
                agora_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                texto_hora = f"J&F Co. | {agora_str}"
                cv2.rectangle(frame, (w_f - 310, 10), (w_f - 10, 42), (0, 0, 0), -1)
                cv2.putText(frame, texto_hora, (w_f - 300, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)

                # ⚡ O preview so' precisa de ~1 quadro por segundo, mas o
                # `copy()` de 2.7 MB era feito 30x/s — sozinho derrubava a
                # taxa de gravacao de 30 para ~11 fps (Jota, 2026-08-16:
                # "está gravando a 2 quadros por seg??? tem q ser 30").
                if agora - ultimo_preview >= 1.0:
                    self.ultimo_frame = frame.copy()
                    ultimo_preview = agora

                # ⚠️ PAUSADO: segue lendo a camera (o preview continua vivo e
                # da' para ajustar o enquadramento) mas NAO grava o quadro.
                # A camera fica retida de proposito — soltar e reabrir e' o
                # que costuma falhar com "Could not start video source".
                if self.pausado_evento.is_set():
                    continue

                if not self._escrever_frame(frame):

                    self.motivo_parada = "falha_ffmpeg"
                    self.parar_evento.set()
                    break

                self.quadros_gravados += 1
                if not primeiro_frame_enviado:
                    primeiro_frame_enviado = True
                    self.primeira_frame_event.set()


        except Exception as exc:
            self.motivo_parada = f"excecao_loop: {exc}"
            self.parar_evento.set()
        finally:
            self.ativa = False
            self._encerrar_recursos()

    def _encerrar_recursos(self) -> None:
        if self.captura is not None:
            try:
                self.captura.release()
            except Exception:
                pass
            self.captura = None

        if self.processo is not None:
            try:
                if self.processo.stdin is not None:
                    self.processo.stdin.close()
            except Exception:
                pass

            try:
                self.processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self.processo.kill()
                except Exception:
                    pass
                try:
                    self.processo.wait(timeout=2)
                except Exception:
                    pass
            except Exception:
                pass

            self.processo = None

        if self._logf is not None:
            try:
                self._logf.close()
            except Exception:
                pass
            self._logf = None

    def video_segundo_atual(self) -> int:
        return int(self.quadros_gravados / self.fps)

    def estado(self) -> Dict[str, Any]:
        if self.inicio_monotonic is None:
            segundos_ativos = 0
        else:
            segundos_ativos = max(
                0, int(time.monotonic() - self.inicio_monotonic)
            )

        return {
            "sessao_ativa": self.ativa,
            "pausada": self.pausado_evento.is_set(),
            "segundos_pausados": round(self.segundos_pausados, 1),
            "arquivo_base": self.nome_base,
            "video_arquivo": self.arquivo_video,
            "video_segundo": self.video_segundo_atual(),
            "caminho_completo": self.caminho_video,
            "inicio_iso": self.inicio_iso,
            "resolucao": tuple(self.dimensao_real or self.resolucao),
            "fps": self.fps,
            "crf": self.crf,
            "quadros_gravados": self.quadros_gravados,
            "segundos_ativos": segundos_ativos,
            "ultima_atividade": self.ultima_atividade_monotonic,
            "motivo_parada": self.motivo_parada,
        }


def _montar_indice(
    sessao: SessaoGravacao,
    tracking: Optional[str],
    aviso: Optional[str] = None,
) -> Dict[str, Any]:
    indice = {
        "sessao_ativa": True,
        "video_arquivo": sessao.arquivo_video,
        "video_segundo": sessao.video_segundo_atual(),
        "quando_iso": datetime.datetime.now().isoformat(timespec="seconds"),
        "tracking": tracking,
    }
    if aviso:
        indice["aviso"] = aviso
    return indice


def iniciar_sessao(
    indice_camera: Optional[int] = None,
    resolucao: Optional[Tuple[int, int]] = None,
    fps: Optional[float] = None,
    crf: Optional[int] = None,
    timeout_sem_atividade: Optional[float] = None,
    pasta: Optional[str] = None,
    minimo_espaco_gb: Optional[float] = None,
    duracao_maxima_horas: Optional[float] = None,
) -> Dict[str, Any]:
    global _SESSAO

    # 🔴 Um ffmpeg orfao de uma sessao anterior (pagina fechada a' forca,
    # processo morto) continua segurando a webcam e faz a proxima gravacao
    # cair para 640x480 ou nao abrir. Limpa antes de comecar.
    _matar_ffmpeg_orfaos()

    cfg_base = carregar_camera_config()
    ind = indice_camera if indice_camera is not None else cfg_base["indice_camera"]
    res = resolucao if resolucao is not None else cfg_base["resolucao"]
    f = fps if fps is not None else cfg_base["fps"]
    c = crf if crf is not None else cfg_base["crf"]
    to = timeout_sem_atividade if timeout_sem_atividade is not None else cfg_base["timeout_sem_atividade"]
    p = pasta if pasta is not None else cfg_base["pasta"]
    min_gb = minimo_espaco_gb if minimo_espaco_gb is not None else cfg_base["minimo_espaco_gb"]
    dur = duracao_maxima_horas if duracao_maxima_horas is not None else cfg_base["duracao_maxima_horas"]

    with _LOCK:
        if _SESSAO is not None and _SESSAO.ativa:
            return {
                "ok": False,
                "erro": "Ja existe sessao ativa",
                "estado": _SESSAO.estado(),
            }

        sessao_anterior = _SESSAO
        _SESSAO = None

        if sessao_anterior is not None:
            try:
                sessao_anterior.parar_evento.set()
                if (
                    sessao_anterior.thread is not None
                    and sessao_anterior.thread.is_alive()
                ):
                    try:
                        if sessao_anterior.captura is not None:
                            sessao_anterior.captura.release()
                    except Exception:
                        pass
                    sessao_anterior.thread.join(timeout=5)
            except Exception:
                pass

            try:
                sessao_anterior._encerrar_recursos()
            except Exception:
                pass

        nova_sessao = SessaoGravacao(
            indice_camera=ind,
            resolucao=res,
            fps=f,
            crf=c,
            timeout_sem_atividade=to,
            pasta=p,
            minimo_espaco_gb=min_gb,
            duracao_maxima_horas=dur,
        )

        try:
            nova_sessao.iniciar()
        except Exception as exc:
            try:
                nova_sessao._encerrar_recursos()
            except Exception:
                pass
            return {"ok": False, "erro": str(exc)}

        _SESSAO = nova_sessao
        return {"ok": True, "estado": nova_sessao.estado()}


def pausar_sessao() -> Dict[str, Any]:
    """Para de GRAVAR sem encerrar a sessao nem soltar a camera.

    O arquivo continua aberto e o preview segue vivo — util para interromper
    enquanto atende o telefone, sem picotar o video em varios arquivos.
    """
    with _LOCK:
        if _SESSAO is None or not _SESSAO.ativa:
            return {"ok": False, "erro": "Nenhuma gravacao ativa."}
        if _SESSAO.pausado_evento.is_set():
            return {"ok": True, "pausado": True, "aviso": "Ja estava pausada."}

        _SESSAO.pausado_evento.set()
        _SESSAO._pausa_iniciada_em = time.monotonic()
        return {"ok": True, "pausado": True,
                "arquivo": _SESSAO.arquivo_video,
                "quadros_gravados": _SESSAO.quadros_gravados}


def retomar_sessao() -> Dict[str, Any]:
    """Volta a gravar no MESMO arquivo depois de uma pausa."""
    with _LOCK:
        if _SESSAO is None or not _SESSAO.ativa:
            return {"ok": False, "erro": "Nenhuma gravacao ativa."}
        if not _SESSAO.pausado_evento.is_set():
            return {"ok": True, "pausado": False, "aviso": "Nao estava pausada."}

        if _SESSAO._pausa_iniciada_em is not None:
            _SESSAO.segundos_pausados += time.monotonic() - _SESSAO._pausa_iniciada_em
            _SESSAO._pausa_iniciada_em = None

        _SESSAO.pausado_evento.clear()
        # Sem isto o watchdog de inatividade poderia matar a sessao logo apos
        # uma pausa longa, achando que a bancada foi abandonada.
        _SESSAO.ultima_atividade_monotonic = time.monotonic()
        return {"ok": True, "pausado": False,
                "segundos_pausados": round(_SESSAO.segundos_pausados, 1)}


def parar_sessao(motivo: str = "manual") -> Dict[str, Any]:
    global _SESSAO

    with _LOCK:
        sessao = _SESSAO
        if sessao is None or not sessao.ativa:
            return {"ok": False, "erro": "Nenhuma sessao ativa"}

        sessao.motivo_parada = motivo
        sessao.parar_evento.set()
        sessao.ativa = False

    if sessao.thread is not None and sessao.thread.is_alive():
        try:
            if sessao.captura is not None:
                sessao.captura.release()
        except Exception:
            pass
        sessao.thread.join(timeout=8)

    with _LOCK:
        if _SESSAO is sessao:
            _SESSAO = None

        try:
            sessao._encerrar_recursos()
        except Exception:
            pass

        return {"ok": True, "estado": sessao.estado()}


def estado_sessao() -> Dict[str, Any]:
    with _LOCK:
        if _SESSAO is None:
            return {"sessao_ativa": False}
        return _SESSAO.estado()


def sinalizar_atividade() -> Dict[str, Any]:
    """Marca atividade na sessao ativa, adiando o desligamento por inatividade."""
    with _LOCK:
        if _SESSAO is None or not _SESSAO.ativa:
            return {"ok": False, "erro": "Nenhuma sessao ativa"}
        _SESSAO.ultima_atividade_monotonic = time.monotonic()
        return {"ok": True}


def sinalizar_atividade_e_obter_indice(
    tracking: Optional[str] = None,
    auto_iniciar: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Funcao principal de integracao com a bipagem.
    Retorna video_arquivo e video_segundo sem nunca travar a operacao.
    """
    global _SESSAO

    with _LOCK:
        if _SESSAO is not None and _SESSAO.ativa:
            _SESSAO.ultima_atividade_monotonic = time.monotonic()
            return _montar_indice(_SESSAO, tracking)

        if not auto_iniciar:
            return {"sessao_ativa": False, "erro": "Nenhuma sessao ativa"}

        cfg = carregar_camera_config()
        if config:
            cfg.update(config)

        try:
            resultado = iniciar_sessao(
                indice_camera=cfg["indice_camera"],
                resolucao=cfg["resolucao"],
                fps=cfg["fps"],
                crf=cfg["crf"],
                timeout_sem_atividade=cfg["timeout_sem_atividade"],
                pasta=cfg["pasta"],
                minimo_espaco_gb=cfg["minimo_espaco_gb"],
                duracao_maxima_horas=cfg["duracao_maxima_horas"],
            )
        except Exception as exc:
            return {"sessao_ativa": False, "erro": str(exc)}

        if not resultado.get("ok") or _SESSAO is None or not _SESSAO.ativa:
            return {
                "sessao_ativa": False,
                "erro": resultado.get("erro") if resultado else "sem_sessao",
            }

        _SESSAO.ultima_atividade_monotonic = time.monotonic()
        return _montar_indice(
            _SESSAO,
            tracking,
            aviso="Sessao iniciada automaticamente pela bipagem",
        )


def frame_atual_jpeg(qualidade: int = 55, largura_maxima: int = 640) -> Optional[bytes]:
    """Ultimo frame da gravacao em JPEG, para mostrar na tela.

    ⚠️ Existe porque gravar as cegas nao da' para ajustar: sem ver o
    enquadramento, nao se sabe se a camera pegou a bancada ou o teto
    (Jota, 2026-08-16 — "precisa mostrar o que esta sendo gravado, nao mostra
    assim nao tem como ajustar o foco de local").

    Devolve None quando nao ha sessao ativa ou nenhum frame chegou ainda.
    """
    with _LOCK:
        if _SESSAO is None or not _SESSAO.ativa or _SESSAO.ultimo_frame is None:
            return None
        frame = _SESSAO.ultimo_frame.copy()

    if cv2 is None:
        return None

    try:
        # ⚡ Reduz antes de comprimir: o preview so' precisa mostrar o
        # enquadramento, e um JPEG 1280px pesa ~4x mais para trafegar pelo
        # websocket a cada atualizacao — era parte do atraso percebido.
        altura, largura = frame.shape[:2]
        if largura_maxima and largura > largura_maxima:
            escala = largura_maxima / float(largura)
            frame = cv2.resize(
                frame, (largura_maxima, int(altura * escala)),
                interpolation=cv2.INTER_AREA)

        ok, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(qualidade)])
        return buffer.tobytes() if ok else None
    except Exception:
        return None


def capturar_print(tracking: Optional[str] = None, pasta_prints: Optional[str] = None) -> str:
    """Salva um frame JPEG instantaneo da sessao ativa no momento da bipagem."""
    with _LOCK:
        if _SESSAO is None or not _SESSAO.ativa or _SESSAO.ultimo_frame is None:
            return ""
        frame = _SESSAO.ultimo_frame.copy()
        pasta_base = pasta_prints or os.path.join(_SESSAO.pasta, "prints")

    try:
        os.makedirs(pasta_base, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        track_clean = _sanitize_filename(tracking or "avulso")
        nome_arq = f"print_{track_clean}_{ts}.jpg"
        caminho_arq = os.path.join(pasta_base, nome_arq)
        if cv2 is not None:
            cv2.imwrite(caminho_arq, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            return nome_arq
    except Exception:
        pass
    return ""


def _sanitize_filename(valor: str) -> str:

    if not valor:
        return "sem_tracking"
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(valor))


def _buscar_indice(
    tracking: str, caminho_banco: str
) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(caminho_banco, timeout=10)
    try:
        cur = conn.execute(
            """
            SELECT video_arquivo, video_segundo
              FROM conferencias
             WHERE tracking = ?
               AND video_arquivo IS NOT NULL
               AND video_arquivo != ''
               AND video_segundo IS NOT NULL
             ORDER BY id DESC
             LIMIT 1
            """,
            (tracking,),
        )
        linha = cur.fetchone()
        if linha is None:
            return None
        return {"video_arquivo": linha[0], "video_segundo": linha[1]}
    finally:
        conn.close()


def extrair_trecho(
    tracking: str,
    margem_antes: int = 15,
    margem_depois: int = 15,
    pasta_saida: Optional[str] = None,
    pasta_videos: Optional[str] = None,
    caminho_banco: Optional[str] = None,
) -> Dict[str, Any]:
    """Corta trecho de 30s correspondente a uma bipagem para defesa de disputa."""
    if not shutil.which("ffmpeg"):
        return {"ok": False, "erro": "Ffmpeg nao encontrado no PATH"}

    caminho_banco = caminho_banco or CAMINHO_BANCO_DEFAULT
    pasta_videos = pasta_videos or PASTA_VIDEO_DEFAULT
    pasta_saida = pasta_saida or os.path.join(pasta_videos, "cortes")

    try:
        registro = _buscar_indice(tracking, caminho_banco)
    except sqlite3.Error as exc:
        return {"ok": False, "erro": f"Falha no banco: {exc}"}

    if not registro:
        return {
            "ok": False,
            "erro": f"Sem indice de video para tracking {tracking}",
        }

    video_arquivo = os.path.basename(registro["video_arquivo"])
    caminho_origem = os.path.join(pasta_videos, video_arquivo)
    if not os.path.isfile(caminho_origem):
        return {
            "ok": False,
            "erro": f"Arquivo de video nao encontrado: {caminho_origem}",
        }

    try:
        video_segundo = int(registro["video_segundo"])
    except (TypeError, ValueError):
        return {"ok": False, "erro": "video_segundo invalido no banco"}

    try:
        inicio = max(0, video_segundo - int(margem_antes))
        duracao = int(margem_antes) + int(margem_depois)
    except (TypeError, ValueError):
        return {"ok": False, "erro": "Margens invalidas"}

    os.makedirs(pasta_saida, exist_ok=True)
    timestamp_saida = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tracking_limpo = _sanitize_filename(tracking)
    nome_saida = f"corte_{tracking_limpo}_{timestamp_saida}.mp4"
    caminho_saida = os.path.join(pasta_saida, nome_saida)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        str(inicio),
        "-i",
        caminho_origem,
        "-t",
        str(duracao),
        "-c:v",
        "copy",
        "-an",
        "-movflags",
        "+faststart",
        caminho_saida,
    ]

    try:
        resultado = subprocess.run(cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "erro": "Ffmpeg excedeu 180s no corte"}

    if resultado.returncode != 0 or not os.path.isfile(caminho_saida):
        stderr = (
            resultado.stderr.decode("utf-8", errors="replace")
            if resultado.stderr
            else ""
        )
        return {
            "ok": False,
            "erro": "Ffmpeg nao gerou corte",
            "stderr": stderr[-2000:],
        }

    return {
        "ok": True,
        "video_arquivo": video_arquivo,
        "video_segundo": video_segundo,
        "inicio": inicio,
        "duracao": duracao,
        "saida": caminho_saida,
        "comando": " ".join(cmd),
    }


def limpar_videos_antigos(
    dias: int = 30,
    pasta: Optional[str] = None,
) -> Dict[str, Any]:
    """Remove videos fora da janela de retencao — SO os deste modulo.

    30 dias: janela definida pelo Jota (2026-08-16). Com ~2h/dia em 720p da'
    ~60 GB no pior caso; o drive E: tem folga para isso.

    🔴 Apaga SOMENTE `expedicao_*.mp4`. Os `PROVA_*.mp4` sao do gravador antigo
    (tools/gravador_webcam_prova.py) — mais simples, menos peca movel, e por
    isso o BACKUP confiavel. Este modulo nao mexe neles: se a automacao daqui
    falhar, o PROVA_* continua sendo a prova.
    """
    pasta = pasta or PASTA_VIDEO_DEFAULT
    if not os.path.isdir(pasta):
        return {
            "ok": True,
            "pasta": pasta,
            "removidos": [],
            "bytes_removidos": 0,
        }

    limite = time.time() - (int(dias) * 86400)
    removidos = []
    bytes_removidos = 0

    with _LOCK:
        nome_base_ativo = None
        if _SESSAO is not None:
            if _SESSAO.ativa or (
                _SESSAO.thread is not None and _SESSAO.thread.is_alive()
            ):
                nome_base_ativo = _SESSAO.nome_base

    # 🔴 NAO incluir PROVA_*.mp4 aqui. Esses vem do gravador antigo
    # (tools/gravador_webcam_prova.py), que e' o backup simples e confiavel do
    # Jota. Este modulo so' gerencia os proprios arquivos (expedicao_*).
    for caminho in Path(pasta).glob("expedicao_*.mp4"):
        if nome_base_ativo and caminho.name.startswith(nome_base_ativo):
            continue

        try:
            stat = caminho.stat()
        except OSError:
            continue

        if stat.st_mtime < limite:
            try:
                bytes_removidos += stat.st_size
                os.remove(caminho)
                removidos.append(str(caminho))
            except OSError:
                pass

    for caminho in Path(pasta).glob("expedicao_*_ffmpeg.log"):
        if nome_base_ativo and caminho.name.startswith(nome_base_ativo):
            continue

        try:
            stat = caminho.stat()
        except OSError:
            continue

        if stat.st_mtime < limite:
            try:
                os.remove(caminho)
                removidos.append(str(caminho))
            except OSError:
                pass

    return {
        "ok": True,
        "pasta": pasta,
        "removidos": removidos,
        "bytes_removidos": bytes_removidos,
    }


def _parar_na_saida() -> None:
    try:
        parar_sessao(motivo="saida_processo")
    except Exception:
        pass


atexit.register(_parar_na_saida)
