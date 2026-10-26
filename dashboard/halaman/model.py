"""
Halaman hasil Tahap 3: evaluasi model "besok ISPU tidak sehat?" dan simulator.

Hasil evaluasi dibaca dari reports/hasil_model.json (ditulis model/train.py).
Simulator melatih ulang model utama dengan kode dan data yang sama, supaya tidak
bergantung pada file pickle yang rawan rusak antar-versi scikit-learn.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
import prepare  # noqa: E402
import train  # noqa: E402

KOTA = {"DKI1": "Jakarta Pusat (Bundaran HI)", "DKI2": "Jakarta Utara (Kelapa Gading)",
        "DKI3": "Jakarta Selatan (Jagakarsa)", "DKI4": "Jakarta Timur (Lubang Buaya)",
        "DKI5": "Jakarta Barat (Kebon Jeruk)"}
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
         "September", "Oktober", "November", "Desember"]
NAMA_FITUR = {
    "hujan_3hari_besok": "Hujan 3 hari s.d. besok", "suhu_maks_besok": "Suhu maksimum besok",
    "ispu_hari_ini": "ISPU hari ini", "hujan_mm_hari_ini": "Hujan hari ini",
    "ispu_kemarin": "ISPU kemarin", "musim_kemarau": "Musim kemarau",
    "suhu_maks_hari_ini": "Suhu maksimum hari ini", "stasiun": "Stasiun",
    "hujan_mm_besok": "Hujan besok", "ispu_rata7": "Rata-rata ISPU 7 hari",
    "ispu_2hari_lalu": "ISPU 2 hari lalu", "pm25_hari_ini": "PM2.5 hari ini",
    "angin_u_hari_ini": "Arah angin hari ini", "angin_maks_kmh_hari_ini": "Angin maksimum hari ini",
    "angin_maks_kmh_besok": "Angin maksimum besok", "suhu_min_besok": "Suhu minimum besok",
    "bulan_besok": "Bulan", "hari_besok": "Hari dalam seminggu", "angin_u_besok": "Arah angin besok",
    "angin_v_besok": "Arah angin besok (utara-selatan)",
}


def angka(x: float, d: int = 2) -> str:
    """Desimal gaya Indonesia: 0,44."""
    return f"{x:.{d}f}".replace(".", ",")


@st.cache_data
def baca_hasil() -> dict:
    return json.loads((ROOT / "reports/hasil_model.json").read_text(encoding="utf-8"))


@st.cache_resource(show_spinner="Menyiapkan model...")
def siapkan_model():
    ispu = prepare.bersihkan_ispu(pd.read_html(prepare.RAW_ISPU)[0])
    data = prepare.bentuk_dataset(ispu, prepare.baca_cuaca(prepare.RAW_CUACA))
    return train.latih_model_simulator(data)


hasil = baca_hasil()
prauc_utama = next(r["PR-AUC"] for r in hasil["perbandingan"] if r["Pendekatan"] == hasil["model_utama"])

st.title("Model kualitas udara")
st.write(
    "Apakah ISPU di sebuah stasiun besok akan melewati 100 (kategori *tidak sehat*)? "
    "Model dilatih dari ISPU harian 5 stasiun DKI dan cuaca harian, Januari 2024 s.d. April 2025, "
    "lalu diuji pada Mei s.d. November 2025."
)
st.info(
    "Halaman ini menampilkan hasil analisis, bukan prakiraan hari ini. Data ISPU publik dari "
    "Satu Data Jakarta berhenti di November 2025, sehingga model belum bisa dijalankan harian.",
    icon="ℹ️",
)

tab_hasil, tab_sim = st.tabs(["Hasil evaluasi", "Simulator"])

# ---------------------------------------------------------------- hasil evaluasi
with tab_hasil:
    k1, k2, k3 = st.columns(3)
    k1.metric("Data latih (stasiun-hari)", f"{hasil['n_latih']:,}".replace(",", "."))
    k2.metric("Data uji (stasiun-hari, Mei–Nov 2025)", f"{hasil['n_uji']:,}".replace(",", "."))
    k3.metric("Hari tidak sehat di data uji", f"{hasil['positif_uji']:.0%}")

    st.subheader("Model dibanding pendekatan sederhana")
    banding = pd.DataFrame(hasil["perbandingan"])[["Pendekatan", "Recall", "Precision", "PR-AUC"]]
    tampil = banding.copy()
    for c in ["Recall", "Precision", "PR-AUC"]:
        tampil[c] = tampil[c].map(lambda v: "–" if pd.isna(v) else angka(v))
    tampil["Pendekatan"] = tampil["Pendekatan"].map(
        lambda n: f"{n} (model utama)" if n == hasil["model_utama"]
        else f"{n} (dipakai simulator)" if n == hasil["model_simulator"] else n)
    st.dataframe(tampil, hide_index=True, width="stretch")
    st.caption("Recall: berapa bagian hari tidak sehat yang tertangkap. Precision: berapa bagian peringatan "
               "yang benar. PR-AUC merangkum keduanya di semua ambang; tebakan acak bernilai sekitar "
               f"{angka(hasil['positif_uji'])}. Persistence dan aturan musim tidak menghasilkan peluang, "
               "jadi tidak punya PR-AUC.")

    garis = pd.concat([pd.DataFrame({"Model": n, "Recall": v["recall"], "Precision": v["precision"]})
                       for n, v in hasil["kurva_pr"].items()])
    titik = banding[banding["PR-AUC"].isna()][["Pendekatan", "Recall", "Precision"]]
    nama_model = list(hasil["kurva_pr"])
    warna = {n: "#c3c9d1" for n in nama_model}
    warna[hasil["model_simulator"]] = "#d08a2e"
    warna[hasil["model_utama"]] = "#12715b"
    kurva = alt.Chart(garis).mark_line().encode(
        x=alt.X("Recall:Q", scale=alt.Scale(domain=[0, 1]), title="Recall (hari tidak sehat yang tertangkap)"),
        y=alt.Y("Precision:Q", scale=alt.Scale(domain=[0, 1]), title="Precision (peringatan yang benar)"),
        color=alt.Color("Model:N", scale=alt.Scale(domain=nama_model, range=[warna[n] for n in nama_model]),
                        legend=alt.Legend(orient="bottom", columns=2, title=None, labelLimit=320)),
        strokeWidth=alt.condition(alt.datum.Model == hasil["model_utama"], alt.value(3.5), alt.value(1.6)),
        tooltip=["Model", alt.Tooltip("Recall:Q", format=".2f"), alt.Tooltip("Precision:Q", format=".2f")],
    )
    pembanding = alt.Chart(titik).mark_point(size=90, filled=True, color="black").encode(
        x="Recall:Q", y="Precision:Q",
        tooltip=["Pendekatan", alt.Tooltip("Recall:Q", format=".2f"), alt.Tooltip("Precision:Q", format=".2f")],
    )
    label = alt.Chart(titik.assign(Label=titik["Pendekatan"].str.split(" ").str[0]
                                   .replace({"Persistence": "Persistence", "Aturan": "Aturan musim"}))
                      ).mark_text(dy=-14, fontSize=12, color="black").encode(x="Recall:Q", y="Precision:Q", text="Label:N")
    st.altair_chart((kurva + pembanding + label).properties(height=380), width="stretch")
    st.caption("Garis hijau tebal adalah model utama, oranye versi ringkas untuk simulator, abu-abu model lain.")

    trade = {round(r["Target recall"], 2): r["Precision model"] for r in hasil["tradeoff"]}
    kem = {r["Periode"]: r for r in hasil["kemarau"]}["Mei-Agu 2025"]
    st.subheader("Yang dipelajari")
    st.markdown(f"""
