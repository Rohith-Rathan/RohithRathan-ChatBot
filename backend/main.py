
"""Trainer Central chatbot backend.
Claude selects read-only Dashboard API tools, the backend fetches the data,
and Claude generates the final answer.
"""

import json
import logging
import os
from pathlib import Path
from typing import Literal

import anthropic
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# --------------------------------------------------
# 1. Environment configuration
# --------------------------------------------------

# Load the root .env file.
# This file is one directory above the backend folder.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY is missing from environment variables.")

BASE_URL = os.getenv(
    "DASHBOARD_BASE_URL",
    "https://trainer-central-assigments-api.onrender.com"
).strip().rstrip("/")

# Use a valid Anthropic API model ID.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5").strip()

# Comma-separated frontend origins.
FRONTEND_ORIGINS = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:5173,"
    "https://rohith-rathan-chat-bot.vercel.app"
)

ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in FRONTEND_ORIGINS.split(",")
    if origin.strip()
]


# --------------------------------------------------
# 2. Logging
# --------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


# --------------------------------------------------
# 3. API clients
# --------------------------------------------------

client = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    timeout=60.0,
    max_retries=2
)

dashboard_session = requests.Session()


# --------------------------------------------------
# 4. FastAPI application and CORS
# --------------------------------------------------

app = FastAPI(
    title="Trainer Central Chatbot API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/")
def root():
    return {"status": "running", "service": "Trainer Central Chatbot API"}


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------
# 5. Claude system instructions
# --------------------------------------------------

SYSTEM = (
    "You are an assistant for the Trainer Central assignment dashboard. "
    "Answer questions about learners' assignment submissions using ONLY "
    "data returned by the available tools. Do not invent dashboard data. "
    "Each entry is a notification email. Learners may appear under several "
    "sender names, such as email, username, or full name. Match names "
    "carefully and explain when counts are combined or ambiguous. "
    "If dashboard data cannot be retrieved, clearly say that the data "
    "is temporarily unavailable. Be concise."
)


# --------------------------------------------------
# 6. Dashboard tools available to Claude
# --------------------------------------------------

TOOLS = [
    {
        "name": "get_learners",
        "description": (
            "GET /api/learners. Returns every learner with their "
            "submission count and last submission time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        },
    },
    {
        "name": "get_stats",
        "description": (
            "GET /api/stats. Returns total entries, top senders, "
            "and submissions per day."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        },
    },
    {
        "name": "list_entries",
        "description": (
            "GET /api/entries. Returns raw submission entries "
            "(id, text, sender, created_at). Use q to search text, "
            "such as a learner name or assignment topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Text to search for."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return."
                },
                "skip": {
                    "type": "integer",
                    "description": "Number of entries to skip."
                },
            },
            "additionalProperties": False
        },
    },
]


PATHS = {
    "get_learners": "/api/learners",
    "get_stats": "/api/stats",
    "list_entries": "/api/entries",
}


# --------------------------------------------------
# 7. Dashboard API request handler
# --------------------------------------------------

