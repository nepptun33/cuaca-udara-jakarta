-- Migrasi 002: perbaiki penghitungan ganda karena grid jam BMKG bergeser.
-- Jalankan sekali setelah 001_schema.sql: psql "$DATABASE_URL" -f sql/002_snapshot_terbaru.sql
--
-- Temuan (23 Okt 2026): dua request ke lokasi yang sama, dengan analysis_date yang sama,
-- bisa mengembalikan grid jam yang berbeda (08:00, 11:00, ... vs 09:00, 12:00, ... UTC).
-- Grid mengikuti jam saat request, bukan jadwal tetap. Kalau semua baris digabung,
-- satu hari bisa berisi 16 slot dan total hujan terhitung dua kali.
--
-- Perbaikan: catat kapan setiap baris terakhir terlihat di respons API. Untuk melihat
-- prakiraan terkini, pakai hanya baris dari fetch terakhir tiap lokasi (satu snapshot utuh).
-- Semua versi tetap tersimpan di cuaca.prakiraan untuk analisis historis.

ALTER TABLE cuaca.prakiraan ADD COLUMN IF NOT EXISTS terakhir_dilihat timestamptz;
UPDATE cuaca.prakiraan SET terakhir_dilihat = fetched_at WHERE terakhir_dilihat IS NULL;
ALTER TABLE cuaca.prakiraan ALTER COLUMN terakhir_dilihat SET NOT NULL;
-- Default sebagai pengaman: kalau kode versi lama sempat jalan setelah migrasi, insert tidak gagal
ALTER TABLE cuaca.prakiraan ALTER COLUMN terakhir_dilihat SET DEFAULT now();

COMMENT ON COLUMN cuaca.prakiraan.fetched_at IS 'Waktu baris ini pertama kali diterima dari API';
COMMENT ON COLUMN cuaca.prakiraan.terakhir_dilihat IS 'Waktu fetch terakhir yang memuat baris ini';

DROP VIEW IF EXISTS cuaca.ringkasan_harian;
DROP VIEW IF EXISTS cuaca.prakiraan_terbaru;

-- Snapshot terbaru: semua baris dari fetch terakhir untuk tiap lokasi
CREATE VIEW cuaca.prakiraan_terbaru AS
SELECT p.*
FROM cuaca.prakiraan p
JOIN (
  SELECT adm4, max(terakhir_dilihat) AS terakhir
  FROM cuaca.prakiraan
  GROUP BY adm4
) m ON m.adm4 = p.adm4 AND p.terakhir_dilihat = m.terakhir;

-- Ringkasan harian, sekarang dihitung dari satu snapshot saja.
-- Ambang hujan lebat mengikuti kategori BMKG (>= 50 mm/hari), dihitung dari prakiraan
-- per 3 jam, jadi sifatnya indikasi. Satu hari penuh = 8 slot; hari ini biasanya belum
-- lengkap karena snapshot dimulai dari jam saat request.
CREATE VIEW cuaca.ringkasan_harian AS
SELECT
  p.adm4,
  l.desa,
  l.kecamatan,
  l.kotkab,
  p.waktu_lokal::date               AS tanggal,
  max(p.suhu_c)                      AS suhu_maks_c,
  min(p.suhu_c)                      AS suhu_min_c,
  round(avg(p.kelembapan_pct), 1)    AS kelembapan_rata_pct,
  sum(p.curah_hujan_mm)              AS curah_hujan_total_mm,
  max(p.kecepatan_angin_kmh)         AS angin_maks_kmh,
  coalesce(sum(p.curah_hujan_mm), 0) >= 50 AS peringatan_hujan_lebat,
  coalesce(max(p.suhu_c), 0) >= 35         AS peringatan_panas,
  count(*)                           AS jumlah_slot,
  count(*) = 8                       AS data_lengkap
FROM cuaca.prakiraan_terbaru p
JOIN cuaca.lokasi l USING (adm4)
GROUP BY p.adm4, l.desa, l.kecamatan, l.kotkab, p.waktu_lokal::date;
