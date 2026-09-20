"""Busca no regulamento interno por trecho relevante (Garantia 4).

O arquivo `dados/regulamento.md` é lido uma única vez e dividido em blocos no
nível de artigo (cada "Art. N" com seus parágrafos/incisos, junto do título
do capítulo a que pertence). A busca é um ranking simples por sobreposição de
palavras, ponderado pela raridade do termo entre os blocos (uma variante leve
de TF-IDF), sem nenhuma chamada externa e sem depender do modelo para
"lembrar" o texto.

Isso é o que sustenta a garantia: o agente principal nunca recebe o
regulamento nas instruções, e a tool devolve apenas os artigos relevantes
para a pergunta — nunca o documento inteiro nem capítulos que tratam de
outros assuntos.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import config

_ART_HEADER_RE = re.compile(r"^\*\*Art\.\s*(\d+)")
_CAPITULO_RE = re.compile(r"^##\s*(Cap[ií]tulo.*)$")

_STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "das", "dos", "em", "no", "na", "nos",
    "nas", "um", "uma", "uns", "umas", "para", "por", "com", "sem", "que",
    "se", "os", "as", "ao", "aos", "à", "às", "é", "ou", "como", "quando",
    "qual", "quais", "quanto", "quanta", "quantos", "quantas", "the", "to",
    "sao", "são", "ser", "estar", "seu", "sua", "seus", "suas", "isso",
    "esse", "essa", "este", "esta", "pode", "podem", "deve", "devem",
}


@dataclass(frozen=True)
class Artigo:
    capitulo: str
    numero: str
    texto: str


def _normalizar(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento


def _tokenizar(texto: str) -> list[str]:
    texto = _normalizar(texto)
    tokens = re.findall(r"[a-z0-9]+", texto)
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


@lru_cache(maxsize=1)
def _carregar_artigos() -> tuple[Artigo, ...]:
    linhas = config.REGULAMENTO_MD.read_text(encoding="utf-8").splitlines()

    artigos: list[Artigo] = []
    capitulo_atual = ""
    numero_atual: str | None = None
    buffer: list[str] = []

    def _fechar_artigo() -> None:
        if numero_atual is not None and buffer:
            artigos.append(
                Artigo(
                    capitulo=capitulo_atual,
                    numero=numero_atual,
                    texto="\n".join(buffer).strip(),
                )
            )

    for linha in linhas:
        cap_match = _CAPITULO_RE.match(linha)
        if cap_match:
            _fechar_artigo()
            capitulo_atual = cap_match.group(1).strip()
            numero_atual = None
            buffer = []
            continue

        art_match = _ART_HEADER_RE.match(linha)
        if art_match:
            _fechar_artigo()
            numero_atual = art_match.group(1)
            buffer = [linha]
            continue

        if numero_atual is not None:
            buffer.append(linha)

    _fechar_artigo()
    return tuple(artigos)


@lru_cache(maxsize=1)
def _indice_documento() -> tuple[dict[str, int], tuple[tuple[str, ...], ...]]:
    """Pré-computa os tokens de cada artigo e a frequência de documentos por
    termo, para pontuar buscas sem reprocessar o regulamento a cada chamada."""
    artigos = _carregar_artigos()
    tokens_por_artigo = tuple(tuple(_tokenizar(a.texto)) for a in artigos)
    doc_freq: dict[str, int] = {}
    for tokens in tokens_por_artigo:
        for termo in set(tokens):
            doc_freq[termo] = doc_freq.get(termo, 0) + 1
    return doc_freq, tokens_por_artigo


def buscar(pergunta: str, top_k: int = 2) -> list[Artigo]:
    """Retorna os `top_k` artigos mais relevantes para a pergunta, ou uma
    lista vazia se nenhum termo da pergunta aparecer no regulamento."""
    artigos = _carregar_artigos()
    doc_freq, tokens_por_artigo = _indice_documento()
    total_docs = len(artigos)

    termos_pergunta = set(_tokenizar(pergunta))
    if not termos_pergunta:
        return []

    pontuacoes: list[tuple[float, int]] = []
    for i, tokens in enumerate(tokens_por_artigo):
        if not tokens:
            continue
        tokens_set = set(tokens)
        score = 0.0
        for termo in termos_pergunta:
            if termo in tokens_set:
                df = doc_freq.get(termo, total_docs)
                # Termos raros entre os artigos pesam mais que termos comuns
                # (ex.: "condomínio" aparece em quase todo artigo e não deve
                # dominar o ranking frente a um termo específico como
                # "piscina").
                score += 1.0 / (1.0 + df)
        if score > 0:
            pontuacoes.append((score, i))

    pontuacoes.sort(key=lambda par: par[0], reverse=True)
    if not pontuacoes:
        return []
    melhor_score = pontuacoes[0][0]
    # Descarta correspondências muito mais fracas que a melhor, para evitar
    # trazer artigos de capítulos que só tangenciam a pergunta.
    relevantes = [
        (score, i) for score, i in pontuacoes if score >= 0.5 * melhor_score
    ]
    return [artigos[i] for _, i in relevantes[:top_k]]


def formatar_resultado(artigos: list[Artigo]) -> str:
    if not artigos:
        return (
            "Nenhum trecho do regulamento interno corresponde a essa "
            "pergunta."
        )
    blocos = []
    for art in artigos:
        blocos.append(f"[{art.capitulo}]\n{art.texto}")
    return "\n\n".join(blocos)
