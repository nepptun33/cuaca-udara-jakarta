"""Test pembersihan data ISPU. Dilewati otomatis kalau pandas tidak terpasang
(GitHub Actions pipeline cuaca tidak memasang dependensi model)."""
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "model"))
import prepare  # noqa: E402


def raw(rows):
    kolom = ["periode_data", "bulan", "tanggal", "stasiun", "max", "kategori"] + prepare.KOMPONEN
    return pd.DataFrame([r + [np.nan] * len(prepare.KOMPONEN) for r in rows], columns=kolom[:6] + prepare.KOMPONEN)


def test_kategori_mengikuti_ambang_resmi():
    assert prepare.kategori_dari_nilai(50) == "BAIK"
    assert prepare.kategori_dari_nilai(67) == "SEDANG"      # di data sumber pernah dilabeli BAIK
    assert prepare.kategori_dari_nilai(103) == "TIDAK SEHAT"  # di data sumber pernah dilabeli SEDANG
    assert prepare.kategori_dari_nilai(250) == "SANGAT TIDAK SEHAT"
    assert prepare.kategori_dari_nilai(np.nan) is None


def test_nama_stasiun_diseragamkan_ke_kode():
    df = prepare.bersihkan_ispu(raw([
        [202401, 1, 1, "DKI1 BUNDARAN HOTEL INDONESIA (HI)", 60, "SEDANG"],
        [202401, 1, 2, "DKI1  BUNDERAN HI", 61, "SEDANG"],
    ]))
    assert set(df["stasiun"]) == {"DKI1"}


def test_tanggal_tidak_valid_dibuang():
    df = prepare.bersihkan_ispu(raw([
        [202509, 9, 30, "DKI5 KEBON JERUK", 70, "SEDANG"],
        [202509, 9, 31, "DKI5 KEBON JERUK", np.nan, np.nan],   # 31 September tidak ada
    ]))
    assert len(df) == 1


def test_nol_dan_tidak_ada_data_menjadi_kosong():
    df = prepare.bersihkan_ispu(raw([
        [202402, 2, 1, "DKI2 KELAPA GADING", 0, "TIDAK ADA DATA"],
        [202402, 2, 2, "DKI2 KELAPA GADING", 0, "BAIK"],
    ]))
    assert df["max"].isna().all()


def test_nilai_identik_berhari_hari_ditandai():
    rows = [[202412, 12, h, "DKI4 LUBANG BUAYA", 54, "SEDANG"] for h in range(1, 7)]   # 6 hari = 54
    rows.append([202412, 12, 7, "DKI4 LUBANG BUAYA", 80, "SEDANG"])
    df = prepare.bersihkan_ispu(raw(rows))
    assert df["mencurigakan"].sum() == 6
    assert df.loc[~df["mencurigakan"], "max"].tolist() == [80]