- **Model sederhana menang.** Regresi logistik mengalahkan gradient boosting. Data latih hanya memuat satu
  musim kemarau, terlalu sedikit untuk model yang lebih rumit.
- **Nilai tambah model ada di dalam musim kemarau.** Aturan kalender "Mei–Agustus berisiko" sudah menangkap
  sebagian besar hari tidak sehat. Di dalam musim kemarau 2025, model mengurutkan hari berisiko dengan PR-AUC
  {angka(kem['PR-AUC model'])}, dibanding proporsi dasar {angka(kem['Proporsi hari tidak sehat'])}.
- **Lebih tepat daripada "besok sama dengan hari ini".** Pada recall 0,50 precision model {angka(trade[0.5])},
  dan pada recall 0,70 precision-nya {angka(trade[0.7])}. Persistence hanya mencapai recall 0,38 dengan
  precision 0,39.
- **Prakiraan cuaca membantu, tapi bukan faktor terbesar.** Menambahkan cuaca besok menaikkan PR-AUC dari 0,41
  ke 0,44. Faktor terbesar tetap ISPU beberapa hari terakhir, lalu stasiun, musim, dan suhu maksimum besok.
- **Fitur yang tumpang-tindih menyesatkan.** Fitur lengkap memuat pasangan yang hampir sama (misalnya hujan
  3 hari s.d. hari ini dan s.d. besok), yang diberi bobot berlawanan arah. Karena itu bobot dan importance
  dibaca dari versi ringkas, dan versi ringkas itu juga yang dipakai simulator.
