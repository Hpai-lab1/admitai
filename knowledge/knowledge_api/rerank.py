"""Cross-encoder reranking (PRD 9.4: hybrid retrieval, then a cross-encoder reorders the best 8).

bge-reranker-base (MIT), int8 ONNX, run with onnxruntime on the CPU: no PyTorch needed. It reads
the question and each passage together, which judges relevance better than comparing two
separately made vectors. It knows English (and Chinese) only, so Indic-script questions keep their
hybrid order; the multilingual reranker is a Level 2 option in the PRD.

KB_RERANKER=<folder with onnx/model_int8.onnx and tokenizer.json>; empty turns it off.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

CANDIDATES = 8
MAX_TOKENS = 512


class Reranker:
    def __init__(self, folder: str | Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        folder = Path(folder)
        model = next(
            p
            for p in (
                folder / "onnx" / "model_int8.onnx",
                folder / "onnx" / "model.onnx",
            )
            if p.exists()
        )
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        self.session = ort.InferenceSession(
            str(model), opts, providers=["CPUExecutionProvider"]
        )
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS, strategy="only_second")
        self.tokenizer.enable_padding()
        self._lock = threading.Lock()  # one batch at a time keeps CPU use predictable

    def scores(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        enc = self.tokenizer.encode_batch([(query, p) for p in passages])
        feed = {
            "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
        }
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
        with self._lock:
            logits = self.session.run(None, feed)[0]
        return [float(x) for x in np.asarray(logits).reshape(len(passages), -1)[:, 0]]


def make_reranker(folder: str) -> Reranker | None:
    if not folder:
        return None
    try:
        return Reranker(folder)
    except (OSError, StopIteration, ImportError):
        return None
