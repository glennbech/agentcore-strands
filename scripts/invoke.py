#!/usr/bin/env python3
"""Send one message to the deployed Dessertifier agent and print the reply.

For a back-and-forth conversation (where the agent remembers what you told
it), use scripts/recipechat.py instead. This script generates a fresh
session per invocation, so nothing persists between runs.

Usage:
    python3 scripts/invoke.py "give me a pizza dessert"
    python3 scripts/invoke.py "I hate coconut. Make me a bbq ribs dessert."
    python3 scripts/invoke.py "beef bourguignon dessert please" --session mysession
    AGENT_RUNTIME_ARN=arn:aws:... python3 scripts/invoke.py "caesar salad"
"""

import argparse
import json
import os
import subprocess
import sys
import uuid

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


def format_iteration(tool: str, output) -> str:
    if tool == "check_ingredients" and isinstance(output, dict) and "missing" in output:
        missing = output["missing"]
        return "all present ✓" if not missing else f"missing: {missing}"
    if tool == "remember":
        return str(output)
    if tool == "recall":
        if isinstance(output, list):
            return f"recalled {len(output)} fact(s): {output}" if output else "nothing remembered yet"
    return json.dumps(output)


def print_iterations(iterations: list[dict]) -> None:
    if not iterations:
        print("(agent replied without calling any tools)", file=sys.stderr)
        return
    print(f"Agent loop — {len(iterations)} tool call(s):", file=sys.stderr)
    for i, it in enumerate(iterations, 1):
        tool = it.get("tool", "?")
        status = format_iteration(tool, it.get("output", {}))
        print(f"  {i}. {tool} → {status}", file=sys.stderr)
    print("", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("message", help="Message to send to the agent.")
    p.add_argument("--session", default=None,
                   help="Session name (auto-generated per call if omitted).")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = p.parse_args()

    session_id = pad_session(args.session or f"oneshot-{uuid.uuid4().hex}")
    arn = get_runtime_arn()

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        payload=json.dumps({"message": args.message, "session_id": session_id}).encode("utf-8"),
    )
    body = resp["response"].read().decode("utf-8")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(body)
        return

    if "reply" in data:
        print_iterations(data.get("iterations", []))
        print(data["reply"])
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"failed to read terraform output: {e}")
