# ADR-0008: Hashed TF-IDF embeddings behind a protocol

**Status:** accepted.

The system must run with no key and no model download, in CI and in `docker compose up`. So the default embedder hashes unigrams and bigrams with blake2b into 1024 buckets, weights by TF-IDF, and stores vectors in pgvector. Search is an exact scan, because the corpus is 19 chunks and an index would add nothing.

**This is lexical matching.** It has no semantic understanding, and the paraphrase subset of the retrieval eval shows the cost plainly. A real model plugs in behind the `Embedder` protocol with a config change and a re-ingest.
