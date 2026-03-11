"""
Worker Coder Agent Lambda
Specialized agent for coding tasks — uses Claude 3.5 Sonnet for code generation,
debugging, and technical implementation.
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

CODER_SYSTEM_PROMPT = """You are an expert software engineer and coding specialist. You excel at:
- Writing clean, efficient, well-documented code
- Debugging and fixing bugs
- Explaining algorithms and data structures
- Implementing design patterns and best practices
- Providing code reviews with actionable feedback

Guidelines:
1. Always include code in properly formatted code blocks with language tags
2. Add inline comments explaining complex logic
3. Consider edge cases and error handling
4. Follow the language's idiomatic conventions
5. If the task is ambiguous, state your assumptions before coding

Respond concisely but thoroughly. Quality over verbosity."""

# Model selection based on complexity
COMPLEXITY_MODELS = {
    "low": "claude-sonnet",         # Use Sonnet for simple coding tasks
    "medium": "claude-3.5-sonnet",  # Use 3.5 Sonnet for medium tasks
    "high": "claude-3.5-sonnet",    # Use 3.5 Sonnet for complex tasks
}


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Execute a coding task using the appropriate Claude model.

    Input:
        {
            "task_id": str,
            "task": str,
            "complexity": str ("low"|"medium"|"high")
        }

    Output:
        {
            "agent": "coder",
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

    # Select model based on complexity
    model_alias = COMPLEXITY_MODELS.get(complexity, "claude-3.5-sonnet")

    logger.info(
        "Coder agent processing task",
        extra={
            "task_id": task_id,
            "complexity": complexity,
            "model": model_alias,
        },
    )

    # Invoke the model
    response = invoke_model(
        model_id=model_alias,
        messages=[{"role": "user", "content": task_text}],
        system_prompt=CODER_SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.2,
    )

    latency_ms = int((time.time() - start_time) * 1000)
    cost = calculate_cost(
        response["model_id"],
        response["input_tokens"],
        response["output_tokens"],
    )

    # Publish metrics
    publish_cost_metrics(
        agent="coder",
        cost=cost,
        input_tokens=response["input_tokens"],
        output_tokens=response["output_tokens"],
    )
    publish_latency_metric("coder", latency_ms)

    result = {
        "agent": "coder",
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
        "Coder agent complete",
        extra={
            "task_id": task_id,
            "latency_ms": latency_ms,
            "cost": cost,
            "tokens": response["input_tokens"] + response["output_tokens"],
        },
    )

    return result