""")

    penting = pd.DataFrame({"Fitur": [NAMA_FITUR.get(k, k) for k in hasil["fitur_penting"]],
                            "Penurunan PR-AUC": list(hasil["fitur_penting"].values())})
    st.altair_chart(alt.Chart(penting).mark_bar().encode(
        x=alt.X("Penurunan PR-AUC:Q", title="Penurunan PR-AUC kalau fitur diacak"),
        y=alt.Y("Fitur:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)),
        tooltip=["Fitur", alt.Tooltip("Penurunan PR-AUC:Q", format=".3f")],
    ).properties(height=300, title="Fitur paling berpengaruh (versi ringkas)"), width="stretch")

    with st.expander("Hasil per stasiun"):
        per = pd.DataFrame(hasil["per_stasiun"])
        per["stasiun"] = per["stasiun"].map(KOTA)
        st.dataframe(per.rename(columns={"stasiun": "Stasiun"}), hide_index=True, width="stretch")
        st.caption("Dengan ambang yang dipilih untuk mengutamakan recall, model menangkap hampir semua hari "
                   "tidak sehat, dengan harga alarm palsu yang banyak. Ambang bisa digeser sesuai kebutuhan.")
    with st.expander("Pembersihan data dan keterbatasan"):
        st.markdown("""
**Masalah di data sumber yang dibersihkan:** file `.xls` dari portal ternyata tabel HTML; 8 variasi nama untuk
5 stasiun; tanggal 31 September; data kosong ditulis sebagai `0` atau "TIDAK ADA DATA"; 21 baris kategori tidak
cocok dengan angkanya (kategori dihitung ulang dari ambang resmi); nilai identik 5 sampai 13 hari berturut-turut
di akhir 2024, kemungkinan sensor macet, dibuang dari pelatihan.

