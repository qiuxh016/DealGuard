"""Lazy local Qwen3 embeddings for policy retrieval and offline evaluation."""

from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Sequence

_MAX_CACHED_DOCUMENT_VECTORS = 1024
_DOCUMENT_VECTOR_CACHE: OrderedDict[tuple[str, str], tuple[float, ...]] = OrderedDict()
_DOCUMENT_VECTOR_CACHE_LOCK = RLock()


@lru_cache(maxsize=2)
def _load_sentence_transformer(model_path: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(str(Path(model_path)), local_files_only=True)


class LocalPolicyEmbeddings:
    def __init__(self, model_path: str):
        if not model_path or not Path(model_path).is_dir():
            raise ValueError("The configured local embedding model directory does not exist.")
        self.model_path = str(Path(model_path).resolve())
        self.model = _load_sentence_transformer(self.model_path)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        keys = [(self.model_path, text) for text in texts]
        cached: dict[str, tuple[float, ...]] = {}
        missing: list[str] = []
        with _DOCUMENT_VECTOR_CACHE_LOCK:
            for key, text in zip(keys, texts, strict=True):
                vector = _DOCUMENT_VECTOR_CACHE.get(key)
                if vector is None:
                    if text not in missing:
                        missing.append(text)
                else:
                    _DOCUMENT_VECTOR_CACHE.move_to_end(key)
                    cached[text] = vector

        if missing:
            vectors = self.model.encode(missing, normalize_embeddings=True, convert_to_numpy=True)
            with _DOCUMENT_VECTOR_CACHE_LOCK:
                for text, vector in zip(missing, vectors, strict=True):
                    value = tuple(float(component) for component in vector)
                    key = (self.model_path, text)
                    _DOCUMENT_VECTOR_CACHE[key] = value
                    _DOCUMENT_VECTOR_CACHE.move_to_end(key)
                    cached[text] = value
                while len(_DOCUMENT_VECTOR_CACHE) > _MAX_CACHED_DOCUMENT_VECTORS:
                    _DOCUMENT_VECTOR_CACHE.popitem(last=False)

        return [list(cached[text]) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.encode(
            [text], prompt_name="query", normalize_embeddings=True, convert_to_numpy=True,
        )[0]
        return vector.tolist()
