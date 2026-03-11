---
title: "🕸️ I Built a Self-Optimizing Multi-Agent AI System on AWS — Here's How It Works"
published: true
description: "How I reduced LLM costs by ~60% using a broker-worker-verifier architecture with specialized AI agents, vector-cached memory, and automated self-correction — all serverless on AWS."
tags: aws, ai, python, serverless
cover_image: https://dev-to-uploads.s3.amazonaws.com/uploads/articles/placeholder.png
canonical_url: https://dev.to/shubham5027/agentic-mesh
---

## The $100/day Problem

Last year, I was running a side project that used Claude for everything — coding help, research queries, summarization tasks. My AWS bill told a depressing story:

- **70% of tasks** were simple summarizations hitting a $0.015/1K-token model
- **30% of identical questions** were being reprocessed from scratch
- **15% of responses** were low-quality — and I had no way to catch them automatically

I was essentially hiring a senior architect to tell me the time.

So I asked: **What if the system could think about *how* to think before thinking?**

That question became **Agentic Mesh** — a self-optimizing multi-agent AI system that reduced my LLM costs by ~60% while *improving* output quality.

It's open source: [github.com/shubham5027/Agentic-Mesh-AWS](https://github.com/shubham5027/Agentic-Mesh-AWS)

---

## The Core Idea

Instead of:
```
User → Expensive LLM → Response (maybe good, maybe bad)
```

Agentic Mesh does:
```
User → Guardrail → Broker (cheap LLM classifies task)
     → Best Specialist Agent (right model for the job)
     → Verification (LLM-as-a-Judge, scored 1-10)
     → Self-Correction (if score < 7, retry with better model)
     → Vector Cache (embed answer for future reuse)
```

Think of it like a hospital triage system. You don't send every patient to a brain surgeon — a triage nurse figures out where you should go first.

---

## The Architecture (For Real)

Everything runs on AWS serverless — zero servers to manage:

```
┌─────────────────────────────────────────────┐
│           AGENTIC MESH PIPELINE             │
│                                             │
│  Client → API Gateway → SQS → Orchestrator │
│                                    │        │
│              ┌─────────────────────▼──┐     │
│              │   STEP FUNCTIONS       │     │
│              │                        │     │
│              │  🛡️  Guardrail Check   │     │
│              │        │               │     │
│  OpenSearch ◄├──  🧠 Broker Agent     │     │
│  (Cache)     │     (Llama 3 8B)       │     │
│              │        │               │     │
│              │   ┌────┼────┐          │     │
│              │   ▼    ▼    ▼          │     │
│              │  💻   🔍   📝         │     │
│              │ Coder Rsch Summ       │     │
│              │   └────┼────┘          │     │
│              │        ▼               │     │
│              │  🔍 Verification       │     │
│              │     │      │           │     │
│              │   Pass   Fail          │     │
│              │     │      ▼           │     │
│              │     │  🔄 Self-Correct │     │
│              │     └──────┘           │     │
│              │        ▼               │     │
│              │  💾 Save + Cache       │     │
│              └────────────────────────┘     │
│                                             │
│  DynamoDB (Results) · CloudWatch (Metrics)  │
└─────────────────────────────────────────────┘
```

**AWS Services Used:**
- **API Gateway** — REST endpoint
- **SQS** — Async task queue
- **Lambda** — 10 functions, each focused on one job
- **Step Functions** — 13-state orchestration (the brains)
- **DynamoDB** — Stores task results
- **OpenSearch Serverless** — Vector memory for caching
- **Bedrock** — Claude Sonnet 4.5, Haiku 4.5, Llama 3 8B, Titan Embeddings
- **CloudWatch** — 6-widget observability dashboard

All defined in a single SAM `template.yaml`. Deploy in 4 commands.

---

## Deep Dive: The Broker Agent

The Broker is the key innovation. It's a Lambda function powered by **Llama 3 8B Instruct** — the cheapest model in the system (~$0.0003 per call).

Its job: classify the task type and complexity in <100ms.

```python
BROKER_SYSTEM_PROMPT = """You are a task routing agent. Analyze the user's task and determine:
1. The TASK TYPE: one of "coding", "research", or "summarize"
2. The COMPLEXITY: one of "low", "medium", or "high"

Respond ONLY with valid JSON:
{"task_type": "coding|research|summarize", "complexity": "low|medium|high", "reasoning": "brief explanation"}
"""
```

But before it even calls the LLM, the Broker does something clever — it checks **vector memory**:

### Vector Cache Lookup

