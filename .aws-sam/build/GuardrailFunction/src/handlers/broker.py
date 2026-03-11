"""
Broker Agent Lambda
The central supervisor that receives tasks and routes them to specialized workers
based on vector-cache lookup and LLM-predicted task complexity/type.
"""

import json
import time
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="agentic-mesh")
tracer = Tracer(service="agentic-mesh")

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.models.bedrock_client import invoke_model, get_embedding
from src.models.vector_memory import search_similar_tasks
from src.models.cost_tracker import publish_routing_metric, publish_cost_metrics

# ── Routing Configuration ────────────────────────────────────────────
CACHE_SIMILARITY_THRESHOLD = 0.85
MIN_CACHE_QUALITY_SCORE = 7.0

BROKER_SYSTEM_PROMPT = """You are a task routing agent. Your job is to analyze a user's task and determine:
1. The TASK TYPE: one of "coding", "research", or "summarize"
2. The COMPLEXITY: one of "low", "medium", or "high"

Rules:
- "coding" tasks involve writing code, debugging, explaining code, algorithms, or technical implementation
- "research" tasks involve analysis, comparison, pros/cons, explaining concepts, or gathering information
- "summarize" tasks involve condensing, summarizing, extracting key points, or reformatting text

Respond ONLY with valid JSON in this exact format:
{"task_type": "coding|research|summarize", "complexity": "low|medium|high", "reasoning": "brief explanation"}
"""


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Broker Agent: Route tasks to the optimal worker agent.

    Input:
        {
            "task_id": str,
            "task": str (sanitized),
            "original_task": str,
            "type_hint": str
        }

    Output:
        {
            "route": "coder|researcher|summarizer",
            "cache_hit": bool,
            "predicted_complexity": str,
            "cached_answer": str|null
        }
    """
    task_id = event.get("task_id", "unknown")
    task_text = event.get("task", "")
    type_hint = event.get("type_hint", "auto")
    start_time = time.time()

    logger.info(
        "Broker processing task",
        extra={"task_id": task_id, "type_hint": type_hint},
    )

    # ── Step 1: Generate task embedding ──────────────────────────
    try:
        task_embedding = get_embedding(task_text)
    except Exception as e:
        logger.warning("Embedding generation failed, skipping cache", extra={"error": str(e)})
        task_embedding = None

    # ── Step 2: Search vector cache ──────────────────────────────
    cached_answer = None
    cache_hit = False
    cached_agent = None

    if task_embedding:
        try:
            similar_tasks = search_similar_tasks(
                task_embedding,
                k=3,
                threshold=CACHE_SIMILARITY_THRESHOLD,
            )

            if similar_tasks:
                best_match = similar_tasks[0]
                if best_match.get("quality_score", 0) >= MIN_CACHE_QUALITY_SCORE:
                    cache_hit = True
                    cached_answer = best_match.get("answer")
                    cached_agent = best_match.get("agent_used", "researcher")

                    logger.info(
                        "Cache hit found",
                        extra={
                            "task_id": task_id,
                            "similarity_score": best_match.get("score"),
                            "cached_agent": cached_agent,
                            "quality_score": best_match.get("quality_score"),
                        },
                    )
        except Exception as e:
            logger.warning("Vector cache search failed", extra={"error": str(e)})

    # ── Step 3: Predict task type & complexity ───────────────────
    if cache_hit and cached_agent:
        route = cached_agent
        predicted_complexity = "cached"
    elif type_hint != "auto" and type_hint in ("coding", "research", "summarize"):
        route = _type_hint_to_route(type_hint)
        predicted_complexity = "medium"  # Default when hint is provided
    else:
        route, predicted_complexity = _predict_route(task_text)

    # ── Step 4: Publish metrics ──────────────────────────────────
    elapsed_ms = int((time.time() - start_time) * 1000)
    publish_routing_metric(route, cache_hit, predicted_complexity)

    result = {
        "route": route,
        "cache_hit": cache_hit,
        "predicted_complexity": predicted_complexity,
        "cached_answer": cached_answer,
        "broker_latency_ms": elapsed_ms,
    }

    logger.info(
        "Broker routing decision",
        extra={
            "task_id": task_id,
            "route": route,
            "cache_hit": cache_hit,
            "complexity": predicted_complexity,
            "latency_ms": elapsed_ms,
        },
    )

    return result


def _predict_route(task_text: str) -> tuple[str, str]:
    """
    Use Llama 3 8B to predict task type and complexity.

    Returns:
        (route, complexity) tuple.
    """
    try:
        response = invoke_model(
            model_id="llama3-8b",
            messages=[{"role": "user", "content": task_text}],
            system_prompt=BROKER_SYSTEM_PROMPT,
            max_tokens=256,
            temperature=0.1,
        )

        content = response["content"].strip()

        # Parse JSON response
        # Handle potential markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        prediction = json.loads(content)
        task_type = prediction.get("task_type", "research")
        complexity = prediction.get("complexity", "medium")

        route = _type_hint_to_route(task_type)

        logger.info(
            "LLM route prediction",
            extra={
                "task_type": task_type,
                "complexity": complexity,
                "reasoning": prediction.get("reasoning", ""),
            },
        )

        return route, complexity

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(
            "Failed to parse LLM routing prediction, defaulting to researcher",
            extra={"error": str(e)},
        )
        return "researcher", "medium"

    except Exception as e:
        logger.error(
            "LLM routing prediction failed, defaulting to researcher",
            extra={"error": str(e)},
        )
        return "researcher", "medium"


def _type_hint_to_route(type_hint: str) -> str:
    """Map a task type to a worker route."""
    mapping = {
        "coding": "coder",
        "code": "coder",
        "research": "researcher",
        "analysis": "researcher",
        "summarize": "summarizer",
        "summary": "summarizer",
    }
    return mapping.get(type_hint.lower(), "researcher")
