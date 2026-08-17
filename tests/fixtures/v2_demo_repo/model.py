DEFAULT_THRESHOLD = 0.5


def predict(value: float, threshold: float = DEFAULT_THRESHOLD) -> int:
    return int(value >= threshold)
