"""Junta as etapas: link -> pasta com os cortes prontos.

Cada etapa grava seu resultado em cache/<id do vídeo>/; se o processo for
interrompido, a próxima execução continua da última etapa concluída.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import baixar, config, llm, renderizar, saida, selecionar, silencio, thumbnail, transcrever
from .config import log
from .erros import ErroAmigavel

# (mensagem mostrada na tela, fração da barra onde a etapa começa)
ETAPAS = {
    "baixar": ("Baixando vídeo...", 0.00),
    "transcrever": ("Lendo a conversa...", 0.12),
    "selecionar": ("Escolhendo os melhores momentos...", 0.20),
    "cortar": ("Cortando vídeos...", 0.65),
    "thumbs": ("Criando thumbnails...", 0.93),
    "fim": ("Pronto!", 1.00),
}
_ORDEM = list(ETAPAS)
_ocupado = threading.Lock()

Progresso = Callable[[float, str, str], None]  # (fração total 0-1, etapa, detalhe)


def _sub(progresso: Progresso, etapa: str) -> Callable[[float, str], None]:
    """Converte o progresso de uma etapa (0-1) em progresso total."""
    ini = ETAPAS[etapa][1]
    fim = ETAPAS[_ORDEM[_ORDEM.index(etapa) + 1]][1]

    def f(fracao: float, detalhe: str = ""):
        progresso(ini + (fim - ini) * max(0.0, min(1.0, fracao)), ETAPAS[etapa][0], detalhe)

    return f


def _conferir_disco() -> None:
    livre = shutil.disk_usage(config.RAIZ).free / 1024**3
    if livre < 4:
        raise ErroAmigavel(
            f"Há pouco espaço livre no disco ({livre:.1f} GB). Libere pelo menos 4 GB — "
            "você pode apagar a pasta “cache” (vídeos originais já baixados) — e tente de novo.")


def _pasta_thumbs(info: dict, c: dict) -> Path:
    return Path(info["video"]).parent / "thumbs" / f"{int(c['ini'])}_{int(c['fim'])}"


def processar(url: str, opcoes: dict | None = None, progresso: Progresso = lambda f, e, d: None) -> Path:
    """Executa tudo e devolve a pasta de saída do vídeo."""
    opcoes = opcoes or {}
    if not _ocupado.acquire(blocking=False):
        raise ErroAmigavel("Já estou trabalhando em um vídeo. Espere terminar para começar outro.")
    t0 = time.time()
    try:
        config.criar_pastas()
        _conferir_disco()
        log.info("=== Novo pedido: %s | opções %s ===", url, opcoes)

        # 1. download
        p = _sub(progresso, "baixar")
        p(0.0, "")
        info = baixar.baixar(url, p)
        video = Path(info["video"])

        # 2. transcrição + silêncios
        p = _sub(progresso, "transcrever")
        p(0.0, "")
        transcricao = transcrever.transcrever(info, lambda f, m: p(f * 0.9, m))
        silencios = silencio.detectar(video, video.parent / "silencios.json")
        p(1.0, "")

        # 3. seleção dos momentos
        p = _sub(progresso, "selecionar")
        p(0.0, "")
        cortes = selecionar.selecionar(info, transcricao, silencios, opcoes, p)

        # 4. render
        assinatura = hashlib.sha1(json.dumps([[c["ini"], c["fim"]] for c in cortes]).encode()).hexdigest()[:12]
        pasta_video = saida.pasta_do_video(info, assinatura)
        registro = saida.ler_registro(pasta_video) or {
            "video_id": info["id"], "assinatura": assinatura,
            "info": {k: info[k] for k in ("id", "titulo", "canal", "canal_url", "url", "duracao")},
            "fonte_transcricao": transcricao["fonte"], "modelo": llm.modelo(), "cortes": [],
        }
        antigos = {c["n"]: c for c in registro["cortes"]}
        registro["cortes"] = []
        for c in cortes:
            velho = antigos.get(c["n"], {})
            item = dict(c)
            item["pasta"] = velho.get("pasta") or f"{c['n']:02d}_{saida.slug(c['titulo'])}"
            for chave in ("renderizado", "excluido", "variante", "thumbnails", "thumb"):
                if chave in velho:
                    item[chave] = velho[chave]
            registro["cortes"].append(item)
        saida.gravar_registro(pasta_video, registro)

        ativos = [c for c in registro["cortes"] if not c.get("excluido")]
        dados = renderizar.sonda(video)
        p = _sub(progresso, "cortar")
        total_seg = sum(c["duracao"] for c in ativos) or 1.0
        feito_seg = 0.0
        for k, c in enumerate(ativos, 1):
            destino = pasta_video / c["pasta"] / "corte.mp4"
            rotulo = f"Corte {k} de {len(ativos)}"
            if c.get("renderizado") and destino.exists():
                feito_seg += c["duracao"]
                continue
            saida.escrever_info(destino.parent, c, info)
            t1 = time.time()
            renderizar.cortar(
                video, c["ini"], c["fim"], destino, dados=dados,
                progresso=lambda f, c=c, rotulo=rotulo, base=feito_seg: p(
                    (base + f * c["duracao"]) / total_seg, f"{rotulo}: {f:.0%}"),
            )
            log.info("%s renderizado em %.0f s (%s).", rotulo, time.time() - t1, destino)
            feito_seg += c["duracao"]
            c["renderizado"] = True
            saida.gravar_registro(pasta_video, registro)
        p(1.0, "")

        # 5. thumbnails
        p = _sub(progresso, "thumbs")
        for k, c in enumerate(ativos, 1):
            p((k - 1) / len(ativos), f"Thumbnail {k} de {len(ativos)}")
            destino = pasta_video / c["pasta"] / "thumbnail.jpg"
            if destino.exists() and c.get("thumb"):
                continue
            c["variante"] = c.get("variante", 0)
            c["thumb"] = thumbnail.gerar(video, c["ini"], c["fim"], c["thumbnails"], destino,
                                         _pasta_thumbs(info, c), c["variante"])
            saida.gravar_registro(pasta_video, registro)

        # 6. info.txt (reescreve sempre: é barato e garante que está em dia)
        for c in ativos:
            saida.escrever_info(pasta_video / c["pasta"], c, info)
        quantos = "1 corte pronto" if len(ativos) == 1 else f"{len(ativos)} cortes prontos"
        progresso(1.0, ETAPAS["fim"][0], f"{quantos} em {_minutos(time.time() - t0)}.")
        log.info("=== Concluído em %s: %s ===", _minutos(time.time() - t0), pasta_video)
        return pasta_video
    except ErroAmigavel as e:
        log.error("Erro: %s | detalhe: %s", e.mensagem, e.detalhe)
        raise
    except Exception:
        log.exception("Erro inesperado")
        raise
    finally:
        _ocupado.release()


def _minutos(seg: float) -> str:
    m = int(seg // 60)
    return f"{m} min" if m >= 1 else f"{int(seg)} s"


def outra_thumbnail(pasta_video: Path, n: int) -> None:
    """Refaz a thumbnail do corte n com outro texto e/ou outro quadro."""
    pasta_video = Path(pasta_video)
    registro = saida.ler_registro(pasta_video)
    if not registro:
        raise ErroAmigavel("Não encontrei os dados deste vídeo. Gere os cortes de novo.")
    c = next((x for x in registro["cortes"] if x["n"] == n and not x.get("excluido")), None)
    if c is None:
        raise ErroAmigavel("Este corte não existe mais.")
    info = dict(registro["info"])
    pasta_cache = config.CACHE / registro["video_id"]
    info["video"] = str(pasta_cache / "video.mp4")
    c["variante"] = c.get("variante", 0) + 1

    # acabaram os textos? pede novos ao modelo (sem repetir os já usados)
    if c["variante"] % len(c["thumbnails"]) == 0:
        try:
            arq_t = pasta_cache / "transcricao.json"
            palavras = json.loads(arq_t.read_text(encoding="utf-8"))["palavras"] if arq_t.exists() else []
            llm.preparar()
            base = dict(c, titulo=c.get("titulo_candidato") or c["titulo"])
            novos = selecionar.gerar_textos(info | {"tags": []}, base, palavras, evitar=c["thumbnails"])
            extras = [t for t in novos["thumbnails"] if t not in c["thumbnails"]]
            c["thumbnails"] += extras
        except Exception as e:
            log.warning("Sem textos novos para a thumbnail (%r); reutilizando os existentes.", e)

    video, ini, fim = Path(info["video"]), c["ini"], c["fim"]
    cache_thumbs = _pasta_thumbs(info, c)
    if not video.exists() and not (cache_thumbs / "quadros.json").exists():
        video, ini, fim = pasta_video / c["pasta"] / "corte.mp4", 0.0, c["duracao"]  # cache apagado: usa o próprio corte
    c["thumb"] = thumbnail.gerar(video, ini, fim, c["thumbnails"], pasta_video / c["pasta"] / "thumbnail.jpg",
                                 cache_thumbs, c["variante"])
    saida.gravar_registro(pasta_video, registro)


def principal() -> None:
    """Uso pelo terminal: python -m cortes_ai.pipeline <link do YouTube>"""
    config.configurar_log()
    if len(sys.argv) < 2:
        print("Uso: python -m cortes_ai.pipeline <link do YouTube>")
        raise SystemExit(2)
    ultimo = [""]

    def mostrar(f, etapa, detalhe):
        linha = f"[{f:4.0%}] {etapa} {detalhe}"
        if linha != ultimo[0]:
            ultimo[0] = linha
            print(linha, flush=True)

    try:
        pasta = processar(sys.argv[1], {}, mostrar)
        print(f"\nCortes salvos em: {pasta}")
    except ErroAmigavel as e:
        print(f"\nERRO: {e.mensagem}")
        raise SystemExit(1)


if __name__ == "__main__":
    principal()
