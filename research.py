import json
import os
import re
import sys
import atexit
import urllib.error
import urllib.parse
import urllib.request

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QElapsedTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap, QRegion, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import keyboard
except Exception:
    keyboard = None


API_KEY = os.environ.get("POLLINATIONS_API_KEY", "pk_WojP8kmju3pNDN3i")
CHAT_API_URL = "https://gen.pollinations.ai/v1/chat/completions"
IMAGE_API_URL = "https://gen.pollinations.ai/image/{prompt}"
UI_STATE_FILE = os.path.join(os.path.expanduser("~"), ".openrelay_ui.json")

TEXT_MODEL_OPTIONS = [
    ("Amazon", "nova-fast"),
    ("Gemini Flash", "gemini-fast"),
    ("Gemini", "gemini-search"),
    ("Qwen", "qwen-character"),
    ("GPT 5 mini", "openai"),
    ("GPT 5 Nano", "openai-fast"),
    ("DeepSeek V3.2", "deepseek"),
    ("GLM 5", "glm"),
    ("Claude 4.5 Haiku", "claude-fast"),
    ("Kimi K2.5", "kimi"),
]

IMAGE_MODEL_OPTIONS = [
    ("Fast", "flux"),
    ("High Quality", "zimage"),
    ("Ultra High Quality", "flux-2-dev"),
]

SYSTEM_PROMPT = (
    "You are a fast research assistant. Answer concisely and directly. "
    "You can generate images by returning this exact XML tag: "
    "<image_generate>DETAILED_IMAGE_PROMPT</image_generate>. "
    "Only if the user EXPLICITLY asks for an image, drawing, illustration, or photo, respond with ONLY "
    "<image_generate>DETAILED_IMAGE_PROMPT</image_generate>. "
    "For all normal questions, never use image_generate tags and reply with normal text. "
    "If asked whether you can make images, reply that you can and ask for an image request."
)
MAX_CONTEXT_TOKENS = 6000


try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        if not text:
            return 0
        return len(_ENC.encode(text))


except Exception:

    def count_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / 3.8))


def _headers(include_json: bool = True) -> dict:
    headers = {"User-Agent": "research.py/2.0"}
    if include_json:
        headers["Content-Type"] = "application/json"
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    return headers


def _request_json(url: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(include_json=True),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "ignore")
    return json.loads(body)


def _extract_content(message_content) -> str:
    if isinstance(message_content, str):
        return message_content
    if isinstance(message_content, list):
        parts = []
        for item in message_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts).strip()
    return ""


def trim_history_for_context(history: list[dict], max_tokens: int) -> list[dict]:
    selected = []
    used = 0

    for msg in reversed(history):
        text = msg.get("content", "")
        if not isinstance(text, str):
            text = str(text)
        cost = count_tokens(text)
        if selected and used + cost > max_tokens:
            break
        selected.append(msg)
        used += cost

    selected.reverse()
    return selected


def selected_model_api(combo: QComboBox, fallback: str) -> str:
    value = combo.currentData()
    if isinstance(value, str) and value.strip():
        return value.strip()
    text = combo.currentText().strip()
    return text or fallback


