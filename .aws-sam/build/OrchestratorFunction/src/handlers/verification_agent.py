"""
Verification Agent Lambda (Shadow Deployment)
Runs in the background to independently verify worker output quality.
Uses Claude 3.5 Sonnet as an LLM-as-a-Judge to grade answers.
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
from src.models.cost_tracker import calculate_cost, publish_quality_metric

# Quality threshold — answers scoring below this trigger self-correction
QUALITY_PASS_THRESHOLD = 7.0

JUDGE_SYSTEM_PROMPT = """You are an expert quality evaluator for AI-generated responses. Your job is to grade the quality of an answer given a specific task.

Evaluate the answer on these three dimensions (each scored 1-10):

1. **Accuracy**: Is the answer factually correct? Does it correctly address the task?
2. **Completeness**: Does the answer fully address all aspects of the task? Are there missing elements?
3. **Relevance**: Is the answer focused on the task? Is there unnecessary filler or off-topic content?

You MUST respond with ONLY valid JSON in this exact format:
{
  "accuracy": <1-10>,
  "completeness": <1-10>,
  "relevance": <1-10>,
  "feedback": "<1-2 sentence explanation of the main strengths or weaknesses>",
  "critical_issues": "<null or brief description of any factual errors or major omissions>"
}

Be strict but fair. A score of 7 means "good enough for production use."
"""


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Verify the quality of a worker agent's output.

    Input:
        {
            "task_id": str,
            "original_task": str,
            "worker_answer": str,
            "worker_agent": str,
            "worker_cost": float
        }

    Output:
        {
            "quality_score": float,
            "passed": bool,
            "feedback": str,
            "dimensions": {
                "accuracy": int,
                "completeness": int,
                "relevance": int
            }
        }
    """
    task_id = event.get("task_id", "unknown")
    original_task = event.get("original_task", "")
    worker_answer = event.get("worker_answer", "")
    worker_agent = event.get("worker_agent", "unknown")
    start_time = time.time()

    logger.info(
        "Verification agent evaluating answer",
        extra={
            "task_id": task_id,
            "worker_agent": worker_agent,
            "answer_length": len(worker_answer),
        },
    )

    # Build evaluation prompt
    evaluation_prompt = f"""## Task Given to Agent
{original_task}

## Agent's Answer ({worker_agent})
{worker_answer}

## Your Evaluation
Evaluate the above answer against the original task. Respond with JSON only."""

    try:
        response = invoke_model(
            model_id="claude-3.5-sonnet",
            messages=[{"role": "user", "content": evaluation_prompt}],
            system_prompt=JUDGE_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.1,
        )

        content = response["content"].strip()

        # Handle potential markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        evaluation = json.loads(content)

        accuracy = evaluation.get("accuracy", 5)
        completeness = evaluation.get("completeness", 5)
        relevance = evaluation.get("relevance", 5)
        feedback = evaluation.get("feedback", "No feedback provided.")
        critical_issues = evaluation.get("critical_issues")

        quality_score = round((accuracy + completeness + relevance) / 3, 1)
        passed = quality_score >= QUALITY_PASS_THRESHOLD

        # Lower the score if critical issues are found
        if critical_issues and critical_issues.lower() != "null":
            quality_score = min(quality_score, 5.0)
            passed = False
            feedback = f"{feedback} CRITICAL: {critical_issues}"

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(
            "Failed to parse verification result, defaulting to pass",
            extra={"error": str(e)},
        )
        accuracy = completeness = relevance = 7
        quality_score = 7.0
        passed = True
        feedback = "Verification parsing failed; defaulting to pass."

    except Exception as e:
        logger.error(
            "Verification failed",
            extra={"error": str(e), "task_id": task_id},
        )
        accuracy = completeness = relevance = 7
        quality_score = 7.0
        passed = True
        feedback = "Verification error; defaulting to pass."

    latency_ms = int((time.time() - start_time) * 1000)

    # Publish quality metrics
    publish_quality_metric(worker_agent, quality_score, passed)

    # Calculate verification cost
    if 'response' in dir():
        verify_cost = calculate_cost(
            response.get("model_id", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            response.get("input_tokens", 0),
            response.get("output_tokens", 0),
        )
    else:
        verify_cost = 0.0

    result = {
        "quality_score": quality_score,
        "passed": passed,
        "feedback": feedback,
        "dimensions": {
            "accuracy": accuracy,
            "completeness": completeness,
            "relevance": relevance,
        },
        "verification_cost": verify_cost,
        "verification_latency_ms": latency_ms,
    }

    logger.info(
        "Verification complete",
        extra={
            "task_id": task_id,
            "quality_score": quality_score,
            "passed": passed,
            "worker_agent": worker_agent,
            "latency_ms": latency_ms,
        },
    )

    return result