**Keterbatasan:** cuaca besok dalam evaluasi adalah pengukuran, dipakai sebagai pengganti prakiraan, jadi hasil
dengan prakiraan sungguhan kemungkinan sedikit lebih rendah. Cuaca diambil dari satu titik untuk seluruh Jakarta.
Data latih hanya memuat satu musim kemarau.
""")

# ---------------------------------------------------------------- simulator
with tab_sim:
    st.write(
        "Geser kondisinya dan lihat bagaimana model menilai risiko besok tidak sehat. Ini **simulasi**, "
        "bukan prakiraan untuk hari ini. Simulator memakai versi ringkas model (PR-AUC data uji "
        f"{angka(hasil['prauc_simulator'])}, sedikit di bawah model utama {angka(prauc_utama)}), karena hanya di versi ini "
        "setiap fitur bisa digeser sendirian tanpa bertabrakan dengan fitur kembarannya."
    )
    model, latih = siapkan_model()
    ambang = hasil["ambang_simulator"]

    a, b = st.columns(2)
    stasiun = a.selectbox("Stasiun", list(KOTA), format_func=KOTA.get)
    bulan = b.selectbox("Bulan", range(1, 13), index=6, format_func=lambda m: BULAN[m - 1])
    acuan = latih[(latih["stasiun"] == stasiun) & (latih["bulan_besok"] == bulan)]
    if acuan.empty:
        acuan = latih[latih["stasiun"] == stasiun]
    median = acuan[train.fitur_simulator()].median(numeric_only=True)

    c1, c2, c3, c4 = st.columns(4)
    ispu = c1.slider("ISPU hari ini", 10, 250, int(np.nan_to_num(median["ispu_hari_ini"], nan=60)))
    suhu_besok = c2.slider("Suhu maksimum besok (°C)", 26.0, 38.0,
                           float(round(np.nan_to_num(median["suhu_maks_besok"], nan=33.0), 1)), 0.1)
    hujan_ini = c3.slider("Hujan hari ini (mm)", 0.0, 60.0, 0.0, 0.5)
    hujan_besok = c4.slider("Hujan besok (mm)", 0.0, 60.0, 0.0, 0.5)

    def baris(**ubah) -> pd.DataFrame:
        r = median.copy()
        nilai = {"ispu": ispu, "suhu_besok": suhu_besok, "hujan_ini": hujan_ini, "hujan_besok": hujan_besok}
        nilai.update(ubah)
        # Penyederhanaan: anggap beberapa hari terakhir stabil di angka ISPU yang dipilih
        for k in ("ispu_hari_ini", "ispu_kemarin", "ispu_rata7", "pm25_hari_ini"):
            r[k] = nilai["ispu"]
        r["bulan_besok"] = bulan
        r["musim_kemarau"] = int(5 <= bulan <= 8)
        r["suhu_maks_besok"] = nilai["suhu_besok"]
        r["hujan_mm_hari_ini"] = nilai["hujan_ini"]
        r["hujan_mm_besok"] = nilai["hujan_besok"]
        out = pd.DataFrame([r])
        out["stasiun"] = stasiun
        return out[train.fitur_simulator() + ["stasiun"]]

    peluang = lambda **u: float(model.predict_proba(baris(**u))[:, 1][0])  # noqa: E731
    p = peluang()
    dasar = float(acuan["target"].mean()) if not acuan.empty else float("nan")

    m1, m2 = st.columns(2)
    m1.metric("Skor risiko model", f"{p * 100:.0f}", help="Skala 0–100. Bukan peluang sungguhan: lihat catatan di bawah.")
    m2.metric(f"Proporsi hari tidak sehat bulan {BULAN[bulan - 1]} di data latih, stasiun ini", f"{dasar:.0%}")
    if p >= ambang:
        st.warning(f"Di atas ambang peringatan ({ambang * 100:.0f}): puskesmas akan menerima peringatan.")
    else:
        st.success(f"Di bawah ambang peringatan ({ambang * 100:.0f}): tidak ada peringatan.")

    pilihan = {
        "ISPU hari ini": ("ispu", np.arange(10, 251, 5), ispu),
        "Suhu maksimum besok (°C)": ("suhu_besok", np.arange(26, 38.01, 0.25), suhu_besok),
        "Hujan besok (mm)": ("hujan_besok", np.arange(0, 60.5, 1.0), hujan_besok),
        "Hujan hari ini (mm)": ("hujan_ini", np.arange(0, 60.5, 1.0), hujan_ini),
    }
    nama_var = st.radio("Tampilkan pengaruh dari", list(pilihan), horizontal=True)
    kunci, rentang, sekarang_x = pilihan[nama_var]
    sens = pd.DataFrame({nama_var: rentang, "Skor": [100 * peluang(**{kunci: float(v)}) for v in rentang]})
    garis_s = alt.Chart(sens).mark_line().encode(
        x=alt.X(f"{nama_var}:Q", title=nama_var),
        y=alt.Y("Skor:Q", scale=alt.Scale(domain=[0, 100]), title="Skor risiko"),
        tooltip=[alt.Tooltip(f"{nama_var}:Q"), alt.Tooltip("Skor:Q", format=".0f")])
    titik = alt.Chart(pd.DataFrame({nama_var: [sekarang_x], "Skor": [100 * p]})).mark_point(
        size=120, filled=True, color="black").encode(x=f"{nama_var}:Q", y="Skor:Q")
    batas = alt.Chart(pd.DataFrame({"y": [100 * ambang]})).mark_rule(strokeDash=[4, 4], color="grey").encode(y="y:Q")
    st.altair_chart((garis_s + batas + titik).properties(
        height=260, title=f"Kalau hanya {nama_var if nama_var.startswith('ISPU') else nama_var[0].lower() + nama_var[1:]} yang berubah"), width="stretch")
    st.caption(
        "Skor risiko bukan peluang sungguhan: model dilatih dengan bobot lebih besar untuk hari tidak sehat yang "
        "jumlahnya sedikit, sehingga skornya cenderung tinggi. Yang bermakna adalah posisinya terhadap ambang "
        "peringatan (garis putus-putus) dan arah perubahannya. Fitur lain (angin, suhu minimum, hari dalam seminggu) diisi "
        "nilai tengah stasiun dan bulan yang dipilih dari data latih. ISPU beberapa hari terakhir dianggap sama dengan "
        "ISPU hari ini. Model dilatih ulang otomatis dari data mentah di repo, dengan kode yang sama seperti evaluasi."
    )
