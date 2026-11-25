"""Inicia o Cortes AI:  python -m cortes_ai"""
from __future__ import annotations

import sys
import threading
import webbrowser

import requests

from . import config, llm
from .config import log


def main() -> None:
    config.configurar_log()
    # Já está aberto (o usuário clicou duas vezes no abrir)? Só mostra a tela que já existe.
    if "--porta" not in sys.argv:
        for porta in range(7860, 7864):
            try:
                r = requests.get(f"http://127.0.0.1:{porta}/", timeout=1.5)
            except requests.RequestException:
                continue
            if "Cortes AI" in r.text:
                log.info("O Cortes AI já está aberto. Mostrando a tela no navegador...")
                webbrowser.open(f"http://127.0.0.1:{porta}/")
                return
    hw = config.hardware()
    log.info("Cortes AI — %s. Modelo de IA: %s.", hw.resumo, config.escolher_modelo_llm(hw))

    def ligar_ollama():
        try:
            llm.iniciar()
        except Exception as e:  # a tela avisa de novo, com instruções, quando o usuário clicar em Gerar
            log.warning("Ollama ainda não está disponível: %s", e)

    threading.Thread(target=ligar_ollama, daemon=True).start()
    log.info("Abrindo a tela no navegador... (para fechar o Cortes AI, feche esta janela)")
    from . import app

    porta = None
    if "--porta" in sys.argv:
        porta = int(sys.argv[sys.argv.index("--porta") + 1])
    app.abrir(abrir_navegador="--sem-navegador" not in sys.argv, porta=porta)


if __name__ == "__main__":
    main()
