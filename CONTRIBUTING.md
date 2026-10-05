# Katkı rehberi

QuaeraLabs'e katkı verdiğiniz için teşekkürler. Bu proje [davranış kurallarına](CODE_OF_CONDUCT.md) tabidir.

## Kurulum

```bash
uv sync
uv run pytest                       # tüm testler
uv run python tools/validate.py     # şemalar, örnekler, ajan ve skill tanımları
uv run python evals/run_evals.py    # değerlendirme setleri (ağ erişimi gerekir)
```

## Nereye katkı verebilirsiniz?

| Alan | Nerede | Not |
| --- | --- | --- |
| Skill | `skills/*.yaml` | v1.0'da topluluğa açık en kolay alan. Skill kendi izinlerini `requiredPermissions` ile beyan eder; bir ajana izinlerini aşan skill atanamaz. |
| Değerlendirme vakası | `evals/sets/` | Eleştirmen testine yeni hatalı ya da temiz vaka, kaynak setine yeni gerçek/uydurma kimlik. |
| Örnek araştırma | `examples/*.json` | Şemanın temsil edemediği bir araştırma durumu bulursanız örnekle birlikte issue açın. |
| Mimari karar | `docs/adr/` | Yeni karar için yeni bir ADR; kabul edilmiş ADR değiştirilmez. |
| Yeni ajan rolü | — | v1.1'de açılacak. |

## Kurallar

- Her değişiklik `uv run pytest` ve `uv run python tools/validate.py` testlerinden geçmelidir.
- Şemada geriye uyumsuz değişiklik yeni ana sürüm gerektirir (bkz. [ADR 0008](docs/adr/0008-sema-surumleme.md)).
- Yeni bir doğrulama kuralı eklerseniz onu ihlal eden bir negatif test de ekleyin (`tests/test_validate.py`).
- Commit mesajları kısa ve açıklayıcı olsun; büyük değişiklikler için önce issue açın.

## Lisans

Katkılarınız [Apache License 2.0](LICENSE) altında lisanslanır. Yayınlanan araştırma raporları ve şemalar CC BY 4.0 altındadır.
