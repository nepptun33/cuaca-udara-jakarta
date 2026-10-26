-- Migrasi 003: user database khusus baca untuk dashboard publik.
-- Dashboard bisa dibuka siapa saja, jadi koneksinya tidak boleh punya hak menulis atau menghapus.
--
-- Sebelum menjalankan: ganti GANTI_DENGAN_PASSWORD dengan password acak, misalnya dari
--   openssl rand -hex 16
-- di terminal. Setelah berhasil, hapus query ini dari riwayat SQL Editor.

CREATE ROLE dashboard_reader WITH LOGIN PASSWORD 'GANTI_DENGAN_PASSWORD';

GRANT CONNECT ON DATABASE cuaca TO dashboard_reader;
GRANT USAGE ON SCHEMA cuaca TO dashboard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA cuaca TO dashboard_reader;  -- termasuk view

-- Tabel atau view baru di schema cuaca otomatis ikut bisa dibaca
ALTER DEFAULT PRIVILEGES IN SCHEMA cuaca GRANT SELECT ON TABLES TO dashboard_reader;

-- Schema raw (JSON mentah) sengaja tidak dibuka untuk dashboard.
