"""
Dashboard peringatan cuaca untuk puskesmas di Jakarta.

Membaca view cuaca.ringkasan_harian dan cuaca.prakiraan_terbaru (lihat sql/002).
Koneksi memakai user read-only dashboard_reader (lihat sql/003).

Jalankan lokal:
  streamlit run dashboard/app.py
dengan DATABASE_URL_READONLY di .streamlit/secrets.toml atau sebagai environment variable.
"""
from __future__ import annotations

import os

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

st.set_page_config(page_title="Peringatan cuaca Jakarta", page_icon="🌦️", layout="wide")

AMBANG_HUJAN_MM = 50   # kategori hujan lebat BMKG, per hari
AMBANG_PANAS_C = 35


def _dsn() -> str:
    try:
        return st.secrets["DATABASE_URL_READONLY"]
    except Exception:
        dsn = os.environ.get("DATABASE_URL_READONLY")
        if not dsn:
            st.error("Koneksi database belum diatur. Isi DATABASE_URL_READONLY di secrets.")
            st.stop()
        return dsn


@st.cache_data(ttl=600, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def tgl(d) -> str:
    """Tanggal pendek berbahasa Indonesia, misalnya 'Sab 24 Okt'."""
    return f"{HARI[d.weekday()]} {d.day} {BULAN[d.month - 1]}"


def nama_kota(kotkab: str) -> str:
    return kotkab.replace("Kota Adm. ", "") if kotkab else "?"


def status(row) -> str:
    if row["peringatan_hujan_lebat"] and row["peringatan_panas"]:
        return "Hujan lebat dan panas"
    if row["peringatan_hujan_lebat"]:
        return "Hujan lebat"
    if row["peringatan_panas"]:
        return "Panas ekstrem"
    return "Aman"


# ---------- data ----------
ringkasan = query("""
    SELECT adm4, desa, kecamatan, kotkab, tanggal, suhu_maks_c, suhu_min_c,
           curah_hujan_total_mm, angin_maks_kmh, peringatan_hujan_lebat,
           peringatan_panas, jumlah_slot, data_lengkap
    FROM cuaca.ringkasan_harian
    WHERE tanggal >= (now() AT TIME ZONE 'Asia/Jakarta')::date
    ORDER BY kotkab, tanggal
""")
segar = query("SELECT max(terakhir_dilihat) AT TIME ZONE 'Asia/Jakarta' AS t FROM cuaca.prakiraan")

# ---------- header ----------
st.title("Peringatan cuaca Jakarta")
st.write(
    "Prakiraan hujan lebat dan panas ekstrem 3 hari ke depan untuk satu kelurahan "
    "di tiap kota administrasi DKI, sebagai bahan persiapan layanan dan imbauan warga."
)
if not segar.empty and pd.notna(segar.iloc[0]["t"]):
    t = segar.iloc[0]["t"]
    st.caption(f"Data terakhir diambil dari BMKG: {tgl(t)} {t.year}, {t:%H.%M} WIB")

if ringkasan.empty:
    st.info("Belum ada prakiraan untuk hari ini dan seterusnya. Pipeline berjalan tiap 6 jam.")
    st.stop()

ringkasan["kota"] = ringkasan["kotkab"].map(nama_kota)
ringkasan["status"] = ringkasan.apply(status, axis=1)

# ---------- ringkasan peringatan ----------
st.subheader("Status 3 hari ke depan")
ada_peringatan = ringkasan[ringkasan["status"] != "Aman"]
if ada_peringatan.empty:
    st.success("Tidak ada peringatan hujan lebat atau panas ekstrem di kelima lokasi.")
else:
    for _, r in ada_peringatan.iterrows():
        st.warning(f"**{r['kota']}** ({r['desa']}), {tgl(r['tanggal'])}: {r['status']}")

tabel = ringkasan.pivot_table(index="kota", columns="tanggal", values="status", aggfunc="first")
tabel.columns = [tgl(c) for c in tabel.columns]
tabel.index.name = "Kota"
st.dataframe(tabel, width="stretch")

tidak_lengkap = ringkasan[~ringkasan["data_lengkap"]]
if not tidak_lengkap.empty:
    st.caption(
        "Hari ini baru sebagian jam yang tercakup prakiraan (dimulai dari jam saat data diambil), "
        "jadi total hujan hari ini bisa lebih kecil dari kenyataan."
    )

# ---------- detail per lokasi ----------
st.subheader("Detail per lokasi")
lokasi = ringkasan.drop_duplicates("adm4")[["adm4", "kota", "desa", "kecamatan"]]
label = {r.adm4: f"{r.kota}, Kel. {r.desa}" for r in lokasi.itertuples()}
opsi = list(label)
# Buka lokasi pertama yang punya peringatan, supaya yang penting langsung terlihat
awal = ada_peringatan["adm4"].iloc[0] if not ada_peringatan.empty else opsi[0]
pilih = st.selectbox("Lokasi", options=opsi, index=opsi.index(awal), format_func=label.get)

per_jam = query("""
    SELECT waktu_lokal, suhu_c, curah_hujan_mm, kelembapan_pct, deskripsi_cuaca
    FROM cuaca.prakiraan_terbaru
    WHERE adm4 = %s
    ORDER BY waktu_lokal
""", (pilih,))

hari = ringkasan[ringkasan["adm4"] == pilih]
kolom = st.columns(len(hari))
for k, (_, r) in zip(kolom, hari.iterrows()):
    k.metric(tgl(r["tanggal"]), f"{r['suhu_maks_c']:.0f} °C")
    k.caption(f"Suhu maksimum. Hujan {r['curah_hujan_total_mm']:.1f} mm, {r['status'].lower()}.")

if not per_jam.empty:
    per_jam["curah_hujan_mm"] = per_jam["curah_hujan_mm"].astype(float)
    per_jam["suhu_c"] = per_jam["suhu_c"].astype(float)
    dasar = alt.Chart(per_jam).encode(
        x=alt.X("waktu_lokal:T", title="Waktu (WIB)",
                axis=alt.Axis(format="%d/%m %H.%M", labelAngle=0, labelOverlap=True)))
    suhu = dasar.mark_line(point=True).encode(
        y=alt.Y("suhu_c:Q", title="Suhu (°C)", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("waktu_lokal:T", title="Waktu", format="%d/%m %H.%M"),
                 alt.Tooltip("suhu_c:Q", title="Suhu °C"),
                 alt.Tooltip("deskripsi_cuaca:N", title="Cuaca")],
    ).properties(height=220)
    hujan = dasar.mark_bar(size=10).encode(
        y=alt.Y("curah_hujan_mm:Q", title="Curah hujan (mm per 3 jam)"),
        tooltip=[alt.Tooltip("waktu_lokal:T", title="Waktu", format="%d/%m %H.%M"),
                 alt.Tooltip("curah_hujan_mm:Q", title="Hujan mm")],
    ).properties(height=160)
    st.altair_chart(alt.vconcat(suhu, hujan).resolve_scale(x="shared"), width="stretch")

# ---------- catatan ----------
st.divider()
st.caption(
    f"Peringatan hujan lebat: total prakiraan ≥ {AMBANG_HUJAN_MM} mm per hari (kategori BMKG). "
    f"Panas ekstrem: suhu maksimum ≥ {AMBANG_PANAS_C} °C. Angka dihitung dari prakiraan per 3 jam, "
    "jadi sifatnya indikasi, bukan hasil pengukuran."
)
st.caption("Sumber data: BMKG (Badan Meteorologi, Klimatologi, dan Geofisika). "
           "Proyek pribadi, kode di github.com/nepptun33/cuaca-udara-jakarta.")
