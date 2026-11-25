"""Detecção de silêncios (ffmpeg silencedetect) e ajuste fino do início/fim dos cortes.

A escolha do ponto exato de corte é feita por código, não pelo modelo de IA:
o corte sempre começa e termina dentro de um silêncio real, de preferência em
fim de frase, sem começar com muleta ("então", "tipo") nem terminar no meio de uma ideia.
"""
from __future__ import annotations

import bisect
import json
import re
import subprocess
import unicodedata
from pathlib import Path

from . import config
from .config import log

RUIDO_DB = -33
SILENCIO_MIN = 0.28

# fim de frase: ponto, exclamação ou interrogação — mas NÃO reticências ("através..."), que indicam fala em aberto
_FIM_FRASE = re.compile(r"(?<![.…])[.!?][\"”')\]]*$")

# muletas: nunca devem abrir um corte
MULETAS_FORTES = {"entao", "tipo", "ai", "ne", "dai", "ah", "ahn", "hum", "ha", "eh", "hm", "enfim", "aham", "uhum"}
# palavras de reação do entrevistador; uma frase feita só delas não serve para abrir um corte
REACOES = {"entendeu", "aham", "uhum", "sim", "nao", "e", "exato", "exatamente", "certo", "ne", "ta", "ok", "isso",
           "pois", "claro", "verdade", "legal", "sabe", "ah", "hum", "ha", "cara", "nossa", "caraca", "bom",
           "beleza", "perfeito", "total", "totalmente", "show", "sei", "entendi", "serio", "jura", "pode", "crer"}
# se a ÚLTIMA palavra antes do corte for uma destas, a frase não terminou
RUIM_PARA_TERMINAR = {"e", "que", "de", "do", "da", "dos", "das", "mas", "porque", "pra", "para", "com", "o", "a",
                      "os", "as", "um", "uma", "em", "no", "na", "nos", "nas", "se", "como", "quando", "ou", "por",
                      "pro", "ate", "nem", "porem", "onde", "ao", "aos", "pelo", "pela", "meu", "minha", "seu",
                      "sua", "esse", "essa", "este", "esta"}
# se a PRIMEIRA palavra do corte for uma destas, ele começaria no meio de um raciocínio
RUIM_PARA_COMECAR = {"e", "que", "mas", "porque", "ou", "nem", "porem", "so"}


def normalizar(palavra: str) -> str:
    s = unicodedata.normalize("NFD", palavra.lower())
    return "".join(c for c in s if c.isalnum() and unicodedata.category(c) != "Mn")


