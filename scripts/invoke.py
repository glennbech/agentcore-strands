#!/usr/bin/env python3
"""Invoke the deployed AgentCore runtime and print the agent loop.

The point of this exercise is that it's an AGENT, not a single LLM call.
This script surfaces that by printing each tool-call iteration the agent
took before it settled on a final recipe. You'll see the model draft,
call `check_ingredients`, get told what's missing, redraft, call again,
and so on until the tool reports nothing missing — then the final recipe.

Usage:
    python3 scripts/invoke.py "pizza"
    python3 scripts/invoke.py "beef bourguignon"
    python3 scripts/invoke.py "bbq ribs" --session mysession
    AGENT_RUNTIME_ARN=arn:aws:... python3 scripts/invoke.py "caesar salad"
"""

import argparse
import json
import os
import subprocess
import sys

import boto3


def get_runtime_arn() -> str:
    if env := os.environ.get("AGENT_RUNTIME_ARN"):
        return env
    out = subprocess.check_output(
        ["terraform", "-chdir=terraform", "output", "-raw", "agent_runtime_arn"],
        text=True,
    )
    return out.strip()


def pad_session(name: str) -> str:
    # AgentCore requires runtimeSessionId to be at least 33 characters.
    return (name + "-" * 33)[:33] if len(name) < 33 else name


def print_iterations(iterations: list[dict]) -> None:
    if not iterations:
        print("(agent returned without calling any tools)", file=sys.stderr)
        return
    print(f"Agent loop — {len(iterations)} tool call(s):", file=sys.stderr)
    for i, it in enumerate(iterations, 1):
        tool = it.get("tool", "?")
        output = it.get("output", {})
        # Compact per-call summary suited to check_ingredients output.
        if isinstance(output, dict) and "missing" in output:
            missing = output["missing"]
            status = "all present ✓" if not missing else f"missing: {missing}"
            print(f"  {i}. {tool} → {status}", file=sys.stderr)
        else:
            print(f"  {i}. {tool} → {json.dumps(output)}", file=sys.stderr)
    print("", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dish", help="Name of the savory dish to dessertify.")
    p.add_argument("--session", default="default",
                   help="Logical session name; padded to 33 chars if shorter.")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = p.parse_args()

    arn = get_runtime_arn()
    session_id = pad_session(args.session)

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        payload=json.dumps({"dish": args.dish}).encode("utf-8"),
    )
    body = resp["response"].read().decode("utf-8")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(body)
        return

    if "recipe" in data:
        print_iterations(data.get("iterations", []))
        print(data["recipe"])
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"failed to read terraform output: {e}")
