"""Etapa 6 - pastas de saída, info.txt e o registro (cortes_ai.json) de cada vídeo.

output/<título do vídeo>/
    cortes_ai.json            registro usado pela tela (pode ignorar)
    01_<titulo-do-corte>/corte.mp4, thumbnail.jpg, info.txt
    02_.../
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from . import config
from .config import log

REGISTRO = "cortes_ai.json"
_PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def nome_de_pasta(titulo: str, limite: int = 70) -> str:
    """Título do vídeo -> nome de pasta válido no Windows, Mac e Linux."""
    t = _PROIBIDOS.sub(" ", unicodedata.normalize("NFC", titulo))
    t = re.sub(r"\s+", " ", t).strip(" .")
    if len(t) > limite:  # corta em fim de palavra
        t = t[:limite]
        if " " in t[limite // 2:]:
            t = t[:t.rfind(" ")]
    return (t.rstrip(" .-|") or "video")


def slug(titulo: str, limite: int = 48) -> str:
    """'O Dia em que Quase Desisti!' -> 'o-dia-em-que-quase-desisti'"""
    s = unicodedata.normalize("NFD", titulo.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > limite:
        s = s[:limite]
        if "-" in s:
            s = s[:s.rfind("-")]
    return s or "corte"


def pasta_do_video(info: dict, assinatura: str) -> Path:
    """Pasta de saída do vídeo. Se já existir com OUTRA seleção de cortes, cria '<título> (2)'."""
    base = nome_de_pasta(info["titulo"])
    for n in range(1, 50):
        p = config.SAIDA / (base if n == 1 else f"{base} ({n})")
        reg = ler_registro(p)
        if not p.exists() or reg is None and not any(p.iterdir()):
            return p
        if reg and reg.get("video_id") == info["id"] and reg.get("assinatura") == assinatura:
            return p
    return config.SAIDA / f"{base} ({info['id']})"


def ler_registro(pasta: Path) -> dict | None:
    try:
        return json.loads((pasta / REGISTRO).read_text(encoding="utf-8"))
    except Exception:
        return None


def gravar_registro(pasta: Path, registro: dict) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    tmp = pasta / (REGISTRO + ".tmp")
    tmp.write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(pasta / REGISTRO)


def texto_info(corte: dict, info: dict) -> str:
    """Conteúdo do info.txt, pronto para copiar e colar no YouTube.

    Formato definido pelas regras de docs/seo_youtube.md: título principal + 2 alternativas (para o
    "Testar e comparar"), descrição com gancho nas primeiras linhas, resumo, créditos com o marcador
    [@CANAL_ORIGINAL], 3 hashtags no fim e poucas tags.
    """
    from .publicacao import PLACEHOLDER_CANAL, TIPOS, montar_hashtags

    titulos = corte.get("titulos") or [{"texto": corte["titulo"], "tipo": "busca"}]
    chamada = corte.get("chamada") or ""
    resumo = corte.get("resumo_publicacao") or corte.get("descricao") or corte.get("resumo", "")
    hashtags = corte.get("hashtags") or montar_hashtags(corte.get("convidado", ""), info.get("canal", ""),
                                                        corte.get("palavra_chave", ""), corte.get("tags") or [])
    podcast = re.sub(r"#(?=\d)", "", info["titulo"])  # "PODCAST #45" viraria uma hashtag a mais
    creditos = [
        f"🎙️ Corte do podcast: {podcast}",
        f"📺 Canal original: {info['canal']} {PLACEHOLDER_CANAL}",
    ]
    if info.get("canal_url"):
        creditos.append(f"🔗 Link do canal: {info['canal_url']}")
    creditos += [
        f"▶️ Assista ao episódio completo: {info['url']}",
        "",
        "Todos os créditos do conteúdo pertencem ao canal original.",
    ]
    blocos = (chamada, resumo, "\n".join(creditos), " ".join(hashtags))
    descricao = "\n\n".join(parte for parte in blocos if parte)

    linhas = ["TÍTULO", titulos[0]["texto"], ""]
    if len(titulos) > 1:
        linhas.append("TÍTULOS ALTERNATIVOS")
        linhas.append("(no YouTube Studio, em “Testar e comparar”, dá para testar os 3 títulos; "
                      "vence o que tiver mais tempo de exibição)")
        for n, t in enumerate(titulos[1:], 2):
            nota = TIPOS.get(t.get("tipo"), "alternativa")
            if any("fala do corte" in a for a in t.get("avisos") or []):
                nota += " — ATENÇÃO: usa palavras que não aparecem na fala; confira se é verdade antes de usar"
            linhas.append(f"Opção {n} — {nota}:")
            linhas.append(t["texto"])
        linhas.append("")
    linhas += [
        "DESCRIÇÃO",
        f"(ANTES DE PUBLICAR: troque {PLACEHOLDER_CANAL} pelo @ do canal original, por exemplo @nomedocanal)",
        "",
        descricao,
        "",
        "TAGS",
        ", ".join(corte["tags"]),
        "",
    ]
    return "\n".join(linhas)


def escrever_info(pasta_corte: Path, corte: dict, info: dict) -> None:
    pasta_corte.mkdir(parents=True, exist_ok=True)
    (pasta_corte / "info.txt").write_text(texto_info(corte, info), encoding="utf-8")


def listar_videos() -> list[tuple[str, str]]:
    """[(título, caminho da pasta)] dos vídeos já processados, do mais recente para o mais antigo."""
    achados = []
    if config.SAIDA.exists():
        for p in config.SAIDA.iterdir():
            reg = p / REGISTRO
            if p.is_dir() and reg.exists():
                achados.append((reg.stat().st_mtime, p.name, str(p)))
    achados.sort(reverse=True)
    return [(nome, caminho) for _, nome, caminho in achados]


def abrir_pasta(caminho: Path) -> None:
    caminho = Path(caminho)
    if not caminho.exists():
        return
    try:
        if os.name == "nt":
            os.startfile(str(caminho))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(caminho)])
        else:
            subprocess.Popen(["xdg-open", str(caminho)])
    except Exception as e:
        log.warning("Não consegui abrir a pasta %s: %r", caminho, e)


def excluir_corte(pasta_video: Path, n: int) -> None:
    """Manda a pasta do corte para a Lixeira (dá para recuperar) e tira do registro."""
    reg = ler_registro(pasta_video)
    if not reg:
        return
    for c in reg["cortes"]:
        if c["n"] == n and not c.get("excluido"):
            alvo = pasta_video / c["pasta"]
            if alvo.exists():
                try:
                    from send2trash import send2trash

                    send2trash(str(alvo))
                except Exception as e:
                    log.warning("Lixeira indisponível (%r); apagando a pasta direto.", e)
                    shutil.rmtree(alvo, ignore_errors=True)
            c["excluido"] = True
            log.info("Corte %02d excluído (%s).", n, c["pasta"])
    gravar_registro(pasta_video, reg)
