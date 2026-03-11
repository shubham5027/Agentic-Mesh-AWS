# Building a Self-Optimizing Multi-Agent AI System with AWS Bedrock, Step Functions, and OpenSearch Serverless

> **Category:** AI/ML | **Level:** Advanced | **Services:** Bedrock, Step Functions, Lambda, OpenSearch Serverless, DynamoDB, API Gateway, SQS, CloudWatch

## Introduction

As AI workloads grow, a common challenge emerges: how do you route diverse tasks to the right model, ensure quality, and control costs — all without managing servers?

**Agentic Mesh** is an open-source reference architecture that solves this with a **broker-worker-verifier** pattern built entirely on AWS serverless services. In this post, I'll walk through the architecture, the engineering decisions, and how to deploy it yourself.

**GitHub:** [github.com/shubham5027/Agentic-Mesh-AWS](https://github.com/shubham5027/Agentic-Mesh-AWS)

---

## Problem Statement

Most LLM-powered applications use a single model for all requests. This approach has three problems:

1. **Cost inefficiency** — A summarization task doesn't need Claude Sonnet; Claude Haiku handles it at 70% lower cost
2. **No quality assurance** — Bad outputs are returned to users with no automated checks
3. **Redundant computation** — Identical or similar tasks are reprocessed from scratch every time

Agentic Mesh addresses all three through intelligent routing, automated verification, and vector-cached memory.

---

## Architecture Overview

```
Client → API Gateway → SQS → Orchestrator Lambda → Step Functions
                                                         │
                                                   Guardrail Check
                                                         │
                                              ┌──── Broker Agent ────┐
                                              │    (Llama 3 8B)      │
                                              │                      │
                                         Cache Hit?            LLM Routing
                                           │   │              ┌────┼────┐
                                          Yes  No           Coder Rsrch Summ
                                           │   └──────────────┘────┘────┘
                                           │                       │
                                           └───── Verification ────┘
                                                      │
                                                 Pass? │ Fail?
                                                  │         │
                                              Save      Self-Correct
                                              Results     (Retry)
                                                │            │
                                              DynamoDB + OpenSearch
```

### AWS Services Used

| Service | Role | Why This Service |
|---------|------|-----------------|
| **API Gateway** | REST endpoint (`POST /task`, `GET /task/{id}`) | Managed, scales automatically, built-in throttling |
| **SQS** | Async task queue between API and orchestrator | Decouples API from processing, absorbs burst traffic |
| **Lambda** (×10) | Individual handlers for each pipeline stage | Pay-per-invocation, scales to 1000+ concurrent |
| **Step Functions** | 13-state orchestration of the full pipeline | Built-in retry/catch, visual debugging, Choice states for routing |
| **Bedrock** | AI model inference (Claude, Llama, Titan) | Managed model hosting, no endpoints to provision |
| **DynamoDB** | Task result storage | On-demand capacity, single-digit ms latency |
| **OpenSearch Serverless** | Vector similarity search (task cache) | Serverless KNN search, AWS-native, auto-scales |
| **CloudWatch** | Custom metrics and pre-built dashboard | 6 widgets: routing distribution, cost, latency, quality, cache rate, escalations |

---

## Component Deep Dives

### 1. Bedrock Guardrails

Every task passes through Bedrock Guardrails as the first Step Functions state:

- **PII Detection & Anonymization** — Names, emails, phone numbers, and SSNs are automatically masked
- **Content Filtering** — Blocks harmful, toxic, or inappropriate content
- **Prompt Injection Protection** — Detects adversarial prompts
- **Topic Deny-Lists** — Configurable topic blocking

If a task is blocked, the state machine routes directly to `SaveResults` with status `BLOCKED` — no LLM invocation occurs, and no cost is incurred.

### 2. Broker Agent (Llama 3 8B Instruct)

The Broker performs two functions:

**a) Vector Cache Lookup**

```python
# Generate 1024-dim embedding
task_embedding = get_embedding(task_text)  # Titan Embeddings V2

# KNN search in OpenSearch
similar_tasks = search_similar_tasks(
    task_embedding, k=3, threshold=0.85
)

# Use cached answer if quality is sufficient
if similar_tasks[0]["quality_score"] >= 7.0:
    return {"cache_hit": True, "cached_answer": similar_tasks[0]["answer"]}
```

**b) Task Classification**

If no cache hit, Llama 3 8B classifies the task:

```python
response = invoke_model(
    model_id="llama3-8b",
    system_prompt="Classify this task as coding/research/summarize with complexity low/medium/high",
    messages=[{"role": "user", "content": task_text}],
    temperature=0.1  # Low temperature for consistent classification
)
```

**Why Llama 3 8B?** At ~$0.0003 per invocation, it's 10x cheaper than Claude Haiku for a task that only requires text classification accuracy.

### 3. Worker Agents

| Agent | Model | Specialization |
|-------|-------|---------------|
| **Coder** | Claude Sonnet 4.5 | Code generation, debugging, algorithms, reviews |
| **Researcher** | Claude Sonnet 4.5 | Analysis, comparisons, explanations, concept breakdowns |
| **Summarizer** | Claude Haiku 4.5 | Condensing, key point extraction, reformatting |

