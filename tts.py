"""
Text-to-speech for the Voice Triage Agent (free, offline, Windows voices).
Test on its own:  python tts.py
Engines (set TTS_ENGINE in .env):
  sapi       - default. Windows speech API directly: fast and reliable.
  powershell - fallback. Slightly slower start, very reliable.
  pyttsx3    - optional. Can go silent after the first lines on some PCs.
"""
import os
import queue
import subprocess
import threading

ENGINE = os.getenv("TTS_ENGINE", "sapi").lower()


def _speak_powershell(text: str):
    safe = text.replace("'", "''")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Add-Type -AssemblyName System.Speech; "
         "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
         f"$s.Rate = 1; $s.Speak('{safe}')"],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class Speaker:
    """Speaks queued text one line at a time on its own thread.
    on_start / on_end let the agent mute the mic while it talks."""

    def __init__(self, on_start=None, on_end=None):
        self.q = queue.Queue()
        self.on_start, self.on_end = on_start, on_end
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text: str):
        self.q.put(text)

    def wait(self):
        self.q.join()

    def _run(self):
        engine = None
        sapi_voice = None
        if ENGINE == "sapi":
            try:
                import comtypes
                import comtypes.client
                comtypes.CoInitialize()
                sapi_voice = comtypes.client.CreateObject("SAPI.SpVoice")
                sapi_voice.Rate = 1  # -10 (slow) .. 10 (fast)
            except Exception as e:
                print(f"[SAPI voice unavailable ({e}) - using PowerShell voice]")
        elif ENGINE == "pyttsx3":
            try:
                try:
                    import comtypes
                    comtypes.CoInitialize()  # Windows voices need COM on this thread
                except Exception:
                    pass
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 175)
            except Exception as e:
                print(f"[pyttsx3 unavailable ({e}) - using PowerShell voice]")
        while True:
            text = self.q.get()
            try:
                if self.on_start:
                    self.on_start()
                if sapi_voice:
                    sapi_voice.Speak(text)  # blocks until finished speaking
                elif engine:
                    engine.say(text)
                    engine.runAndWait()
                else:
                    _speak_powershell(text)
            except Exception as e:
                print(f"[tts error: {e}]")
            finally:
                if self.on_end:
                    self.on_end()
                self.q.task_done()


if __name__ == "__main__":
    s = Speaker()
    s.say("Hi, you've reached support. Please describe the problem you're facing.")
    s.say("Thanks. What error message do you see when you try to log in?")
    s.wait()
    print("TTS test finished.")
