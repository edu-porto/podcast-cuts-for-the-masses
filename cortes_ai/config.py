"""Caminhos, valores padrão e detecção de hardware."""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CACHE = RAIZ / "cache"
SAIDA = RAIZ / "output"
LOGS = RAIZ / "logs"
ASSETS = RAIZ / "assets"
MODELOS = RAIZ / "modelos"
ARQ_CONFIG = RAIZ / "configuracao.json"

FONTE = ASSETS / "Anton-Regular.ttf"
MODELO_ROSTO = ASSETS / "face_detection_yunet_2023mar.onnx"

OLLAMA_URL = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")

# Limites dos cortes (o usuário pode mudar em "Avançado")
DUR_MIN_PADRAO = 5 * 60
DUR_MAX_PADRAO = 30 * 60
DUR_MIN_ABSOLUTA = 5 * 60
DUR_MAX_ABSOLUTA = 30 * 60

# Janelas de transcrição enviadas ao modelo local
JANELA_SEG = 15 * 60
SOBREPOSICAO_SEG = 3 * 60
NUM_CTX = 8192

log = logging.getLogger("cortes_ai")

# Sem telemetria: tudo local.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # sem avisos técnicos do OpenCV na janela preta


def criar_pastas() -> None:
    for p in (CACHE, SAIDA, LOGS, MODELOS):
        p.mkdir(parents=True, exist_ok=True)


def configurar_log() -> None:
    """Grava tudo em logs/cortes_ai.log (até 3 arquivos de 2 MB)."""
    if log.handlers:
        return
    criar_pastas()
    for saida_padrao in (sys.stdout, sys.stderr):  # acentos corretos na janela do Windows
        try:
            saida_padrao.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # o Windows gera erros inofensivos quando o navegador interrompe o carregamento de um vídeo
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    log.setLevel(logging.DEBUG)
    arq = RotatingFileHandler(LOGS / "cortes_ai.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    arq.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(module)s] %(message)s"))
    arq.setLevel(logging.DEBUG)
    log.addHandler(arq)
    tela = logging.StreamHandler(sys.stdout)
    tela.setFormatter(logging.Formatter("%(message)s"))
    tela.setLevel(logging.INFO)
    log.addHandler(tela)
    log.propagate = False


def config_usuario() -> dict:
    """Lê configuracao.json (opcional). Ex.: {"modelo": "gemma4:12b"}."""
    try:
        return json.loads(ARQ_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Programas externos
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def ffmpeg() -> str:
    """Caminho do ffmpeg: o do sistema, ou o que vem junto com o pacote imageio-ffmpeg."""
    achado = shutil.which("ffmpeg")
    if achado:
        return achado
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        from .erros import ErroAmigavel

        raise ErroAmigavel(
            "Não encontrei o programa ffmpeg, necessário para cortar os vídeos. "
            "Rode o instalador de novo (instalar.bat ou instalar.sh).",
            repr(e),
        )


def ollama_exe() -> str | None:
    achado = shutil.which("ollama")
    if achado:
        return achado
    candidatos = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        candidatos.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    else:
        candidatos += [Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama"),
                       Path("/Applications/Ollama.app/Contents/Resources/ollama")]
    for c in candidatos:
        if c.exists():
            return str(c)
    return None


def sem_janela() -> dict:
    """Argumentos para o subprocess não abrir uma janela preta no Windows."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# --------------------------------------------------------------------------- #
# Hardware
# --------------------------------------------------------------------------- #
@dataclass
class Hardware:
    ram_gb: float
    vram_gb: float
    gpu_nome: str
    apple_silicon: bool
    cuda: bool

    @property
    def resumo(self) -> str:
        if self.vram_gb:
            gpu = f"placa de vídeo {self.gpu_nome} ({self.vram_gb:.0f} GB)"
        elif self.apple_silicon:
            gpu = "Apple Silicon"
        else:
            gpu = "sem placa de vídeo dedicada"
        return f"{self.ram_gb:.0f} GB de memória, {gpu}"


@lru_cache(maxsize=1)
def hardware() -> Hardware:
    import psutil

    ram = psutil.virtual_memory().total / 1024**3
    vram, nome = 0.0, ""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, **sem_janela(),
        )
        if r.returncode == 0:
            for linha in r.stdout.strip().splitlines():
                mem, _, n = linha.partition(",")
                if float(mem) / 1024 > vram:
                    vram, nome = float(mem) / 1024, n.strip()
    except Exception:
        pass
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    cuda = False
    if vram:
        try:
            import ctranslate2

            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            cuda = False
    return Hardware(round(ram, 1), round(vram, 1), nome, apple, cuda)


def escolher_modelo_llm(hw: Hardware | None = None) -> str:
    """Escolhe o modelo do Ollama conforme a máquina (ver README)."""
    forcado = os.environ.get("CORTES_MODELO") or config_usuario().get("modelo")
    if forcado:
        return forcado
    hw = hw or hardware()
    if hw.vram_gb >= 20 or (hw.apple_silicon and hw.ram_gb >= 48):
        return "qwen3.5:27b"
    if hw.ram_gb >= 11.5 or hw.vram_gb >= 8 or (hw.apple_silicon and hw.ram_gb >= 16):
        return "qwen3.5:9b"
    return "qwen3.5:4b"


def escolher_whisper(hw: Hardware | None = None) -> tuple[str, str, str]:
    """(modelo, dispositivo, tipo de cálculo) para o faster-whisper."""
    hw = hw or hardware()
    if hw.cuda:
        return "large-v3", "cuda", "float16"
    return "small", "cpu", "int8"


def faixa_de_cortes(duracao_seg: float) -> tuple[int, int]:
    """Quantos cortes gerar: (mínimo, máximo) conforme a duração do vídeo."""
    if duracao_seg < 3600:
        return 5, 6
    if duracao_seg <= 7200:
        return 6, 8
    return 8, 10
