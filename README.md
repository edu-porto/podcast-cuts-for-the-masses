# ✂️ Cortes AI

Transforma um podcast brasileiro do YouTube em **cortes prontos para postar**: você cola o link, clica em um botão e recebe uma pasta com 5 a 10 vídeos de 5 a 30 minutos, cada um com thumbnail, título, descrição e tags.

**100% gratuito e local.** Nada de assinatura, chave de API ou envio dos seus dados: tudo roda no seu computador.

---

## Instalação (uma vez só)

Precisa de internet e de uns **12 GB livres** no disco.

**Windows**

1. Dê dois cliques em **`instalar.bat`**.
2. Espere terminar (de 10 a 40 minutos, dependendo da internet: ele baixa uma inteligência artificial de 6,6 GB e um transcritor de áudio de 0,5 GB).
3. Se o Windows pedir permissão para instalar o Python ou o Ollama, aceite.

**Mac / Linux**

1. Abra o Terminal na pasta do Cortes AI.
2. Digite `bash instalar.sh` e aperte Enter.

O instalador prepara tudo sozinho: as bibliotecas do programa (em uma pasta própria, `.venv`), o **ffmpeg** (corta os vídeos), o **Ollama** (roda a inteligência artificial) e o modelo de IA certo para o seu computador. Se algo der errado, ele explica em português o que fazer; pode rodar de novo quantas vezes quiser, pois ele continua de onde parou.

## Como usar

1. Dê dois cliques em **`abrir.bat`** (Windows) ou rode `bash abrir.sh` (Mac/Linux). A tela abre sozinha no navegador.
2. Cole o link do podcast do YouTube.
3. Clique em **Gerar cortes** e acompanhe a barra de progresso.
4. Quando terminar, cada corte aparece com thumbnail, título, duração, player e os botões:
   - **Baixar vídeo** / **Thumbnail**: salva uma cópia na pasta de downloads;
   - **Abrir pasta**: mostra os arquivos do corte;
   - **Gerar outra thumbnail**: troca o texto e/ou a imagem;
   - **Excluir**: manda o corte para a Lixeira.

A seção **Avançado** (fechada por padrão) permite escolher a quantidade de cortes e a duração mínima/máxima, e reabrir vídeos já processados. Não precisa mexer nela.

Deixe a janela preta aberta enquanto usa. Para fechar o programa, feche essa janela.

### Onde ficam os arquivos

```
output/
  <título do vídeo>/
    01_<titulo-do-corte>/
      corte.mp4        vídeo pronto (H.264 + AAC, 16:9, mesma resolução do original)
      thumbnail.jpg    1280x720
      info.txt         3 títulos, descrição (com crédito e link do original) e tags: é só copiar e colar no YouTube
    02_.../
```

A numeração vai do melhor corte (01) para o menos forte, segundo a nota da IA.

Outras pastas: `cache/` guarda o vídeo original e a transcrição (pode apagar para liberar espaço; se usar o mesmo link de novo, ele baixa outra vez), `logs/` guarda o registro técnico e `modelos/` guarda o transcritor de áudio.

**Antes de publicar:** na descrição, troque `[@CANAL_ORIGINAL]` pelo @ do canal original (por exemplo `@nomedocanal`). No YouTube isso vira um link para o canal. Os 2 títulos alternativos servem para o “Testar e comparar” do YouTube Studio (só no computador): ele mostra os 3 títulos para públicos diferentes e fica com o de maior tempo de exibição.

> **Direitos autorais e monetização:** publique cortes apenas de canais que autorizam (muitos podcasts têm regras para canais de cortes). O `info.txt` já credita o canal original e coloca o link do episódio completo. Atenção: pelas políticas do YouTube, trechos de vídeos de outras pessoas sem comentário ou edição própria podem não ser aceitos para monetização, mesmo com autorização. Os detalhes e as fontes estão em [`docs/seo_youtube.md`](docs/seo_youtube.md).

---

## Computador mínimo

| | Mínimo | Recomendado |
|---|---|---|
| Sistema | Windows 10/11, macOS 12+ ou Linux, 64 bits | — |
| Memória (RAM) | 8 GB | 16 GB ou mais |
| Placa de vídeo | não precisa | NVIDIA com 8 GB+ ou Mac com Apple Silicon |
| Disco livre | 12 GB + ~2 GB por hora de podcast (vídeo original + cortes) | SSD |
| Internet | só para instalar e para baixar cada vídeo | — |

