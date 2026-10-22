"""Monta a mensagem no formato obrigatório do WhatsApp."""
from __future__ import annotations

from models import Decisao, Oferta


def brl(v: float | None) -> str:
    if v is None:
        return "não informado"
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _status(o: Oferta, d: Decisao) -> str:
    if o.condicao == "usado":
        return "Seminovo"
    if d.regra == "A":
        return "Raro em Estoque"
    return "Novo Lacrado"


def _loja(o: Oferta) -> str:
    if o.vendedor and o.vendedor.strip().lower() not in o.loja.lower():
        return f"{o.loja} (vendedor: {o.vendedor.strip()})"
    return o.loja


def _desconto(o: Oferta, d: Decisao) -> str:
    if d.preco_capa is None or d.desconto_pct is None:
        return "sem referência de capa"
    if d.desconto_pct <= 0:
        acima = o.preco - d.preco_capa
        if acima <= 0.005:
            return "sem desconto (preço de capa)"
        return f"sem desconto ({brl(acima)} acima da capa)"
    economia = d.preco_capa - o.preco
    return f"*{d.desconto_pct:.0f}% OFF* (Economia de {brl(economia)})"


def _uma_linha(s: str) -> str:
    # parâmetros de template da Meta não aceitam quebra de linha nem 4+ espaços seguidos
    return " ".join(str(s).split())[:900]


def campos(o: Oferta, d: Decisao) -> list[str]:
    """Os 8 valores variáveis da mensagem, na ordem do template {{1}}..{{8}}."""
    return [_uma_linha(x) for x in (
        o.titulo.strip(),
        d.formato or "Encadernado",
        _loja(o),
        _status(o, d),
        brl(d.preco_capa),
        brl(o.preco),
        _desconto(o, d),
        o.url,
    )]


def montar(o: Oferta, d: Decisao) -> str:
    c = campos(o, d)
    return "\n".join([
        "🚨 *NOVA OFERTA ENCONTRADA!*",
        "",
        f"📖 *Título:* {c[0]}",
        f"📦 *Formato:* {c[1]}",
        f"🏪 *Loja:* {c[2]}",
        f"⚡ *Status:* {c[3]}",
        "",
        f"🏷️ *Preço de Capa:* {c[4]}",
        f"💰 *Você Paga:* *{c[5]}*",
        f"📉 *Desconto:* {c[6]}",
        "",
        "🔗 *Link direto de compra:*",
        c[7],
    ])
