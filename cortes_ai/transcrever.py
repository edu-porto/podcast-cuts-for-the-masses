"""Etapa 2 - obter a transcrição em português, com o tempo de cada palavra.

Ordem de preferência:
  1. legendas manuais do YouTube (pt-BR / pt)
  2. legendas automáticas do YouTube (faixa original em português)
  3. transcrição local com faster-whisper (só se não houver legendas)

Formato salvo em cache/<id>/transcricao.json:
  {"fonte": "...", "tem_pontuacao": bool, "palavras": [[inicio, fim, "texto"], ...]}
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

from . import config
from .baixar import opcoes_base
from .config import log
from .erros import ErroAmigavel

_RUIDO = re.compile(r"\[[^\]]*\]|♪|>>")
# fim de frase: ponto, exclamação ou interrogação — mas NÃO reticências ("através..."), que indicam fala em aberto
_FIM_FRASE = re.compile(r"(?<![.…])[.!?][\"”')\]]*$")


# --------------------------------------------------------------------------- #
# Legendas do YouTube
# --------------------------------------------------------------------------- #
def _escolher_faixas(info: dict) -> list[tuple[str, str]]:
    """Lista [(tipo, idioma)] em ordem de preferência."""
    faixas: list[tuple[str, str]] = []
    manuais = [l for l in info.get("legendas_manuais", []) if l.lower().startswith("pt")]
    manuais.sort(key=lambda l: (l.lower() != "pt-br", l.lower() != "pt", l))
    faixas += [("manual", l) for l in manuais[:2]]
    autos = info.get("legendas_automaticas", [])
    originais = [l for l in autos if l.endswith("-orig")]
    if "pt-orig" in autos:
        faixas.append(("auto", "pt-orig"))
    elif not originais and "pt" in autos:
        faixas.append(("auto", "pt"))
    # se a faixa original for de outro idioma, o "pt" automático seria uma tradução: não serve.
    return faixas


def _baixar_faixa(url: str, pasta: Path, tipo: str, idioma: str) -> Path | None:
    import yt_dlp

    for velho in pasta.glob("legenda.*"):
        velho.unlink(missing_ok=True)
    opcoes = opcoes_base() | {
        "skip_download": True,
        "writesubtitles": tipo == "manual",
        "writeautomaticsub": tipo == "auto",
        "subtitleslangs": [idioma],
        "subtitlesformat": "json3",
        "outtmpl": str(pasta / "legenda.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            ydl.download([url])
    except Exception as e:
        log.warning("Não consegui baixar a legenda %s/%s: %s", tipo, idioma, e)
        return None
    achados = sorted(pasta.glob("legenda.*.json3"))
    return achados[0] if achados else None


def ler_json3(caminho: Path) -> list[list]:
    """Converte uma legenda json3 do YouTube em lista de palavras [inicio, fim, texto]."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    brutas: list[tuple[float, float | None, str]] = []  # (inicio, fim conhecido ou None, palavra)
    for ev in dados.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        t0 = ev.get("tStartMs", 0) / 1000.0
        dur = ev.get("dDurationMs", 0) / 1000.0
        por_palavra = any("tOffsetMs" in s or "acAsrConf" in s for s in segs)
        if por_palavra:  # legenda automática: cada trecho é uma palavra com seu tempo
            for s in segs:
                texto = _RUIDO.sub(" ", s.get("utf8", ""))
                ini = t0 + s.get("tOffsetMs", 0) / 1000.0
                for k, p in enumerate(texto.split()):
                    brutas.append((ini + 0.01 * k, None, p))
        else:  # legenda manual: uma linha inteira; distribui as palavras pelo tempo da linha
            texto = _RUIDO.sub(" ", "".join(s.get("utf8", "") for s in segs)).replace("\n", " ")
            palavras = [p for p in texto.split() if p not in ("-", "–", "—")]
            if not palavras or dur <= 0:
                continue
            pesos = [len(p) + 1 for p in palavras]
            total, acum = sum(pesos), 0
            for p, w in zip(palavras, pesos):
                ini = t0 + dur * acum / total
                acum += w
                brutas.append((ini, t0 + dur * acum / total, p))
    brutas.sort(key=lambda x: x[0])
    palavras_out: list[list] = []
    for i, (ini, fim, p) in enumerate(brutas):
        prox = brutas[i + 1][0] if i + 1 < len(brutas) else ini + 1.0
        if fim is None:
            fim = ini + 0.25 + 0.07 * len(p)  # duração estimada da fala
        fim = max(ini + 0.05, min(fim, prox))
        palavras_out.append([round(ini, 2), round(fim, 2), p])
    return palavras_out


