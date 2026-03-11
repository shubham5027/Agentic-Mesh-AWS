"""
Cost Tracker Module
Calculates per-model token costs and publishes cost metrics to CloudWatch.
Implements autonomous cost management for the Agentic Mesh.
"""

import boto3
from datetime import datetime, timezone
from aws_lambda_powertools import Logger

logger = Logger(service="agentic-mesh", child=True)

cloudwatch = boto3.client("cloudwatch")

# ── Per-Model Pricing (USD per 1K tokens) ────────────────────────────
# Updated 2026-03-10: Added Sonnet 4.5 & Haiku 4.5 pricing
MODEL_PRICING = {
    # Meta Llama 3 8B Instruct
    "meta.llama3-8b-instruct-v1:0": {
        "input_per_1k": 0.0003,
        "output_per_1k": 0.0006,
        "tier": "cheap",
    },
    # Anthropic Claude Haiku 4.5 (inference profile)
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {
        "input_per_1k": 0.0008,
        "output_per_1k": 0.004,
        "tier": "cheap",
    },
    # Anthropic Claude Sonnet 4.5 (inference profile)
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
        "tier": "elite",
    },
    # Anthropic Claude Sonnet 4 (legacy, kept for cost lookups on old invocations)
    "us.anthropic.claude-sonnet-4-20250514-v1:0": {
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
        "tier": "elite",
    },
    # Amazon Titan Embeddings V2
    "amazon.titan-embed-text-v2:0": {
        "input_per_1k": 0.0002,
        "output_per_1k": 0.0,
        "tier": "utility",
    },
}

# Namespace for all custom CloudWatch metrics
METRIC_NAMESPACE = "AgenticMesh"


def calculate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate the USD cost for a model invocation.

    Args:
        model_id: Full Bedrock model ID.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.

    Returns:
        Estimated cost in USD.
    """
    pricing = MODEL_PRICING.get(model_id)
    if not pricing:
        logger.warning(f"Unknown model pricing for {model_id}, using zero cost")
        return 0.0

    cost = (input_tokens / 1000) * pricing["input_per_1k"] + \
           (output_tokens / 1000) * pricing["output_per_1k"]

    logger.info(
        "Cost calculated",
        extra={
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "tier": pricing["tier"],
        },
    )
    return round(cost, 8)


def get_model_tier(model_id: str) -> str:
    """Return the pricing tier for a model."""
    pricing = MODEL_PRICING.get(model_id, {})
    return pricing.get("tier", "unknown")


def publish_cost_metrics(
    agent: str,
    cost: float,
    input_tokens: int,
    output_tokens: int,
    cache_hit: bool = False,
    cost_saving: float = 0.0,
) -> None:
    """
    Publish cost-related metrics to CloudWatch.

    Args:
        agent: Agent name (coder, researcher, summarizer, broker, verification).
        cost: Total cost in USD for this invocation.
        input_tokens: Input token count.
        output_tokens: Output token count.
        cache_hit: Whether this was served from the vector cache.
        cost_saving: Estimated cost saving from cache hit.
    """
    timestamp = datetime.now(timezone.utc)

    metric_data = [
        {
            "MetricName": "CostPerTask",
            "Dimensions": [{"Name": "Agent", "Value": agent}],
            "Timestamp": timestamp,
            "Value": cost,
            "Unit": "None",
        },
        {
            "MetricName": "TokensUsed",
            "Dimensions": [{"Name": "Agent", "Value": agent}],
            "Timestamp": timestamp,
            "Value": input_tokens + output_tokens,
            "Unit": "Count",
        },
    ]

    if cache_hit:
        metric_data.extend([
            {
                "MetricName": "CacheHitRate",
                "Timestamp": timestamp,
                "Value": 1.0,
                "Unit": "None",
            },
            {
                "MetricName": "CostSavingFromCache",
                "Timestamp": timestamp,
                "Value": cost_saving,
                "Unit": "None",
            },
        ])
    else:
        metric_data.append({
            "MetricName": "CacheHitRate",
            "Timestamp": timestamp,
            "Value": 0.0,
            "Unit": "None",
        })

    try:
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=metric_data,
        )
        logger.info(
            "Cost metrics published",
            extra={"agent": agent, "cost": cost, "cache_hit": cache_hit},
        )
    except Exception as e:
        logger.error("Failed to publish cost metrics", extra={"error": str(e)})


def publish_routing_metric(agent: str, cache_hit: bool, complexity: str) -> None:
    """Publish routing decision metrics to CloudWatch."""
    timestamp = datetime.now(timezone.utc)

    try:
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "RoutingDecision",
                    "Dimensions": [
                        {"Name": "Agent", "Value": agent},
                        {"Name": "CacheHit", "Value": str(cache_hit)},
                    ],
                    "Timestamp": timestamp,
                    "Value": 1.0,
                    "Unit": "Count",
                },
            ],
        )
    except Exception as e:
        logger.error("Failed to publish routing metric", extra={"error": str(e)})


def publish_quality_metric(agent: str, quality_score: float, passed: bool) -> None:
    """Publish quality/verification metrics to CloudWatch."""
    timestamp = datetime.now(timezone.utc)

    try:
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "QualityScore",
                    "Dimensions": [{"Name": "Agent", "Value": agent}],
                    "Timestamp": timestamp,
                    "Value": quality_score,
                    "Unit": "None",
                },
                {
                    "MetricName": "VerificationPassRate",
                    "Timestamp": timestamp,
                    "Value": 1.0 if passed else 0.0,
                    "Unit": "None",
                },
            ],
        )
    except Exception as e:
        logger.error("Failed to publish quality metric", extra={"error": str(e)})


def publish_escalation_metric(escalated: bool) -> None:
    """Publish escalation rate metric to CloudWatch."""
    timestamp = datetime.now(timezone.utc)

    try:
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "EscalationRate",
                    "Timestamp": timestamp,
                    "Value": 1.0 if escalated else 0.0,
                    "Unit": "None",
                },
            ],
        )
    except Exception as e:
        logger.error("Failed to publish escalation metric", extra={"error": str(e)})


def publish_latency_metric(agent: str, latency_ms: int) -> None:
    """Publish task latency metric to CloudWatch."""
    timestamp = datetime.now(timezone.utc)

    try:
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "TaskLatency",
                    "Dimensions": [{"Name": "Agent", "Value": agent}],
                    "Timestamp": timestamp,
                    "Value": float(latency_ms),
                    "Unit": "Milliseconds",
                },
            ],
        )
    except Exception as e:
        logger.error("Failed to publish latency metric", extra={"error": str(e)})
