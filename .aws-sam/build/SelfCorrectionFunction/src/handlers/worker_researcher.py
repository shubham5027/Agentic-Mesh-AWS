"""
Worker Researcher Agent Lambda
Specialized agent for research and analysis tasks — uses Claude 3 Sonnet
for balanced quality/cost on information synthesis.
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

RESEARCHER_SYSTEM_PROMPT = """You are an expert research analyst and knowledge synthesizer. You excel at:
- Breaking down complex topics into clear explanations
- Comparing and contrasting different approaches, technologies, or ideas
- Providing balanced analysis with pros and cons
- Citing relevant frameworks, methodologies, and best practices
- Structuring information for maximum clarity

Guidelines:
1. Structure your response with clear headings and bullet points
2. Present multiple perspectives when applicable
3. Distinguish between facts, widely-accepted views, and opinions
4. Use concrete examples to illustrate abstract concepts
5. Conclude with actionable insights or recommendations

Be thorough but organized. Depth over breadth when appropriate."""

# Model selection based on complexity
COMPLEXITY_MODELS = {
    "low": "claude-haiku",       # Haiku for simple research
    "medium": "claude-sonnet",   # Sonnet for balanced research
    "high": "claude-3.5-sonnet", # 3.5 Sonnet for deep research
}


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Execute a research/analysis task.

    Input:
        {
            "task_id": str,
            "task": str,
            "complexity": str ("low"|"medium"|"high")
        }

    Output:
        {
            "agent": "researcher",
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

    model_alias = COMPLEXITY_MODELS.get(complexity, "claude-sonnet")

    logger.info(
        "Researcher agent processing task",
        extra={
            "task_id": task_id,
            "complexity": complexity,
            "model": model_alias,
        },
    )

    response = invoke_model(
        model_id=model_alias,
        messages=[{"role": "user", "content": task_text}],
        system_prompt=RESEARCHER_SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.4,
    )

    latency_ms = int((time.time() - start_time) * 1000)
    cost = calculate_cost(
        response["model_id"],
        response["input_tokens"],
        response["output_tokens"],
    )

    publish_cost_metrics(
        agent="researcher",
        cost=cost,
        input_tokens=response["input_tokens"],
        output_tokens=response["output_tokens"],
    )
    publish_latency_metric("researcher", latency_ms)

    result = {
        "agent": "researcher",
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
        "Researcher agent complete",
        extra={
            "task_id": task_id,
            "latency_ms": latency_ms,
            "cost": cost,
        },
    )

    return result
