"""Motor de regras A (raros), B (promoções), C (bloqueios) e Radar Omnibus."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from models import Decisao, Oferta
from texto import contem, extrair_volume, normalizar

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
    excluir: list
    regex: re.Pattern | None
    excluir_regex: re.Pattern | None
    volumes: SpecVolume
    formato: str | None
    prioridade: str
    ativo: bool
    buscas: list
    editora: str | None

    @classmethod
    def de_dict(cls, d: dict) -> "Entrada":
        return cls(
            id=d["id"],
            nome=d.get("nome", d["id"]),
            termos=d.get("termos", []),
            qualquer=d.get("qualquer", []),
            excluir=d.get("excluir", []),
            regex=re.compile(d["regex"]) if d.get("regex") else None,
            excluir_regex=re.compile(d["excluir_regex"]) if d.get("excluir_regex") else None,
            volumes=SpecVolume.parse(d.get("volumes", "todos")),
            formato=d.get("formato"),
            prioridade=d.get("prioridade", "normal"),
            ativo=d.get("ativo", True),
            buscas=d.get("buscas", []),
            editora=d.get("editora"),
        )

    def casa(self, t: str) -> bool:
        if not all(contem(t, x) for x in self.termos):
            return False
        if self.qualquer and not any(contem(t, x) for x in self.qualquer):
            return False
        if any(contem(t, x) for x in self.excluir):
            return False
        if self.regex and not self.regex.search(t):
            return False
        if self.excluir_regex and self.excluir_regex.search(t):
            return False
        return True


# ------------------------------------------------------------------ catálogo
class Catalogo:
    def __init__(self, caminho: str | Path):
        dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
        cfg = dados.get("config", {})
        self.desconto_minimo = float(cfg.get("desconto_minimo", 25))
        self.margem_lista = float(cfg.get("margem_preco_lista", 1.05))
        self.bloqueios = [Entrada.de_dict(d) for d in dados.get("bloqueios", [])]
        self.raros = [Entrada.de_dict(d) for d in dados.get("raros", [])]
        self.promocoes = [Entrada.de_dict(d) for d in dados.get("promocoes", [])]
        ro = dados.get("radar_omnibus", {}) or {}
        self.omnibus = Entrada.de_dict(
            {"id": "radar-omnibus", "nome": "Radar Omnibus", "termos": ro.get("termos", ["omnibus"]),
             "volumes": "todos", "formato": "Omnibus", "ativo": ro.get("ativo", True),
             "buscas": ro.get("buscas", [])}
        )
        inativos = [e.id for e in self.promocoes + self.raros if not e.ativo]
        if inativos:
            log.warning("Entradas inativas (preencha o volume e ative): %s", ", ".join(inativos))

    def todas_buscas(self) -> list[tuple[str, str]]:
        """[(entrada_id, termo_de_busca)] sem duplicatas, raros primeiro."""
        vistos, saida = set(), []
        for e in self.raros + self.promocoes + [self.omnibus]:
            if not e.ativo:
                continue
            for b in e.buscas:
                if b.lower() not in vistos:
                    vistos.add(b.lower())
                    saida.append((e.id, b))
        return saida


# ------------------------------------------------------------------ avaliação
def detectar_formato(t: str, padrao: str | None) -> str:
    if contem(t, "omnibus"):
        return "Omnibus"
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
    if ref_capa:
        if lista and ref_capa <= lista <= ref_capa * margem:
            return lista
        return ref_capa
    return lista


def _desconto(preco: float, capa: float | None) -> float | None:
    if not capa or capa <= 0:
        return None
    return round((1 - preco / capa) * 100, 1)


def avaliar(oferta: Oferta, cat: Catalogo, ref_capa: float | None = None) -> Decisao:
    t = normalizar(oferta.titulo)
    vol = extrair_volume(t)

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
                return Decisao(
                    True, "A", r.id, r.nome, detectar_formato(t, r.formato), vol,
                    capa, _desconto(oferta.preco, capa), r.prioridade, "raro com estoque",
                )
            reivindicado = reivindicado or r

    # Regra B
    for p in cat.promocoes:
        if p.casa(t):
            if not p.ativo:
                return Decisao(False, motivo=f"entrada inativa ({p.id})", volume=vol)
            if p.volumes.aceita(vol):
                return _decidir_promo(oferta, cat, p, t, vol, ref_capa, "B")
            reivindicado = reivindicado or p

    if reivindicado:
        return Decisao(False, entrada_id=reivindicado.id,
                       motivo=f"volume {vol} fora da lista ({reivindicado.id})", volume=vol)

    # Radar Omnibus
    if cat.omnibus.ativo and cat.omnibus.casa(t):
        return _decidir_promo(oferta, cat, cat.omnibus, t, vol, ref_capa, "OMNIBUS")

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