def call_dashboard(name: str, args: dict):
    """Call an approved read-only Dashboard API endpoint."""

    # Validate the requested tool.
    if name not in PATHS:
        raise ValueError(f"Unknown dashboard tool: {name}")

    if not isinstance(args, dict):
        raise ValueError("Invalid dashboard tool arguments.")

    params = None

    # Prepare query parameters only for list_entries.
    if name == "list_entries":
        params = {}

        query = args.get("q")
        if query is not None:
            params["q"] = str(query)

        try:
            limit = int(args.get("limit", 20))
            skip = int(args.get("skip", 0))
        except (TypeError, ValueError):
            raise ValueError("Invalid limit or skip parameter.")

        # Keep pagination values valid for the Dashboard API.
        params["limit"] = max(1, min(limit, 100))
        params["skip"] = max(0, skip)

    url = BASE_URL + PATHS[name]

    try:
        response = dashboard_session.get(
            url,
            params=params,
            timeout=(10, 90)
        )

        response.raise_for_status()

        data = response.json()

    except requests.Timeout:
        logger.exception("Dashboard API request timed out: %s", name)
        raise

    except requests.ConnectionError:
        logger.exception("Could not connect to Dashboard API: %s", name)
        raise

    except requests.HTTPError:
        logger.exception(
            "Dashboard API returned HTTP %s for %s",
            response.status_code,
            name
        )
        raise

    except requests.exceptions.JSONDecodeError:
        logger.exception("Dashboard API returned invalid JSON: %s", name)
        raise

    except requests.RequestException:
        logger.exception("Dashboard API request failed: %s", name)
        raise

    # Reduce entry text size before sending data to Claude.
    if name == "list_entries" and isinstance(data, dict):
        items = data.get("items", [])

        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    text = item.get("text")

                    if isinstance(text, str):
                        item["text"] = text[:500]

    return data


# --------------------------------------------------
# 8. Request data validation
# --------------------------------------------------

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


# --------------------------------------------------
# 9. Chat API endpoint
# --------------------------------------------------

@app.post("/api/chat")
def chat(req: ChatRequest):

    try:
        return run_chat(req)

    except anthropic.AuthenticationError:
        logger.exception("Anthropic authentication failed")

        raise HTTPException(
            status_code=502,
            detail="AI service authentication failed. Please contact support."
        )

    except anthropic.RateLimitError:
        logger.exception("Anthropic API rate limit reached")

        raise HTTPException(
            status_code=503,
            detail="AI service is temporarily busy. Please try again."
        )

    except anthropic.APITimeoutError:
        logger.exception("Anthropic API request timed out")

        raise HTTPException(
            status_code=504,
            detail="AI service took too long to respond. Please try again."
        )

    except anthropic.APIConnectionError:
        logger.exception("Could not connect to Anthropic API")

        raise HTTPException(
            status_code=502,
            detail="Could not connect to the AI service."
        )

    except anthropic.APIStatusError as exc:
        logger.exception(
            "Anthropic API returned HTTP %s",
            exc.status_code
        )

        raise HTTPException(
            status_code=502,
            detail="AI service returned an error. Please try again."
        )

    except anthropic.APIError:
        logger.exception("Anthropic API error")

        raise HTTPException(
            status_code=502,
            detail="AI service is temporarily unavailable."
        )

    except Exception:
        logger.exception("Unexpected chatbot backend error")

        raise HTTPException(
            status_code=500,
            detail="Something went wrong while processing your request."
        )


# --------------------------------------------------
# 10. Claude tool-use conversation loop
# --------------------------------------------------

def run_chat(req: ChatRequest):

    # Convert Pydantic objects into dictionaries for the Anthropic SDK.
    messages = [
        {
            "role": message.role,
            "content": message.content
        }
        for message in req.messages
    ]

    while True:

        # Send conversation to Claude.
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages
        )

        # Claude has completed its answer.
        if response.stop_reason != "tool_use":

            reply = "".join(
                block.text
                for block in response.content
                if block.type == "text"
            )

            return {"reply": reply}

        # Add Claude's tool-use response to conversation history.
        messages.append({
            "role": "assistant",
            "content": response.content
        })

        tool_results = []

        # Execute every tool requested by Claude.
        for block in response.content:

            if block.type != "tool_use":
                continue

            try:
                result = call_dashboard(
                    block.name,
                    block.input
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(
                        result,
                        ensure_ascii=False,
                        default=str
                    )
                })

            except Exception:
                logger.exception(
                    "Dashboard tool execution failed: %s",
                    block.name
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": (
                        "Dashboard data could not be retrieved. "
                        "Tell the user that this data is temporarily unavailable."
                    ),
                    "is_error": True
                })

        # Send tool results back to Claude.
        messages.append({
            "role": "user",
            "content": tool_results
        })
