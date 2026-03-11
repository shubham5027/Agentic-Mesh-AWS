"""
Shared Bedrock Invocation Client
Provides unified interface for invoking Amazon Bedrock models and generating embeddings.
Includes retry logic, structured logging, and token usage tracking.
"""

import json
import time
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger

logger = Logger(service="agentic-mesh", child=True)

# ── Bedrock client with retry configuration ──────────────────────────
bedrock_config = Config(
    retries={"max_attempts": 3, "mode": "adaptive"},
    read_timeout=120,
    connect_timeout=10,
)
bedrock_runtime = boto3.client("bedrock-runtime", config=bedrock_config)

# ── Model IDs ────────────────────────────────────────────────────────
# Claude 4+ models require inference profiles (us. prefix) instead of direct model IDs
# Updated 2026-03-10: Sonnet 4 marked Legacy → upgraded to Sonnet 4.5 & Haiku 4.5
MODELS = {
    "llama3-8b":          "meta.llama3-8b-instruct-v1:0",
    "claude-haiku":       "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-3.5-haiku":   "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet":      "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-3.5-sonnet":  "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "titan-embed-v2":     "amazon.titan-embed-text-v2:0",
}


def invoke_model(
    model_id: str,
    messages: list[dict],
    system_prompt: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> dict:
    """
    Invoke a Bedrock model and return the response with metadata.

    Args:
        model_id: Full Bedrock model ID or alias from MODELS dict.
        messages: List of message dicts [{"role": "user", "content": "..."}].
        system_prompt: Optional system-level instructions.
        max_tokens: Maximum output tokens.
        temperature: Sampling temperature.

    Returns:
        {
            "content": str,
            "input_tokens": int,
            "output_tokens": int,
            "latency_ms": int,
            "model_id": str,
            "stop_reason": str
        }
    """
    # Resolve alias to full model ID
    resolved_model = MODELS.get(model_id, model_id)
    is_anthropic = "anthropic" in resolved_model
    is_meta = "meta" in resolved_model

    start_time = time.time()

    try:
        if is_anthropic:
            response = _invoke_anthropic(
                resolved_model, messages, system_prompt, max_tokens, temperature
            )
        elif is_meta:
            response = _invoke_meta(
                resolved_model, messages, system_prompt, max_tokens, temperature
            )
        else:
            raise ValueError(f"Unsupported model: {resolved_model}")

        latency_ms = int((time.time() - start_time) * 1000)
        response["latency_ms"] = latency_ms
        response["model_id"] = resolved_model

        logger.info(
            "Model invocation successful",
            extra={
                "model_id": resolved_model,
                "input_tokens": response.get("input_tokens", 0),
                "output_tokens": response.get("output_tokens", 0),
                "latency_ms": latency_ms,
            },
        )
        return response

    except ClientError as e:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "Bedrock invocation failed",
            extra={
                "model_id": resolved_model,
                "error": str(e),
                "latency_ms": latency_ms,
            },
        )
        raise


def _invoke_anthropic(
    model_id: str,
    messages: list[dict],
    system_prompt: str,
    max_tokens: int,
    temperature: float,
) -> dict:
    """Invoke an Anthropic Claude model via Bedrock Messages API."""
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system_prompt:
        body["system"] = system_prompt

    response = bedrock_runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )

    result = json.loads(response["body"].read())

    return {
        "content": result["content"][0]["text"],
        "input_tokens": result["usage"]["input_tokens"],
        "output_tokens": result["usage"]["output_tokens"],
        "stop_reason": result.get("stop_reason", "end_turn"),
    }


def _invoke_meta(
    model_id: str,
    messages: list[dict],
    system_prompt: str,
    max_tokens: int,
    temperature: float,
) -> dict:
    """Invoke a Meta Llama model via Bedrock."""
    # Build prompt in Llama 3 chat format
    prompt_parts = []
    if system_prompt:
        prompt_parts.append(f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{system_prompt}<|eot_id|>")

    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        prompt_parts.append(
            f"<|start_header_id|>{role}<|end_header_id|>\n\n{content}<|eot_id|>"
        )

    prompt_parts.append("<|start_header_id|>assistant<|end_header_id|>\n\n")
    full_prompt = "".join(prompt_parts)

    body = {
        "prompt": full_prompt,
        "max_gen_len": max_tokens,
        "temperature": temperature,
    }

    response = bedrock_runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )

    result = json.loads(response["body"].read())

    return {
        "content": result.get("generation", ""),
        "input_tokens": result.get("prompt_token_count", 0),
        "output_tokens": result.get("generation_token_count", 0),
        "stop_reason": result.get("stop_reason", "stop"),
    }


def get_embedding(text: str) -> list[float]:
    """
    Generate a text embedding using Amazon Titan Embeddings V2.

    Args:
        text: Input text to embed (max ~8000 tokens).

    Returns:
        List of floats (1024-dimensional embedding vector).
    """
    model_id = MODELS["titan-embed-v2"]

    body = {
        "inputText": text[:8000],  # Safety truncation
        "dimensions": 1024,
        "normalize": True,
    }

    try:
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )

        result = json.loads(response["body"].read())
        embedding = result["embedding"]

        logger.info(
            "Embedding generated",
            extra={"text_length": len(text), "dimensions": len(embedding)},
        )
        return embedding

    except ClientError as e:
        logger.error("Embedding generation failed", extra={"error": str(e)})
        raise
