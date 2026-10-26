"""
Tahap 3, langkah 1: bersihkan data ISPU dan cuaca, lalu bentuk dataset pelatihan.

Satu baris = satu stasiun pada satu hari (hari t). Target = apakah ISPU stasiun itu
pada hari berikutnya (t+1) > 100, alias kategori TIDAK SEHAT atau lebih buruk.

Masukan:
  data/raw/ispu_dki_2024-01_2025-11.xls          (Satu Data Jakarta; isinya tabel HTML)
  data/raw/open_meteo_jakarta_2024-01_2025-11.csv (Open-Meteo Historical Weather API)
Keluaran:
  data/processed/dataset.csv
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_ISPU = ROOT / "data/raw/ispu_dki_2024-01_2025-11.xls"
RAW_CUACA = ROOT / "data/raw/open_meteo_jakarta_2024-01_2025-11.csv"
OUT = ROOT / "data/processed/dataset.csv"

KOMPONEN = ["pm_sepuluh", "pm_duakomalima", "sulfur_dioksida",
            "karbon_monoksida", "ozon", "nitrogen_dioksida"]
AMBANG_TIDAK_SEHAT = 100   # ISPU 101-200 = TIDAK SEHAT (ambang resmi)
DERET_MENCURIGAKAN = 5     # nilai max identik >= 5 hari berturut-turut dianggap tidak valid


def kategori_dari_nilai(v: float) -> str | None:
    """Kategori ISPU dihitung ulang dari angka, karena sebagian label di data sumber tidak cocok."""
    if pd.isna(v):
        return None
    if v <= 50:
        return "BAIK"
    if v <= 100:
        return "SEDANG"
    if v <= 200:
        return "TIDAK SEHAT"
    if v <= 300:
        return "SANGAT TIDAK SEHAT"
    return "BERBAHAYA"


def bersihkan_ispu(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    # 8 variasi penulisan nama untuk 5 stasiun -> pakai kode DKI1..DKI5
    df["stasiun"] = df["stasiun"].astype(str).str.extract(r"^\s*(DKI\d)", expand=False)
    # tanggal yang tidak ada di kalender (contoh: 31 September) menjadi NaT lalu dibuang
    df["tanggal"] = pd.to_datetime(
        dict(year=df["periode_data"] // 100, month=df["bulan"], day=df["tanggal"]), errors="coerce")
    df = df[df["tanggal"].notna() & df["stasiun"].notna()].copy()

    for c in KOMPONEN + ["max"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # data kosong kadang ditulis 0, kadang 'TIDAK ADA DATA': keduanya berarti tidak ada pengukuran
    df.loc[df["max"] <= 0, "max"] = np.nan
    df.loc[df["kategori"].eq("TIDAK ADA DATA"), "max"] = np.nan

    # nilai max identik berhari-hari: kemungkinan sensor macet atau data diisi ulang
    df = df.sort_values(["stasiun", "tanggal"])
    blok = (df["max"] != df.groupby("stasiun")["max"].shift()).cumsum()
    panjang = df.groupby(["stasiun", blok])["max"].transform("size")
    df["mencurigakan"] = df["max"].notna() & (panjang >= DERET_MENCURIGAKAN)
    df.loc[df["mencurigakan"], "max"] = np.nan

    df["kategori_hitung"] = df["max"].map(kategori_dari_nilai)
    df = df.drop_duplicates(["stasiun", "tanggal"])
    return df[["stasiun", "tanggal", "max", "pm_duakomalima", "kategori_hitung", "mencurigakan"]]


def baca_cuaca(path: Path) -> pd.DataFrame:
    w = pd.read_csv(path, skiprows=3)   # 3 baris pertama berisi metadata lokasi
    w.columns = ["tanggal", "suhu_maks", "suhu_min", "hujan_mm", "angin_maks_kmh", "arah_angin_deg"]
    w["tanggal"] = pd.to_datetime(w["tanggal"])
    rad = np.deg2rad(w["arah_angin_deg"])
    w["angin_u"], w["angin_v"] = np.sin(rad), np.cos(rad)   # arah angin sebagai vektor, bukan derajat
    w["hujan_3hari"] = w["hujan_mm"].rolling(3, min_periods=1).sum()
    return w.drop(columns="arah_angin_deg")


def bentuk_dataset(ispu: pd.DataFrame, cuaca: pd.DataFrame) -> pd.DataFrame:
    hari = pd.date_range(ispu["tanggal"].min(), ispu["tanggal"].max(), freq="D")
    baris = []
    for st, g in ispu.groupby("stasiun"):
        g = g.set_index("tanggal").reindex(hari)
        f = pd.DataFrame(index=hari)
        f["stasiun"] = st
        f["ispu_hari_ini"] = g["max"]
        f["ispu_kemarin"] = g["max"].shift(1)
        f["ispu_2hari_lalu"] = g["max"].shift(2)
        f["ispu_rata7"] = g["max"].rolling(7, min_periods=4).mean()
        f["pm25_hari_ini"] = g["pm_duakomalima"]
        f["target_ispu_besok"] = g["max"].shift(-1)
        baris.append(f)
    d = pd.concat(baris).rename_axis("tanggal").reset_index()

    besok = d["tanggal"] + pd.Timedelta(days=1)
    d["bulan_besok"] = besok.dt.month
    d["hari_besok"] = besok.dt.day_of_week
    d["musim_kemarau"] = d["bulan_besok"].between(5, 8).astype(int)

    # cuaca hari ini dan cuaca besok. Cuaca besok di sini adalah pengukuran, dipakai sebagai
    # pengganti prakiraan BMKG: saat dipakai sungguhan, nilainya datang dari pipeline prakiraan.
    c = cuaca.set_index("tanggal")
    d = d.join(c.add_suffix("_hari_ini"), on="tanggal")
    d["tgl_besok"] = besok
    d = d.join(c.add_suffix("_besok"), on="tgl_besok").drop(columns="tgl_besok")

    d["target"] = np.where(d["target_ispu_besok"].isna(), np.nan,
                           (d["target_ispu_besok"] > AMBANG_TIDAK_SEHAT).astype(float))
    return d


def main() -> None:
    raw = pd.read_html(RAW_ISPU)[0]
    ispu = bersihkan_ispu(raw)
    cuaca = baca_cuaca(RAW_CUACA)
    data = bentuk_dataset(ispu, cuaca)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUT, index=False)
    ada = data["target"].notna()
    print(f"ISPU bersih: {len(ispu)} baris, {int(ispu['mencurigakan'].sum())} ditandai mencurigakan")
    print(f"Dataset: {len(data)} baris, {int(ada.sum())} punya target, "
          f"{data.loc[ada, 'target'].mean():.1%} hari besok tidak sehat")
    print(f"Tersimpan di {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
