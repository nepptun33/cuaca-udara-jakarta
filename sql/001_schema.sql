-- Tahap 1: skema penyimpanan prakiraan cuaca BMKG
-- Jalankan sekali: psql "$DATABASE_URL" -f sql/001_schema.sql

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS cuaca;

-- Respons mentah disimpan apa adanya, supaya bisa di-parse ulang kalau logika parsing berubah
CREATE TABLE IF NOT EXISTS raw.bmkg_response (
  id          bigserial PRIMARY KEY,
  adm4        text        NOT NULL,
  fetched_at  timestamptz NOT NULL,
  payload     jsonb       NOT NULL
);

CREATE TABLE IF NOT EXISTS cuaca.lokasi (
  adm4       text PRIMARY KEY,
  desa       text,
  kecamatan  text,
  kotkab     text,
  provinsi   text,
  lat        numeric,
  lon        numeric
);

-- Satu baris = prakiraan untuk satu lokasi, satu jam, dari satu kali analisis BMKG.
-- analysis_date ikut di primary key supaya prakiraan lama tidak tertimpa:
-- nanti bisa dibandingkan seberapa berubah prakiraan untuk jam yang sama.
CREATE TABLE IF NOT EXISTS cuaca.prakiraan (
  adm4                 text        NOT NULL REFERENCES cuaca.lokasi (adm4),
  waktu_utc            timestamptz NOT NULL,
  waktu_lokal          timestamp,
  analysis_date        timestamptz NOT NULL,
  suhu_c               numeric,
  kelembapan_pct       numeric,
  tutupan_awan_pct     numeric,
  curah_hujan_mm       numeric,
  kecepatan_angin_kmh  numeric,
  arah_angin           text,
  jarak_pandang_m      numeric,
  kode_cuaca           integer,
  deskripsi_cuaca      text,
  fetched_at           timestamptz NOT NULL,
  PRIMARY KEY (adm4, waktu_utc, analysis_date)
);

CREATE INDEX IF NOT EXISTS prakiraan_waktu_idx ON cuaca.prakiraan (waktu_utc);

-- Prakiraan paling baru untuk tiap lokasi dan jam
CREATE OR REPLACE VIEW cuaca.prakiraan_terbaru AS
SELECT DISTINCT ON (adm4, waktu_utc) *
FROM cuaca.prakiraan
ORDER BY adm4, waktu_utc, analysis_date DESC;

-- Ringkasan harian per lokasi, dasar untuk dashboard peringatan.
-- Dikelompokkan per tanggal WIB (waktu_lokal), bukan UTC.
-- Ambang hujan lebat mengikuti kategori BMKG (>= 50 mm/hari). Ini jumlah dari
-- prakiraan per 3 jam, jadi sifatnya indikasi, bukan hasil pengukuran.
CREATE OR REPLACE VIEW cuaca.ringkasan_harian AS
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
  -- BMKG memberi prakiraan per 3 jam = 8 slot per hari. Hari ini biasanya belum
  -- lengkap (prakiraan mulai dari jam sekarang), jadi total hujannya bisa terlalu kecil.
  count(*)                           AS jumlah_slot,
  count(*) = 8                       AS data_lengkap
FROM cuaca.prakiraan_terbaru p
JOIN cuaca.lokasi l USING (adm4)
GROUP BY p.adm4, l.desa, l.kecamatan, l.kotkab, p.waktu_lokal::date;
