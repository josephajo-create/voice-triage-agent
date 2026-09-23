"""
Voice Triage Agent: turn a caller's conversation into ONE structured ticket.
Test on its own:  python triage.py "I can't log in since the update"
"""
import os
import sys
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()  # reads OPENAI_API_KEY from .env
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

CATEGORIES = ["Login/Access", "Billing", "Bug/Error", "Performance",
              "Feature Request", "Account Change", "Other"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]

SYSTEM_PROMPT = f"""You are a voice customer-support triage agent. The input is the
live transcript of a phone call so far, one line per turn, labelled "Caller:" or
"Agent:" (Agent lines are what you already said out loud).
- Caller turns are often fragments of ONE sentence, split by pauses. Read them together.
- Speech-to-text errors are likely. Use context to infer the intended words
  (e.g. "claimed them all" after "I have a" probably means "client demo").
- Produce ONE ticket for the caller's overall issue, using ALL details so far.
  Later details (deadlines, error messages, device) should refine the ticket.

Decide if the call so far describes a support issue. If it is only a greeting or small talk,
set is_support_issue=false and keep other fields minimal.

Otherwise create a ticket:
- category: one of {CATEGORIES}
- priority rules:
  Critical = service down for many users, data loss, security or payment failure
  High     = user fully blocked, or urgent deadline mentioned
  Medium   = partially blocked or workaround exists
  Low      = question, cosmetic issue, or feature request
- summary: one line, max 12 words, ticket-title style
- missing_info: at most 3 short questions to ask the caller to complete the
  ticket. Never ask for something the caller already said. Empty list if complete.
- agent_reply: what you say NEXT, spoken aloud by text-to-speech.
  Max 2 short, natural sentences. No lists, symbols or markdown.
  * Not a support issue yet: greet briefly and ask what the problem is.
  * Info missing: briefly acknowledge, then ask ONLY the single most important
    missing question. Don't repeat a question the Agent already asked unless the
    caller clearly didn't answer it.
  * Nothing important missing: confirm the ticket in plain words (issue and
    priority) and say the support team will follow up shortly.
"""

TICKET_SCHEMA = {
    "name": "support_ticket",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["is_support_issue", "category", "priority", "summary",
                     "missing_info", "agent_reply"],
        "properties": {
            "is_support_issue": {"type": "boolean"},
            "category": {"type": "string", "enum": CATEGORIES},
            "priority": {"type": "string", "enum": PRIORITIES},
            "summary": {"type": "string"},
            "missing_info": {"type": "array", "items": {"type": "string"}},
            "agent_reply": {"type": "string"},
        },
    },
}


def classify(text: str) -> dict:
    """Send one caller utterance to OpenAI and return the ticket as a dict."""
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_schema", "json_schema": TICKET_SCHEMA},
    )
    return json.loads(response.choices[0].message.content)


def classify_conversation(conversation) -> dict:
    """Classify the whole call so far into one ticket + the agent's next reply.
    conversation: list of (role, text) tuples, or plain strings (= Caller)."""
    lines = []
    for item in conversation:
        role, text = ("Caller", item) if isinstance(item, str) else item
        lines.append(f"{role}: {text}")
    return classify("\n".join(lines))


if __name__ == "__main__":
    sample = " ".join(sys.argv[1:]) or (
        "I can't log in since yesterday's update and I have a client demo in an hour."
    )
    print(f"INPUT: {sample}\n")
    print(json.dumps(classify(sample), indent=2))
