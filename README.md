# QuaeraLabs

Açık kaynaklı, kâr amacı gütmeyen bir AI araştırma ekibi. Kullanıcı bir araştırma sorusu girer; dokuz ajandan oluşan bir ekip soruyu literatürden rapora kadar yürütür, insan ise yön verir ve maliyetli ya da kritik adımları onaylar. İlk alanlar: **Matematik** (Lean 4 ile doğrulanmış ispatlar) ve **AI/ML araştırması**.

> **Durum:** Faz 4 · Kapalı alfaya hazırlık. Yazılım tarafı hazır: araştırma hafızası, "Sorun bildir" → hata vakası seti, dürüstlük denetimi (bugüne kadarki tüm projelerde uydurma alıntı ve sahte doğrulama 0) ve tek komutla kurulum (Linux, WSL2; macOS henüz yok). Katılımcılarla yürütülecek kısım bekliyor: [alfa programı](docs/alpha/README.md). Ayrıntı: [Faz 4](docs/faz4-durum.md) · [Faz 3](docs/faz3-durum.md) · [Faz 2](docs/faz2-durum.md) · [Faz 1](docs/faz1-durum.md) · [Faz 0](docs/faz0-durum.md).

## Kurulum

```bash
./install.sh            # Linux ya da Windows + WSL2; --with-ml, --with-lean, --all
quaera doctor
```
Ayrıntılar ve platform durumu: [docs/kurulum.md](docs/kurulum.md). macOS henüz desteklenmiyor.

## Kullanım

```bash
uv sync
uv run quaera doctor                       # bwrap, claude CLI, Lean + Mathlib, REPL
uv run quaera ask "Her n doğal sayısı için n³ - n, 6'ya bölünür mü?" --budget 3 --auto-approve-under 3
uv run quaera ask "Doğrusal model %75'i geçer mi?" --domain ml --data-dir ./veri --scope "X: (n,2), y: 0/1" --budget 2 --autonomy cap
uv run quaera status <proje>               # aşamalar, hipotezler, maliyet
uv run quaera resume <proje>               # kesilen araştırmaya kaldığı yerden devam
uv run quaera branch <proje> --at 42 --name yeni-dal   # bir olay noktasından dallanma
uv run quaera verify <proje>               # ispatı temiz ortamda yeniden derle
uv run quaera serve                        # web arayüzü: http://127.0.0.1:8765
uv run quaera memory search "doğrusal sınıflandırıcı"  # araştırma hafızası (bu makinedeki geçmiş projeler)
uv run quaera audit [--lean]               # dürüstlük denetimi: uydurma alıntı, sahte doğrulama
uv run quaera triage scan                  # hata işaretleri; triage add/export: hata vakaları
```

