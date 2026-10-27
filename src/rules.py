"""Motor de regras A (raros), B (promoções), C (bloqueios) e Radar Omnibus."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from models import Decisao, Oferta
from texto import contar_volumes, contem, extrair_volume, normalizar

log = logging.getLogger("regras")


# ------------------------------------------------------------------ volumes
@dataclass
class SpecVolume:
    todos: bool = False
    box: bool = False
    exatos: frozenset = frozenset()
    faixas: tuple = ()          # ((ini, fim), ...)
    a_partir: int | None = None

    @classmethod
    def parse(cls, spec) -> "SpecVolume":
        s = str(spec).strip().lower().replace(" ", "")
        if s in ("todos", "todas", "*"):
            return cls(todos=True)
        if s == "box":
            return cls(box=True)
        exatos, faixas, a_partir = set(), [], None
        for parte in s.split(","):
            if not parte:
                continue
            if parte.endswith("+"):
                n = int(parte[:-1])
                a_partir = n if a_partir is None else min(a_partir, n)
            elif "-" in parte:
                a, b = parte.split("-")
                faixas.append((int(a), int(b)))
            else:
                exatos.add(int(parte))
        return cls(exatos=frozenset(exatos), faixas=tuple(faixas), a_partir=a_partir)

    def aceita(self, vol: int | None) -> bool:
        if self.todos or self.box:
            return True
        if vol is None:
            return False
        if vol in self.exatos:
            return True
        if any(a <= vol <= b for a, b in self.faixas):
            return True
        return self.a_partir is not None and vol >= self.a_partir


# ------------------------------------------------------------------ entradas
@dataclass
class Entrada:
    id: str
    nome: str
    termos: list
    qualquer: list
    qualquer_2: list
    excluir: list
    regex: re.Pattern | None
    excluir_regex: re.Pattern | None
    volumes: SpecVolume
    formato: str | None
    prioridade: str
    ativo: bool
    buscas: list
    editora: str | None
    volumes_txt: str = "todos"
    modo: str = "normal"                  # "pacote": só kits/boxes com vários volumes
    categoria: str = "livros"             # "livros" ou "games": define em quais lojas buscar
    so_menor_preco: bool = False          # só alerta quando bate o menor preço já visto
    urls: tuple = ()                      # páginas de produto conferidas em toda varredura
    preco_volume: float | None = None     # referência manual de 1 volume (opcional)

    @classmethod
    def de_dict(cls, d: dict) -> "Entrada":
        return cls(
            id=d["id"],
            nome=d.get("nome", d["id"]),
            termos=d.get("termos", []),
            qualquer=d.get("qualquer", []),
            qualquer_2=d.get("qualquer_2", []),
            excluir=d.get("excluir", []),
            regex=re.compile(d["regex"]) if d.get("regex") else None,
            excluir_regex=re.compile(d["excluir_regex"]) if d.get("excluir_regex") else None,
            volumes=SpecVolume.parse(d.get("volumes", "todos")),
            formato=d.get("formato"),
            prioridade=d.get("prioridade", "normal"),
            ativo=d.get("ativo", True),
            buscas=d.get("buscas", []),
            editora=d.get("editora"),
            volumes_txt=str(d.get("volumes", "todos")),
            modo=d.get("modo", "normal"),
            categoria=d.get("categoria", "livros"),
            so_menor_preco=bool(d.get("so_menor_preco", False)),
            urls=tuple(d.get("urls") or ()),
            preco_volume=d.get("preco_volume"),
        )

    def casa(self, t: str) -> bool:
        if not all(contem(t, x) for x in self.termos):
            return False
        if self.qualquer and not any(contem(t, x) for x in self.qualquer):
            return False
        if self.qualquer_2 and not any(contem(t, x) for x in self.qualquer_2):
            return False
        if any(contem(t, x) for x in self.excluir):
            return False
        if self.regex and not self.regex.search(t):
            return False
        if self.excluir_regex and self.excluir_regex.search(t):
            return False
        return True


# ------------------------------------------------------------------ catálogo
def _yaml(caminho) -> dict:
    return yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}


class Catalogo:
    """Catálogo de UMA pessoa: config e radares do arquivo geral + listas da pessoa.
    Sem arquivo de pessoa, lê tudo do arquivo geral (formato antigo)."""

    def __init__(self, caminho: str | Path, pessoa: str | Path | None = None):
        dados = _yaml(caminho)
        cfg = dados.get("config", {})
        self.desconto_minimo = float(cfg.get("desconto_minimo", 25))
        self.margem_lista = float(cfg.get("margem_preco_lista", 1.05))
        # item de promoção achado em até N lojas = difícil de achar: avisa mesmo sem desconto (0 desliga)
        self.escasso_max_lojas = int(cfg.get("escasso_max_lojas", 2))
        self.excluir_global = list(cfg.get("excluir_titulos", []))
        radares = dados.get("radares", [])
        listas, self.nome = dados, None
        if pessoa:
            listas = _yaml(pessoa)
            self.nome = listas.get("nome") or Path(pessoa).stem.title()
            ligados = set(listas.get("radares") or [])
            radares = [r for r in radares if r["id"] in ligados]
        self.bloqueios = [Entrada.de_dict(d) for d in listas.get("bloqueios") or []]
        self.raros = [Entrada.de_dict(d) for d in listas.get("raros") or []]
        self.promocoes = [Entrada.de_dict(d) for d in listas.get("promocoes") or []]
        self.radares = [Entrada.de_dict({**d, "volumes": "todos"}) for d in radares]
        ro = dados.get("radar_omnibus")          # formato antigo do catálogo
        if ro and not any(r.id == "radar-omnibus" for r in self.radares):
            self.radares.insert(0, Entrada.de_dict(
                {"id": "radar-omnibus", "nome": "Radar Omnibus", "termos": ro.get("termos", ["omnibus"]),
                 "volumes": "todos", "formato": "Omnibus", "ativo": ro.get("ativo", True),
                 "buscas": ro.get("buscas", [])}))
        inativos = [e.id for e in self.promocoes + self.raros if not e.ativo]
        if inativos:
            log.warning("%sEntradas inativas: %s", f"[{self.nome}] " if self.nome else "", ", ".join(inativos))

    def todas_buscas(self) -> list[tuple[str, str, str]]:
        """[(entrada_id, termo_de_busca, categoria)] sem duplicatas, raros primeiro."""
        vistos, saida = set(), []
        for e in self.raros + self.promocoes + self.radares:
            if not e.ativo:
                continue
            for b in e.buscas:
                if (b.lower(), e.categoria) not in vistos:
                    vistos.add((b.lower(), e.categoria))
                    saida.append((e.id, b, e.categoria))
        return saida

    def urls_fixas(self) -> list[tuple[str, str]]:
        return [(u, e.nome) for e in self.raros + self.promocoes if e.ativo for u in e.urls]

    def categoria_do_titulo(self, t: str) -> str | None:
        for e in self.raros + self.promocoes:
            if e.ativo and e.casa(t):
                return e.categoria
        return None


class Grupo:
    """Todos os perfis: config/catalogo.yaml + config/pessoas/*.yaml."""

    def __init__(self, pasta_config: str | Path):
        pasta = Path(pasta_config)
        arquivos = sorted((pasta / "pessoas").glob("*.yaml")) if (pasta / "pessoas").is_dir() else []
        geral = pasta / "catalogo.yaml"
        self.perfis = [Catalogo(geral, a) for a in arquivos] or [Catalogo(geral)]

    def todas_buscas(self) -> list[tuple[str, str, str]]:
        vistos, saida = set(), []
        for p in self.perfis:
            for eid, b, cat in p.todas_buscas():
                if (b.lower(), cat) not in vistos:
                    vistos.add((b.lower(), cat))
                    saida.append((eid, b, cat))
        return saida

    def urls_fixas(self) -> list[tuple[str, str]]:
        vistos, saida = set(), []
        for p in self.perfis:
            for u, nome in p.urls_fixas():
                if u not in vistos:
                    vistos.add(u)
                    saida.append((u, nome))
        return saida


def melhor_decisao(decisoes: list[Decisao]) -> Decisao:
    """Para montar a mensagem: raro primeiro, depois prioridade, depois maior desconto."""
    ordem = {"maxima": 0, "alta": 1, "normal": 2}
    return sorted(decisoes, key=lambda d: (d.regra != "A", ordem.get(d.prioridade, 2),
                                           -(d.desconto_pct or 0)))[0]


def volumes_legivel(spec: str, modo: str = "normal", so_menor: bool = False) -> str:
    if so_menor:
        return "quando bater o menor preço já visto"
    if modo == "pacote":
        return "só pacotes/kits com vários volumes"
    s = str(spec).strip().lower()
    if s in ("todos", "*"):
        return "todos"
    if s == "box":
        return "box/kit"
    itens = [p.strip() for p in s.split(",") if p.strip()]
    if len(itens) == 1 and itens[0].endswith("+"):
        return f"a partir do vol. {itens[0][:-1]}"
    partes = []
    for x in itens:
        if x.endswith("+"):
            partes.append(f"do {x[:-1]} em diante")
        elif "-" in x:
            a, b = x.split("-")
            partes.append(f"{a} ao {b}")
        else:
            partes.append(x)
    texto = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]
    return "vol. " + texto


def resumo_perfil(cat: Catalogo, nomes_radares: dict | None = None) -> str:
    def linhas(entradas):
        return "\n".join(f"• {e.nome}: {volumes_legivel(e.volumes_txt, e.modo, e.so_menor_preco)}" for e in entradas if e.ativo) or "• nada ainda"
    nomes_radares = nomes_radares or {}
    radares = ", ".join(nomes_radares.get(r.id, r.nome) for r in cat.radares if r.ativo) or "desligados"
    partes = [
        f"📋 *Radar de {cat.nome or 'todos'}*",
        "",
        f"⭐ *Raros / monitorados*\n{linhas(cat.raros)}",
        "",
        f"🏷️ *Promoções ({cat.desconto_minimo:.0f}% OFF ou mais"
        + (f"; em até {cat.escasso_max_lojas} lojas avisa mesmo sem desconto" if cat.escasso_max_lojas else "")
        + f")*\n{linhas(cat.promocoes)}",
        "",
        f"📡 *Radares:* {radares}",
        "",
        f"🚫 *Já tem (não alerta)*\n{linhas(cat.bloqueios)}",
    ]
    return "\n".join(partes)


# ------------------------------------------------------------------ avaliação
def detectar_formato(t: str, padrao: str | None) -> str:
    if padrao in ("Console", "Game", "Acessório"):
        return padrao
    if contem(t, "omnibus"):
        return "Omnibus"
    if padrao == "Box":
        return "Box"
    if contem(t, "capa dura"):
        return "Capa Dura"
    if padrao:
        return padrao
    if any(contem(t, x) for x in ("manga", "tankobon", "shonen", "seinen")):
        return "Mangá"
    return "Encadernado"


def calcular_capa(oferta: Oferta, ref_capa: float | None, margem: float) -> float | None:
    """Preço de capa confiável: referência da editora > preço 'de' da loja (se não inflado)."""
    lista = oferta.preco_lista if oferta.preco_lista and oferta.preco_lista > 0 else None
    preco = oferta.preco or 0
    if ref_capa:
        if lista and ref_capa <= lista <= ref_capa * margem:
            return lista
        capa = ref_capa
    else:
        capa = lista
    # mais de 75% de desconto quase sempre é preço lido errado: melhor não confiar
    if capa and preco > 0 and capa > preco * 4:
        return None
    return capa


def _desconto(preco: float, capa: float | None) -> float | None:
    if not capa or capa <= 0:
        return None
    return round((1 - preco / capa) * 100, 1)


def avaliar(oferta: Oferta, cat: Catalogo, ref_capa: float | None = None) -> Decisao:
    t = normalizar(oferta.titulo)
    vol = extrair_volume(t)

    eh_livro = (cat.categoria_do_titulo(t) or "livros") == "livros"
    for termo in (cat.excluir_global if eh_livro else []):
        if contem(t, termo):
            return Decisao(False, motivo=f"título indica edição estrangeira ('{termo}')", volume=vol)

    # Regra C
    for b in cat.bloqueios:
        if b.ativo and b.casa(t) and b.volumes.aceita(vol):
            return Decisao(False, motivo=f"bloqueado ({b.id})", volume=vol)

    reivindicado = None

    # Regra A
    for r in cat.raros:
        if r.ativo and r.casa(t):
            if r.volumes.aceita(vol):
                capa = calcular_capa(oferta, ref_capa, cat.margem_lista)
                d = Decisao(
                    True, "A", r.id, r.nome, detectar_formato(t, r.formato), vol,
                    capa, _desconto(oferta.preco, capa), r.prioridade, "raro com estoque",
                )
                d.so_menor_preco = r.so_menor_preco
                return d
            reivindicado = reivindicado or r

    # Regra B
    for p in cat.promocoes:
        if p.casa(t):
            if not p.ativo:
                return Decisao(False, motivo=f"entrada inativa ({p.id})", volume=vol)
            if p.modo == "pacote":
                n = contar_volumes(oferta.titulo)
                if not n or n < 2:
                    return Decisao(False, motivo=f"pacote sem quantidade de volumes ({p.id})", volume=vol)
                d = _decidir_promo(oferta, cat, p, t, None, ref_capa, "B")
                d.pacote = n
                return d
            if p.volumes.aceita(vol):
                return _decidir_promo(oferta, cat, p, t, vol, ref_capa, "B")
            reivindicado = reivindicado or p

    if reivindicado:
        return Decisao(False, entrada_id=reivindicado.id,
                       motivo=f"volume {vol} fora da lista ({reivindicado.id})", volume=vol)

    # Radar Omnibus
    for radar in cat.radares:
        if radar.ativo and radar.casa(t):
            return _decidir_promo(oferta, cat, radar, t, vol, ref_capa, "RADAR")

    return Decisao(False, motivo="fora do catálogo", volume=vol)


def _decidir_promo(oferta, cat, entrada, t, vol, ref_capa, regra) -> Decisao:
    capa = calcular_capa(oferta, ref_capa, cat.margem_lista)
    desc = _desconto(oferta.preco, capa)
    base = dict(regra=regra, entrada_id=entrada.id, nome=entrada.nome,
                formato=detectar_formato(t, entrada.formato), volume=vol,
                preco_capa=capa, desconto_pct=desc)
    if desc is None:
        return Decisao(False, motivo="sem preço de capa confiável", **base)
    if desc < cat.desconto_minimo:
        return Decisao(False, motivo=f"desconto {desc}% abaixo do mínimo", **base)
    return Decisao(True, motivo=f"desconto {desc}%", **base)
