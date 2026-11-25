"""Etapa 3 - escolher os melhores momentos com o modelo local (Ollama).

Modelos locais pequenos são bons em dizer "qual é o assunto aqui e quão interessante
ele é", mas ruins em marcar trechos longos com precisão. Por isso:

  1. (IA)     a transcrição é dividida em janelas de ~15 min com sobreposição; em cada
              janela o modelo separa a conversa em BLOCOS DE ASSUNTO, por número de linha;
  2. (código) os blocos das janelas são costurados numa lista única do vídeo inteiro;
  3. (IA)     um ranking final, só sobre a lista de blocos (não a transcrição), escolhe
              os assuntos mais fortes;
  4. (código) cada assunto escolhido vira um corte: começa no início do bloco e cresce
              com os blocos seguintes até atingir a duração mínima; duração máxima,
              não-sobreposição e ajuste fino das bordas (silêncio + fim de frase)
              são regras do código;
  5. (IA)     título, descrição, tags, gancho e textos de thumbnail de cada corte.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from . import config, llm, publicacao
from .config import log
from .erros import ErroAmigavel
from .silencio import RUIM_PARA_COMECAR, Ajustador, normalizar

NOTA_FRACA = 5             # blocos com nota até aqui são "enchimento" (apresentação, recados, despedida)
ALVO_LINHA = 22.0          # segundos de fala por linha numerada
MAX_CHARS_JANELA = 15000   # para caber com folga em num_ctx = 8192
# fim de frase: ponto, exclamação ou interrogação — mas NÃO reticências ("através..."), que indicam fala em aberto
_FIM_FRASE = re.compile(r"(?<![.…])[.!?][\"”')\]]*$")

SISTEMA = ("Você é um editor experiente de canais de cortes de podcasts brasileiros no YouTube. "
           "Escreva sempre em português do Brasil e responda somente com o JSON pedido.")


def mmss(t: float) -> str:
    t = int(round(t))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- #
# Preparação do texto
# --------------------------------------------------------------------------- #
def montar_linhas(palavras: list[list], pontuada: bool) -> list[dict]:
    """Agrupa as palavras em linhas de ~22 s, quebrando em pausas ou fins de frase."""
    linhas, i0 = [], 0
    for k, w in enumerate(palavras):
        ultimo = k == len(palavras) - 1
        dur = w[1] - palavras[i0][0]
        pausa = (palavras[k + 1][0] - w[1]) if not ultimo else 99.0
        boa_quebra = pausa >= 0.45 or (pontuada and bool(_FIM_FRASE.search(w[2])))
        if ultimo or pausa >= 6 or dur >= ALVO_LINHA * 1.6 or (dur >= ALVO_LINHA and boa_quebra):
            linhas.append({"ini": palavras[i0][0], "fim": w[1],
                           "texto": " ".join(x[2] for x in palavras[i0:k + 1])})
            i0 = k + 1
    return linhas


def montar_janelas(linhas: list[dict], janela: float = config.JANELA_SEG,
                   sobreposicao: float = config.SOBREPOSICAO_SEG,
                   max_chars: int = MAX_CHARS_JANELA) -> list[tuple[int, int]]:
    """Faixas (primeira linha, última linha) de ~15 min, com ~3 min de sobreposição."""
    janelas, a, n = [], 0, len(linhas)
    while a < n:
        b, chars = a, len(linhas[a]["texto"])
        while b + 1 < n and linhas[b + 1]["fim"] - linhas[a]["ini"] <= janela \
                and chars + len(linhas[b + 1]["texto"]) <= max_chars:
            b += 1
            chars += len(linhas[b]["texto"])
        # sobra pequena no fim: junta na última janela, se couber
        if b + 1 < n and linhas[-1]["fim"] - linhas[b]["fim"] < 240 \
                and chars + sum(len(l["texto"]) for l in linhas[b + 1:]) <= max_chars * 1.25:
            b = n - 1
        janelas.append((a, b))
        if b >= n - 1:
            break
        prox = b
        while prox > a + 1 and linhas[b]["fim"] - linhas[prox]["ini"] < sobreposicao:
            prox -= 1
        a = max(prox, a + 1)
    return janelas


# --------------------------------------------------------------------------- #
# Passo 1 (IA): blocos de assunto em cada janela
# --------------------------------------------------------------------------- #
ESQUEMA_JANELA = {
    "type": "object",
    "properties": {
        "blocos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio": {"type": "integer"},
                    "titulo": {"type": "string"},
                    "resumo": {"type": "string"},
                    "nota": {"type": "integer"},
                },
                "required": ["inicio", "titulo", "resumo", "nota"],
            },
        }
    },
    "required": ["blocos"],
}

_TITULOS_EXEMPLO = {"A infância pobre no interior", "O dia em que quase desisti de tudo"}

# Texto fixo, igual em todas as janelas: o Ollama reaproveita esse começo já processado
# (cache de prefixo), o que deixa cada janela mais rápida.
_INSTRUCOES_JANELA = """TAREFA
Você vai receber um trecho da transcrição de um podcast, em linhas numeradas (a transcrição é automática e pode ter erros).
Divida o trecho em blocos de assunto, na ordem em que aparecem. Cada bloco é uma parte da conversa sobre UM assunto; quando o assunto muda, começa um bloco novo. Um trecho de 15 minutos costuma ter de 4 a 8 blocos.

Para cada bloco informe:
- "inicio": número da linha onde o assunto começa (de preferência a pergunta ou a frase que abre o assunto);
- "titulo": título chamativo e honesto para o assunto, com até 60 caracteres;
- "resumo": uma frase dizendo o que é falado;
- "nota": de 1 a 10, o potencial do assunto para virar um corte de YouTube que prende a atenção.

