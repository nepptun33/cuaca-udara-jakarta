"""
Tahap 3, langkah 2: latih dan evaluasi model "besok tidak sehat?" per stasiun.

Pembagian data berdasarkan waktu (bukan acak):
  latih: hari t s.d. 30 Apr 2025
  uji:   1 Mei 2025 s.d. 29 Nov 2025 (mencakup musim kemarau 2025)

Ambang probabilitas dipilih dari prediksi out-of-fold di data latih (bukan dari data uji),
dengan metrik F2: recall dibobot lebih tinggi daripada precision, karena bagi puskesmas
peringatan yang terlewat lebih mahal daripada alarm palsu.

Keluaran: reports/hasil_model.md, reports/kurva_pr.png, dan reports/hasil_model.json
(dibaca halaman "Model kualitas udara" di dashboard).
"""
from __future__ import annotations

from pathlib import Path

import json

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, fbeta_score, precision_recall_curve, precision_score, recall_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/processed/dataset.csv"
REPORT = ROOT / "reports/hasil_model.md"
REPORT_JSON = ROOT / "reports/hasil_model.json"
FIG = ROOT / "reports/kurva_pr.png"
BATAS_UJI = pd.Timestamp("2025-05-01")
SEED = 42
# Model utama: regresi logistik, dipilih dari prediksi out-of-fold data latih (PR-AUC 0.20 vs 0.12 untuk
# gradient boosting). Data latih hanya memuat satu musim kemarau, jadi model sederhana lebih stabil.
MODEL_UTAMA = "Logistik, dengan cuaca besok"
# Versi ringkas untuk simulator dan pembacaan bobot. Fitur lengkap memuat pasangan yang tumpang-tindih
# (hujan 3 hari s.d. hari ini vs s.d. besok, suhu maksimum hari ini vs besok) yang diberi bobot berlawanan
# arah, sehingga efek satu fitur tidak bisa dibaca atau digeser sendirian. Di data latih kedua versi setara
# (out-of-fold PR-AUC 0.193 vs 0.196); di data uji versi ringkas lebih rendah, dan itu dilaporkan apa adanya.
MODEL_SIMULATOR = "Logistik ringkas, dengan cuaca besok"

FITUR_HARI_INI = [
    "ispu_hari_ini", "ispu_kemarin", "ispu_2hari_lalu", "ispu_rata7", "pm25_hari_ini",
    "bulan_besok", "hari_besok", "musim_kemarau",
    "suhu_maks_hari_ini", "suhu_min_hari_ini", "hujan_mm_hari_ini", "angin_maks_kmh_hari_ini",
    "angin_u_hari_ini", "angin_v_hari_ini", "hujan_3hari_hari_ini",
]
FITUR_RINGKAS = [
    "ispu_hari_ini", "ispu_kemarin", "ispu_rata7", "pm25_hari_ini",
    "bulan_besok", "hari_besok", "musim_kemarau", "hujan_mm_hari_ini",
    "hujan_mm_besok", "suhu_maks_besok", "suhu_min_besok", "angin_maks_kmh_besok", "angin_u_besok", "angin_v_besok",
]
FITUR_CUACA_BESOK = [
    "suhu_maks_besok", "suhu_min_besok", "hujan_mm_besok", "angin_maks_kmh_besok",
    "angin_u_besok", "angin_v_besok", "hujan_3hari_besok",
]


def buat_model(jenis: str, numerik: list[str]):
    if jenis == "logistik":
        pra = ColumnTransformer([
            ("num", make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler()), numerik),
            ("st", OneHotEncoder(handle_unknown="ignore"), ["stasiun"]),
        ])
        return make_pipeline(pra, LogisticRegression(class_weight="balanced", max_iter=2000, C=0.5))
    pra = ColumnTransformer([
        ("num", "passthrough", numerik),   # HistGradientBoosting menangani nilai kosong sendiri
        ("st", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["stasiun"]),
    ])
    return make_pipeline(pra, HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=30,
        class_weight="balanced", random_state=SEED))


def pilih_ambang(model_fn, X: pd.DataFrame, y: pd.Series) -> float:
    """Ambang terbaik (F2) dari prediksi out-of-fold berurutan waktu di data latih."""
    oof = pd.Series(np.nan, index=X.index)
    for tr, va in TimeSeriesSplit(n_splits=4).split(X):
        if y.iloc[tr].nunique() < 2:
            continue
        m = model_fn().fit(X.iloc[tr], y.iloc[tr])
        oof.iloc[va] = m.predict_proba(X.iloc[va])[:, 1]
    ada = oof.notna()
    kandidat = np.linspace(0.05, 0.95, 91)
    skor = [fbeta_score(y[ada], oof[ada] >= t, beta=2, zero_division=0) for t in kandidat]
    return float(kandidat[int(np.argmax(skor))])


def fitur_simulator() -> list[str]:
    return FITUR_RINGKAS


