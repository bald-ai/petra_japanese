import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, abort, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent

DATA_FILES: Dict[str, str] = {
    "grammar": "grammar.JSON",
    "vocabulary": "vocabulary.JSON",
    "kanji": "kanji.JSON",
    "reading": "reading.JSON",
    "listening": "listening.JSON",
}

LESSON_NAMES: Dict[str, str] = {
    "grammar": "Grammar",
    "vocabulary": "Vocabulary",
    "kanji": "Kanji",
    "reading": "Reading",
    "listening": "Listening",
}


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _image_sort_key(path: Path) -> Tuple[int, int, str]:
    stem = path.stem
    if stem.isdigit():
        return (0, int(stem), stem)
    return (1, 0, stem)


def _resolve_answer(choices: Optional[List[str]], answer: Any) -> Tuple[Optional[int], Optional[str]]:
    position: Optional[int] = None
    text: Optional[str] = None

    if isinstance(answer, dict):
        position = answer.get("position")
        text = answer.get("text")
    elif isinstance(answer, int):
        position = answer
    elif isinstance(answer, str):
        text = answer

    if choices and position:
        index = position - 1
        if 0 <= index < len(choices):
            text = choices[index]

    return position, text


def _prepare_data() -> Dict[str, List[Dict[str, Any]]]:
    data: Dict[str, List[Dict[str, Any]]] = {}
    for key, file_name in DATA_FILES.items():
        data[key] = _load_json(BASE_DIR / file_name)

    for key in ("grammar", "vocabulary", "kanji"):
        for item in data.get(key, []):
            choices = item.get("choices")
            position, text = _resolve_answer(choices, item.get("answer"))
            item["answer_position"] = position
            item["answer_text"] = text

    for reading_item in data.get("reading", []):
        for question in reading_item.get("questions", []):
            choices = question.get("choices")
            position, text = _resolve_answer(choices, question.get("answer"))
            question["answer_position"] = position
            question["answer_text"] = text

    listening_items = data.get("listening", [])
    audio_dir = BASE_DIR / "static" / "audio"
    image_dir = BASE_DIR / "static" / "images"

    audio_paths = {
        audio.name: f"audio/{audio.name}"
        for audio in sorted(audio_dir.glob("*.mp3"))
    }
    image_paths = sorted(image_dir.glob("*.gif"), key=_image_sort_key)

    for index, item in enumerate(listening_items):
        audio_file = item.get("audio")
        if audio_file and audio_file in audio_paths:
            item["audio_path"] = audio_paths[audio_file]
        if index < len(image_paths):
            item["image_path"] = f"images/{image_paths[index].name}"
        position, text = _resolve_answer(item.get("choices"), item.get("answer"))
        item["answer_position"] = position
        item["answer_text"] = text

    return data


app = Flask(__name__)
LESSON_DATA = _prepare_data()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")


def _build_openai_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    typed_content: List[Dict[str, Any]] = []
    for item in messages[-50:]:  # keep payload compact
        role = item.get("role")
        text = (item.get("text") or "").strip()
        if not text or role not in {"system", "user", "assistant"}:
            continue
        typed_content.append(
            {
                "role": role,
                "content": text,
            }
        )
    return typed_content


def _extract_output_text(payload: Dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    def _collect_from_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        text_parts: List[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type in {"output_text", "text"}:
                        text = item.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
                    elif item_type == "message":
                        nested = _collect_from_content(item.get("content"))
                        if nested:
                            text_parts.append(nested)
                elif isinstance(item, str):
                    text_parts.append(item)
        return "".join(text_parts)

    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            message_block = candidate.get("message")
            if isinstance(message_block, dict):
                collected = _collect_from_content(message_block.get("content"))
                if collected:
                    return collected
            collected = _collect_from_content(candidate.get("content"))
            if collected:
                return collected

    output_chunks = payload.get("output", [])
    text_parts: List[str] = []
    for chunk in output_chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_type = chunk.get("type")
        if chunk_type == "output_text":
            text = chunk.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        elif chunk_type == "message":
            collected = _collect_from_content(chunk.get("content"))
            if collected:
                text_parts.append(collected)

    return "".join(text_parts)


@app.context_processor
def inject_navigation() -> Dict[str, Any]:
    return {
        "lesson_names": LESSON_NAMES,
    }


@app.post("/api/chat")
def chat_api():
    if not OPENAI_API_KEY:
        return (
            jsonify({"error": "Missing OPENAI_API_KEY environment variable."}),
            500,
        )

    payload = request.get_json(silent=True) or {}
    history = payload.get("history") or []
    model = payload.get("model") or "gpt-5-2025-08-07"

    typed_history = _build_openai_input(history)
    if not typed_history:
        typed_history = [
            {
                "role": "system",
                "content": "You are a helpful assistant for Japanese language lessons.",
            }
        ]

    try:
        response = requests.post(
            f"{OPENAI_API_BASE.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": typed_history,
            },
            timeout=45,
        )
    except requests.RequestException as exc:
        return jsonify({"error": "Upstream request failed.", "details": str(exc)}), 502

    if not response.ok:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {"error": response.text or response.reason}
        message: Optional[str] = None
        if isinstance(error_payload, dict):
            raw_error = error_payload.get("error")
            if isinstance(raw_error, dict):
                message = raw_error.get("message") or raw_error.get("code")
            elif isinstance(raw_error, str):
                message = raw_error
        return (
            jsonify(
                {
                    "error": "OpenAI API error.",
                    "details": error_payload,
                    "message": message,
                }
            ),
            response.status_code,
        )

    try:
        data = response.json()
    except ValueError:
        return jsonify({"error": "Invalid response from OpenAI API."}), 502

    output_text = _extract_output_text(data).strip()
    if not output_text:
        return jsonify({"error": "Model returned no text."}), 502

    return jsonify({"text": output_text})


@app.route("/")
def index():
    return render_template("index.html", lesson_key=None)


@app.route("/lesson/<lesson_key>")
def lesson_page(lesson_key: str):
    if lesson_key not in LESSON_DATA:
        abort(404)

    lesson_name = LESSON_NAMES[lesson_key]
    items = LESSON_DATA[lesson_key]

    if lesson_key == "reading":
        return render_template(
            "reading.html",
            lesson_key=lesson_key,
            lesson_name=lesson_name,
            items=items,
        )

    if lesson_key == "listening":
        return render_template(
            "listening.html",
            lesson_key=lesson_key,
            lesson_name=lesson_name,
            items=items,
        )

    return render_template(
        "lesson.html",
        lesson_key=lesson_key,
        lesson_name=lesson_name,
        items=items,
    )


if __name__ == "__main__":
    app.run(debug=True)
