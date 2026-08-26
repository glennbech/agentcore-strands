"""Dessertifier agent — you chat with it about savory dishes and it responds
with dessert versions that keep every original ingredient intact. Anchovies
stay anchovies, BBQ sauce stays BBQ sauce; they just get candied, whipped,
or folded into meringue. The result is meant to be absurd.

The agent is *conversational and stateful*, not a text-in/text-out endpoint:

- Every request carries a `session_id`. AgentCore Runtime routes all
  requests for the same session to the same container instance, so the
  Strands `Agent` object stays alive across turns and remembers what was
  said. Different `session_id` → different Agent → different memory.
- Two session-scoped tools make that memory *visible*:
    - `remember(fact)` persists a fact for the rest of the session
      (preferences, allergies, dislikes).
    - `recall()` returns everything the session has been told to remember.
  Because they show up in the returned `iterations` list, students can
  literally watch the agent write to and read from session memory.

Exposes the two HTTP endpoints AgentCore Runtime requires:

  GET  /ping          → health check
  POST /invocations   → body {"message": "...", "session_id": "..."}
                        → {"reply", "iterations": [{tool, input, output}, ...]}
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from strands import Agent, tool
from strands.models import BedrockModel


REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get(
    "MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)


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


# Model is expensive-ish to construct (initializes a boto3 client); Agents
# wrapping it are cheap. We keep one Agent per session_id so its message
# history — plus its session-scoped remember/recall closure — carries across
# turns for the same runtimeSessionId.
_model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
_sessions: dict[str, Agent] = {}
_session_memory: dict[str, list[str]] = {}


def _make_session_tools(session_id: str) -> list:
    """Build tools whose state is scoped to this session_id via closure.
    Different sessions get different memory lists, which is what makes the
    'same input, different session, different result' demo work."""
    memory = _session_memory.setdefault(session_id, [])

    @tool
    def remember(fact: str) -> str:
        """Persist a fact for the rest of this session. Use for user
        preferences, allergies, dislikes, or style choices you should honor
        in every subsequent recipe this session."""
        memory.append(fact)
        return f"remembered: {fact}"

    @tool
    def recall() -> list[str]:
        """Return every fact this session has been told to remember."""
        return list(memory)

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
