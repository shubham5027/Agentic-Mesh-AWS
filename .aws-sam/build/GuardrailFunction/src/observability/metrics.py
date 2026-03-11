"""
CloudWatch Metrics Helper
Provides pre-defined metric publishing functions for the Agentic Mesh observability layer.
"""

import boto3
from datetime import datetime, timezone
from aws_lambda_powertools import Logger

logger = Logger(service="agentic-mesh", child=True)

cloudwatch = boto3.client("cloudwatch")

METRIC_NAMESPACE = "AgenticMesh"


def put_metric(
    name: str,
    value: float,
    unit: str = "None",
    dimensions: dict | None = None,
) -> None:
    """
    Publish a single custom metric to CloudWatch.

    Args:
        name: Metric name.
        value: Metric value.
        unit: CloudWatch unit (None, Count, Milliseconds, etc.).
        dimensions: Optional dict of dimension name→value pairs.
    """
    metric_data = {
        "MetricName": name,
        "Timestamp": datetime.now(timezone.utc),
        "Value": value,
        "Unit": unit,
    }

    if dimensions:
        metric_data["Dimensions"] = [
            {"Name": k, "Value": v} for k, v in dimensions.items()
        ]

    try:
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[metric_data],
        )
    except Exception as e:
        logger.error(
            "Failed to publish metric",
            extra={"metric_name": name, "error": str(e)},
        )


def put_task_complete_metrics(
    task_id: str,
    agent: str,
    cost: float,
    quality_score: float,
    latency_ms: int,
    cache_hit: bool,
    escalated: bool,
) -> None:
    """
    Publish all end-of-task metrics in a single batch.

    Args:
        task_id: Unique task identifier.
        agent: Agent that handled the task.
        cost: Total cost in USD.
        quality_score: Verification quality score (0-10).
        latency_ms: Total task processing time in ms.
        cache_hit: Whether the task was served from cache.
        escalated: Whether self-correction was triggered.
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
            "MetricName": "QualityScore",
            "Dimensions": [{"Name": "Agent", "Value": agent}],
            "Timestamp": timestamp,
            "Value": quality_score,
            "Unit": "None",
        },
        {
            "MetricName": "TaskLatency",
            "Dimensions": [{"Name": "Agent", "Value": agent}],
            "Timestamp": timestamp,
            "Value": float(latency_ms),
            "Unit": "Milliseconds",
        },
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
        {
            "MetricName": "EscalationRate",
            "Timestamp": timestamp,
            "Value": 1.0 if escalated else 0.0,
            "Unit": "None",
        },
        {
            "MetricName": "CacheHitRate",
            "Timestamp": timestamp,
            "Value": 1.0 if cache_hit else 0.0,
            "Unit": "None",
        },
    ]

    try:
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=metric_data,
        )
        logger.info(
            "Task complete metrics published",
            extra={
                "task_id": task_id,
                "agent": agent,
                "cost": cost,
                "quality_score": quality_score,
            },
        )
    except Exception as e:
        logger.error(
            "Failed to publish task complete metrics",
            extra={"task_id": task_id, "error": str(e)},
        )
