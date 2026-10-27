from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Oferta:
    """Um anúncio encontrado numa loja."""
    loja: str
    titulo: str
    url: str
    preco: float
    preco_lista: float | None = None      # preço "de" informado pela loja
    vendedor: str | None = None
    condicao: str = "novo"                # novo | usado
    disponivel: bool | None = None        # None = ainda não validado
    motivo_estoque: str = ""              # por que foi aceito/rejeitado na validação
    extra: dict = field(default_factory=dict)


@dataclass
class Decisao:
    alertar: bool
    regra: str | None = None              # "A", "B", "RADAR"
    entrada_id: str | None = None
    nome: str | None = None
    formato: str | None = None
    volume: int | None = None
    preco_capa: float | None = None
    desconto_pct: float | None = None
    prioridade: str = "normal"
    motivo: str = ""
    pacote: int | None = None             # nº de volumes, quando é um pacote/kit
    preco_volume: float | None = None     # preço típico de 1 volume usado na estimativa
    so_menor_preco: bool = False          # só alerta se for o menor preço já visto
    lojas_com_estoque: int | None = None  # em quantas lojas o item estava disponível nesta varredura
