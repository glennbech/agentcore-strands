# Emojifier — an AgentCore + Strands discovery exercise

Rewrite any piece of text to contain *exactly* N emojis. Sounds trivial —
until you remember LLMs cannot count. To hit an exact number the agent has
to loop: rewrite, ask a tool for the true count, adjust, check, done. That's
a real agent loop in about 40 lines of Python.

The task is silly so the fun bit is the plumbing: **you'll ship a real LLM
agent to AWS in about an hour.**

## What you'll actually learn

| Piece | What it is | Why it matters |
|---|---|---|
| **Strands Agents SDK** | Open-source Python framework by AWS. `model + system prompt + tools = agent`. The SDK runs the agent loop for you (call model → run any tools the model asks for → feed results back → repeat until model returns final answer). | This is how you write agent logic in 30 lines instead of 300. |
| **Amazon Bedrock AgentCore Runtime** | Serverless container host purpose-built for agents. ARM64 image, HTTP on port 8080, `POST /invocations` + `GET /ping`. | Someone else runs the container, handles auth, streams responses. You just push an image. |
| **Terraform (`aws` provider)** | Declarative infra. `aws_bedrockagentcore_agent_runtime` is the resource we deploy. | This is how you ship agents at work — not clickops in the console. See Step 4 for `aws` vs `awscc`. |
| **boto3 `bedrock-agentcore` client** | Data-plane SDK to call your deployed agent. | Any Python app (Lambda, backend, CLI) can now talk to your agent. |

By the end you'll have deployed a containerized agent, invoked it from the CLI,
watched the logs in CloudWatch, and torn it all down. Every piece here is what
teams actually use in production agent deployments.

---

## Prerequisites (5 min)

Before you touch anything:

1. **AWS account with credentials configured.**
   ```bash
   aws configure                     # region: us-east-1 is safest for Bedrock
   aws sts get-caller-identity       # sanity check
   ```

2. **Enable model access in Bedrock.**
   - Open the Bedrock console → **Model access** → **Modify model access**.
   - Enable **Anthropic Claude Haiku 4.5** in `us-east-1` (inference profile
     `us.anthropic.claude-haiku-4-5-20251001-v1:0`). If you prefer a different
     Haiku, override with `MODEL_ID=...` in your environment before starting
     the server.
   - Usually approved in under a minute. Without it, step 1 will fail with
     `AccessDeniedException` or `ValidationException: The provided model
     identifier is invalid`.

3. **Tools already in your Codespace** (this workshop assumes GitHub Codespaces
   — everything below is preinstalled there). Sanity-check:
   ```bash
   docker buildx version    # buildx (for ARM64 builds)
   terraform version        # >= 1.6
   python3 --version        # >= 3.11
   aws --version             # v2.15+ (needed for `bedrock-agentcore-control`)
   ```
   If you're *not* on a Codespace and one of these is missing or old, install
   from the official source (AWS CLI: <https://aws.amazon.com/cli/> — do **not**
   `pip install awscli`, that gives you v1 and breaks `aws logs tail`).

4. **Open this repo in your Codespace.**

> **Cost warning:** ECR storage is cents. AgentCore charges per invocation.
> Claude Haiku is ~$0.0001 per short call. If you finish the exercise and run
> the cleanup at the end, total spend is under $0.10. **Don't skip cleanup.**

---

## The mental model (2 min — read this before you start)

```
   ┌──────────────────────┐        invoke_agent_runtime          ┌─────────────────────────┐
   │  Your laptop / CI    │  ──────────────────────────────────▶ │  AgentCore Runtime      │
   │  (boto3 client)      │                                      │  (serverless container) │
   └──────────────────────┘                                      │                         │
                                                                 │  ┌───────────────────┐  │
                                                                 │  │ your container    │  │
                                                                 │  │  FastAPI          │  │
                                                                 │  │   /ping           │  │
                                                                 │  │   /invocations ── │──┼──┐
                                                                 │  └───────────────────┘  │  │
                                                                 └─────────────────────────┘  │
                                                                                              ▼
                                                                                    ┌────────────────┐
                                                                                    │ Strands Agent  │
                                                                                    │   ↓ InvokeModel│
                                                                                    │ Bedrock Haiku  │
                                                                                    └────────────────┘
```

