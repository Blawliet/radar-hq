"""Canal PÚBLICO de ofertas Nesta Estante (Canal do WhatsApp), com a tag de afiliado.

A API oficial do WhatsApp não posta em Canais, e automatizar um número comum arrisca
banimento. Então o radar faz o resto: a cada rodada, manda para o Bruno, numa conversa
PRIVADA com o bot do Telegram, cada post pronto no formato do WhatsApp (negrito com *,
riscado com ~). Um toque no bloco copia; é só colar no Canal.

Separado do grupo dos amigos (BASTA LER): aqui não entram nomes nem listas pessoais.
Config em config/canal_publico.yaml. Secrets: NESTA_RASCUNHO_CHAT (o ID do Bruno no
Telegram, para onde vão os rascunhos) e AMAZON_TAG (a tag de Associado da Amazon).

O que é postado: ofertas da Amazon (das buscas das listas e das buscas próprias do
canal) com desconto de pelo menos `desconto_minimo`, edição brasileira, com estoque.
Cada produto é postado no máximo uma vez a cada `repostar_dias`, a não ser que o
preço caia de novo.
"""
from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from texto import contem, normalizar

log = logging.getLogger("canal")
RAIZ = Path(__file__).resolve().parents[1]
_lock = threading.Lock()


def config() -> dict:
    caminho = RAIZ / "config" / "canal_publico.yaml"
    try:
        cfg = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        cfg = {}
    cfg["chat"] = os.getenv("NESTA_RASCUNHO_CHAT") or cfg.get("chat") or ""
    cfg["tag"] = os.getenv("AMAZON_TAG") or cfg.get("tag") or ""
    cfg.setdefault("desconto_minimo", 30)
    cfg.setdefault("max_posts_rodada", 5)
    cfg.setdefault("repostar_dias", 7)
    cfg.setdefault("buscas_por_rodada", 3)
    return cfg


def ativo(cfg: dict | None = None) -> bool:
    cfg = cfg or config()
    return bool(cfg.get("ativo"))


