#!/usr/bin/env bash
# Cortes AI - abrir no Mac e no Linux.   Uso:  bash abrir.sh
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo
  echo " O Cortes AI ainda não foi instalado."
  echo " Rode primeiro:  bash instalar.sh"
  echo
  exit 1
fi

echo "============================================================"
echo "   CORTES AI"
echo "   A tela vai abrir no seu navegador em alguns segundos."
echo "   Deixe esta janela aberta enquanto usa o programa."
echo "   Para fechar o Cortes AI, feche esta janela (ou Ctrl+C)."
echo "============================================================"
echo
if ! PYTHONUTF8=1 .venv/bin/python -m cortes_ai; then
  echo
  echo " O Cortes AI fechou por causa de um erro."
  echo " Tente abrir de novo. Se continuar, rode  bash instalar.sh  mais uma vez."
  echo " (Detalhes técnicos: logs/cortes_ai.log)"
  exit 1
fi
