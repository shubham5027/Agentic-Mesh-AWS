"""
Orchestrator Lambda
Triggered by SQS messages. Starts the Step Functions state machine execution.
"""

import os
import json
import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="agentic-mesh")
tracer = Tracer(service="agentic-mesh")

sfn = boto3.client("stepfunctions")

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """
    Process SQS messages and start Step Functions executions.

    Each SQS message contains:
        {
            "task_id": "uuid",
            "task": "user task text",
            "type_hint": "auto|coding|research|summarize"
        }
    """
    results = []

    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            task_id = message["task_id"]

            logger.info(
                "Starting state machine execution",
                extra={"task_id": task_id, "type_hint": message.get("type_hint")},
            )

            response = sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=f"task-{task_id}",
                input=json.dumps(message),
            )

            results.append({
                "task_id": task_id,
                "execution_arn": response["executionArn"],
                "status": "STARTED",
            })

            logger.info(
                "State machine execution started",
                extra={
                    "task_id": task_id,
                    "execution_arn": response["executionArn"],
                },
            )

        except Exception as e:
            logger.error(
                "Failed to start execution",
                extra={
                    "error": str(e),
                    "record": record.get("messageId", "unknown"),
                },
            )
            raise  # Let SQS retry via visibility timeout

    return {"processed": len(results), "results": results}
