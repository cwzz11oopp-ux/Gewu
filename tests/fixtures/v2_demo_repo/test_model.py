from model import predict


def test_predict_returns_binary_label():
    assert predict(-1.0) == 0
    assert predict(1.0) == 1
