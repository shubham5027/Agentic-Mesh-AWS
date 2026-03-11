"""
Worker Summarizer Agent Lambda
Specialized agent for summarization tasks — uses Claude 3 Haiku for
fast, cost-effective text condensation and extraction.
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

from src.models.bedrock_client import invoke_model
from src.models.cost_tracker import calculate_cost, publish_cost_metrics, publish_latency_metric

SUMMARIZER_SYSTEM_PROMPT = """You are an expert text summarizer and information distiller. You excel at:
- Condensing long documents into clear, concise summaries
- Extracting key points and main ideas
- Creating bullet-point summaries and executive briefs
- Reformatting and restructuring text for clarity
- Identifying the most important information in a body of text

Guidelines:
1. Preserve the original meaning and nuance
2. Use clear, simple language
3. Structure with bullet points or numbered lists when appropriate
4. Highlight key takeaways at the beginning
5. Keep summaries to 20-30% of the original length unless specified

Be concise. Every sentence must add value."""

# Summarizer always uses cheap models — that's the point
COMPLEXITY_MODELS = {
    "low": "claude-haiku",
    "medium": "claude-haiku",
    "high": "claude-sonnet",  # Only upgrade for very long/complex text
}


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Execute a summarization task.

    Input:
        {
            "task_id": str,
            "task": str,
            "complexity": str ("low"|"medium"|"high")
        }

    Output:
        {
            "agent": "summarizer",
            "answer": str,
            "model": str,
            "latency_ms": int,
            "token_usage": {"input": int, "output": int},
            "cost_estimate": float
        }
    """
    task_id = event.get("task_id", "unknown")
    task_text = event.get("task", "")
    complexity = event.get("complexity", "medium")
    start_time = time.time()

    model_alias = COMPLEXITY_MODELS.get(complexity, "claude-haiku")

    logger.info(
        "Summarizer agent processing task",
        extra={
            "task_id": task_id,
            "complexity": complexity,
            "model": model_alias,
        },
    )

    response = invoke_model(
        model_id=model_alias,
        messages=[{"role": "user", "content": task_text}],
        system_prompt=SUMMARIZER_SYSTEM_PROMPT,
        max_tokens=2048,
        temperature=0.2,
    )

    latency_ms = int((time.time() - start_time) * 1000)
    cost = calculate_cost(
        response["model_id"],
        response["input_tokens"],
        response["output_tokens"],
    )

    publish_cost_metrics(
        agent="summarizer",
        cost=cost,
        input_tokens=response["input_tokens"],
        output_tokens=response["output_tokens"],
    )
    publish_latency_metric("summarizer", latency_ms)

    result = {
        "agent": "summarizer",
        "answer": response["content"],
        "model": response["model_id"],
        "latency_ms": latency_ms,
        "token_usage": {
            "input": response["input_tokens"],
            "output": response["output_tokens"],
        },
        "cost_estimate": cost,
    }

    logger.info(
        "Summarizer agent complete",
        extra={
            "task_id": task_id,
            "latency_ms": latency_ms,
            "cost": cost,
        },
    )

    return result