def load_ui_state() -> dict:
    try:
        with open(UI_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_ui_state(state: dict) -> None:
    try:
        with open(UI_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


class ChatWorker(QObject):
    chunk = pyqtSignal(str)
    finished = pyqtSignal(str, int, int, int)
    failed = pyqtSignal(str)

    def __init__(self, model: str, context_messages: list[dict]):
        super().__init__()
        self.model = model
        self.context_messages = context_messages

    def run(self):
        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.context_messages
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "max_tokens": 2000,
            }
            req = urllib.request.Request(
                CHAT_API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers=_headers(include_json=True),
                method="POST",
            )
            answer = ""
            usage = {}
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        chunk_obj = json.loads(line)
                    except Exception:
                        continue

                    if isinstance(chunk_obj, dict) and isinstance(chunk_obj.get("usage"), dict):
                        usage = chunk_obj["usage"]

                    choices = chunk_obj.get("choices") if isinstance(chunk_obj, dict) else None
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    text = delta.get("content") if isinstance(delta, dict) else None
                    if text:
                        answer += text
                        self.chunk.emit(text)

            answer = answer.strip()
            if not answer:
                raise RuntimeError("Model returned an empty response.")

            in_tokens = usage.get("prompt_tokens")
            out_tokens = usage.get("completion_tokens")
            if not isinstance(in_tokens, int):
                in_tokens = sum(count_tokens(m.get("content", "")) for m in self.context_messages)
            else:
                in_tokens = max(0, in_tokens - count_tokens(SYSTEM_PROMPT))
            if not isinstance(out_tokens, int):
                out_tokens = count_tokens(answer)

            self.finished.emit(answer, in_tokens, out_tokens, len(self.context_messages))
        except urllib.error.HTTPError as err:
            try:
                body = err.read().decode("utf-8", "ignore")
            except Exception:
                body = ""
            detail = body[:300] if body else str(err)
            self.failed.emit(f"HTTP {err.code}: {detail}")
        except Exception as err:
            self.failed.emit(str(err))


class ImageWorker(QObject):
    finished = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, prompt: str, model: str):
        super().__init__()
        self.prompt = prompt
        self.model = model

    def run(self):
        try:
            encoded_prompt = urllib.parse.quote(self.prompt)
            url = (
                IMAGE_API_URL.format(prompt=encoded_prompt)
                + f"?model={urllib.parse.quote(self.model)}&width=768&height=768&nologo=true"
            )
            req = urllib.request.Request(url, headers=_headers(include_json=False))
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()

            if not data:
                raise RuntimeError("Image API returned no data.")

            self.finished.emit(data)
        except urllib.error.HTTPError as err:
            try:
                body = err.read().decode("utf-8", "ignore")
            except Exception:
                body = ""
            detail = body[:300] if body else str(err)
            self.failed.emit(f"HTTP {err.code}: {detail}")
        except Exception as err:
            self.failed.emit(str(err))


