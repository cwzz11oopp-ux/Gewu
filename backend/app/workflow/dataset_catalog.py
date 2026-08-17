from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    torchvision_class: str
    marker: str
    input_shape: tuple[int, int, int]
    num_classes: int
    train_size: int
    test_size: int
    normalization: dict[str, tuple[float, ...]] = field(default_factory=dict)
    description: str = ""
    download_artifacts: tuple[str, ...] = ()


_CATALOG = {
    "cifar-10": DatasetSpec(
        name="cifar-10",
        torchvision_class="CIFAR10",
        marker="cifar-10-batches-py",
        input_shape=(3, 32, 32),
        num_classes=10,
        train_size=50000,
        test_size=10000,
        normalization={"mean": (0.4914, 0.4822, 0.4465), "std": (0.2470, 0.2435, 0.2616)},
        description="RGB 32x32 natural images across 10 object classes.",
        download_artifacts=("cifar-10-python.tar.gz",),
    ),
    "cifar-100": DatasetSpec(
        name="cifar-100",
        torchvision_class="CIFAR100",
        marker="cifar-100-python",
        input_shape=(3, 32, 32),
        num_classes=100,
        train_size=50000,
        test_size=10000,
        normalization={"mean": (0.5071, 0.4865, 0.4409), "std": (0.2673, 0.2564, 0.2762)},
        description="RGB 32x32 natural images across 100 fine-grained classes.",
        download_artifacts=("cifar-100-python.tar.gz",),
    ),
    "mnist": DatasetSpec(
        name="mnist",
        torchvision_class="MNIST",
        marker="MNIST/raw",
        input_shape=(1, 28, 28),
        num_classes=10,
        train_size=60000,
        test_size=10000,
        normalization={"mean": (0.1307,), "std": (0.3081,)},
        description="Grayscale 28x28 handwritten digits, 10 classes.",
        download_artifacts=(
            "MNIST/raw/train-images-idx3-ubyte.gz",
            "MNIST/raw/train-labels-idx1-ubyte.gz",
            "MNIST/raw/t10k-images-idx3-ubyte.gz",
            "MNIST/raw/t10k-labels-idx1-ubyte.gz",
        ),
    ),
    "fashion-mnist": DatasetSpec(
        name="fashion-mnist",
        torchvision_class="FashionMNIST",
        marker="FashionMNIST/raw",
        input_shape=(1, 28, 28),
        num_classes=10,
        train_size=60000,
        test_size=10000,
        normalization={"mean": (0.2860,), "std": (0.3530,)},
        description="Grayscale 28x28 clothing images, 10 classes.",
        download_artifacts=(
            "FashionMNIST/raw/train-images-idx3-ubyte.gz",
            "FashionMNIST/raw/train-labels-idx1-ubyte.gz",
            "FashionMNIST/raw/t10k-images-idx3-ubyte.gz",
            "FashionMNIST/raw/t10k-labels-idx1-ubyte.gz",
        ),
    ),
}

_ALIASES = {
    "cifar10": "cifar-10",
    "cifar-10": "cifar-10",
    "cifar100": "cifar-100",
    "cifar-100": "cifar-100",
    "mnist": "mnist",
    "fashionmnist": "fashion-mnist",
    "fashion-mnist": "fashion-mnist",
}


def supported_dataset_names() -> list[str]:
    return sorted(_CATALOG)