Your container just needs to speak HTTP on `POST /invocations` and `GET /ping`.
Everything else — TLS, auth, logging, autoscaling — is AgentCore's problem.
We use **FastAPI** for those two endpoints and **Strands** as the agent brain
behind them.

---

## Step 1 — Build the agent locally (20 min)

The whole agent is in [`agent/app.py`](agent/app.py). Read it — it's under 60
lines. The important bits:

- `FastAPI()` with two routes: `GET /ping` (health check) and `POST /invocations`
  (the actual work). These are the two routes AgentCore requires.
- `Agent(model=..., system_prompt=..., tools=[...])` — the Strands agent object.
- `@tool` — decorator that exposes a plain Python function to the LLM. The
  docstring is what the model sees when deciding whether to call the tool.

### Run it locally

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app:app --host 0.0.0.0 --port 8080 &

# Give it a second to boot, then:
curl -s http://localhost:8080/ping                    # {"status":"Healthy"}

curl -s -X POST http://localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -d '{"text": "The quarterly report is due Friday.", "target": 5}' | jq .
```

You should get back a JSON object with `result`, `emoji_count`, and `target` —
and `emoji_count` should equal `target`. If it does, the agent successfully
looped through the `count_emojis` tool until it hit the mark. Troubleshooting:

- **`AccessDeniedException`** — model access isn't enabled (prereqs, step 2).
- **`ResourceNotFoundException: Model use case details have not been submitted`**
  — for Anthropic models on a fresh AWS account you must fill in the Anthropic
  use-case form from the Bedrock console (Model access → Anthropic → Available
  to request → fill form). Approval can take up to 15 minutes. If your account
  can't get that approved right now, temporarily point at an Amazon-owned model
  such as Nova micro with `MODEL_ID=us.amazon.nova-micro-v1:0 uvicorn ...`.
  The same fallback applies to the *deployed* runtime in Step 4 — pass the
  override as a Terraform variable:
  `terraform apply -var 'model_id=us.amazon.nova-micro-v1:0'`.
- **`ValidationException: The provided model identifier is invalid`** — the
  model ID is wrong or that inference profile isn't ACTIVE in your region.
  Run `aws bedrock list-inference-profiles --region us-east-1` to see valid
  IDs.
- **`NoCredentialsError`** — `aws configure` didn't take; check `~/.aws/credentials`.
- **Port 8080 in use** — `lsof -ti:8080 | xargs kill`.

### Poke at it more

```bash
for pair in '0:"Rip out every emoji: 🎉🎉 party time 🎉🎉"' \
            '3:"Standup at 10, then coffee, then real work."' \
            '10:"Deploy the new API to production."'; do
  target="${pair%%:*}"; text="${pair#*:}"
  curl -s -X POST http://localhost:8080/invocations \
    -H 'Content-Type: application/json' \
    -d "{\"text\": $text, \"target\": $target}" | jq .
  echo "---"
done
```

Watch the uvicorn logs — you'll see the model call `count_emojis` multiple
times per invocation, adjusting until the count matches. That's the agent
loop, running locally, exactly the same as it will on AgentCore.

Kill the local server before moving on. Because we launched it with `&`, the
easiest way is:

```bash
lsof -ti:8080 | xargs kill
```

(`fg` also works if the job is still attached to the current shell; on a fresh
shell it won't be.)

---

## Step 2 — Create the ECR repo (2 min)

AgentCore pulls your container from ECR, so the repo has to exist before we push
an image (and before Terraform can look it up).

```bash
aws ecr create-repository \
  --repository-name emojifier-agent \
  --region us-east-1
```

Confirm:

```bash
aws ecr describe-repositories --repository-names emojifier-agent \
  --region us-east-1 \
  --query 'repositories[0].repositoryUri' --output text