class ShimmerLabel(QLabel):
    def __init__(self, text="Thinking...", parent=None):
        super().__init__(text, parent)
        self._shimmer_width_px = 40.0
        self._shimmer_gap_px = 24.0
        self._shimmer_speed_px_per_sec = 60.0
        self._shimmer_phase_px = 0.0
        self._elapsed = QElapsedTimer()
        self._last_elapsed_ms = 0
        self._shimmer_active = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setFont(QFont("Segoe UI", 11))
        self.setStyleSheet("color:#555555;")

    def _text_span_px(self) -> float:
        return float(max(1, self.fontMetrics().horizontalAdvance(self.text()) + 8))

    def _tick(self):
        now_ms = self._elapsed.elapsed()
        dt = max(1, now_ms - self._last_elapsed_ms) / 1000.0
        self._last_elapsed_ms = now_ms

        text_span = self._text_span_px()
        gap = max(8.0, min(self._shimmer_gap_px, text_span * 0.12))
        cycle_px = max(1.0, text_span + gap + self._shimmer_width_px)
        self._shimmer_phase_px = (self._shimmer_phase_px + (self._shimmer_speed_px_per_sec * dt)) % cycle_px
        self.update()

    def start_shimmer(self, text=None):
        if text:
            self.setText(text)
        self._shimmer_active = True
        self._shimmer_phase_px = 0.0
        self._elapsed.restart()
        self._last_elapsed_ms = 0
        self._timer.start()
        self.update()

    def stop_shimmer(self, text=None):
        self._shimmer_active = False
        self._timer.stop()
        if text:
            self.setText(text)
        self.setStyleSheet("color:#cccccc;")
        self.update()

    def paintEvent(self, event):
        if not self._shimmer_active:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        padding = w * 0.1
        grad = QLinearGradient(-padding, 0, w + padding, 0)
        total = w + 2 * padding

        def norm(x):
            return (x + padding) / total

        left_x = float(self.contentsRect().x())
        center_px = left_x + self._shimmer_phase_px - (self._shimmer_width_px / 2.0)
        half_width_px = self._shimmer_width_px / 2.0
        base = QColor(80, 80, 80)
        highlight = QColor(220, 220, 220)
        grad.setColorAt(0.0, base)
        grad.setColorAt(1.0, base)
        for stop_pos, color in (
            (norm(center_px - half_width_px), base),
            (norm(center_px), highlight),
            (norm(center_px + half_width_px), base),
        ):
            if 0.0 <= stop_pos <= 1.0:
                grad.setColorAt(stop_pos, color)

        path = QPainterPath()
        path.addText(
            self.contentsRect().x(),
            self.contentsRect().y() + self.fontMetrics().ascent(),
            self.font(),
            self.text(),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPath(path)
        painter.end()


class ResearchWindow(QWidget):
    open_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenRelay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(520, 340)

        self.history: list[dict] = []
        self.sent_count = 0
        self.busy = False
        self.thread = None
        self.worker = None
        self._drag_offset = None
        self._stream_buffer = ""
        self._streaming_assistant_started = False
        self._awaiting_first_token = False
        self._last_image = QImage()
        self._loading_elapsed = 0.0
        self._loading_title = ""
        self._loading_subtitle = ""
        self._loading_elapsed_timer = QTimer(self)
        self._loading_elapsed_timer.setInterval(100)
        self._loading_elapsed_timer.timeout.connect(self._tick_loading_elapsed)
        self._geom_save_timer = QTimer(self)
        self._geom_save_timer.setSingleShot(True)
        self._geom_save_timer.setInterval(180)
        self._geom_save_timer.timeout.connect(self._persist_geometry)
        self._restored_geometry = False

        self.open_requested.connect(self._show_as_popup)
        self._build_ui()
        self._load_models()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = QWidget()
        card.setObjectName("card")
        card.setStyleSheet(
            "#card { "
            "background: qradialgradient(cx:0.18, cy:0.08, radius:1.05, "
            "stop:0 rgba(54,95,170,58), stop:0.45 rgba(22,28,42,22), stop:1 #121212); "
            "color: #e8e8e8; border-radius: 12px; font-family: 'Segoe UI'; }"
            "#topbar { background: #171717; border: 1px solid #2b2b2b; border-radius: 8px; }"
            "#bottom-actions { background: rgba(18, 18, 18, 0.88); border: 1px solid #2f2f2f; border-radius: 18px; }"
            "QTextEdit { background: transparent; border: none; padding: 4px 2px; font-size: 15px; }"
            "QLineEdit { background: transparent; border: none; padding: 6px 2px; font-size: 16px; color: #f0f0f0; }"
            "QComboBox { background: transparent; border: none; padding: 4px; color: #d8d8d8; }"
            "QPushButton { background: transparent; border: none; color: #bdbdbd; padding: 2px 6px; }"
            "QPushButton:disabled { color: #6f6f6f; }"
        )
        root.addWidget(card)

        content = QVBoxLayout(card)
        content.setContentsMargins(12, 12, 12, 12)
        content.setSpacing(8)

        self.top_wrap = QWidget()
        self.top_wrap.setObjectName("topbar")
        self.top_wrap.setCursor(Qt.CursorShape.SizeAllCursor)
        self.top_wrap.mousePressEvent = self._drag_mouse_press
        self.top_wrap.mouseMoveEvent = self._drag_mouse_move
        self.top_wrap.mouseReleaseEvent = self._drag_mouse_release
        top_row = QHBoxLayout(self.top_wrap)
        top_row.setContentsMargins(8, 4, 8, 4)
        top_row.setSpacing(8)

        self.chat_model_combo = QComboBox()
        self.chat_model_combo.setFixedWidth(170)
        self.image_model_combo = QComboBox()
        self.image_model_combo.setFixedWidth(140)
        for label, api_name in IMAGE_MODEL_OPTIONS:
            self.image_model_combo.addItem(label, api_name)

        self.clear_btn = QPushButton("Clear Context")
        self.clear_btn.clicked.connect(self._on_clear_clicked)

        self.min_btn = QPushButton("−")
        self.min_btn.clicked.connect(self.showMinimized)
        self.min_btn.setStyleSheet("font-size:16px; color:#cfcfcf; padding:0 4px;")
        self.close_btn = QPushButton("×")
        self.close_btn.clicked.connect(QApplication.instance().quit)
        self.close_btn.setStyleSheet("font-size:16px; color:#cfcfcf; padding:0 4px;")

        top_row.addWidget(QLabel("Text"))
        top_row.addWidget(self.chat_model_combo)
        top_row.addWidget(QLabel("Image"))
        top_row.addWidget(self.image_model_combo)
        top_row.addStretch(1)
        top_row.addWidget(self.min_btn)
        top_row.addWidget(self.close_btn)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter message")
        self.input.returnPressed.connect(self.send_message)
        self.input_divider = QWidget()
        self.input_divider.setFixedHeight(1)
        self.input_divider.setStyleSheet("background: rgba(126,152,195,0.26); border: none;")

        self.loading_box = QWidget()
        self.loading_box.setStyleSheet(
            "background: transparent;"
            "border: none;"
        )
        loading_layout = QVBoxLayout(self.loading_box)
        loading_layout.setContentsMargins(2, 4, 2, 4)
        loading_layout.setSpacing(6)
        self.loading_label = ShimmerLabel("Thinking...")
        self.loading_label.setStyleSheet("color:#9aa8c0;")
        self.loading_sub = QLabel("")
        self.loading_sub.setWordWrap(True)
        self.loading_sub.setStyleSheet("color:#95a3b8; font-size:11px;")
        loading_layout.addWidget(self.loading_label)
        loading_layout.addWidget(self.loading_sub)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Assistant reply")
        self.output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.output.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.output.mouseDoubleClickEvent = self._output_mouse_double_click

        self.reply_stack_host = QWidget()
        self.reply_stack = QStackedLayout(self.reply_stack_host)
        self.reply_stack.setContentsMargins(0, 0, 0, 0)
        self.reply_stack.addWidget(self.output)
        self.reply_stack.addWidget(self.loading_box)
        self.reply_stack.setCurrentWidget(self.output)

        self.token_label = QLabel("press control + space to open")
        self.token_label.setStyleSheet("color:#9a9a9a; font-size:12px;")
        self.copy_img_btn = QPushButton("Copy Image")
        self.copy_img_btn.clicked.connect(self._copy_image)
        self.copy_img_btn.hide()
        self.open_img_btn = QPushButton("Open Image")
        self.open_img_btn.clicked.connect(self._open_image_overlay)
        self.open_img_btn.hide()
        self.save_img_btn = QPushButton("Save Image")
        self.save_img_btn.clicked.connect(self._save_image)
        self.save_img_btn.hide()
        self.copy_msg_btn = QPushButton("Copy Message")
        self.copy_msg_btn.clicked.connect(self._copy_message)
        self.copy_msg_btn.hide()
        self.open_img_btn.setProperty("base_text", "Open Image")
        self.copy_img_btn.setProperty("base_text", "Copy Image")
        self.save_img_btn.setProperty("base_text", "Save Image")
        self.copy_msg_btn.setProperty("base_text", "Copy Message")
        self.clear_btn.setProperty("base_text", "Clear Context")
        self._apply_action_text_style(self.open_img_btn)
        self._apply_action_text_style(self.copy_img_btn)
        self._apply_action_text_style(self.save_img_btn)
        self._apply_action_text_style(self.copy_msg_btn)
        self._apply_action_text_style(self.clear_btn)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(2)
        left_col.addWidget(self.token_label)
        bottom_row.addLayout(left_col, 1)

        self.bottom_actions = QWidget()
        self.bottom_actions.setObjectName("bottom-actions")
        self.bottom_actions.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.bottom_actions.setStyleSheet("background: rgba(18, 18, 18, 0.88); border: 1px solid #2f2f2f; border-radius: 18px;")
        self.bottom_actions.setMinimumHeight(38)
        actions_row = QHBoxLayout(self.bottom_actions)
        actions_row.setContentsMargins(8, 4, 8, 4)
        actions_row.setSpacing(6)
        actions_row.addWidget(self.open_img_btn)
        actions_row.addWidget(self.copy_img_btn)
        actions_row.addWidget(self.save_img_btn)
        actions_row.addWidget(self.copy_msg_btn)
        actions_row.addWidget(self.clear_btn)
        bottom_row.addWidget(self.bottom_actions)

        content.addWidget(self.top_wrap)
        content.addWidget(self.input)
        content.addWidget(self.input_divider)
        content.addWidget(self.reply_stack_host, 1)
        content.addLayout(bottom_row)

        self.image_overlay = QWidget(self)
        self.image_overlay.setStyleSheet("background: #000000;")
        self.image_overlay.hide()
        overlay_layout = QVBoxLayout(self.image_overlay)
        overlay_layout.setContentsMargins(18, 18, 18, 18)
        overlay_layout.setSpacing(12)
        self.overlay_image_label = QLabel()
        self.overlay_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_image_label.setStyleSheet("background: #000000;")
        self.overlay_close_btn = QPushButton("Close Image")
        self.overlay_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_close_btn.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._apply_pill_button_style(self.overlay_close_btn, text_color="#f0f0f0")
        self.overlay_close_btn.clicked.connect(self._close_image_overlay)
        overlay_layout.addWidget(self.overlay_image_label, 1)
        overlay_layout.addWidget(self.overlay_close_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.image_overlay.mousePressEvent = self._overlay_mouse_press
        self.image_overlay.raise_()

    def _load_models(self):
        self.chat_model_combo.clear()
        for label, api_name in TEXT_MODEL_OPTIONS:
            self.chat_model_combo.addItem(label, api_name)

        preferred = "Gemini"
        idx = self.chat_model_combo.findText(preferred)
        if idx >= 0:
            self.chat_model_combo.setCurrentIndex(idx)
        image_pref = self.image_model_combo.findText("High Quality")
        if image_pref >= 0:
            self.image_model_combo.setCurrentIndex(image_pref)
        else:
            self.image_model_combo.setCurrentIndex(0)

    def _show_as_popup(self):
        if not self._restored_geometry:
            st = load_ui_state()
            try:
                x = int(st.get("x"))
                y = int(st.get("y"))
                w = int(st.get("w"))
                h = int(st.get("h"))
                if w > 220 and h > 180:
                    self.resize(w, h)
                self.move(x, y)
                self._restored_geometry = True
            except Exception:
                screen = QApplication.primaryScreen().availableGeometry()
                x = screen.center().x() - int(self.width() / 2)
                y = screen.center().y() - int(self.height() / 2)
                self.move(x, y)
            self._restored_geometry = True
        self.show()
        self.activateWindow()
        self.input.setFocus()

    def show_assistant_text(self, text: str):
        self._append_transcript_entry(text)

    def _append_transcript_entry(self, text: str):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        if self.output.toPlainText().strip():
            self.output.insertPlainText("\n\n")
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.ensureCursorVisible()

    def _append_stream_text(self, text: str):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def _show_loading(self, title: str, subtitle: str = ""):
        self._loading_title = title
        self._loading_subtitle = subtitle
        self._loading_elapsed = 0.0
        self.loading_label.start_shimmer(title)
        self.loading_sub.setText(subtitle if subtitle else "Elapsed: 0.0s")
        self.loading_sub.setVisible(True)
        self._loading_elapsed_timer.start()
        self.reply_stack.setCurrentWidget(self.loading_box)

    def _hide_loading(self):
        self.loading_label.stop_shimmer()
        self._loading_elapsed_timer.stop()
        self.reply_stack.setCurrentWidget(self.output)

    def _tick_loading_elapsed(self):
        self._loading_elapsed += 0.1
        elapsed = f"Elapsed: {self._loading_elapsed:.1f}s"
        if self._loading_subtitle:
            self.loading_sub.setText(f"{self._loading_subtitle}\n{elapsed}")
        else:
            self.loading_sub.setText(elapsed)

    def show_error(self, text: str):
        self._append_transcript_entry(f"Error: {text}")
        self._hide_loading()

    def set_busy(self, busy: bool):
        self.busy = busy
        self.input.setEnabled(not busy)
        self.chat_model_combo.setEnabled(not busy)
        self.image_model_combo.setEnabled(not busy)
        self.close_btn.setEnabled(not busy)
        self.min_btn.setEnabled(not busy)
        self.clear_btn.setEnabled(not busy)

    def clear_context(self):
        if self.busy:
            return
        self.history.clear()
        self.output.clear()
        self._streaming_assistant_started = False
        self.open_img_btn.hide()
        self.copy_img_btn.hide()
        self.save_img_btn.hide()
        self.copy_msg_btn.hide()
        self._last_image = QImage()
        self._hide_loading()
        self.input.setFocus()

    def _is_explicit_image_request(self, text: str) -> bool:
        if not text:
            return False
        t = text.lower()
        # Capability questions should not trigger generation.
        capability_checks = [
            "can you make image",
            "can you make images",
            "can you generate image",
            "can you generate images",
            "can you create image",
            "can you create images",
            "are you able to make image",
            "do you make images",
            "do you generate images",
            "can you do images",
        ]
        if any(p in t for p in capability_checks):
            return False

        direct_request_patterns = [
            "generate an image",
            "generate a image",
            "generate image of",
            "create an image",
            "create a image",
            "create image of",
            "make an image",
            "make a image",
            "make image of",
            "draw ",
            "illustrate ",
            "render ",
            "show me an image",
            "show me a picture",
            "make me a logo",
            "create a logo",
        ]
        return any(p in t for p in direct_request_patterns)

    def _looks_like_image_refusal(self, text: str) -> bool:
        if not text:
            return False
        t = text.lower()
        patterns = [
            "can't generate images",
            "cannot generate images",
            "can't generate image",
            "cannot generate image",
            "can't create images",
            "cannot create images",
            "i can describe",
            "sorry, but i can't",
            "i'm unable to generate",
            "i am unable to generate",
        ]
        return any(p in t for p in patterns)

    def send_message(self):
        if self.busy:
            return

        prompt = self.input.text().strip()
        if not prompt:
            return

        self.input.clear()
        self.input.setFocus()
        self.output.setStyleSheet("")
        self._stream_buffer = ""
        self._streaming_assistant_started = False
        self._awaiting_first_token = True
        self.open_img_btn.hide()
        self.copy_img_btn.hide()
        self.save_img_btn.hide()
        self.copy_msg_btn.hide()
        self._show_loading("Thinking...")
        QApplication.processEvents()

        self.history.append({"role": "user", "content": prompt})
        context = trim_history_for_context(self.history, MAX_CONTEXT_TOKENS)
        self._start_chat_worker(context)

    def _start_chat_worker(self, context_messages: list[dict]):
        self._cleanup_worker()
        self.set_busy(True)

        self.thread = QThread()
        model_name = selected_model_api(self.chat_model_combo, "nova-fast")
        self.worker = ChatWorker(model_name, context_messages)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.chunk.connect(self._on_chat_chunk)
        self.worker.finished.connect(self._on_chat_finished)
        self.worker.failed.connect(self._on_worker_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _start_image_worker(self, prompt: str):
        self._cleanup_worker()
        self.set_busy(True)
        self._show_loading(
            "Generating image...",
            "Making image, may take a while for realistic and high quality models.",
        )

        self.thread = QThread()
        image_model = selected_model_api(self.image_model_combo, "flux")
        self.worker = ImageWorker(prompt, image_model)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_image_finished)
        self.worker.failed.connect(self._on_worker_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _on_chat_chunk(self, text: str):
        if self._awaiting_first_token:
            self._awaiting_first_token = False
            self._hide_loading()
        if not self._streaming_assistant_started:
            self._streaming_assistant_started = True
            self.output.moveCursor(QTextCursor.MoveOperation.End)
            if self.output.toPlainText().strip():
                self.output.insertPlainText("\n\n")
        self._stream_buffer += text
        self._append_stream_text(text)

    def _on_chat_finished(self, answer: str, in_tokens: int, out_tokens: int, context_count: int):
        self.set_busy(False)
        if self._awaiting_first_token:
            self._awaiting_first_token = False
            self._hide_loading()
        last_user_text = ""
        for msg in reversed(self.history):
            if msg.get("role") == "user":
                last_user_text = str(msg.get("content", ""))
                break

        # Only trigger image generation when the model response is exactly one image tag.
        image_match = re.fullmatch(
            r"\s*<image_generate>(.*?)</image_generate>\s*",
            answer,
            re.IGNORECASE | re.DOTALL,
        )

        if image_match:
            image_prompt = image_match.group(1).strip()
            self.history.append({"role": "assistant", "content": answer})
            self._start_image_worker(image_prompt)
            return

        # If user asked for an image and no XML tag came back, force image generation.
        if self._is_explicit_image_request(last_user_text):
            fallback_prompt = last_user_text
            self.history.append(
                {"role": "assistant", "content": f"<image_generate>{fallback_prompt}</image_generate>"}
            )
            self._start_image_worker(fallback_prompt)
            return

        # If the model refuses image generation, still force image generation from last user message.
        if self._looks_like_image_refusal(answer) and last_user_text:
            fallback_prompt = last_user_text
            self.history.append(
                {"role": "assistant", "content": f"<image_generate>{fallback_prompt}</image_generate>"}
            )
            self._start_image_worker(fallback_prompt)
            return

        self.history.append({"role": "assistant", "content": answer})
        if not self._stream_buffer:
            self.show_assistant_text(answer)
        self.copy_msg_btn.show()

    def _on_image_finished(self, data: bytes):
        self.set_busy(False)
        self._hide_loading()
        self.copy_msg_btn.show()
        image = QImage()
        if not image.loadFromData(data):
            self._on_worker_failed("Image data could not be decoded.")
            return

        viewport = self.output.viewport().size()
        max_w = max(200, viewport.width() - 10)
        max_h = max(160, viewport.height() - 20)
        scaled = image.scaled(
            max_w,
            max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        rounded = self._rounded_image(scaled, 12)
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        if self.output.toPlainText().strip():
            self.output.insertPlainText("\n\n")
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertImage(rounded, "generated_image")
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
        self._last_image = image
        self.open_img_btn.show()
        self.copy_img_btn.show()
        self.save_img_btn.show()
        self.open_img_btn.setText(str(self.open_img_btn.property("base_text") or "Open Image"))
        self.copy_img_btn.setText(str(self.copy_img_btn.property("base_text") or "Copy Image"))
        self.save_img_btn.setText(str(self.save_img_btn.property("base_text") or "Save Image"))

    def _on_worker_failed(self, error_message: str):
        self.set_busy(False)
        self._hide_loading()
        self.show_error(error_message)

    def _flash_button_text(self, button: QPushButton, text: str, duration_ms: int = 900):
        base = button.property("base_text") or button.text()
        button.setText(str(text))
        QTimer.singleShot(duration_ms, lambda b=button, t=str(base): b.setText(t))

    def _apply_pill_button_style(self, button: QPushButton, text_color: str = "#d2d2d2"):
        button.setMinimumHeight(30)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            "QPushButton { "
            "background: rgba(90, 90, 90, 0.16); "
            "border: 1px solid rgba(180, 180, 180, 0.24); "
            f"color: {text_color}; "
            "border-radius: 15px; "
            "padding: 4px 12px; "
            "font-size: 12px; }"
            "QPushButton:hover { background: rgba(120, 120, 120, 0.28); }"
            "QPushButton:pressed { background: rgba(170, 170, 170, 0.34); }"
            "QPushButton:disabled { color: #6f6f6f; }"
        )

    def _apply_action_text_style(self, button: QPushButton):
        button.setMinimumHeight(28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            "QPushButton { "
            "background: transparent; "
            "border: none; "
            "color: #d2d2d2; "
            "border-radius: 10px; "
            "padding: 4px 10px; "
            "font-size: 12px; }"
            "QPushButton:hover { background: rgba(120, 120, 120, 0.24); }"
            "QPushButton:pressed { background: rgba(170, 170, 170, 0.30); }"
            "QPushButton:disabled { color: #6f6f6f; }"
        )

    def _rounded_image(self, image: QImage, radius: int) -> QImage:
        if image.isNull():
            return image
        rounded = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, float(image.width()), float(image.height()), float(radius), float(radius))
        painter.setClipPath(path)
        painter.drawImage(0, 0, image)
        painter.end()
        return rounded

    def _open_image_overlay(self):
        if self._last_image.isNull():
            return
        self.image_overlay.setGeometry(self.rect())
        self._refresh_overlay_image()
        self.image_overlay.show()
        self.image_overlay.raise_()

    def _close_image_overlay(self):
        self.image_overlay.hide()

    def _overlay_mouse_press(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        clicked = self.image_overlay.childAt(event.position().toPoint())
        if clicked is self.overlay_image_label or clicked is self.overlay_close_btn:
            event.accept()
            return
        self._close_image_overlay()
        event.accept()

    def _refresh_overlay_image(self):
        if self._last_image.isNull():
            return
        target = self.image_overlay.size()
        max_w = max(200, target.width() - 40)
        max_h = max(160, target.height() - 96)
        scaled = self._last_image.scaled(
            max_w,
            max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        rounded = self._rounded_image(scaled, 14)
        self.overlay_image_label.setPixmap(QPixmap.fromImage(rounded))

    def _output_mouse_double_click(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._last_image.isNull()
            and self.copy_img_btn.isVisible()
        ):
            self._open_image_overlay()
            event.accept()
            return
        QTextEdit.mouseDoubleClickEvent(self.output, event)

    def _on_clear_clicked(self):
        self.clear_context()
        self._flash_button_text(self.clear_btn, "Cleared!")

    def _copy_image(self):
        if self._last_image.isNull():
            return
        QApplication.clipboard().setImage(self._last_image)
        self._flash_button_text(self.copy_img_btn, "Copied!")

    def _copy_message(self):
        text = self.output.toPlainText().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._flash_button_text(self.copy_msg_btn, "Copied!")

    def _save_image(self):
        if self._last_image.isNull():
            return
        default_path = os.path.join(os.path.expanduser("~"), "OpenRelay_image.png")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Image",
            default_path,
            "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg)",
        )
        if not path:
            return
        fmt = "PNG"
        lower = path.lower()
        if lower.endswith(".jpg") or lower.endswith(".jpeg"):
            fmt = "JPEG"
        if self._last_image.save(path, fmt):
            self._flash_button_text(self.save_img_btn, "Saved!")

    def _on_stop_clicked(self):
        if not self.busy:
            return
        if self.thread and self.thread.isRunning():
            self.thread.terminate()
            self.thread.wait(300)
        self._cleanup_worker()
        self._awaiting_first_token = False
        self._stream_buffer = ""
        self._streaming_assistant_started = False
        self.set_busy(False)
        self._hide_loading()
        self._append_transcript_entry("Request stopped.")
        self.input.setFocus()

    def _cleanup_worker(self):
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(800)
        self.thread = None
        self.worker = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.image_overlay.isVisible():
                self._close_image_overlay()
                return
            self.hide()
            return
        super().keyPressEvent(event)

    def _drag_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _drag_mouse_move(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _drag_mouse_release(self, event):
        self._drag_offset = None
        self._schedule_geometry_save()
        event.accept()

    def resizeEvent(self, event):
        # Keep true rounded window corners for frameless mode.
        radius = 12
        path = QPainterPath()
        path.addRoundedRect(float(self.rect().x()), float(self.rect().y()), float(self.width()), float(self.height()), radius, radius)
        polygon = path.toFillPolygon().toPolygon()
        self.setMask(QRegion(polygon))
        self.image_overlay.setGeometry(self.rect())
        if self.image_overlay.isVisible():
            self._refresh_overlay_image()
        self._schedule_geometry_save()
        super().resizeEvent(event)

    def _schedule_geometry_save(self):
        if self.isMinimized():
            return
        self._geom_save_timer.start()

    def _persist_geometry(self):
        save_ui_state(
            {
                "x": self.x(),
                "y": self.y(),
                "w": self.width(),
                "h": self.height(),
            }
        )


def main():
    if sys.platform == "win32":
        try:
            import ctypes

            console = ctypes.windll.kernel32.GetConsoleWindow()
            if console:
                ctypes.windll.user32.ShowWindow(console, 0)
        except Exception:
            pass

    app = QApplication(sys.argv)
    window = ResearchWindow()
    window._show_as_popup()

    if keyboard is not None:
        try:
            keyboard.add_hotkey("ctrl+space", window.open_requested.emit)
            atexit.register(keyboard.unhook_all_hotkeys)
        except Exception:
            pass

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
