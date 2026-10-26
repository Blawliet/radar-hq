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
    if d.regra == "A" and not d.so_menor_preco:
        return "Raro em Estoque"
    return "Novo Lacrado"


def _loja(o: Oferta) -> str:
    if o.vendedor and o.vendedor.strip().lower() not in o.loja.lower():
        return f"{o.loja} (vendedor: {o.vendedor.strip()})"
    return o.loja


def _capa(d: Decisao) -> str:
    if d.pacote and d.preco_capa and d.preco_volume:
        return f"{brl(d.preco_capa)} (estimado: {d.pacote} vols x {brl(d.preco_volume)})"
    if d.pacote and d.preco_capa:
        return f"{brl(d.preco_capa)} ({d.pacote} volumes)"
    return brl(d.preco_capa)


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
        _capa(d),
        brl(o.preco),
        _desconto(o, d),
        o.url,
    )]


def juntar_nomes(nomes: list[str]) -> str:
    nomes = [n for n in nomes if n]
    return nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]


def linha_historico(preco: float, anterior: tuple[float, str, str] | None) -> str | None:
    """anterior = (menor preço já visto, loja, data) ou None se é o primeiro registro."""
    if anterior is None:
        return None
    p_ant, loja, data = anterior
    if preco < p_ant - 0.005:
        return f"🏆 *MENOR PREÇO JÁ VISTO PELO RADAR!* (antes: {brl(p_ant)}, {loja}, {data})"
    if preco <= p_ant + 0.005:
        return f"🏆 *Igual ao menor preço já visto* ({loja}, {data})"
    return f"📊 *Menor já visto:* {brl(p_ant)} ({loja}, {data})"


def linha_outras(outras: list[tuple[str, float]] | None) -> str | None:
    if not outras:
        return None
    partes = [f"{loja} {brl(preco)}" for loja, preco in outras[:4]]
    extra = f" e mais {len(outras) - 4}" if len(outras) > 4 else ""
    return "🛍️ *Também em:* " + " · ".join(partes) + extra


def montar(o: Oferta, d: Decisao, interessados: list[str] | None = None,
           historico: str | None = None, outras: list[tuple[str, float]] | None = None) -> str:
    c = campos(o, d)
    topo = ["🚨 *NOVA OFERTA ENCONTRADA!*"]
    if interessados and any(interessados):
        topo.append(f"👥 *Para:* {juntar_nomes(interessados)}")
    return "\n".join(topo + [
        "",
        f"📖 *Título:* {c[0]}",
        f"📦 *Formato:* {c[1]}",
        f"🏪 *Loja:* {c[2]}",
        f"⚡ *Status:* {c[3]}",
        "",
        f"🏷️ *Preço de Capa:* {c[4]}",
        f"💰 *Você Paga:* *{c[5]}*",
        f"📉 *Desconto:* {c[6]}",
    ] + [l for l in (historico, linha_outras(outras)) if l] + [
        "",
        "🔗 *Link direto de compra:*",
        c[7],
    ])


def resumo_do_dia(dia_br: str, alertas: list[dict], checados: int, lojas: int) -> str:
    """Resumão das ofertas enviadas no dia. dia_br = 'dd/mm'."""
    topo = [f"📰 *Resumão do dia {dia_br}*", ""]
    if not alertas:
        return "\n".join(topo + [f"Dia tranquilo: nenhuma oferta nova hoje ({checados} anúncios conferidos "
                                   f"em {lojas} lojas)."])

    def item(a, i=None):
        num = f"{i}. " if i else "• "
        desc = f" *{a['desconto']:.0f}% OFF*" if a.get("desconto") and a["desconto"] > 0 else ""
        quem = f" · {a['pessoas']}" if a.get("pessoas") else ""
        return f"{num}{(a.get('titulo') or 'Oferta')[:70]}: *{brl(a['preco'])}*{desc} ({a.get('loja') or '?'}){quem}\n{a['url']}"

    linhas = topo + [f"{len(alertas)} oferta{'s' if len(alertas) > 1 else ''} enviada{'s' if len(alertas) > 1 else ''} hoje · "
                     f"{checados} anúncios conferidos em {lojas} lojas", ""]
    com_desc = sorted([a for a in alertas if (a.get("desconto") or 0) > 0], key=lambda a: -a["desconto"])[:5]
    if com_desc:
        linhas += ["🏆 *Maiores descontos*"] + [item(a, i) for i, a in enumerate(com_desc, 1)] + [""]
    recordes = [a for a in alertas if a.get("recorde") and a not in com_desc]
    if recordes:
        linhas += ["📉 *Menor preço já visto*"] + [item(a) for a in recordes[:5]] + [""]
    raros = [a for a in alertas if a.get("regra") == "A" and a not in com_desc and a not in recordes]
    if raros:
        linhas += ["⭐ *Raros e monitorados encontrados*"] + [item(a) for a in raros[:5]] + [""]
    contagem: dict[str, int] = {}
    for a in alertas:
        for p in (a.get("pessoas") or "").replace(" e ", ", ").split(", "):
            if p.strip():
                contagem[p.strip()] = contagem.get(p.strip(), 0) + 1
    if contagem:
        linhas.append("👥 *Por pessoa:* " + " · ".join(f"{p} {n}" for p, n in sorted(contagem.items())))
    return "\n".join(linhas).rstrip()