```

> Why not create the ECR repo in Terraform? Because the runtime resource needs
> to reference an image *digest* that already exists. Building the image would
> then depend on ECR, and Terraform would depend on the built image — that's a
> circular dependency in a single stack. Externally-managed ECR keeps the flow
> linear: **repo → push image → terraform apply**.

---

## Step 3 — Build and push the ARM64 image (10 min)

**AgentCore Runtime only accepts `linux/arm64` images.** Codespaces are x86_64,
so we use `docker buildx` with QEMU emulation. The first build is slow (~3–5 min
because layers are cold); subsequent ones are fast.

```bash
cd ..
./scripts/build-and-push.sh
```

The script:
1. Logs docker into ECR with a temporary token.
2. Creates a buildx builder (idempotent).
3. Runs `docker buildx build --platform linux/arm64 --push`.

Verify the image made it:

```bash
aws ecr list-images --repository-name emojifier-agent --region us-east-1
```

You'll actually see **three** digests: the manifest list (which is what
`:latest` points at), the arm64 manifest, and an attestation manifest that
buildx pushes alongside. Only the manifest list has a tag. That's normal for
`buildx --push`; not a bug.

---

## Step 4 — Deploy the AgentCore Runtime (10 min)

Now Terraform can reference an image that actually exists.

```bash
cd terraform
terraform init     # first init downloads ~1 GB of providers; grab a coffee
terraform apply
```

### A note on providers: `aws` vs `awscc`

AgentCore is a very new AWS service. When brand-new services launch, they
usually appear first in the **`hashicorp/awscc`** provider (auto-generated
from CloudFormation schemas), and only later in the handwritten
**`hashicorp/aws`** provider that most people are used to. For a while
`awscc` was the only way to manage AgentCore from Terraform. The `aws`
provider has since caught up (`aws_bedrockagentcore_agent_runtime` and ~20
sibling resources), and it's what we use here.

Why prefer the handwritten `aws` provider when both work:

- Cleaner HCL syntax (block form vs `awscc`'s nested-object assignment).
- Built-in retry for common gotchas — including **IAM eventual consistency**,
  which we would otherwise have to work around with a `time_sleep` block.
- Better documentation and community examples.

Keep `awscc` in your back pocket for the *next* new AWS service that
appears — the pattern (aws lags, awscc leads, aws catches up) repeats.

### While apply runs, read `main.tf`. Pay attention to:

- **The trust policy** on `aws_iam_role.runtime` — only the AgentCore service
  (`bedrock-agentcore.amazonaws.com`) can assume the role, and only when the
  request comes from your own AWS account (`aws:SourceAccount`) and is acting
  on an AgentCore resource in your account (`aws:SourceArn`). In plain English:
  we're saying *"AgentCore, you can use this role, but only when you're doing
  something for me"* — that stops another AWS customer from tricking AgentCore
  into using our role for their agent (a class of bug called the
  "confused-deputy problem").
- **The image URI** — pinned by *digest*, not tag. When you re-push a new image,
  the `data.aws_ecr_image` re-reads the digest and Terraform sees a diff, which
  forces a runtime update. If you used `:latest` directly, Terraform would never
  notice.
- **`bedrock-agentcore:GetWorkloadAccessToken*`** — required so the runtime can
  talk to the AgentCore control plane on your behalf.

Apply usually takes ~2 min (runtime provisioning). If it fails with
`CREATE_FAILED`, the usual culprits are:

- Image is x86_64 not arm64 — re-run `build-and-push.sh` after fixing.
- Runtime role missing ECR perms — check the ECR statements in `main.tf`.
- Runtime name collision — change `name` in `variables.tf`.
- **`Role validation failed ... trust policy allows assumption by this
  service`** — an IAM propagation race. The `aws` provider retries this
  internally, but if it exhausts retries just run `terraform apply` again.

When it finishes:

```bash
terraform output
```

Copy the `agent_runtime_arn` value. That's what you invoke.

---

## Step 5 — Invoke the deployed agent (5 min)

```bash
cd ..
# Re-activate the venv you made in Step 1 if you're in a fresh shell:
#   source agent/.venv/bin/activate
pip install --upgrade boto3     # need a recent version for bedrock-agentcore
python3 scripts/invoke.py 5 "The quarterly report is due Friday."
python3 scripts/invoke.py 0 "Rip out every emoji: 🎉🎉 party time 🎉🎉"
python3 scripts/invoke.py 12 "Deploy the new API to production this afternoon."
```

`invoke.py` reads the ARN from `terraform output` automatically. Note the
`--session` flag it accepts — AgentCore requires a session id ≥ 33 characters,
and the script pads short names for you.

> **Gotcha:** a session id is sticky to the runtime version it first hit. If
> you re-`apply` Terraform with a new `model_id` (or otherwise create a new
> runtime version) and immediately re-invoke with the same `--session` value,
> the request may still be routed to the *previous* container. Pass a fresh
> `--session my-new-name` after any config change to force a new container.

Tail the CloudWatch logs in another shell while you invoke. The log group
includes the runtime's short ID (from `terraform output agent_runtime_id`)
plus `-DEFAULT`:

```bash
RUNTIME_ID=$(cd terraform && terraform output -raw agent_runtime_id)
aws logs tail "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT" \
  --region us-east-1 --follow
