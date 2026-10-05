# QuaeraLabs tasarım sistemi (v3, 2026-10-05)

Kaynaklar:
- ui-ux-pro-max önerisi: "Swiss Modernism 2.0" (ızgara, rasyonel boşluklar, sade).
- Araştırmacı oylaması sonrası verilen referans: dar sol panel, sıcak nötr tonlar, ortada büyük giriş kutusu, serif karşılama başlığı.

Referansın düzeni ve renk tonu uyarlandı; marka, simge ve yazı tipleri QuaeraLabs'e aittir. Önceki sürüm (v2: iOS gri + yeşil vurgu) bu belgeyle değiştirildi.

## Kararlar
- **Dil:** arayüz İngilizce. Model çıktılarının dili `QUAERA_LANGUAGE` ile seçilir (varsayılan English).
- **Tema:** açık varsayılan. Koyu tema sistem tercihine göre ya da kenar çubuğundaki simgeli seçiciyle (System / Light / Dark) gelir. Seçim yalnızca tarayıcıda saklanır.
- **Renk (tokenlar `web/src/styles.css`):**
  - Açık tema: sıcak kâğıt arka plan #FAF9F5, sol panel #F3F1EA, kartlar beyaz, metin #1F1E1B.
  - Koyu tema: #1F1E1C / #1A1917 / #262523, metin #ECEAE4.
  - Vurgu kil turuncusu: açıkta #A9502F, koyuda #E2896A.
  - Anlamsal renkler: başarılı/desteklendi = yeşil, çürütüldü/hata = kırmızı, uyarı/itiraz = koyu sarı, bilgi/yöntem = mavi, açık soru = mor.
  - Her metin/zemin çifti ölçüldü: en düşük 4.79:1 (WCAG AA).
- **Yazı:** başlıklar ve karşılama Source Serif 4; gövde Inter; kod ve Lean Geist Mono; matematik KaTeX.
- **Yerleşim:**
  - Sol panel (276 px): marka + daraltma, arama, New research, gezinme, "What the lab learned" (bağlamsal hafıza), tarihe göre gruplanmış tek satırlık kısa proje adları, alt satırda yerel laboratuvar + tema.
  - Mobilde panel çekmece olarak açılır (Esc ve arka plana dokunma kapatır).
  - Ana sayfa: ortalanmış serif karşılama + büyük giriş kutusu. Alan, bütçe ve otonomi kutunun altında; örnek sorular çip olarak.
  - Proje sayfası:
    - üstte kısa ad (yeniden adlandırılabilir), tam soru (dizgili) ve aşama çubuğu;
    - ana sütunda karar kartı, kanıt özeti, deney/ispat ve etkinlik;
    - sağ sütunda Proje yöneticisi, bütçe, aktif ajan, ekip ve sınırlar;
    - altta yapışkan giriş kutusu: varsayılan alıcı yönetici, istenirse ekibe not.
- **Köşe yarıçapı:** kart 14 px, iç öğe 10 px, düğme 9 px, giriş kutuları 18 px, rozet/çip tam yuvarlak. Cam/bulanıklık yok.
- **Hareket:** az; 150–200 ms; `prefers-reduced-motion` saygı görür.
- **Erişilebilirlik:** AA kontrast her iki temada, görünür odak halkası, tüm etkileşimler klavyeyle, çekmece/diyalog odak tuzağı ve Esc, kaydırılabilir bölgeler odaklanabilir. axe denetimi uçtan uca testte açık ve koyu temada koşar.
