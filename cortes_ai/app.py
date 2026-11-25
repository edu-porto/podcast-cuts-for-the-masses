"""Tela do Cortes AI (Gradio): um campo para o link, um botão e a grade de resultados."""
from __future__ import annotations

import html
import threading
import time
from pathlib import Path
from urllib.parse import quote

import gradio as gr

from . import config, pipeline, saida
from .config import log
from .erros import ErroAmigavel
from .selecionar import mmss

CSS = """
.gradio-container {max-width: 1280px !important; margin: 0 auto !important;}
#ca-topo h1 {font-size: 2.1rem; margin: 0 0 .15rem 0; letter-spacing: -.5px;}
#ca-topo p {margin: 0; opacity: .75; font-size: 1.02rem;}
#ca-gerar {min-height: 58px; font-size: 1.15rem; font-weight: 700;}
.ca-status {border-radius: 14px; padding: 16px 18px; background: var(--block-background-fill);
  border: 1px solid var(--border-color-primary);}
.ca-etapa {font-size: 1.2rem; font-weight: 700;}
.ca-detalhe {opacity: .75; margin: 4px 0 10px 0; min-height: 1.3em;}
.ca-barra {height: 14px; border-radius: 99px; background: var(--border-color-primary); overflow: hidden;}
.ca-barra > div {height: 100%; border-radius: 99px; background: linear-gradient(90deg,#f59e0b,#facc15);
  transition: width .6s ease;}
.ca-erro {border-radius: 14px; padding: 16px 18px; background: #fef2f2; border: 1px solid #fca5a5; color: #7f1d1d;}
.ca-erro *, .ca-erro {color: #7f1d1d !important;}
.ca-ok *, .ca-ok {color: #14532d !important;}
.ca-erro b {font-size: 1.1rem;}
.ca-ok {border-radius: 14px; padding: 16px 18px; background: #f0fdf4; border: 1px solid #86efac; color: #14532d;
  font-weight: 600;}
.ca-card {border-radius: 14px; overflow: hidden; border: 1px solid var(--border-color-primary);
  background: var(--block-background-fill);}
.ca-card img {width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block;}
.ca-card video {width: 100%; aspect-ratio: 16/9; background: #000; display: block;}
.ca-corpo {padding: 12px 14px 14px 14px;}
.ca-titulo {font-weight: 700; font-size: 1.05rem; line-height: 1.3; margin-bottom: 4px; min-height: 2.6em;}
.ca-meta {opacity: .7; font-size: .92rem; margin-bottom: 10px;}
.ca-baixar {display: inline-block; margin-top: 10px; padding: 9px 14px; border-radius: 10px; font-weight: 700;
  background: #f59e0b; color: #1c1917 !important; text-decoration: none !important;}
.ca-baixar.sec {background: transparent; border: 1px solid var(--border-color-primary); color: inherit !important;
  margin-left: 6px;}
.ca-acoes {gap: 6px !important; flex-wrap: nowrap !important;}
.ca-acoes button {font-size: .86rem !important; padding: 7px 4px !important; white-space: nowrap !important;}
"""


# --------------------------------------------------------------------------- #
# Tarefa em andamento (uma por vez; a tela pode se "reconectar" a ela)
# --------------------------------------------------------------------------- #
class Tarefa:
    def __init__(self, url: str, opcoes: dict):
        self.url, self.opcoes = url, opcoes
        self.ultimo = (0.0, "Preparando...", "")
        self.inicio = time.time()
        self.pasta: Path | None = None
        self.erro: str | None = None
        self.thread = threading.Thread(target=self._rodar, daemon=True)
        self.thread.start()

    def _rodar(self):
        try:
            self.pasta = pipeline.processar(self.url, self.opcoes, self._progresso)
        except ErroAmigavel as e:
            self.erro = e.mensagem
        except Exception as e:  # erro não previsto: mensagem genérica + log
            log.exception("Erro inesperado na tarefa")
            self.erro = ("Aconteceu um erro inesperado. Clique em “Gerar cortes” de novo: o trabalho continua "
                         f"de onde parou. Se o erro se repetir, veja o arquivo logs/cortes_ai.log. ({type(e).__name__})")

    def _progresso(self, fracao: float, etapa: str, detalhe: str):
        self.ultimo = (fracao, etapa, detalhe)

    @property
    def viva(self) -> bool:
        return self.thread.is_alive()


_tarefa: Tarefa | None = None
_trava = threading.Lock()


