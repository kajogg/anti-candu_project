# SadarLayar

Aplikasi terminal untuk membantu keluarga membangun kebiasaan layar yang sehat. Orang tua bisa melihat laporan dan menetapkan aturan. Anak bisa melihat datanya sendiri, mengatur batas pribadi, dan mendapat waktu bonus dari kegiatan di luar layar.

Prinsip utamanya adalah **transparansi**: aplikasi hanya mencatat *nama aplikasi aktif, kategori, dan waktu*. Tidak ada rekaman layar, kamera, mikrofon, judul jendela, pesan, atau lokasi. Anak dapat melihat semua yang dicatat lewat menu **Transparansi**.

## Fitur

- **Laporan harian**: total pemakaian vs batas, rincian per kategori, jam pertama dan terakhir pakai, blok tanpa jeda terpanjang, dan grafik per jam.
- **Laporan 7 hari**: tren pemakaian, aktivitas di jam tidur, dan rasio edukasi/produktif vs hiburan/game.
- **Wawasan otomatis**: peringatan batas hampir/terlampaui, anomali dibanding kebiasaan 14 hari, tren naik, pemakaian saat jam tidur atau sebelum jam 06:00, pemakaian panjang tanpa jeda, dan pelanggaran blok tenang.
- **Aktivitas offline dan bonus**: kegiatan seperti membaca buku atau olahraga menambah waktu layar (maksimal 30 menit bonus per hari).
- **Batas pilihan anak**: anak boleh menetapkan batas harian lebih ketat dari batas orang tua (minimal 15 menit).
- **Monitor langsung**: memantau aplikasi aktif secara berkala, dengan peringatan di 80% dan 100% batas serta pengingat istirahat (aturan 20-20-20).
- **Mode orang tua dilindungi PIN** dan **log audit** semua perubahan pengaturan.
- **Kategori aplikasi** berbasis aturan pola yang bisa ditambah sendiri.

## Kebutuhan

- Python **3.8 atau lebih baru**
- Tidak perlu menginstal library tambahan (hanya pustaka standar)
- Terminal dengan dukungan UTF-8 dan warna ANSI (hampir semua terminal modern)

## Cara menjalankan

```bash
python sadarlayar.py
```

Coba dulu dengan data contoh: pilih menu **3. Buat data demo**. Ini membuat dua anak (Aisyah dan Rafi) dengan riwayat 21 hari. PIN orang tua untuk data demo adalah `1234`.

> Menu demo menghapus data anak, sesi, blok tenang, aktivitas, bonus, dan log audit yang sudah ada. Jangan dijalankan di data yang sungguhan.

Jika belum ada anak, menu orang tua akan langsung meminta Anda menambahkan anak dan membuat PIN.

## Menu

**Mode orang tua** (perlu PIN):

| No | Menu | Fungsi |
|----|------|--------|
| 1 | Ringkasan keluarga | Ringkasan semua anak hari ini |
| 2 | Laporan harian | Detail satu hari (0 = hari ini, 1 = kemarin, dst.) |
| 3 | Laporan 7 hari | Tren seminggu terakhir |
| 4 | Wawasan | Peringatan dan saran otomatis |
| 5 | Aktivitas offline | Bonus hari ini dan riwayat aktivitas |
| 6 | Aturan | Batas, jam tidur, dan blok tenang |
| 7 | Catat aktivitas | Mencatat kegiatan offline anak |
| 8 | Ubah batas harian | Mengubah batas (juga mereset batas pilihan anak) |
| 9 | Ubah jam tidur | Format `HH:MM-HH:MM`, misalnya `21:00-05:30` |
| 10 | Tambah anak | Jam tidur bawaan 21:00-05:30 |
| 11 | Tambah aturan kategori | Pola nama aplikasi menjadi kategori |
| 12 | Mulai monitor | Pemantauan langsung |
| 13 | Ganti anak | Pindah ke anak lain |

**Mode anak** (tanpa PIN): Hari ini, 7 hari, Wawasan, Aktivitas offline, Catat aktivitas, Atur batas sendiri, dan Transparansi.

## Kategori dan aturan

Kategori yang tersedia: `edukasi`, `produktif`, `sosial`, `hiburan`, `game`, `lainnya`.

Aplikasi diklasifikasikan dengan pola (regex) terhadap nama aplikasi yang sudah dibuat huruf kecil dan tanpa spasi. Contoh: `Mobile Legends` menjadi `mobilelegends`. Aplikasi yang tidak cocok dengan aturan mana pun masuk ke `lainnya`.

Untuk menambahkan aturan, gunakan menu **Tambah aturan kategori**. Misalnya pola `scratch` dengan kategori `edukasi`.

## Tarif bonus aktivitas

Bonus = durasi aktivitas × tarif, dibatasi 30 menit per hari.

| Aktivitas | Tarif |
|-----------|-------|
| Membaca buku | ×0,5 |
| Olahraga | ×0,5 |
| Membantu pekerjaan rumah | ×0,5 |
| Belajar / PR | ×0,3 |
| Kegiatan kreatif | ×0,4 |

## Monitor langsung

Monitor memeriksa aplikasi aktif setiap 5 detik. Sesi yang berlangsung kurang dari 3 detik tidak disimpan. Berhenti dengan `Ctrl+C`, lalu data tersimpan.

Dukungan platform untuk mendeteksi aplikasi aktif:

| Sistem | Cara deteksi |
|--------|--------------|
| Windows | `tasklist` dan Windows API (bawaan) |
| macOS | `osascript` (bawaan). Mungkin perlu izin Accessibility untuk Terminal |
| Linux | Perlu `xdotool` dan sesi X11 (tidak untuk Wayland) |

Monitor hanya mencatat komputer tempat aplikasi dijalankan, bukan ponsel atau perangkat lain.

## Data dan pengaturan

- Data disimpan di SQLite: `~/.sadarlayar.db`
- Ganti lokasi dengan variabel lingkungan `SADARLAYAR_DB`:

  ```bash
  SADARLAYAR_DB=/path/ke/data.db python sadarlayar.py
  ```

- Matikan warna dengan `NO_COLOR=1`. Paksa warna menyala dengan `FORCE_COLOR=1` (berguna jika output di-pipe).
- PIN disimpan sebagai hash PBKDF2-SHA256 dengan salt, bukan teks biasa.

## Keterbatasan

- **Blok tenang** (misalnya jam sekolah atau maghrib) saat ini hanya terisi lewat data demo. Belum ada menu untuk menambah atau mengubahnya.
- Mode anak tidak dilindungi PIN, dan siapa pun yang punya akses ke file database dapat membacanya. Aplikasi ini ditujukan sebagai alat kesadaran bersama keluarga, bukan sistem pengawasan yang tidak bisa dilewati.
- Aplikasi tidak memblokir atau menutup aplikasi apa pun. Ia hanya mencatat, melaporkan, dan mengingatkan.

## Lisensi

Belum ditentukan. Tambahkan file `LICENSE` sesuai kebutuhan Anda.
