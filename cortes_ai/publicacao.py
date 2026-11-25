"""Passo 7 - títulos e descrição de cada corte para publicar no YouTube.

As regras vêm da pesquisa registrada em docs/seo_youtube.md (seção 6). O modelo local só
ESCREVE (3 títulos, a chamada e o resumo); tudo o que dá para conferir é conferido aqui,
em código: tamanho, nome/assunto no começo, fidelidade ao que é dito, maiúsculas, emoji,
repetição da thumbnail, créditos, placeholder do @ do canal, 3 hashtags e poucas tags.

Esta chamada é SEPARADA da que escolhe a frase inicial e final do corte (selecionar.py,
passo 5): aquele prompt não foi tocado, então os pontos de corte não mudam por causa dos
títulos. Ela também roda depois do ajuste final, e por isso lê o corte como ele ficou.
"""
from __future__ import annotations

import re

from . import llm
from .config import log
from .erros import ErroAmigavel
from .silencio import normalizar

PLACEHOLDER_CANAL = "[@CANAL_ORIGINAL]"
TITULO_ALVO = 60       # regra 3: alvo de até 60 caracteres...
TITULO_MAX = 70        # ...e nunca mais de 70
POSICAO_BUSCA = 45     # regra 2: nome do convidado ou assunto nos primeiros 45 caracteres
N_TITULOS = 3          # regra 7: três opções para o "Testar e comparar"
N_HASHTAGS = 3         # regra 11
TAGS_MIN, TAGS_MAX = 5, 8   # regra 12
CHAMADA_MAX = 200      # regra 8: 1-2 linhas antes do "mostrar mais"
RESUMO_MAX = 700
DESCRICAO_MAX = 5000   # limite do YouTube

TIPOS = {"busca": "direto, para quem pesquisa", "curiosidade": "desperta curiosidade",
         "frase": "frase dita no corte (confira se foi dita assim antes de usar)", "reserva": "alternativa"}

SISTEMA = ("Você escreve títulos e descrições para um canal de cortes de podcasts brasileiros no YouTube. "
           "Você é chamativo, mas nunca promete o que o vídeo não entrega. "
           "Escreva em português do Brasil e responda somente com o JSON pedido.")

ESQUEMA = {
    "type": "object",
    "properties": {
        "resumo": {"type": "string"},
        "convidado": {"type": "string"},
        "palavra_chave": {"type": "string"},
        "titulo_busca": {"type": "string"},
        "titulo_curiosidade": {"type": "string"},
        "titulo_frase": {"type": "string"},
        "chamada": {"type": "string"},
    },
    "required": ["resumo", "convidado", "palavra_chave", "titulo_busca", "titulo_curiosidade", "titulo_frase",
                 "chamada"],
}

_NOME_EXEMPLO = "João Silva"

# Texto fixo e curto (o modelo é pequeno); igual para todos os cortes, para o Ollama reaproveitar o prefixo.
_INSTRUCOES = """TAREFA
Você vai receber os dados de um corte de podcast que será publicado no YouTube. Responda com:
- "resumo": 2 a 4 frases naturais contando o que é dito no corte, com as palavras que alguém digitaria para procurar esse assunto;
- "convidado": nome do convidado principal, copiado letra por letra do nome do PODCAST ("" se não houver);
- "palavra_chave": o assunto específico deste corte (não o tema geral do podcast), em 1 ou 2 palavras que alguém digitaria na busca;
- "titulo_busca": nome do convidado + o que ele conta, explica ou defende de mais forte no corte, de forma direta;
- "titulo_curiosidade": pergunta ou afirmação que desperta curiosidade sobre o ponto mais forte do corte, sem exagerar;
- "titulo_frase": sobrenome do convidado, dois-pontos e, entre aspas, uma frase curta (até 8 palavras) que ele diz no corte como opinião dele e que faz sentido sozinha;
- "chamada": 1 ou 2 frases curtas (até 150 caracteres) para abrir a descrição: o gancho do corte, com o nome do convidado e a palavra-chave.

Regras dos 3 títulos: são CURTOS, no máximo 50 caracteres cada; começam pelo nome do convidado ou pela palavra-chave; só prometem o que é dito no corte; acrescentam algo ao TEXTO DA THUMBNAIL em vez de repeti-lo; sem emoji, sem hashtag, sem palavrão e sem palavras em MAIÚSCULAS.

Exemplo do formato (valores apenas ilustrativos):
{"resumo":"O empresário João Silva conta como a primeira empresa faliu e deixou uma dívida de dois milhões de reais. Ele explica os erros de gestão que cometeu e o que mudou para recomeçar do zero.","convidado":"João Silva","palavra_chave":"falência","titulo_busca":"João Silva conta como faliu devendo R$ 2 milhões","titulo_curiosidade":"João Silva e o erro que quebrou sua empresa","titulo_frase":"Silva: \\"Eu achava que caixa era lucro\\"","chamada":"João Silva relembra a falência da primeira empresa e a dívida de R$ 2 milhões. O que ele faria diferente hoje?"}
"""

