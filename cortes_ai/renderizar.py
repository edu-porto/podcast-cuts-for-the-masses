"""Etapa 4 - cortar o vídeo com ffmpeg, recodificando para o corte ser exato no quadro.

Saída: H.264 + AAC, 16:9, na resolução do original, com fade de 0,3 s no áudio.
Usa a placa de vídeo para codificar quando ela funciona (NVIDIA, Intel, AMD, Apple);
se não, usa o processador (libx264).
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Callable

from . import config
from .config import log
from .erros import ErroAmigavel

FADE = 0.3

_CODIFICADORES = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "22", "-b:v", "0", "-pix_fmt", "yuv420p"],
    "h264_qsv": ["-c:v", "h264_qsv", "-global_quality", "22", "-preset", "medium", "-pix_fmt", "nv12"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23",
                 "-qp_b", "25", "-pix_fmt", "nv12"],
    "h264_videotoolbox": ["-c:v", "h264_videotoolbox", "-q:v", "62", "-pix_fmt", "yuv420p"],
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"],
}


def sonda(video: Path) -> dict:
    """Largura, altura, fps e duração de um vídeo (lendo a saída do ffmpeg)."""
    r = subprocess.run([config.ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **config.sem_janela())
    txt = r.stderr
    d = {"largura": 0, "altura": 0, "fps": 30.0, "duracao": 0.0, "tem_audio": "Audio:" in txt}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        d["duracao"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for linha in txt.splitlines():
        if "Video:" in linha and "attached pic" not in linha:
            m = re.search(r"\b(\d{2,5})x(\d{2,5})\b", linha.split("Video:")[1])
            if m:
                d["largura"], d["altura"] = int(m.group(1)), int(m.group(2))
            m = re.search(r"([\d.]+)\s*fps", linha)
            if m:
                d["fps"] = float(m.group(1))
            break
    return d


@lru_cache(maxsize=1)
def codificador() -> str:
    """Testa os codificadores de placa de vídeo com um vídeo de 1 s; se nenhum funcionar, usa libx264."""
    ordem = ["h264_videotoolbox"] if sys.platform == "darwin" else ["h264_nvenc", "h264_qsv", "h264_amf"]
    for nome in ordem:
        cmd = [config.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", "testsrc2=size=1920x1080:rate=30:duration=1", *_CODIFICADORES[nome], "-f", "null", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60, **config.sem_janela())
            if r.returncode == 0:
                log.info("Codificador de vídeo: %s (placa de vídeo).", nome)
                return nome
        except Exception:
            pass
    log.info("Codificador de vídeo: libx264 (processador).")
    return "libx264"


def _par(n: float) -> int:
    return int(round(n / 2)) * 2


def filtro_16x9(largura: int, altura: int) -> str:
    """Mantém a resolução do original; se não for 16:9, completa com faixas pretas."""
    if not largura or not altura:
        return "setsar=1"
    if abs(largura / altura - 16 / 9) < 0.012:
        return f"scale={_par(largura)}:{_par(altura)},setsar=1"
    if largura / altura > 16 / 9:
        w, h = _par(largura), _par(largura * 9 / 16)
    else:
        w, h = _par(altura * 16 / 9), _par(altura)
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"


def _rodar(cmd: list[str], duracao: float, progresso: Callable[[float], None]) -> tuple[int, str]:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as erros:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=erros, text=True, encoding="utf-8",
                             errors="replace", **config.sem_janela())
        try:
            for linha in p.stdout:
                if linha.startswith("out_time_us=") or linha.startswith("out_time_ms="):
                    try:
                        us = int(linha.split("=")[1])
                        progresso(max(0.0, min(1.0, us / 1e6 / duracao)))
                    except ValueError:
                        pass
            p.wait()
        except BaseException:
            p.kill()
            raise
        erros.seek(0)
        return p.returncode, erros.read()[-3000:]


def cortar(video: Path, inicio: float, fim: float, destino: Path,
           progresso: Callable[[float], None] = lambda f: None, dados: dict | None = None) -> None:
    """Gera destino (mp4) com o trecho [inicio, fim] do vídeo."""
    dados = dados or sonda(video)
    dur = fim - inicio
    tmp = destino.with_name(destino.stem + ".parcial.mp4")
    destino.parent.mkdir(parents=True, exist_ok=True)

    def comando(cod: str) -> list[str]:
        cmd = [config.ffmpeg(), "-hide_banner", "-y", "-nostats", "-loglevel", "error", "-progress", "pipe:1",
               "-ss", f"{inicio:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
               "-map", "0:v:0", "-vf", filtro_16x9(dados["largura"], dados["altura"]), *_CODIFICADORES[cod]]
        if dados.get("tem_audio", True):
            cmd += ["-map", "0:a:0", "-af",
                    f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(0.0, dur - FADE):.3f}:d={FADE}",
                    "-c:a", "aac", "-b:a", "160k"]
        cmd += ["-movflags", "+faststart", "-max_muxing_queue_size", "2048", str(tmp)]
        return cmd

    cod = codificador()
    codigo, erro = _rodar(comando(cod), dur, progresso)
    if codigo != 0 and cod != "libx264":
        log.warning("Codificador %s falhou (%s); tentando de novo com libx264.", cod, erro[-300:])
        codigo, erro = _rodar(comando("libx264"), dur, progresso)
    if codigo != 0 or not tmp.exists() or tmp.stat().st_size < 50_000:
        tmp.unlink(missing_ok=True)
        if "No space left" in erro:
            raise ErroAmigavel("O disco encheu enquanto eu cortava o vídeo. Libere espaço e tente de novo.", erro)
        raise ErroAmigavel("Não consegui cortar um dos vídeos. Veja os detalhes em logs/cortes_ai.log.", erro)
    tmp.replace(destino)
    progresso(1.0)
