"""Lazy local Qwen3 embeddings for policy retrieval and offline evaluation."""

from functools import lru_cache
from pathlib import Path
from typing import Sequence


@lru_cache(maxsize=2)
def _load_sentence_transformer(model_path: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(str(Path(model_path)), local_files_only=True)


class LocalPolicyEmbeddings:
    def __init__(self, model_path: str):
        if not model_path or not Path(model_path).is_dir():
            raise ValueError("The configured local embedding model directory does not exist.")
        self.model = _load_sentence_transformer(str(Path(model_path).resolve()))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self.model.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True)
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.encode(
            [text], prompt_name="query", normalize_embeddings=True, convert_to_numpy=True,
        )[0]
        return vector.tolist()