_EMOJI = re.compile("[\U0001F000-\U0001FAFF←-⇿⌀-⏿☀-➿⬀-⯿️‍]")
_PALAVRA = re.compile(r"[0-9A-Za-zÀ-ÿ]+")
_PEQUENAS = {"de", "da", "do", "dos", "das", "e"}
# palavras que não dizem nada sobre o conteúdo (não entram na conferência de fidelidade)
_VAZIAS = set("""para pelo pela pelos pelas como mais muito muita sobre entre esse essa este esta isso isto aquele
aquela aquilo quando onde porque quem qual quais seus suas dele dela nosso nossa voce voces mesmo ainda tambem
depois antes agora hoje aqui tudo nada todo toda todos todas cada outro outra outros outras ser estar ter fazer
sendo sido esta estao foram tinha tinham pode podem deve devem vai vamos foi era sao tem
conta contou revela revelou explica explicou fala falou diz disse mostra mostrou responde respondeu comenta
comentou critica criticou detona detonou relembra relembrou admite admitiu confessa confessou questiona
verdade motivo jeito coisa coisas vida gente pessoas podcast corte cortes video episodio""".split())


# --------------------------------------------------------------------------- #
# Ferramentas de texto
# --------------------------------------------------------------------------- #
def _tokens(texto: str) -> list[str]:
    return _PALAVRA.findall(texto or "")


def palavras_de_conteudo(texto: str, ignorar: set[str] = frozenset()) -> list[str]:
    """Palavras 'de assunto' (4+ letras, sem as vazias), já sem acento e em minúsculas."""
    out = []
    for t in _tokens(texto):
        n = normalizar(t)
        if len(n) >= 4 and not n.isdigit() and n not in _VAZIAS and n not in ignorar:
            out.append(n)
    return out


def _prefixo(n: str) -> str:
    return n[:4]  # tolera flexões: "largou"/"larguei", "filósofo"/"filosofia"


def vocabulario_do_corte(palavras: list[list], ini: float, fim: float, extras: list[str] = ()) -> set[str]:
    """Prefixos das palavras realmente ditas no corte (mais nomes do vídeo), para conferir os títulos."""
    vocab = {_prefixo(normalizar(w[2])) for w in palavras if ini <= w[0] < fim}
    for texto in extras:
        vocab |= {_prefixo(normalizar(t)) for t in _tokens(texto)}
    vocab.discard("")
    return vocab


def fidelidade(titulo: str, vocab: set[str], ignorar: set[str] = frozenset()) -> float:
    """Fração das palavras de conteúdo do título que aparecem na fala do corte (regra 1)."""
    conteudo = palavras_de_conteudo(titulo, ignorar)
    if not conteudo or not vocab:
        return 1.0
    return sum(_prefixo(p) in vocab for p in conteudo) / len(conteudo)


def tem_emoji(texto: str) -> bool:
    return bool(_EMOJI.search(texto or ""))


def nome_formatado(nome: str) -> str:
    """'LUIZ FELIPE PONDÉ' -> 'Luiz Felipe Pondé' (deixa como está se já vier em caixa mista)."""
    nome = re.sub(r"\s+", " ", str(nome or "")).strip(" .,-–—\"“”")
    if nome.isupper() or nome.islower():
        nome = " ".join(p.lower() if p.lower() in _PEQUENAS else p.capitalize() for p in nome.split())
    return nome


def _tokens_do_nome(nome: str) -> set[str]:
    return {normalizar(t) for t in _tokens(nome) if len(normalizar(t)) >= 4}


