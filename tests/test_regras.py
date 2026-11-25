"""Testes das regras determinísticas (sem internet, sem IA).

Rodar:  .venv\\Scripts\\python -m tests.test_regras      (Windows)
        .venv/bin/python -m tests.test_regras            (Mac/Linux)
"""
from cortes_ai import publicacao, saida, selecionar, silencio, transcrever
from cortes_ai.baixar import extrair_id
from cortes_ai.erros import ErroAmigavel


def _linhas(n, dur=20.0):
    return [{"ini": k * dur, "fim": (k + 1) * dur - 0.5, "texto": f"linha {k} " + "palavra " * 30} for k in range(n)]


def teste_links():
    assert extrair_id("https://www.youtube.com/watch?v=rWWeRNY4_1Q&t=10s") == "rWWeRNY4_1Q"
    assert extrair_id("https://youtu.be/rWWeRNY4_1Q?si=abc") == "rWWeRNY4_1Q"
    assert extrair_id("https://www.youtube.com/live/rWWeRNY4_1Q") == "rWWeRNY4_1Q"
    for ruim in ("", "https://www.google.com", "https://www.youtube.com/@canal", "https://www.youtube.com/watch?v=curto"):
        try:
            extrair_id(ruim)
            raise AssertionError(f"deveria recusar: {ruim!r}")
        except ErroAmigavel:
            pass


def teste_janelas():
    linhas = _linhas(220)  # ~73 min
    janelas = selecionar.montar_janelas(linhas)
    assert janelas[0][0] == 0 and janelas[-1][1] == len(linhas) - 1
    for (a, b), (a2, b2) in zip(janelas, janelas[1:]):
        assert a < a2 <= b < b2, "janelas devem avançar e se sobrepor"
        assert linhas[b]["fim"] - linhas[a2]["ini"] >= 150, "sobreposição de ~3 min"
    for a, b in janelas:
        assert linhas[b]["fim"] - linhas[a]["ini"] <= 15 * 60 * 1.3


def teste_validar_janela():
    v = selecionar.validar_janela
    r = v({"blocos": [{"inicio": 12, "titulo": "B", "resumo": "", "nota": 99},
                      {"inicio": 1, "titulo": "A", "resumo": "", "nota": 5},
                      {"inicio": 500, "titulo": "fora", "resumo": "", "nota": 5},
                      {"inicio": 12, "titulo": "repetido", "resumo": "", "nota": 5}]}, 0, 30)
    assert [x["inicio"] for x in r["blocos"]] == [0, 12], r
    assert r["blocos"][1]["nota"] == 10
    for ruim in ({"blocos": []}, {"blocos": [{"inicio": 900, "titulo": "x", "resumo": "", "nota": 5}]},
                 {"blocos": [{"inicio": 0, "titulo": "só um", "resumo": "", "nota": 5}]}):
        try:
            v(ruim, 0, 30)
            raise AssertionError("deveria recusar")
        except ValueError:
            pass


def teste_costurar():
    linhas = _linhas(56)
    janelas = [(0, 31), (24, 55)]
    b = lambda i, t, n=5: {"inicio": i, "titulo": t, "resumo": "", "nota": n}
    todos = selecionar.costurar(janelas, [[b(0, "a"), b(8, "b"), b(20, "c"), b(28, "d")],
                                          [b(24, "x"), b(30, "e"), b(40, "f")]], linhas)
    # cobre todas as linhas, sem buracos nem sobreposição
    cobertas = [n for x in todos for n in range(x["lin_ini"], x["lin_fim"] + 1)]
    assert cobertas == list(range(56)), cobertas
    assert [x["titulo"] for x in todos] == ["a", "b", "c", "e", "f"], [x["titulo"] for x in todos]
    # janela que falhou não derruba o resto
    todos = selecionar.costurar(janelas, [[b(0, "a"), b(8, "b")], []], linhas)
    assert [n for x in todos for n in range(x["lin_ini"], x["lin_fim"] + 1)] == list(range(56))


