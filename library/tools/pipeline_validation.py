def require_keys(data: dict, keys: list[str], step_name: str = "unknown") -> None:
    """Raise ValueError if any required key is missing or empty from data."""
    missing = [k for k in keys if k not in data or data[k] is None]
    if missing:
        raise ValueError(
            f"Step '{step_name}': missing required input keys: {missing}. "
            f"Available keys: {list(data.keys())}"
        )

def require_type(value, expected_type, key_name: str, step_name: str = "unknown"):
    """Raise TypeError if value is not the expected type."""
    if not isinstance(value, expected_type):
        raise TypeError(
            f"Step '{step_name}': key '{key_name}' expected {expected_type.__name__}, "
            f"got {type(value).__name__}"
        )