Notas altas: histórias pessoais, opiniões fortes ou polêmicas, humor, fatos surpreendentes, conselhos práticos, momentos emocionantes, cultura brasileira, atualidades e pessoas famosas.
Notas baixas: apresentações, propagandas, agradecimentos, recados e conversa fiada.

Exemplo do formato (valores apenas ilustrativos):
{"blocos":[{"inicio":40,"titulo":"A infância pobre no interior","resumo":"O convidado conta como era a vida na roça e o que aprendeu com o pai.","nota":6},{"inicio":47,"titulo":"O dia em que quase desisti de tudo","resumo":"Ele relembra a falência da primeira empresa e a dívida de dois milhões.","nota":9}]}
"""


def prompt_janela(info: dict, linhas: list[dict], a: int, b: int) -> str:
    capitulos = info.get("capitulos") or []
    partes = []
    for n in range(a, b + 1):
        for c in capitulos:
            if linhas[n]["ini"] <= c["inicio"] < linhas[n]["fim"] and c["titulo"]:
                partes.append(f"--- capítulo do vídeo: {c['titulo']} ---")
        partes.append(f"[{n}] {linhas[n]['texto']}")
    texto = "\n".join(partes)
    return f"""{_INSTRUCOES_JANELA}
PODCAST: {info['titulo']} — canal {info['canal']}

TRANSCRIÇÃO (linhas {a} a {b}):
{texto}

Agora responda somente com o JSON dos blocos de assunto deste trecho. O primeiro bloco começa na linha {a}; use os números de linha entre colchetes."""


def validar_janela(obj: dict, a: int, b: int) -> dict:
    """Confere os blocos de uma janela: linhas dentro da faixa, em ordem, sem repetição."""
    blocos = obj.get("blocos")
    if not isinstance(blocos, list) or not blocos:
        raise ValueError("a lista \"blocos\" veio vazia")
    limpos: dict[int, dict] = {}
    for x in blocos[:20]:
        try:
            ini = int(x["inicio"])
            titulo = re.sub(r"\s+", " ", str(x["titulo"])).strip()
        except (KeyError, ValueError, TypeError):
            continue
        if not titulo or titulo in _TITULOS_EXEMPLO or not (a <= ini <= b):
            continue
        try:
            nota = int(x.get("nota", 5))
        except (ValueError, TypeError):
            nota = 5
        if ini not in limpos:
            limpos[ini] = {"inicio": ini, "titulo": titulo[:120],
                           "resumo": re.sub(r"\s+", " ", str(x.get("resumo", ""))).strip()[:300],
                           "nota": max(1, min(10, nota))}
    if not limpos:
        raise ValueError(f"nenhum bloco válido: \"inicio\" deve ser um número de linha entre {a} e {b}")
    if len(limpos) < 2 and b - a > 12:
        raise ValueError("só veio um bloco; divida o trecho em vários blocos de assunto")
    ordenados = [limpos[k] for k in sorted(limpos)]
    if ordenados[0]["inicio"] - a <= 3:
        ordenados[0]["inicio"] = a
    else:  # o modelo pulou o começo da janela: cria um bloco "neutro" para cobrir
        ordenados.insert(0, {"inicio": a, "titulo": "(sem assunto definido)", "resumo": "", "nota": 3})
    return {"blocos": ordenados}


# --------------------------------------------------------------------------- #
# Passo 2 (código): costurar os blocos das janelas numa lista única
# --------------------------------------------------------------------------- #
def costurar(janelas: list[tuple[int, int]], por_janela: list[list[dict]], linhas: list[dict]) -> list[dict]:
    """Junta os blocos de todas as janelas, resolvendo as regiões de sobreposição.

    Na sobreposição entre duas janelas, a emenda é feita num início de bloco (um ponto
    em que o assunto muda) o mais perto possível do meio; se não houver, no meio.
    """
    por_janela = [[dict(x) for x in bl] for bl in por_janela]  # cópia: não altera o que está em cache
    todos: list[dict] = []
    primeira_livre = 0
    for k, (a, b) in enumerate(janelas):
        bl = por_janela[k] or [{"inicio": a, "titulo": "(trecho não analisado)", "resumo": "", "nota": 1}]
        if k + 1 < len(janelas):
            a2 = janelas[k + 1][0]
            meio = (a2 + b + 1) // 2
            # preferência: uma mudança de assunto vista pela janela seguinte (ela conhece o que vem depois)
            emendas = [x["inicio"] for x in (por_janela[k + 1] or [])[1:] if a2 < x["inicio"] <= b] \
                or [x["inicio"] for x in bl if a2 < x["inicio"] <= b]
            emenda = min(emendas, key=lambda s: abs(s - meio)) if emendas else max(a2 + 1, meio)
            ultima = emenda - 1
        else:
            a2, ultima = b + 1, b
        for i, x in enumerate(bl):
            ini = max(x["inicio"], primeira_livre)
            fim = min(bl[i + 1]["inicio"] - 1 if i + 1 < len(bl) else b, ultima)
            if fim < ini:
                continue
            todos.append({"titulo": x["titulo"], "resumo": x["resumo"], "nota": x["nota"],
                          "lin_ini": ini, "lin_fim": fim})
        primeira_livre = ultima + 1
        # as duas janelas discordaram por 1-2 linhas sobre onde o assunto muda: o pedacinho
        # que sobrou antes da emenda passa para o bloco seguinte (o corte começa um pouco antes)
        if todos and todos[-1]["lin_ini"] > a2 and todos[-1]["lin_fim"] - todos[-1]["lin_ini"] < 2 and k + 1 < len(janelas):
            primeira_livre = todos.pop()["lin_ini"]
            for x in por_janela[k + 1]:
                if x["inicio"] == ultima + 1:
                    x["inicio"] = primeira_livre
    for n, x in enumerate(todos, 1):
        x["id"] = n
        x["ini"], x["fim"] = linhas[x["lin_ini"]]["ini"], linhas[x["lin_fim"]]["fim"]
    return todos


# --------------------------------------------------------------------------- #
# Passo 3 (IA): ranking final sobre a lista de blocos
# --------------------------------------------------------------------------- #
ESQUEMA_RANKING = {
    "type": "object",
    "properties": {
        "escolhidos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "nota": {"type": "integer"}},
                "required": ["id", "nota"],
            },
        }
    },
    "required": ["escolhidos"],
}


def prompt_ranking(info: dict, blocos: list[dict], n: int) -> str:
    lista = "\n".join(
        f"{x['id']} | {mmss(x['fim'] - x['ini'])} | {x['titulo']} — {x['resumo']}"
        for x in blocos if x["nota"] > 1
    )
    return f"""PODCAST: {info['titulo']} — canal {info['canal']}