def teste_montar_cortes():
    # 30 blocos de 2,5 min = 75 min
    blocos = [{"id": k + 1, "titulo": f"t{k + 1}", "resumo": "r", "nota": 5, "ini": k * 150.0, "fim": (k + 1) * 150.0}
              for k in range(30)]
    ordem = [(10, 9), (11, 9), (3, 8), (30, 8), (20, 6), (25, 5), (1, 4)]
    cortes = selecionar.montar_cortes(blocos, ordem, n_min=5, n_max=6, dur_min=300, dur_max=1800)
    assert len(cortes) == 5, len(cortes)  # o 6º só entraria com nota >= 7
    for c in cortes:
        assert 300 <= c["fim"] - c["ini"] <= 1800
    em_ordem = sorted(cortes, key=lambda c: c["ini"])
    for x, y in zip(em_ordem, em_ordem[1:]):
        assert x["fim"] <= y["ini"], "cortes não podem se sobrepor"
    assert cortes[0]["ini"] == 9 * 150.0, "o corte começa no início do assunto escolhido"
    # assunto no fim do vídeo cresce para trás
    ultimo = next(c for c in cortes if c["titulo"] == "t30")
    assert ultimo["fim"] == 30 * 150.0 and ultimo["ini"] < 29 * 150.0
    # bloco seguinte fraco (despedida) e anterior bom: cresce para trás, e a despedida fica de fora
    b2 = [dict(x) for x in blocos]
    b2[29]["nota"], b2[27]["nota"], b2[26]["nota"] = 2, 8, 8    # bloco 30 = despedida; 27 e 28 = bons
    c = selecionar.montar_cortes(b2, [(29, 9)], 1, 1, 300, 1800)[0]
    assert c["ini"] == 26 * 150.0 and c["fim"] == 29 * 150.0, c
    # duração máxima respeitada mesmo com bloco gigante
    gigante = [{"id": 1, "titulo": "g", "resumo": "", "nota": 9, "ini": 0.0, "fim": 4000.0}]
    c = selecionar.montar_cortes(gigante, [(1, 9)], 1, 1, 300, 1800)[0]
    assert c["fim"] - c["ini"] <= 1800


def teste_ajuste_de_bordas():
    # fala contínua com pausas a cada 5 palavras; "então" logo depois de uma pausa
    palavras, t = [], 0.0
    for k in range(400):
        txt = "então" if k == 100 else ("que" if k % 5 == 4 and k % 10 != 9 else f"p{k}")
        palavras.append([round(t, 2), round(t + 0.3, 2), txt])
        t += 0.35 + (0.8 if k % 5 == 4 else 0.0)
    silencios = [[palavras[k][1] + 0.05, palavras[k + 1][0] - 0.05] for k in range(4, 399, 5)]
    aj = silencio.Ajustador(palavras, silencios, False, t)
    alvo = palavras[100][0]
    ini = aj.ajustar_inicio(alvo)
    dentro = any(s0 - 0.01 <= ini <= s1 + 0.3 for s0, s1 in silencios)
    assert dentro or ini >= palavras[101][0] - 0.3, "o corte deve começar num silêncio (ou logo após a muleta)"
    primeira = next(w for w in palavras if w[0] >= ini - 0.05)
    assert primeira[2] != "então", "não pode começar com muleta"
    fim = aj.ajustar_fim(palavras[250][0])
    ultima = [w for w in palavras if w[0] < fim][-1]
    assert ultima[2] != "que", "não pode terminar em conectivo"
    assert any(s0 - 0.01 <= fim <= s1 + 0.01 for s0, s1 in silencios), "o corte deve terminar num silêncio"
    # limites respeitados
    assert aj.ajustar_inicio(50.0, minimo=52.0) >= 52.0
    assert aj.ajustar_fim(50.0, minimo=10.0, maximo=45.0) <= 45.0


def teste_finalizar_limites():
    palavras, t = [], 0.0
    for k in range(6000):
        palavras.append([round(t, 2), round(t + 0.3, 2), f"p{k}"])
        t += 0.35 + (0.7 if k % 7 == 6 else 0.0)
    silencios = [[palavras[k][1] + 0.05, palavras[k + 1][0] - 0.05] for k in range(6, 5999, 7)]
    aj = silencio.Ajustador(palavras, silencios, False, t)
    brutos = [{"titulo": "a", "ini": 100.0, "fim": 420.0, "nota": 8},
              {"titulo": "b", "ini": 421.0, "fim": 760.0, "nota": 7},
              {"titulo": "c", "ini": 1500.0, "fim": 1500.0 + 2000, "nota": 9}]
    finais = selecionar.finalizar_limites(brutos, aj, 300, 1800, t)
    assert len(finais) == 3
    for c in finais:
        assert 300 <= c["fim"] - c["ini"] <= 1800, c
    finais.sort(key=lambda c: c["ini"])
    for x, y in zip(finais, finais[1:]):
        assert x["fim"] <= y["ini"]


