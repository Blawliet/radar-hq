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
from mensagem import campos, montar  # noqa: E402
from models import Decisao, Oferta  # noqa: E402
from rules import Catalogo, avaliar  # noqa: E402

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


def varrer_loja(loja, cat: Catalogo, banco: Banco) -> list[tuple[Oferta, Decisao]]:
    try:
        return _varrer_loja(loja, cat, banco)
    finally:
        loja.f.fechar()        # fecha o navegador na mesma thread que o abriu


def _varrer_loja(loja, cat: Catalogo, banco: Banco) -> list[tuple[Oferta, Decisao]]:
    achados, vistos = [], set()
    for _, termo in cat.todas_buscas():
        try:
            candidatos = loja.buscar(termo)
        except Exception as e:
            log.warning("%s: erro na busca '%s': %s", loja.nome, termo, e)
            continue
        for o in candidatos:
            if o.url in vistos:
                continue
            vistos.add(o.url)

            pre = avaliar(o, cat)                      # filtro barato pelo título da busca
            if descartavel(pre):
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

            d0 = avaliar(o, cat)                       # agora com título e preço da página
            if not d0.entrada_id or d0.volume is None and d0.motivo.startswith("volume"):
                continue
            chave = chave_capa(d0)
            if loja.oficial:
                banco.salvar_capa(chave, o.preco_lista or o.preco, loja.nome)
            d = avaliar(o, cat, banco.capa(chave))
            if d.alertar:
                log.info("ALERTA %s [%s] %s R$ %.2f", d.regra, loja.nome, o.titulo[:60], o.preco)
                achados.append((o, d))
            else:
                log.info("sem alerta [%s] %s: %s", loja.nome, o.titulo[:60], d.motivo)
    return achados


def varredura(enviar: bool = True) -> int:
    if not _rodando.acquire(blocking=False):
        log.warning("Varredura anterior ainda rodando, pulando esta janela.")
        return 0
    try:
        from stores import montar_lojas
        from whatsapp import cliente_do_ambiente

        inicio = time.time()
        cat = Catalogo(RAIZ / "config" / "catalogo.yaml")
        banco = Banco(RAIZ / "data" / "radar.db")
        lojas = montar_lojas(RAIZ / "config" / "lojas.yaml")
        oficiais = [l for l in lojas if l.oficial]
        demais = [l for l in lojas if not l.oficial]
        log.info("Varredura iniciada: %d lojas, %d buscas.", len(lojas), len(cat.todas_buscas()))

        achados: list[tuple[Oferta, Decisao]] = []
        # lojas oficiais primeiro: elas alimentam a tabela de preço de capa
        for grupo in (oficiais, demais):
            with ThreadPoolExecutor(max_workers=max(1, len(grupo))) as ex:
                for res in ex.map(lambda l: varrer_loja(l, cat, banco), grupo):
                    achados.extend(res)

        ordem = {"maxima": 0, "alta": 1, "normal": 2}
        achados.sort(key=lambda x: (x[1].regra != "A", ordem.get(x[1].prioridade, 2), -(x[1].desconto_pct or 0)))

        realertar = int(os.getenv("REALERTAR_DIAS", "7"))
        destino = os.getenv("WHATSAPP_DESTINO", "")
        cli = cliente_do_ambiente() if enviar else None
        enviados = 0
        for o, d in achados:
            if not banco.deve_alertar(o.url, o.preco, realertar):
                continue
            texto = montar(o, d)
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


def teste():
    from whatsapp import cliente_do_ambiente
    o = Oferta(loja="Loja Panini", titulo="A Saga do Flash Vol. 14", url="https://exemplo.com/produto",
               preco=89.90, preco_lista=None, condicao="novo")
    d = Decisao(True, "A", "saga-do-flash", "A Saga do Flash", "Encadernado", 14, 79.90, -12.5, "maxima")
    texto = "🧪 *MENSAGEM DE TESTE*\n\n" + montar(o, d)
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
    a = ap.parse_args()
    if a.teste:
        teste()
    elif a.agora:
        varredura(True)
    elif a.seco:
        varredura(False)
    else:
        agendar()