ASSUNTOS DA CONVERSA, EM ORDEM (id | duração | título — resumo):
{lista}

TAREFA
Escolha os {n} assuntos com mais potencial para virar cortes de sucesso no YouTube, do melhor para o pior.
Critérios: assunto claro que vira um bom título, que faz sentido sozinho, com história, opinião forte, polêmica, humor, emoção ou conselho prático.
Evite escolher dois assuntos repetidos e evite apresentações, propagandas e despedidas. Use somente os ids da lista.
Para cada um, dê uma "nota" de 1 a 10.

Exemplo do formato (valores apenas ilustrativos):
{{"escolhidos":[{{"id":4,"nota":9}},{{"id":1,"nota":8}}]}}"""


def validar_ranking(obj: dict, blocos: list[dict]) -> dict:
    ids_validos = {x["id"] for x in blocos}
    vistos, limpos = set(), []
    for e in obj.get("escolhidos") or []:
        try:
            i, nota = int(e["id"]), int(e.get("nota", 7))
        except (KeyError, ValueError, TypeError):
            continue
        if i in ids_validos and i not in vistos:
            vistos.add(i)
            limpos.append({"id": i, "nota": max(1, min(10, nota))})
    if not limpos:
        raise ValueError("nenhum id válido em \"escolhidos\"; use os ids da lista")
    return {"escolhidos": limpos}


# --------------------------------------------------------------------------- #
# Passo 4 (código): dos assuntos escolhidos aos cortes
# --------------------------------------------------------------------------- #
def montar_cortes(blocos: list[dict], ordem: list[tuple[int, int]], n_min: int, n_max: int,
                  dur_min: float, dur_max: float, nota_enchimento: int = 7) -> list[dict]:
    """Transforma assuntos em cortes de duração válida, sem sobreposição.

    ordem: [(id do bloco, nota)] do melhor para o pior.
    Cada corte começa no início do bloco escolhido (o gancho do assunto) e cresce com os
    blocos SEGUINTES até passar da duração mínima; se não houver blocos livres à frente,
    cresce para trás. Depois de n_min cortes, só entram assuntos com nota >= nota_enchimento
    ("menos cortes, mas melhores").
    """
    pos = {x["id"]: k for k, x in enumerate(blocos)}
    usados: set[int] = set()
    cortes: list[dict] = []
    folga = 12.0  # o ajuste fino das bordas pode tirar alguns segundos

    for bid, nota in ordem:
        if len(cortes) >= n_max:
            break
        if len(cortes) >= n_min and nota < nota_enchimento:
            continue
        k = pos.get(bid)
        if k is None or k in usados:
            continue
        i = j = k

        def dur() -> float:
            return blocos[j]["fim"] - blocos[i]["ini"]

        def livre(m: int) -> bool:
            return 0 <= m < len(blocos) and m not in usados

        while dur() < dur_min + folga:
            frente = j + 1 if livre(j + 1) else None
            tras = i - 1 if livre(i - 1) else None
            if frente is None and tras is None:
                break
            # cresce para a frente (o gancho do assunto fica no começo), a não ser que o bloco
            # seguinte seja fraco (despedida, propaganda...) e o anterior não
            frente_fraca = frente is not None and blocos[frente]["nota"] <= NOTA_FRACA
            tras_boa = tras is not None and blocos[tras]["nota"] > NOTA_FRACA
            if frente is not None and not (frente_fraca and tras_boa):
                j = frente
            else:
                i = tras
        if dur() < dur_min + folga:
            continue  # espremido entre cortes já escolhidos: não dá para chegar à duração mínima
        semente = blocos[k]
        outros = [blocos[m] for m in range(i, j + 1) if m != k]
        media_outros = sum(o["nota"] for o in outros) / len(outros) if outros else nota
        resumo = semente["resumo"]
        if outros:
            resumo += " Também no corte: " + "; ".join(o["titulo"] for o in outros[:4]
                                                         if not o["titulo"].startswith("(")) + "."
        cortes.append({
            "titulo": semente["titulo"],
            "assunto_principal": semente["titulo"],
            "resumo": resumo,
            "nota": max(1, min(10, round(0.7 * nota + 0.3 * media_outros))),
            "ini": blocos[i]["ini"],
            "fim": min(blocos[j]["fim"], blocos[i]["ini"] + dur_max - folga),
        })
        usados.update(range(i, j + 1))
    return cortes


# Janela de frases mostrada ao modelo em volta do começo e do fim aproximados de cada corte
INI_ANTES, INI_DEPOIS = 20.0, 28.0
FIM_ANTES, FIM_DEPOIS = 28.0, 16.0
FOLGA_WHISPER = 4.0  # o Whisper transcreve um pouco além da janela, para não cortar palavras


def refinar_bordas(info: dict, palavras: list[list], cortes: list[dict], total: float,
                   progresso: Callable[[float, str], None] = lambda f, m: None) -> list[list]:
    """Legendas automáticas do YouTube não têm pontuação, então não dá para saber onde as frases
    terminam. Aqui o Whisper local transcreve SÓ os ~50 s em volta de cada começo/fim de corte
    (rápido), e essas palavras — com pontuação e tempo exato — substituem as da legenda nesses
    trechos. O ajuste das bordas continua sendo feito por código (silencio.Ajustador).
    """
    from . import transcrever

    video = Path(info["video"])
    arq = video.parent / "bordas.json"
    cache = _ler_json(arq)
    brutas = [(c["ini"] - INI_ANTES - FOLGA_WHISPER, c["ini"] + INI_DEPOIS + FOLGA_WHISPER) for c in cortes]
    brutas += [(c["fim"] - FIM_ANTES - FOLGA_WHISPER, c["fim"] + FIM_DEPOIS + FOLGA_WHISPER) for c in cortes]
    regioes: list[list[float]] = []
    for a, b in sorted(brutas):
        a, b = max(0.0, a), min(total, b)
        if regioes and a <= regioes[-1][1] + 2:
            regioes[-1][1] = max(regioes[-1][1], b)
        else:
            regioes.append([a, b])
    hibridas = list(palavras)
    for k, (a, b) in enumerate(regioes):
        progresso(k / len(regioes), f"Ajustando o começo e o fim de cada corte ({k + 1} de {len(regioes)})")
        chave = f"{a:.0f}-{b:.0f}"
        if chave not in cache:
            cache[chave] = transcrever.transcrever_trecho(video, a, b)
            arq.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        # descarta 1 s de cada ponta do trecho (palavras cortadas ao meio)
        miolo = [w for w in cache[chave] if w[0] >= a + 1.0 and w[1] <= b - 1.0]
        if len(miolo) < 5:
            continue
        lo, hi = miolo[0][0], miolo[-1][1]
        hibridas = [w for w in hibridas if w[1] <= lo or w[0] >= hi] + miolo
    hibridas.sort(key=lambda w: w[0])
    transcrever.descarregar_whisper()  # libera memória para o modelo de linguagem
    return hibridas


def finalizar_limites(cortes: list[dict], aj: Ajustador, dur_min: float, dur_max: float, total: float) -> list[dict]:
    """Ajuste fino: cada corte começa e termina em silêncio/fim de frase, sem sobreposição."""
    em_ordem = sorted(cortes, key=lambda c: c["ini"])
    prontos: list[dict] = []
    piso = 0.0
    for k, c in enumerate(em_ordem):
        prox = em_ordem[k + 1]["ini"] if k + 1 < len(em_ordem) else total
        # "preciso" = o modelo apontou a frase exata; o código só encaixa o corte na pausa dela
        if c.get("ini_preciso"):
            ini = aj.ajustar_inicio(c["ini"], minimo=piso, preciso=True)
        else:
            ini = aj.ajustar_inicio(c["ini"], minimo=piso)
        teto = min(total, ini + dur_max, max(prox, ini + dur_min + 20))
        alvo = min(max(c["fim"], ini + dur_min + 3), teto - 1)
        if c.get("fim_preciso") and abs(alvo - c["fim"]) < 0.01:
            fim = aj.ajustar_fim(alvo, minimo=ini + dur_min, maximo=teto, preciso=True)
        else:
            fim = aj.ajustar_fim(alvo, minimo=ini + dur_min, maximo=teto, antes=15, depois=12)
        if not (dur_min <= fim - ini <= dur_max) or fim > total + 0.5:
            log.warning("Corte “%s” descartado: duração final %.0fs fora dos limites.", c["titulo"], fim - ini)
            continue
        prontos.append(dict(c, ini=round(ini, 2), fim=round(fim, 2)))
        piso = fim
    return prontos


# --------------------------------------------------------------------------- #
# Passo 5 (IA): textos de publicação de cada corte
# --------------------------------------------------------------------------- #
ESQUEMA_TEXTOS = {
    "type": "object",
    "properties": {
        "primeira_frase": {"type": "integer"},
        "ultima_frase": {"type": "integer"},
        "titulo": {"type": "string"},
        "gancho": {"type": "string"},
        "descricao": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "thumbnails": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["primeira_frase", "ultima_frase", "titulo", "gancho", "descricao", "tags", "thumbnails"],
}

_INSTRUCOES_TEXTOS = """TAREFA
Você vai receber os dados de um corte de podcast, com as frases do INÍCIO e do FIM numeradas. Decida em que frase o corte começa e em que frase termina, e crie os textos para publicá-lo no YouTube, no estilo dos canais de cortes brasileiros de sucesso:
- "primeira_frase": número da frase do INÍCIO em que o corte deve começar: a pergunta ou afirmação que abre o assunto. Não escolha uma frase que seja resposta, comentário ou continuação de algo dito antes;
- "ultima_frase": número da frase do FIM em que o corte deve terminar: a conclusão do assunto, uma piada ou frase de efeito, antes de a conversa mudar de assunto. Não escolha uma pergunta nem uma frase incompleta;
- "titulo": chamativo e honesto, no máximo 65 caracteres, sem hashtags;
- "gancho": a frase mais forte dita logo no começo do corte (copie das frases do INÍCIO, corrigindo erros);
- "descricao": 2 ou 3 frases que resumem o corte e despertam curiosidade;
- "tags": de 12 a 15 palavras-chave de busca (nomes das pessoas, assuntos, termos populares), sem #;
- "thumbnails": 3 opções de texto para a thumbnail, cada uma com 2 a 5 palavras simples e bem escritas, que despertem curiosidade, choque ou emoção sem mentir sobre o conteúdo.

