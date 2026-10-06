"""Synthetic discovery set: ML questions whose correct answer is fixed by the data generator's design.

Goal: measure whether the agent team reaches a finding through experiment rather than recall.
The data is regenerated on every run with a hidden seed; it cannot be in model training data.
The team only sees the question, the location of the data files and the column descriptions; never the generator.

Each task:
  question   the question asked of the user (yes/no)
  truth      "yes" | "no"   (from the generator's design)
  describe   data description given to the team
  generate   (rng, out_dir) -> writes the files (train.npz, test.npz, ...)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np


@dataclass
class Task:
    id: str
    question: str
    truth: str
    describe: str
    generate: Callable[[np.random.Generator, Path], None]


def _save(out: Path, name: str, X: np.ndarray, y: np.ndarray) -> None:
    np.savez(out / f"{name}.npz", X=X.astype(np.float32), y=y)


def gen_xor(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        X = rng.uniform(-1, 1, size=(n, 2))
        y = ((X[:, 0] * X[:, 1]) > 0).astype(np.int64)
        flip = rng.random(n) < 0.03
        y[flip] = 1 - y[flip]
        _save(out, name, X, y)


def gen_irrelevant_feature(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        X = rng.normal(size=(n, 3))
        logits = 2.0 * X[:, 0] - 1.5 * X[:, 1]          # f2 (third column) is unrelated to the target
        y = (logits + 0.3 * rng.normal(size=n) > 0).astype(np.int64)
        _save(out, name, X, y)


def gen_shift(rng, out):
    def make(n, lo, hi):
        X = np.column_stack([rng.uniform(lo, hi, n), rng.uniform(-1, 1, n)])
        y = (np.sin(3 * X[:, 0]) + 0.3 * X[:, 1] > 0).astype(np.int64)
        return X, y
    _save(out, "train", *make(4000, -1, 1))
    _save(out, "test_a", *make(2000, -1, 1))
    _save(out, "test_b", *make(2000, 2, 4))          # covariate shift in split B: x0 lies in a range unseen in training


def gen_nonmonotone(rng, out):
    for name, n in (("train", 3000), ("test", 1500)):
        X = rng.uniform(-2, 2, size=(n, 1))
        y = (1.0 - X[:, 0] ** 2 + 0.1 * rng.normal(size=n)).astype(np.float32)   # hill-shaped: not monotone
        _save(out, name, X, y)


def gen_interaction(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        X = rng.normal(size=(n, 2))
        y = (X[:, 0] * np.sign(X[:, 1]) + 0.2 * rng.normal(size=n)).astype(np.float32)   # the effect of f0 flips with the sign of f1
        _save(out, name, X, y)


def gen_important_feature(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        X = rng.normal(size=(n, 3))
        y = (2.5 * X[:, 0] + 0.4 * X[:, 1] + 0.3 * rng.normal(size=n) > 0).astype(np.int64)   # column 0 is decisive
        _save(out, name, X, y)


def gen_imbalanced(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        y = (rng.random(n) < 0.12).astype(np.int64)   # minority class ~12%
        X = rng.normal(size=(n, 2)) + y[:, None] * 0.8
        _save(out, name, X, y)


def gen_sine_regression(rng, out):
    for name, n in (("train", 3000), ("test", 1500)):
        X = rng.uniform(-2, 2, size=(n, 1))
        y = (np.sin(3 * X[:, 0]) + 0.1 * rng.normal(size=n)).astype(np.float32)
        _save(out, name, X, y)


def gen_tree_scaling(rng, out):
    scale = np.array([1000.0, 0.001, 1.0])
    for name, n in (("train", 4000), ("test", 2000)):
        Z = rng.normal(size=(n, 3))
        y = ((Z[:, 0] > 0.2) & (Z[:, 1] < 0.5) | (Z[:, 2] > 1.0)).astype(np.int64)
        _save(out, name, Z * scale, y)   # the columns have very different scales


TASKS = [
    Task("syn-01", "On this dataset, can a linear classifier (logistic regression) achieve more than 75% accuracy on the test set?",
         "no", "Binary classification. /data/train.npz and /data/test.npz; X: (n, 2) numeric features, y: 0/1 label.", gen_xor),
    Task("syn-02", "On this dataset, is the third feature (column 2) needed to predict the target; does test accuracy drop by more than 1 point when it is removed?",
         "no", "Binary classification. /data/train.npz and /data/test.npz; X: (n, 3) numeric features, y: 0/1 label.", gen_irrelevant_feature),
    Task("syn-03", "Does a model trained on the training set achieve accuracy on the test_b split close to that on the test_a split (at most 5 points lower)?",
         "no", "Binary classification. /data/train.npz, /data/test_a.npz, /data/test_b.npz; X: (n, 2), y: 0/1.", gen_shift),
    Task("syn-04", "On this dataset, is the target y a monotone (always increasing or always decreasing) function of the single feature x?",
         "no", "Regression. /data/train.npz and /data/test.npz; X: (n, 1), y: real number.", gen_nonmonotone),
    Task("syn-05", "On this dataset, does the direction of the first feature's (column 0) effect on the target change with the sign of the second feature (column 1)?",
         "yes", "Regression. /data/train.npz and /data/test.npz; X: (n, 2), y: real number.", gen_interaction),
    Task("syn-06", "On this dataset, does an MLP with one hidden layer beat logistic regression in test accuracy by at least 10 points?",
         "yes", "Binary classification. /data/train.npz and /data/test.npz; X: (n, 2) numeric features, y: 0/1 label.", gen_xor),
    Task("syn-07", "On this dataset, does test accuracy drop by more than 5 points when the first feature (column 0) is removed?",
         "yes", "Binary classification. /data/train.npz and /data/test.npz; X: (n, 3) numeric features, y: 0/1 label.", gen_important_feature),
    Task("syn-08", "On this dataset, does a model that always predicts the most frequent class achieve more than 80% accuracy on the test set?",
         "yes", "Binary classification. /data/train.npz and /data/test.npz; X: (n, 2) numeric features, y: 0/1 label.", gen_imbalanced),
    Task("syn-09", "On this dataset, does an MLP with one hidden layer achieve a lower test mean squared error (MSE) than linear regression?",
         "yes", "Regression. /data/train.npz and /data/test.npz; X: (n, 1), y: real number.", gen_sine_regression),
    Task("syn-10", "On this dataset, does standardizing the features (mean 0, variance 1) change a decision tree's test accuracy by more than 1 point?",
         "no", "Binary classification. /data/train.npz and /data/test.npz; X: (n, 3) numeric features (very different scales), y: 0/1 label.", gen_tree_scaling),
]


def materialize(task: Task, out_dir: Path, seed: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    task.generate(np.random.default_rng(seed), out_dir)
    return out_dir