def teste_textos():
    assert selecionar.limpar_texto_thumb('"perdi 2 milhões."') == "PERDI 2 MILHÕES"
    assert selecionar.limpar_texto_thumb("uma") is None
    assert selecionar.limpar_texto_thumb("um texto muito longo que não cabe em thumbnail") is None
    t = selecionar.limpar_titulo("Um título enorme que passa bastante do limite de setenta caracteres do YouTube #tag")
    assert len(t) <= 70 and "#" not in t
    info = {"canal": "Canal X", "titulo": "Entrevista com Fulano de Tal sobre negócios", "tags": ["a1", "b2", "c3"]}
    tags = selecionar.completar_tags(["Dinheiro", "#dinheiro", "x"], info)
    assert 10 <= len(tags) <= 15 and len(set(tags)) == len(tags), tags
    assert len(selecionar.completar_tags([f"tag {k}" for k in range(30)], info)) == 15
    v = selecionar.validar_textos({"titulo": "Um bom título de corte", "gancho": "g", "descricao": "d",
                                   "tags": [], "thumbnails": ["perdi tudo", "x", "PERDI TUDO"]})
    assert v["thumbnails"] == ["PERDI TUDO"]
    # conferência de grafia dos textos de thumbnail
    vocab = selecionar.montar_vocabulario([[0, 1, "vários"], [1, 2, "livros"], [2, 3, "filósofo"]], ["A jornada"])
    assert selecionar.corrigir_thumb("LEIO VARIOS LIVROS", vocab)[0].endswith("VÁRIOS LIVROS")
    assert selecionar.corrigir_thumb("NAO E FACIL", vocab) == ("NÃO E FÁCIL", 0)
    assert selecionar.corrigir_thumb("A JOORNADA DO FILÓSOFO", vocab)[1] == 1
    assert selecionar.corrigir_thumb("A JORNADA DO FILÓSOFO", vocab)[1] == 0


def teste_saida():
    assert saida.slug("O Dia em que QUASE desisti! (ção)") == "o-dia-em-que-quase-desisti-cao"
    assert saida.nome_de_pasta('A: "B" / C? | D*') == "A B C D"
    corte = {"titulo": "T", "descricao": "D.", "tags": [f"t{k}" for k in range(12)]}
    info = {"titulo": "Podcast", "canal": "Canal", "canal_url": "", "url": "https://youtu.be/x"}
    txt = saida.texto_info(corte, info)
    assert "TÍTULO" in txt and "https://youtu.be/x" in txt and "Canal" in txt and "t11" in txt


# --------------------------------------------------------------------------- #
# Regras de título e descrição para o YouTube (docs/seo_youtube.md, seção 6)
# --------------------------------------------------------------------------- #
_INFO_PUB = {"titulo": "A FILOSOFIA PARA PENSAR MELHOR (LUIZ FELIPE PONDÉ) | JOTA JOTA PODCAST #45", "canal": "Joel Jota",
             "canal_url": "https://www.youtube.com/channel/UCxxxx", "url": "https://www.youtube.com/watch?v=abc"}
_FALA_PUB = ("eu larguei a medicina no quinto ano para virar filósofo e todo mundo achou que eu estava maluco "
             "jornalistas e publicitários vão para o inferno juntos a leitura mudou a minha vida").split()
_PALAVRAS_PUB = [[k * 0.5, k * 0.5 + 0.4, w] for k, w in enumerate(_FALA_PUB)]


def _vocab_pub():
    return publicacao.vocabulario_do_corte(_PALAVRAS_PUB, 0, 999, [_INFO_PUB["titulo"], _INFO_PUB["canal"]])


