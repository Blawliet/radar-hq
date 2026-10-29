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
from mensagem import boletim_lojas, campos, juntar_nomes, linha_historico, montar, resumo_do_dia  # noqa: E402
from models import Decisao, Oferta  # noqa: E402
from rules import Catalogo, Grupo, avaliar, melhor_decisao, resumo_perfil  # noqa: E402

carregar_env()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%d/%m %H:%M:%S",
)
log = logging.getLogger("radar")

JANELAS = [(5, 30), (7, 30), (9, 30), (11, 30), (13, 30), (15, 30), (17, 30), (19, 30), (21, 30), (23, 30)]
JANELA_RESUMO = (23, 30)
TEMPO_MAX_LOJA_MIN = float(os.getenv("TEMPO_MAX_LOJA_MIN", "15"))   # cada loja para depois disso
DIAS_ESCASSEZ = 3                                                   # janela para contar lojas com o item
MAX_FALHAS_SEGUIDAS = 6                                             # loja que não responde é abandonada
_rodando = threading.Lock()
_vistos_lock = threading.Lock()
VISTOS_NA_VARREDURA: list[dict] = []     # tudo que casou com alguma lista e tinha estoque (para o relatório)


def chave_capa(d: Decisao) -> str:
    return f"{d.entrada_id}:{d.volume if d.volume is not None else 'unico'}"


def descartavel(d: Decisao) -> bool:
    """Pode pular sem abrir a página? Só quando temos certeza pelo título."""
    if not d.entrada_id:
        return True                                    # bloqueado ou fora do catálogo
    if d.motivo.startswith("pacote"):
        return True                                    # "pacote" sem quantidade de volumes no título
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


def registrar_visto(o: Oferta, grupo: Grupo):
    """Guarda, para o relatório, cada item com estoque que casa com a lista de alguém
    (mesmo que não tenha virado alerta por causa de preço)."""
    for p in grupo.perfis:
        d = avaliar(o, p)
        if not d.entrada_id or d.entrada_id.startswith("radar-") or d.motivo.startswith(("volume", "pacote")):
            continue
        if d.motivo.startswith("bloqueado"):
            continue
        with _vistos_lock:
            VISTOS_NA_VARREDURA.append({"pessoa": p.nome, "entrada": d.entrada_id, "volume": d.volume,
                                        "loja": o.loja, "preco": o.preco, "url": o.url})


def varrer_loja(loja, grupo: Grupo, banco: Banco) -> list[Oferta]:
    try:
        return _varrer_loja(loja, grupo, banco)
    finally:
        loja.f.fechar()        # fecha o navegador na mesma thread que o abriu


def avaliar_perfil(o: Oferta, cat: Catalogo, oficial: bool, banco: Banco,
                   n_lojas: int | None = None) -> Decisao | None:
    d0 = avaliar(o, cat)                               # com título e preço da página
    if not d0.entrada_id or d0.volume is None and d0.motivo.startswith("volume"):
        return None
    chave = "capa:" + o.extra.get("chave", chave_capa(d0))
    if d0.pacote:
        # pacote: capa estimada = nº de volumes x preço típico de um volume da série
        entrada = next((e for e in cat.promocoes if e.id == d0.entrada_id), None)
        serie = normalizar(entrada.termos[0]) if entrada and entrada.termos else ""
        unit = (entrada.preco_volume if entrada and entrada.preco_volume else None) \
            or (banco.preco_tipico_volume(serie) if serie else None)
        d = avaliar(o, cat, unit * d0.pacote if unit else None)
        d.preco_volume = unit
    else:
        if oficial:
            banco.salvar_capa(chave, o.preco_lista or o.preco, o.loja)
        d = avaliar(o, cat, banco.capa(chave))
    d.lojas_com_estoque = n_lojas
    # difícil de achar: item da lista de promoções que só apareceu em poucas lojas
    # vale mesmo sem desconto (o mais barato entre elas é o que vai no alerta)
    if (not d.alertar and d.regra == "B" and not d.pacote and n_lojas and cat.escasso_max_lojas
            and n_lojas <= cat.escasso_max_lojas
            and d.motivo.startswith(("desconto", "sem preço de capa"))):
        d.alertar, d.regra = True, "ESCASSO"
        d.motivo = f"difícil de achar ({n_lojas} loja{'s' if n_lojas > 1 else ''})"
    quem = f"[{cat.nome}] " if cat.nome else ""
    if d.alertar:
        log.info("ALERTA %s%s [%s] %s R$ %.2f", quem, d.regra, o.loja, o.titulo[:60], o.preco)
        return d
    log.info("sem alerta %s[%s] %s: %s", quem, o.loja, o.titulo[:60], d.motivo)
    return None