def conferir_convidado(nome: str, info: dict, vocab: set[str]) -> str:
    """Aceita o nome que o modelo deu só se ele existir no título do vídeo ou na fala.

    Se o título do vídeo traz o nome completo ("... (LUIZ FELIPE PONDÉ) | ..."), usa essa forma,
    para o nome sair igual em todos os cortes mesmo que o modelo escreva só "Felipe Pondé".
    """
    nome = nome_formatado(nome)
    toks = _tokens_do_nome(nome)
    if not toks or nome == _NOME_EXEMPLO or len(nome) > 40:
        return ""
    # tolera erro de grafia do modelo ("Luis Felipe Pondé"): basta metade do nome bater com o trecho
    for pedaco in re.split(r"[|()\[\]:–—]| - ", info.get("titulo", "")):
        do_pedaco = [normalizar(t) for t in _tokens(pedaco) if len(normalizar(t)) >= 3]
        if not (1 <= len(pedaco.split()) <= 5) or any(c.isdigit() for c in pedaco) or not do_pedaco:
            continue
        acertos = sum(any(_parecido(t, d) for d in do_pedaco) for t in toks)
        if acertos * 2 >= len(toks) and acertos * 2 >= len(do_pedaco):
            return nome_formatado(pedaco)
    no_titulo = {normalizar(t) for t in _tokens(info.get("titulo", ""))}
    if toks & no_titulo or any(_prefixo(t) in vocab for t in toks):
        return nome
    return ""


def _parecido(a: str, b: str) -> bool:
    """Duas palavras (já sem acento) são a mesma, a menos de um erro de digitação?"""
    import difflib

    if a == b:
        return True
    if min(len(a), len(b)) < 3 or a[0] != b[0] or abs(len(a) - len(b)) > 1:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.74


def corrigir_nome(texto: str, convidado: str, exigir_maiuscula: bool = True) -> str:
    """Conserta o nome do convidado escrito errado pelo modelo ("Luí Felipe Pondé", "Luis Felipe Ponde").

    Só mexe numa palavra parecida com uma parte do nome quando ela está colada em outra parte do nome
    (para não trocar "ponte" por "Pondé" no meio de uma frase qualquer).
    """
    partes = {normalizar(p): p for p in convidado.split() if len(normalizar(p)) >= 3 and p.lower() not in _PEQUENAS}
    if len(partes) < 2 or not texto:
        return texto
    achados = list(_PALAVRA.finditer(texto))
    canon = []
    for m in achados:
        n = normalizar(m.group(0))
        canon.append(next((p for k, p in partes.items() if _parecido(n, k)), None))
    for i in reversed(range(len(achados))):
        if canon[i] is None:
            continue
        palavra = achados[i].group(0)
        vizinho = any(0 <= j < len(achados) and canon[j] is not None for j in (i - 1, i + 1))
        if palavra.lower() == canon[i].lower() or not vizinho or (exigir_maiuscula and not palavra[:1].isupper()):
            continue
        novo = canon[i].upper() if palavra.isupper() else (canon[i] if exigir_maiuscula else canon[i].lower())
        texto = texto[:achados[i].start()] + novo + texto[achados[i].end():]
    return texto


def convidado_mais_citado(respostas: list[dict], info: dict) -> str:
    """Nome que mais aparece entre os cortes do vídeo; serve de reserva quando o modelo deixa em branco."""
    from collections import Counter

    nomes = Counter(n for n in (conferir_convidado(r.get("convidado", ""), info, set()) for r in respostas) if n)
    if not nomes:
        return ""
    nome, vezes = nomes.most_common(1)[0]
    return nome if vezes >= 2 or len(respostas) == 1 else ""


def encurtar_nome(titulo: str, convidado: str) -> str:
    """Título acima do alvo com o nome completo (3+ palavras): fica só o sobrenome ("Pondé")."""
    partes = convidado.split()
    if len(titulo) <= TITULO_ALVO or len(partes) < 3:
        return titulo
    # procura o nome completo ou um pedaço dele com 2+ palavras ("Felipe Pondé"), do maior para o menor
    for tamanho in range(len(partes), 1, -1):
        for inicio in range(len(partes) - tamanho + 1):
            pedaco = " ".join(partes[inicio:inicio + tamanho])
            achado = re.search(re.escape(pedaco), titulo, flags=re.IGNORECASE)
            if achado:
                return titulo[:achado.start()] + partes[-1] + titulo[achado.end():]
    return titulo


