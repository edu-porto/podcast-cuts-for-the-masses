"""Etapa 1 - baixar o vídeo e os metadados com yt-dlp (com cache por ID do vídeo)."""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Callable

from . import config
from .config import log
from .erros import ErroAmigavel

_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
DURACAO_MINIMA = 8 * 60


def extrair_id(url: str) -> str:
    """Devolve o ID de 11 caracteres do vídeo, ou explica o que há de errado com o link."""
    url = (url or "").strip()
    if not url:
        raise ErroAmigavel("Cole o link de um vídeo do YouTube antes de clicar em “Gerar cortes”.")
    if "youtube.com" not in url and "youtu.be" not in url:
        raise ErroAmigavel(
            "Esse link não parece ser do YouTube. Copie o endereço do vídeo no navegador "
            "(algo como https://www.youtube.com/watch?v=...) e cole aqui."
        )
    m = _ID_RE.search(url)
    if not m:
        raise ErroAmigavel(
            "Não consegui identificar o vídeo nesse link. Abra o vídeo no YouTube, "
            "copie o endereço da barra do navegador e cole aqui. "
            "(Links de canal ou de playlist inteira não funcionam.)"
        )
    return m.group(1)


def pasta_do_video(video_id: str) -> Path:
    p = config.CACHE / video_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _tem_internet() -> bool:
    for host in ("www.youtube.com", "1.1.1.1"):
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            continue
    return False


def traduzir_erro(exc: Exception) -> ErroAmigavel:
    """Transforma um erro do yt-dlp em uma explicação em português."""
    t = str(exc)
    tl = t.lower()
    if "private video" in tl or "video is private" in tl:
        msg = "Este vídeo é privado. Só é possível usar vídeos públicos do YouTube."
    elif "confirm your age" in tl or "age-restricted" in tl or "age restricted" in tl or "inappropriate for some users" in tl:
        msg = ("Este vídeo tem restrição de idade: o YouTube exige login para assistir, "
               "então não é possível baixá-lo. Escolha outro vídeo.")
    elif "members-only" in tl or "members only" in tl or "join this channel" in tl:
        msg = "Este vídeo é exclusivo para membros do canal e não pode ser baixado."
    elif "not a bot" in tl or "too many requests" in tl or "http error 429" in tl:
        msg = ("O YouTube bloqueou o download por alguns minutos (ele achou que havia acessos demais). "
               "Espere uns 15 minutos e tente de novo.")
    elif "live event will begin" in tl or "premieres in" in tl or "is live" in tl or "live stream" in tl:
        msg = "Este vídeo é uma transmissão ao vivo ou estreia que ainda não terminou. Tente depois que ela acabar."
    elif "video unavailable" in tl or "has been removed" in tl or "not available" in tl or "account associated" in tl:
        msg = "Este vídeo não está disponível: pode ter sido removido ou estar bloqueado no Brasil."
    elif "unsupported url" in tl or "not a valid url" in tl or "incomplete youtube id" in tl or "truncated" in tl:
        msg = "O link parece incompleto ou inválido. Copie de novo o endereço do vídeo no YouTube."
    elif any(s in tl for s in ("getaddrinfo", "name resolution", "timed out", "connection", "network is unreachable",
                               "urlopen error", "unable to download webpage", "transporterror")) and not _tem_internet():
        msg = "Sem conexão com a internet. Verifique sua rede e tente de novo."
    elif "no space left" in tl or "errno 28" in tl:
        msg = "O disco está cheio. Libere espaço (os vídeos baixados ficam na pasta “cache”) e tente de novo."
    elif not _tem_internet():
        msg = "Sem conexão com a internet. Verifique sua rede e tente de novo."
    else:
        msg = ("Não consegui baixar o vídeo. O YouTube muda com frequência; rode o instalador de novo "
               "(instalar.bat ou instalar.sh) para atualizar e tente outra vez. "
               "Se continuar, veja o arquivo logs/cortes_ai.log.")
    return ErroAmigavel(msg, t)


class _SilenciarLog:
    def debug(self, m):
        if not m.startswith("[debug]") and not m.startswith("[download]"):
            log.debug("yt-dlp: %s", m)

    def info(self, m):
        log.debug("yt-dlp: %s", m)

    def warning(self, m):
        log.debug("yt-dlp aviso: %s", m)

    def error(self, m):
        log.debug("yt-dlp erro: %s", m)


def opcoes_base() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "logger": _SilenciarLog(),
        "ffmpeg_location": config.ffmpeg(),
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
    }


