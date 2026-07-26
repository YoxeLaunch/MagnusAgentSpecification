"""Vector store en memoria que implementa `DenseRetriever` (paso 4.2).

Recupera por coseno sobre los vectores que produce el `Embedder` enchufado.
Con el embedder por defecto ([`HashingEmbedder`](embedder.py)) esos vectores
son **random indexing con pesos TF-IDF, no embeddings neuronales**: el store es
agnóstico y funcionaría igual con bge-m3, pero hoy no hay ningún modelo
neuronal en el repositorio y conviene no dar a entender lo contrario.

Filtra por los namespaces del agente ANTES de puntuar — la parcela de
conocimiento es un límite duro, no un criterio de ordenación.

Es deliberadamente el backend más simple que cumple el contrato: 1162 chunks
en la wiki real caben de sobra en memoria y una consulta cuesta milisegundos.
Cambiarlo por Qdrant es sustituir esta clase, no tocar el pipeline.
"""
from __future__ import annotations

from kernel.rag.embedder import HashingEmbedder, coseno
from kernel.rag.file_store import FileWikiStore, _ns_match
from kernel.rag.pipeline import Provenance, ScoredChunk


class InMemoryVectorStore:
    def __init__(self, embedder: HashingEmbedder | None = None):
        self.embedder = embedder or HashingEmbedder()
        self._docs: list[dict] = []
        self._vectores: list[list[float]] = []
        self._knowledge_version: str = "desconocida"

    # -- construcción --------------------------------------------------------
    def index(self, documents: list[dict], *, knowledge_version: str = "desconocida") -> dict:
        """Indexa los chunks ya troceados por el store léxico."""
        self._docs = list(documents)
        self._knowledge_version = knowledge_version
        textos = [f"{d['heading']} {d['text']}" for d in self._docs]
        if not self.embedder.ajustado:
            self.embedder.fit(textos)
        self._vectores = self.embedder.embed(textos)
        return {"chunks": len(self._docs), "dim": self.embedder.dim,
                "embedder": self.embedder.name}

    @classmethod
    def from_wiki_store(cls, store: FileWikiStore,
                        embedder: HashingEmbedder | None = None) -> "InMemoryVectorStore":
        """Se construye sobre los MISMOS chunks del store léxico.

        Comparten troceado y por tanto `chunk_id`, que es lo que permite al
        pipeline deduplicar y fusionar los dos rankings.
        """
        vs = cls(embedder)
        vs.index(store.documents(), knowledge_version=f"wiki:{store.snapshot_id}")
        return vs

    # -- contrato DenseRetriever ---------------------------------------------
    def retrieve(self, query: str, namespaces: list[str], k: int) -> list[ScoredChunk]:
        if not self._docs:
            return []
        q = self.embedder.embed([query])[0]
        if not any(q):
            return []

        out: list[ScoredChunk] = []
        for doc, vec in zip(self._docs, self._vectores):
            if namespaces and not any(_ns_match(doc["namespace"], ns) for ns in namespaces):
                continue
            score = coseno(q, vec)
            if score <= 0.0:
                continue
            out.append(ScoredChunk(
                chunk_id=doc["chunk_id"], text=doc["text"], score=round(score, 3),
                namespace=doc["namespace"],
                provenance=Provenance(source=f"{doc['source']} · «{doc['heading']}»",
                                      hash=doc["hash"],
                                      knowledge_version=self._knowledge_version)))
        out.sort(key=lambda c: c.score, reverse=True)
        return out[:k]

    def __len__(self) -> int:
        return len(self._docs)