def busca_no_comeco(titulo: str, convidado: str, palavra_chave: str) -> bool:
    """Regra 2: o nome do convidado ou a palavra-chave aparece nos primeiros 45 caracteres?"""
    inicio = {normalizar(t) for t in _tokens(titulo[:POSICAO_BUSCA])}
    alvo = _tokens_do_nome(convidado) | {normalizar(t) for t in _tokens(palavra_chave) if len(normalizar(t)) >= 4}
    if not alvo:
        return True  # sem nome nem assunto conhecidos não há o que cobrar
    return bool(inicio & alvo) or any(_prefixo(a) in {_prefixo(i) for i in inicio} for a in alvo)


def repete_thumbnail(titulo: str, texto_thumb: str, convidado: str = "") -> bool:
    """Regra 6: o título só repete o texto da thumbnail, sem acrescentar informação?

    Usar o mesmo assunto é normal (thumbnail "NATUREZA HUMANA?" + título sobre natureza humana).
    Repetir é dizer as palavras da thumbnail e nada além delas (nenhuma palavra de conteúdo nova).
    """
    da_thumb = {_prefixo(normalizar(t)) for t in _tokens(texto_thumb) if len(normalizar(t)) >= 4}
    do_titulo = {_prefixo(p) for p in palavras_de_conteudo(titulo, _tokens_do_nome(convidado))}
    todas = {_prefixo(normalizar(t)) for t in _tokens(titulo)}
    if len(da_thumb) < 2 or len(da_thumb & todas) / len(da_thumb) < 0.75:
        return False
    return len(do_titulo - da_thumb) < 1


