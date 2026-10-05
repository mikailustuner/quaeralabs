# QuaeraLabs — UI Konsept Görselleri (Codex görevi)

## Görev

Aşağıdaki 5 prompt ile 5 adet UI konsept görseli üret ve `images/` klasörüne kaydet.

- Görselleri image generation aracınla üret (ör. `gpt-image-1`); her prompt için **1 görsel**.
- Boyut: **1536×1024** (yatay, masaüstü ekran oranı). Kalite: high.
- Her görselde **"Ortak stil"** bölümündeki stil bloğunu, ilgili ekran promptunun başına ekleyerek kullan.
- Dosya adları: `01-laboratuvar.png`, `02-yeni-arastirma.png`, `03-kanit-grafigi.png`, `04-lean-ispat.png`, `05-arastirma-raporu.png`.
- Referans tasarım: `design-reference/lab-screen-B2.html`. Bu, laboratuvar ekranının mevcut tasarımı; renkleri, yazı tiplerini ve bileşen stilini oradan doğrula.
- Bir görselde yazılar bozuk ya da okunmaz çıkarsa o görseli **en fazla 1 kez** yeniden üret. Sonuçları `images/NOTES.md` dosyasına kısaca yaz: hangi görsel kaç denemede çıktı, ne sorun vardı.

## Ürün bağlamı (görsel üretiminde referans)

QuaeraLabs, kâr amacı gütmeyen, açık kaynaklı bir "AI bilim laboratuvarı". Kullanıcı bir araştırma sorusu girer; 8 AI ajandan oluşan bir ekip araştırmayı yürütür: Direktör, Literatür, Hipotez, Deney tasarımcısı, Mühendis, Analist, Eleştirmen ve Yazar. İnsan, maliyetli adımları onaylar. İlk alanlar **Matematik** (Lean ile doğrulanmış ispatlar) ve **AI/ML araştırması**. Kullanıcı kendi API anahtarını kullanır, bu yüzden token bütçesi her ekranda görünür.

---

## Ortak stil (her prompta ekle)

```
High-fidelity UI design screenshot of a modern dark-mode web application called "QuaeraLabs", an open-source AI research lab where a team of AI agents conducts scientific research. Desktop browser viewport, flat front-on screenshot, no device frame, no perspective, no people, no hands.

Visual style: modern, calm, premium developer-tool aesthetic. Near-black background #0A0B0D; panels/cards #121418 with 1px subtle borders rgba(255,255,255,0.07) and 16px rounded corners; inner tiles slightly lighter. Single warm amber accent #F5B83D used sparingly for primary buttons and highlights; mint green #4ADEA3 for "running/live/verified"; soft blue #7DAAFF for data and "supporting"; soft orange #FF9B6A for "critique/contradicting". Typography: clean geometric sans-serif (Geist-like) for UI text, monospace (Geist Mono-like) for numbers, IDs and code. Generous but information-dense layout, crisp alignment on a grid, high contrast readable text, no gradients on backgrounds, no glassmorphism, no emoji, thin-stroke line icons only.

Agents are shown as small cute flat-vector scientist avatars in 44px rounded-square tiles (each with a distinct accessory: bow tie, round glasses, lightbulb, flask, lab goggles, bar-chart badge, magnifying glass, beret).

UI text is in Turkish; keep labels short and render all text sharp and legible.
```

---

## 1) Laboratuvar ekranı — canlı araştırma → `01-laboratuvar.png`

```
Screen: the live "lab" dashboard of one research project.
Top bar: small amber square logo with "QuaeraLabs", breadcrumb "Laboratuvarım / Isınma çalışması", a wide command search field "Ara veya ekibe komut ver ⌘K", a small circular budget ring with "$3.40 / $10", and a mint "Canlı" pill with a dot.
Header: small tags "AI / ML", "Q-0012"; large title "Öğrenme oranı ısınması küçük transformer modellerinde gerekli mi?"; below it a 7-segment progress bar labeled Soru, Literatür, Hipotez, Deney, Analiz, Eleştiri, Rapor — first three amber, "Deney" half filled.
Three-column layout:
Left card "Araştırma ekibi": vertical list of 8 agents with scientist avatars and status lines; "Mühendis" row highlighted mint with "Run 5/9 çalışıyor" and a thin progress bar; "Analist" row highlighted amber with "Onayınızı bekliyor".
Center: a card "Eğitim kaybı · E-0031" with 4 metric tiles (2.47, 3.4k / 10k, ~18 dk, 5 / 9) and a line chart of 4 smooth loss curves (blue, dashed blue, orange plateauing, short mint live curve with a dot); below, an approval card with a thin amber gradient border: "Analist onayınızı istiyor" with small chips "+12 run", "~$2.40", "~3 sa GPU" and buttons "Onayla" (amber) and "Düzenle"; below, an "Etkinlik" timeline with colored dots.
Right: "Hipotezler" card with three hypothesis tiles H1 (blue "Test ediliyor" badge and a small evidence bar), H2 (orange "Eleştiri altında"), H3 ("Sırada"); and a "Tekrar üretilebilirlik" checklist with mint check marks.
```

