"""
Voice Triage Agent - Step 2 & 3: live mic -> AssemblyAI realtime transcripts.
Speak, and each completed caller turn prints as one clean utterance.
Press Ctrl+C to stop.
"""
import os
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
API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
if not API_KEY:
    raise SystemExit("ASSEMBLYAI_API_KEY missing - add it to your .env file")

utterances = []  # each completed caller turn goes here (feeds the LLM later)


def on_begin(client, event: BeginEvent):
    print(f"Connected (session {event.id}). Start speaking...\n")


def on_turn(client, event: TurnEvent):
    if not event.end_of_turn:
        # live partial text, overwritten on the same line
        print(f"\r... {event.transcript}", end="", flush=True)
        return
    if event.turn_is_formatted:
        utterances.append(event.transcript)
        print(f"\rCALLER: {event.transcript}\n")


def on_terminated(client, event: TerminationEvent):
    print(f"\nSession ended ({event.audio_duration_seconds:.1f}s of audio).")


def on_error(client, error: StreamingError):
    print(f"\nError: {error}")


def mic_stream(sample_rate=16000, chunk=800):
    """Yield 50 ms chunks of 16-bit mono audio from the default mic."""
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=sample_rate,
                     input=True, frames_per_buffer=chunk)
    try:
        while True:
            yield stream.read(chunk, exception_on_overflow=False)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def main():
    client = StreamingClient(
        StreamingClientOptions(api_key=API_KEY, api_host="streaming.assemblyai.com")
    )
    client.on(StreamingEvents.Begin, on_begin)
    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)

    client.connect(StreamingParameters(sample_rate=16000, format_turns=True))
    try:
        client.stream(mic_stream())
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect(terminate=True)
        print("\nCaptured utterances:")
        for i, u in enumerate(utterances, 1):
            print(f"  {i}. {u}")


if __name__ == "__main__":
    main()