def _varrer_loja(loja, grupo: Grupo, banco: Banco) -> list[Oferta]:
    """Busca e valida. Devolve as ofertas com estoque confirmado; as regras de cada
    pessoa são aplicadas depois, com todas as lojas já varridas."""
    achados, vistos = [], set()
    t0, n_buscas, n_result, n_erros, n_valid = time.time(), 0, 0, 0, 0
    # cada loja tem um tempo máximo; uma loja lenta ou travada não segura as outras
    prazo = t0 + float(getattr(loja, "cfg", {}).get("tempo_max_min", TEMPO_MAX_LOJA_MIN)) * 60
    try:
        loja.f.prazo = prazo
    except AttributeError:
        pass

    def estourou():
        return time.time() > prazo

    def travada():
        return getattr(loja.f, "falhas_seguidas", 0) >= MAX_FALHAS_SEGUIDAS
    parada = ""
    # páginas fixas (ex.: um produto específico) primeiro, depois as buscas
    fixas = [Oferta(loja.nome, nome, url, 0, extra={"fixa": True})
             for url, nome in grupo.urls_fixas() if loja.dominio and loja.dominio in url]
    lotes = [(None, fixas)]
    for _, termo, categoria in grupo.todas_buscas():
        if categoria in loja.categorias:
            lotes.append((categoria, termo))
    for categoria, item in lotes:
        if estourou():
            parada = "tempo esgotado"
            break
        if travada():
            parada = "loja não responde"
            break
        if categoria is None:
            candidatos = item
        else:
            loja.categoria_atual = categoria
            n_buscas += 1
            try:
                candidatos = loja.buscar(item)
            except Exception as e:
                n_erros += 1
                log.warning("%s: erro na busca '%s': %s", loja.nome, item, e)
                continue
            n_result += len(candidatos)
        for o in candidatos:
            if estourou() or travada():
                break
            if o.url in vistos:
                continue
            vistos.add(o.url)

            # filtro barato pelo título da busca: só abre a página se interessar a alguém
            if not o.extra.get("fixa") and all(descartavel(avaliar(o, p)) for p in grupo.perfis):
                continue

            n_valid += 1
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

            registrar_visto(o, grupo)
            o.extra["oficial"] = loja.oficial
            achados.append(o)
    if not parada and travada():
        parada = "loja não responde"
    elif not parada and estourou():
        parada = "tempo esgotado"
    if not parada and getattr(loja, "aviso", ""):
        parada = loja.aviso          # ex.: "API bloqueada (HTTP 403), busca pelo site"
    log.info("%s: %d buscas, %d resultados, %d páginas conferidas, %d com estoque (%.0fs)%s.",
             loja.nome, n_buscas, n_result, n_valid, len(achados), time.time() - t0,
             f" | PAROU: {parada}" if parada else "")
    banco.diagnostico(loja.nome, n_buscas, n_result, n_erros, n_valid, len(achados), time.time() - t0, parada)
    return achados


