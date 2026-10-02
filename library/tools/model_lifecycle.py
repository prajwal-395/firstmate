import contextlib
import gc
import sys

_loaded_models = {}


def _torch():
    """torch, if a model loader has already imported it; else None.

    Never imported here: the runner and deterministic steps import this
    module, and a top-level `import torch` charged each of them 0.9s for
    memory management that only applies once a loader brought torch in. A
    process that never imported torch holds no torch memory to free.
    """
    return sys.modules.get("torch")


def _empty_gpu_cache():
    """Empty GPU cache gracefully depending on the available backend."""
    torch = _torch()
    if torch is None:
        return
        
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        torch.mps.empty_cache()


def load_model(name: str, loader_fn):
    """Load a model and register it in the lifecycle manager.
    
    If the model is already loaded, returns the cached instance.
    """
    if name in _loaded_models:
        return _loaded_models[name]
        
    print(f"[{name}] Loading model into memory...", file=sys.stderr)
    # Imported here: callers load this file by bare name off a sys.path
    # hack, before the repository root is on the path.
    from library.tools import perf_ledger
    with perf_ledger.span("model_load", model=name):
        model = loader_fn()
    _loaded_models[name] = model
    return model


def unload_model(name: str):
    """Unload a specific model and free its GPU memory."""
    if name in _loaded_models:
        print(f"[{name}] Unloading model from memory...", file=sys.stderr)
        
        model = _loaded_models.pop(name)
        
        # Try to explicitly move PyTorch models to CPU if possible
        if _torch() is not None and hasattr(model, 'to'):
            try:
                model.to('cpu')
            except Exception:
                pass
                
        # If it's a tuple (like whisperx returns multiple models), process each
        if isinstance(model, tuple):
            for m in model:
                if _torch() is not None and hasattr(m, 'to'):
                    try:
                        m.to('cpu')
                    except Exception:
                        pass
        
        del model
        
    # Force garbage collection and empty caches
    gc.collect()
    _empty_gpu_cache()


def unload_all():
    """Unload all currently loaded models."""
    names = list(_loaded_models.keys())
    if not names:
        return
        
    print(f"Unloading all models: {', '.join(names)}", file=sys.stderr)
    for name in names:
        model = _loaded_models.pop(name)
        
        if _torch() is not None and hasattr(model, 'to'):
            try:
                model.to('cpu')
            except Exception:
                pass
                
        if isinstance(model, tuple):
            for m in model:
                if _torch() is not None and hasattr(m, 'to'):
                    try:
                        m.to('cpu')
                    except Exception:
                        pass
                        
        del model
        
    gc.collect()
    _empty_gpu_cache()


@contextlib.contextmanager
def managed_model(name: str, loader_fn):
    """Context manager for loading and automatically unloading a model."""
    try:
        model = load_model(name, loader_fn)
        yield model
    finally:
        unload_model(name)