**Web arayüzü (Faz 3):** önce bir kez `cd web && npm install && npm run build`. Arayüz İngilizcedir; sıcak nötr tonlarda, açık tema varsayılan, koyu tema da var. Sol panel kısa proje adlarını ve projeler arası öğrenilenleri (bağlamsal hafıza) gösterir. Proje yöneticisi araştırmayı izler ve ekibi bölmeden sorularını yanıtlar; ekibe not yalnızca sen gönderirsen gider ([ADR 0016](docs/adr/0016-arayuz-v3-yonetici-baglamsal-hafiza.md)). Model çıktılarının dili `QUAERA_LANGUAGE` ile seçilir (varsayılan English). Bu makinede kurulu Codex CLI ve OpenCode CLI otomatik tespit edilir ve çapraz kontrolde (Eleştirmen, Doğrulayıcı, paralel şeritler) farklı model ailesi olarak kullanılır. Matematikte iki kip vardır: **Verify** (bir iddiayı sına) ve **Discover** (açık bir probleme çok modelli fikir üretimi, çapraz inceleme ve Lean lemma programıyla saldır; yalnızca Lean'in doğruladığı sonuç sayılır) ([ADR 0017](docs/adr/0017-kesif-kipi-coklu-model.md)). Laboratuvar ekranında ekip canlı izlenir: ajanın o an derlediği kod görünür, ajana tıklanınca söyledikleri tam metin olarak açılır. Matematik ve ML deneyleri adım adım görselleştirilir, hipotez sıralaması ölçütleriyle gösterilir, paralel ajanlar şeritleriyle izlenir ([ADR 0015](docs/adr/0015-arayuz-v2-paralel-ajanlar.md)). Ayrıca onay kartları yanıtlanır, `@ajan` ile mesaj yazılır, tamamlanan bir aşamadan dallanılır; kanıt grafiği, Lean ispatı, rapor (Markdown, PDF yazdırma, imzalı RO-Crate kanıt paketi) ve anahtarsız tekrar oynatma aynı arayüzdedir. Sunucu yalnızca 127.0.0.1'de dinler ve başka sitelerden gelen istekleri reddeder ([ADR 0011](docs/adr/0011-yerel-web-arayuzu.md)).

Projeler `~/.quaera/projects/` altında tutulur (`QUAERA_HOME` ile değiştirilebilir). Her projede `quaera.db` (olay kaydı), `cas/` (içerik adresli dosyalar), `rapor.md` ve `bundle.json` bulunur.

**Gereksinimler (Faz 1):** Linux, `bwrap`, giriş yapılmış `claude` CLI (model sağlayıcı), Lean 4 + Mathlib (`lean/` dizini; `lake exe cache get` ve `lake build REPL/repl`). Lean REPL, Mathlib ile yaklaşık 4–5 GB bellek kullanır.

## Repo yapısı

| Yol | İçerik |
| --- | --- |
| `schemas/v1/` | Araştırma nesneleri, mesajlar, ajan/skill tanımları ve kanıt paketi için JSON Schema'lar ([rehber](docs/semalar.md)) |
| `agents/` | Dokuz ajanın rol kartı, iş akışı, skill listesi ve izinleri |
| `skills/` | 24 skill: araçlar (MCP) ve gereken izinler |
| `examples/` | Şemaların uçtan uca örnekleri: Lean ispatı, itirazlı bir ML deneyi, negatif sonuç; örnek kanıt paketi manifesti |
| `src/quaera/` | Motor: veri deposu, izinler, model gateway, sandbox, Lean, MCP araçları, orkestratör, rapor, kanıt paketi, web sunucusu, CLI |
| `web/` | Arayüz (React + TypeScript, Vite); `e2e/` tarayıcı testleri |
| `lean/` | Mathlib'e sabitlenmiş Lean çalışma alanı ve REPL |
| `tools/validate.py` | Şemaların ve şemanın tek başına ifade edemediği kuralların doğrulayıcısı |
| `evals/` | Değerlendirme setleri ve başlangıç değerleri ([rehber](evals/README.md)) |
| `docs/adr/` | Mimari karar kayıtları |
| `docs/policy/` | Kullanım politikası ve QuaeraLabs AI etiketi |
| `docs/research/` | Araştırmacı görüşme rehberi, alfa aday listesi, kullanılabilirlik testi |
| `docs/alpha/` | Kapalı alfa programı: davet, başlangıç, rıza, görüşme, hata incelemesi, ölçütler |
| `install.sh` | Tek komutla kurulum; `tools/install_test.sh` temiz konteynerlerde sınar |

## Hızlı başlangıç

```bash
uv sync
uv run pytest                       # hızlı testler (sahte model ve sahte Lean ile)
uv run pytest -m lean               # gerçek Lean + Mathlib testleri (yavaş)
# Ağır işleri bellek/CPU sınırıyla çalıştırın (ADR 0010):
tools/limited.sh 8G 300% uv run pytest -m lean
uv run python tools/validate.py     # tüm repo
uv run python evals/run_evals.py    # değerlendirme setleri (ağ erişimi gerekir)
# Arayüz uçtan uca + erişilebilirlik (sahte modeller, para harcamaz):
uv run python tests/e2e_server.py /tmp/qe2e 8766 &   # ayrı terminalde
cd web && npm run test:e2e
```

## Temel ilkeler

1. Kaynağı olmayan iddia yoktur.
2. Tek bir doğruluk skoru yoktur; kanıt durumu ve bağımsızlık seviyesi gösterilir.
3. Negatif sonuç da sonuçtur.
4. Para harcamadan önce sorulur; hiçbir ajan yayınlayamaz ya da bütçe tavanını yükseltemez.
5. Matematikte "doğrulandı" etiketini yalnızca Lean verir.
6. Her şey tekrar üretilebilir; ön kayıt sonuç görülmeden kilitlenir.
7. AI'ın yaptığı açıktır: her rapor QuaeraLabs AI etiketi taşır.

## Lisans

Kod [Apache License 2.0](LICENSE). Yayınlanan araştırma raporları ve şemalar CC BY 4.0. Katkı için [CONTRIBUTING.md](CONTRIBUTING.md).
