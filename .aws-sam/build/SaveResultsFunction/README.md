# 🕸️ Self-Optimizing Agentic Mesh for Heterogeneous Tasks

An AWS-native multi-agent system where a **Broker Agent** dynamically routes tasks to specialized **Worker Agents** based on real-time cost, performance, and cached task history. Features shadow verification, self-correction loops, and autonomous cost management.

---

## 🏗️ Architecture

```
Client → API Gateway → [API Handler λ] → SQS Queue
                                              ↓
                                       [Orchestrator λ]
                                              ↓
                                     ┌─ Step Functions ─┐
                                     │                   │
                                     │  Guardrail Check  │
                                     │        ↓         │
                                     │   Broker Agent    │ ←→ OpenSearch (Vector Cache)
                                     │    ↓    ↓    ↓   │
                                     │ Coder Researcher  │
                                     │       Summarizer  │
                                     │        ↓         │
                                     │  Verification     │ (Shadow Agent)
                                     │    ↓        ↓    │
                                     │ ✅ Pass  ❌ Fail  │
                                     │    ↓        ↓    │
                                     │  Save   Self-Correction → Save │
                                     └───────────────────┘
                                              ↓
                                     DynamoDB + CloudWatch
```

---

## 🧠 Key Features

| Feature | Implementation |
|---|---|
| **Broker-Based Routing** | Llama 3 8B predicts task type + complexity before routing to the cheapest capable agent |
| **Vector Task Cache** | OpenSearch Serverless stores task embeddings (Titan V2, 1024-d) — if a similar task was solved cheaply before, reuse that route |
| **Shadow Verification** | Every worker's output is independently graded by Claude Sonnet 4 (accuracy/completeness/relevance) |
| **Self-Correction Loop** | If verification fails (score < 7/10), the elite model re-generates with feedback context |
| **Bedrock Guardrails** | PII anonymization + prompt injection detection + content policy filtering before tasks reach workers |
| **Autonomous Cost Management** | Per-task cost tracking, model tier pricing, and CloudWatch dashboard with 6 cost/quality widgets |

---

## 📋 Prerequisites

- **AWS Account** with Bedrock model access enabled for:
  - Meta Llama 3 8B Instruct
  - Anthropic Claude 3 Haiku, Claude 3.5 Haiku, Claude Sonnet 4
  - Amazon Titan Embeddings V2
- **AWS SAM CLI** v1.100+ (`pip install aws-sam-cli`)
- **Python 3.10+**
- **AWS CLI** configured with appropriate credentials

---

## 🚀 Deployment

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Build & Deploy
```bash
sam build
sam deploy --guided
```

### 3. Note the outputs
After deployment, note the `ApiEndpoint` URL from the stack outputs.

---

## 📡 API Usage

### Submit a Task (PowerShell)
```powershell
Invoke-RestMethod -Uri "https://<api-id>.execute-api.us-east-1.amazonaws.com/Prod/task" -Method POST -ContentType "application/json" -Body '{"task": "Write a Python function for binary search", "type_hint": "coding"}'
```

### Poll for Results
```powershell
Invoke-RestMethod -Uri "https://<api-id>.execute-api.us-east-1.amazonaws.com/Prod/task/<task_id>"
```

---

## 📁 Project Structure

```
aws_mesh/
├── template.yaml                    # SAM infrastructure-as-code
├── samconfig.toml                   # Deployment config
├── requirements.txt                 # Python dependencies
├── events/
│   └── test_task.json               # Sample test event
├── src/
│   ├── handlers/
│   │   ├── api_handler.py           # API Gateway → SQS
│   │   ├── orchestrator.py          # SQS → Step Functions
│   │   ├── guardrail_handler.py     # Input safety filter
│   │   ├── broker.py                # Task routing brain
│   │   ├── worker_coder.py          # Coding specialist
│   │   ├── worker_researcher.py     # Research specialist
│   │   ├── worker_summarizer.py     # Summarization specialist
│   │   ├── verification_agent.py    # Shadow quality checker
│   │   ├── self_correction.py       # Escalation handler
│   │   └── save_results.py          # DynamoDB + cache persistence
│   ├── models/
│   │   ├── bedrock_client.py        # Shared Bedrock invocation
│   │   ├── cost_tracker.py          # Token economics
│   │   └── vector_memory.py         # OpenSearch k-NN cache
│   ├── guardrails/
│   │   └── guardrails.py            # PII + injection detection
│   ├── observability/
│   │   └── metrics.py               # CloudWatch metrics helper
│   └── state_machine/
│       └── definition.asl.json      # Step Functions FSM
└── tests/
    ├── test_guardrails.py           # Guardrails unit tests
    └── test_cost_tracker.py         # Cost tracker unit tests
```

---

## 💰 Cost Management

| Model | Input ($/1K tokens) | Output ($/1K tokens) | Used For |
|---|---|---|---|
| Llama 3 8B | $0.0003 | $0.0006 | Broker routing decisions |
| Claude 3 Haiku | $0.00025 | $0.00125 | Summarizer agent |
| Claude 3.5 Haiku | $0.0008 | $0.004 | Summarizer escalation |
| Claude Sonnet 4 | $0.003 | $0.015 | Coder + Verification + Self-correction |
| Titan Embeddings V2 | $0.0002 | — | Vector cache embeddings |