def _legenda_aproveitavel(palavras: list[list], duracao: float) -> bool:
    if not palavras or not duracao:
        return False
    minutos = duracao / 60
    cobre = palavras[-1][0] >= 0.75 * duracao
    densa = len(palavras) >= 40 * minutos
    return cobre and densa


def tem_pontuacao(palavras: list[list]) -> bool:
    if not palavras:
        return False
    fins = sum(1 for p in palavras if _FIM_FRASE.search(p[2]))
    return fins / len(palavras) > 0.02


# --------------------------------------------------------------------------- #
# Whisper local
# --------------------------------------------------------------------------- #
def estimar_minutos_whisper(duracao: float) -> int:
    """Estimativa grosseira do tempo de transcrição local, em minutos."""
    modelo, dispositivo, _ = config.escolher_whisper()
    fator = 12.0 if dispositivo == "cuda" else 2.5  # vezes mais rápido que o tempo real
    return max(1, round(duracao / fator / 60))


def _carregar_audio(video: Path, inicio: float | None = None, duracao: float | None = None):
    """Áudio do vídeo (ou de um trecho dele) em 16 kHz mono (float32), extraído com o ffmpeg.

    (Mais robusto do que deixar o faster-whisper abrir o arquivo: não depende da
    versão da biblioteca PyAV e funciona com acentos no caminho da pasta.)
    """
    import subprocess

    import numpy as np

    cmd = [config.ffmpeg(), "-hide_banner", "-loglevel", "error"]
    if inicio is not None:
        cmd += ["-ss", f"{max(0.0, inicio):.3f}"]
    cmd += ["-i", str(video)]
    if duracao is not None:
        cmd += ["-t", f"{duracao:.3f}"]
    cmd += ["-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True, **config.sem_janela())
    if r.returncode != 0 or len(r.stdout) < 16000:
        raise ErroAmigavel("Não consegui ler o áudio deste vídeo para transcrever.",
                           r.stderr.decode("utf-8", "replace")[-500:])
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


_whisper: dict = {"modelo": None, "dispositivo": ""}


def carregar_whisper():
    """Carrega o modelo do Whisper uma vez só. Devolve (modelo, dispositivo)."""
    if _whisper["modelo"] is not None:
        return _whisper["modelo"], _whisper["dispositivo"]
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        raise ErroAmigavel("O transcritor de áudio não está instalado. Rode o instalador de novo.", repr(e))

    modelo, dispositivo, calculo = config.escolher_whisper()
    pasta_modelos = str(config.MODELOS / "whisper")
    try:
        try:
            wm = WhisperModel(modelo, device=dispositivo, compute_type=calculo, download_root=pasta_modelos)
        except Exception as e:
            if dispositivo == "cpu":
                raise
            log.warning("Whisper na placa de vídeo falhou (%s); usando o processador.", e)
            modelo, dispositivo, calculo = "small", "cpu", "int8"
            wm = WhisperModel(modelo, device=dispositivo, compute_type=calculo, download_root=pasta_modelos)
    except Exception as e:
        raise ErroAmigavel(
            "Não consegui carregar o transcritor de áudio. Na primeira vez ele precisa de internet "
            "para baixar o modelo (cerca de 500 MB). Verifique a conexão e tente de novo.",
            repr(e),
        )
    log.debug("Whisper carregado: modelo=%s dispositivo=%s", modelo, dispositivo)
    _whisper.update(modelo=wm, dispositivo=dispositivo)
    return wm, dispositivo


def descarregar_whisper() -> None:
    """Tira o Whisper da memória (ele é recarregado em 2 s quando for preciso de novo)."""
    _whisper.update(modelo=None, dispositivo="")
    import gc

    gc.collect()


# Texto de exemplo que induz o Whisper a escrever com pontuação (ele imita o estilo do texto anterior).
_ESTILO_PONTUADO = ("Bom, vamos lá. Você acha que isso faz sentido? Olha, eu acho que sim. "
                    "Mas tem um detalhe importante: nem todo mundo concorda. É verdade.")


def transcrever_trecho(video: Path, inicio: float, fim: float) -> list[list]:
    """Transcreve só o trecho [inicio, fim] do vídeo, com pontuação e tempo de cada palavra.

    Usado para achar o fim exato das frases perto do começo/fim de cada corte quando
    as legendas do YouTube vêm sem pontuação.
    """
    wm, dispositivo = carregar_whisper()
    audio = _carregar_audio(video, inicio, fim - inicio)
    segmentos, _ = wm.transcribe(
        audio, language="pt", word_timestamps=True, vad_filter=False,
        beam_size=5 if dispositivo == "cuda" else 2, condition_on_previous_text=False,
        initial_prompt=_ESTILO_PONTUADO,
    )
    palavras: list[list] = []
    for seg in segmentos:
        for w in seg.words or []:
            texto = w.word.strip()
            if texto:
                palavras.append([round(inicio + float(w.start), 2), round(inicio + float(w.end), 2), texto])
    return palavras


def transcrever_whisper(video: Path, duracao: float, progresso: Callable[[float, str], None]) -> list[list]:
    minutos = estimar_minutos_whisper(duracao)
    aviso = (f"Este vídeo não tem legendas. Transcrevendo o áudio no seu computador — "
             f"deve levar uns {minutos} minutos...")
    progresso(0.0, aviso)
    log.info("Whisper no vídeo inteiro (estimativa %d min)", minutos)
    wm, dispositivo = carregar_whisper()

    t0 = time.time()
    audio = _carregar_audio(video)
    segmentos, _ = wm.transcribe(
        audio, language="pt", word_timestamps=True, vad_filter=True,
        beam_size=5 if dispositivo == "cuda" else 2, condition_on_previous_text=False,
    )
    palavras: list[list] = []
    for seg in segmentos:
        for w in seg.words or []:
            texto = w.word.strip()
            if texto:
                palavras.append([round(float(w.start), 2), round(float(w.end), 2), texto])
        if duracao:
            feito = min(1.0, seg.end / duracao)
            gasto = time.time() - t0
            falta = gasto / feito * (1 - feito) / 60 if feito > 0.02 else minutos
            progresso(feito, f"Transcrevendo o áudio... faltam cerca de {max(1, round(falta))} min")
    log.info("Whisper terminou em %.0f s (%d palavras).", time.time() - t0, len(palavras))
    if not palavras:
        raise ErroAmigavel("Não encontrei fala neste vídeo. Ele precisa ter uma conversa em português.")
    return palavras


# --------------------------------------------------------------------------- #
# Etapa completa
# --------------------------------------------------------------------------- #
def transcrever(info: dict, progresso: Callable[[float, str], None] = lambda f, m: None) -> dict:
    """Devolve a transcrição (do cache, das legendas do YouTube ou do Whisper)."""
    pasta = Path(info["video"]).parent
    arq = pasta / "transcricao.json"
    if arq.exists():
        try:
            t = json.loads(arq.read_text(encoding="utf-8"))
            if t.get("palavras"):
                progresso(1.0, "Transcrição já pronta — aproveitando.")
                return t
        except Exception:
            pass

    progresso(0.0, "Procurando legendas no YouTube...")
    palavras, fonte = None, ""
    for tipo, idioma in _escolher_faixas(info):
        caminho = _baixar_faixa(info["url"], pasta, tipo, idioma)
        if not caminho:
            continue
        try:
            lidas = ler_json3(caminho)
        except Exception as e:
            log.warning("Legenda %s ilegível: %s", caminho.name, e)
            continue
        if _legenda_aproveitavel(lidas, info["duracao"]):
            palavras = lidas
            fonte = "legendas_manuais" if tipo == "manual" else "legendas_automaticas"
            log.info("Usando %s do YouTube (%s): %d palavras.", fonte, idioma, len(lidas))
            break
        log.info("Legenda %s/%s incompleta (%d palavras); ignorando.", tipo, idioma, len(lidas))

    if palavras is None:
        palavras = transcrever_whisper(Path(info["video"]), info["duracao"], progresso)
        fonte = "whisper"

    t = {"fonte": fonte, "tem_pontuacao": tem_pontuacao(palavras), "palavras": palavras}
    arq.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    progresso(1.0, "Transcrição pronta.")
    return t
