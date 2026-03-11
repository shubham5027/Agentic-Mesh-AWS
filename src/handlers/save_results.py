"""
Save Results Lambda
Final step in the Step Functions state machine.
Persists task results to DynamoDB and updates the OpenSearch vector cache.
"""

import os
import json
import time
from decimal import Decimal
from datetime import datetime, timezone
import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="agentic-mesh")
tracer = Tracer(service="agentic-mesh")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.models.bedrock_client import get_embedding
from src.models.vector_memory import store_task_result
from src.observability.metrics import put_task_complete_metrics

dynamodb = boto3.resource("dynamodb")
TASK_TABLE_NAME = os.environ.get("TASK_TABLE_NAME", "AgenticMeshTaskResults")


def _to_decimal(obj):
    """Convert floats to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_decimal(i) for i in obj]
    return obj


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Save task results to DynamoDB and update the vector cache.

    Input (full state from Step Functions):
        {
            "task_id": str,
            "task": str,
            "guardrail_result": {...},
            "broker_result": {...},
            "worker_result": {...},
            "verification_result": {...} (optional),
            "correction_result": {...} (optional),
            "final_result": {
                "status": str,
                "answer": str,
                "agent": str,
                "quality_score": float,
                "cost_estimate": float,
                "escalated": bool
            }
        }

    Output:
        {"saved": bool, "task_id": str, "status": str}
    """
    task_id = event.get("task_id", "unknown")
    task_text = event.get("task", "")
    final_result = event.get("final_result", {})
    broker_result = event.get("broker_result", {})
    worker_result = event.get("worker_result", {})
    verification_result = event.get("verification_result", {})
    correction_result = event.get("correction_result", {})

    status = final_result.get("status", "UNKNOWN")
    answer = final_result.get("answer", "")
    agent = final_result.get("agent", "unknown")
    quality_score = final_result.get("quality_score", 0)
    cost_estimate = final_result.get("cost_estimate", 0)
    escalated = final_result.get("escalated", False)

    logger.info(
        "Saving task results",
        extra={
            "task_id": task_id,
            "status": status,
            "agent": agent,
            "escalated": escalated,
        },
    )

    # ── Step 1: Save to DynamoDB ─────────────────────────────────
    try:
        table = dynamodb.Table(TASK_TABLE_NAME)

        # Calculate total cost (worker + verification + correction)
        total_cost = cost_estimate
        verification_cost = verification_result.get("verification_cost", 0)
        correction_cost = correction_result.get("cost_estimate", 0) if correction_result else 0
        total_cost = cost_estimate + verification_cost + correction_cost

        item = {
            "task_id": task_id,
            "task": task_text,
            "status": status,
            "answer": answer,
            "agent": agent,
            "model": worker_result.get("model", "unknown"),
            "quality_score": quality_score,
            "cost_estimate": total_cost,
            "escalated": escalated,
            "cache_hit": broker_result.get("cache_hit", False),
            "predicted_complexity": broker_result.get("predicted_complexity", "unknown"),
            "worker_latency_ms": worker_result.get("latency_ms", 0),
            "verification_score": quality_score,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "ttl": int(time.time()) + (30 * 24 * 60 * 60),  # 30 days TTL
        }

        if escalated and correction_result:
            item["correction_model"] = correction_result.get("model", "unknown")
            item["correction_cost"] = correction_cost
            item["correction_feedback"] = correction_result.get("correction_context", {}).get("feedback_used", "")

        # Convert floats for DynamoDB
        item = _to_decimal(item)

        table.put_item(Item=item)
        logger.info("Results saved to DynamoDB", extra={"task_id": task_id})

    except Exception as e:
        logger.error("Failed to save to DynamoDB", extra={"error": str(e), "task_id": task_id})

    # ── Step 2: Update Vector Cache ──────────────────────────────
    if status in ("SUCCESS", "CORRECTED") and quality_score >= 7.0:
        try:
            task_embedding = get_embedding(task_text)
            store_task_result(
                task_embedding=task_embedding,
                task_text=task_text,
                agent_used=agent,
                model_used=worker_result.get("model", "unknown"),
                cost=total_cost,
                quality_score=quality_score,
                answer=answer,
                complexity=broker_result.get("predicted_complexity", "medium"),
            )
            logger.info("Vector cache updated", extra={"task_id": task_id})
        except Exception as e:
            logger.warning("Failed to update vector cache", extra={"error": str(e)})

    # ── Step 3: Publish Final Metrics ────────────────────────────
    try:
        put_task_complete_metrics(
            task_id=task_id,
            agent=agent,
            cost=total_cost,
            quality_score=quality_score,
            latency_ms=worker_result.get("latency_ms", 0),
            cache_hit=broker_result.get("cache_hit", False),
            escalated=escalated,
        )
    except Exception as e:
        logger.warning("Failed to publish final metrics", extra={"error": str(e)})

    return {
        "saved": True,
        "task_id": task_id,
        "status": status,
        "total_cost": float(total_cost) if not isinstance(total_cost, float) else total_cost,
    }