O modelo de IA é escolhido sozinho, conforme a máquina:

| Seu computador | Modelo | Tamanho |
|---|---|---|
| Menos de 12 GB de RAM, sem placa de vídeo | `qwen3.5:4b` | 3,3 GB |
| 12 GB de RAM ou mais, ou placa de vídeo com 8 GB+ | `qwen3.5:9b` | 6,6 GB |
| Placa de vídeo com 20 GB+ ou Apple Silicon com 48 GB+ | `qwen3.5:27b` | 17 GB |

## Tempo de processamento (por hora de podcast)

Medido em um notebook comum **sem placa de vídeo** (Intel Core 7 150U, 16 GB de RAM, Windows 11), com um podcast de 72 minutos em 1080p que gerou 8 cortes (52 minutos de vídeo no total):

| Etapa | Tempo |
|---|---|
| Baixar o vídeo (743 MB) | 3 min (depende da internet) |
| Ler a conversa (legendas do YouTube + silêncios) | 10 s |
| Escolher os melhores momentos: analisar a conversa | 25 min |
| Escolher os melhores momentos: ranking, frases exatas e textos dos 8 cortes | 20 min |
| Escrever os 3 títulos e a descrição dos 8 cortes | 13 min (12 a 16 em três execuções) |
| Cortar os vídeos (8 cortes) | 8 min |
| Criar as thumbnails | 1 min |
| **Total** | **cerca de 70 min** |

(Soma dos tempos de cada etapa, medidos em execuções de teste; não foi uma única execução cronometrada do início ao fim.)

- **Sem placa de vídeo:** conte com cerca de **1 hora por hora de podcast** (cada corte custa uns 2 minutos a mais por causa dos títulos e da descrição). Quase todo esse tempo é da inteligência artificial. Pode deixar rodando e fazer outra coisa.
- **Com placa de vídeo NVIDIA ou Apple Silicon:** a etapa da IA fica várias vezes mais rápida (estimativa: 10 a 15 minutos no total por hora de podcast; não medido).
- **Vídeo sem legendas no YouTube:** o áudio inteiro é transcrito no próprio computador. Num teste de 3 minutos de áudio, isso levou cerca de metade da duração do áudio (com o computador ocupado); conte com uns 25 a 30 minutos a mais por hora de podcast sem placa de vídeo. A tela avisa e mostra a estimativa.
- O mesmo link nunca é processado duas vezes: o download e a transcrição ficam guardados. Se o processo for interrompido (fechou a janela, acabou a bateria), clique em **Gerar cortes** de novo com o mesmo link e ele **continua de onde parou**.

---

## Problemas comuns

| O que aparece | O que fazer |
|---|---|
| “O Cortes AI ainda não foi instalado” | Rode `instalar.bat` (ou `bash instalar.sh`) antes do `abrir`. |
| “Não consegui baixar o vídeo” | O YouTube muda com frequência. Rode o instalador de novo: ele atualiza o baixador. |
| “Este vídeo é privado / tem restrição de idade / é exclusivo para membros” | Esses vídeos exigem login no YouTube e não podem ser baixados. Use outro. |
| “O YouTube bloqueou o download por alguns minutos” | Espere uns 15 minutos e tente de novo. |
| “Sem conexão com a internet” | Confira a rede. A internet só é usada para baixar o vídeo. |
| “O Ollama não está instalado / não respondeu” | Rode o instalador de novo. Se continuar, instale manualmente em https://ollama.com/download e reinicie o computador. |
| “Faltou memória para rodar a inteligência artificial” | Feche outros programas (principalmente o navegador com muitas abas) e tente de novo. |
| “Há pouco espaço livre no disco” | Apague a pasta `cache/` e os cortes antigos em `output/`. |
| Está demorando muito | É normal sem placa de vídeo (veja a tabela acima). A barra mostra a etapa e o tempo. Para ficar mais rápido, veja “Modelo mais leve” abaixo. |
| O corte começou ou terminou num ponto estranho | Aumente a duração mínima em **Avançado** e gere de novo, ou exclua o corte. |
| A thumbnail não ficou boa | Clique em **Gerar outra thumbnail**: cada clique troca o texto e/ou a imagem. |
| A janela preta fechou sozinha | Abra de novo. Se repetir, rode o instalador outra vez e veja `logs/cortes_ai.log`. |

Se precisar de ajuda, envie o arquivo **`logs/cortes_ai.log`**: ele registra tudo o que o programa fez.

### Modelo mais leve (ou mais forte)

