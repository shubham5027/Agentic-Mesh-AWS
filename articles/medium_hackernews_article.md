# How I Built a Self-Optimizing Multi-Agent AI System on AWS That Reduced LLM Costs by 60%

*A deep dive into the broker-worker-verifier architecture behind Agentic Mesh*

---

Most AI applications today follow a brutally simple pattern: take user input, send it to the most powerful LLM available, return the output. This approach is the equivalent of hiring a brain surgeon every time someone gets a paper cut.

The result? Ballooning costs, unnecessary latency, and a complete absence of quality control. When your summarization tasks and your complex coding challenges both hit Claude Sonnet at $0.015 per 1K output tokens, you're leaving money — and reliability — on the table.

I spent the last several months building a different approach. **Agentic Mesh** is an open-source, AWS-native multi-agent orchestration system that dynamically routes tasks to specialized AI agents based on cost, quality, and historical performance. It runs entirely on serverless infrastructure and deploys with a single SAM command.

Here's how it works, why it works, and the engineering decisions that shaped it.

## The Architecture

Agentic Mesh implements what I call a **broker-worker-verifier** pattern:

1. **Guardrail Layer** — Every incoming task passes through Bedrock Guardrails for PII anonymization, content filtering, and prompt injection detection
2. **Broker Agent** — A lightweight LLM (Llama 3 8B, ~$0.0003/call) classifies the task type and complexity
3. **Vector Cache Lookup** — Before routing, the broker searches an OpenSearch Serverless index for semantically similar previously-solved tasks
4. **Specialized Workers** — Three domain-specific agents (Coder, Researcher, Summarizer), each with tuned system prompts and appropriate models
5. **Verification Agent** — An LLM-as-a-Judge that scores every response on accuracy, completeness, and relevance (1-10 scale)
6. **Self-Correction Loop** — If the quality score is below 7/10, the system automatically regenerates using an enhanced prompt and the verifier's feedback
7. **Persistent Memory** — Successful answers are embedded using Amazon Titan V2 and indexed in OpenSearch for future reuse

The entire pipeline is orchestrated by AWS Step Functions — a 13-state machine that handles routing, error recovery, retries, and parallel execution.

## Why These Specific Technology Choices

Every architectural decision in Agentic Mesh was driven by a specific constraint or observation. Here are the ones that matter:

### Llama 3 8B for Routing (Not Claude, Not GPT)

The broker agent's job is classification: "Is this a coding task, a research task, or a summarization task?" and "Is this low, medium, or high complexity?"

This is fundamentally a categorization problem. You don't need a 200B-parameter model for it. Llama 3 8B Instruct handles it with >95% accuracy at a cost of ~$0.0003 per call — roughly 10x cheaper than using Claude Haiku for the same task.

The broker's entire prompt produces a JSON response:
```json
{"task_type": "coding", "complexity": "medium", "reasoning": "The task asks to implement binary search, which is a well-known algorithm"}
```

Fast, cheap, accurate. The right tool for the job.

### OpenSearch Serverless Over Pinecone or pgvector

I evaluated three vector stores:

- **Pinecone** — Excellent product, but adds an external dependency. I wanted the entire system deployable through a single SAM template.
- **pgvector on RDS** — Requires a provisioned database. Defeats the serverless architecture.
- **OpenSearch Serverless** — AWS-native, scales to zero, supports KNN with cosine similarity, deployable via CloudFormation.

OpenSearch Serverless won because it's the only option that maintains the fully-serverless, single-template-deploy constraint.

### Step Functions Over SQS Choreography

I initially considered a choreography-based approach where each Lambda publishes to the next SQS queue. This quickly became unmanageable because:

- Error handling requires dead-letter queues at every stage
- Retry logic is scattered across multiple functions
- There's no visual way to debug a failed execution
- Conditional routing (cache hit? verification passed?) requires additional coordination

Step Functions solved all of these with its built-in retry/catch blocks, Choice states for conditional routing, and the visual execution history in the AWS console. When a task fails at the verification stage, I can see exactly why in the execution timeline.

### Single Retry for Self-Correction (Not Two, Not Zero)

When verification fails, the system retries once with an enhanced prompt that includes the verifier's specific feedback. Why exactly one retry?

- **Zero retries:** ~15% of responses would be low quality with no recourse
- **One retry:** Fixes ~85% of initial failures, at the cost of one additional LLM call
- **Two retries:** Only catches an additional ~5% of failures, essentially doubling the correction cost for marginal gains