def _resumir_info(info: dict) -> dict:
    def idiomas(d):
        return sorted((d or {}).keys())

    return {
        "id": info.get("id"),
        "titulo": info.get("title") or "Sem título",
        "canal": info.get("channel") or info.get("uploader") or "Canal desconhecido",
        "canal_url": info.get("channel_url") or info.get("uploader_url") or "",
        "duracao": float(info.get("duration") or 0),
        "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id')}",
        "capitulos": [
            {"inicio": float(c.get("start_time") or 0), "fim": float(c.get("end_time") or 0), "titulo": c.get("title") or ""}
            for c in (info.get("chapters") or [])
        ],
        "tags": (info.get("tags") or [])[:30],
        "legendas_manuais": idiomas(info.get("subtitles")),
        "legendas_automaticas": [k for k in idiomas(info.get("automatic_captions")) if k.startswith("pt") or k.endswith("-orig")],
        "ao_vivo": bool(info.get("is_live")),
    }


def obter_info(url: str) -> dict:
    """Consulta os metadados do vídeo sem baixar nada."""
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(opcoes_base()) as ydl:
            bruto = ydl.extract_info(url, download=False)
    except Exception as e:
        raise traduzir_erro(e) from e
    info = _resumir_info(bruto)
    if info["ao_vivo"]:
        raise ErroAmigavel("Este vídeo é uma transmissão ao vivo que ainda não terminou. Tente depois que ela acabar.")
    if info["duracao"] and info["duracao"] < DURACAO_MINIMA:
        raise ErroAmigavel(
            f"Este vídeo tem só {info['duracao'] / 60:.0f} minutos. Para gerar cortes de 5 minutos ou mais, "
            "use um vídeo com pelo menos 8 minutos (o ideal é um podcast de 1 hora ou mais)."
        )
    return info


def baixar(url: str, progresso: Callable[[float, str], None] = lambda f, m: None) -> dict:
    """Baixa o vídeo (até 1080p). Se já estiver no cache, não baixa de novo.

    Devolve o dicionário de informações do vídeo, com a chave "video" (caminho do arquivo).
    """
    import yt_dlp

    video_id = extrair_id(url)
    pasta = pasta_do_video(video_id)
    arq_info, arq_video = pasta / "info.json", pasta / "video.mp4"

    if arq_info.exists() and arq_video.exists() and arq_video.stat().st_size > 1_000_000:
        info = json.loads(arq_info.read_text(encoding="utf-8"))
        info["video"] = str(arq_video)
        log.info("Vídeo %s já estava no cache.", video_id)
        progresso(1.0, "Vídeo já baixado antes — aproveitando.")
        return info

    progresso(0.0, "Consultando o YouTube...")
    url_limpa = f"https://www.youtube.com/watch?v={video_id}"
    info = obter_info(url_limpa)
    arq_info.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("Baixando “%s” (%s, %.0f min)", info["titulo"], info["canal"], info["duracao"] / 60)

    estado = {"arquivos": 0}

    def gancho(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            feito = d.get("downloaded_bytes") or 0
            if total:
                # o vídeo vem primeiro (pesado), depois o áudio (leve)
                base = 0.0 if estado["arquivos"] == 0 else 0.9
                peso = 0.9 if estado["arquivos"] == 0 else 0.08
                vel = d.get("speed") or 0
                extra = f" ({vel / 1e6:.1f} MB/s)" if vel else ""
                progresso(min(0.98, base + peso * feito / total),
                          f"{feito / 1e6:.0f} de {total / 1e6:.0f} MB{extra}")
        elif d.get("status") == "finished":
            estado["arquivos"] += 1

    opcoes = opcoes_base() | {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "format_sort": ["res:1080", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "outtmpl": str(pasta / "video.%(ext)s"),
        "progress_hooks": [gancho],
        "continuedl": True,
    }
    try:
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            ydl.download([url_limpa])
    except Exception as e:
        raise traduzir_erro(e) from e

    if not arq_video.exists():
        # o yt-dlp pode ter gravado com outra extensão (mkv/webm)
        outros = [p for p in pasta.glob("video.*") if p.suffix.lower() in (".mkv", ".webm", ".mov")]
        if outros:
            outros[0].rename(arq_video)
    if not arq_video.exists():
        raise ErroAmigavel("O download terminou, mas o arquivo do vídeo não apareceu. Tente de novo.",
                           f"conteúdo da pasta: {[p.name for p in pasta.iterdir()]}")
    info["video"] = str(arq_video)
    progresso(1.0, "Vídeo baixado.")
    return info