## 2) Yeni araştırma başlat → `02-yeni-arastirma.png`

```
Screen: "Yeni araştırma" — creating a new research project, centered single-column form (max width ~760px) on the dark background, with the same top bar.
Step indicator at top: "1 Soru · 2 Ekip · 3 Bütçe" with step 1 active in amber.
Large multi-line text area labeled "Araştırma sorusu" containing "Seyrek dikkat, uzun bağlamda tam dikkatle aynı doğruluğa ulaşabilir mi?".
Below: "Alan" segmented choice with two large selectable cards: "Matematik" (subtitle "Lean ile doğrulanmış ispatlar", small sigma line icon) and "AI / ML" (subtitle "Deneyler ve ablasyonlar", small neural-net line icon) — "AI / ML" selected with an amber border.
A helper card from the "Hipotez" agent avatar: "Sorunuzu test edilebilir hale getirmek için 2 öneri var" with two suggestion chips.
Section "Ekip": row of 8 small scientist avatars with toggles, all on.
Section "Bütçe": slider "Token bütçe tavanı" set to "$10", a masked API key field "sk-••••••••3f9c" with a mint "Bağlı" label, a note "Anahtarınız yalnızca tarayıcınızda saklanır".
Bottom right: secondary button "Taslak kaydet" and primary amber button "Araştırmayı başlat".
```

## 3) Hipotez ve kanıt grafiği → `03-kanit-grafigi.png`

```
Screen: "Kanıt grafiği" — an interactive node-link graph view of a research project's knowledge.
Full-width canvas area with a subtle dot grid. Nodes are rounded rectangles with monospace IDs: a root question node "Q-0012" on the left; three hypothesis nodes "H1", "H2", "H3"; experiment nodes "E-0031", "E-0032"; result nodes; a critique node with an orange outline; a "derived hypothesis" node "H4" with a dashed outline. Edges are thin curved lines labeled with small pills: "test eder", "destekler" (blue), "çürütür" (orange), "türetildi".
H1 node is selected, with a soft amber outline.
Right side panel (360px) for the selected node: title "H1 · Küçük modellerde düşük oranlarda ısınma gereksizdir", a status badge "Test ediliyor", an evidence summary with a horizontal stacked bar (2 destekleyen blue, 0 çürüten orange, 7 bekliyor gray), a list "Bağımsızlık seviyesi: L1", links to experiments, and a button "Eleştirmen'den değerlendirme iste".
Top left: filter chips "Tümü", "Hipotezler", "Deneyler", "Eleştiriler", "Negatif sonuçlar"; bottom left: zoom controls and a mini-map.
```

## 4) Matematik — Lean ispat görünümü → `04-lean-ispat.png`

```
Screen: a mathematics research project in QuaeraLabs, tag "Matematik", title "Küçük Ramsey sayıları için yeni bir alt sınır denemesi".
Two-column layout.
Left (60%): a code editor card showing Lean 4 source code with syntax highlighting on a dark background (keywords "theorem", "lemma", "by", "intro", "simp", "omega", "exact"), line numbers in monospace, one lemma line highlighted mint with a check mark in the gutter, one line marked with a small orange "sorry" warning badge.
Right (40%): a "İspat ağacı" card showing a vertical tree of lemmas with status icons: mint check "Doğrulandı", amber dot "Ajan üzerinde çalışıyor", orange "Eksik adım"; above it a large status block "Lean doğrulaması: 7 / 9 lemma" with a progress bar; below it a small card from the "Eleştirmen" avatar: "Lemma 4'teki durum ayrımı eksik olabilir" with buttons "Göster" and "Yoksay".
Top bar identical to the lab screen with budget ring and "Canlı" pill.
```

## 5) Araştırma raporu → `05-arastirma-raporu.png`

```
Screen: "Araştırma raporu" — the final published report of a finished research project, a readable document layout (content column ~760px) with a right-side sticky outline.
Header: tags "AI / ML", "Tamamlandı", "Negatif sonuç"; title "Isınma küçük transformer modellerinde gerekli değil — ancak yüksek öğrenme oranlarında kararlılığı artırıyor"; meta line "8 ajan · 21 çalıştırma · $8.70 · 3 gün"; small row of 8 scientist avatars labeled "Yazarlar: QuaeraLabs ajan ekibi, insan danışman onaylı".
Body sections with clear headings: "Özet", "Bulgular" (with one clean line chart comparing warmup vs no-warmup and a compact results table in monospace), "Eleştirmen değerlendirmesi" (a card with a mint "Onaylandı" badge and two resolved objections), "Sınırlamalar", "Tekrar üret" (a code block "quaera replicate E-0031" and badges "Kod sabit", "Seed", "Ortam kilitli").
Right outline panel: section links, buttons "PDF indir", "Bu çalışmayı tekrar üret" (amber), and "Kanıt grafiğinde aç".
```
