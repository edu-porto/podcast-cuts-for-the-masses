"""Etapa 5 - thumbnail 1280x720 (JPG < 2 MB).

1. Quadro: amostra quadros do corte e dá nota por nitidez, rostos visíveis e
   expressão (boca aberta/sorriso), usando o detector de rostos YuNet do OpenCV.
2. Texto: 2 a 6 palavras em MAIÚSCULAS (escritas pelo modelo local), fonte Anton,
   branco/amarelo com contorno preto grosso, posicionado onde NÃO cobre rostos.
3. Leve reforço de contraste e cor.
"""
from __future__ import annotations

import io
import itertools
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import config
from .config import log

L, A = 1280, 720
N_QUADROS = 16
N_GUARDADOS = 6
AMARELO, BRANCO, PRETO = (255, 226, 0), (255, 255, 255), (0, 0, 0)
TAM_MIN, TAM_MAX = 78, 190

# regiões onde o texto pode ficar: (nome, x0, y0, x1, y1, alinhamento)
ZONAS = [
    ("esquerda", 36, 40, 610, 680, "esq"),
    ("direita", 670, 40, 1244, 680, "dir"),
    ("baixo", 36, 430, 1244, 690, "centro"),
    ("cima", 36, 30, 1244, 290, "centro"),
]


