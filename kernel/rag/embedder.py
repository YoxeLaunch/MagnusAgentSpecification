"""Embedder local que implementa el puerto `Embedder` (paso 4.1 del ROADMAP).

Qué es y qué NO es
------------------
Es **random indexing**: cada rasgo del texto (unigrama, bigrama, prefijo) se
proyecta a un vector ternario pseudoaleatorio determinista, y el documento es
la suma ponderada por TF-IDF de los rasgos que contiene, normalizada a norma 1.
La salida es un vector denso de dimensión fija sobre el que se hace coseno.

**No es un embedding neuronal.** No conoce sinónimos que no compartan forma:
"paro" y "desempleo" siguen sin parecerse. Lo que sí aporta frente al retriever
léxico existente ([`file_store.py`](file_store.py), TF puro por solape de
tokens):

  - **IDF**: "banco" pesa menos que "glinfático". El léxico no distingue.
  - **Bigramas**: "banco central" y "política monetaria" son un rasgo, no dos
    palabras sueltas que casan por separado.
  - **Prefijos**: "inflación", "inflacionario" e "inflacionaria" comparten
    rasgo. En español esto importa mucho.
  - **Coseno normalizado**: la longitud del chunk deja de sesgar el score.

Un `Embedder` neuronal de verdad (bge-m3 vía sentence-transformers, Voyage,
etc.) se enchufa en este mismo puerto sin tocar nada aguas arriba — esa es
justamente la razón de que el puerto exista. Se eligió esta implementación
para no meter torch ni una descarga de ~2 GB en un proyecto que hoy corre
entero con la biblioteca estándar.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

_STOP = {
    "de", "la", "el", "en", "y", "a", "los", "las", "un", "una", "que", "con",
    "por", "para", "del", "al", "se", "su", "sus", "o", "es", "como", "mas",
    "the", "of", "to", "and", "in", "for", "on", "is", "are",
}

_ACENTOS = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
            ("ü", "u"), ("ñ", "n"))

_PREFIJO = 5          # longitud del rasgo de prefijo
_NO_CEROS = 4         # entradas no nulas por rasgo en la proyección


def _norm(texto: str) -> str:
    t = texto.lower()
    for a, b in _ACENTOS:
        t = t.replace(a, b)
    return t


def _tokens(texto: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _norm(texto))
            if w not in _STOP and len(w) > 2]


def _rasgos(texto: str) -> list[str]:
    """Unigramas + bigramas + prefijos. Determinista y sin estado."""
    toks = _tokens(texto)
    rasgos = list(toks)
    rasgos.extend(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
    rasgos.extend(f"^{t[:_PREFIJO]}" for t in toks if len(t) > _PREFIJO)
    return rasgos


@dataclass
class HashingEmbedder:
    """Random indexing con pesos TF-IDF. `fit()` aprende el IDF del corpus."""

    dim: int = 256
    name: str = "random_indexing_es"
    _idf: dict[str, float] = field(default_factory=dict, repr=False)
    _n_docs: int = 0
    _proyeccion: dict[str, tuple[tuple[int, float], ...]] = field(
        default_factory=dict, repr=False)

    # -- ajuste --------------------------------------------------------------
    def fit(self, textos: list[str]) -> "HashingEmbedder":
        """Calcula el IDF. Sin esto todos los rasgos pesarían igual."""
        df: dict[str, int] = {}
        for texto in textos:
            for rasgo in set(_rasgos(texto)):
                df[rasgo] = df.get(rasgo, 0) + 1
        self._n_docs = len(textos)
        n = max(1, self._n_docs)
        # IDF suavizado; un rasgo que aparece en todos los documentos no
        # discrimina y su peso tiende a 0.
        self._idf = {r: math.log((n + 1) / (d + 1)) + 1.0 for r, d in df.items()}
        return self

    @property
    def ajustado(self) -> bool:
        return bool(self._idf)

    # -- puerto Embedder -----------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    # -- interno -------------------------------------------------------------
    def _vector(self, texto: str) -> list[float]:
        tf: dict[str, int] = {}
        for rasgo in _rasgos(texto):
            tf[rasgo] = tf.get(rasgo, 0) + 1
        if not tf:
            return [0.0] * self.dim

        v = [0.0] * self.dim
        # Un rasgo que no se vio en el corpus no aporta señal comparable: se
        # le da el IDF máximo posible acotado, no se descarta (una consulta
        # puede traer términos que solo están en una nota).
        idf_desconocido = math.log(self._n_docs + 1) + 1.0 if self._n_docs else 1.0
        for rasgo, n in tf.items():
            peso = (1.0 + math.log(n)) * self._idf.get(rasgo, idf_desconocido)
            for i, signo in self._proyectar(rasgo):
                v[i] += signo * peso

        norma = math.sqrt(sum(x * x for x in v))
        return [x / norma for x in v] if norma else v

    def _proyectar(self, rasgo: str) -> tuple[tuple[int, float], ...]:
        """Vector ternario disperso y determinista para un rasgo (cacheado)."""
        cache = self._proyeccion.get(rasgo)
        if cache is not None:
            return cache
        digest = hashlib.blake2b(rasgo.encode("utf-8"), digest_size=16).digest()
        entradas = []
        for j in range(_NO_CEROS):
            trozo = digest[j * 4:(j + 1) * 4]
            idx = int.from_bytes(trozo[:3], "big") % self.dim
            signo = 1.0 if trozo[3] & 1 else -1.0
            entradas.append((idx, signo))
        self._proyeccion[rasgo] = tuple(entradas)
        return self._proyeccion[rasgo]


def coseno(a: list[float], b: list[float]) -> float:
    """Ambos vectores llegan ya normalizados, así que es el producto punto."""
    return sum(x * y for x, y in zip(a, b))