def normalize_dataset_name(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        return ""
    key = re.sub(r"[\s_]+", "-", value.strip().lower())
    return _ALIASES.get(key, "")


def canonical_dataset_name_from_text(value: object) -> str:
    """Resolve a catalog dataset explicitly mentioned in free text.

    This is used only while selecting a concrete local directory.  It never
    infers a dataset from a directory basename.
    """
    if not isinstance(value, str):
        return ""
    compact = re.sub(r"[\s_-]+", "", value.lower())
    candidates = sorted(
        ((re.sub(r"[\s_-]+", "", alias), canonical) for alias, canonical in _ALIASES.items()),
        key=lambda item: len(item[0]), reverse=True,
    )
    for alias, canonical in candidates:
        if alias and alias in compact:
            return canonical
    return ""


def dataset_display_name(name: object) -> str:
    canonical = normalize_dataset_name(name)
    if not canonical:
        return ""
    return {
        "cifar-10": "CIFAR-10",
        "cifar-100": "CIFAR-100",
        "mnist": "MNIST",
        "fashion-mnist": "FashionMNIST",
    }[canonical]


def dataset_spec(name: str) -> DatasetSpec:
    normalized = normalize_dataset_name(name)
    if not normalized:
        raise ValueError(f"EXPERIMENT_DATASET_UNSUPPORTED:{name}")
    return _CATALOG[normalized]


def dataset_card(name: str) -> dict:
    """Serializable data card describing the dataset's exact form for LLM prompts."""
    spec = dataset_spec(name)
    return {
        "name": spec.name,
        "input_shape": list(spec.input_shape),
        "channels": spec.input_shape[0],
        "num_classes": spec.num_classes,
        "train_size": spec.train_size,
        "test_size": spec.test_size,
        "normalization": {key: list(value) for key, value in spec.normalization.items()},
        "loader": f"torchvision.datasets.{spec.torchvision_class}(root=os.environ['DATA_ROOT'], download=False)",
        "description": spec.description,
    }


def dataset_present(root: Path, name: str) -> bool:
    marker = root / Path(*dataset_spec(name).marker.split("/"))
    return marker.is_dir() and any(marker.iterdir())


def availability_status(root: Path, source: str) -> list[dict]:
    """Availability of every supported dataset under a local cache directory.

    Status is "cached" when the files are on disk, "downloadable" when the
    online source can provision them before the run, and "missing" when the
    local source requires the user to place the files first.
    """
    entries = []
    for name in supported_dataset_names():
        spec = _CATALOG[name]
        if dataset_present(root, name):
            status = "cached"
        elif source != "local":
            status = "downloadable"
        else:
            status = "missing"
        entries.append(
            {
                "name": name,
                "status": status,
                "marker": spec.marker,
                "card": dataset_card(name),
            }
        )
    return entries


def dataset_download_script() -> str:
    """Download a whitelisted dataset with resume, retries, checksum, and fallback."""
    class_names = {spec.name: spec.torchvision_class for spec in _CATALOG.values()}
    return (
        "import hashlib, os, pathlib, sys, time, urllib.request\n"
        "from torchvision import datasets\n"
        f"classes={class_names!r}\n"
        "root, name = sys.argv[1], sys.argv[2]\n"
        "mirror = sys.argv[3].strip() if len(sys.argv) > 3 else ''\n"
        "retries = int(sys.argv[4]) if len(sys.argv) > 4 else 5\n"
        "cls = getattr(datasets, classes[name])\n"
        "if name.startswith('cifar-'):\n"
        "    root_path = pathlib.Path(root); root_path.mkdir(parents=True, exist_ok=True)\n"
        "    filename, expected = cls.filename, cls.tgz_md5\n"
        "    final = root_path / filename; partial = root_path / (filename + '.part')\n"
        "    urls = []\n"
        "    if mirror:\n"
        "        urls.append(mirror.format(filename=filename, dataset=name) if '{' in mirror else mirror.rstrip('/') + '/' + filename)\n"
        "    urls.append(cls.url)\n"
        "    def md5(path):\n"
        "        digest = hashlib.md5()\n"
        "        with path.open('rb') as stream:\n"
        "            for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)\n"
        "        return digest.hexdigest()\n"
        "    if not (final.is_file() and md5(final) == expected):\n"
        "        last_error = None\n"
        "        for url in urls:\n"
        "            for attempt in range(retries):\n"
        "                try:\n"
        "                    offset = partial.stat().st_size if partial.exists() else 0\n"
        "                    request = urllib.request.Request(url, headers={'User-Agent': 'GEWU-Dataset/1.0'})\n"
        "                    if offset: request.add_header('Range', 'bytes=' + str(offset) + '-')\n"
        "                    with urllib.request.urlopen(request, timeout=60) as response:\n"
        "                        append = offset > 0 and getattr(response, 'status', 200) == 206\n"
        "                        with partial.open('ab' if append else 'wb') as output:\n"
        "                            while True:\n"
        "                                chunk = response.read(1024 * 1024)\n"
        "                                if not chunk: break\n"
        "                                output.write(chunk); output.flush()\n"
        "                    if md5(partial) != expected:\n"
        "                        partial.unlink(missing_ok=True); raise RuntimeError('checksum mismatch')\n"
        "                    os.replace(partial, final); last_error = None; break\n"
        "                except Exception as exc:\n"
        "                    last_error = exc; print('DOWNLOAD_RETRY:' + url + ':' + str(attempt + 1), file=sys.stderr)\n"
        "                    time.sleep(min(2 ** attempt, 8))\n"
        "            if last_error is None: break\n"
        "        if last_error is not None: raise last_error\n"
        "cls(root=root, train=True, download=True)\n"
        "cls(root=root, train=False, download=True)\n"
        "print('DATASET_READY:' + name)\n"
    )


def dataset_presence_script() -> str:
    """Prints JSON {"present": bool} for sys.argv[1]=root, sys.argv[2]=marker."""
    return (
        "import json, pathlib, sys\n"
        "marker = pathlib.Path(sys.argv[1]) / sys.argv[2]\n"
        "present = marker.is_dir() and any(marker.iterdir())\n"
        "print(json.dumps({'present': bool(present)}))\n"
    )


def dataset_batch_presence_script() -> str:
    """Prints JSON {name: bool} for sys.argv[1]=root and sys.argv[2]=JSON {name: marker}."""
    return (
        "import json, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1])\n"
        "markers = json.loads(sys.argv[2])\n"
        "print(json.dumps({name: (root / marker).is_dir() and any((root / marker).iterdir()) "
        "for name, marker in markers.items()}))\n"
    )
