"""Entrada da API para a Vercel.

A Vercel invoca a classe ``handler`` a cada requisição. Não chame
``server.main()`` aqui: ele inicia um processo permanente, próprio apenas para
execução local.
"""
from server import PMSHandler


class handler(PMSHandler):
    """Expõe as rotas já implementadas em ``server.py`` como função Python."""