def _barra(fracao: float, etapa: str, detalhe: str, inicio: float) -> str:
    gasto = time.time() - inicio
    tempo = f"{int(gasto // 60)} min {int(gasto % 60):02d} s"
    linha = " · ".join(x for x in (html.escape(detalhe), f"tempo: {tempo}") if x)
    return (f'<div class="ca-status"><div class="ca-etapa">{html.escape(etapa)}</div>'
            f'<div class="ca-detalhe">{linha}</div>'
            f'<div class="ca-barra"><div style="width:{max(2, round(fracao * 100))}%"></div></div></div>')


def _erro_html(msg: str) -> str:
    return f'<div class="ca-erro"><b>Não deu certo 😕</b><br>{html.escape(msg)}</div>'


def _acompanhar(t: Tarefa):
    """Gera atualizações da barra até a tarefa terminar. Saídas: status, pasta, versão, botão, lista."""
    while t.viva:
        yield _barra(*t.ultimo, t.inicio), gr.skip(), gr.skip(), gr.update(interactive=False), gr.skip()
        time.sleep(0.7)
    if t.erro:
        yield _erro_html(t.erro), gr.skip(), gr.skip(), gr.update(interactive=True), gr.skip()
        return
    total = time.time() - t.inicio
    ok = (f'<div class="ca-ok">✅ Pronto! {html.escape(t.ultimo[2])} '
          f'Os arquivos estão em: {html.escape(str(t.pasta))}</div>')
    if total < 5:
        ok = '<div class="ca-ok">✅ Este vídeo já tinha sido processado. Aqui estão os cortes.</div>'
    yield ok, str(t.pasta), time.time(), gr.update(interactive=True), gr.update(choices=_escolhas(), value=str(t.pasta))


def gerar(url: str, n_cortes: float, dur_min: float, dur_max: float):
    global _tarefa
    with _trava:
        if _tarefa is None or not _tarefa.viva:
            if dur_max < dur_min + 1:
                yield (_erro_html("A duração máxima precisa ser maior que a mínima (veja em “Avançado”)."),
                       gr.skip(), gr.skip(), gr.skip(), gr.skip())
                return
            opcoes = {"n_cortes": int(n_cortes or 0), "dur_min": float(dur_min) * 60, "dur_max": float(dur_max) * 60}
            _tarefa = Tarefa(url, opcoes)
        t = _tarefa
    yield from _acompanhar(t)


def ao_abrir():
    """Ao abrir a página: reconecta a uma tarefa em andamento ou mostra o último vídeo processado."""
    t = _tarefa
    if t is not None and t.viva:
        yield from _acompanhar(t)
        return
    videos = saida.listar_videos()
    pasta = videos[0][1] if videos else ""
    yield "", pasta, time.time(), gr.update(interactive=True), gr.update(choices=_escolhas(), value=pasta or None)


def _escolhas() -> list[tuple[str, str]]:
    return saida.listar_videos()


# --------------------------------------------------------------------------- #
# Grade de resultados
# --------------------------------------------------------------------------- #
def _url_arquivo(caminho: Path) -> str:
    v = int(caminho.stat().st_mtime) if caminho.exists() else 0
    return "/gradio_api/file=" + quote(str(caminho).replace("\\", "/"), safe="/:") + f"?v={v}"


def _card_html(pasta_video: Path, c: dict) -> str:
    pasta = pasta_video / c["pasta"]
    thumb, video = pasta / "thumbnail.jpg", pasta / "corte.mp4"
    titulo = html.escape(c["titulo"])
    nome = saida.slug(c["titulo"])
    partes = ['<div class="ca-card">']
    if thumb.exists():
        partes.append(f'<img src="{_url_arquivo(thumb)}" alt="thumbnail">')
    partes.append('<div class="ca-corpo">')
    partes.append(f'<div class="ca-titulo">{c["n"]:02d}. {titulo}</div>')
    partes.append(f'<div class="ca-meta">⏱ {mmss(c["duracao"])} · nota {c["nota"]}/10 · '
                  f'trecho {mmss(c["ini"])}–{mmss(c["fim"])} do original</div>')
    if video.exists():
        # "#t=0.1" faz o navegador mostrar o primeiro quadro em vez de um retângulo preto
        partes.append(f'<video controls preload="metadata" src="{_url_arquivo(video)}#t=0.1"></video>')
        partes.append(f'<a class="ca-baixar" href="{_url_arquivo(video)}" download="{nome}.mp4">⬇ Baixar vídeo</a>')
        if thumb.exists():
            partes.append(f'<a class="ca-baixar sec" href="{_url_arquivo(thumb)}" download="{nome}.jpg">⬇ Thumbnail</a>')
    else:
        partes.append("<i>O arquivo deste corte não foi encontrado na pasta.</i>")
    partes.append("</div></div>")
    return "".join(partes)