def detectar(video: Path, arq_cache: Path | None = None) -> list[list[float]]:
    """Lista de silêncios [[inicio, fim], ...] do áudio inteiro (fica em cache)."""
    if arq_cache and arq_cache.exists():
        try:
            return json.loads(arq_cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    cmd = [config.ffmpeg(), "-hide_banner", "-nostats", "-i", str(video), "-vn", "-sn", "-dn",
           "-af", f"silencedetect=noise={RUIDO_DB}dB:d={SILENCIO_MIN}", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **config.sem_janela())
    silencios, inicio = [], None
    for linha in r.stderr.splitlines():
        if "silence_start:" in linha:
            try:
                inicio = max(0.0, float(linha.split("silence_start:")[1].strip().split()[0]))
            except ValueError:
                inicio = None
        elif "silence_end:" in linha and inicio is not None:
            try:
                fim = float(linha.split("silence_end:")[1].strip().split()[0])
                silencios.append([round(inicio, 3), round(fim, 3)])
            except ValueError:
                pass
            inicio = None
    log.info("Silêncios detectados: %d", len(silencios))
    if arq_cache:
        arq_cache.write_text(json.dumps(silencios), encoding="utf-8")
    return silencios


class Ajustador:
    """Encontra o melhor ponto de corte perto de um tempo desejado."""

    def __init__(self, palavras: list[list], silencios: list[list[float]], pontuada: bool, duracao: float):
        self.p = palavras
        self.inicios = [w[0] for w in palavras]
        self.pontuada = pontuada
        self.duracao = duracao
        self.pausas = self._montar_pausas(silencios)

    def _montar_pausas(self, silencios) -> list[tuple]:
        """Pontos onde um corte pode acontecer.

        = silêncios detectados no áudio (os mais confiáveis)
        + pausas longas entre palavras
        + todo fim de frase (quando a transcrição tem pontuação), mesmo sem silêncio:
          o fade de 0,3 s do áudio garante que a emenda não fique seca.
        """
        # (início, fim, é silêncio real?, índice da primeira palavra depois — None = descobrir pelo tempo)
        pausas = [(s0, s1, True, None) for s0, s1 in silencios if s1 - s0 >= SILENCIO_MIN]
        ini_sil = [s[0] for s in silencios]
        for i in range(len(self.p) - 1):
            a, b = self.p[i][1], self.p[i + 1][0]
            fim_de_frase = self.pontuada and bool(_FIM_FRASE.search(self.p[i][2]))
            if b - a >= 0.6 or fim_de_frase:
                k = bisect.bisect_left(ini_sil, a - 0.5)
                perto = k < len(silencios) and silencios[k][0] < b + 0.3
                if not perto:
                    pausas.append((a, max(b, a + 0.02), False, i + 1))
        pausas.sort(key=lambda x: x[0])
        return pausas

    def _abre_com_pergunta(self, i: int) -> bool:
        """A frase que começa na palavra i é uma pergunta? (perguntas costumam abrir um assunto)"""
        for j in range(i, min(i + 45, len(self.p))):
            if _FIM_FRASE.search(self.p[j][2]):
                return "?" in self.p[j][2]
        return False

    # índice da primeira palavra que começa depois do silêncio
    def _palavra_depois(self, t: float) -> int:
        return bisect.bisect_left(self.inicios, t - 0.25)

    def _pular_muletas(self, i: int, limite: int = 4) -> int:
        j = i
        if self.pontuada:  # frases curtinhas de reação ("Entendeu?", "Aham.", "É verdade.") não abrem corte
            for _ in range(4):
                fim = next((k for k in range(j, min(j + 3, len(self.p) - 1)) if _FIM_FRASE.search(self.p[k][2])), None)
                if fim is None or not all(normalizar(self.p[k][2]) in REACOES for k in range(j, fim + 1)):
                    break
                j = fim + 1
        i = j
        while j < len(self.p) - 1 and j - i < limite:
            n = normalizar(self.p[j][2])
            pausa = self.p[j + 1][0] - self.p[j][1]
            # só pula a muleta se houver uma pausa depois dela; se estiver colada na palavra
            # seguinte ("Então tem..."), cortar ali picotaria a fala: melhor manter a muleta
            if (n in MULETAS_FORTES and pausa >= 0.12) or (n in {"e", "mas"} and pausa >= 0.35) or n == "":
                j += 1
            else:
                break
        return j

    def ajustar_inicio(self, t: float, minimo: float = 0.0, maximo: float | None = None,
                       antes: float = 14.0, depois: float = 14.0, preciso: bool = False) -> float:
        """Melhor ponto para COMEÇAR um corte perto de t (em segundos).

        preciso=True: t já é o início exato de uma frase escolhida; só encaixa na pausa dela.
        """
        maximo = self.duracao if maximo is None else maximo
        if preciso:
            antes = depois = 0.8
        peso_dist = 3.0 if preciso else 1 / 6.0
        lo, hi = max(minimo, t - antes), min(maximo, t + depois)
        if hi - lo < 1.0:  # o corte anterior terminou depois do ponto desejado: procura logo após ele
            hi = min(maximo, lo + 4.0)
        melhor, melhor_nota = None, -1e9
        for s0, s1, real, idx in self.pausas:
            if s1 < lo or s1 > hi:
                continue
            i = idx if idx is not None else self._palavra_depois(s1)
            if i >= len(self.p):
                continue
            nota = min(s1 - s0, 1.0) * (2.0 if real else 1.0)
            anterior = self.p[i - 1][2] if i > 0 else "."
            primeira = normalizar(self.p[i][2])
            continuacao = primeira in RUIM_PARA_COMECAR
            if self.pontuada:  # fim de frase vale mais do que um silêncio longo no meio de uma frase
                nota += 3.0 if _FIM_FRASE.search(anterior) else -3.0
                if self._abre_com_pergunta(i) and not continuacao:
                    nota += 1.0
            if normalizar(anterior) in RUIM_PARA_TERMINAR:
                nota -= 1.5  # a frase anterior não terminou
            if continuacao:
                nota -= 4.0  # "E...", "Mas...", "Porque...": continuação do que veio antes
            elif primeira in MULETAS_FORTES:
                # "Então...", "Aí...": com pausa depois, a muleta é pulada e o resto da frase serve;
                # colada na palavra seguinte, não dá para pular: evita, se houver opção melhor
                colada = i + 1 < len(self.p) and self.p[i + 1][0] - self.p[i][1] < 0.12
                nota -= 2.5 if colada else 0.5
            corte = max(s0, s1 - 0.25)
            nota -= abs(corte - t) * peso_dist
            if nota > melhor_nota:
                melhor, melhor_nota = (corte, i, s1), nota
        if melhor is None:  # sem pausa por perto: começa no início da palavra mais próxima
            i = min(self._palavra_depois(max(lo, t) + 0.25), len(self.p) - 1)
            return max(minimo, self.p[i][0] - 0.15)
        corte, i, s1 = melhor
        j = self._pular_muletas(i)
        if j > i and self.p[j][0] <= hi + 8:
            corte = max(corte, self.p[j][0] - 0.2)  # pula as muletas e começa na primeira palavra "de verdade"
        return round(max(minimo, corte), 2)

    def ajustar_fim(self, t: float, minimo: float = 0.0, maximo: float | None = None,
                    antes: float = 15.0, depois: float = 10.0, preciso: bool = False) -> float:
        """Melhor ponto para TERMINAR um corte perto de t (em segundos)."""
        maximo = self.duracao if maximo is None else maximo
        if preciso:
            antes = depois = 0.8
        peso_dist = 3.0 if preciso else 1 / 8.0
        lo, hi = max(minimo, t - antes), min(maximo, t + depois)
        melhor, melhor_nota = None, -1e9
        for s0, s1, real, idx in self.pausas:
            if s0 < lo or s0 > hi:
                continue
            # última palavra antes da pausa
            i = idx - 1 if idx is not None else bisect.bisect_right(self.inicios, s0 + 0.1) - 1
            if i < 0:
                continue
            nota = min(s1 - s0, 1.0) * (2.0 if real else 1.0)
            ultima = self.p[i][2]
            if self.pontuada:
                nota += 3.0 if _FIM_FRASE.search(ultima) else -3.0
                if "?" in ultima:
                    nota -= 1.0  # terminar numa pergunta deixa o assunto no ar
            n = normalizar(ultima)
            if n in RUIM_PARA_TERMINAR:
                nota -= 2.0  # terminaria no meio de uma ideia
            corte = min(s1, s0 + 0.45)
            nota -= abs(s0 - t) * peso_dist
            if nota > melhor_nota:
                melhor, melhor_nota = corte, nota
        if melhor is None:
            i = max(0, bisect.bisect_right(self.inicios, min(hi, t)) - 1)
            return round(max(minimo, min(maximo, self.p[i][1] + 0.2)), 2)
        return round(max(minimo, min(maximo, melhor)), 2)
