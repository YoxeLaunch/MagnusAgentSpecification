"""Ingesta y recuperación REAL sobre la LLM-Wiki (Obsidian), solo stdlib.

- Recorre LLM-Wiki/wiki/**/*.md, trocea por párrafos con su encabezado.
- namespace = primera carpeta bajo wiki/ (p.ej. '01-Economia-y-Finanzas').
- Recuperación léxica por solape de términos (TF) con normalización de acentos.

Este es el retriever LÉXICO del pipeline híbrido: TF sobre tokens, que es
exactamente lo que significa recuperación léxica. El segundo retriever, el
vectorial local, vive en [`vector_store.py`](vector_store.py) y se construye
sobre estos mismos chunks (ver `documents()`), de modo que ambos comparten
`chunk_id` y el pipeline puede deduplicar y fusionar sus rankings.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from kernel.rag.pipeline import Provenance, ScoredChunk

_STOP = {
    "de", "la", "el", "en", "y", "a", "los", "las", "un", "una", "que", "con",
    "por", "para", "del", "al", "se", "su", "sus", "o", "es", "como", "mas",
    "the", "of", "to", "and", "in", "for", "on", "is", "are",
}


def _norm(text: str) -> str:
    t = text.lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        t = t.replace(a, b)
    return t


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _norm(text)) if w not in _STOP and len(w) > 2]


def _sha(text: str, n: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


class _Chunk:
    __slots__ = ("chunk_id", "namespace", "text", "source", "heading", "tf", "hash")

    def __init__(self, chunk_id, namespace, text, source, heading):
        self.chunk_id = chunk_id
        self.namespace = namespace
        self.text = text
        self.source = source
        self.heading = heading
        # Hash del pasaje EXACTO que se le entregó al agente. Permite verificar
        # después si la nota cambió desde que se emitió la cita.
        self.hash = _sha(text)
        self.tf: dict[str, int] = {}
        for tok in _tokens(text + " " + heading):
            self.tf[tok] = self.tf.get(tok, 0) + 1


class FileWikiStore:
    """Índice en memoria construido desde la carpeta real de la wiki."""

    def __init__(self, wiki_root: str | Path):
        self.root = Path(wiki_root)
        self._chunks: list[_Chunk] = []
        self._namespaces: set[str] = set()
        self._snapshot_id: str = "vacio"

    @property
    def snapshot_id(self) -> str:
        """Identificador del ESTADO de la wiki en el momento de la ingesta.

        Es el digest del contenido de todas las notas indexadas, así que dos
        ingestas distintas coinciden si y solo si la wiki no cambió. Sustituye
        al `wiki-live` anterior, que era una etiqueta constante: con ella una
        cita publicada no se podía reproducir después de editar la nota (paso
        3.5 del ROADMAP).
        """
        return self._snapshot_id

    # -- ingesta ----------------------------------------------------------
    def ingest(self) -> dict:
        n_files = 0
        huellas: list[str] = []
        for md in sorted(self.root.rglob("*.md")):
            rel = md.relative_to(self.root)
            namespace = rel.parts[0] if len(rel.parts) > 1 else "_root"
            self._namespaces.add(namespace)
            try:
                raw = md.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            n_files += 1
            source = str(rel).replace("\\", "/")
            huellas.append(f"{source}:{_sha(raw, 32)}")
            self._index_document(raw, namespace, source=source)

        self._snapshot_id = _sha("\n".join(huellas)) if huellas else "vacio"
        # La provenance se rellena al recuperar, así que el snapshot debe estar
        # calculado antes de servir cualquier chunk.
        return {
            "files": n_files,
            "chunks": len(self._chunks),
            "namespaces": sorted(self._namespaces),
            "snapshot": self._snapshot_id,
        }

    def _index_document(self, raw: str, namespace: str, source: str) -> None:
        # elimina front-matter YAML de Obsidian
        raw = re.sub(r"^---\n.*?\n---\n", "", raw, count=1, flags=re.S)
        heading = Path(source).stem
        buf: list[str] = []
        idx = 0

        def flush():
            nonlocal buf, idx
            text = " ".join(buf).strip()
            buf = []
            if len(text) < 40:
                return
            idx += 1
            self._chunks.append(_Chunk(
                chunk_id=f"{source}#{idx}", namespace=namespace,
                text=text[:1200], source=source, heading=heading))

        for line in raw.splitlines():
            s = line.strip()
            if s.startswith("#"):
                flush()
                heading = s.lstrip("#").strip() or heading
            elif not s:
                flush()
            else:
                buf.append(s)
        flush()

    # -- recuperación (contrato de Retriever del pipeline) ----------------
    def retrieve(self, query: str, namespaces: list[str], k: int) -> list[ScoredChunk]:
        q = _tokens(query)
        if not q:
            return []
        qset = set(q)
        out: list[ScoredChunk] = []
        for c in self._chunks:
            if namespaces and not any(_ns_match(c.namespace, ns) for ns in namespaces):
                continue
            hits = sum(c.tf.get(tok, 0) for tok in qset)
            if hits == 0:
                continue
            # score normalizado por longitud del chunk (evita premiar textos largos)
            score = min(1.0, hits / (1.0 + len(c.tf) ** 0.5))
            out.append(ScoredChunk(
                chunk_id=c.chunk_id, text=c.text, score=round(score, 3),
                namespace=c.namespace,
                provenance=Provenance(source=f"{c.source} · «{c.heading}»",
                                      hash=c.hash,
                                      knowledge_version=f"wiki:{self._snapshot_id}")))
        out.sort(key=lambda x: x.score, reverse=True)
        return out[:k]

    def namespaces(self) -> list[str]:
        return sorted(self._namespaces)

    def documents(self) -> list[dict]:
        """Los chunks ya troceados, para que otro índice los reutilice.

        El troceado (y su hash) es trabajo del store; el retriever denso no
        debe volver a leer la wiki ni trocearla distinto — si lo hiciera, un
        mismo pasaje tendría dos `chunk_id` y la deduplicación del pipeline
        dejaría de funcionar.
        """
        return [
            {"chunk_id": c.chunk_id, "namespace": c.namespace, "text": c.text,
             "source": c.source, "heading": c.heading, "hash": c.hash}
            for c in self._chunks
        ]


def _ns_match(chunk_ns: str, wanted: str) -> bool:
    w = wanted.rstrip("/")
    return chunk_ns == w or chunk_ns.startswith(w) or w.startswith(chunk_ns)
