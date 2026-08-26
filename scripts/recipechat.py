#!/usr/bin/env python3
"""Chat REPL for the deployed Dessertifier agent.

Multi-turn: the container keeps one Agent alive per session_id, so the
agent remembers what you've already asked. Try:

    you > pizza
    ... (recipe) ...
    you > make it more caramelly
    ... (revised recipe) ...
    you > now do bbq ribs
    ...

Ctrl-D or 'exit' to quit.

Usage:
    python3 scripts/recipechat.py
    python3 scripts/recipechat.py --session mysession
    AGENT_RUNTIME_ARN=arn:aws:... python3 scripts/recipechat.py
"""

import argparse
import json
import os
import readline  # noqa: F401  (enables arrow-key history in input())
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
        return
    print(f"[agent loop: {len(iterations)} tool call(s)]", file=sys.stderr)
    for i, it in enumerate(iterations, 1):
        tool = it.get("tool", "?")
        status = format_iteration(tool, it.get("output", {}))
        print(f"  {i}. {tool} → {status}", file=sys.stderr)
    print("", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", default=None,
                   help="Session name (auto-generated UUID if omitted).")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = p.parse_args()

    session_id = pad_session(args.session or f"chat-{uuid.uuid4().hex}")
    arn = get_runtime_arn()
    client = boto3.client("bedrock-agentcore", region_name=args.region)

    print(f"session: {session_id}")
    print("Type a dish, or a follow-up ('more caramelly', 'add pistachio').")
    print("Ctrl-D or 'exit' to quit.\n")

    while True:
        try:
            msg = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg.lower() in ("exit", "quit"):
            break

        payload = json.dumps({"message": msg, "session_id": session_id}).encode("utf-8")
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session_id,
            payload=payload,
        )
        body = resp["response"].read().decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print(body)
            continue

        print_iterations(data.get("iterations", []))
        print(data.get("reply", data))
        print()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"failed to read terraform output: {e}")