def teste_titulo_limpeza_e_maiusculas():
    p = publicacao
    # regra 4: sem emoji e sem hashtag; aspas em volta do título inteiro e ponto final saem
    assert p.limpar_titulo('  "Pondé largou a medicina 😱 #filosofia."  ') == "Pondé largou a medicina"
    assert p.limpar_titulo('Pondé: "eu estava maluco"') == 'Pondé: "eu estava maluco"'  # aspas internas ficam
    assert not p.tem_emoji(p.limpar_titulo("Olha isso 🔥🔥"))
    # regra 5: MAIÚSCULAS só no nome do convidado; siglas curtas ficam; nomes próprios não são rebaixados errado
    t = p.ajustar_maiusculas("O MOTIVO que fez PONDÉ CRITICAR o STF no BRASIL", "Luiz Felipe Pondé", "stf",
                             referencia="Pondé fala do Brasil.")
    assert t == "O motivo que fez PONDÉ criticar o STF no Brasil", t
    assert p.ajustar_maiusculas("luiz felipe pondé explica tudo", "Luiz Felipe Pondé", "") \
        == "LUIZ FELIPE PONDÉ explica tudo"
    # sem convidado conhecido, o destaque vai para a palavra-chave
    assert p.ajustar_maiusculas("A verdade sobre a inflação no país", "", "inflação") \
        == "A verdade sobre a INFLAÇÃO no país"
    assert p.nome_formatado("LUIZ FELIPE DE PONDÉ") == "Luiz Felipe de Pondé"
    # o nome sai sempre na forma completa do título do vídeo; nome inventado é recusado
    assert p.conferir_convidado("Felipe Pondé", _INFO_PUB, set()) == "Luiz Felipe Pondé"
    assert p.conferir_convidado("Fulano Inexistente", _INFO_PUB, set()) == ""
    assert p.conferir_convidado("João Silva", _INFO_PUB, set()) == ""        # nome do exemplo do prompt
    # regra 3: título acima do alvo troca o nome completo pelo sobrenome
    longo = 'Luiz Felipe Pondé: "Jornalistas e publicitários vão pro inferno juntos"'
    assert p.encurtar_nome(longo, "Luiz Felipe Pondé") == 'Pondé: "Jornalistas e publicitários vão pro inferno juntos"'
    assert p.encurtar_nome("Luiz Felipe Pondé explica a medicina", "Luiz Felipe Pondé") \
        == "Luiz Felipe Pondé explica a medicina"                              # já cabe: não mexe
    assert p.encurtar_nome("Felipe Pondé explica por que deixou a medicina para ser filósofo", "Luiz Felipe Pondé") \
        == "Pondé explica por que deixou a medicina para ser filósofo"         # nome parcial também encurta


def teste_titulo_busca_fidelidade_thumbnail():
    p = publicacao
    vocab = _vocab_pub()
    # regra 2: nome ou assunto nos primeiros 45 caracteres
    assert p.busca_no_comeco("Pondé explica por que largou a medicina", "Luiz Felipe Pondé", "medicina")
    assert p.busca_no_comeco("Largar a medicina foi loucura?", "", "medicina")
    assert not p.busca_no_comeco("Ninguém esperava ouvir isso num podcast de filosofia hoje, diz Pondé",
                                 "Luiz Felipe Pondé", "medicina")
    # regra 1: palavras do título precisam existir na fala do corte (tolerando flexões)
    assert p.fidelidade("Pondé largou a medicina para virar filósofo", vocab, {"ponde"}) == 1.0
    assert p.fidelidade("Pondé revela fortuna escondida em paraíso fiscal", vocab, {"ponde"}) < 0.5
    # regra 6: título que repete a thumbnail
    assert p.repete_thumbnail("Pondé: larguei a medicina", "LARGUEI A MEDICINA", "Luiz Felipe Pondé")
    assert not p.repete_thumbnail("Pondé e o dia em que todos o acharam maluco", "LARGUEI A MEDICINA")
    # mesmo assunto com informação nova não é repetição
    assert not p.repete_thumbnail("Pondé conta como uma coluna salvou uma vida", "SALVOU UMA VIDA", "Luiz Felipe Pondé")
    ruins = p.problemas_do_titulo("x" * 75, "", "", "", vocab)
    assert "tamanho" in ruins
    assert "longo" in p.problemas_do_titulo("Pondé explica " + "a medicina " * 5, "Pondé", "medicina", "", vocab)


