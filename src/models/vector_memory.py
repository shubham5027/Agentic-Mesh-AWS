"""
Vector Memory Module — Task-Success Cache
Uses Amazon OpenSearch Serverless with k-NN to store and retrieve
similar past tasks, enabling intelligent cost-optimized routing.
"""

import os
import json
import hashlib
from datetime import datetime, timezone
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
import boto3
from aws_lambda_powertools import Logger

logger = Logger(service="agentic-mesh", child=True)

# ── OpenSearch Configuration ─────────────────────────────────────────
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "")
INDEX_NAME = os.environ.get("OPENSEARCH_INDEX", "task-success-cache")
EMBEDDING_DIMENSIONS = 1024
SIMILARITY_THRESHOLD = 0.85

# ── AWS Auth for OpenSearch Serverless ───────────────────────────────
_os_client = None


def _get_opensearch_client() -> OpenSearch:
    """Lazy-initialize the OpenSearch client with SigV4 auth."""
    global _os_client
    if _os_client is not None:
        return _os_client

    credentials = boto3.Session().get_credentials()
    region = os.environ.get("AWS_REGION", "us-east-1")

    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        "aoss",
        session_token=credentials.token,
    )

    # Strip protocol from endpoint if present
    host = OPENSEARCH_ENDPOINT.replace("https://", "").replace("http://", "")

    _os_client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )

    _ensure_index_exists(_os_client)
    return _os_client


def _ensure_index_exists(client: OpenSearch) -> None:
    """Create the k-NN index if it doesn't already exist."""
    try:
        if client.indices.exists(index=INDEX_NAME):
            logger.info(f"Index '{INDEX_NAME}' already exists")
            return

        index_body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 100,
                }
            },
            "mappings": {
                "properties": {
                    "task_embedding": {
                        "type": "knn_vector",
                        "dimension": EMBEDDING_DIMENSIONS,
                        "method": {
                            "engine": "nmslib",
                            "space_type": "cosinesimil",
                            "name": "hnsw",
                            "parameters": {
                                "ef_construction": 256,
                                "m": 48,
                            },
                        },
                    },
                    "task_text": {"type": "text"},
                    "task_hash": {"type": "keyword"},
                    "agent_used": {"type": "keyword"},
                    "model_used": {"type": "keyword"},
                    "cost": {"type": "float"},
                    "quality_score": {"type": "float"},
                    "answer": {"type": "text"},
                    "timestamp": {"type": "date"},
                    "complexity": {"type": "keyword"},
                }
            },
        }

        client.indices.create(index=INDEX_NAME, body=index_body)
        logger.info(f"Created k-NN index '{INDEX_NAME}'")

    except Exception as e:
        logger.warning(f"Index creation check failed: {e}")


def search_similar_tasks(
    embedding: list[float],
    k: int = 3,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[dict]:
    """
    Search for similar past tasks using k-NN vector similarity.

    Args:
        embedding: Query embedding vector (1024-d).
        k: Number of nearest neighbors to retrieve.
        threshold: Minimum cosine similarity score to consider a match.

    Returns:
        List of matching task documents sorted by similarity score.
        Each doc includes: task_text, agent_used, cost, quality_score, answer, score.
    """
    client = _get_opensearch_client()

    query = {
        "size": k,
        "query": {
            "knn": {
                "task_embedding": {
                    "vector": embedding,
                    "k": k,
                }
            }
        },
        "_source": [
            "task_text",
            "agent_used",
            "model_used",
            "cost",
            "quality_score",
            "answer",
            "complexity",
            "timestamp",
        ],
    }

    try:
        response = client.search(index=INDEX_NAME, body=query)

        results = []
        for hit in response["hits"]["hits"]:
            score = hit["_score"]
            if score >= threshold:
                doc = hit["_source"]
                doc["score"] = score
                results.append(doc)

        logger.info(
            "Vector search complete",
            extra={
                "total_hits": len(response["hits"]["hits"]),
                "above_threshold": len(results),
                "threshold": threshold,
            },
        )
        return results

    except Exception as e:
        logger.error("Vector search failed", extra={"error": str(e)})
        return []


def store_task_result(
    task_embedding: list[float],
    task_text: str,
    agent_used: str,
    model_used: str,
    cost: float,
    quality_score: float,
    answer: str,
    complexity: str = "medium",
) -> bool:
    """
    Store a completed task result in the vector cache.

    Args:
        task_embedding: Embedding vector for the task.
        task_text: Original task text.
        agent_used: Agent that handled the task.
        model_used: Model ID used.
        cost: Cost in USD.
        quality_score: Quality score (0-10).
        answer: The generated answer.
        complexity: Task complexity (low/medium/high).

    Returns:
        True if stored successfully, False otherwise.
    """
    client = _get_opensearch_client()

    # Generate a deterministic ID based on task content
    task_hash = hashlib.sha256(task_text.encode()).hexdigest()[:16]

    document = {
        "task_embedding": task_embedding,
        "task_text": task_text,
        "task_hash": task_hash,
        "agent_used": agent_used,
        "model_used": model_used,
        "cost": cost,
        "quality_score": quality_score,
        "answer": answer,
        "complexity": complexity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        client.index(
            index=INDEX_NAME,
            body=document,
            id=task_hash,
            refresh=True,
        )

        logger.info(
            "Task result stored in vector cache",
            extra={
                "task_hash": task_hash,
                "agent_used": agent_used,
                "quality_score": quality_score,
            },
        )
        return True

    except Exception as e:
        logger.error("Failed to store task result", extra={"error": str(e)})
        return False


def get_cache_stats() -> dict:
    """Return basic statistics about the task-success cache."""
    client = _get_opensearch_client()

    try:
        count = client.count(index=INDEX_NAME)
        return {
            "total_cached_tasks": count["count"],
            "index_name": INDEX_NAME,
        }
    except Exception as e:
        logger.error("Failed to get cache stats", extra={"error": str(e)})
        return {"total_cached_tasks": 0, "index_name": INDEX_NAME}