def buscas_da_rodada(cfg: dict, janela: int | None = None) -> list[str]:
    """Reveza as buscas próprias do canal: algumas por rodada, para não pesar na Amazon."""
    import time
    todas = list(cfg.get("buscas") or [])
    if not todas:
        return []
    n = max(1, int(cfg.get("buscas_por_rodada", 3)))
    janela = int(time.time() // 7200) if janela is None else janela
    ini = (janela * n) % len(todas)
    return [todas[(ini + i) % len(todas)] for i in range(min(n, len(todas)))]


def desconto(o) -> float | None:
    if o.preco and o.preco_lista and o.preco_lista > o.preco:
        return round((1 - o.preco / o.preco_lista) * 100, 1)
    return None


def link_afiliado(o, tag: str) -> str:
    asin = o.extra.get("asin") or (re.search(r"/dp/([A-Z0-9]{10})", o.url) or [None, None])[1]
    if not asin:
        return o.url
    return f"https://www.amazon.com.br/dp/{asin}" + (f"?tag={tag}" if tag else "")


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_post(o, tag: str) -> str:
    """Texto do post no formato do WhatsApp."""
    d = desconto(o)
    linhas = [f"📚 *{o.titulo[:180].replace('*', '')}*", ""]
    if o.preco_lista and d:
        linhas.append(f"De ~{_brl(o.preco_lista)}~ por *{_brl(o.preco)}* ({d:.0f}% OFF)")
    else:
        linhas.append(f"Por *{_brl(o.preco)}*")
    if o.vendedor and "amazon" not in o.vendedor.lower():
        linhas.append(f"Vendido por {o.vendedor[:60]}")
    linhas += ["", f"🛒 {link_afiliado(o, tag)}", "", "_Preço e estoque podem mudar a qualquer momento._"]
    if tag:
        linhas.append("_Como Associado da Amazon, recebo por compras qualificadas._")
    return "\n".join(linhas)


def mensagem_rascunho(post: str, n: int, total: int) -> str:
    """Mensagem do bot: o post dentro de um bloco que copia com um toque."""
    return (f"📝 <b>Nesta Estante</b> · post {n} de {total}\n"
            f"Toque no bloco para copiar e cole no Canal do WhatsApp.\n\n"
            f"<pre>{html.escape(post)}</pre>")


# ------------------------------------------------------------------ memória dos posts
def _con(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS canal_posts (chave TEXT PRIMARY KEY, preco REAL, quando TEXT, titulo TEXT)")
    return con


def ja_postado(con, chave: str, preco: float, dias: int) -> bool:
    row = con.execute("SELECT preco, quando FROM canal_posts WHERE chave=?", (chave,)).fetchone()
    if not row:
        return False
    if preco < row[0] * 0.97:
        return False                    # caiu de novo: vale postar
    return datetime.fromisoformat(row[1]) > datetime.now() - timedelta(days=dias)


def selecionar(validadas: list, cfg: dict, excluir_titulos: list[str]) -> list:
    minimo = float(cfg.get("desconto_minimo", 30))
    vistos, saida = set(), []
    for o in validadas:
        if not o.loja.startswith("Amazon") or "via @" in o.loja or not o.disponivel:
            continue
        if o.condicao == "usado":
            continue
        d = desconto(o)
        if d is None or d < minimo or d > 85:          # > 85% costuma ser preço "de" inflado
            continue
        n = normalizar(o.titulo)
        if any(contem(n, x) for x in excluir_titulos):
            continue
        chave = o.extra.get("asin") or o.url
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(o)
    saida.sort(key=lambda o: -(desconto(o) or 0))
    return saida


def publicar(validadas: list, excluir_titulos: list[str], enviar: bool = True,
             db: Path | None = None) -> int:
    cfg = config()
    if not ativo(cfg):
        return 0
    cands = selecionar(validadas, cfg, excluir_titulos)
    if not cands:
        log.info("Canal público: nenhuma oferta nova da Amazon com %s%%+.", cfg["desconto_minimo"])
        return 0
    con = _con(db or RAIZ / "data" / "radar.db")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    postados = 0
    for o in cands:
        if postados >= int(cfg["max_posts_rodada"]):
            break
        chave = o.extra.get("asin") or o.url
        with _lock:
            if ja_postado(con, chave, o.preco, int(cfg["repostar_dias"])):
                continue
        texto = mensagem_rascunho(montar_post(o, cfg["tag"]), postados + 1, min(len(cands), int(cfg["max_posts_rodada"])))
        if not enviar or not cfg["chat"] or not token:
            if not cfg["chat"] and enviar:
                log.warning("Canal público: falta o Secret NESTA_RASCUNHO_CHAT; só mostrando no log.")
            texto_post = montar_post(o, cfg["tag"])
            print("\n[Nesta Estante]\n" + texto_post + "\n" + "-" * 40)
            if not os.getenv("PYTEST_CURRENT_TEST"):
                # modo seco: guarda os rascunhos para conferir depois
                arq = RAIZ / "data" / "debug" / "nesta-estante-rascunhos.txt"
                arq.parent.mkdir(parents=True, exist_ok=True)
                with arq.open("a" if postados else "w", encoding="utf-8") as fh:
                    fh.write(texto_post + "\n" + "-" * 40 + "\n")
            postados += 1
            continue
        import httpx
        try:
            r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
                "chat_id": cfg["chat"], "text": texto, "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True}})
            ok = r.status_code == 200 and r.json().get("ok")
        except Exception as e:
            log.warning("Canal público: falha ao postar (%s)", e)
            ok = False
        if not ok:
            log.warning("Canal público: Telegram recusou o post (%s)", getattr(r, "text", "")[:200])
            break
        with _lock:
            con.execute("INSERT OR REPLACE INTO canal_posts VALUES (?,?,?,?)",
                        (chave, o.preco, datetime.now().isoformat(timespec="seconds"), o.titulo[:200]))
            con.commit()
        postados += 1
        import time
        time.sleep(3)
    log.info("Canal público: %d rascunho(s) para o WhatsApp.", postados)
    return postados