def teste_escolher_titulos():
    p = publicacao
    vocab = _vocab_pub()
    conv, chave, thumb = "Luiz Felipe Pondé", "medicina", "LARGUEI A MEDICINA"
    brutos = [("busca", "Pondé: larguei a medicina"),                                    # repete a thumbnail
              ("curiosidade", "PONDÉ E O DIA EM QUE TODO MUNDO ACHOU QUE ELE ESTAVA MALUCO 😱"),
              ("frase", '"Jornalistas e publicitários vão para o inferno juntos", diz Pondé')]
    ts = p.escolher_titulos(brutos, conv, chave, thumb, vocab, reservas=["Reserva do passo 5"])
    assert len(ts) == 3 and len({t["texto"] for t in ts}) == 3
    for t in ts:
        assert 8 <= len(t["texto"]) <= p.TITULO_MAX, t
        assert not p.tem_emoji(t["texto"]) and "#" not in t["texto"]
        palavras = [w for w in t["texto"].replace('"', " ").split() if len(w) >= 3 and w.isalpha()]
        assert sum(w.isupper() for w in palavras) / len(palavras) <= 0.5, t   # nunca tudo em maiúsculas
        assert "PONDÉ" in t["texto"]                                           # só o nome fica destacado
    assert not p.repete_thumbnail(ts[0]["texto"], thumb), "o principal não pode repetir a thumbnail"
    # título longo demais é descartado e a reserva entra; título infiel vai para o fim
    longo = "Pondé explica com todos os detalhes possíveis por que resolveu largar a medicina no quinto ano"
    ts = p.escolher_titulos([("busca", longo), ("curiosidade", "Pondé revela fortuna escondida em paraíso fiscal"),
                             ("frase", 'Pondé: "eu larguei a medicina no quinto ano"')], conv, chave, "", vocab,
                            reservas=["Pondé e a medicina: por que ele virou filósofo"])
    assert len(ts) == 3 and all(len(t["texto"]) <= p.TITULO_MAX for t in ts)
    assert "fortuna" in ts[-1]["texto"] and ts[-1]["avisos"], ts
    # regra 2 consertada pelo código: sem nome/assunto no começo, o nome vai para a frente
    ts = p.escolher_titulos([("busca", "Todo mundo achou que eu estava maluco")], conv, chave, "", vocab)
    assert ts[0]["texto"].upper().startswith(("LUIZ FELIPE PONDÉ:", "PONDÉ:")), ts[0]
    # o título antigo (reserva) não passa na frente de um título novo que só está um pouco longo
    ts = p.escolher_titulos([("busca", "Luiz Felipe Pondé explica com calma por que largou a medicina no quinto ano")],
                            conv, chave, "", vocab, reservas=["Título antigo sobre a medicina"])
    assert ts[0]["tipo"] == "busca" and len(ts[0]["texto"]) <= p.TITULO_MAX, ts
    # a frase citada nunca passa na frente de um título direto sem defeitos (pode ser fala de terceiros)
    ts = p.escolher_titulos([("frase", 'Pondé: "eu larguei a medicina"'),
                             ("busca", "Pondé conta por que virou filósofo no quinto ano")], conv, chave, "", vocab)
    assert ts[0]["tipo"] == "busca", ts
    # regra 4: título com palavrão é descartado
    ts = p.escolher_titulos([("busca", "Pondé: isso é groselha pra caralho"),
                             ("curiosidade", "Pondé e a medicina no quinto ano")], conv, chave, "", vocab)
    assert all(not p.tem_palavrao(t["texto"]) for t in ts) and len(ts) == 3
    # nome do convidado escrito errado pelo modelo é consertado (só quando colado em outra parte do nome)
    assert p.corrigir_nome("Luí Felipe Ponde conta tudo", conv) == "Luiz Felipe Pondé conta tudo"
    assert p.corrigir_nome("LUIS FELIPE PONDÉ: ler ou estudar?", conv) == "LUIZ FELIPE PONDÉ: ler ou estudar?"
    assert p.corrigir_nome("a ponte entre a medicina e a filosofia", conv) == "a ponte entre a medicina e a filosofia"
    assert p.conferir_convidado("Luis Felipe Pondé", _INFO_PUB, set()) == "Luiz Felipe Pondé"
    assert "luiz felipe pondé" in p.montar_tags(conv, "Joel Jota", chave, ["luis felipe ponte", "filosofia"])
    assert "luis felipe ponte" not in p.montar_tags(conv, "Joel Jota", chave, ["luis felipe ponte", "filosofia"])
    # nome reserva: o mais citado entre os cortes do vídeo
    assert p.convidado_mais_citado([{"convidado": "Luiz Felipe Pondé"}, {"convidado": "Felipe Pondé"},
                                    {"convidado": ""}, {"convidado": "Joel Jota"}], _INFO_PUB) == "Luiz Felipe Pondé"
    # mesmo sem nenhuma resposta do modelo sempre saem 3 títulos
    assert len(p.escolher_titulos([], conv, chave, "", vocab)) == 3
    assert len(p.escolher_titulos([], "", "", "", set())) == 3
    # o modelo só é chamado de novo quando NADA se aproveita
    try:
        p._validar({"titulo_busca": "x" * 90, "titulo_curiosidade": "", "titulo_frase": "y" * 80, "resumo": "r" * 60})
        raise AssertionError("deveria recusar")
    except ValueError:
        pass
    p._validar({"titulo_busca": "Pondé largou a medicina", "titulo_curiosidade": "", "titulo_frase": "",
                "resumo": "r" * 60})


