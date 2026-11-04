from app.rag import embeddings


def test_document_embeddings_are_reused_and_refresh_when_policy_text_changes(tmp_path, monkeypatch):
    class FakeModel:
        def __init__(self):
            self.batches = []

        def encode(self, texts, **_kwargs):
            self.batches.append(list(texts))
            return [[float(len(text)), float(sum(map(ord, text)))] for text in texts]

    model = FakeModel()
    monkeypatch.setattr(embeddings, "_load_sentence_transformer", lambda _path: model)
    monkeypatch.setattr(embeddings, "_MAX_CACHED_DOCUMENT_VECTORS", 2)
    with embeddings._DOCUMENT_VECTOR_CACHE_LOCK:
        embeddings._DOCUMENT_VECTOR_CACHE.clear()

    try:
        service = embeddings.LocalPolicyEmbeddings(str(tmp_path))
        original = service.embed_documents(["discount up to five percent", "bulk order terms"])
        repeated = service.embed_documents(["discount up to five percent", "bulk order terms"])
        revised = service.embed_documents(["discount up to six percent", "bulk order terms"])

        assert original == repeated
        assert revised[1] == original[1]
        assert revised[0] != original[0]
        assert len(embeddings._DOCUMENT_VECTOR_CACHE) <= 2
        assert model.batches == [
            ["discount up to five percent", "bulk order terms"],
            ["discount up to six percent"],
        ]
    finally:
        with embeddings._DOCUMENT_VECTOR_CACHE_LOCK:
            embeddings._DOCUMENT_VECTOR_CACHE.clear()
