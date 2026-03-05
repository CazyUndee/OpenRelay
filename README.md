# OpenRelay

OpenRelay is a minimal desktop AI popup (`research.py`) with streaming chat, image generation fallback, and hotkey open.

This repository also includes a static showcase/download website.

## Files
- `research.py`: main desktop popup script
- `index.html`, `styles.css`, `app.js`: showcase website

## Run Desktop App
```bash
pip install PyQt6 keyboard tiktoken
python research.py
```

## Run Website Locally
Open `index.html` directly in your browser.

## Deploy Website on GitHub Pages
1. Push this repository to GitHub.
2. In GitHub repo settings, open **Pages**.
3. Set source to **Deploy from a branch**.
4. Select branch `main` and folder `/ (root)`.