# --------------------------------------------------------------------------- #
# Quadros e rostos
# --------------------------------------------------------------------------- #
def extrair_quadro(video: Path, t: float) -> Image.Image | None:
    """Um quadro do vídeo no tempo t, já em 1280x720 (preenchendo e cortando as sobras)."""
    cmd = [config.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
           "-frames:v", "1", "-vf", f"scale={L}:{A}:force_original_aspect_ratio=increase,crop={L}:{A}",
           "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "2", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, **config.sem_janela())
        if r.returncode != 0 or not r.stdout:
            return None
        return Image.open(io.BytesIO(r.stdout)).convert("RGB")
    except Exception as e:
        log.debug("Falha ao extrair quadro em %.1fs: %r", t, e)
        return None


_detector = {"obj": None, "tentou": False}


def _obter_detector():
    if _detector["tentou"]:
        return _detector["obj"]
    _detector["tentou"] = True
    try:
        import cv2

        dados = np.frombuffer(config.MODELO_ROSTO.read_bytes(), dtype=np.uint8)
        try:  # carregar da memória evita problemas com acentos no caminho da pasta
            det = cv2.FaceDetectorYN.create("onnx", dados, np.empty(0, dtype=np.uint8), (L, A), 0.75, 0.3, 50)
        except Exception:
            det = cv2.FaceDetectorYN.create(str(config.MODELO_ROSTO), "", (L, A), 0.75, 0.3, 50)
        _detector["obj"] = det
    except Exception as e:
        log.warning("Detector de rostos indisponível (%r); as thumbnails serão feitas sem ele.", e)
    return _detector["obj"]


def detectar_rostos(img: Image.Image) -> list[dict]:
    """Rostos na imagem: caixa (x, y, w, h), confiança e 'expressão' de 0 a 1."""
    det = _obter_detector()
    if det is None:
        return []
    import cv2

    bgr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    det.setInputSize((img.width, img.height))
    try:
        _, achados = det.detect(bgr)
    except Exception as e:
        log.debug("Detecção de rosto falhou: %r", e)
        return []
    rostos = []
    for f in (achados if achados is not None else []):
        x, y, w, h = [float(v) for v in f[:4]]
        olho_d, olho_e = f[4:6], f[6:8]
        boca_d, boca_e = f[10:12], f[12:14]
        dist_olhos = float(np.linalg.norm(olho_d - olho_e)) or 1.0
        larg_boca = float(np.linalg.norm(boca_d - boca_e))
        # boca larga em relação aos olhos = sorriso, riso ou fala enfática
        expressao = float(np.clip((larg_boca / dist_olhos - 0.78) / 0.35, 0, 1))
        rostos.append({"caixa": [x, y, w, h], "conf": float(f[14]), "expressao": expressao})
    return rostos


def nitidez(img: Image.Image) -> float:
    import cv2

    cinza = cv2.cvtColor(np.asarray(img.resize((640, 360))), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(cinza, cv2.CV_64F).var())


def nota_do_quadro(nit_rel: float, rostos: list[dict]) -> float:
    """Nitidez (20%) + tamanho/confiança do maior rosto (50%) + expressão (30%).

    Rosto em close vale mais que plano aberto; a expressão só conta em rostos grandes
    o bastante para serem vistos numa thumbnail pequena.
    """
    nit = nit_rel ** 0.5
    if not rostos:
        return 0.20 * nit
    maior = max(rostos, key=lambda r: r["caixa"][2] * r["caixa"][3])
    x, y, w, h = maior["caixa"]
    area = (w * h) / (L * A)
    tam = min(1.0, area / 0.06) * maior["conf"]
    cortado = x < 4 or y < 4 or x + w > L - 4 or y + h > A - 4
    if cortado:
        tam *= 0.5
    expr = maior["expressao"] * min(1.0, area / 0.03)
    return 0.20 * nit + 0.50 * tam + 0.30 * expr


def analisar_quadros(video: Path, inicio: float, fim: float, pasta_cache: Path) -> list[dict]:
    """Amostra quadros do corte, dá nota e guarda os melhores em pasta_cache (para regenerar rápido)."""
    indice = pasta_cache / "quadros.json"
    if indice.exists():
        try:
            lista = json.loads(indice.read_text(encoding="utf-8"))
            if lista and all((pasta_cache / q["arquivo"]).exists() for q in lista):
                return lista
        except Exception:
            pass
    pasta_cache.mkdir(parents=True, exist_ok=True)
    dur = fim - inicio
    tempos = [inicio + dur * (0.04 + 0.92 * (k + 0.5) / N_QUADROS) for k in range(N_QUADROS)]
    amostras = []
    for t in tempos:
        img = extrair_quadro(video, t)
        if img is None:
            continue
        amostras.append({"t": round(t, 2), "img": img, "nit": nitidez(img), "rostos": detectar_rostos(img)})
    if not amostras:
        return []
    nit_max = max(a["nit"] for a in amostras) or 1.0
    for a in amostras:
        a["nota"] = nota_do_quadro(a["nit"] / nit_max, a["rostos"])
        # só serve se houver onde pôr o texto sem cobrir rostos
        a["tem_espaco"] = melhor_zona(a["rostos"]) is not None
    amostras.sort(key=lambda a: (not a["tem_espaco"], -a["nota"]))
    lista = []
    for k, a in enumerate(amostras[:N_GUARDADOS]):
        nome = f"quadro_{k}.jpg"
        a["img"].save(pasta_cache / nome, quality=95)
        lista.append({"arquivo": nome, "t": a["t"], "nota": round(a["nota"], 3), "rostos": a["rostos"]})
    indice.write_text(json.dumps(lista), encoding="utf-8")
    return lista


# --------------------------------------------------------------------------- #
# Texto
# --------------------------------------------------------------------------- #
def _fonte(tam: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(config.FONTE), tam)


def _caixas_rostos(rostos: list[dict], folga: float = 0.14) -> list[tuple[float, float, float, float]]:
    caixas = []
    for r in rostos:
        x, y, w, h = r["caixa"]
        caixas.append((x - w * folga, y - h * folga * 1.6, x + w * (1 + folga), y + h * (1 + folga)))
    return caixas


def _intersecao(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def _zona_livre(zona, caixas) -> tuple[float, float, float, float] | None:
    """Encolhe a zona até não tocar em nenhum rosto. None se sobrar pouco espaço."""
    _, x0, y0, x1, y1, alinh = zona
    for c in caixas:
        if _intersecao((x0, y0, x1, y1), c) <= 0:
            continue
        if alinh == "esq":
            x1 = min(x1, c[0] - 12)
        elif alinh == "dir":
            x0 = max(x0, c[2] + 12)
        elif zona[0] == "baixo":
            y0 = max(y0, c[3] + 10)
        else:
            y1 = min(y1, c[1] - 10)
    if x1 - x0 < 330 or y1 - y0 < 110:
        return None
    if any(_intersecao((x0, y0, x1, y1), c) > 0 for c in caixas):
        return None
    return (x0, y0, x1, y1)


def zonas_livres(rostos: list[dict]) -> list[tuple]:
    """Zonas onde cabe texto sem tocar em rostos: [(zona, caixa livre)], da maior para a menor."""
    caixas = _caixas_rostos(rostos)
    opcoes = []
    for k, z in enumerate(ZONAS):
        livre = _zona_livre(z, caixas)
        if livre:
            area = (livre[2] - livre[0]) * (livre[3] - livre[1])
            bonus = 1.15 if z[0] in ("esquerda", "direita") else 1.0  # texto ao lado do rosto fica maior
            opcoes.append((area * bonus, k, z, livre))
    opcoes.sort(key=lambda o: -o[0])
    return [(z, livre) for _, _, z, livre in opcoes]


def melhor_zona(rostos: list[dict]):
    livres = zonas_livres(rostos)
    return livres[0] if livres else None


def _quebras(palavras: list[str], max_linhas: int):
    """Todas as formas de dividir as palavras em até max_linhas linhas (sem mudar a ordem)."""
    n = len(palavras)
    for k in range(1, min(max_linhas, n) + 1):
        for cortes in itertools.combinations(range(1, n), k - 1):
            pts = [0, *cortes, n]
            yield [" ".join(palavras[pts[i]:pts[i + 1]]) for i in range(k)]


def _medir(linhas: list[str], tam: int) -> tuple[float, float, float]:
    """(largura, altura total, passo entre linhas) do bloco de texto no tamanho tam."""
    f = _fonte(tam)
    larg = max(f.getlength(l) for l in linhas)
    alt_maiuscula = f.getbbox("ÁÇH")[3] - f.getbbox("H")[1]
    passo = alt_maiuscula * 1.12
    return larg, passo * len(linhas), passo


def ajustar_texto(texto: str, larg_max: float, alt_max: float) -> tuple[list[str], int] | None:
    """Melhor divisão em linhas e maior tamanho de fonte que cabe na área."""
    palavras = texto.split()
    melhor = None
    for linhas in _quebras(palavras, 3):
        lo, hi = TAM_MIN, TAM_MAX
        larg, alt, _ = _medir(linhas, lo)
        borda = lo * 0.24
        if larg + borda > larg_max or alt + borda > alt_max:
            continue
        while hi - lo > 2:
            meio = (lo + hi) // 2
            larg, alt, _ = _medir(linhas, meio)
            borda = meio * 0.24
            if larg + borda <= larg_max and alt + borda <= alt_max:
                lo = meio
            else:
                hi = meio
        # prefere letras maiores; em empate, linhas de comprimento parecido
        equilibrio = min(len(l) for l in linhas) / max(len(l) for l in linhas)
        chave = (lo + 6 * equilibrio, lo)
        if melhor is None or chave > melhor[0]:
            melhor = (chave, linhas, lo)
    if melhor is None:
        return None
    return melhor[1], melhor[2]


def _escurecer(img: Image.Image, caixa, alinh: str) -> Image.Image:
    """Degradê escuro atrás do texto, para ele ficar legível em qualquer fundo."""
    x0, y0, x1, y1 = [int(v) for v in caixa]
    mascara = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mascara)
    d.rounded_rectangle((x0 - 60, y0 - 50, x1 + 60, y1 + 50), radius=60, fill=150)
    mascara = mascara.filter(ImageFilter.GaussianBlur(70))
    return Image.composite(Image.new("RGB", img.size, PRETO), img, mascara)


def compor(base: Image.Image, rostos: list[dict], texto: str, variante: int = 0) -> tuple[Image.Image, dict]:
    """Desenha o texto na imagem sem cobrir rostos. Devolve (imagem, informações do layout)."""
    img = ImageEnhance.Contrast(base).enhance(1.12)
    img = ImageEnhance.Color(img).enhance(1.15)
    img = ImageEnhance.Sharpness(img).enhance(1.25)

    achado, encaixe = None, None
    # entre as zonas livres de rostos, fica a que permite a maior letra
    for z, livre in zonas_livres(rostos):
        e = ajustar_texto(texto, livre[2] - livre[0], livre[3] - livre[1])
        if e and (encaixe is None or e[1] > encaixe[1] + 12):
            achado, encaixe = (z, livre), e
    if achado is None:
        # sem área livre de rostos: faixa estreita embaixo, com letra menor (último recurso)
        livre = (36, 540, 1244, 704)
        encaixe = ajustar_texto(texto, livre[2] - livre[0], livre[3] - livre[1]) or ([texto], TAM_MIN)
        achado = (ZONAS[2], livre)
    (zona, livre), (linhas, tam) = achado, encaixe
    x0, y0, x1, y1 = livre
    alinh = zona[5]
    fonte = _fonte(tam)
    larg, alt, passo = _medir(linhas, tam)
    contorno = max(7, round(tam * 0.105))

    # posição vertical: zonas laterais e de cima centralizam; a de baixo encosta embaixo
    if zona[0] == "baixo":
        ty = y1 - alt - contorno
    else:
        ty = y0 + (y1 - y0 - alt) / 2
    bloco = [1e9, ty - contorno, -1e9, ty + alt + contorno]
    posicoes = []
    for k, linha in enumerate(linhas):
        w = fonte.getlength(linha)
        if alinh == "esq":
            tx = x0 + contorno
        elif alinh == "dir":
            tx = x1 - w - contorno
        else:
            tx = x0 + (x1 - x0 - w) / 2
        posicoes.append((tx, ty + k * passo))
        bloco[0], bloco[2] = min(bloco[0], tx - contorno), max(bloco[2], tx + w + contorno)
    cobre = any(_intersecao(bloco, c) > 0 for c in _caixas_rostos(rostos, folga=0.0))

    img = _escurecer(img, bloco, alinh)
    d = ImageDraw.Draw(img)
    # cores: uma linha em amarelo (destaque), as outras em branco; a variante alterna qual
    destaque = (len(linhas) - 1) if variante % 2 == 0 else 0
    topo_letra = fonte.getbbox("H")[1]
    for k, (linha, (tx, ty_l)) in enumerate(zip(linhas, posicoes)):
        cor = AMARELO if (k == destaque or (len(linhas) == 1 and variante % 2 == 0)) else BRANCO
        y = ty_l - topo_letra
        d.text((tx + contorno * 0.5, y + contorno * 0.6), linha, font=fonte, fill=PRETO,
               stroke_width=contorno, stroke_fill=PRETO)  # sombra
        d.text((tx, y), linha, font=fonte, fill=cor, stroke_width=contorno, stroke_fill=PRETO)
    return img, {"zona": zona[0], "tamanho": tam, "linhas": linhas, "bloco": [round(v) for v in bloco],
                 "cobre_rosto": cobre}


def salvar_jpg(img: Image.Image, destino: Path, limite_bytes: int = 2_000_000) -> int:
    for q in (92, 88, 82, 75, 65, 55):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() < limite_bytes:
            break
    destino.write_bytes(buf.getvalue())
    return buf.tell()


def gerar(video: Path, inicio: float, fim: float, textos: list[str], destino: Path,
          pasta_cache: Path, variante: int = 0) -> dict:
    """Cria (ou recria, com outra variante) a thumbnail de um corte.

    variante 0 = melhor quadro + primeiro texto; a cada variante muda o quadro e o texto.
    """
    quadros = analisar_quadros(video, inicio, fim, pasta_cache)
    textos = [t for t in textos if t] or ["VOCÊ PRECISA VER"]
    texto = textos[variante % len(textos)]
    if not quadros:
        base, rostos, q = Image.new("RGB", (L, A), (20, 20, 20)), [], {"t": inicio, "nota": 0}
    else:
        # o quadro avança a cada volta completa pelos textos, e também a cada variante ímpar
        q = quadros[(variante + variante // len(textos)) % len(quadros)] if variante else quadros[0]
        base, rostos = Image.open(pasta_cache / q["arquivo"]).convert("RGB"), q["rostos"]
    img, layout = compor(base, rostos, texto, variante)
    tamanho = salvar_jpg(img, destino)
    layout.update({"texto": texto, "quadro_t": q["t"], "quadro_nota": q.get("nota"), "bytes": tamanho,
                   "rostos": len(rostos)})
    log.info("Thumbnail %s: “%s” (%s, letra %d, %d rosto(s), cobre rosto: %s)", destino.parent.name, texto,
             layout["zona"], layout["tamanho"], len(rostos), layout["cobre_rosto"])
    return layout