def latih_model_simulator(d: pd.DataFrame):
    """Latih model simulator di periode latih saja, persis seperti saat evaluasi. Dipakai dashboard."""
    d = d[d["target"].notna()].sort_values(["tanggal", "stasiun"])
    latih = d[d["tanggal"] < BATAS_UJI]
    kolom = fitur_simulator() + ["stasiun"]
    return buat_model("logistik", fitur_simulator()).fit(latih[kolom], latih["target"].astype(int)), latih


def nilai(nama: str, y: pd.Series, pred: np.ndarray, prob: np.ndarray | None = None) -> dict:
    return {
        "Pendekatan": nama,
        "Recall": recall_score(y, pred, zero_division=0),
        "Precision": precision_score(y, pred, zero_division=0),
        "F2": fbeta_score(y, pred, beta=2, zero_division=0),
        "PR-AUC": average_precision_score(y, prob) if prob is not None else np.nan,
        "Peringatan": int(pred.sum()),
    }


def main() -> None:
    d = pd.read_csv(DATA, parse_dates=["tanggal"])
    d = d[d["target"].notna()].sort_values(["tanggal", "stasiun"]).reset_index(drop=True)
    latih, uji = d[d["tanggal"] < BATAS_UJI], d[d["tanggal"] >= BATAS_UJI]
    y_latih, y_uji = latih["target"].astype(int), uji["target"].astype(int)

    hasil, kurva, model_utama = [], {}, None
    # pembanding sederhana
    hasil.append(nilai("Persistence (besok = hari ini)", y_uji, (uji["ispu_hari_ini"] > 100).to_numpy()))
    hasil.append(nilai("Aturan musim (Mei-Agu = tidak sehat)", y_uji, (uji["musim_kemarau"] == 1).to_numpy()))

    skenario = [
        ("Logistik, tanpa cuaca besok", "logistik", FITUR_HARI_INI),
        ("Logistik, dengan cuaca besok", "logistik", FITUR_HARI_INI + FITUR_CUACA_BESOK),
        ("Logistik ringkas, dengan cuaca besok", "logistik", FITUR_RINGKAS),
        ("Gradient boosting, tanpa cuaca besok", "gbm", FITUR_HARI_INI),
        ("Gradient boosting, dengan cuaca besok", "gbm", FITUR_HARI_INI + FITUR_CUACA_BESOK),
    ]
    for nama, jenis, fitur in skenario:
        kolom = fitur + ["stasiun"]
        fn = lambda j=jenis, f=fitur: buat_model(j, f)
        ambang = pilih_ambang(fn, latih[kolom], y_latih)
        m = fn().fit(latih[kolom], y_latih)
        prob = m.predict_proba(uji[kolom])[:, 1]
        r = nilai(nama, y_uji, prob >= ambang, prob)
        r["Ambang"] = ambang
        hasil.append(r)
        kurva[nama] = prob
        if nama == MODEL_UTAMA:
            model_utama = (m, kolom, prob >= ambang, prob)
        if nama == MODEL_SIMULATOR:
            model_sim = (m, kolom)

    tabel = pd.DataFrame(hasil)
    print(tabel.round(3).to_string(index=False))

    # trade-off: berapa precision model pada recall tertentu, dibanding pembanding sederhana
    p_, r_, _ = precision_recall_curve(y_uji, kurva[MODEL_UTAMA])
    tradeoff = pd.DataFrame([{"Target recall": t, "Precision model": p_[np.where(r_ >= t)[0][-1]]}
                             for t in (0.5, 0.7, 0.86)])
    musim = (uji["musim_kemarau"] == 1).to_numpy()
    kemarau = pd.DataFrame([
        {"Periode": "Mei-Agu 2025", "Proporsi hari tidak sehat": y_uji[musim].mean(),
         "PR-AUC model": average_precision_score(y_uji[musim], kurva[MODEL_UTAMA][musim])},
        {"Periode": "Sep-Nov 2025", "Proporsi hari tidak sehat": y_uji[~musim].mean(),
         "PR-AUC model": average_precision_score(y_uji[~musim], kurva[MODEL_UTAMA][~musim])},
    ])
    print(tradeoff.round(3).to_string(index=False)); print(kemarau.round(3).to_string(index=False))

    # recall per stasiun untuk model utama
    m, kolom, pred, prob_utama = model_utama
    per_st = (uji.assign(pred=pred, y=y_uji.to_numpy())
                 .groupby("stasiun")
                 .apply(lambda g: pd.Series({
                     "Hari tidak sehat": int(g["y"].sum()),
                     "Tertangkap": int((g["y"] & g["pred"]).sum()),
                     "Alarm palsu": int((~g["y"].astype(bool) & g["pred"]).sum())}), include_groups=False))
    print(per_st)

    # importance dihitung pada versi ringkas: pada fitur lengkap, mengacak satu fitur dari pasangan yang
    # tumpang-tindih merusak keseimbangan bobot pasangannya, sehingga importance-nya terlihat berlebihan
    m_sim, kolom_sim = model_sim
    imp = permutation_importance(m_sim, uji[kolom_sim], y_uji, scoring="average_precision",
                                 n_repeats=10, random_state=SEED)
    penting = (pd.Series(imp.importances_mean, index=kolom_sim).sort_values(ascending=False).head(8))
    print(penting.round(4))

    # grafik precision-recall
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 5))
    for nama, prob in kurva.items():
        p, r, _ = precision_recall_curve(y_uji, prob)
        ax.plot(r, p, label=f"{nama} (PR-AUC {average_precision_score(y_uji, prob):.2f})")
    pers = tabel.iloc[0]
    ax.scatter([pers["Recall"]], [pers["Precision"]], color="black", zorder=5, label="Persistence")
    ax.axhline(y_uji.mean(), color="grey", ls="--", lw=1, label=f"Tebakan acak ({y_uji.mean():.0%})")
    ax.set_xlabel("Recall (hari tidak sehat yang tertangkap)")
    ax.set_ylabel("Precision (peringatan yang benar)")
    ax.set_title("Prediksi ISPU tidak sehat besok, data uji Mei-Nov 2025")
    ax.legend(fontsize=7, loc="upper right")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    fig.tight_layout(); FIG.parent.mkdir(exist_ok=True); fig.savefig(FIG, dpi=150)

    REPORT.write_text(
        "# Hasil model: ISPU tidak sehat besok\n\n"
        f"Data latih: {len(latih)} baris stasiun-hari (s.d. 30 Apr 2025), "
        f"{y_latih.mean():.1%} positif. Data uji: {len(uji)} baris (Mei-Nov 2025), {y_uji.mean():.1%} positif.\n\n"
        "## Perbandingan pendekatan (data uji)\n\n"
        + tabel.round(3).to_markdown(index=False) + "\n\n"
        f"## Trade-off model utama ({MODEL_UTAMA.lower()})\n\n"
        "Dibaca dari kurva precision-recall data uji. Sebagai pembanding: aturan musim mencapai recall 0.86 "
        "dengan precision 0.23, persistence recall 0.38 dengan precision 0.39.\n\n"
        + tradeoff.round(3).to_markdown(index=False) + "\n\n"
        "## Bisakah model memilah hari berisiko di dalam musim kemarau?\n\n"
        "PR-AUC yang jauh di atas proporsi hari tidak sehat berarti model tidak sekadar menebak musim, "
        "tapi bisa mengurutkan hari mana yang paling berisiko.\n\n"
        + kemarau.round(3).to_markdown(index=False) + "\n\n"
        "## Model utama per stasiun (ambang F2 dari data latih)\n\n"
        + per_st.to_markdown() + "\n\n"
        "## Fitur paling berpengaruh (permutation importance, PR-AUC, versi ringkas)\n\n"
        "Dihitung pada versi ringkas. Pada fitur lengkap, pasangan fitur yang tumpang-tindih membuat importance "
        "per fitur terlihat berlebihan.\n\n"
        + penting.round(4).rename("Penurunan PR-AUC").to_markdown() + "\n\n"
        "![Kurva precision-recall](kurva_pr.png)\n",
        encoding="utf-8")
    def kurva_ringkas(prob):
        p, r, _ = precision_recall_curve(y_uji, prob)
        idx = np.unique(np.linspace(0, len(p) - 1, 120).astype(int))
        return {"recall": r[idx].round(4).tolist(), "precision": p[idx].round(4).tolist()}

    ambang_utama = float(tabel.loc[tabel["Pendekatan"] == MODEL_UTAMA, "Ambang"].iloc[0])
    REPORT_JSON.write_text(json.dumps({
        "model_utama": MODEL_UTAMA,
        "ambang_utama": ambang_utama,
        "model_simulator": MODEL_SIMULATOR,
        "ambang_simulator": float(tabel.loc[tabel["Pendekatan"] == MODEL_SIMULATOR, "Ambang"].iloc[0]),
        "prauc_simulator": float(tabel.loc[tabel["Pendekatan"] == MODEL_SIMULATOR, "PR-AUC"].iloc[0]),
        "n_latih": int(len(latih)), "n_uji": int(len(uji)),
        "positif_latih": float(y_latih.mean()), "positif_uji": float(y_uji.mean()),
        "perbandingan": json.loads(tabel.round(3).to_json(orient="records")),
        "tradeoff": json.loads(tradeoff.round(3).to_json(orient="records")),
        "kemarau": json.loads(kemarau.round(3).to_json(orient="records")),
        "per_stasiun": json.loads(per_st.reset_index().to_json(orient="records")),
        "fitur_penting": {k: round(float(v), 4) for k, v in penting.items()},
        "kurva_pr": {nama: kurva_ringkas(prob) for nama, prob in kurva.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Laporan: {REPORT.relative_to(ROOT)} dan {REPORT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
