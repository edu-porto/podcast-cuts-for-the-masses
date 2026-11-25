"""Conversa com o Ollama (modelo de linguagem local).

Sempre com: num_ctx explícito, saída estruturada (JSON com esquema),
validação da resposta e novas tentativas quando ela vem malformada.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Callable

import requests

from . import config
from .config import log
from .erros import ErroAmigavel

_estado = {"think": True, "modelo": None}


def modelo() -> str:
    if not _estado["modelo"]:
        _estado["modelo"] = config.escolher_modelo_llm()
    return _estado["modelo"]


# --------------------------------------------------------------------------- #
# Servidor e modelo
# --------------------------------------------------------------------------- #
def no_ar() -> bool:
    try:
        return requests.get(config.OLLAMA_URL + "/api/version", timeout=2).ok
    except requests.RequestException:
        return False


def iniciar() -> None:
    """Liga o Ollama se ele ainda não estiver rodando."""
    if no_ar():
        return
    exe = config.ollama_exe()
    if not exe:
        raise ErroAmigavel(
            "O Ollama (programa que roda a inteligência artificial no seu computador) não está instalado. "
            "Rode o instalador (instalar.bat ou instalar.sh) ou baixe em https://ollama.com/download."
        )
    log.info("Ligando o Ollama...")
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, **config.sem_janela())
    except Exception as e:
        raise ErroAmigavel("Não consegui ligar o Ollama. Tente abrir o programa Ollama manualmente.", repr(e))
    for _ in range(60):
        time.sleep(0.5)
        if no_ar():
            return
    raise ErroAmigavel("O Ollama não respondeu. Reinicie o computador e tente de novo.")


def modelos_instalados() -> list[str]:
    r = requests.get(config.OLLAMA_URL + "/api/tags", timeout=10)
    r.raise_for_status()
    return [m.get("name", "") for m in r.json().get("models", [])]


def tem_modelo(nome: str | None = None) -> bool:
    nome = nome or modelo()
    alvo = nome if ":" in nome else nome + ":latest"
    return alvo in modelos_instalados()


def baixar_modelo(nome: str | None = None, progresso: Callable[[float, str], None] = lambda f, m: None) -> None:
    nome = nome or modelo()
    log.info("Baixando o modelo %s...", nome)
    try:
        with requests.post(config.OLLAMA_URL + "/api/pull", json={"model": nome, "stream": True},
                           stream=True, timeout=(10, 600)) as r:
            r.raise_for_status()
            camadas: dict[str, tuple[int, int]] = {}
            for linha in r.iter_lines():
                if not linha:
                    continue
                d = json.loads(linha)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                if d.get("digest") and d.get("total"):
                    camadas[d["digest"]] = (d.get("completed", 0), d["total"])
                    feito = sum(c for c, _ in camadas.values())
                    total = sum(t for _, t in camadas.values())
                    progresso(feito / total, f"Baixando a inteligência artificial (só na primeira vez): "
                                             f"{feito / 1e9:.1f} de {total / 1e9:.1f} GB")
    except Exception as e:
        raise ErroAmigavel(
            f"Não consegui baixar o modelo de IA “{nome}”. Verifique a internet e o espaço em disco "
            "(são necessários uns 8 GB livres) e tente de novo.", repr(e))


def preparar(progresso: Callable[[float, str], None] = lambda f, m: None) -> None:
    """Garante que o Ollama está ligado e o modelo baixado."""
    iniciar()
    if not tem_modelo():
        baixar_modelo(progresso=progresso)


# --------------------------------------------------------------------------- #
# Geração com JSON validado
# --------------------------------------------------------------------------- #
def _chamar(mensagens: list[dict], esquema: dict, temperatura: float, max_tokens: int) -> tuple[str, dict]:
    corpo = {
        "model": modelo(),
        "messages": mensagens,
        "stream": True,
        "format": esquema,
        "keep_alive": "20m",
        "options": {
            "num_ctx": config.NUM_CTX,
            "temperature": temperatura,
            "top_p": 0.9,
            "num_predict": max_tokens,
        },
    }
    if _estado["think"]:
        corpo["think"] = False  # modelos "pensantes": responder direto, sem raciocínio longo
    with requests.post(config.OLLAMA_URL + "/api/chat", json=corpo, stream=True, timeout=(10, 3600)) as r:
        if r.status_code == 400 and _estado["think"] and "think" in r.text.lower():
            _estado["think"] = False
            return _chamar(mensagens, esquema, temperatura, max_tokens)
        if r.status_code == 404:
            raise ErroAmigavel(
                f"O modelo de IA “{modelo()}” não está instalado no Ollama. Rode o instalador de novo.", r.text)
        r.raise_for_status()
        partes, final = [], {}
        for linha in r.iter_lines():
            if not linha:
                continue
            d = json.loads(linha)
            if d.get("error"):
                raise RuntimeError(d["error"])
            partes.append(d.get("message", {}).get("content", ""))
            if d.get("done"):
                final = d
    return "".join(partes), final


def gerar_json(sistema: str, usuario: str, esquema: dict, validar: Callable[[dict], dict], *,
               tentativas: int = 3, temperatura: float = 0.5, max_tokens: int = 1200, rotulo: str = "",
               cache: Path | None = None) -> dict:
    """Pede um JSON ao modelo, valida e tenta de novo se vier errado.

    validar(obj) deve devolver o objeto limpo ou levantar ValueError com o motivo.
    cache: arquivo .json onde as respostas válidas ficam guardadas; se a mesma pergunta já foi
           respondida (processo interrompido e retomado), a resposta guardada é reaproveitada.
    """
    chave, guardadas = "", {}
    if cache is not None:
        chave = hashlib.sha1(json.dumps([modelo(), sistema, usuario, esquema], sort_keys=True).encode()).hexdigest()
        try:
            guardadas = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            guardadas = {}
        if chave in guardadas:
            try:
                r = validar(guardadas[chave])
                log.debug("LLM %s: resposta reaproveitada do cache.", rotulo)
                return r
            except Exception:
                pass
    ultimo_erro = ""
    for n in range(1, tentativas + 1):
        texto_usuario = usuario
        if ultimo_erro:
            texto_usuario += (f"\n\nATENÇÃO: sua resposta anterior foi recusada ({ultimo_erro}). "
                              "Corrija e responda somente com o JSON no formato pedido.")
        mensagens = [{"role": "system", "content": sistema}, {"role": "user", "content": texto_usuario}]
        t0 = time.time()
        try:
            texto, final = _chamar(mensagens, esquema, temperatura + 0.15 * (n - 1), max_tokens)
        except ErroAmigavel:
            raise
        except requests.ConnectionError as e:
            raise ErroAmigavel(
                "Perdi a conexão com o Ollama (a inteligência artificial local). "
                "Feche outros programas pesados e clique em “Gerar cortes” de novo — o trabalho continua de onde parou.",
                repr(e))
        except Exception as e:
            ultimo_erro = f"erro de comunicação: {e}"
            log.warning("LLM %s tentativa %d falhou: %r", rotulo, n, e)
            if "memory" in str(e).lower():
                raise ErroAmigavel(
                    "Faltou memória para rodar a inteligência artificial. Feche outros programas "
                    "(principalmente o navegador com muitas abas) e tente de novo.", repr(e))
            continue
        pt, st = final.get("prompt_eval_count", 0), final.get("eval_count", 0)
        log.debug("LLM %s tentativa %d: %.0fs, %d tokens de entrada, %d de saída, fim=%s",
                  rotulo, n, time.time() - t0, pt, st, final.get("done_reason"))
        if pt and pt + max_tokens > config.NUM_CTX:
            log.warning("LLM %s: entrada de %d tokens perto do limite de contexto (%d).", rotulo, pt, config.NUM_CTX)
        log.debug("LLM %s resposta: %.2500s", rotulo, texto)
        try:
            obj = json.loads(texto)
            if not isinstance(obj, dict):
                raise ValueError("a resposta não é um objeto JSON")
            limpo = validar(obj)
            if cache is not None:
                guardadas[chave] = obj
                try:
                    cache.write_text(json.dumps(guardadas, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
            return limpo
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            ultimo_erro = str(e)[:200] if not isinstance(e, json.JSONDecodeError) else "JSON incompleto ou inválido"
            log.warning("LLM %s tentativa %d inválida (%s). Resposta: %.400s", rotulo, n, ultimo_erro, texto)
    raise ValueError(f"o modelo não devolveu uma resposta válida ({ultimo_erro})")