def varredura(enviar: bool = True, imprimir: bool = True) -> int:
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
        log.info("Varredura iniciada: %d lojas, %d buscas, perfis: %s.", len(lojas),
                 len(grupo.todas_buscas()), ", ".join(p.nome or "único" for p in grupo.perfis))

        # todas as lojas ao mesmo tempo
        validadas: list[Oferta] = []
        with ThreadPoolExecutor(max_workers=max(1, len(lojas))) as ex:
            for res in ex.map(lambda l: varrer_loja(l, grupo, banco), lojas):
                validadas.extend(res)
        log.info("Coleta terminou em %.0fs.", time.time() - inicio)

        # regras de cada pessoa; ofertas das lojas oficiais primeiro, porque elas
        # atualizam a tabela de preço de capa usada para as demais
        achados: list[tuple[Oferta, Decisao, list[str]]] = []
        # em quantas lojas cada produto apareceu com estoque nesta varredura
        # Conta as lojas desta varredura E dos últimos 3 dias: a busca de cada loja traz só
        # parte dos volumes de uma série, então olhar uma varredura só faz item comum
        # parecer "difícil de achar".
        lojas_por_produto: dict[str, set] = {}
        for o in validadas:
            lojas_por_produto.setdefault(o.extra.get("chave", o.url), set()).add(o.loja)
        for chave in lojas_por_produto:
            lojas_por_produto[chave] |= banco.lojas_recentes(chave, DIAS_ESCASSEZ)
        for o in sorted(validadas, key=lambda x: not x.extra.get("oficial")):
            n = len(lojas_por_produto.get(o.extra.get("chave", o.url), {o.loja}))
            por_pessoa = [(p.nome, avaliar_perfil(o, p, bool(o.extra.get("oficial")), banco, n))
                          for p in grupo.perfis]
            por_pessoa = [(n, d) for n, d in por_pessoa if d]
            if por_pessoa:
                achados.append((o, melhor_decisao([d for _, d in por_pessoa]), [n for n, _ in por_pessoa]))

        # o mesmo produto pode aparecer em buscas diferentes: junta os interessados
        unicos: dict[str, tuple[Oferta, Decisao, list[str]]] = {}
        for o, d, nomes in achados:
            if o.url in unicos:
                _, d_ant, n_ant = unicos[o.url]
                unicos[o.url] = (o, melhor_decisao([d, d_ant]), sorted(set(n_ant) | set(nomes), key=str))
            else:
                unicos[o.url] = (o, d, nomes)
        # o mesmo produto em várias lojas: um alerta só, com a oferta mais barata
        por_produto: dict[str, list] = {}
        for o, d, nomes in unicos.values():
            por_produto.setdefault(o.extra.get("chave", o.url), []).append((o, d, nomes))
        agrupados = []
        for itens in por_produto.values():
            itens.sort(key=lambda x: x[0].preco)
            o, d, nomes = itens[0]
            todos = sorted({n for _, _, ns in itens for n in ns if n}, key=str)
            outras = [(x[0].loja, x[0].preco) for x in itens[1:]]
            o.extra["urls_grupo"] = [x[0].url for x in itens[1:]]
            agrupados.append((o, d, todos or nomes, outras))
        ordem = {"maxima": 0, "alta": 1, "normal": 2}
        achados = sorted(agrupados, key=lambda x: (x[1].regra not in ("A", "ESCASSO"), ordem.get(x[1].prioridade, 2),
                                                   -(x[1].desconto_pct or 0)))

        realertar = int(os.getenv("REALERTAR_DIAS", "7"))
        destino = os.getenv("WHATSAPP_DESTINO", "")
        cli = cliente_do_ambiente() if enviar else None
        enviados = 0
        for o, d, nomes, outras in achados:
            if not banco.deve_alertar(o.url, o.preco, realertar):
                continue
            anterior = banco.menor_preco_anterior(o.extra.get("chave", o.url), o.url,
                                                  o.extra.get("urls_grupo", []))
            if d.so_menor_preco and anterior and o.preco > anterior[0] - 0.005:
                log.info("sem alerta (não é o menor preço já visto) %s R$ %.2f", o.titulo[:50], o.preco)
                continue
            texto = montar(o, d, nomes, linha_historico(o.preco, anterior), outras)
            if not enviar:
                if not imprimir:
                    continue
                print("\n" + texto + "\n" + "-" * 40)
                continue
            if cli.enviar_alerta(destino, campos(o, d), texto):
                recorde = bool(anterior and o.preco < anterior[0] - 0.005)
                banco.registrar_alerta(o.url, o.preco, d.regra, o.titulo, o.loja, d.desconto_pct,
                                       juntar_nomes(nomes) if nomes and any(nomes) else None, recorde)
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
        tarefa = resumo if (h, m) == JANELA_RESUMO else varredura
        sched.add_job(tarefa, CronTrigger(hour=h, minute=m, timezone=tz),
                      id=f"janela_{h:02d}{m:02d}", max_instances=1, coalesce=True,
                      misfire_grace_time=1800)
    log.info("Agendado: %s (horário de Brasília).", ", ".join(f"{h:02d}h{m:02d}" for h, m in JANELAS))

    if os.getenv("WEBHOOK_ENABLED", "false").lower() == "true":
        from webhook_server import iniciar_webhook
        iniciar_webhook()

    sched.start()


