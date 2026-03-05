# OpenRelay

OpenRelay is a minimal desktop AI popup (`research.py`) made with the [pollinations api](https://pollinations.ai). Usually, talking to an ai requires a full app, which is usually very heavy an inconvienient, with OpenRelay, you get access to many AI Chatbots and models like GPT 5 and Claude, unlimited, for free.

## Install

Download the python script from [the website](https://cazyundee.github.io/OpenRelay)
Then run the following command in your terminal (with python installed)

```bash
pip install PyQt6 keyboard
```
Then run the script as follows (or just double click it)

```bash
python openrelay.py
```

---

## Usage

- `Ctrl+Space` -- open/focus the window
- `Escape` -- hide the window
- `Enter` -- send message
- **Clear Context** -- wipe conversation history
- Drag the top bar to move the window

### Image generation

Just ask it normally like for instance: 
> "make an image of a cat"

---

## Notes

- Runs frameless, always on top
- No console window on Windows
- Conversation history persists within a session, cleared on restart or via Clear Context
