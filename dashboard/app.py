"""
Dashboard proyek cuaca dan kualitas udara Jakarta.

Jalankan lokal dari folder utama repo:
  streamlit run dashboard/app.py
"""
import streamlit as st

st.set_page_config(page_title="Cuaca dan udara Jakarta", page_icon="🌦️", layout="wide")

halaman = st.navigation([
    st.Page("halaman/peringatan.py", title="Peringatan cuaca", icon="🌦️", default=True),
    st.Page("halaman/model.py", title="Model kualitas udara", icon="🫁"),
])
halaman.run()
