"""Erros com mensagens em português simples, para mostrar ao usuário."""


class ErroAmigavel(Exception):
    """Erro cuja mensagem pode ser mostrada diretamente na tela.

    mensagem: texto em português claro, sem termos técnicos.
    detalhe:  informação técnica, que vai só para o arquivo de log.
    """

    def __init__(self, mensagem: str, detalhe: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.detalhe = detalhe


class Cancelado(Exception):
    """O usuário interrompeu o processamento."""