def construir() -> gr.Blocks:
    with gr.Blocks(title="Cortes AI") as tela:
        gr.HTML('<div id="ca-topo"><h1>✂️ Cortes AI</h1>'
                "<p>Cole o link de um podcast do YouTube e receba os melhores cortes prontos para postar. "
                "Tudo roda no seu computador, de graça.</p></div>")
        with gr.Row(equal_height=True):
            link = gr.Textbox(label="Link do vídeo no YouTube", placeholder="https://www.youtube.com/watch?v=...",
                              scale=5, max_lines=1, autofocus=True)
            botao = gr.Button("Gerar cortes", variant="primary", scale=1, elem_id="ca-gerar")
        with gr.Accordion("Avançado", open=False):
            with gr.Row():
                n_cortes = gr.Slider(0, 10, value=0, step=1, label="Quantidade de cortes",
                                     info="0 = automático (de 5 a 10, conforme a duração do vídeo)")
                dur_min = gr.Slider(5, 15, value=5, step=1, label="Duração mínima de cada corte (minutos)")
                dur_max = gr.Slider(10, 30, value=30, step=1, label="Duração máxima de cada corte (minutos)")
            anteriores = gr.Dropdown(choices=_escolhas(), value=None, label="Ver cortes de um vídeo já processado",
                                     interactive=True)
        status = gr.HTML()
        pasta_atual = gr.State("")
        versao = gr.State(0.0)

        @gr.render(inputs=[pasta_atual, versao])
        def grade(pasta: str, _versao: float):
            if not pasta:
                return
            pasta_video = Path(pasta)
            reg = saida.ler_registro(pasta_video)
            if not reg:
                return
            cortes = [c for c in reg["cortes"] if not c.get("excluido")]
            info = reg["info"]
            with gr.Row(equal_height=True):
                quantos = "1 corte" if len(cortes) == 1 else f"{len(cortes)} cortes"
                gr.Markdown(f"### {quantos} de “{info['titulo']}”\nCanal original: {info['canal']}")
                abrir_tudo = gr.Button("📂 Abrir pasta com todos os cortes", scale=0, min_width=300)
                abrir_tudo.click(lambda p=pasta_video: saida.abrir_pasta(p), None, None)
            for i in range(0, len(cortes), 3):
                with gr.Row():
                    for c in cortes[i:i + 3]:
                        with gr.Column(min_width=300):
                            cartao = gr.HTML(_card_html(pasta_video, c))
                            with gr.Row(elem_classes="ca-acoes"):
                                b_pasta = gr.Button("📂 Abrir pasta", size="sm", scale=3, min_width=90)
                                b_thumb = gr.Button("🖼 Gerar outra thumbnail", size="sm", scale=5, min_width=150)
                                b_excluir = gr.Button("🗑 Excluir", size="sm", variant="stop", scale=2, min_width=70)

                            def abrir(p=pasta_video / c["pasta"]):
                                saida.abrir_pasta(p)

                            def outra(n=c["n"], p=pasta_video):
                                try:
                                    pipeline.outra_thumbnail(p, n)
                                except ErroAmigavel as e:
                                    gr.Warning(e.mensagem)
                                except Exception as e:
                                    log.exception("Erro ao refazer thumbnail")
                                    gr.Warning(f"Não consegui refazer a thumbnail ({type(e).__name__}).")
                                return time.time()

                            def excluir(n=c["n"], p=pasta_video):
                                saida.excluir_corte(p, n)
                                gr.Info("Corte enviado para a Lixeira.")
                                return time.time()

                            b_pasta.click(abrir, None, None)
                            b_thumb.click(outra, None, versao, show_progress_on=cartao)
                            b_excluir.click(excluir, None, versao)
                    for _ in range(3 - len(cortes[i:i + 3])):
                        gr.Column(min_width=300)

        saidas = [status, pasta_atual, versao, botao, anteriores]
        botao.click(gerar, [link, n_cortes, dur_min, dur_max], saidas, concurrency_limit=4)
        link.submit(gerar, [link, n_cortes, dur_min, dur_max], saidas, concurrency_limit=4)
        anteriores.input(lambda p: (p or "", time.time()), anteriores, [pasta_atual, versao])
        tela.load(ao_abrir, None, saidas)
    return tela


def abrir(abrir_navegador: bool = True, porta: int | None = None) -> None:
    config.configurar_log()
    config.criar_pastas()
    tela = construir()
    tema = gr.themes.Soft(primary_hue="amber", neutral_hue="slate",
                          font=["Segoe UI", "system-ui", "Arial", "sans-serif"],
                          font_mono=["Consolas", "monospace"])
    tela.queue(default_concurrency_limit=4)
    tela.launch(inbrowser=abrir_navegador, server_name="127.0.0.1", server_port=porta, show_error=True,
                quiet=True, allowed_paths=[str(config.SAIDA)], footer_links=[], theme=tema, css=CSS)
