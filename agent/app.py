"""Dessertifier agent — turns a savory dish into a dessert version that keeps
every original ingredient intact. Anchovies stay anchovies, BBQ sauce stays
BBQ sauce — they just get candied, whipped, or folded into meringue. The
result is meant to be absurd.

Why this is a good demo of agents: the model will happily *claim* it kept
every ingredient, but often quietly drops the unpleasant ones. A
`check_ingredients` tool that greps the dessert recipe for every original
ingredient gives the model ground truth. If anything is missing, it rewrites
and checks again — that loop is what makes this an agent instead of a single
prompt.

Exposes the two HTTP endpoints AgentCore Runtime requires:

  GET  /ping          → health check
  POST /invocations   → body {"dish": "pizza"}
                        → {"dish", "recipe", "iterations": [{tool, input, output}, ...]}

`iterations` surfaces the agent's tool-call loop so callers can see it work.
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


@tool
def check_ingredients(recipe: str, ingredients: list[str]) -> dict:
    """Given a candidate dessert recipe and the list of ingredients from the
    ORIGINAL savory dish that must all still appear, return which are present
    and which are missing. Case-insensitive substring match. Use this as
    ground truth — do not trust your own scan of the recipe."""
    lower = recipe.lower()
    present = [i for i in ingredients if i.lower() in lower]
    missing = [i for i in ingredients if i.lower() not in lower]
    return {"present": present, "missing": missing}


SYSTEM_PROMPT = """You are Dessertifier. The user names a savory dish. You
reply with a DESSERT version of that dish. The twist: every core ingredient
of the original savory dish MUST appear in your dessert version. Tomatoes
stay tomatoes. Anchovies stay anchovies. BBQ sauce stays BBQ sauce. Ground
beef stays ground beef. You may candy, whip, chocolate-dip, caramelize,
fold into custard, layer with meringue, or otherwise coax them into dessert
form — but do not substitute them away. Lean into the absurdity.

Procedure:
1. List 5–10 core ingredients of the ORIGINAL savory dish.
2. Draft a dessert recipe titled "<Dish> Dessert" with two sections:
   Ingredients (list) and Method (numbered steps). Every ingredient from
   step 1 must appear literally in the Ingredients list of your draft.
3. Call `check_ingredients(recipe=<your full draft>, ingredients=<list from step 1>)`
   to verify. The tool is ground truth; your own scan is not.
4. If anything is missing, rewrite to include it in a dessert-appropriate
   way, then check again. Loop until nothing is missing.
5. Output only the final recipe — title, Ingredients section, Method
   section. No preamble, no tool trace, no meta commentary.
"""


# Model is expensive-ish to construct (initializes a boto3 client); the Agent
# wrapping it is cheap. Build one Agent per request so `agent.messages` is a
# clean per-invocation trace we can serialize back to the caller.
_model = BedrockModel(model_id=MODEL_ID, region_name=REGION)


def _new_agent() -> Agent:
    return Agent(model=_model, system_prompt=SYSTEM_PROMPT, tools=[check_ingredients])


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
    dish = body.get("dish")
    if not dish:
        return JSONResponse(
            {"error": "payload must include 'dish' (string)"},
            status_code=400,
        )

    agent = _new_agent()
    prompt = f"Give me a dessert version of: {dish}"
    result = str(agent(prompt)).strip()
    iterations = _extract_iterations(agent.messages)
    return JSONResponse({"dish": dish, "recipe": result, "iterations": iterations})