Each worker has a domain-specific system prompt optimized for its task type. The Coder, for example, is instructed to always use code blocks with language tags, add inline comments, and consider edge cases.

The Summarizer uses **Haiku 4.5** instead of Sonnet — saving ~70% per summarization task with equivalent quality for that task type.

### 4. Verification Agent (LLM-as-a-Judge)

Every worker response is evaluated across three dimensions:

| Dimension | What It Measures |
|-----------|-----------------|
| **Accuracy** | Is the information factually correct? |
| **Completeness** | Does it fully address the original task? |
| **Relevance** | Is every part of the response relevant to the task? |

The overall quality score is the average of the three dimensions. The pass threshold is **7.0/10**.

This approach (LLM-as-a-Judge) generalizes better than heuristic-based checks because it works across all task types — coding, research, and summarization — without task-specific validation logic.

### 5. Self-Correction Loop

When verification fails:

1. The Self-Correction Lambda receives the original task, failed answer, and verifier feedback
2. It constructs an enhanced prompt incorporating the feedback
3. Regenerates using Claude Sonnet 4.5 with the enhanced context
4. The new answer is saved directly (single retry to prevent infinite loops)

The Step Functions state machine marks corrected responses with `"escalated": true` for downstream analytics.

### 6. Save Results + Vector Cache Update

Successful results are persisted in two stores:

- **DynamoDB** — Full result record (task, answer, agent, quality score, cost, latency)
- **OpenSearch** — Task embedding + answer for future cache lookups

---

## Infrastructure as Code

The entire system is defined in a single `template.yaml` (SAM/CloudFormation):

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Self-Optimizing Agentic Mesh for Heterogeneous Tasks

Globals:
  Function:
    Runtime: python3.10
    Timeout: 120
    MemorySize: 512

Resources:
  # API Gateway, 10 Lambdas, Step Functions, SQS, DynamoDB,
  # OpenSearch Serverless, CloudWatch Dashboard, IAM Roles,
  # Bedrock Guardrail — all in ~600 lines of YAML
```

### Deploy

```bash
git clone https://github.com/shubham5027/Agentic-Mesh-AWS.git
cd agentic-mesh
pip install -r requirements.txt
sam build
sam deploy --guided
```

**Prerequisites:**
- AWS CLI configured with credentials
- SAM CLI installed
- Bedrock model access enabled (Llama 3 8B, Claude Haiku 4.5, Claude Sonnet 4.5, Titan Embeddings V2)

---

## Observability

### CloudWatch Dashboard

The SAM template deploys a 6-widget CloudWatch dashboard:

| Widget | Metric |
|--------|--------|
| Routing Distribution | Tasks per agent (coder/researcher/summarizer) |
| Cost per Agent | Running cost breakdown |
| Latency by Agent | p50/p95 worker latency |
| Verification Scores | Quality score distribution |
| Cache Hit Rate | % of tasks served from vector memory |
| Escalation Rate | % triggering self-correction |

### Structured Logging & Tracing

All Lambda functions use [AWS Lambda Powertools for Python](https://docs.powertools.aws.dev/lambda/python/):

```python
from aws_lambda_powertools import Logger, Tracer

logger = Logger(service="agentic-mesh")
tracer = Tracer(service="agentic-mesh")

@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event, context):
    logger.info("Processing task", extra={"task_id": event["task_id"]})
```

Every invocation produces structured JSON logs with correlation IDs and X-Ray trace segments for end-to-end distributed tracing.

---

## Web Dashboard

A standalone web dashboard (HTML/CSS/JS) provides:

- Chat-like task submission interface
- Real-time pipeline visualization (animated Step Functions stages)
- Agent performance analytics
- Task history with filtering and detail modals

```bash
python -m http.server 8080 --directory dashboard
```

The dashboard communicates with the API Gateway endpoint. CORS is handled through Powertools' `CORSConfig`.

---

## Cost Optimization Results

Five-layer cost optimization:

| Layer | Strategy | Savings |
|-------|----------|---------|
| Smart Routing | Llama 3 8B routes for $0.0003 vs $0.003 | ~70% on routing |
| Model Tiering | Haiku for summarization, Sonnet for coding | ~60% per task type |
| Complexity Matching | Right-sized models for task difficulty | ~30% per task |
| Vector Cache | Cached results served for $0 LLM cost | 100% on hits |
| Guardrail Blocking | Blocked tasks incur zero model cost | 100% on blocked |

---

## Next Steps

Planned enhancements:
- WebSocket streaming for real-time progress
- Multi-modal support (images + PDFs via Claude Vision)
- Agent collaboration chains for multi-step tasks
- A/B model testing (shadow evaluator)
- Cognito authentication for the API

---

## Resources

- **GitHub Repository:** [github.com/shubham5027/Agentic-Mesh-AWS](https://github.com/shubham5027/Agentic-Mesh-AWS)
- **License:** MIT
- **Author:** Shubham Kumbhar

Contributions welcome — see the [Contributing Guide](https://github.com/shubham5027/Agentic-Mesh-AWS#-contributing) in the README.
