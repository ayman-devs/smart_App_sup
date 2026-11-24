"""Qdrant: where vectors are stored and searched.

Two collections:
- doc_text   : one point per text chunk   (payload: text, page, source)
- doc_images : one point per image        (payload: file, page, source)
"""

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from .config import settings


def make_client(url: str | None = None) -> QdrantClient:
    url = url or settings.qdrant_url
    if url == ":memory:":          # used by the tests, no Docker needed
        return QdrantClient(location=":memory:")
    return QdrantClient(url=url, timeout=30)


def point_id(key: str) -> str:
    """Qdrant ids must be ints or UUIDs: build a stable UUID from our key."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def recreate_collection(client: QdrantClient, name: str, dim: int) -> None:
    """Delete then create a collection (ingestion starts from a clean state)."""
    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(
        name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
    )


def upsert(client: QdrantClient, name: str, keys: list[str],
           vectors: list[list[float]], payloads: list[dict]) -> None:
    points = [PointStruct(id=point_id(k), vector=v, payload=p)
              for k, v, p in zip(keys, vectors, payloads)]
    for i in range(0, len(points), 128):          # send in batches
        client.upsert(name, points=points[i:i + 128])


def search(client: QdrantClient, name: str, vector: list[float],
           limit: int) -> list[tuple[dict, float]]:
    """Nearest neighbours: list of (payload, similarity score)."""
    if not client.collection_exists(name):
        return []
    hits = client.query_points(name, query=vector, limit=limit,
                               with_payload=True).points
    return [(h.payload, h.score) for h in hits]


def all_payloads(client: QdrantClient, name: str) -> list[dict]:
    """Read every payload of a collection (used to build the BM25 index)."""
    if not client.collection_exists(name):
        return []
    payloads, offset = [], None
    while True:
        points, offset = client.scroll(name, limit=256, offset=offset,
                                       with_payload=True, with_vectors=False)
        payloads.extend(p.payload for p in points)
        if offset is None:
            return payloads


def count(client: QdrantClient, name: str) -> int:
    if not client.collection_exists(name):
        return 0
    return client.count(name).count
