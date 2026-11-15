"""Simple chatbot backend: Claude decides which Dashboard API to call, we fetch the data, Claude answers."""
import json
import os
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_URL = os.getenv("DASHBOARD_BASE_URL", "https://trainer-central-assigments-api.onrender.com").rstrip("/")
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYSTEM = (
    "You are an assistant for the Trainer Central assignment dashboard. Answer questions about learners' "
    "assignment submissions using ONLY data from the tools. Each entry is a notification email; learners "
    "may appear under several sender names (email, username, full name), so match names loosely and say so "
    "when counts are combined or ambiguous. Be concise."
)

# Read-only Dashboard endpoints exposed to the LLM.
TOOLS = [
    {
        "name": "get_learners",
        "description": "GET /api/learners: every learner with their submission count and last submission time.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_stats",
        "description": "GET /api/stats: total entries, top senders, and submissions per day.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_entries",
        "description": "GET /api/entries: raw submission entries (id, text, sender, created_at). "
        "Use q to search text (e.g. a learner name or assignment topic).",
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "search text"},
                "limit": {"type": "integer", "description": "max items, default 20"},
                "skip": {"type": "integer"},
            },
        },
    },
]

PATHS = {"get_learners": "/api/learners", "get_stats": "/api/stats", "list_entries": "/api/entries"}


def call_dashboard(name: str, args: dict):
    params = None
    if name == "list_entries":
        params = {k: v for k, v in args.items() if v not in (None, "")}
        params["limit"] = min(int(params.get("limit", 20)), 100)
    r = requests.get(BASE_URL + PATHS[name], params=params, timeout=90)
    r.raise_for_status()
    data = r.json()
    if name == "list_entries":  # keep the LLM context small
        for it in data.get("items", []):
            it["text"] = it["text"][:500]
    return data


class ChatRequest(BaseModel):
    messages: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        return run_chat(req)
    except anthropic.AuthenticationError:
        return {"reply": "The Anthropic API key is invalid. Update ANTHROPIC_API_KEY in .env and restart the backend.", "error": True}
    except Exception as e:
        return {"reply": f"Backend error: {e}", "error": True}


def run_chat(req: ChatRequest):
    messages = [{"role": m["role"], "content": m["content"]} for m in req.messages]
    for _ in range(5):  # tool loop
        resp = client.messages.create(
            model=MODEL, max_tokens=1024, system=SYSTEM, tools=TOOLS, messages=messages
        )
        if resp.stop_reason != "tool_use":
            return {"reply": "".join(b.text for b in resp.content if b.type == "text")}
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                try:
                    out = json.dumps(call_dashboard(b.name, b.input))
                except Exception as e:
                    out = f"Error calling dashboard: {e}"
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
        messages.append({"role": "user", "content": results})
    return {"reply": "Sorry, I could not finish that request."}
