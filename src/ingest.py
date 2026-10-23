"""
Ambil prakiraan cuaca dari API publik BMKG lalu simpan ke PostgreSQL.

Alur:
  1. Baca daftar kode wilayah (adm4) dari config/locations.csv
  2. Panggil API BMKG per lokasi (dengan jeda, batas 60 request/menit/IP)
  3. Simpan respons mentah (JSON) ke raw.bmkg_response untuk audit
  4. Ratakan (flatten) jadi baris per jam, lalu upsert ke cuaca.prakiraan

Aman dijalankan ulang (idempotent): primary key (adm4, waktu_utc, analysis_date)
mencegah duplikat kalau job jalan dua kali untuk data yang sama. Baris yang muncul lagi
hanya diperbarui kolom terakhir_dilihat-nya, supaya view prakiraan_terbaru bisa mengambil
satu snapshot utuh dari fetch terakhir (lihat sql/002_snapshot_terbaru.sql).

Pemakaian:
  DATABASE_URL=postgresql://... python src/ingest.py
"""
from __future__ import annotations

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://api.bmkg.go.id/publik/prakiraan-cuaca"
ROOT = Path(__file__).resolve().parent.parent
LOCATIONS_FILE = ROOT / "config" / "locations.csv"
DELAY_SECONDS = 1.1  # di bawah batas 60 request/menit

log = logging.getLogger("ingest")


def load_locations(path: Path = LOCATIONS_FILE) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return [row["adm4"].strip() for row in csv.DictReader(f) if row["adm4"].strip()]


def fetch(adm4: str, session: requests.Session, retries: int = 3) -> dict:
    """Ambil satu lokasi, dengan retry + backoff untuk error jaringan, 5xx, dan 429."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(API_URL, params={"adm4": adm4}, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"status {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise
            wait = 2 ** attempt
            log.warning("adm4=%s gagal (%s), coba lagi dalam %ss", adm4, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _to_dt(value: str | None, assume_utc: bool) -> datetime | None:
    """BMKG memakai '2024-12-10 03:00:00' dan '2024-12-10T00:00:00'. Terima keduanya."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace(" ", "T").replace("Z", "+00:00"))
    if assume_utc and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_location(payload: dict) -> dict | None:
    lok = payload.get("lokasi") or {}
    if not lok.get("adm4"):
        return None
    return {
        "adm4": lok["adm4"],
        "desa": lok.get("desa"),
        "kecamatan": lok.get("kecamatan"),
        "kotkab": lok.get("kotkab"),
        "provinsi": lok.get("provinsi"),
        "lat": _num(lok.get("lat")),
        "lon": _num(lok.get("lon")),
    }


def parse_forecast(payload: dict, adm4: str, fetched_at: datetime) -> list[dict]:
    """Ratakan struktur data -> cuaca (list per hari) -> item per jam."""
    rows = []
    for block in payload.get("data", []) or []:
        for day in block.get("cuaca", []) or []:
            for item in day or []:
                waktu_utc = _to_dt(item.get("utc_datetime"), assume_utc=True)
                analysis = _to_dt(item.get("analysis_date"), assume_utc=True)
                if waktu_utc is None or analysis is None:
                    continue  # baris tanpa waktu tidak bisa dipakai
                rows.append({
                    "adm4": adm4,
                    "waktu_utc": waktu_utc,
                    "waktu_lokal": _to_dt(item.get("local_datetime"), assume_utc=False),
                    "analysis_date": analysis,
                    "suhu_c": _num(item.get("t")),
                    "kelembapan_pct": _num(item.get("hu")),
                    "tutupan_awan_pct": _num(item.get("tcc")),
                    "curah_hujan_mm": _num(item.get("tp")),
                    "kecepatan_angin_kmh": _num(item.get("ws")),
                    "arah_angin": item.get("wd"),
                    "jarak_pandang_m": _num(item.get("vs")),
                    "kode_cuaca": int(item["weather"]) if item.get("weather") is not None else None,
                    "deskripsi_cuaca": item.get("weather_desc"),
                    "fetched_at": fetched_at,
                })
    return rows


UPSERT_LOKASI = """
INSERT INTO cuaca.lokasi (adm4, desa, kecamatan, kotkab, provinsi, lat, lon)
VALUES (%(adm4)s, %(desa)s, %(kecamatan)s, %(kotkab)s, %(provinsi)s, %(lat)s, %(lon)s)
ON CONFLICT (adm4) DO UPDATE SET
  desa = EXCLUDED.desa, kecamatan = EXCLUDED.kecamatan, kotkab = EXCLUDED.kotkab,
  provinsi = EXCLUDED.provinsi, lat = EXCLUDED.lat, lon = EXCLUDED.lon
"""

INSERT_PRAKIRAAN = """
INSERT INTO cuaca.prakiraan (
  adm4, waktu_utc, waktu_lokal, analysis_date, suhu_c, kelembapan_pct, tutupan_awan_pct,
  curah_hujan_mm, kecepatan_angin_kmh, arah_angin, jarak_pandang_m, kode_cuaca,
  deskripsi_cuaca, fetched_at, terakhir_dilihat)
VALUES (
  %(adm4)s, %(waktu_utc)s, %(waktu_lokal)s, %(analysis_date)s, %(suhu_c)s, %(kelembapan_pct)s,
  %(tutupan_awan_pct)s, %(curah_hujan_mm)s, %(kecepatan_angin_kmh)s, %(arah_angin)s,
  %(jarak_pandang_m)s, %(kode_cuaca)s, %(deskripsi_cuaca)s, %(fetched_at)s, %(fetched_at)s)
ON CONFLICT (adm4, waktu_utc, analysis_date)
DO UPDATE SET terakhir_dilihat = EXCLUDED.terakhir_dilihat
"""


def save(conn, adm4: str, payload: dict, fetched_at: datetime) -> int:
    """Satu transaksi per lokasi: raw, lokasi, dan prakiraan masuk bersama atau tidak sama sekali."""
    lokasi = parse_location(payload)
    rows = parse_forecast(payload, adm4, fetched_at)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.bmkg_response (adm4, fetched_at, payload) VALUES (%s, %s, %s)",
                (adm4, fetched_at, json.dumps(payload)),
            )
            if lokasi:
                cur.execute(UPSERT_LOKASI, lokasi)
            if rows:
                cur.executemany(INSERT_PRAKIRAAN, rows)
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL belum di-set")
        return 2

    import psycopg  # diimpor di sini supaya parsing dan test tidak butuh driver database

    locations = load_locations()
    fetched_at = datetime.now(timezone.utc)
    ok, failed = 0, []

    with requests.Session() as session, psycopg.connect(dsn) as conn:
        session.headers["User-Agent"] = "cuaca-udara-jakarta (proyek portofolio)"
        for i, adm4 in enumerate(locations):
            if i:
                time.sleep(DELAY_SECONDS)
            try:
                payload = fetch(adm4, session)
                n = save(conn, adm4, payload, fetched_at)
                log.info("adm4=%s: %d baris prakiraan", adm4, n)
                ok += 1
            except Exception as exc:  # satu lokasi gagal tidak boleh menghentikan lokasi lain
                log.error("adm4=%s gagal: %s", adm4, exc)
                failed.append(adm4)

    log.info("selesai: %d berhasil, %d gagal %s", ok, len(failed), failed or "")
    return 1 if ok == 0 else 0  # job dianggap gagal hanya kalau semua lokasi gagal


if __name__ == "__main__":
    sys.exit(main())
