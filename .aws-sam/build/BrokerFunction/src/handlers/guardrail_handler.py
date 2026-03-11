"""
Guardrail Handler Lambda
Step Functions step that runs guardrail checks on incoming tasks.
"""

import json
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="agentic-mesh")
tracer = Tracer(service="agentic-mesh")

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.guardrails.guardrails import check_guardrails


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Run guardrail checks on the incoming task.

    Input:
        {"task_id": str, "task": str, "type_hint": str}

    Output:
        {
            "safe": bool,
            "sanitized_input": str,
            "violations": [...],
            "pii_found": [...]
        }
    """
    task_text = event.get("task", "")
    task_id = event.get("task_id", "unknown")

    logger.info("Running guardrail checks", extra={"task_id": task_id})

    result = check_guardrails(task_text)

    if not result["safe"]:
        logger.warning(
            "Task blocked by guardrails",
            extra={
                "task_id": task_id,
                "violations": result["violations"],
            },
        )
    else:
        logger.info(
            "Task passed guardrails",
            extra={
                "task_id": task_id,
                "pii_found": len(result.get("pii_found", [])),
            },
        )

    return result