Para trocar o modelo de IA, crie na pasta do programa um arquivo `configuracao.json` com, por exemplo:

```json
{"modelo": "qwen3.5:4b"}
```

e rode o instalador de novo para baixá-lo. Um modelo menor, como o `qwen3.5:4b`, deve ser mais rápido e escolher um pouco pior (não medi). Qualquer modelo do Ollama serve (por exemplo `gemma4:12b`).

---

## Como funciona (decisões técnicas)

1. **Download** — `yt-dlp`, melhor qualidade até 1080p (preferindo H.264), com título, canal, duração e capítulos. Cache por ID do vídeo em `cache/<id>/`.
2. **Transcrição** — primeiro as legendas do YouTube (manuais; se não houver, as automáticas em português), que já trazem o tempo de cada palavra. Só se não houver legendas o áudio é transcrito localmente com `faster-whisper` (`large-v3` na placa de vídeo, `small` no processador).
3. **Escolha dos momentos** — modelo local via Ollama, com `num_ctx` fixado em 8192, saída em JSON com esquema, validação de cada resposta e até 3 tentativas:
   - a transcrição vira linhas numeradas de ~22 s e é dividida em janelas de 15 min com 3 min de sobreposição;
   - em cada janela o modelo separa a conversa em **blocos de assunto** (linha de início, título, resumo, nota de 1 a 10). Ele aponta **números de linha**, não tempos: o código converte;
   - o código costura os blocos das janelas em uma lista única;
   - um **ranking final** recebe só essa lista (não a transcrição) e escolhe os assuntos mais fortes;
   - **código, não IA:** cada assunto escolhido vira um corte que começa no início do bloco e cresce com os blocos vizinhos até passar da duração mínima (evitando blocos fracos, como despedidas e recados); duração de 5 a 30 min; nenhuma sobreposição; quantidade de cortes pela duração do vídeo (menos de 1 h: 5 a 6; 1 a 2 h: 6 a 8; mais de 2 h: 8 a 10; depois do mínimo só entram assuntos com nota 7 ou mais);
   - **frases exatas:** muitas legendas automáticas do YouTube vêm sem pontuação. Quando é o caso, o Whisper local transcreve só os ~50 s em volta de cada começo e fim de corte (rápido), o que revela onde as frases terminam. Se a legenda já vem pontuada, esse passo é pulado;
   - uma chamada curta por corte escreve o gancho, as tags e 3 textos de thumbnail e, na mesma resposta, indica **em qual frase** (de uma lista numerada) o corte deve começar e terminar;
   - **código, não IA:** o ponto exato do corte é encaixado na pausa daquela frase (silêncios reais do áudio, via `ffmpeg silencedetect`), sem abrir com muleta (“então”, “tipo”, “aí”). Se o modelo indicar uma frase inválida ou um fim em pergunta, vale a regra do código: fim de frase mais próximo, de preferência com silêncio.

   *Por que assim:* no primeiro teste real, o modelo de 9B nomeava bem os assuntos, mas marcava trechos de 1 a 2 minutos mesmo quando instruído a marcar 5 ou mais, e vários cortes começavam no meio de uma frase. Pedir ao modelo só o que ele faz bem (assunto, nota e “qual frase abre o assunto”) e deixar duração e ponto de corte para o código resolveu os dois problemas.
4. **Corte** — `ffmpeg` recodificando (corte exato no quadro), H.264 + AAC, 16:9 na resolução do original (faixas pretas se o original não for 16:9), fade de 0,3 s no áudio. Usa o codificador da placa de vídeo quando funciona (NVIDIA, Intel Quick Sync, AMD, Apple) e `libx264` caso contrário.
5. **Thumbnail** — 16 quadros do corte recebem nota por nitidez, tamanho do rosto e expressão (detector de rostos YuNet do OpenCV); o texto (fonte Anton, branco/amarelo com contorno preto) é posicionado na maior área que **não toca em nenhum rosto**; leve reforço de contraste e cor; JPG 1280x720 abaixo de 2 MB.
6. **Títulos e descrição para o YouTube** — depois que o começo e o fim de cada corte estão definidos, uma segunda chamada curta por corte escreve 3 títulos de estilos diferentes (direto, curiosidade e frase dita no corte), a chamada das primeiras linhas e o resumo. O código confere e conserta tudo o que dá para conferir: tamanho (alvo de 60 caracteres, máximo 70), nome do convidado ou assunto no começo, palavras do título presentes na fala do corte, sem emoji nem hashtag, MAIÚSCULAS só no nome do convidado, título que não repete a thumbnail, créditos, 3 hashtags e de 5 a 8 tags. As regras e as fontes de cada uma estão em [`docs/seo_youtube.md`](docs/seo_youtube.md).

   *Por que uma chamada separada:* a chamada que escolhe a frase inicial e final não foi alterada, então os títulos novos não mexem nos pontos de corte. E, rodando depois, ela descreve o corte como ele realmente ficou.