def limpar_titulo(t: str) -> str:
    """Tira emoji, hashtag, aspas em volta do título todo, ponto final e espaços sobrando."""
    t = _EMOJI.sub("", str(t or ""))
    t = re.sub(r"#[0-9A-Za-zÀ-ÿ_]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace("“", '"').replace("”", '"')
    t = re.sub(r"\s+([.,;:!?])", r"\1", t).rstrip(" .;,:-–—|")
    t = re.sub(r'\s+"$', '"', t)     # espaço que sobrou antes da aspa final
    if len(t) > 2 and t[0] == '"' and t[-1] == '"' and t.count('"') == 2:
        t = t[1:-1].strip()          # aspas em volta do título inteiro
    if t.count('"') % 2 == 1:        # aspa sem par
        t = t[1:] if t.startswith('"') else (t[:-1] if t.endswith('"') else t.replace('"', ""))
    return t.strip().rstrip(" .;,:-–—|")


def ajustar_maiusculas(titulo: str, convidado: str, palavra_chave: str, referencia: str = "") -> str:
    """Regra 5: MAIÚSCULAS só no nome do convidado (ou no assunto, se não houver nome)."""
    do_nome = _tokens_do_nome(convidado)
    proprias = {normalizar(t) for t in _tokens(referencia) if t[:1].isupper() and not t.isupper()}

    def rebaixar(m: re.Match) -> str:
        p = m.group(0)
        n = normalizar(p)
        if not p.isupper() or len(n) <= 4 or n in do_nome:   # siglas curtas (STF, EUA, UFC) ficam
            return p
        return p.capitalize() if (n in proprias or m.start() == 0) else p.lower()

    t = _PALAVRA.sub(rebaixar, titulo)
    if t[:1].islower():
        t = t[:1].upper() + t[1:]
    destacou = False
    if do_nome:
        def destacar(m: re.Match) -> str:
            nonlocal destacou
            if normalizar(m.group(0)) in do_nome:
                destacou = True
                return m.group(0).upper()
            return m.group(0)
        t = _PALAVRA.sub(destacar, t)
    if not destacou and palavra_chave:
        achado = re.search(re.escape(palavra_chave), t, flags=re.IGNORECASE)
        if achado and len(palavra_chave.split()) <= 3:
            t = t[:achado.start()] + achado.group(0).upper() + t[achado.end():]
    return t


def problemas_do_titulo(titulo: str, convidado: str, palavra_chave: str, texto_thumb: str, vocab: set[str]) -> dict:
    """Confere um título contra as regras 1 a 6. Devolve {regra quebrada: explicação}."""
    ruins: dict[str, str] = {}
    if not (8 <= len(titulo) <= TITULO_MAX):
        ruins["tamanho"] = f"{len(titulo)} caracteres (máximo {TITULO_MAX})"
    elif len(titulo) > TITULO_ALVO:
        ruins["longo"] = f"{len(titulo)} caracteres (alvo {TITULO_ALVO})"
    if tem_emoji(titulo) or "#" in titulo:
        ruins["emoji_hashtag"] = "emoji ou hashtag no título"
    if tem_palavrao(titulo):
        ruins["palavrao"] = "palavrão no título"
    if not busca_no_comeco(titulo, convidado, palavra_chave):
        ruins["busca"] = f"nome/assunto fora dos primeiros {POSICAO_BUSCA} caracteres"
    ignorar = _tokens_do_nome(convidado)
    if fidelidade(titulo, vocab, ignorar) < 0.5:
        ruins["fidelidade"] = "usa palavras que não aparecem na fala do corte"
    if repete_thumbnail(titulo, texto_thumb, convidado):
        ruins["thumbnail"] = "repete o texto da thumbnail"
    letras = [p for p in _tokens(titulo) if len(p) >= 3 and p.isalpha()]
    if letras and sum(p.isupper() for p in letras) / len(letras) > 0.5:
        ruins["maiusculas"] = "título quase todo em maiúsculas"
    return ruins


_PESO = {"tamanho": 100, "emoji_hashtag": 100, "maiusculas": 100, "palavrao": 100, "fidelidade": 40, "busca": 3,
         "thumbnail": 3, "longo": 1}
# Ordem de preferência para o título principal. A "frase" fica por último de propósito: o código não
# consegue julgar se a citação é boa nem se foi o convidado quem disse aquilo (pode ser ele citando
# outra pessoa), então ela só vira o título principal se as outras opções forem descartadas.
# As "reservas" (título do passo 5 e título do bloco) só passam na frente de um título novo com defeito grave.
_BASE_DO_TIPO = {"busca": 0, "curiosidade": 1, "reserva": 10, "frase": 20}

# regra 4: palavrão no título reduz ou zera os anúncios (diretrizes de conteúdo adequado para publicidade)
_PALAVROES = set("""caralho caralhos porra porras merda merdas foda fodas foder fodeu fodido fodida fodase
puta putas puto putaria cacete bosta bostas buceta cu cuzao cuzão pqp vsf fdp arrombado arrombada
cagar cagou cagada cagando piroca punheta""".split())


def tem_palavrao(texto: str) -> bool:
    return any(normalizar(t) in _PALAVROES or t.lower() in _PALAVROES for t in _tokens(texto))


def escolher_titulos(brutos: list[tuple[str, str]], convidado: str, palavra_chave: str, texto_thumb: str,
                     vocab: set[str], referencia: str = "", reservas: list[str] = ()) -> list[dict]:
    """Limpa, corrige, confere e ordena as opções; devolve exatamente 3 títulos diferentes.

    brutos:   [(tipo, texto)] na ordem busca, curiosidade, frase.
    reservas: títulos de plano B (o do passo 5 e o do bloco de assunto), usados se faltar opção.
    A ordenação é estável: entre opções igualmente boas, vale a ordem busca > curiosidade > frase,
    e a primeira é a que fica se o "Testar e comparar" não tiver vencedor.
    """
    prontos: list[dict] = []
    vistos: set[str] = set()

    def preparar(tipo: str, texto: str) -> None:
        t = limpar_titulo(texto)
        if not t or _NOME_EXEMPLO in t or t.startswith("Silva:"):
            return
        t = encurtar_nome(corrigir_nome(t, convidado), convidado)
        # regra 2: se o nome/assunto não está no começo, tenta pôr o nome na frente
        if not busca_no_comeco(t, convidado, palavra_chave) and convidado:
            curto = convidado.split()[-1]
            for nome in (convidado, curto):
                candidato = f"{nome}: {t}"
                if len(candidato) <= TITULO_MAX:
                    t = candidato
                    break
        t = ajustar_maiusculas(t, convidado, palavra_chave, referencia)
        chave = normalizar(t)
        if chave in vistos:
            return
        ruins = problemas_do_titulo(t, convidado, palavra_chave, texto_thumb, vocab)
        if any(_PESO[r] >= 100 for r in ruins):
            log.debug("Título descartado (%s): %s", "; ".join(ruins.values()), t)
            return
        vistos.add(chave)
        peso = sum(_PESO[r] for r in ruins) + _BASE_DO_TIPO.get(tipo, 2)
        prontos.append({"texto": t, "tipo": tipo, "avisos": list(ruins.values()), "_peso": peso})

    for tipo, texto in brutos:
        preparar(tipo, texto)
    for texto in reservas:
        if len(prontos) >= N_TITULOS:
            break
        preparar("reserva", texto)
    # último recurso, para nunca faltar opção: nome + assunto
    extras = [f"{convidado} fala sobre {palavra_chave}" if convidado and palavra_chave else "",
              f"{convidado}: o que ele pensa sobre {palavra_chave}" if convidado and palavra_chave else "",
              f"{palavra_chave.capitalize()}: o que foi dito no podcast" if palavra_chave else "",
              "Corte do podcast", "Trecho do podcast", "Melhor momento do podcast"]
    for texto in extras:
        if len(prontos) >= N_TITULOS:
            break
        if texto:
            preparar("reserva", texto)
    prontos.sort(key=lambda d: d["_peso"])
    for d in prontos:
        d.pop("_peso")
    return prontos[:N_TITULOS]


def _frases(texto: str) -> list[str]:
    return [f.strip() for f in re.split(r"(?<=[.!?…])\s+", re.sub(r"\s+", " ", str(texto or "")).strip()) if f.strip()]


def limpar_chamada(chamada: str, convidado: str, palavra_chave: str) -> str:
    """Regra 8: 1-2 frases, até 200 caracteres, com o nome do convidado ou a palavra-chave."""
    chamada = re.sub(r"#\S+", "", str(chamada or "")).replace(_NOME_EXEMPLO, "").strip()
    escolhidas: list[str] = []
    for f in _frases(chamada)[:2]:
        if len(" ".join(escolhidas + [f])) <= CHAMADA_MAX:
            escolhidas.append(f)
    texto = " ".join(escolhidas)
    if not texto and chamada:  # uma frase só, comprida demais: corta numa vírgula ou palavra
        texto = chamada[:CHAMADA_MAX]
        corte = max(texto.rfind(","), texto.rfind(" "))
        texto = texto[:corte].rstrip(" ,;:") + "."
    if texto and convidado:  # o nome do convidado ou o assunto precisa estar nas primeiras linhas
        ditas = {_prefixo(normalizar(t)) for t in _tokens(texto)}
        alvo = _tokens_do_nome(convidado) | {normalizar(t) for t in _tokens(palavra_chave) if len(normalizar(t)) >= 4}
        if not any(_prefixo(a) in ditas for a in alvo):
            texto = f"{convidado}: {texto}"
    return texto


def limpar_resumo(resumo: str) -> str:
    """Regra 9: resumo natural; corta em fim de frase se passar do limite; sem hashtags soltas."""
    resumo = re.sub(r"#\S+", "", str(resumo or "")).strip()
    out: list[str] = []
    for f in _frases(resumo):
        if len(" ".join(out + [f])) > RESUMO_MAX:
            break
        out.append(f)
    return " ".join(out)


def _sem_acento(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


_HASHTAG_DELICADA = ("suicid", "estupr", "pedof", "assassin", "aborto")


def montar_hashtags(convidado: str, canal: str, palavra_chave: str, tags: list[str] = ()) -> list[str]:
    """Regra 11: exatamente 3 hashtags ligadas ao corte (convidado, podcast, assunto), sem espaços."""
    prontas: list[str] = []
    nomes = _tokens_do_nome(convidado) | _tokens_do_nome(canal)
    uma_palavra = [t for t in [palavra_chave, *tags] if len(str(t).split()) == 1 and normalizar(str(t)) not in nomes]
    for bruto in [convidado, canal, *uma_palavra, palavra_chave, *tags, "podcast", "cortes", "entrevista"]:
        h = "".join(_tokens(str(bruto or ""))).lower()
        if normalizar(h).startswith(_HASHTAG_DELICADA):
            continue  # cautela (não é regra da pesquisa): assunto delicado não vira hashtag em destaque
        if 3 <= len(h) <= 30 and not h.isdigit() and ("#" + h) not in prontas:
            prontas.append("#" + h)
        if len(prontas) == N_HASHTAGS:
            break
    return prontas


def montar_tags(convidado: str, canal: str, palavra_chave: str, tags_modelo: list[str] = ()) -> list[str]:
    """Regra 12: de 5 a 8 tags. Nome com e sem acento (erro de digitação comum), podcast e assunto."""
    prontas, vistas = [], set()

    def por(t: str) -> None:
        t = re.sub(r"\s+", " ", str(t or "").replace("#", "").replace(",", " ")).strip().lower()
        if 2 <= len(t) <= 30 and t not in vistas and len(prontas) < TAGS_MAX:
            vistas.add(t)
            prontas.append(t)

    por(convidado)
    if convidado and _sem_acento(convidado) != convidado:
        por(_sem_acento(convidado))
    por(palavra_chave)
    por(canal)
    for t in tags_modelo or []:
        por(corrigir_nome(str(t), convidado, exigir_maiuscula=False))  # "luis felipe ponte" -> nome certo
    for t in ("podcast", "cortes", "cortes podcast", "entrevista", "melhores momentos"):
        if len(prontas) >= TAGS_MIN:
            break
        por(t)
    return prontas


# --------------------------------------------------------------------------- #
# Chamada ao modelo
# --------------------------------------------------------------------------- #
def trechos_do_corte(palavras: list[list], ini: float, fim: float) -> tuple[str, str, str]:
    dur = fim - ini

    def fala(a: float, b: float) -> str:
        return " ".join(w[2] for w in palavras if a <= w[0] < b)

    comeco = fala(ini, ini + 45)
    meio = fala(ini + dur * 0.35, ini + dur * 0.35 + 20) + " … " + fala(ini + dur * 0.65, ini + dur * 0.65 + 20)
    final = fala(fim - 25, fim)
    return comeco, meio, final


def montar_prompt(info: dict, c: dict, palavras: list[list], texto_thumb: str) -> str:
    comeco, meio, final = trechos_do_corte(palavras, c["ini"], c["fim"])
    assunto = c.get("assunto_principal") or c.get("titulo_candidato") or c.get("titulo", "")
    return f"""{_INSTRUCOES}
PODCAST: {info['titulo']} — canal {info['canal']}
ASSUNTO DO CORTE: {assunto} — {c.get('resumo', '')}
TEXTO DA THUMBNAIL: {texto_thumb}

COMEÇO DO CORTE: {comeco}
MEIO: …{meio}…
FIM: …{final}

Agora responda somente com o JSON deste corte."""


def _validar(obj: dict) -> dict:
    """Só recusa (e pede de novo) o que é inaproveitável; o resto o código conserta."""
    titulos = [str(obj.get(k) or "").strip() for k in ("titulo_busca", "titulo_curiosidade", "titulo_frase")]
    aproveitaveis = [t for t in titulos if 8 <= len(limpar_titulo(t)) <= TITULO_MAX and _NOME_EXEMPLO not in t]
    if not aproveitaveis:
        raise ValueError(f"os títulos precisam ter no máximo {TITULO_ALVO} caracteres; escreva títulos mais curtos")
    if len(str(obj.get("resumo") or "").strip()) < 40:
        raise ValueError("o resumo veio vazio ou curto demais")
    return obj


def pedir(info: dict, c: dict, palavras: list[list], cache=None) -> dict:
    """Parte de IA: pede ao modelo os textos crus do corte c. Devolve {} se a IA falhar."""
    texto_thumb = (c.get("thumbnails") or [""])[0]
    try:
        return llm.gerar_json(SISTEMA, montar_prompt(info, c, palavras, texto_thumb), ESQUEMA, _validar,
                              tentativas=2, temperatura=0.6, max_tokens=700,
                              rotulo=f"publicação:{c.get('titulo', '')[:22]}", cache=cache)
    except ErroAmigavel:
        raise
    except Exception as e:
        log.warning("Títulos/descrição do corte “%s” montados sem IA (%s).", c.get("titulo", ""), e)
        return {}


def gerar(info: dict, c: dict, palavras: list[list], cache=None) -> dict:
    """pedir() + aplicar_regras() para um corte só (o programa chama as duas partes separadas)."""
    return aplicar_regras(info, c, palavras, pedir(info, c, palavras, cache))


def aplicar_regras(info: dict, c: dict, palavras: list[list], r: dict, convidado_reserva: str = "") -> dict:
    """Parte de código: confere e conserta a resposta do modelo e monta os textos de publicação.

    Chaves devolvidas: titulo, titulos [{texto, tipo, avisos}], chamada, resumo_publicacao, descricao,
                       convidado, palavra_chave, hashtags, tags.
    Com r vazio (IA falhou), usa os textos do passo 5 como plano B: o programa não para por causa disso.
    """
    texto_thumb = (c.get("thumbnails") or [""])[0]
    titulo_p5, descricao_p5 = c.get("titulo", ""), c.get("descricao", "")
    vocab = vocabulario_do_corte(palavras, c["ini"], c["fim"], [info.get("titulo", ""), info.get("canal", "")])
    convidado = conferir_convidado(r.get("convidado", ""), info, vocab) or convidado_reserva
    palavra_chave = re.sub(r"\s+", " ", str(r.get("palavra_chave") or "")).strip(" .,;:\"").lower()
    if len(palavra_chave.split()) > 4 or len(palavra_chave) > 40:
        palavra_chave = ""
    resumo = limpar_resumo(r.get("resumo", "")) or limpar_resumo(descricao_p5) or c.get("resumo", "")
    resumo = corrigir_nome(resumo, convidado)
    referencia = " ".join([resumo, str(r.get("chamada") or ""), descricao_p5])
    titulos = escolher_titulos(
        [("busca", r.get("titulo_busca", "")), ("curiosidade", r.get("titulo_curiosidade", "")),
         ("frase", r.get("titulo_frase", ""))],
        convidado, palavra_chave, texto_thumb, vocab, referencia,
        reservas=[titulo_p5, c.get("titulo_candidato", "")])
    chamada = limpar_chamada(corrigir_nome(str(r.get("chamada") or ""), convidado), convidado, palavra_chave) \
        or limpar_chamada(c.get("gancho", ""), convidado, palavra_chave) or (_frases(resumo) or [""])[0]
    for t in titulos:
        if t["avisos"]:
            log.debug("Título com ressalva (%s): %s", "; ".join(t["avisos"]), t["texto"])
    return {
        "titulo": titulos[0]["texto"],
        "titulos": titulos,
        "chamada": chamada,
        "resumo_publicacao": resumo,
        "descricao": f"{chamada}\n\n{resumo}".strip(),
        "convidado": convidado,
        "palavra_chave": palavra_chave,
        "hashtags": montar_hashtags(convidado, info.get("canal", ""), palavra_chave, c.get("tags") or []),
        "tags": montar_tags(convidado, info.get("canal", ""), palavra_chave, c.get("tags") or []),
        "titulo_passo5": titulo_p5,
    }


# --------------------------------------------------------------------------- #
# Conferência final (usada pelo programa e pelos testes)
# --------------------------------------------------------------------------- #
def conferir(corte: dict, info: dict, texto_info: str) -> list[str]:
    """Lista o que estiver fora das regras no info.txt de um corte (lista vazia = tudo certo)."""
    ruins: list[str] = []
    titulos = corte.get("titulos") or []
    if len(titulos) != N_TITULOS:
        ruins.append(f"{len(titulos)} títulos (deveriam ser {N_TITULOS})")
    if len({normalizar(t["texto"]) for t in titulos}) != len(titulos):
        ruins.append("títulos repetidos")
    for t in titulos:
        if not (8 <= len(t["texto"]) <= TITULO_MAX):
            ruins.append(f"título com {len(t['texto'])} caracteres: {t['texto']}")
        if tem_emoji(t["texto"]) or "#" in t["texto"]:
            ruins.append(f"emoji ou hashtag no título: {t['texto']}")
        if t["texto"] not in texto_info:
            ruins.append(f"título ausente do info.txt: {t['texto']}")
    descricao = texto_info.split("DESCRIÇÃO", 1)[-1].split("\nTAGS", 1)[0]
    linha_canal = [l for l in descricao.splitlines() if info.get("canal") and info["canal"] in l and "📺" in l]
    if not any(PLACEHOLDER_CANAL in l for l in linha_canal):
        ruins.append(f"falta o marcador {PLACEHOLDER_CANAL} na linha de crédito do canal")
    for obrigatorio, nome in ((info.get("canal"), "nome do canal original"), (info.get("url"), "link do episódio"),
                              (info.get("canal_url"), "link do canal original")):
        if obrigatorio and obrigatorio not in descricao:
            ruins.append(f"falta {nome} nos créditos")
    n_hash = len(re.findall(r"(?<!\S)#(?=[0-9A-Za-zÀ-ÿ]*[A-Za-zÀ-ÿ])[0-9A-Za-zÀ-ÿ]+", descricao))
    if n_hash != N_HASHTAGS:
        ruins.append(f"{n_hash} hashtags na descrição (deveriam ser {N_HASHTAGS})")
    if not (TAGS_MIN <= len(corte.get("tags") or []) <= TAGS_MAX):
        ruins.append(f"{len(corte.get('tags') or [])} tags (deveriam ser de {TAGS_MIN} a {TAGS_MAX})")
    if len(descricao) > DESCRICAO_MAX:
        ruins.append("descrição passa de 5.000 caracteres")
    if not (corte.get("chamada") or "").strip():
        ruins.append("descrição sem a chamada das primeiras linhas")
    return ruins
