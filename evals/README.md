# Değerlendirme setleri

| Set | Dosya | Durum | Ne ölçer |
| --- | --- | --- | --- |
| Eleştirmen testi | `sets/critic-test.yaml` | v0.1 · 30 hatalı + 6 temiz vaka | Yerleştirilmiş hataları yakalama ve temiz raporlarda yanlış alarm vermeme |
| Kaynak doğrulama | `sets/citations.yaml` | v0.1 · 8 gerçek + 6 uydurma kimlik | Uydurma kaynağı kabul etmeme (sıfır tolerans) |
| Bilinen bulgular | `sets/known-findings.yaml` | Seçim protokolü hazır, vakalar alan danışmanında | Ajan ekibinin yayımlanmış bulguları sonucu bilmeden yeniden keşfetmesi |
| miniF2F (Lean 4) | `sets/minif2f.yaml` | Kaynak tanımlı, sürüm Faz 1'de sabitlenecek | Biçimsel ispat bulma oranı |

## Çalıştırma

```bash
uv run python evals/run_evals.py                 # tümü
uv run python evals/run_evals.py --only citations
```

Sonuçlar `results/baseline-<tarih>.json` dosyasına yazılır. Uydurma bir kaynak kabul edilirse komut 1 ile çıkar.

## Başlangıç değerleri

Güncel değerler ve yorumları: [../docs/faz0-durum.md](../docs/faz0-durum.md#başlangıç-değerleri).

- **Çoğunluk sınıfı temel çizgisi** her rapora en sık kategoriyle itiraz eder. Vakalardan bağımsız bir alt sınırdır; bir LLM Eleştirmen hem yakalama hem yanlış alarm oranında bunu geçmelidir.
- Faz 0'da kural tabanlı bir Eleştirmen de denendi ancak kuralları vakaları okuyarak yazıldığı için sete uydurulmuş sonuç verdi (30/30) ve kaldırıldı. Bu nedenle setin vakaları geliştirme sırasında ajan promptlarına ya da kurallara asla girdi olarak verilmemelidir; bir sonraki sürümde ayrı bir gizli test bölümü ayrılmalıdır.
