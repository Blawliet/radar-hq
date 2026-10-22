"""Radar HQ: orquestra coleta, validação, regras e envio.

Uso:
  python src/main.py --agora          roda uma varredura e sai
  python src/main.py --seco           varredura sem enviar (imprime as mensagens)
  python src/main.py --agendar        fica rodando nas 6 janelas diárias (padrão no Docker)
  python src/main.py --teste          envia uma mensagem de exemplo para o WhatsApp
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from db import Banco  # noqa: E402
from dotenv_mini import carregar_env  # noqa: E402
from texto import normalizar  # noqa: E402
from mensagem import campos, linha_historico, montar  # noqa: E402
from models import Decisao, Oferta  # noqa: E402
from rules import Catalogo, Grupo, avaliar, melhor_decisao, resumo_perfil  # noqa: E402

carregar_env()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%d/%m %H:%M:%S",
)
log = logging.getLogger("radar")

JANELAS = [(5, 30), (9, 0), (12, 0), (15, 0), (19, 0), (23, 30)]
_rodando = threading.Lock()


def chave_capa(d: Decisao) -> str:
    return f"{d.entrada_id}:{d.volume if d.volume is not None else 'unico'}"


def descartavel(d: Decisao) -> bool:
    """Pode pular sem abrir a página? Só quando temos certeza pelo título."""
    if not d.entrada_id:
        return True                                    # bloqueado ou fora do catálogo
    return d.motivo.startswith("volume") and d.volume is not None   # volume conhecido e fora da lista


RUIDO_TITULO = {"panini", "jbc", "newpop", "pipoca", "nanquim", "mythos", "devir", "editora", "manga",
                "hq", "novo", "lacrado", "original", "capa", "dura", "brochura", "frete", "gratis",
                "edicao", "portugues", "br", "brasil", "lancamento", "pronta", "entrega", "de", "do", "da"}


def chave_produto(o: Oferta, grupo: Grupo) -> str:
    """Identidade do produto entre lojas: série do catálogo + volume; para itens
    de radar (omnibus, boxes), o título sem palavras de enfeite."""
    for p in grupo.perfis:
        d = avaliar(o, p)
        if d.entrada_id and not d.entrada_id.startswith("radar-"):
            nome = normalizar(d.nome or d.entrada_id)
            return f"{nome}:{d.volume if d.volume is not None else 'unico'}"
    palavras = [w for w in normalizar(o.titulo).split() if w not in RUIDO_TITULO]
    return "titulo:" + " ".join(palavras)[:120]


def varrer_loja(loja, grupo: Grupo, banco: Banco) -> list[tuple[Oferta, Decisao, list[str]]]:
    try:
        return _varrer_loja(loja, grupo, banco)
    finally:
        loja.f.fechar()        # fecha o navegador na mesma thread que o abriu


def avaliar_perfil(o: Oferta, cat: Catalogo, loja, banco: Banco) -> Decisao | None:
    d0 = avaliar(o, cat)                               # com título e preço da página
    if not d0.entrada_id or d0.volume is None and d0.motivo.startswith("volume"):
        return None
    chave = chave_capa(d0)
    if loja.oficial:
        banco.salvar_capa(chave, o.preco_lista or o.preco, loja.nome)
    d = avaliar(o, cat, banco.capa(chave))
    quem = f"[{cat.nome}] " if cat.nome else ""
    if d.alertar:
        log.info("ALERTA %s%s [%s] %s R$ %.2f", quem, d.regra, loja.nome, o.titulo[:60], o.preco)
        return d
    log.info("sem alerta %s[%s] %s: %s", quem, loja.nome, o.titulo[:60], d.motivo)
    return None


def _varrer_loja(loja, grupo: Grupo, banco: Banco) -> list[tuple[Oferta, Decisao, list[str]]]:
    achados, vistos = [], set()
    for _, termo in grupo.todas_buscas():
        try:
            candidatos = loja.buscar(termo)
        except Exception as e:
            log.warning("%s: erro na busca '%s': %s", loja.nome, termo, e)
            continue
        for o in candidatos:
            if o.url in vistos:
                continue
            vistos.add(o.url)

            # filtro barato pelo título da busca: só abre a página se interessar a alguém
            if all(descartavel(avaliar(o, p)) for p in grupo.perfis):
                continue

            try:
                o = loja.validar(o)
            except Exception as e:
                log.warning("%s: erro validando %s: %s", loja.nome, o.url, e)
                continue
            banco.historico(o.loja, o.titulo, o.url, o.preco, o.disponivel, o.motivo_estoque)
            if not o.disponivel:
                log.info("descartado [%s] %s: %s", loja.nome, o.titulo[:60], o.motivo_estoque)
                continue

            o.extra["chave"] = chave_produto(o, grupo)
            banco.registrar_preco(o.extra["chave"], o.url, o.preco, o.loja)

            por_pessoa = [(p.nome, avaliar_perfil(o, p, loja, banco)) for p in grupo.perfis]
            por_pessoa = [(n, d) for n, d in por_pessoa if d]
            if por_pessoa:
                achados.append((o, melhor_decisao([d for _, d in por_pessoa]), [n for n, _ in por_pessoa]))
    return achados


def varredura(enviar: bool = True) -> int:
    if not _rodando.acquire(blocking=False):
        log.warning("Varredura anterior ainda rodando, pulando esta janela.")
        return 0
    try:
        from stores import montar_lojas
        from whatsapp import cliente_do_ambiente

        inicio = time.time()
        grupo = Grupo(RAIZ / "config")
        banco = Banco(RAIZ / "data" / "radar.db")
        lojas = montar_lojas(RAIZ / "config" / "lojas.yaml")
        oficiais = [l for l in lojas if l.oficial]
        demais = [l for l in lojas if not l.oficial]
        log.info("Varredura iniciada: %d lojas, %d buscas, perfis: %s.", len(lojas),
                 len(grupo.todas_buscas()), ", ".join(p.nome or "único" for p in grupo.perfis))

        achados: list[tuple[Oferta, Decisao, list[str]]] = []
        # lojas oficiais primeiro: elas alimentam a tabela de preço de capa
        for leva in (oficiais, demais):
            with ThreadPoolExecutor(max_workers=max(1, len(leva))) as ex:
                for res in ex.map(lambda l: varrer_loja(l, grupo, banco), leva):
                    achados.extend(res)

        # o mesmo produto pode aparecer em buscas diferentes: junta os interessados
        unicos: dict[str, tuple[Oferta, Decisao, list[str]]] = {}
        for o, d, nomes in achados:
            if o.url in unicos:
                _, d_ant, n_ant = unicos[o.url]
                unicos[o.url] = (o, melhor_decisao([d, d_ant]), sorted(set(n_ant) | set(nomes), key=str))
            else:
                unicos[o.url] = (o, d, nomes)
        ordem = {"maxima": 0, "alta": 1, "normal": 2}
        achados = sorted(unicos.values(), key=lambda x: (x[1].regra != "A", ordem.get(x[1].prioridade, 2),
                                                         -(x[1].desconto_pct or 0)))

        realertar = int(os.getenv("REALERTAR_DIAS", "7"))
        destino = os.getenv("WHATSAPP_DESTINO", "")
        cli = cliente_do_ambiente() if enviar else None
        enviados = 0
        for o, d, nomes in achados:
            if not banco.deve_alertar(o.url, o.preco, realertar):
                continue
            anterior = banco.menor_preco_anterior(o.extra.get("chave", o.url), o.url)
            texto = montar(o, d, nomes, linha_historico(o.preco, anterior))
            if not enviar:
                print("\n" + texto + "\n" + "-" * 40)
                continue
            if cli.enviar_alerta(destino, campos(o, d), texto):
                banco.registrar_alerta(o.url, o.preco, d.regra)
                enviados += 1
                time.sleep(2.5)
        log.info("Fim: %d achados, %d enviados, %.0fs.", len(achados), enviados, time.time() - inicio)
        return enviados
    finally:
        _rodando.release()


def agendar():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    tz = "America/Sao_Paulo"
    sched = BlockingScheduler(timezone=tz)
    for h, m in JANELAS:
        sched.add_job(varredura, CronTrigger(hour=h, minute=m, timezone=tz),
                      id=f"janela_{h:02d}{m:02d}", max_instances=1, coalesce=True,
                      misfire_grace_time=1800)
    log.info("Agendado: %s (horário de Brasília).", ", ".join(f"{h:02d}h{m:02d}" for h, m in JANELAS))

    if os.getenv("WEBHOOK_ENABLED", "false").lower() == "true":
        from webhook_server import iniciar_webhook
        iniciar_webhook()

    sched.start()


def listar(enviar: bool = True):
    """Manda para o Telegram o que cada pessoa está buscando."""
    from whatsapp import cliente_do_ambiente
    import yaml
    geral = yaml.safe_load((RAIZ / "config" / "catalogo.yaml").read_text(encoding="utf-8")) or {}
    nomes_radares = {"radar-omnibus": "Omnibus", "radar-boxes": "Boxes e coleções"}
    nomes_radares.update({r["id"]: r.get("nome", r["id"]) for r in geral.get("radares", [])
                          if r["id"] not in nomes_radares})
    cli = cliente_do_ambiente() if enviar else None
    for p in Grupo(RAIZ / "config").perfis:
        texto = resumo_perfil(p, nomes_radares)
        if enviar:
            cli.enviar_texto(os.getenv("WHATSAPP_DESTINO", ""), texto)
            time.sleep(1.5)
        else:
            print(texto + "\n")


def teste():
    from whatsapp import cliente_do_ambiente
    o = Oferta(loja="Loja Panini", titulo="A Saga do Flash Vol. 14", url="https://exemplo.com/produto",
               preco=89.90, preco_lista=None, condicao="novo")
    d = Decisao(True, "A", "saga-do-flash", "A Saga do Flash", "Encadernado", 14, 79.90, -12.5, "maxima")
    texto = "🧪 *MENSAGEM DE TESTE*\n\n" + montar(o, d, [p.nome for p in Grupo(RAIZ / "config").perfis][:2])
    ok = cliente_do_ambiente().enviar_alerta(os.getenv("WHATSAPP_DESTINO", ""), campos(o, d), texto)
    print("Enviado!" if ok else "Falhou, confira a configuração e os logs acima.")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--agora", action="store_true")
    g.add_argument("--seco", action="store_true")
    g.add_argument("--agendar", action="store_true")
    g.add_argument("--teste", action="store_true")
    g.add_argument("--lista", action="store_true")
    a = ap.parse_args()
    if a.teste:
        teste()
    elif a.lista:
        listar(True)
    elif a.agora:
        varredura(True)
    elif a.seco:
        varredura(False)
    else:
        agendar()