def teste_descricao_hashtags_tags():
    p = publicacao
    conv, canal, chave = "Luiz Felipe Pondé", "Joel Jota", "medicina"
    # regra 8: chamada curta, com o nome do convidado ou o assunto
    ch = p.limpar_chamada("Ele largou tudo no quinto ano. E não se arrepende. Terceira frase sobra. #tag", conv, chave)
    assert len(ch) <= p.CHAMADA_MAX and "Terceira" not in ch and "#" not in ch
    assert "Pondé" in ch, ch
    assert p.limpar_chamada("Pondé conta por que largou a medicina.", conv, chave) == "Pondé conta por que largou a medicina."
    assert len(p.limpar_chamada("palavra " * 80, conv, chave)) <= p.CHAMADA_MAX + len(conv) + 3
    # regra 9: resumo corta em fim de frase
    r = p.limpar_resumo("Frase um. " + "Frase bem comprida " * 60 + ". Frase três.")
    assert r == "Frase um."
    # regra 11: exatamente 3 hashtags, sem espaços
    hs = p.montar_hashtags(conv, canal, chave, ["filosofia"])
    assert hs == ["#luizfelipepondé", "#joeljota", "#medicina"], hs
    assert len(p.montar_hashtags("", "", "", [])) == 3
    # assunto de duas palavras não vira hashtag colada se houver um de uma palavra só
    assert p.montar_hashtags(conv, canal, "ética jornalística", ["joel jota", "filosofia"])[2] == "#filosofia"
    assert p.montar_hashtags(conv, canal, "suicídio", ["coluna"]) == ["#luizfelipepondé", "#joeljota", "#coluna"]
    # regra 12: de 5 a 8 tags, com o nome sem acento
    tags = p.montar_tags(conv, canal, chave, [f"tag {k}" for k in range(20)])
    assert p.TAGS_MIN <= len(tags) <= p.TAGS_MAX and "luiz felipe ponde" in tags and len(set(tags)) == len(tags)
    assert p.TAGS_MIN <= len(p.montar_tags("", "Canal", "", [])) <= p.TAGS_MAX


