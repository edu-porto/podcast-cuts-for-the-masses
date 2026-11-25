"""Chamado pelo instalador: confere o ffmpeg, instala/confere o Ollama e baixa o modelo de IA.

Uso:  python -m cortes_ai.preparar
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time

from . import config
from .config import log
from .erros import ErroAmigavel


def _titulo(n: int, texto: str) -> None:
    print(f"\n[{n}/4] {texto}", flush=True)


def _conferir_ffmpeg() -> None:
    caminho = config.ffmpeg()
    r = subprocess.run([caminho, "-hide_banner", "-version"], capture_output=True, text=True, **config.sem_janela())
    if r.returncode != 0:
        raise ErroAmigavel("O ffmpeg foi encontrado, mas não funcionou. Rode o instalador de novo.", r.stderr[-500:])
    origem = "do sistema" if shutil.which("ffmpeg") else "incluído no Cortes AI"
    print(f"      ffmpeg OK ({origem}).")


def _instalar_ollama() -> bool:
    """Tenta instalar o Ollama sozinho. Devolve True se conseguiu."""
    sistema = platform.system()
    try:
        if sistema == "Windows" and shutil.which("winget"):
            print("      Instalando o Ollama pelo Windows (winget). Pode aparecer uma janela pedindo permissão...")
            subprocess.run(["winget", "install", "-e", "--id", "Ollama.Ollama", "--accept-source-agreements",
                            "--accept-package-agreements"], check=False)
        elif sistema == "Darwin" and shutil.which("brew"):
            print("      Instalando o Ollama pelo Homebrew...")
            subprocess.run(["brew", "install", "ollama"], check=False)
        elif sistema == "Linux" and shutil.which("curl"):
            print("      Instalando o Ollama pelo instalador oficial (pode pedir a senha do computador)...")
            subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, check=False)
    except Exception as e:
        log.warning("Instalação automática do Ollama falhou: %r", e)
    return config.ollama_exe() is not None


def _conferir_ollama() -> None:
    if config.ollama_exe():
        print("      Ollama já instalado.")
        return
    print("      O Ollama (programa que roda a inteligência artificial) ainda não está instalado.")
    if _instalar_ollama():
        print("      Ollama instalado.")
        return
    raise ErroAmigavel(
        "Não consegui instalar o Ollama automaticamente.\n"
        "      Faça assim:\n"
        "        1. Abra https://ollama.com/download no navegador\n"
        "        2. Baixe e instale o Ollama (é gratuito; basta avançar até o fim)\n"
        "        3. Rode este instalador de novo")


def _baixar_modelo() -> None:
    from . import llm

    hw = config.hardware()
    nome = llm.modelo()
    print(f"      Seu computador: {hw.resumo}.")
    print(f"      Modelo escolhido: {nome}")
    llm.iniciar()
    if llm.tem_modelo(nome):
        print("      Modelo já baixado.")
    else:
        livre = shutil.disk_usage(config.RAIZ).free / 1024**3
        if livre < 10:
            print(f"      ATENÇÃO: só há {livre:.0f} GB livres no disco; o modelo precisa de uns 8 GB.")
        print("      Baixando o modelo (vários GB — pode demorar bastante, dependendo da internet)...")
        ultimo = [0.0]

        def mostrar(f: float, msg: str):
            if time.time() - ultimo[0] > 2 or f >= 1:
                ultimo[0] = time.time()
                print(f"\r      {f:6.1%}  {msg.split(': ')[-1]}      ", end="", flush=True)

        for tentativa in range(1, 6):
            try:
                llm.baixar_modelo(nome, mostrar)
                print()
                break
            except ErroAmigavel as e:
                print(f"\n      A conexão caiu (tentativa {tentativa} de 5). Continuando de onde parou...")
                log.warning("Download do modelo falhou: %s", e.detalhe)
                time.sleep(5)
        if not llm.tem_modelo(nome):
            raise ErroAmigavel("Não consegui baixar o modelo de IA. Verifique a internet e rode o instalador de novo "
                               "(o download continua de onde parou).")
    print("      Testando a inteligência artificial (pode levar 1 minuto)...", flush=True)
    try:
        r = llm.gerar_json(
            "Responda somente com JSON.", 'Responda exatamente: {"ok": true}',
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            lambda o: o, max_tokens=20, rotulo="teste")
        print("      IA funcionando." if r.get("ok") is not None else "      IA respondeu.")
    except ErroAmigavel:
        raise
    except Exception as e:
        raise ErroAmigavel("O modelo de IA foi baixado, mas não respondeu ao teste. "
                           "Reinicie o computador e rode o instalador de novo.", repr(e))


def _baixar_whisper() -> None:
    """Baixa o transcritor de áudio (usado para achar o fim exato das frases e em vídeos sem legenda)."""
    from . import transcrever

    nome, dispositivo, _ = config.escolher_whisper()
    tamanho = "3 GB" if nome == "large-v3" else "500 MB"
    print(f"      Transcritor: Whisper {nome} ({'placa de vídeo' if dispositivo == 'cuda' else 'processador'}), "
          f"cerca de {tamanho} na primeira vez...", flush=True)
    transcrever.carregar_whisper()
    print("      Transcritor OK.")


def main() -> int:
    config.configurar_log()
    try:
        _titulo(1, "Conferindo o ffmpeg (programa que corta os vídeos)")
        _conferir_ffmpeg()
        _titulo(2, "Conferindo o Ollama (inteligência artificial local)")
        _conferir_ollama()
        _titulo(3, "Preparando os modelos de IA")
        _baixar_whisper()
        _baixar_modelo()
        _titulo(4, "Conferindo os arquivos do programa")
        for arq in (config.FONTE, config.MODELO_ROSTO):
            if not arq.exists():
                raise ErroAmigavel(f"Está faltando o arquivo {arq.name} na pasta “assets”. "
                                   "Baixe o Cortes AI de novo, com todas as pastas.")
        config.criar_pastas()
        print("      Tudo certo.")
    except ErroAmigavel as e:
        print(f"\n  ERRO: {e.mensagem}\n")
        log.error("Instalação: %s | %s", e.mensagem, e.detalhe)
        return 1
    except Exception as e:
        log.exception("Erro inesperado na instalação")
        print(f"\n  ERRO inesperado: {e}\n  Veja o arquivo logs/cortes_ai.log para detalhes.\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
