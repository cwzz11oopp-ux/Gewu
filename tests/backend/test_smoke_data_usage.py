import numpy as np
import pytest

from backend.app.workflow.phase2_evidence import progressive_protocol


def test_ipix_smoke_uses_full_binary_data_and_svm_can_fit():
    sklearn = pytest.importorskip("sklearn")
    from sklearn.model_selection import StratifiedKFold
    from sklearn.svm import SVC

    clutter = np.arange(24, dtype=float).reshape(6, 4)
    target = np.arange(24, 48, dtype=float).reshape(6, 4)
    X = np.vstack([clutter, target])
    y = np.concatenate([np.zeros(len(clutter)), np.ones(len(target))])
    original_X, original_y = X.copy(), y.copy()

    train_index, validation_index = next(
        StratifiedKFold(n_splits=2, shuffle=True, random_state=42).split(X, y)
    )
    X_train, X_validation = X[train_index], X[validation_index]
    y_train, y_validation = y[train_index], y[validation_index]

    assert np.array_equal(X, original_X)
    assert np.array_equal(y, original_y)
    assert set(y_train) == {0.0, 1.0}
    assert set(y_validation) == {0.0, 1.0}
    model = SVC(kernel="linear", probability=True).fit(X_train, y_train)
    assert model.predict_proba(X_validation).shape == (len(y_validation), 2)


def test_pytorch_smoke_limits_batches_without_changing_data_or_split():
    torch = pytest.importorskip("torch")
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    features = torch.arange(96, dtype=torch.float32).reshape(8, 4, 3)
    labels = torch.tensor([0, 1, 0, 1, 0, 1, 0, 1])
    original_features, original_labels = features.clone(), labels.clone()
    train_loader = DataLoader(TensorDataset(features[:6], labels[:6]), batch_size=2)
    validation_loader = DataLoader(TensorDataset(features[6:], labels[6:]), batch_size=2)
    model = nn.LSTM(input_size=3, hidden_size=4, batch_first=True)
    classifier = nn.Linear(4, 2)
    optimizer = torch.optim.SGD([*model.parameters(), *classifier.parameters()], lr=0.01)
    loss_fn = nn.CrossEntropyLoss()

    train_batches = 0
    for batch_features, batch_labels in train_loader:
        optimizer.zero_grad()
        _, (hidden, _) = model(batch_features)
        loss = loss_fn(classifier(hidden[-1]), batch_labels)
        loss.backward()
        optimizer.step()
        train_batches += 1
        break
    validation_batches = 0
    with torch.no_grad():
        for batch_features, batch_labels in validation_loader:
            _, (hidden, _) = model(batch_features)
            predictions = classifier(hidden[-1]).argmax(dim=1)
            assert predictions.shape == batch_labels.shape
            validation_batches += 1
            break

    assert train_batches == validation_batches == 1
    assert torch.equal(features, original_features)
    assert torch.equal(labels, original_labels)


def test_small_scale_and_formal_protocols_keep_their_existing_budgets():
    contract = {"seeds": [3, 5, 7], "epochs": 20}
    assert progressive_protocol(contract, "small_scale")["seeds"] == [3, 5]
    assert progressive_protocol(contract, "small_scale")["epochs"] == 5
    assert progressive_protocol(contract, "formal_validation")["seeds"] == [3, 5, 7]
    assert progressive_protocol(contract, "formal_validation")["epochs"] == 20