def teste_info_txt_completo():
    p = publicacao
    c = {"ini": 0.0, "fim": 999.0, "titulo": "Título antigo do passo 5 sobre Pondé e a medicina",
         "descricao": "Descrição antiga do passo 5.", "resumo": "Resumo do bloco.", "gancho": "Eu larguei a medicina.",
         "thumbnails": ["LARGUEI A MEDICINA"], "tags": ["filosofia", "medicina", "carreira"],
         "titulo_candidato": "De médico a filósofo"}
    resposta = {"resumo": "Luiz Felipe Pondé conta por que largou a medicina no quinto ano para virar filósofo. "
                          "Ele lembra que todo mundo achou que ele estava maluco.",
                "convidado": "LUIZ FELIPE PONDÉ", "palavra_chave": "Medicina",
                "titulo_busca": "Pondé explica por que largou a medicina para virar filósofo",
                "titulo_curiosidade": "Pondé e o dia em que todo mundo achou que ele estava maluco",
                "titulo_frase": 'Pondé: "Eu larguei a medicina no quinto ano"',
                "chamada": "Pondé largou a medicina no quinto ano. Por quê?"}
    original = publicacao.llm.gerar_json
    try:
        publicacao.llm.gerar_json = lambda *a, **k: dict(resposta)
        c.update(p.gerar(_INFO_PUB, c, _PALAVRAS_PUB))
        txt = saida.texto_info(c, _INFO_PUB)
        assert p.conferir(c, _INFO_PUB, txt) == [], p.conferir(c, _INFO_PUB, txt)
        assert c["convidado"] == "Luiz Felipe Pondé" and c["palavra_chave"] == "medicina"
        assert txt.index("TÍTULO") < txt.index("TÍTULOS ALTERNATIVOS") < txt.index("DESCRIÇÃO") < txt.index("TAGS")
        assert txt.count("Opção ") == 2
        desc = txt.split("DESCRIÇÃO", 1)[1].split("\nTAGS", 1)[0]
        corpo = desc.split("\n", 3)[3]                    # depois da linha de aviso e da linha em branco
        assert corpo.startswith(c["chamada"]), "as primeiras linhas da descrição são a chamada"
        assert f"Canal original: Joel Jota {p.PLACEHOLDER_CANAL}" in desc
        assert _INFO_PUB["canal_url"] in desc and _INFO_PUB["url"] in desc
        assert desc.rstrip().splitlines()[-1] == " ".join(c["hashtags"]), "as 3 hashtags fecham a descrição"
        assert "#45" not in desc, "o '#45' do nome do podcast viraria uma quarta hashtag"
        # a conferência acusa quando falta o marcador, sobra hashtag ou falta título
        assert p.conferir(c, _INFO_PUB, txt.replace(f"Joel Jota {p.PLACEHOLDER_CANAL}", "Joel Jota"))
        assert p.conferir(c, _INFO_PUB, txt.replace("#medicina", "#medicina #extra"))
        assert p.conferir(dict(c, titulos=c["titulos"][:2]), _INFO_PUB, txt)
        # plano B: se a IA falhar, o info.txt continua completo e dentro das regras
        def falha(*a, **k):
            raise RuntimeError("sem IA")
        publicacao.llm.gerar_json = falha
        c2 = dict(c, titulo="Título antigo do passo 5 sobre Pondé e a medicina", descricao="Descrição antiga do passo 5.")
        for chave in ("titulos", "chamada", "resumo_publicacao", "hashtags", "convidado", "palavra_chave"):
            c2.pop(chave, None)
        c2.update(p.gerar(_INFO_PUB, c2, _PALAVRAS_PUB))
        txt2 = saida.texto_info(c2, _INFO_PUB)
        assert p.conferir(c2, _INFO_PUB, txt2) == [], p.conferir(c2, _INFO_PUB, txt2)
    finally:
        publicacao.llm.gerar_json = original


def teste_legenda_json3(tmp):
    import json
    arq = tmp / "l.json3"
    arq.write_text(json.dumps({"events": [
        {"tStartMs": 0, "dDurationMs": 3000, "segs": [{"utf8": "fala", "acAsrConf": 0}, {"utf8": " galera", "tOffsetMs": 400}]},
        {"tStartMs": 2000, "dDurationMs": 100, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 3000, "dDurationMs": 2000, "segs": [{"utf8": "[Música]", "acAsrConf": 0}, {"utf8": " tudo", "tOffsetMs": 500}]},
        {"tStartMs": 6000, "dDurationMs": 2000, "segs": [{"utf8": "Olá, tudo bem?\nSim."}]},
    ]}), encoding="utf-8")
    p = transcrever.ler_json3(arq)
    assert [w[2] for w in p] == ["fala", "galera", "tudo", "Olá,", "tudo", "bem?", "Sim."], p
    assert p[1][0] == 0.4 and p[2][0] == 3.5
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(p, p[1:])), "palavras não se sobrepõem"
    assert transcrever.tem_pontuacao(p)


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    for nome, f in list(globals().items()):
        if nome.startswith("teste_") and nome != "teste_legenda_json3":
            f()
            print("ok ", nome)
    with tempfile.TemporaryDirectory() as d:
        teste_legenda_json3(Path(d))
        print("ok  teste_legenda_json3")
    print("\nTodos os testes passaram.")