The economics clearly favor a single retry. The Step Functions state machine implements this as a simple Choice → Task → Pass flow.

## The Vector Cache: A Compounding Advantage

The vector cache is the most interesting part of the system from an economic perspective.

After every successful task (quality score ≥ 7.0), the system:

1. Generates a 1024-dimensional embedding of the task text using Amazon Titan Embeddings V2
2. Indexes the embedding, answer, agent type, quality score, and model in OpenSearch
3. On new tasks, performs a KNN search with a cosine similarity threshold of 0.85

If a cached answer exists with quality ≥ 7.0 and similarity ≥ 0.85, the system serves the cached answer directly — skipping the worker agent, verification, and self-correction entirely.

The threshold values (0.85 similarity, 7.0 quality) were tuned empirically. Lower similarity thresholds caused incorrect cache matches. Higher quality thresholds excluded too many valid cached answers.

The key insight is that this creates a **compounding advantage**:

— After 100 tasks: low cache hit rate (~5%)
— After 1,000 tasks: moderate hit rate (~15-20%)  
— After 10,000 tasks: significant hit rate (~30%+)

Each cache hit costs $0.0002 (just the embedding generation) versus $0.003-0.008 for a full pipeline execution. At scale, this dominates the cost equation.

## Cost Analysis

Here's a realistic breakdown for 1,000 tasks with a mix of types:

| Component | Without Mesh | With Mesh |
|-----------|-------------|-----------|
| Routing | $0 | $0.30 (Llama 3) |
| Coding (300 tasks) | $2.40 | $2.40 (same model) |
| Research (400 tasks) | $2.00 | $2.00 (same model) |
| Summarization (300 tasks) | $1.50 | $0.30 (Haiku instead of Sonnet) |
| Verification | $0 | $1.00 (mandatory quality check) |
| Correction (~15% fail) | $0 | $0.36 (retry cost) |
| Cache hits (~20%) | $0 | -$1.26 (200 tasks served for free) |
| **Total** | **$5.90** | **$5.10** |
| **With mature cache (40% hits)** | **$5.90** | **$3.50** |

At low cache hit rates, the savings come primarily from using Haiku for summarization. As the cache matures, the savings compound significantly.

## Observability

The system publishes custom CloudWatch metrics at every stage:

- `TaskRouted` — dimensions: agent type, cache hit, complexity
- `TaskCost` — dimensions: agent, model
- `WorkerLatency` — dimensions: agent
- `VerificationScore` — dimensions: agent
- `EscalationTriggered` — dimensions: original agent
- `CacheHitRate` — overall percentage

These feed into a pre-built CloudWatch dashboard that deploys automatically with the SAM template. I can see, at a glance, which agents are being used, what the quality distribution looks like, and where costs are concentrated.

All Lambda functions also use AWS Lambda Powertools for structured JSON logging and X-Ray for distributed tracing.

## Deployment

The entire system deploys from a single SAM template:

```bash
git clone https://github.com/shubham5027/Agentic-Mesh-AWS.git
cd agentic-mesh
pip install -r requirements.txt
sam build
sam deploy --guided
```

This provisions: API Gateway, 10 Lambda functions, Step Functions state machine, SQS queue, DynamoDB table, OpenSearch Serverless collection, CloudWatch dashboard, IAM roles, and Bedrock Guardrails.

No Docker. No Kubernetes. No servers. The entire infrastructure scales to zero when idle and scales up automatically under load.

## What's Next

The current system handles single-turn tasks well. The next priorities are:

1. **WebSocket streaming** — Real-time progress updates instead of polling
2. **Multi-modal input** — Images and PDFs via Claude's vision capabilities
3. **Agent collaboration** — Multi-step pipelines where agents build on each other's work
4. **A/B model testing** — Shadow evaluation to compare model performance on identical tasks
5. **Conversation memory** — Multi-turn sessions with context persistence

## Open Source

Agentic Mesh is MIT-licensed: [github.com/shubham5027/Agentic-Mesh-AWS](https://github.com/shubham5027/Agentic-Mesh-AWS)

If you're building multi-agent systems on AWS, I'd be interested in your experience. The issues tab is open for questions, bug reports, and feature requests.

---

*Shubham Kumbhar — Cloud Architect building at the intersection of AI and serverless infrastructure.*