7. **Saída** — `info.txt` com o título principal, 2 títulos alternativos (para o “Testar e comparar” do YouTube Studio), a descrição (gancho, resumo, créditos com o marcador `[@CANAL_ORIGINAL]` e 3 hashtags) e as tags.

**Modelo escolhido: `qwen3.5:9b`.** É a geração atual da família Qwen na faixa de 7 a 9 bilhões de parâmetros pedida para máquinas de 8 a 16 GB, escreve bem em português do Brasil (os títulos do teste saíram naturais), aceita desligar o modo “pensante” (responde direto em JSON, o que economiza muito tempo no processador) e tem irmãos menor (`4b`) e maior (`27b`) que usam exatamente os mesmos prompts.

**ffmpeg:** se já houver um ffmpeg instalado no sistema, ele é usado; se não, o programa usa o que vem junto com a biblioteca `imageio-ffmpeg`. Por isso a instalação não depende de instalar o ffmpeg à parte.

**Privacidade:** a telemetria do Gradio e do Hugging Face fica desligada. A internet é usada apenas para baixar o vídeo/legendas do YouTube e, na instalação, as bibliotecas e os modelos.

### Arquivos do projeto

```
instalar.bat / instalar.sh   instalador
abrir.bat / abrir.sh         abre o programa
requirements.txt             bibliotecas Python
cortes_ai/
  config.py       caminhos, detecção de hardware, escolha de modelos
  erros.py        mensagens de erro em português
  baixar.py       etapa 1      transcrever.py  etapa 2
  llm.py          cliente do Ollama (JSON validado + novas tentativas)
  selecionar.py   etapa 3      silencio.py     silêncios e ajuste das bordas
  renderizar.py   etapa 4      thumbnail.py    etapa 5
  publicacao.py   títulos e descrição para o YouTube (regras de docs/seo_youtube.md)
  saida.py        info.txt e pastas   pipeline.py   junta tudo e retoma de onde parou
  preparar.py     usado pelo instalador
  app.py          tela (Gradio)
docs/seo_youtube.md          pesquisa sobre títulos e descrições, com fontes, e as regras aplicadas
tests/test_regras.py         testes das regras determinísticas
assets/                      fonte Anton (licença OFL) e modelo de rostos YuNet (licença MIT)
```

Para rodar sem a tela: `.venv\Scripts\python -m cortes_ai.pipeline <link>`.

### Limitações conhecidas

- Os scripts de Mac/Linux (`instalar.sh`, `abrir.sh`) tiveram só a sintaxe conferida; o teste completo foi feito no Windows 11.
- No Windows, o instalador foi testado em uma máquina que já tinha Python, ffmpeg e Ollama. A instalação automática do Python e do Ollama (via `winget`) está escrita, mas não foi exercitada.
- O programa só foi testado em um notebook de 16 GB de RAM; a faixa de 8 GB (modelo `qwen3.5:4b`) não foi testada.
- O programa só foi testado sem placa de vídeo dedicada. Os caminhos para NVIDIA/Apple Silicon (modelos maiores, Whisper `large-v3`) seguem a documentação das bibliotecas, sem teste real.
- Um corte pode abrir com “Então…” quando a palavra está colada na seguinte: o programa prefere isso a picotar a fala.
- A IA escolhe o começo e o fim entre as frases a até ~30 s do ponto em que o assunto muda. Se o assunto de fato começar antes ou depois disso, o corte pode abrir com alguns segundos do assunto anterior.
- As legendas automáticas do YouTube têm erros; títulos e descrições podem herdar algum nome mal transcrito. Vale uma lida antes de postar.
- O código garante que os títulos seguem as regras (tamanho, nome no começo, sem palavrão etc.), mas não sabe dizer se um título é *bom* nem se o resumo tem algum erro de sentido. A opção 3 (frase entre aspas) vem com aviso porque o convidado pode estar citando outra pessoa.
- A “expressão” do rosto na thumbnail é uma estimativa simples (largura da boca em relação aos olhos).