```

You'll see the FastAPI request line, the Strands model call, the tool
invocation, and the response — the exact same log stream you'd get running it
locally, just on someone else's box.

---

## Step 6 — Clean up (5 min) **DO NOT SKIP**

```bash
cd terraform
terraform destroy
```

Then delete the ECR repo (Terraform doesn't own it):

```bash
aws ecr delete-repository \
  --repository-name emojifier-agent \
  --region us-east-1 \
  --force
```

Confirm nothing is left:

```bash
aws bedrock-agentcore-control list-agent-runtimes \
  --region us-east-1 \
  --query 'agentRuntimes[].agentRuntimeName'
aws ecr describe-repositories \
  --region us-east-1 \
  --query 'repositories[].repositoryName' 2>/dev/null
```

Neither should contain anything starting with `emojifier`.

---

## Extensions (if you finish early)

Pick one, get it working, share with the class:

1. **Add a second tool.** Write `@tool def sweetness_score(text: str) -> int`
   returning 1–10, and update the system prompt so the agent includes the score
   in the title.
2. **Swap the model.** `main.tf` passes `MODEL_ID` as an env var to the
   container, and the `model_id` Terraform variable controls it. Try
   `terraform apply -var 'model_id=us.anthropic.claude-sonnet-4-5-20250929-v1:0'`
   — no rebuild needed. Compare quality vs. latency vs. cost.
3. **Stream responses.** FastAPI supports `StreamingResponse`; Strands supports
   `agent.stream_async(...)`. Change the invoke script to print tokens as they
   arrive.
4. **Add AgentCore Memory.** Remember every text the user has ever emojified
   and use their previous emoji choices as context. See the
   [AgentCore Memory docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html).
5. **Add a second toy agent.** A `Dejargonizer` (rewrites corporate-speak into
   plain English, tool: `jargon_density(text)`, loop until below threshold).
   Same pattern, ten minutes. Second Docker image, second runtime resource in
   Terraform.

---

## Reference — file layout

```
.
├── README.md
├── agent/
│   ├── app.py                  # FastAPI + Strands agent
│   ├── requirements.txt
│   └── Dockerfile              # ARM64, uvicorn on :8080
├── terraform/
│   ├── versions.tf             # aws provider (~> 6.0)
│   ├── variables.tf
│   ├── main.tf                 # IAM role, AgentCore Runtime, image lookup
│   └── outputs.tf
├── scripts/
│   ├── build-and-push.sh       # buildx → ECR
│   └── invoke.py               # boto3 call to the deployed runtime
└── .gitignore
```

## Reference — docs to read after class

- Strands Agents: <https://strandsagents.com/>
- AgentCore Runtime: <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html>
- `awscc` Terraform provider: <https://registry.terraform.io/providers/hashicorp/awscc/latest/docs>
- Bedrock model catalog: <https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html>
