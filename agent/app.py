"""Emojifier agent — rewrites text to contain an exact number of emojis.

Why this is a good demo of agents: LLMs cannot count reliably. To hit an
*exact* emoji count, the agent has to loop — rewrite the text, call a tool
that returns the ground-truth count, adjust, check again. That loop is what
makes this an agent instead of a single prompt.

Exposes the two HTTP endpoints AgentCore Runtime requires:

  GET  /ping          → health check
  POST /invocations   → body {"text": "...", "target": N}
                        → {"result": "...", "emoji_count": N, "target": N}
"""

import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from strands import Agent, tool
from strands.models import BedrockModel


REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get(
    "MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)

# Broad emoji regex — covers the ranges most emojis live in. Not perfectly
# exhaustive, but that's fine: what matters is that the *tool* is the source
# of truth and the model can't game it.
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


@tool
def count_emojis(text: str) -> int:
    """Return the exact number of emoji characters in `text`. Use this to
    verify every candidate rewrite — you cannot count reliably yourself."""
    return len(EMOJI_RE.findall(text))


SYSTEM_PROMPT = """You are Emojifier. The user gives you a piece of text and a
target number of emojis. Your job: return the same text, meaning intact,
containing EXACTLY that many emojis.

Rules:
- The user's meaning must survive. Do not paraphrase heavily; add/remove
  emojis, don't rewrite sentences.
- After every candidate rewrite, call the `count_emojis` tool on your
  candidate to check. The tool is ground truth; your own count is not.
- If the count is wrong, adjust (add or remove emojis) and check again.
- Keep looping until `count_emojis` returns EXACTLY the target.
- When you're done, output ONLY the final text. No preamble, no explanation,
  no tool trace.
"""


agent = Agent(
    model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
    system_prompt=SYSTEM_PROMPT,
    tools=[count_emojis],
)


app = FastAPI()


@app.get("/ping")
def ping():
    return {"status": "Healthy"}


@app.post("/invocations")
async def invoke(request: Request):
    body = await request.json()
    text = body.get("text")
    target = body.get("target")
    if not text or target is None:
        return JSONResponse(
            {"error": "payload must include 'text' (string) and 'target' (int)"},
            status_code=400,
        )

    prompt = f"Rewrite the following to contain exactly {target} emojis:\n\n{text}"
    result = str(agent(prompt)).strip()
    return JSONResponse({
        "result": result,
        "emoji_count": len(EMOJI_RE.findall(result)),
        "target": target,
    })
