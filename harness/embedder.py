"""The local text embedder shared by the harness (docs/NOTES.md "Semantic search"):
BAAI/bge-small-en-v1.5 (384-d) via fastembed, on the GPU (onnxruntime-gpu CUDA EP) by
default, the CPU only when CUDA doesn't load or UO_EMBED_CPU=1 is set. Nothing leaves the
machine after the one-time model download to ~/.cache/fastembed.

Users: discord_search.py (chat chunks), discord_kb.py (claims), knowledge.py via ctl
(`ctl know search` / `brief`). Needs fastembed-gpu + the nvidia-* CUDA 13 wheels in the
running Python (installed in both .venv-discord and the system Python 3.13 that ctl uses).
"""

import os

import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"
MODEL_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "fastembed")

_model = None


def available() -> bool:
    import importlib.util
    return importlib.util.find_spec("fastembed") is not None


def loaded() -> bool:
    return _model is not None


def model():
    """The embedder, on the GPU when CUDA loads, else the CPU. The CUDA 13 / cuDNN 9 DLLs
    come from the Python environment's nvidia-* wheels (docs/NOTES.md)."""
    global _model
    if _model is None:
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        import warnings
        warnings.filterwarnings("ignore", message=".*symlinks.*")
        warnings.filterwarnings("ignore", message=".*CUDAExecutionProvider.*")
        import onnxruntime as ort
        ort.set_default_logger_severity(3)  # shape ops placed on the CPU are expected; errors only
        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers() and not os.environ.get("UO_EMBED_CPU"):
            try:
                ort.preload_dlls()
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            except Exception:
                pass
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL, cache_dir=MODEL_CACHE, providers=providers)
    return _model


def device() -> str:
    sess = getattr(getattr(model(), "model", None), "model", None)  # fastembed internals
    return "cuda" if sess and "CUDAExecutionProvider" in sess.get_providers() else "cpu"


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def passages(texts) -> np.ndarray:
    """L2-normalised float32 vectors (n, 384) of documents."""
    return _unit(list(model().embed(list(texts), batch_size=64)))


def query(text: str) -> np.ndarray:
    """L2-normalised float32 vector (384,) of a search query (bge's query instruction)."""
    return _unit(next(iter(model().query_embed([text]))))
