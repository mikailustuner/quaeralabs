"""Sentetik keşif seti: doğru cevabı veri üreticisinin tasarımıyla belirlenen ML soruları.

Amaç: ajan ekibinin bir bulguya ezberden değil deneyle ulaşıp ulaşmadığını ölçmek.
Veriler her koşuda gizli bir seed ile yeniden üretilir; model eğitim verisinde bulunamaz.
Ekip yalnızca soruyu, veri dosyalarının yerini ve sütun açıklamalarını görür; üreticiyi görmez.

Her görev:
  question   kullanıcıya sorulan soru (evet/hayır)
  truth      "yes" | "no"   (üretici tasarımından)
  describe   ekibe verilen veri açıklaması
  generate   (rng, out_dir) -> dosyaları yazar (train.npz, test.npz, ...)
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
        logits = 2.0 * X[:, 0] - 1.5 * X[:, 1]          # f2 (üçüncü sütun) hedefle ilgisiz
        y = (logits + 0.3 * rng.normal(size=n) > 0).astype(np.int64)
        _save(out, name, X, y)


def gen_shift(rng, out):
    def make(n, lo, hi):
        X = np.column_stack([rng.uniform(lo, hi, n), rng.uniform(-1, 1, n)])
        y = (np.sin(3 * X[:, 0]) + 0.3 * X[:, 1] > 0).astype(np.int64)
        return X, y
    _save(out, "train", *make(4000, -1, 1))
    _save(out, "test_a", *make(2000, -1, 1))
    _save(out, "test_b", *make(2000, 2, 4))          # B bölümünde ortak değişken kayması: x0 eğitimde görülmemiş aralıkta


def gen_nonmonotone(rng, out):
    for name, n in (("train", 3000), ("test", 1500)):
        X = rng.uniform(-2, 2, size=(n, 1))
        y = (1.0 - X[:, 0] ** 2 + 0.1 * rng.normal(size=n)).astype(np.float32)   # tepe şeklinde: monoton değil
        _save(out, name, X, y)


def gen_interaction(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        X = rng.normal(size=(n, 2))
        y = (X[:, 0] * np.sign(X[:, 1]) + 0.2 * rng.normal(size=n)).astype(np.float32)   # f0'ın etkisi f1'in işaretine göre ters döner
        _save(out, name, X, y)


def gen_important_feature(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        X = rng.normal(size=(n, 3))
        y = (2.5 * X[:, 0] + 0.4 * X[:, 1] + 0.3 * rng.normal(size=n) > 0).astype(np.int64)   # sütun 0 belirleyici
        _save(out, name, X, y)


def gen_imbalanced(rng, out):
    for name, n in (("train", 4000), ("test", 2000)):
        y = (rng.random(n) < 0.12).astype(np.int64)   # azınlık sınıfı ~%12
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
        _save(out, name, Z * scale, y)   # sütunların ölçekleri çok farklı


TASKS = [
    Task("syn-01", "Bu veri setinde doğrusal bir sınıflandırıcı (lojistik regresyon) test setinde %75'in üzerinde doğruluk elde edebilir mi?",
         "no", "İkili sınıflandırma. /data/train.npz ve /data/test.npz; X: (n, 2) sayısal özellik, y: 0/1 etiket.", gen_xor),
    Task("syn-02", "Bu veri setinde üçüncü özellik (sütun 2) hedefi tahmin etmek için gerekli midir; çıkarıldığında test doğruluğu 1 puandan fazla düşer mi?",
         "no", "İkili sınıflandırma. /data/train.npz ve /data/test.npz; X: (n, 3) sayısal özellik, y: 0/1 etiket.", gen_irrelevant_feature),
    Task("syn-03", "Eğitim setinde eğitilen bir model, test_b bölümünde test_a bölümündekine yakın (en fazla 5 puan düşük) doğruluk elde eder mi?",
         "no", "İkili sınıflandırma. /data/train.npz, /data/test_a.npz, /data/test_b.npz; X: (n, 2), y: 0/1.", gen_shift),
    Task("syn-04", "Bu veri setinde hedef y, tek özellik x'in monoton (sürekli artan ya da sürekli azalan) bir fonksiyonu mudur?",
         "no", "Regresyon. /data/train.npz ve /data/test.npz; X: (n, 1), y: gerçel sayı.", gen_nonmonotone),
    Task("syn-05", "Bu veri setinde birinci özelliğin (sütun 0) hedef üzerindeki etkisinin yönü, ikinci özelliğin (sütun 1) işaretine göre değişir mi?",
         "yes", "Regresyon. /data/train.npz ve /data/test.npz; X: (n, 2), y: gerçel sayı.", gen_interaction),
    Task("syn-06", "Bu veri setinde tek gizli katmanlı bir MLP, test doğruluğunda lojistik regresyonu en az 10 puan geçer mi?",
         "yes", "İkili sınıflandırma. /data/train.npz ve /data/test.npz; X: (n, 2) sayısal özellik, y: 0/1 etiket.", gen_xor),
    Task("syn-07", "Bu veri setinde birinci özellik (sütun 0) çıkarıldığında test doğruluğu 5 puandan fazla düşer mi?",
         "yes", "İkili sınıflandırma. /data/train.npz ve /data/test.npz; X: (n, 3) sayısal özellik, y: 0/1 etiket.", gen_important_feature),
    Task("syn-08", "Bu veri setinde her zaman en sık sınıfı tahmin eden bir model test setinde %80'in üzerinde doğruluk elde eder mi?",
         "yes", "İkili sınıflandırma. /data/train.npz ve /data/test.npz; X: (n, 2) sayısal özellik, y: 0/1 etiket.", gen_imbalanced),
    Task("syn-09", "Bu veri setinde tek gizli katmanlı bir MLP, doğrusal regresyondan daha düşük test ortalama kare hatası (MSE) elde eder mi?",
         "yes", "Regresyon. /data/train.npz ve /data/test.npz; X: (n, 1), y: gerçel sayı.", gen_sine_regression),
    Task("syn-10", "Bu veri setinde özellikleri standartlaştırmak (ortalama 0, varyans 1) bir karar ağacının test doğruluğunu 1 puandan fazla değiştirir mi?",
         "no", "İkili sınıflandırma. /data/train.npz ve /data/test.npz; X: (n, 3) sayısal özellik (ölçekleri çok farklı), y: 0/1 etiket.", gen_tree_scaling),
]


def materialize(task: Task, out_dir: Path, seed: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    task.generate(np.random.default_rng(seed), out_dir)
    return out_dir
