"""Sentetik görevlerin doğru cevabını üreticiden bağımsız olarak, basit deneylerle doğrular.

Kullanım (ML ortamıyla): ml-env/bin/python evals/synthetic/reference.py
Her görev için 3 farklı veri seed'inde ölçer; cevap üç seed'de de aynı olmalıdır.
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

sys.path.insert(0, str(Path(__file__).parent))
from tasks import TASKS, materialize  # noqa: E402


def load(d, name):
    z = np.load(d / f"{name}.npz")
    return z["X"], z["y"]


def answer(task_id, d):
    if task_id == "syn-01":
        X, y = load(d, "train"); Xt, yt = load(d, "test")
        acc = LogisticRegression().fit(X, y).score(Xt, yt)
        return ("yes" if acc > 0.75 else "no"), f"lojistik regresyon doğruluğu {acc:.3f}"
    if task_id == "syn-02":
        X, y = load(d, "train"); Xt, yt = load(d, "test")
        full = LogisticRegression().fit(X, y).score(Xt, yt)
        drop = LogisticRegression().fit(X[:, :2], y).score(Xt[:, :2], yt)
        return ("yes" if full - drop > 0.01 else "no"), f"tam {full:.3f}, sütun 2'siz {drop:.3f}"
    if task_id == "syn-03":
        X, y = load(d, "train")
        m = MLPClassifier(hidden_layer_sizes=(64, 64), max_iter=400, random_state=0).fit(X, y)
        a, b = m.score(*load(d, "test_a")), m.score(*load(d, "test_b"))
        return ("yes" if a - b <= 0.05 else "no"), f"test_a {a:.3f}, test_b {b:.3f}"
    if task_id == "syn-04":
        X, y = load(d, "train")
        order = np.argsort(X[:, 0]); xs, ys = X[order, 0], y[order]
        bins = np.array_split(np.arange(len(xs)), 10)
        means = np.array([ys[b].mean() for b in bins])
        diffs = np.diff(means)
        mono = bool(np.all(diffs > 0) or np.all(diffs < 0))
        return ("yes" if mono else "no"), f"10 kutunun ortalamaları: {np.round(means, 2).tolist()}"
    if task_id == "syn-05":
        X, y = load(d, "train")
        pos, neg = X[:, 1] > 0, X[:, 1] <= 0
        bp = LinearRegression().fit(X[pos][:, :1], y[pos]).coef_[0]
        bn = LinearRegression().fit(X[neg][:, :1], y[neg]).coef_[0]
        return ("yes" if np.sign(bp) != np.sign(bn) else "no"), f"eğim (sütun1>0) {bp:.2f}, (sütun1≤0) {bn:.2f}"
    if task_id == "syn-06":
        X, y = load(d, "train"); Xt, yt = load(d, "test")
        lr = LogisticRegression().fit(X, y).score(Xt, yt)
        mlp = MLPClassifier(hidden_layer_sizes=(32,), max_iter=800, random_state=0).fit(X, y).score(Xt, yt)
        return ("yes" if mlp - lr >= 0.10 else "no"), f"LR {lr:.3f}, MLP {mlp:.3f}"
    if task_id == "syn-07":
        X, y = load(d, "train"); Xt, yt = load(d, "test")
        full = LogisticRegression().fit(X, y).score(Xt, yt)
        drop = LogisticRegression().fit(X[:, 1:], y).score(Xt[:, 1:], yt)
        return ("yes" if full - drop > 0.05 else "no"), f"tam {full:.3f}, sütun 0'sız {drop:.3f}"
    if task_id == "syn-08":
        X, y = load(d, "train"); Xt, yt = load(d, "test")
        maj = np.bincount(y).argmax()
        acc = float((yt == maj).mean())
        return ("yes" if acc > 0.80 else "no"), f"çoğunluk doğruluğu {acc:.3f}"
    if task_id == "syn-09":
        X, y = load(d, "train"); Xt, yt = load(d, "test")
        lin = float(((LinearRegression().fit(X, y).predict(Xt) - yt) ** 2).mean())
        mlp = float(((MLPRegressor(hidden_layer_sizes=(64,), max_iter=2000, random_state=0).fit(X, y).predict(Xt) - yt) ** 2).mean())
        return ("yes" if mlp < lin else "no"), f"doğrusal MSE {lin:.3f}, MLP MSE {mlp:.3f}"
    if task_id == "syn-10":
        X, y = load(d, "train"); Xt, yt = load(d, "test")
        raw = DecisionTreeClassifier(max_depth=5, random_state=0).fit(X, y).score(Xt, yt)
        sc = StandardScaler().fit(X)
        scaled = DecisionTreeClassifier(max_depth=5, random_state=0).fit(sc.transform(X), y).score(sc.transform(Xt), yt)
        return ("yes" if abs(raw - scaled) > 0.01 else "no"), f"ham {raw:.3f}, ölçekli {scaled:.3f}"
    raise KeyError(task_id)


def main():
    ok = True
    for t in TASKS:
        answers = []
        for seed in (101, 202, 303):
            d = materialize(t, Path(tempfile.mkdtemp()), seed)
            ans, why = answer(t.id, d)
            answers.append(ans)
        consistent = len(set(answers)) == 1
        match = answers[0] == t.truth
        ok &= consistent and match
        print(f"{t.id}: üretici cevabı={t.truth} · referans={answers} · {why} · {'✓' if consistent and match else '✗'}")
    print("TÜMÜ TUTARLI" if ok else "TUTARSIZLIK VAR")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