```python
# Generate embedding for the incoming task
task_embedding = get_embedding(task_text)  # Titan Embeddings V2

# Search OpenSearch for similar previously-solved tasks
similar_tasks = search_similar_tasks(
    task_embedding,
    k=3,
    threshold=0.85,  # Cosine similarity threshold
)

# If we find a high-quality cached answer, skip everything
if similar_tasks and similar_tasks[0]["quality_score"] >= 7.0:
    cache_hit = True
    cached_answer = similar_tasks[0]["answer"]
    # → Skip worker + verification → instant response, $0 LLM cost
```

**The result?** Every successfully answered question makes the *next* similar question free and instant.

### Routing Decision

If there's no cache hit, Llama 3 classifies the task:

| Task Type | Routes To | Model Used |
|-----------|-----------|------------|
| `coding` | Coder Agent | Claude Sonnet 4.5 |
| `research` | Research Agent | Claude Sonnet 4.5 |
| `summarize` | Summarizer Agent | Claude Haiku 4.5 (70% cheaper) |

Why Llama 3 for routing? Because classification doesn't need Claude-level intelligence. Llama 3 8B is **10x cheaper** and more than accurate enough for "is this a coding question or a research question?"

---

## Deep Dive: Specialized Worker Agents

Each worker is a Lambda function with a carefully tuned system prompt. Here's the Coder Agent's:

```python
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
```

The Coder also does **complexity-based model selection**:

```python
COMPLEXITY_MODELS = {
    "low":    "claude-sonnet",       # Sonnet 4.5 — fast for simple tasks
    "medium": "claude-3.5-sonnet",   # Sonnet 4.5 — balanced
    "high":   "claude-3.5-sonnet",   # Sonnet 4.5 — maximum capability
}
```

The Summarizer, meanwhile, uses **Claude Haiku 4.5** — because you don't need a $0.015/1K model to summarize a paragraph. Haiku does it for $0.005/1K with identical quality for this task type.

---

## Deep Dive: The Verification Agent (LLM-as-a-Judge)

This is where things get interesting. **Every single response** goes through a Verification Agent that scores it on three dimensions:

```python
VERIFICATION_PROMPT = """Evaluate this response on:
1. ACCURACY (1-10): Is the information factually correct?
2. COMPLETENESS (1-10): Does it fully address the task?
3. RELEVANCE (1-10): Is every part of the response relevant?

Calculate an overall quality score (average of the three).
If the score is >= 7.0, the response PASSES.
If < 7.0, it FAILS and needs regeneration."""
```

**Why 7.0 as the threshold?**

Through experimentation, I found:
- Scores 8-10: Excellent responses, no issues
- Scores 7-8: Acceptable quality, minor improvements possible
- Scores 5-7: Noticeable quality gaps, should regenerate
- Scores below 5: Significant failures

Setting the bar at 7.0 catches most quality issues without triggering unnecessary regenerations.

---

## Deep Dive: Self-Correction Loop

When verification fails (score < 7/10), the system doesn't just give up — it **self-corrects**:

```python
CORRECTION_SYSTEM_PROMPT = """You are a senior AI specialist. A previous attempt
to answer a task received low quality scores.

Your job is to produce a BETTER answer by:
1. Addressing the specific feedback from the verifier
2. Being more thorough and accurate
3. Adding detail where the original was lacking

Previous attempt's score: {quality_score}/10
Verifier feedback: {feedback}
"""
```

The self-correction Lambda receives:
- The original task
- The failed answer
- The verifier's specific feedback
- The quality dimensions that scored lowest

It then regenerates using **Claude Sonnet 4.5** (the most capable model) with the enhanced context.

**Key design decision:** Only ONE retry. Why? To prevent infinite loops. If the correction still fails, the system saves the best attempt and moves on. In practice, the single retry fixes ~85% of initial failures.

The Step Functions state machine handles this elegantly:

```json
"CheckVerification": {
    "Type": "Choice",
    "Choices": [{
        "Variable": "$.verification_result.passed",
        "BooleanEquals": false,
        "Next": "SelfCorrection"
    }],
    "Default": "PrepareSuccessResult"
}
```

---

## Deep Dive: Vector Memory (The Secret Weapon)

This is my favorite part. After every successful task:

1. **Embed** the answer using Amazon Titan Embeddings V2 (1024-dimensional vectors)
2. **Index** in OpenSearch Serverless with metadata (agent used, quality score, model, timestamp)
3. **Future lookups** use cosine similarity KNN search

```python
def save_to_vector_cache(task_text, answer, agent, quality_score, model):
    embedding = get_embedding(task_text)
    
    document = {
        "task_text": task_text,
        "task_embedding": embedding,
        "answer": answer,
        "agent_used": agent,
        "quality_score": quality_score,
        "model_used": model,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    opensearch_client.index(
        index="task-success-cache",
        body=document
    )
```

**The compounding effect:** After processing 100 tasks, the system has 100 high-quality answers indexed. After 1000 tasks, the cache hit rate climbs significantly. Every answer makes the system smarter, faster, and cheaper.

---

