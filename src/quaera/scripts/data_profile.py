"""Data profile (deterministic, no model): shapes, dtypes, class balance, basic statistics and linear
correlation with the target for the .npz/.npy/.csv files under /data. Runs in the sandbox without network.
Output: a single line `QUAERA_PROFILE {...}`."""

import json
import os

import numpy as np

MAX_COLS = 20


def describe(name, arr):
    arr = np.asarray(arr)
    out = {"name": name, "shape": list(arr.shape), "dtype": str(arr.dtype)}
    if arr.size == 0 or arr.dtype.kind not in "biuf":
        return out
    if arr.ndim == 1:
        uniq = np.unique(arr)
        if len(uniq) <= 20 and np.allclose(uniq, np.round(uniq)):
            vals, counts = np.unique(arr, return_counts=True)
            out["classes"] = {str(v.item()): int(c) for v, c in zip(vals, counts)}
            out["majority_rate"] = round(float(counts.max() / counts.sum()), 4)
        else:
            out["stats"] = {"mean": round(float(arr.mean()), 4), "std": round(float(arr.std()), 4),
                            "min": round(float(arr.min()), 4), "max": round(float(arr.max()), 4)}
    elif arr.ndim == 2:
        cols = arr[:, :MAX_COLS].astype(float)
        out["columns"] = [{"i": i, "mean": round(float(c.mean()), 4), "std": round(float(c.std()), 4),
                           "min": round(float(c.min()), 4), "max": round(float(c.max()), 4)} for i, c in enumerate(cols.T)]
    return out


def main():
    files = []
    for root, _, names in os.walk("/data"):
        for n in sorted(names):
            path = os.path.join(root, n)
            rel = os.path.relpath(path, "/data")
            try:
                if n.endswith(".npz"):
                    z = np.load(path)
                    arrays = {k: z[k] for k in z.files}
                    entry = {"file": rel, "arrays": [describe(k, v) for k, v in arrays.items()]}
                    X, y = arrays.get("X"), arrays.get("y")
                    if X is not None and y is not None and X.ndim == 2 and len(X) == len(y) and y.dtype.kind in "biuf":
                        yc = y.astype(float) - y.mean()
                        corr = []
                        for i in range(min(X.shape[1], MAX_COLS)):
                            xc = X[:, i].astype(float) - X[:, i].mean()
                            den = np.sqrt((xc ** 2).sum() * (yc ** 2).sum())
                            corr.append(round(float((xc * yc).sum() / den), 4) if den > 0 else None)
                        entry["corr_with_y"] = corr
                        # For the UI: the first 5 rows and per-class histograms of the (at most 3) features most correlated with the target
                        entry["preview"] = {"columns": [f"x{i}" for i in range(min(X.shape[1], 8))] + ["y"],
                                            "rows": [[round(float(v), 4) for v in X[r, :8]] + [round(float(y[r]), 4)]
                                                     for r in range(min(5, len(X)))]}
                        classes = np.unique(y)
                        if 2 <= len(classes) <= 6:
                            order = sorted((i for i, c in enumerate(corr) if c is not None), key=lambda i: -abs(corr[i]))[:3]
                            hists = []
                            for i in order:
                                col = X[:, i].astype(float)
                                edges = np.linspace(col.min(), col.max(), 21)
                                hists.append({"feature": i, "corr": corr[i], "edges": [round(float(e), 4) for e in edges],
                                              "byClass": {str(c.item()): np.histogram(col[y == c], bins=edges)[0].tolist() for c in classes}})
                            entry["histograms"] = hists
                elif n.endswith(".npy"):
                    entry = {"file": rel, "arrays": [describe(n, np.load(path))]}
                elif n.endswith(".csv"):
                    data = np.genfromtxt(path, delimiter=",", names=True, max_rows=100000)
                    entry = {"file": rel, "columns": list(data.dtype.names or []), "rows": int(data.shape[0])}
                else:
                    entry = {"file": rel, "bytes": os.path.getsize(path)}
            except Exception as exc:  # an unreadable file must not break the profile
                entry = {"file": rel, "error": str(exc)[:200]}
            files.append(entry)
    print("QUAERA_PROFILE " + json.dumps({"files": files}, ensure_ascii=False))


if __name__ == "__main__":
    main()
