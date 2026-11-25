#!/usr/bin/env bash
# Cortes AI - instalação no Mac e no Linux.   Uso:  bash instalar.sh
cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "   CORTES AI - INSTALAÇÃO"
echo "   (só precisa fazer isso uma vez; precisa de internet)"
echo "============================================================"
echo

SISTEMA="$(uname -s)"

# ---------------------------------------------------------------
# 1. Python 3.10 ou mais novo
# ---------------------------------------------------------------
echo "[Passo 1 de 3] Procurando o Python..."
achar_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' >/dev/null 2>&1; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY="$(achar_python)"
if [ -z "$PY" ] && [ "$SISTEMA" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
  echo "   Python não encontrado. Instalando pelo Homebrew..."
  brew install python@3.12
  PY="$(achar_python)"
fi
if [ -z "$PY" ]; then
  echo
  echo " ERRO: preciso do Python 3.10 ou mais novo e não o encontrei."
  if [ "$SISTEMA" = "Darwin" ]; then
    echo " Baixe e instale em https://www.python.org/downloads/ e rode  bash instalar.sh  de novo."
  else
    echo " No Ubuntu/Debian, rode:  sudo apt install python3 python3-venv"
    echo " Depois rode  bash instalar.sh  de novo."
  fi
  exit 1
fi
echo "   Python OK ($PY)."
echo

# ---------------------------------------------------------------
# 2. Ambiente virtual + bibliotecas
# ---------------------------------------------------------------
echo "[Passo 2 de 3] Instalando as bibliotecas do Cortes AI (alguns minutos)..."
if [ ! -x ".venv/bin/python" ]; then
  if ! "$PY" -m venv .venv; then
    echo
    echo " ERRO: não consegui preparar a pasta do programa (.venv)."
    echo " No Ubuntu/Debian, rode:  sudo apt install python3-venv   e tente de novo."
    exit 1
  fi
fi
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
if ! .venv/bin/python -m pip install --upgrade -r requirements.txt --quiet --disable-pip-version-check; then
  echo
  echo " ERRO: não consegui baixar as bibliotecas."
  echo " Verifique sua conexão com a internet e rode  bash instalar.sh  de novo."
  exit 1
fi
echo "   Bibliotecas OK."
echo

# ---------------------------------------------------------------
# 3. ffmpeg, Ollama e modelo de IA
# ---------------------------------------------------------------
echo "[Passo 3 de 3] Preparando o ffmpeg, o Ollama e a inteligência artificial..."
if ! PYTHONUTF8=1 .venv/bin/python -m cortes_ai.preparar; then
  echo
  echo " A instalação NÃO terminou. Leia a mensagem de erro acima,"
  echo " resolva o que ela pede e rode  bash instalar.sh  de novo."
  echo " (Detalhes técnicos: logs/cortes_ai.log)"
  exit 1
fi

chmod +x abrir.sh instalar.sh 2>/dev/null
echo
echo "============================================================"
echo "   INSTALAÇÃO CONCLUÍDA!"
echo "   Para usar:  bash abrir.sh   (ou dois cliques em abrir.sh)"
echo "============================================================"