Exemplo do formato (valores apenas ilustrativos):
{"primeira_frase":3,"ultima_frase":106,"titulo":"Ele perdeu R$ 2 milhões e conta o que aprendeu","gancho":"Eu fiquei devendo dois milhões de reais.","descricao":"O empresário relembra a falência da primeira empresa. Ele explica os erros que cometeu e como recomeçou do zero.","tags":["empreendedorismo","falência","negócios","dinheiro","podcast","cortes","história de vida","superação","empresário","dívidas","recomeço","lições"],"thumbnails":["Perdi 2 milhões","Fali aos 30 anos","O erro que me quebrou"]}
"""


# ---- conferência ortográfica simples dos textos de thumbnail ------------------
# Modelos pequenos às vezes erram a grafia ("NINGÉM", "JOORNADA"). O texto vem em minúsculas
# (o código é que põe em MAIÚSCULAS) e cada palavra é conferida contra o vocabulário do
# próprio vídeo; opções com palavras desconhecidas vão para o fim da fila.
_COMUNS = set("""a o as os um uma uns umas de do da dos das em no na nos nas por pelo pela para pra com sem sobre
entre e ou mas que se como quando onde porque por que quem qual quais eu tu ele ela nos eles elas voce voces me te
lhe meu minha seu sua nosso nossa este esta esse essa isso isto aquilo aquele aquela ser estar ter fazer ir ver
dar dizer foi era sao sou esta estao tem tinha vai vao fez faz disse nao sim nunca sempre ja ainda mais menos muito
muita pouco tudo nada todo toda todos todas cada outro outra mesmo so tambem bem mal bom boa melhor pior grande
maior menor novo nova velho verdade mentira segredo erro medo vida morte amor dinheiro mundo brasil brasileiro
historia motivo jeito coisa dia vez ano anos hoje agora antes depois aqui la assim entao quase tanto ninguem alguem
todo mundo pode podia quer queria sabe sabia acha conta contou revela revelou explica explicou mudou perdeu ganhou
chorou riu brigou polemica chocante absurdo incrivel impossivel dificil facil real verdadeiro pesado forte
errado certo culpa culpado contra favor fim comeco inicio""".split())


_ACENTOS = {"nao": "não", "voce": "você", "voces": "vocês", "ja": "já", "so": "só", "ate": "até",
            "tambem": "também", "ninguem": "ninguém", "alguem": "alguém", "historia": "história",
            "polemica": "polêmica", "incrivel": "incrível", "impossivel": "impossível", "dificil": "difícil",
            "facil": "fácil", "comeco": "começo", "inicio": "início", "sao": "são", "estao": "estão", "vao": "vão"}


def _norm(p: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFD", p.lower())
    return "".join(c for c in s if c.isalnum() and unicodedata.category(c) != "Mn")


def montar_vocabulario(palavras: list[list], textos_extras: list[str]) -> dict[str, str]:
    """{forma sem acento: forma mais comum com acento} das palavras do vídeo e dos textos gerados."""
    from collections import Counter

    formas: dict[str, Counter] = {}
    fontes = [w[2] for w in palavras]
    for t in textos_extras:
        fontes += re.findall(r"[0-9A-Za-zÀ-ÿ]+", t or "")
    for bruto in fontes:
        for pedaco in re.findall(r"[0-9A-Za-zÀ-ÿ]+", bruto):
            n = _norm(pedaco)
            if n:
                formas.setdefault(n, Counter())[pedaco.lower()] += 1
    return {n: c.most_common(1)[0][0] for n, c in formas.items()}


def corrigir_thumb(texto: str, vocab: dict[str, str]) -> tuple[str, int]:
    """Devolve (texto com acentos restaurados, número de palavras de grafia duvidosa)."""
    if not vocab:
        return texto, 0
    saida_, duvidosas = [], 0
    for palavra in texto.split():
        miolo = re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", palavra)
        n = _norm(miolo)
        sem_acento = miolo.lower() == n
        if n in _ACENTOS and sem_acento:  # NAO -> NÃO, VOCE -> VOCÊ
            palavra = palavra.replace(miolo, _ACENTOS[n].upper())
        elif not n or n.isdigit() or len(n) <= 2 or n in _COMUNS:
            pass
        elif n not in vocab:
            duvidosas += 1
        elif len(n) >= 5 and sem_acento and vocab[n] != n:
            # palavra longa escrita sem acento, que no vídeo aparece acentuada: restaura (VARIOS -> VÁRIOS)
            palavra = palavra.replace(miolo, vocab[n].upper())
        saida_.append(palavra)
    return " ".join(saida_), duvidosas


def frases_da_regiao(palavras: list[list], a: float, b: float) -> tuple[list[dict], str]:
    """Divide as palavras entre a e b em frases (pela pontuação; sem pontuação, pelas pausas).

    Devolve (frases completas [{ini, fim, texto}], texto do pedaço incompleto que sobrou no fim).
    """
    ws = [w for w in palavras if a <= w[0] < b]
    # Com pontuação (Whisper ou legenda manual), uma frase só termina em ponto: frase longa NÃO é
    # partida nas pausas, senão o corte poderia começar/terminar no meio dela.
    pontuada = sum(1 for w in ws if _FIM_FRASE.search(w[2])) >= max(2, len(ws) // 60)
    frases, atual = [], []
    for k, w in enumerate(ws):
        atual.append(w)
        pausa = ws[k + 1][0] - w[1] if k + 1 < len(ws) else 0.0
        dur = w[1] - atual[0][0]
        if pontuada:
            fecha = bool(_FIM_FRASE.search(w[2])) or (dur >= 35 and pausa >= 0.4)
        else:
            fecha = (dur >= 10 and pausa >= 0.4) or dur >= 18
        if fecha:
            frases.append({"ini": atual[0][0], "fim": atual[-1][1], "texto": " ".join(x[2] for x in atual)})
            atual = []
    return frases, " ".join(x[2] for x in atual)


def frases_das_bordas(palavras: list[list], c: dict) -> tuple[str, list[dict], list[dict], str]:
    """(contexto antes, frases candidatas a INÍCIO, frases candidatas a FIM, contexto depois)"""
    do_inicio, _ = frases_da_regiao(palavras, c["ini"] - INI_ANTES, c["ini"] + INI_DEPOIS)
    antes = ""
    if len(do_inicio) > 3 and c["ini"] - INI_ANTES > 0:
        antes = do_inicio.pop(0)["texto"]  # a 1ª "frase" da janela costuma estar pela metade
    do_fim, depois = frases_da_regiao(palavras, c["fim"] - FIM_ANTES, c["fim"] + FIM_DEPOIS)
    if len(do_fim) > 3:
        do_fim.pop(0)
    return antes, do_inicio[:20], do_fim[:20], depois


def prompt_textos(info: dict, c: dict, palavras: list[list], evitar: list[str] | None = None) -> str:
    antes, do_inicio, do_fim, depois = frases_das_bordas(palavras, c)
    t_ini = do_inicio[-1]["fim"] if do_inicio else c["ini"]
    continua = " ".join(w[2] for w in palavras if t_ini <= w[0] < t_ini + 18)
    meio_t = (c["ini"] + c["fim"]) / 2
    meio = " ".join(w[2] for w in palavras if meio_t <= w[0] < meio_t + 20)
    lista_ini = "\n".join(f"[{n}] {f['texto']}" for n, f in enumerate(do_inicio, 1))
    lista_fim = "\n".join(f"[{n}] {f['texto']}" for n, f in enumerate(do_fim, 101))
    extra = ""
    if evitar:
        extra = "\nCrie textos de thumbnail DIFERENTES destes, que já foram usados: " + "; ".join(evitar) + "."
    return f"""{_INSTRUCOES_TEXTOS}
