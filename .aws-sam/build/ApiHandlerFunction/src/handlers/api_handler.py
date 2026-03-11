"""
API Handler Lambda
REST API entry point for the Agentic Mesh.
Handles POST /task (submit) and GET /task/{taskId} (poll results).
"""

import os
import json
import uuid
import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, CORSConfig
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="agentic-mesh")
tracer = Tracer(service="agentic-mesh")

cors_config = CORSConfig(allow_origin="*", allow_headers=["Content-Type", "Authorization"], max_age=300)
app = APIGatewayRestResolver(cors=cors_config)

sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")

QUEUE_URL = os.environ.get("QUEUE_URL", "")
TASK_TABLE_NAME = os.environ.get("TASK_TABLE_NAME", "AgenticMeshTaskResults")


@app.post("/task")
@tracer.capture_method
def submit_task():
    """
    Submit a new task for processing.

    Request body:
        {
            "task": "Write a Python function for binary search",
            "type_hint": "coding"  // optional: "coding", "research", "summarize"
        }

    Response:
        {
            "task_id": "uuid",
            "status": "QUEUED",
            "message": "Task submitted successfully"
        }
    """
    body = app.current_event.json_body

    task_text = body.get("task", "").strip()
    if not task_text:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Field 'task' is required and cannot be empty."}),
        }

    task_id = str(uuid.uuid4())
    type_hint = body.get("type_hint", "auto")

    # ── Enqueue to SQS ───────────────────────────────────────────
    message = {
        "task_id": task_id,
        "task": task_text,
        "type_hint": type_hint,
    }

    try:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(message),
            MessageAttributes={
                "TaskId": {"StringValue": task_id, "DataType": "String"},
            },
        )
    except Exception as e:
        logger.error("Failed to enqueue task", extra={"error": str(e)})
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to submit task. Please try again."}),
        }

    # ── Create initial DynamoDB record ───────────────────────────
    try:
        table = dynamodb.Table(TASK_TABLE_NAME)
        table.put_item(
            Item={
                "task_id": task_id,
                "task": task_text,
                "type_hint": type_hint,
                "status": "QUEUED",
                "created_at": str(uuid.uuid1().time),
            }
        )
    except Exception as e:
        logger.warning("Failed to create initial DDB record", extra={"error": str(e)})

    logger.info("Task submitted", extra={"task_id": task_id, "type_hint": type_hint})

    return {
        "statusCode": 202,
        "body": json.dumps({
            "task_id": task_id,
            "status": "QUEUED",
            "message": "Task submitted successfully. Poll GET /task/{task_id} for results.",
        }),
    }


@app.get("/task/<task_id>")
@tracer.capture_method
def get_task_result(task_id: str):
    """
    Poll for task results.

    Response (pending):
        {"task_id": "uuid", "status": "PROCESSING"}

    Response (complete):
        {
            "task_id": "uuid",
            "status": "SUCCESS",
            "answer": "...",
            "agent": "coder",
            "quality_score": 8.5,
            "cost_estimate": 0.012,
            "escalated": false
        }
    """
    try:
        table = dynamodb.Table(TASK_TABLE_NAME)
        response = table.get_item(Key={"task_id": task_id})

        if "Item" not in response:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": f"Task '{task_id}' not found."}),
            }

        item = response["Item"]

        # Convert Decimal types for JSON serialization
        result = {}
        for key, value in item.items():
            if hasattr(value, "__float__"):
                result[key] = float(value)
            else:
                result[key] = value

        return {
            "statusCode": 200,
            "body": json.dumps(result),
        }

    except Exception as e:
        logger.error("Failed to get task", extra={"task_id": task_id, "error": str(e)})
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to retrieve task results."}),
        }


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Main Lambda entry point — delegates to the API resolver."""
    return app.resolve(event, context)
