"""Killable, one-at-a-time process for local Laya inference."""
from __future__ import annotations

import time
from typing import Any


def run_laya_worker(checkpoint_directory: str, requests: Any, responses: Any) -> None:
    """Load only a local checkpoint, then process one parent-submitted decision at a time."""
    load_started = time.perf_counter()
    try:
        from laya import Router
        from laya.common import serialize_state

        router = Router(models={"multilingual": checkpoint_directory}, device="cpu", max_loaded=1)
        agent = router.load("multilingual")
        responses.put({"kind": "ready", "startup_duration_ms": max(
            0, int((time.perf_counter() - load_started) * 1000))})
    except BaseException:
        responses.put({"kind": "startup_error", "code": "runtime_missing"})
        return

    while True:
        try:
            message = requests.get()
        except (EOFError, OSError):
            return
        if message is None:
            return
        request_id = message.get("request_id")
        try:
            operation = message.get("operation")
            questions = message.get("questions")
            if not isinstance(questions, dict):
                raise ValueError("invalid questions")
            if operation == "predict":
                state = message.get("state")
                tokens = agent.tok(serialize_state(state), add_special_tokens=False)["input_ids"]
                if len(tokens) > 7_500:
                    raise ValueError("input_too_large")
                prediction = router.predict(state, questions, model="multilingual", max_len=8_192)
                if prediction.get("usage", {}).get("truncated"):
                    raise ValueError("truncated")
                answer = prediction.get("answers")
                if not isinstance(answer, dict) or not set(questions).issubset(answer):
                    raise ValueError("invalid_result")
                result = answer
            elif operation == "predict_many":
                states = message.get("states")
                if not isinstance(states, list) or len(states) > 8:
                    raise ValueError("invalid_batch")
                batch = []
                for state in states:
                    tokens = agent.tok(serialize_state(state), add_special_tokens=False)["input_ids"]
                    if len(tokens) > 7_500:
                        raise ValueError("input_too_large")
                    batch.append({"state": state, "questions": questions,
                                  "model": "multilingual", "max_len": 8_192})
                predictions = router.predict_batch(
                    batch, batch_size=min(len(batch), 8), sort_by_length=True)
                if not isinstance(predictions, list) or len(predictions) != len(states):
                    raise ValueError("invalid_result")
                result = []
                for prediction in predictions:
                    if prediction.get("usage", {}).get("truncated"):
                        raise ValueError("truncated")
                    answer = prediction.get("answers")
                    if not isinstance(answer, dict) or not set(questions).issubset(answer):
                        raise ValueError("invalid_result")
                    result.append(answer)
            else:
                raise ValueError("invalid_operation")
            responses.put({"kind": "result", "request_id": request_id, "ok": True, "result": result})
        except BaseException as error:
            code = str(error) if str(error) in {
                "input_too_large", "truncated", "invalid_result", "invalid_batch", "invalid_operation",
            } else "prediction_failed"
            try:
                responses.put({"kind": "result", "request_id": request_id, "ok": False, "error_code": code})
            except (EOFError, OSError):
                return