## The Cost Math

Let me break down the actual numbers:

### Without Agentic Mesh (Single Model)

| Task | Model | Cost/Task |
|------|-------|-----------|
| Coding | Claude Sonnet 4.5 | ~$0.008 |
| Research | Claude Sonnet 4.5 | ~$0.005 |
| Summarization | Claude Sonnet 4.5 | ~$0.005 |
| **Average** | | **~$0.006** |

### With Agentic Mesh

| Task | Model | Cost/Task |
|------|-------|-----------|
| Broker routing | Llama 3 8B | $0.0003 |
| Coding | Sonnet 4.5 | $0.008 |
| Research | Sonnet 4.5 | $0.005 |
| Summarization | Haiku 4.5 | **$0.001** |
| Verification | Sonnet 4.5 | $0.002 |
| **Cache hit** | **None** | **$0.000** |
| **Average (with 20% cache hits)** | | **~$0.003** |

**At 1000 tasks/day:**
- Without Mesh: $6/day → $180/month
- With Mesh: $3/day → $90/month
- **Savings: ~$90/month** (and growing as cache hits increase)

---

## Guardrails: Because Safety Matters

Before any task reaches the Broker, it passes through **Bedrock Guardrails**:

| Protection | What It Does |
|-----------|-------------|
| **PII Detection** | Automatically anonymizes names, emails, phone numbers, SSNs |
| **Content Filtering** | Blocks harmful, toxic, or inappropriate content |
| **Prompt Injection** | Detects "ignore your instructions" style attacks |
| **Topic Blocking** | Configurable deny-lists for specific topics |

If a task is blocked, the Step Functions state machine routes it to `TaskBlocked` — the answer is never generated, so no LLM cost is incurred.

---

## Deploying Your Own

The entire system is Infrastructure as Code (SAM):

```bash
# Clone
git clone https://github.com/shubham5027/Agentic-Mesh-AWS.git
cd agentic-mesh

# Install dependencies
pip install -r requirements.txt

# Build
sam build

# Deploy (first time — interactive)
sam deploy --guided

# Deploy (subsequent times)
sam deploy --no-confirm-changeset
```

SAM provisions **everything** — API Gateway, 10 Lambdas, Step Functions, SQS, DynamoDB, OpenSearch Serverless, CloudWatch Dashboard, IAM roles, and Bedrock Guardrails.

### Using the API

```bash
# Submit a task
curl -X POST https://YOUR_API/Prod/task \
  -H "Content-Type: application/json" \
  -d '{"task": "Write a Python function for binary search", "type_hint": "coding"}'

# Response:
# {"task_id": "a1b2c3d4-...", "status": "QUEUED"}

# Poll for results
curl https://YOUR_API/Prod/task/a1b2c3d4-...

# Response:
# {"status": "SUCCESS", "answer": "def binary_search(arr, target)...",
#  "agent": "coder", "quality_score": 8.5, "cost_estimate": 0.004}
```

---

## The Dashboard

I also built a real-time web dashboard (pure HTML/CSS/JS — no frameworks):

- 💬 Chat-like task submission
- 🔀 Animated pipeline showing live processing stages
- 📊 Agent performance analytics
- 📋 Filterable task history
- 🌙 Dark glassmorphism design

```bash
# Run locally
python -m http.server 8080 --directory dashboard
```

---

## What I Learned Building This

1. **Cheap models are underrated for routing.** Llama 3 8B is absurdly good at classification tasks. Don't use a $0.01 model for a $0.0003 job.

2. **LLM-as-a-Judge works.** Having a separate model grade responses catches quality issues that would otherwise reach users silently.

3. **Vector caching compounds.** The more tasks you process, the more cache hits you get. It's a flywheel that makes the system cheaper over time.

4. **Step Functions > custom orchestration.** The built-in retry, catch, and visual debugging features saved me weeks of writing my own queue management code.

5. **Self-correction is worth one retry.** One retry with enhanced context fixes ~85% of failures. More retries aren't worth the cost.

---

## What's Next

I'm planning to add:
- 🔄 WebSocket streaming for real-time progress
- 📎 Multi-modal support (images + PDFs via Claude Vision)
- 🔗 Multi-step task chains (agents collaborating on complex problems)
- 📊 A/B model testing (shadow evaluator for model comparison)
- 💬 Conversation memory (multi-turn sessions)

---

## Try It

The entire project is open source under MIT license:

⭐ **[github.com/shubham5027/Agentic-Mesh-AWS](https://github.com/shubham5027/Agentic-Mesh-AWS)**

If you build something cool with it, or have ideas for improvements, I'd love to hear from you. Drop a comment below or open an issue on GitHub.

And if this was useful — a ⭐ on the repo helps others find it!

---

*Built with Python, AWS Bedrock, and too much coffee ☕*
