"""Test parsing tanpa jaringan dan tanpa database.

Fixture-nya meniru struktur respons API BMKG. Setelah berhasil memanggil API
asli, ganti tests/fixtures/sample_response.json dengan respons sungguhan.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ingest  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_response.json").read_text())
FETCHED = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


def test_parse_location():
    lok = ingest.parse_location(FIXTURE)
    assert lok["adm4"] == "31.71.03.1001"
    assert lok["kecamatan"] == "Kemayoran"
    assert lok["lat"] == -6.1628


def test_flatten_and_skip_rows_without_time():
    rows = ingest.parse_forecast(FIXTURE, "31.71.03.1001", FETCHED)
    assert len(rows) == 3  # baris tanpa utc_datetime dibuang


def test_types_and_timezones():
    rows = ingest.parse_forecast(FIXTURE, "31.71.03.1001", FETCHED)
    first = rows[0]
    assert first["waktu_utc"] == datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)
    assert first["waktu_lokal"] == datetime(2026, 10, 23, 7, 0)  # WIB, tanpa zona
    assert first["analysis_date"].tzinfo is not None
    assert first["curah_hujan_mm"] == 0.0
    assert rows[1]["kode_cuaca"] == 63


def test_bad_numbers_become_null():
    rows = ingest.parse_forecast(FIXTURE, "31.71.03.1001", FETCHED)
    assert rows[2]["suhu_c"] is None
    assert rows[2]["curah_hujan_mm"] is None
    assert rows[2]["tutupan_awan_pct"] is None


def test_empty_payload():
    assert ingest.parse_forecast({}, "x", FETCHED) == []
    assert ingest.parse_location({}) is None


def test_load_locations(tmp_path):
    f = tmp_path / "loc.csv"
    f.write_text("adm4,catatan\n31.71.03.1001,a\n ,kosong\n")
    assert ingest.load_locations(f) == ["31.71.03.1001"]


# --- Respons asli BMKG (diambil 23 Okt 2026, Kemayoran) ---
REAL = json.loads((Path(__file__).parent / "fixtures" / "bmkg_kemayoran_asli.json").read_text())


def test_real_response_parses_all_rows():
    rows = ingest.parse_forecast(REAL, "31.71.03.1001", FETCHED)
    assert len(rows) == 19
    assert all(r["waktu_utc"].tzinfo is not None for r in rows)
    assert all(r["suhu_c"] is not None for r in rows)


def test_real_response_local_time_is_utc_plus_7():
    rows = ingest.parse_forecast(REAL, "31.71.03.1001", FETCHED)
    for r in rows:
        assert (r["waktu_lokal"] - r["waktu_utc"].replace(tzinfo=None)).total_seconds() == 7 * 3600


def test_real_response_visibility_can_be_null():
    rows = ingest.parse_forecast(REAL, "31.71.03.1001", FETCHED)
    assert {r["jarak_pandang_m"] for r in rows} == {None}


def test_real_response_location():
    lok = ingest.parse_location(REAL)
    assert lok["kotkab"] == "Kota Adm. Jakarta Pusat"
    assert lok["lat"] is not None and lok["lon"] is not None
