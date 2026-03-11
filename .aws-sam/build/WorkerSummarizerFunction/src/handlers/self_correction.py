"""
Self-Correction Lambda
Triggered when the Verification Agent fails a worker's output.
Re-invokes the elite model (Claude 3.5 Sonnet) with enhanced prompt
that includes the original task, failed answer, and verification feedback.
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
from src.models.cost_tracker import (
    calculate_cost,
    publish_cost_metrics,
    publish_latency_metric,
    publish_escalation_metric,
)

CORRECTION_SYSTEM_PROMPT = """You are an expert AI assistant tasked with producing a corrected, high-quality response.

A previous AI agent attempted this task but produced an answer that failed quality verification. You have been given:
1. The original task
2. The previous (failed) answer
3. Specific feedback on what was wrong

Your job is to produce a CORRECTED answer that addresses all the feedback points.

Guidelines:
- DO NOT simply rephrase the failed answer
- Address each specific issue mentioned in the feedback
- If the previous answer had factual errors, correct them
- If the previous answer was incomplete, fill in the gaps
- Maintain high quality, accuracy, and completeness
- Be thorough but concise

This is a self-correction step — the quality of your output directly impacts the system's reliability."""


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Produce a corrected answer using the elite model with enhanced context.

    Input:
        {
            "task_id": str,
            "original_task": str,
            "failed_answer": str,
            "failed_agent": str,
            "verification_feedback": str,
            "quality_score": float
        }

    Output:
        {
            "corrected_answer": str,
            "model": str,
            "cost_estimate": float,
            "latency_ms": int,
            "correction_context": {
                "original_agent": str,
                "original_quality_score": float,
                "feedback_used": str
            }
        }
    """
    task_id = event.get("task_id", "unknown")
    original_task = event.get("original_task", "")
    failed_answer = event.get("failed_answer", "")
    failed_agent = event.get("failed_agent", "unknown")
    feedback = event.get("verification_feedback", "No specific feedback.")
    original_score = event.get("quality_score", 0)
    start_time = time.time()

    logger.info(
        "Self-correction triggered",
        extra={
            "task_id": task_id,
            "failed_agent": failed_agent,
            "original_quality_score": original_score,
        },
    )

    # Build enhanced correction prompt
    correction_prompt = f"""## Original Task
{original_task}

## Previous Answer (by {failed_agent} agent — quality score: {original_score}/10)
{failed_answer}

## Verification Feedback
{feedback}

## Your Corrected Answer
Please produce a high-quality corrected response that addresses all the issues identified above."""

    # Always use the elite model for self-correction
    response = invoke_model(
        model_id="claude-3.5-sonnet",
        messages=[{"role": "user", "content": correction_prompt}],
        system_prompt=CORRECTION_SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.3,
    )

    latency_ms = int((time.time() - start_time) * 1000)
    cost = calculate_cost(
        response["model_id"],
        response["input_tokens"],
        response["output_tokens"],
    )

    # Publish escalation metrics
    publish_escalation_metric(escalated=True)
    publish_cost_metrics(
        agent="self_correction",
        cost=cost,
        input_tokens=response["input_tokens"],
        output_tokens=response["output_tokens"],
    )
    publish_latency_metric("self_correction", latency_ms)

    result = {
        "corrected_answer": response["content"],
        "model": response["model_id"],
        "cost_estimate": cost,
        "latency_ms": latency_ms,
        "correction_context": {
            "original_agent": failed_agent,
            "original_quality_score": original_score,
            "feedback_used": feedback,
        },
    }

    logger.info(
        "Self-correction complete",
        extra={
            "task_id": task_id,
            "latency_ms": latency_ms,
            "cost": cost,
            "original_agent": failed_agent,
        },
    )

    return result