PODCAST: {info['titulo']} — canal {info['canal']}
ASSUNTO DO CORTE: {c.get('assunto_principal') or c['titulo']} — {c['resumo']}

INÍCIO (o corte começa em uma destas frases):
(antes: …{antes})
{lista_ini}
(continua: {continua}…)

MEIO DO CORTE: …{meio}…

FIM (o corte termina em uma destas frases):
{lista_fim}
(depois: {depois}…)

Agora responda somente com o JSON deste corte: "primeira_frase" entre 1 e {len(do_inicio)}, "ultima_frase" entre 101 e {100 + len(do_fim)}.{extra}"""


def limpar_texto_thumb(t: str) -> str | None:
    t = re.sub(r"[\"“”‘’'*#_]", "", str(t)).strip().strip(".,;:-–— ").upper()
    t = re.sub(r"\s+", " ", t)
    n = len(t.split())
    if 2 <= n <= 6 and len(t) <= 38:
        return t
    return None


def limpar_titulo(t: str, limite: int = 70) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"#\S+", "", str(t))).strip().strip("\"“”")
    if len(t) <= limite:
        return t
    curto = t[:limite - 1]
    if " " in curto:
        curto = curto[:curto.rfind(" ")]
    return curto.rstrip(" ,;:-–—|") + "…"


_STOP = {"para", "pelo", "pela", "como", "mais", "muito", "sobre", "entre", "esse", "essa", "isso", "aquele",
         "podcast", "episodio", "episódio", "com", "que", "uma", "dos", "das", "por", "não", "nao"}


def completar_tags(tags: list, info: dict) -> list[str]:
    """Entre 10 e 15 tags únicas; completa com dados do vídeo original se o modelo trouxe poucas."""
    limpas, vistas = [], set()

    def por(t):
        t = re.sub(r"\s+", " ", str(t).replace("#", "").replace(",", " ")).strip().lower()
        if 2 <= len(t) <= 30 and t not in vistas:
            vistas.add(t)
            limpas.append(t)

    for t in tags or []:
        por(t)
    if len(limpas) < 12:
        extras = [info["canal"], "podcast", "cortes", "cortes podcast", f"cortes {info['canal']}"]
        extras += [p for p in re.findall(r"[A-Za-zÀ-ÿ]{4,}", info["titulo"]) if p.lower() not in _STOP]
        extras += info.get("tags") or []
        for t in extras:
            if len(limpas) >= 12:
                break
            por(t)
    return limpas[:15]


def validar_textos(obj: dict) -> dict:
    bruto = re.sub(r"\s+", " ", str(obj.get("titulo", ""))).strip()
    if len(bruto) > 80:
        raise ValueError("o título passou de 65 caracteres; escreva um mais curto")
    titulo = limpar_titulo(bruto)
    if len(titulo) < 8:
        raise ValueError("o título veio vazio ou curto demais")
    thumbs = []
    for t in obj.get("thumbnails") or []:
        limpo = limpar_texto_thumb(t)
        if limpo and limpo not in thumbs:
            thumbs.append(limpo)
    if not thumbs:
        raise ValueError("cada texto de thumbnail precisa ter de 2 a 5 palavras")
    descricao = re.sub(r"\s+", " ", str(obj.get("descricao", ""))).strip()
    gancho = re.sub(r"\s+", " ", str(obj.get("gancho", ""))).strip().strip("\"“”")
    def inteiro(chave):
        try:
            return int(obj.get(chave))
        except (ValueError, TypeError):
            return None

    return {"titulo": titulo, "gancho": gancho[:300], "descricao": descricao[:600],
            "tags": obj.get("tags") or [], "thumbnails": thumbs[:4],
            "primeira_frase": inteiro("primeira_frase"), "ultima_frase": inteiro("ultima_frase")}


def gerar_textos(info: dict, c: dict, palavras: list[list], evitar: list[str] | None = None) -> dict:
    """Título, gancho, descrição, tags e textos de thumbnail de um corte (com plano B sem IA)."""
    try:
        r = llm.gerar_json(SISTEMA, prompt_textos(info, c, palavras, evitar), ESQUEMA_TEXTOS, validar_textos,
                           temperatura=0.5, max_tokens=600, rotulo=f"textos:{c['titulo'][:25]}",
                           cache=_arq_cache_llm(info))
    except ErroAmigavel:
        raise
    except Exception as e:
        log.warning("Textos do corte “%s” gerados sem IA (%s).", c["titulo"], e)
        base = limpar_texto_thumb(" ".join(c["titulo"].split()[:4])) or "VOCÊ PRECISA VER"
        r = {"titulo": limpar_titulo(c["titulo"]), "gancho": "", "descricao": c.get("resumo", ""), "tags": [],
             "thumbnails": [base], "primeira_frase": None, "ultima_frase": None}
    # frases escolhidas pelo modelo -> tempos (se o número vier fora da lista, vale a regra do código)
    _, do_inicio, do_fim, _ = frases_das_bordas(palavras, c)
    pf, uf = r.pop("primeira_frase", None), r.pop("ultima_frase", None)
    if pf is not None and 1 <= pf <= len(do_inicio):
        # frase que começa com "E", "Mas", "Porque"... é continuação: recusada, vale a regra do código
        abre = normalizar(do_inicio[pf - 1]["texto"].split()[0])
        if abre not in RUIM_PARA_COMECAR:
            r["ini_escolhido"] = do_inicio[pf - 1]["ini"]
    if uf is not None and 101 <= uf <= 100 + len(do_fim):
        ultima = do_fim[uf - 101]["texto"]
        # fim em pergunta ou em frase sem ponto final é recusado: aí vale a regra do código
        if _FIM_FRASE.search(ultima) and "?" not in ultima[-3:]:
            r["fim_escolhido"] = do_fim[uf - 101]["fim"]
    if not r["gancho"]:
        r["gancho"] = " ".join(w[2] for w in palavras if c["ini"] <= w[0] < c["ini"] + 12)
    # grafia: restaura acentos e põe na frente as opções sem palavras duvidosas
    vocab = montar_vocabulario(palavras, [info["titulo"], info["canal"], r["titulo"], r["descricao"], r["gancho"],
                                          c.get("resumo", "")]) if palavras else {}
    conferidas = [corrigir_thumb(t, vocab) for t in r["thumbnails"]]
    for (texto, duvidosas), original in zip(conferidas, r["thumbnails"]):
        if duvidosas:
            log.debug("Texto de thumbnail com grafia duvidosa: “%s”", original)
    conferidas.sort(key=lambda par: par[1])  # ordenação estável: mantém a ordem do modelo entre as boas
    r["thumbnails"] = list(dict.fromkeys(t for t, _ in conferidas))
    if evitar:
        r["thumbnails"] = [t for t in r["thumbnails"] if t not in evitar] or r["thumbnails"]
    r["tags"] = completar_tags(r["tags"], info)
    return r


# --------------------------------------------------------------------------- #
# Etapa completa
# --------------------------------------------------------------------------- #
def _assinatura(*coisas) -> str:
    return hashlib.sha1(json.dumps(coisas, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def _arq_cache_llm(info: dict) -> Path | None:
    """Arquivo onde ficam as respostas já dadas pela IA para este vídeo (para retomar sem refazer)."""
    video = info.get("video")
    return Path(video).parent / "respostas_ia.json" if video else None


def _ler_json(arq: Path) -> dict:
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except Exception:
        return {}


def selecionar(info: dict, transcricao: dict, silencios: list, opcoes: dict,
               progresso: Callable[[float, str], None] = lambda f, m: None) -> list[dict]:
    """Devolve a lista final de cortes (início, fim, título, resumo, gancho, nota, textos)."""
    pasta = Path(info["video"]).parent
    palavras = transcricao["palavras"]
    total = max(info["duracao"], palavras[-1][1])
    dur_min = float(opcoes.get("dur_min") or config.DUR_MIN_PADRAO)
    dur_max = float(opcoes.get("dur_max") or config.DUR_MAX_PADRAO)
    n_pedido = int(opcoes.get("n_cortes") or 0)
    n_min, n_max = (n_pedido, n_pedido) if n_pedido else config.faixa_de_cortes(total)
    cabem = max(1, int(total * 0.85 // dur_min))
    n_min, n_max = min(n_min, cabem), min(n_max, cabem)

    arq_sel = pasta / "selecao.json"
    ass_sel = _assinatura("v11", llm.modelo(), dur_min, dur_max, n_min, n_max, len(palavras))
    salvo = _ler_json(arq_sel)
    if salvo.get("assinatura") == ass_sel and salvo.get("cortes"):
        progresso(1.0, "Momentos já escolhidos antes — aproveitando.")
        return salvo["cortes"]

    llm.preparar(lambda f, m: progresso(0.0, m))
    linhas = montar_linhas(palavras, transcricao.get("tem_pontuacao", False))
    janelas = montar_janelas(linhas)
    log.info("Transcrição: %d linhas, %d janelas; modelo %s.", len(linhas), len(janelas), llm.modelo())

    # ---- passo 1: blocos de assunto por janela (salva a cada janela, para poder retomar)
    arq_blocos = pasta / "blocos.json"
    ass_blocos = _assinatura("v2", llm.modelo(), janelas, len(palavras))
    salvo = _ler_json(arq_blocos)
    feitas: dict[str, list] = salvo.get("janelas", {}) if salvo.get("assinatura") == ass_blocos else {}
    peso_janelas = 0.72
    for k, (a, b) in enumerate(janelas):
        progresso(peso_janelas * k / len(janelas),
                  f"Analisando a parte {k + 1} de {len(janelas)} da conversa (esta é a etapa mais demorada)")
        if str(k) in feitas:
            continue
        try:
            r = llm.gerar_json(SISTEMA, prompt_janela(info, linhas, a, b), ESQUEMA_JANELA,
                               lambda o, a=a, b=b: validar_janela(o, a, b),
                               temperatura=0.4, max_tokens=900, rotulo=f"janela {k + 1}/{len(janelas)}")
            feitas[str(k)] = r["blocos"]
        except ErroAmigavel:
            raise
        except Exception as e:
            log.warning("Janela %d não pôde ser analisada (%s).", k + 1, e)
            feitas[str(k)] = []
        arq_blocos.write_text(json.dumps({"assinatura": ass_blocos, "janelas": feitas}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    if not any(feitas.values()):
        raise ErroAmigavel(
            "A inteligência artificial não conseguiu analisar a conversa deste vídeo. "
            "Tente de novo; se continuar, veja o arquivo logs/cortes_ai.log.")

    # ---- passo 2: lista única de blocos
    blocos = costurar(janelas, [feitas.get(str(k), []) for k in range(len(janelas))], linhas)
    log.info("Assuntos encontrados: %d", len(blocos))
    for x in blocos:
        log.debug("  bloco %2d %s–%s nota %d: %s", x["id"], mmss(x["ini"]), mmss(x["fim"]), x["nota"], x["titulo"])

    # ---- passo 3: ranking final (só a lista de assuntos, não a transcrição)
    progresso(peso_janelas, "Comparando os assuntos e escolhendo os melhores")
    por_id = {x["id"]: x for x in blocos}
    ordem: list[tuple[int, int]] = []
    n_pedir = min(len(blocos), n_max + 4)
    try:
        r = llm.gerar_json(SISTEMA, prompt_ranking(info, blocos, n_pedir), ESQUEMA_RANKING,
                           lambda o: validar_ranking(o, blocos), temperatura=0.3, max_tokens=400, rotulo="ranking",
                           cache=_arq_cache_llm(info))
        # nota final do assunto = média entre a nota na janela e a nota no ranking geral;
        # assuntos que a análise da janela considerou fracos não viram ponto de partida de corte
        ordem = [(e["id"], round((por_id[e["id"]]["nota"] + e["nota"]) / 2 + 0.01)) for e in r["escolhidos"]
                 if por_id[e["id"]]["nota"] > NOTA_FRACA]
    except ErroAmigavel:
        raise
    except Exception as e:
        log.warning("Ranking pelo modelo falhou (%s); usando as notas das janelas.", e)
    ja = {i for i, _ in ordem}
    ordem += [(x["id"], x["nota"]) for x in sorted(blocos, key=lambda x: -x["nota"]) if x["id"] not in ja]

    # ---- passo 4: cortes com duração válida, sem sobreposição, com bordas ajustadas
    escolhidos = montar_cortes(blocos, ordem, n_min, n_max, dur_min, dur_max)
    pontuada = bool(transcricao.get("tem_pontuacao", False))
    if not pontuada and escolhidos:
        try:
            palavras = refinar_bordas(info, palavras, escolhidos, total,
                                      lambda f, m: progresso(0.74 + 0.06 * f, m))
            pontuada = True
        except Exception as e:  # sem Whisper o programa continua: só o ajuste fica menos preciso
            log.warning("Não consegui refinar as bordas com o Whisper (%r); usando só as pausas.",
                        getattr(e, "detalhe", "") or e)
    # ---- passo 5: textos de cada corte + escolha da frase inicial e final
    escolhidos.sort(key=lambda c: c["ini"])
    for k, c in enumerate(escolhidos):
        progresso(0.80 + 0.10 * k / len(escolhidos),
                  f"Escolhendo a frase de começo e de fim de cada corte ({k + 1} de {len(escolhidos)})")
        c["titulo_candidato"] = c["titulo"]
        textos = gerar_textos(info, c, palavras)
        if "ini_escolhido" in textos:
            c["ini"], c["ini_preciso"] = textos.pop("ini_escolhido"), True
        if "fim_escolhido" in textos:
            c["fim"], c["fim_preciso"] = textos.pop("fim_escolhido"), True
        c.update(textos)

    # ---- passo 6: ajuste final das bordas (código): silêncio + fim de frase, sem sobreposição
    aj = Ajustador(palavras, silencios, pontuada, total)
    finais = finalizar_limites(escolhidos, aj, dur_min, dur_max, total)
    if not finais:
        raise ErroAmigavel("Não consegui montar nenhum corte dentro da duração pedida. "
                           "Tente diminuir a duração mínima em “Avançado”.")
    finais.sort(key=lambda c: (-c["nota"], c["ini"]))
    for c in finais:
        c["duracao"] = round(c["fim"] - c["ini"], 2)
        c.pop("ini_preciso", None)
        c.pop("fim_preciso", None)

    # ---- passo 7: títulos e descrição para o YouTube (chamada própria; ver publicacao.py)
    # Roda DEPOIS do ajuste das bordas: não interfere nelas e descreve o corte como ele ficou.
    respostas = []
    for k, c in enumerate(finais):
        progresso(0.90 + 0.10 * k / len(finais), f"Escrevendo títulos e descrições ({k + 1} de {len(finais)})")
        respostas.append(publicacao.pedir(info, c, palavras, cache=_arq_cache_llm(info)))
    reserva = publicacao.convidado_mais_citado(respostas, info)  # para cortes em que o modelo não deu o nome
    for c, r in zip(finais, respostas):
        c.update(publicacao.aplicar_regras(info, c, palavras, r, reserva))
    for n, c in enumerate(finais, 1):
        c["n"] = n
        log.info("Corte %02d: %s–%s (%s) nota %d — %s", n, mmss(c["ini"]), mmss(c["fim"]),
                 mmss(c["duracao"]), c["nota"], c["titulo"])

    arq_sel.write_text(json.dumps({"assinatura": ass_sel, "cortes": finais}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    progresso(1.0, "Momentos escolhidos.")
    return finais
