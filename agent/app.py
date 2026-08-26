"""Dessertifier agent — you chat with it about savory dishes and it responds
with dessert versions that keep every original ingredient intact. Anchovies
stay anchovies, BBQ sauce stays BBQ sauce; they just get candied, whipped,
or folded into meringue. The result is meant to be absurd.

The agent is *conversational and stateful*, but the durable state lives in
**AgentCore Memory**, not in this container. That distinction matters:

- Every request carries a `session_id`. AgentCore Runtime routes all
  requests for the same session to the same container instance, so the
  Strands `Agent` object (with its in-flight message history) can stay
  alive across a chat's turns.
- The user's *facts* (allergies, dislikes, style choices) go through two
  session-scoped tools: `remember(fact)` writes a `CreateEvent` to
  AgentCore Memory keyed by session_id; `recall()` reads them back via
  `ListEvents`. When the container recycles, the Strands Agent is
  reconstructed, but every remembered fact survives.
- Because the tools appear in the returned `iterations` list, students
  can literally watch the agent write to and read from AgentCore Memory.

Exposes the two HTTP endpoints AgentCore Runtime requires:

  GET  /ping          → health check
  POST /invocations   → body {"message": "...", "session_id": "..."}
                        → {"reply", "iterations": [{tool, input, output}, ...]}
"""

import os
from datetime import datetime, timezone

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from strands import Agent, tool
from strands.models import BedrockModel


REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get(
    "MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
MEMORY_ID = os.environ.get("MEMORY_ID")  # injected by Terraform


SYSTEM_PROMPT = """You are Dessertifier. You chat with the user about savory
dishes and reply with DESSERT versions of them. Every core ingredient of the
original savory dish should appear in your dessert version. Tomatoes stay
tomatoes. Anchovies stay anchovies. BBQ sauce stays BBQ sauce. Ground beef
stays ground beef. You may candy, whip, chocolate-dip, caramelize, fold into
custard, layer with meringue, or otherwise coax them into dessert form — but
do not substitute them away. Lean into the absurdity.

You have session-scoped memory via two tools:
- `remember(fact)`: whenever the user shares a preference, allergy, dislike,
  or style choice ("I'm allergic to nuts", "I hate coconut", "keep it dark
  chocolate"), immediately call `remember(fact=<concise fact>)`.
- `recall()`: BEFORE drafting any recipe, call `recall()` to see everything
  this session has been told to remember. Honor every constraint you find —
  never include ingredients the user is allergic to or has said they dislike.

Procedure when the user names a dish:
1. Call `recall()` to load session memory.
2. Reply with a recipe titled "<Dish> Dessert" with two sections: Ingredients
   (every core ingredient of the savory original, minus anything memory says
   to avoid) and Method (numbered steps). No preamble, no meta commentary.

When the user asks to revise a prior recipe ("more caramelly", "add
pistachio"), output the full revised recipe under the same rules.

When the user just chats or shares preferences without asking for a recipe,
respond in one or two conversational sentences after calling `remember` if
appropriate.
"""


# The BedrockModel wraps a boto3 client (expensive-ish to construct). Agents
# wrapping it are cheap. We keep one Agent per session_id for the *current
# conversation's* in-flight message history — that's ephemeral. Durable facts
# live in AgentCore Memory, retrieved through remember/recall.
_model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
_memory = boto3.client("bedrock-agentcore", region_name=REGION)
_sessions: dict[str, Agent] = {}

# Local-dev fallback: if MEMORY_ID is unset (e.g. `uvicorn app:app` on a
# laptop with no memory resource provisioned), keep facts in a plain dict so
# the workshop's Step 1 local test still exercises remember/recall. In the
# deployed runtime MEMORY_ID is always set by Terraform.
_local_facts: dict[str, list[str]] = {}


def _remember_fact(session_id: str, fact: str) -> None:
    if not MEMORY_ID:
        _local_facts.setdefault(session_id, []).append(fact)
        return
    _memory.create_event(
        memoryId=MEMORY_ID,
        actorId=session_id,
        sessionId=session_id,
        eventTimestamp=datetime.now(timezone.utc),
        payload=[{"conversational": {"role": "USER", "content": {"text": fact}}}],
    )


def _recall_facts(session_id: str) -> list[str]:
    if not MEMORY_ID:
        return list(_local_facts.get(session_id, []))
    resp = _memory.list_events(
        memoryId=MEMORY_ID,
        actorId=session_id,
        sessionId=session_id,
        includePayloads=True,
        maxResults=100,
    )
    facts: list[str] = []
    for event in resp.get("events", []):
        for item in event.get("payload", []) or []:
            conv = item.get("conversational")
            if conv:
                text = (conv.get("content") or {}).get("text")
                if text:
                    facts.append(text)
    return facts


def _make_session_tools(session_id: str) -> list:
    """Build tools whose calls carry the session_id via closure. Both tools
    hit the AgentCore Memory data plane — no in-container fact storage."""

    @tool
    def remember(fact: str) -> str:
        """Persist a fact about this session's user (preference, allergy,
        dislike, style choice) into AgentCore Memory. It survives container
        recycles and is retrievable via `recall` for the rest of the session."""
        _remember_fact(session_id, fact)
        return f"remembered: {fact}"

    @tool
    def recall() -> list[str]:
        """Return every fact this session has stored via `remember`, pulled
        from AgentCore Memory."""
        return _recall_facts(session_id)

    return [remember, recall]


def _new_agent(session_id: str) -> Agent:
    return Agent(
        model=_model,
        system_prompt=SYSTEM_PROMPT,
        tools=_make_session_tools(session_id),
    )


def _extract_iterations(messages: list[dict]) -> list[dict]:
    """Pair up toolUse blocks (assistant) with matching toolResult blocks
    (user) to reconstruct the loop. Returns a list ordered by call, each
    element {tool, input, output}."""
    pending: dict[str, dict] = {}
    iterations: list[dict] = []
    for msg in messages:
        for block in msg.get("content", []) or []:
            if "toolUse" in block:
                tu = block["toolUse"]
                pending[tu["toolUseId"]] = {"tool": tu["name"], "input": tu.get("input", {})}
            elif "toolResult" in block:
                tr = block["toolResult"]
                call = pending.pop(tr["toolUseId"], None)
                if call is None:
                    continue
                outputs = []
                for c in tr.get("content", []) or []:
                    if "json" in c:
                        outputs.append(c["json"])
                    elif "text" in c:
                        outputs.append(c["text"])
                call["output"] = outputs[0] if len(outputs) == 1 else outputs
                iterations.append(call)
    return iterations


app = FastAPI()


@app.get("/ping")
def ping():
    return {"status": "Healthy"}


@app.post("/invocations")
async def invoke(request: Request):
    body = await request.json()
    message = body.get("message")
    session_id = body.get("session_id")

    if not (message and session_id):
        return JSONResponse(
            {"error": "payload must include 'message' (string) and 'session_id' (string)"},
            status_code=400,
        )

    agent = _sessions.setdefault(session_id, _new_agent(session_id))
    before = len(agent.messages)
    reply = str(agent(message)).strip()
    iterations = _extract_iterations(agent.messages[before:])
    return JSONResponse({"reply": reply, "iterations": iterations})
