"""Cross-encoder reranking (PRD 9.4: hybrid retrieval, then a cross-encoder reorders the best 8).

bge-reranker-base (MIT), int8 ONNX, run with onnxruntime on the CPU: no PyTorch needed. It reads
the question and each passage together, which judges relevance better than comparing two
separately made vectors. It knows English (and Chinese) only, so Indic-script questions keep their
hybrid order; the multilingual reranker is a Level 2 option in the PRD.

KB_RERANKER=<folder with onnx/model_int8.onnx and tokenizer.json>; empty turns it off.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

CANDIDATES = 8
# How much of each (question, passage) pair the cross-encoder reads. Its cost grows with this
# length; on a laptop CPU reranking was ~95% of a search. Measured on RUAS's 54 answerable test
# questions (17 Nov 2026): 512 -> hit@1 12, hit@4 36; 256 -> hit@1 13, hit@4 36, in about half
# the time (the start of a passage, with its title and section, carries what decides relevance).
MAX_TOKENS = int(os.environ.get("KB_RERANK_MAX_TOKENS", "256"))
MEMO_SIZE = 4096  # remembered (question, passage) scores, a few MB at most


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
        # (question, passage) -> score. The score depends only on the two texts, so a cached one
        # is never stale: a changed page has different text and is scored afresh. A repeated
        # question (a demo, the evaluation gate re-asking its test set) then skips the model.
        self._memo: OrderedDict[tuple[str, str], float] = OrderedDict()

    def scores(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        keys = [(query, p) for p in passages]
        with self._lock:
            known = {k: self._memo[k] for k in keys if k in self._memo}
            for k in known:
                self._memo.move_to_end(k)
        todo = [k for k in dict.fromkeys(keys) if k not in known]
        if todo:
            known.update(zip(todo, self._run(todo)))
            with self._lock:
                for k in todo:
                    self._memo[k] = known[k]
                while len(self._memo) > MEMO_SIZE:
                    self._memo.popitem(last=False)
        return [known[k] for k in keys]

    def _run(self, pairs: list[tuple[str, str]]) -> list[float]:
        enc = self.tokenizer.encode_batch(pairs)
        feed = {
            "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
        }
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
        with self._lock:
            logits = self.session.run(None, feed)[0]
        return [float(x) for x in np.asarray(logits).reshape(len(pairs), -1)[:, 0]]


def make_reranker(folder: str) -> Reranker | None:
    if not folder:
        return None
    try:
        return Reranker(folder)
    except (OSError, StopIteration, ImportError):
        return None
