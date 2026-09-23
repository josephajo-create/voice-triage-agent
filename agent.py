"""
Voice Triage Agent: live mic -> AssemblyAI transcript -> Gemini/OpenAI ticket
-> spoken reply (TTS). One call = one ticket, updated as the caller talks.
Press Ctrl+C to end the call. The ticket is saved to tickets.json.
"""
import time
import os
import json
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

import pyaudio
from assemblyai.streaming.v3 import (
    StreamingClient,
    StreamingClientOptions,
    StreamingParameters,
    StreamingEvents,
    BeginEvent,
    TurnEvent,
    TerminationEvent,
    StreamingError,
)

load_dotenv()
from triage import classify_conversation  # after load_dotenv so keys are loaded
from tts import Speaker

API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
if not API_KEY:
    raise SystemExit("ASSEMBLYAI_API_KEY missing - add it to your .env file")

TICKETS_FILE = "tickets.json"

# Words the caller is likely to say - helps speech-to-text get them right
KEYTERMS = ["log in", "login", "password", "client demo", "update", "billing",
            "refund", "invoice", "subscription", "error message", "crash"]

GREETING = "Hi, you've reached support. Please describe the problem you're facing."
TICKET_FIELDS = ["is_support_issue", "category", "priority", "summary", "missing_info"]

conversation = []       # (role, text) for every Caller and Agent turn
current_ticket = None   # the single ticket for this call
latest_version = 0      # bumps on every new turn
lock = threading.Lock()
pool = ThreadPoolExecutor(max_workers=1)  # one LLM call at a time, in order

agent_speaking = threading.Event()  # mic is muted while this is set
caller_speaking = threading.Event()  # caller is mid-sentence - don't talk over them


def _mute():
    agent_speaking.set()


def _unmute():
    time.sleep(0.3)  # let the speaker's echo die down
    agent_speaking.clear()


speaker = Speaker(on_start=_mute, on_end=_unmute)


def update_ticket(snapshot, version):
    """Re-classify the whole call, then speak the agent's reply.
    Skips work that a newer caller turn has made stale."""
    global current_ticket
    if version != latest_version:
        return  # a newer turn is already queued - skip this one
    try:
        result = classify_conversation(snapshot)
    except Exception as e:
        print(f"\n  [triage error: {e}]\n")
        return
    changed = is_new = False
    with lock:
        if version != latest_version:
            return  # result is outdated
        if result["is_support_issue"]:
            ticket = {k: result[k] for k in TICKET_FIELDS}
            is_new = current_ticket is None
            changed = ticket != current_ticket
            current_ticket = ticket
    if changed:
        ticket = current_ticket
        label = "TICKET CREATED" if is_new else "TICKET UPDATED"
        print(f"\n  {label}  [{ticket['priority']}] {ticket['category']}: {ticket['summary']}")
        for q in ticket["missing_info"]:
            print(f"    ? {q}")
        print()

    reply = result.get("agent_reply", "").strip()
    if reply and not caller_speaking.is_set():
        with lock:
            conversation.append(("Agent", reply))
        print(f"AGENT: {reply}\n")
        speaker.say(reply)


def on_begin(client, event: BeginEvent):
    print(f"Connected (session {event.id}).\n")
    with lock:
        conversation.append(("Agent", GREETING))
    print(f"AGENT: {GREETING}\n")
    speaker.say(GREETING)


def on_turn(client, event: TurnEvent):
    global latest_version
    if not event.end_of_turn:
        if event.transcript.strip():
            caller_speaking.set()
            print(f"\r... {event.transcript}", end="", flush=True)
        return
    caller_speaking.clear()
    if event.turn_is_formatted and event.transcript.strip():
        print(f"\rCALLER: {event.transcript}")
        with lock:
            conversation.append(("Caller", event.transcript))
            latest_version += 1
            snapshot, version = list(conversation), latest_version
        pool.submit(update_ticket, snapshot, version)


def on_terminated(client, event: TerminationEvent):
    print(f"\nCall ended ({event.audio_duration_seconds:.1f}s of audio).")


def on_error(client, error: StreamingError):
    print(f"\nError: {error}")


def mic_stream(sample_rate=16000, chunk=800):
    """Yield 50 ms chunks of 16-bit mono audio from the default mic.
    Sends silence while the agent is talking, so it doesn't hear itself."""
    silence = b"\x00" * chunk * 2
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=sample_rate,
                     input=True, frames_per_buffer=chunk)
    try:
        while True:
            data = stream.read(chunk, exception_on_overflow=False)
            yield silence if agent_speaking.is_set() else data
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def save_ticket():
    """Append this call's final ticket + transcript to tickets.json."""
    if current_ticket is None:
        print("\nNo support issue detected - nothing saved.")
        return
    try:
        with open(TICKETS_FILE, encoding="utf-8") as f:
            all_tickets = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        all_tickets = []
    record = {
        "id": len(all_tickets) + 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **current_ticket,
        "transcript": [{"role": r, "text": t} for r, t in conversation],
    }
    all_tickets.append(record)
    with open(TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_tickets, f, indent=2)
    print(f"\nFinal ticket #{record['id']} saved to {TICKETS_FILE}:")
    print(json.dumps(record, indent=2))


def main():
    client = StreamingClient(
        StreamingClientOptions(api_key=API_KEY, api_host="streaming.assemblyai.com")
    )
    client.on(StreamingEvents.Begin, on_begin)
    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)

    client.connect(StreamingParameters(
        sample_rate=16000,
        format_turns=True,
        # Turn detection: wait a bit longer so mid-sentence pauses don't split turns
        end_of_turn_confidence_threshold=0.7,
        min_turn_silence=800,     # ms of silence needed when confident the turn ended
        max_turn_silence=2500,    # ms of silence that always ends a turn
        keyterms_prompt=KEYTERMS,
    ))
    try:
        client.stream(mic_stream())
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect(terminate=True)
        pool.shutdown(wait=True)
        save_ticket()


if __name__ == "__main__":
    main()