def resumo(enviar: bool = True):
    """Última janela do dia: varre, manda os alertas e depois o resumão do dia."""
    from datetime import datetime
    from whatsapp import cliente_do_ambiente
    varredura(enviar)
    banco = Banco(RAIZ / "data" / "radar.db")
    # se o GitHub atrasar e a execução passar da meia-noite, o resumo continua sendo do dia certo
    from datetime import timedelta
    hoje = datetime.now() - timedelta(hours=3)
    checados, lojas = banco.checagens_do_dia(hoje.strftime("%Y-%m-%d"))
    texto = resumo_do_dia(hoje.strftime("%d/%m"), banco.alertas_do_dia(hoje.strftime("%Y-%m-%d")),
                          checados, lojas)
    import yaml
    cfg = yaml.safe_load((RAIZ / "config" / "lojas.yaml").read_text(encoding="utf-8")) or {}
    ativas = [c["nome"] for c in cfg.get("lojas", []) if c.get("ativo", True)]
    boletim = boletim_lojas(hoje.strftime("%d/%m"), banco.saude_lojas(hoje.strftime("%Y-%m-%d")), ativas)
    if enviar:
        cli = cliente_do_ambiente()
        cli.enviar_texto(os.getenv("WHATSAPP_DESTINO", ""), texto)
        time.sleep(1.5)
        cli.enviar_texto(os.getenv("WHATSAPP_DESTINO", ""), boletim)
    else:
        print(texto + "\n\n" + boletim)


def relatorio(enviar: bool = True):
    """Varre todas as lojas sem mandar alertas e envia, por pessoa, o quanto cada item
    está fácil ou difícil de achar (quantas lojas têm em estoque e o menor preço)."""
    from mensagem import brl
    from whatsapp import cliente_do_ambiente
    VISTOS_NA_VARREDURA.clear()
    varredura(enviar=False, imprimir=False)
    cli = cliente_do_ambiente() if enviar else None
    textos = []
    for p in Grupo(RAIZ / "config").perfis:
        itens = []
        for e in p.raros + p.promocoes:
            if not e.ativo:
                continue
            vols = sorted(e.volumes.exatos) if e.volumes.exatos and not e.volumes.faixas \
                and e.volumes.a_partir is None and len(e.volumes.exatos) > 1 else [None]
            for v in vols:
                achados = [x for x in VISTOS_NA_VARREDURA if x["pessoa"] == p.nome and x["entrada"] == e.id
                           and (v is None or x["volume"] == v)]
                lojas = {}
                for x in achados:
                    if x["loja"] not in lojas or x["preco"] < lojas[x["loja"]]:
                        lojas[x["loja"]] = x["preco"]
                nome = e.nome + (f" vol. {v}" if v is not None else "")
                itens.append((nome, lojas))
        if not itens:
            continue
        faceis = [(n, l) for n, l in itens if len(l) >= 3]
        poucos = [(n, l) for n, l in itens if 1 <= len(l) <= 2]
        sumidos = [n for n, l in itens if not l]

        def linha(n, l):
            loja_min = min(l, key=l.get)
            return f"• {n}: {len(l)} loja{'s' if len(l) > 1 else ''}, a partir de {brl(l[loja_min])} ({loja_min})"
        partes = [f"📊 *Rastreamento de {p.nome}*", ""]
        if faceis:
            partes += ["🟢 *Fácil de achar (3+ lojas)*"] + [linha(n, l) for n, l in faceis] + [""]
        if poucos:
            partes += ["🟡 *Pouca oferta (1 ou 2 lojas)*"] + [linha(n, l) for n, l in poucos] + [""]
        if sumidos:
            partes += ["🔴 *Não achei com estoque*"] + [f"• {n}" for n in sumidos] + [""]
        partes.append("_Amazon e Mercado Livre às vezes bloqueiam o radar, então um item em 🔴 pode existir lá._")
        textos.append("\n".join(partes))
    for t in textos:
        if enviar:
            cli.enviar_texto(os.getenv("WHATSAPP_DESTINO", ""), t)
            time.sleep(1.5)
        else:
            print(t + "\n")


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
    g.add_argument("--relatorio", action="store_true")
    g.add_argument("--resumo", action="store_true")
    a = ap.parse_args()
    if a.teste:
        teste()
    elif a.lista:
        listar(True)
    elif a.relatorio:
        relatorio(True)
    elif a.resumo:
        resumo(True)
    elif a.agora:
        varredura(True)
    elif a.seco:
        varredura(False)
    else:
        agendar()
