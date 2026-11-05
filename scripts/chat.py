"""Simple terminal chat with Sylvie (Phase 0). Type /think to toggle thinking mode, /quit to exit."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm import LLM  # noqa: E402
from core.persona import build_system_prompt, build_turn_context, wrap_user_message  # noqa: E402
from core.toolcall import StreamSplitter  # noqa: E402


def main() -> None:
    print("Loading Sylvie…", flush=True)
    llm = LLM()
    llm.load()
    messages = [{"role": "system", "content": build_system_prompt()}]
    thinking = False

    # Greeting
    messages.append(
        {
            "role": "user",
            "content": wrap_user_message("(Lea just opened the chat. Greet her.)", build_turn_context()),
        }
    )
    print("Sylvie: ", end="", flush=True)
    sp = StreamSplitter(False)
    for c in llm.stream(messages, max_tokens=80):
        for kind, s in sp.feed(c.text):
            if kind == "text":
                print(s, end="", flush=True)
    for _kind, s in sp.flush():
        print(s, end="", flush=True)
    print()
    messages.append({"role": "assistant", "content": sp.visible_text.strip()})

    while True:
        try:
            text = input("\nLea: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/think":
            thinking = not thinking
            print(f"(thinking mode {'on' if thinking else 'off'})")
            continue
        messages.append({"role": "user", "content": wrap_user_message(text, build_turn_context())})
        sp = StreamSplitter(thinking)
        print("Sylvie: ", end="", flush=True)
        for c in llm.stream(messages, thinking=thinking):
            for kind, s in sp.feed(c.text):
                print(s if kind == "text" else f"\033[2m{s}\033[0m", end="", flush=True)
        for _kind, s in sp.flush():
            print(s, end="", flush=True)
        print()
        messages.append({"role": "assistant", "content": sp.visible_text.strip()})


if __name__ == "__main__":
    main()
