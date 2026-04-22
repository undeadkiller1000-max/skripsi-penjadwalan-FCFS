
# -*- coding: utf-8 -*-
"""
DSS Penjadwalan Produksi Job Shop — Garmen | FCFS Only
Mode simulasi penjadwalan murni FCFS (tanpa SA, tanpa MILP)

Pembaruan:
  - Hanya menggunakan FCFS sebagai engine penjadwalan
  - Seluruh komponen MILP/optimizer/pembanding dihapus
  - Tambah opsi urutan FCFS:
      * Sesuai urutan dataset (default)
      * Due Date Terdekat
      * Due Date Terjauh
  - Tambah opsi pilih semua order sesuai hasil filter aktif
"""

import streamlit as st
import pandas as pd
import math
import random
import io
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go

# ============================================================
# 1. KONFIGURASI HALAMAN
# ============================================================
st.set_page_config(page_title="DSS Penjadwalan Job Shop — FCFS", layout="wide", page_icon="🏭")

st.markdown("""
<style>
.main-header{font-size:2.2rem;font-weight:700;color:#1E3A8A;margin-bottom:0}
.sub-header{font-size:1.05rem;color:#64748B;margin-bottom:16px}
.section-title{font-size:1.1rem;font-weight:600;color:#1E3A8A;margin:8px 0}
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">🏭 DSS: Penjadwalan Produksi FCFS</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Sistem Penjadwalan Produksi — Routing Dinamis (OPC) · FCFS Simulation · Analisis Sensitivitas</p>', unsafe_allow_html=True)
st.divider()

# ============================================================
# 2. KONSTANTA GLOBAL
# ============================================================
STATIONS = [
    '1. Potong',
    '2. Jahit_KaosPolo',
    '3. Jahit_KemejaJaket',
    '4. Sablon',
    '5. DTF',
    '6. Bordir',
    '7. Pasang_Kancing',
    '8. Buang_Benang',
    '9. Lipat',
    '10. Packing',
]

REQUIRED_COLUMNS = [
    'id pesanan', 'jenis produk', 'qty', 'due date (tanggal)',
    'furing', 'sablon', 'dtf', 'bordir', 'pasang kancing',
]
BINARY_COLUMNS = ['furing', 'sablon', 'dtf', 'bordir', 'pasang kancing']

MENIT_PER_HARI = 450
MENIT_ISTIRAHAT = 90

# ============================================================
# 3. LOAD & VALIDASI DATA
# ============================================================
def load_order_file(uploaded_file):
    fn = uploaded_file.name.lower()
    if fn.endswith('.csv'):
        df = pd.read_csv(uploaded_file)
    elif fn.endswith(('.xlsx', '.xls')):
        df = pd.read_excel(uploaded_file)
    else:
        raise ValueError("Format tidak didukung. Gunakan CSV atau Excel (.xlsx/.xls).")

    df.columns = df.columns.str.lower().str.strip()

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Kolom wajib tidak ditemukan: {missing}")

    df = df[REQUIRED_COLUMNS].copy()
    df['id pesanan'] = df['id pesanan'].astype(str).str.strip()
    df['jenis produk'] = df['jenis produk'].astype(str).str.strip().str.lower()
    df['_dataset_order'] = range(len(df))

    mapping = {'kaos': 'kaos', 'polo': 'polo', 'kemeja': 'kemeja', 'jaket': 'jaket'}
    df['jenis produk'] = df['jenis produk'].replace(mapping)
    unknown = sorted(set(df['jenis produk']) - set(mapping))
    if unknown:
        raise ValueError(f"Jenis produk tidak dikenali: {unknown}")

    df['qty'] = pd.to_numeric(df['qty'], errors='coerce')
    for col in BINARY_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df['due date (tanggal)'] = pd.to_datetime(
        df['due date (tanggal)'], errors='coerce', dayfirst=True
    )

    null_mask = df[['qty', 'due date (tanggal)'] + BINARY_COLUMNS].isnull().any(axis=1)
    if null_mask.any():
        raise ValueError(
            f"Data kosong/invalid di baris: {df.index[null_mask].tolist()}. "
            "Cek kolom qty, due date, dan flag 0/1."
        )
    if (df['qty'] <= 0).any():
        raise ValueError(f"qty <= 0 pada order: {df.loc[df['qty']<=0,'id pesanan'].tolist()}")
    for col in BINARY_COLUMNS:
        inv = df.loc[~df[col].isin([0,1]), col].unique().tolist()
        if inv:
            raise ValueError(f"Kolom '{col}' hanya boleh 0 atau 1. Nilai: {inv}")

    return df

# ============================================================
# 4. WAKTU PROSES (OPC / ROUTING DINAMIS)
# ============================================================
def hitung_waktu_proses(row, resources, setup_time):
    qty = row['qty']
    jenis = str(row['jenis produk']).lower()
    furing = row['furing']
    P = {m: 0.0 for m in STATIONS}

    cap_potong = 1000
    if jenis in ('kemeja', 'jaket'):
        cap_potong = 125 if furing == 1 else 250
    P['1. Potong'] = (qty / (cap_potong * resources['1. Potong'])) * MENIT_PER_HARI

    if jenis in ('kaos', 'polo'):
        cap_j = 112.5 if jenis == 'kaos' else 55
        P['2. Jahit_KaosPolo'] = (qty / (cap_j * resources['2. Jahit_KaosPolo'])) * MENIT_PER_HARI
    elif jenis in ('kemeja', 'jaket'):
        base = 13.5 if jenis == 'kemeja' else 11.0
        if furing == 1:
            base *= 2/3
        P['3. Jahit_KemejaJaket'] = (qty / (base * resources['3. Jahit_KemejaJaket'])) * MENIT_PER_HARI

    if row['sablon'] == 1:
        P['4. Sablon'] = (qty / (700 * resources['4. Sablon'])) * MENIT_PER_HARI
    if row['dtf'] == 1:
        P['5. DTF'] = (qty / (750 * resources['5. DTF'])) * MENIT_PER_HARI
    if row['bordir'] == 1:
        P['6. Bordir'] = (qty / (442.5 * resources['6. Bordir'])) * MENIT_PER_HARI

    if row['pasang kancing'] == 1 and jenis != 'kaos':
        cap_k = 400 if jenis == 'polo' else 125
        P['7. Pasang_Kancing'] = (qty / (cap_k * resources['7. Pasang_Kancing'])) * MENIT_PER_HARI

    cap_benang = 166.67 if furing == 1 else 500
    P['8. Buang_Benang'] = (qty / (cap_benang * resources['8. Buang_Benang'])) * MENIT_PER_HARI
    P['9. Lipat'] = (qty / (500 * resources['9. Lipat'])) * MENIT_PER_HARI
    P['10. Packing'] = (qty / (500 * resources['10. Packing'])) * MENIT_PER_HARI

    for m in STATIONS:
        if P[m] > 0:
            P[m] += setup_time

    return P

# ============================================================
# 5. KONVERSI WAKTU: MENIT EFEKTIF ↔ JAM DINDING
# ============================================================
def konversi_ke_jam_dinding(menit_efektif, start_date):
    hari_ke = int(menit_efektif // MENIT_PER_HARI)
    sisa = menit_efektif % MENIT_PER_HARI

    current = start_date
    hari_ditambah = 0
    while hari_ditambah < hari_ke:
        current += timedelta(days=1)
        if current.weekday() != 6:
            hari_ditambah += 1

    if current.weekday() == 6:
        current += timedelta(days=1)

    base = current.replace(hour=8, minute=30, second=0, microsecond=0)
    if sisa <= 180:
        return base + timedelta(minutes=sisa)
    else:
        return base + timedelta(minutes=sisa + MENIT_ISTIRAHAT)

def hitung_target_menit(target_dt, start_dt):
    if target_dt <= start_dt:
        return 0

    total = 0
    current = start_dt

    while current.date() < target_dt.date():
        if current.weekday() != 6:
            total += MENIT_PER_HARI
        current += timedelta(days=1)

    if target_dt.weekday() != 6:
        jam_mulai_hari = current.replace(hour=8, minute=30, second=0, microsecond=0)
        delta_kal = (target_dt - jam_mulai_hari).total_seconds() / 60
        if delta_kal <= 0:
            mnt_hari_ini = 0
        elif delta_kal <= 180:
            mnt_hari_ini = delta_kal
        elif delta_kal <= 180 + MENIT_ISTIRAHAT:
            mnt_hari_ini = 180
        else:
            mnt_hari_ini = delta_kal - MENIT_ISTIRAHAT
        total += mnt_hari_ini

    return total

def pecah_balok_gantt(start_efektif, durasi, start_date):
    blocks = []
    tersisa = durasi
    cur_efektif = start_efektif

    while tersisa > 0.01:
        mnt_di_hari = cur_efektif % MENIT_PER_HARI
        if mnt_di_hari < 180:
            chunk = min(tersisa, 180 - mnt_di_hari)
        else:
            chunk = min(tersisa, MENIT_PER_HARI - mnt_di_hari)

        if chunk < 0.01:
            cur_efektif += 0.01
            continue

        blocks.append({
            'start_nyata': konversi_ke_jam_dinding(cur_efektif, start_date),
            'end_nyata': konversi_ke_jam_dinding(cur_efektif + chunk, start_date),
            'durasi_potongan': chunk,
        })
        cur_efektif += chunk
        tersisa -= chunk

    return blocks

# ============================================================
# 6. FCFS ENGINE
# ============================================================
def run_fcfs(job_sequence, P_dict, D_dict, W_dict):
    """
    FCFS untuk Weighted Total Tardiness.
    Urutan job ditentukan dari sequence yang diberikan.
    """
    m_avail = {m: 0.0 for m in STATIONS}
    j_avail = {j: 0.0 for j in job_sequence}
    sched = []
    total_tard = 0.0

    for j in job_sequence:
        rute = [m for m in STATIONS if P_dict[j][m] > 0]
        for m in rute:
            dur = P_dict[j][m]
            start = max(m_avail[m], j_avail[j])
            end = start + dur
            m_avail[m] = end
            j_avail[j] = end
            sched.append({'job': j, 'm': m, 'start': start, 'dur': dur})
        total_tard += max(0, j_avail[j] - D_dict[j]) * W_dict[j]

    return total_tard, sched, j_avail

# ============================================================
# 7. HELPER: BANGUN JADWAL GANTT DARI SCHED LIST
# ============================================================
def build_gantt_df(sched_list, df_pool, start_date):
    rows = []
    for t in sched_list:
        qty_val = df_pool[df_pool['id pesanan'].astype(str) == t['job']]['qty'].iloc[0]
        for blk in pecah_balok_gantt(t['start'], t['dur'], start_date):
            rows.append({
                'Stasiun Kerja': t['m'],
                'ID Pesanan': t['job'],
                'Qty': qty_val,
                'Mulai': blk['start_nyata'],
                'Selesai': blk['end_nyata'],
                'Durasi (Menit)': round(blk['durasi_potongan'], 2),
            })
    return pd.DataFrame(rows)

# ============================================================
# 8. SANITY CHECK
# ============================================================
def jalankan_sanity_check(jadwal_final, df_pool, P_dict, start_date):
    log = []
    log.append("=" * 60)
    log.append("🔍 SANITY CHECK — VERIFIKASI LOGIKA JADWAL")
    log.append("=" * 60)

    err_overlap = False
    err_presedens = False

    log.append("\n[1/3] Memeriksa Overlap Kapasitas Mesin...")
    for st_name in STATIONS:
        tasks = sorted([t for t in jadwal_final if t['m'] == st_name], key=lambda x: x['start'])
        for i in range(1, len(tasks)):
            prev, curr = tasks[i-1], tasks[i]
            gap = curr['start'] - (prev['start'] + prev['dur'])
            if gap < -0.01:
                log.append(f"  ❌ OVERLAP di {st_name}: Order {prev['job']} & {curr['job']} bentrok! (gap={gap:.2f} mnt)")
                err_overlap = True
    if not err_overlap:
        log.append("  ✔️ LULUS: Tidak ada tumpang tindih antar order di setiap stasiun.")

    log.append("\n[2/3] Memeriksa Presedensi (Urutan Stasiun per Pesanan)...")
    for job in set(t['job'] for t in jadwal_final):
        tasks_j = sorted([t for t in jadwal_final if t['job'] == job], key=lambda x: x['start'])
        for i in range(1, len(tasks_j)):
            prev, curr = tasks_j[i-1], tasks_j[i]
            gap = curr['start'] - (prev['start'] + prev['dur'])
            if gap < -0.01:
                log.append(f"  ❌ ERROR Order {job}: {curr['m']} mulai sebelum {prev['m']} selesai!")
                err_presedens = True
    if not err_presedens:
        log.append("  ✔️ LULUS: Semua urutan stasiun per pesanan sudah benar.")

    log.append("\n[3/3] Memeriksa Jadwal di Hari Minggu...")
    minggu_rows = []
    for t in jadwal_final:
        blk_list = pecah_balok_gantt(t['start'], t['dur'], start_date)
        for blk in blk_list:
            if blk['start_nyata'].weekday() == 6:
                minggu_rows.append({
                    'ID Pesanan': t['job'],
                    'Stasiun': t['m'],
                    'Mulai': blk['start_nyata'].strftime('%d-%b-%y %H:%M'),
                    'Selesai': blk['end_nyata'].strftime('%d-%b-%y %H:%M'),
                })
    if minggu_rows:
        log.append(f"  ❌ DITEMUKAN {len(minggu_rows)} tugas dijadwalkan di Hari Minggu!")
    else:
        log.append("  ✔️ LULUS: Tidak ada jadwal aktif di Hari Minggu.")

    log.append("\n" + "=" * 60)
    if err_overlap or err_presedens or minggu_rows:
        log.append("🚨 SANITY CHECK GAGAL! Ada pelanggaran yang perlu diperiksa.")
    else:
        log.append("✅ SANITY CHECK PASSED! Jadwal valid — tidak ada pelanggaran.")
    log.append("=" * 60)

    all_jobs = list(set(t['job'] for t in jadwal_final))
    sample_job_id = random.choice(all_jobs) if all_jobs else None
    sample_row_df = df_pool[df_pool['id pesanan'].astype(str) == sample_job_id] if sample_job_id else pd.DataFrame()
    sample_row = sample_row_df.iloc[0].to_dict() if not sample_row_df.empty else {}
    sample_sched = sorted([t for t in jadwal_final if t['job'] == sample_job_id], key=lambda x: x['start']) if sample_job_id else []
    sample_rute = [t['m'] for t in sample_sched]

    return {
        'log_text': "\n".join(log),
        'err_overlap': err_overlap,
        'err_presedens': err_presedens,
        'sample_job_id': sample_job_id,
        'sample_row': sample_row,
        'sample_sched': sample_sched,
        'sample_rute': sample_rute,
        'tabel_minggu': pd.DataFrame(minggu_rows) if minggu_rows else pd.DataFrame(),
    }

# ============================================================
# 9. SIDEBAR
# ============================================================
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2043/2043236.png", width=60)
    st.header("⚙️ Konfigurasi Sistem")

    with st.expander("📥 Template Data"):
        tpl = pd.DataFrame({
            'id pesanan': ['ORD-01', 'ORD-02'],
            'jenis produk': ['Kaos', 'Kemeja'],
            'qty': [100, 50],
            'due date (tanggal)': ['15/05/2026', '20/05/2026'],
            'furing': [0, 1],
            'sablon': [1, 0],
            'dtf': [0, 0],
            'bordir': [0, 1],
            'pasang kancing': [0, 1],
        })
        st.download_button(
            "⬇️ Download Template.csv",
            tpl.to_csv(index=False).encode('utf-8'),
            "Template_Order_Pabrik.csv", "text/csv",
        )

    uploaded_file = st.file_uploader("1. Upload Data Order", type=['csv', 'xlsx', 'xls'])
    start_date_input = st.date_input("2. Tanggal Mulai Produksi", datetime.today())
    start_date = datetime.combine(start_date_input, datetime.min.time()).replace(hour=8, minute=30)

    st.subheader("🛠️ Analisis Sensitivitas")
    use_custom = st.checkbox("Ubah Kapasitas/Resource Default",
                             help="Simulasikan operator sakit atau mesin rusak.")

    res = {m: 1 for m in STATIONS}
    res['3. Jahit_KemejaJaket'] = 3
    res['8. Buang_Benang'] = 2
    setup_time_val = 0.0

    if use_custom:
        with st.container(border=True):
            setup_time_val = st.number_input("Setup Antar Order (menit)", 0.0, 60.0, 0.0, 5.0)
            res['1. Potong'] = st.number_input("Operator Potong", 1, 10, 1)
            res['2. Jahit_KaosPolo'] = st.number_input("Tim Jahit Kaos/Polo", 1, 10, 1)
            res['3. Jahit_KemejaJaket'] = st.number_input("Tim Jahit Kemeja/Jaket", 1, 10, 3)
            res['4. Sablon'] = st.number_input("Mesin Sablon", 1, 10, 1)
            res['5. DTF'] = st.number_input("Mesin DTF", 1, 10, 1)
            res['6. Bordir'] = st.number_input("Mesin Bordir", 1, 10, 1)
            res['7. Pasang_Kancing'] = st.number_input("Operator Pasang Kancing", 1, 10, 1)
            res['8. Buang_Benang'] = st.number_input("Operator Buang Benang", 1, 10, 2)
            res['9. Lipat'] = st.number_input("Operator Lipat", 1, 10, 1)
            res['10. Packing'] = st.number_input("Operator Packing", 1, 10, 1)

    run_button = st.button("🚀 JALANKAN FCFS", type="primary", use_container_width=True)

# ============================================================
# 10. MAIN AREA
# ============================================================
if uploaded_file is None:
    st.info("👈 Silakan unggah file CSV / Excel di panel kiri untuk memulai.")
else:
    try:
        df = load_order_file(uploaded_file)
        df['Bulan-Tahun'] = df['due date (tanggal)'].dt.strftime('%B %Y')

        with st.container(border=True):
            c1, c2, c3 = st.columns([1, 1, 1.2])
            bulan_pilih = c1.selectbox("Filter Bulan Due Date:", ["Semua"] + list(df['Bulan-Tahun'].unique()))
            urutan_fcfs = c2.selectbox(
                "Urutan FCFS:",
                ["Sesuai urutan dataset", "Due Date Terdekat", "Due Date Terjauh"]
            )
            pilih_semua = c3.checkbox("Pilih semua order hasil filter", value=False)

        df_disp = df.copy() if bulan_pilih == "Semua" else df[df['Bulan-Tahun'] == bulan_pilih].copy()

        if urutan_fcfs == "Sesuai urutan dataset":
            df_disp = df_disp.sort_values('_dataset_order', ascending=True)
        elif urutan_fcfs == "Due Date Terdekat":
            df_disp = df_disp.sort_values(['due date (tanggal)', '_dataset_order'], ascending=[True, True])
        elif urutan_fcfs == "Due Date Terjauh":
            df_disp = df_disp.sort_values(['due date (tanggal)', '_dataset_order'], ascending=[False, True])

        default_pick = bool(pilih_semua)
        if "Pilih" not in df_disp.columns:
            df_disp.insert(0, "Pilih", default_pick)
        else:
            df_disp["Pilih"] = default_pick

        if "Priority" not in df_disp.columns:
            df_disp.insert(1, "Priority", False)

        st.subheader("📋 Pemilihan Order")
        st.info(
            "💡 Centang **Pilih** untuk memasukkan order ke simulasi FCFS. "
            "Opsi **Pilih semua order hasil filter** akan langsung mencentang seluruh order yang sedang tampil."
        )

        edited_df = st.data_editor(
            df_disp.drop(columns=['Bulan-Tahun', '_dataset_order']),
            hide_index=True,
            use_container_width=True,
        )
        df_pool = edited_df[edited_df["Pilih"] == True].copy()

        # ============================================================
        # 11. ENGINE FCFS
        # ============================================================
        if run_button:
            if len(df_pool) == 0:
                st.warning("⚠️ Centang minimal 1 pesanan untuk dijadwalkan.")
                st.stop()

            progress_bar = st.progress(0, text="Memulai simulasi FCFS…")

            progress_bar.progress(15, "1/3 Kalkulasi routing & waktu proses…")

            df_pool = df_pool.copy()
            df_pool['target_dt'] = df_pool['due date (tanggal)'].apply(
                lambda x: x.replace(hour=17, minute=30, second=0, microsecond=0)
            )
            df_pool['target_menit'] = df_pool['target_dt'].apply(
                lambda x: hitung_target_menit(x, start_date)
            )

            if urutan_fcfs == "Sesuai urutan dataset":
                df_pool = df_pool.reset_index(drop=True)
            elif urutan_fcfs == "Due Date Terdekat":
                df_pool = df_pool.sort_values('due date (tanggal)', ascending=True, kind='stable').reset_index(drop=True)
            elif urutan_fcfs == "Due Date Terjauh":
                df_pool = df_pool.sort_values('due date (tanggal)', ascending=False, kind='stable').reset_index(drop=True)

            jobs_raw = df_pool.to_dict('records')
            job_ids = [str(j['id pesanan']) for j in jobs_raw]
            P = {str(j['id pesanan']): hitung_waktu_proses(j, res, setup_time_val) for j in jobs_raw}
            D = {str(j['id pesanan']): j['target_menit'] for j in jobs_raw}
            W = {str(j['id pesanan']): 10_000 if j.get('Priority', False) else 1 for j in jobs_raw}

            progress_bar.progress(55, "2/3 Menyusun jadwal FCFS…")
            fcfs_sequence = job_ids.copy()
            fcfs_score, jadwal_final, waktu_selesai_dict = run_fcfs(fcfs_sequence, P, D, W)

            sc = jalankan_sanity_check(jadwal_final, df_pool, P, start_date)
            progress_bar.progress(100, "✅ Selesai!")

            laporan_order = []
            jadwal_op_rows = []
            pesanan_telat = 0

            for i in job_ids:
                target_nyata = df_pool[df_pool['id pesanan'].astype(str) == i]['target_dt'].iloc[0]
                selesai_nyata = konversi_ke_jam_dinding(waktu_selesai_dict[i], start_date)
                selisih_mnt = (selesai_nyata - target_nyata).total_seconds() / 60
                status_ord = 'Telat' if selisih_mnt > 0 else 'Tepat Waktu'
                if selisih_mnt > 0:
                    pesanan_telat += 1

                laporan_order.append({
                    'ID Pesanan': i,
                    'Prioritas': "⭐ Ya" if W[i] > 1 else "Tidak",
                    'Target Selesai': target_nyata.strftime('%d-%b-%y %H:%M'),
                    'Estimasi Selesai': selesai_nyata.strftime('%d-%b-%y %H:%M'),
                    'Status': status_ord,
                    'Telat (Hari)': math.ceil(max(0, selisih_mnt) / MENIT_PER_HARI),
                })

            for t in jadwal_final:
                qty_row = df_pool[df_pool['id pesanan'].astype(str) == t['job']]['qty'].iloc[0]
                jadwal_op_rows.append({
                    'Stasiun Kerja': t['m'],
                    'ID Pesanan': t['job'],
                    'Qty': qty_row,
                    'Mulai': konversi_ke_jam_dinding(t['start'], start_date).strftime('%d-%b-%y %H:%M'),
                    'Selesai': konversi_ke_jam_dinding(t['start'] + t['dur'], start_date).strftime('%d-%b-%y %H:%M'),
                })

            df_gantt = build_gantt_df(jadwal_final, df_pool, start_date)
            df_laporan = pd.DataFrame(laporan_order).sort_values(
                by=['Status', 'Estimasi Selesai'], ascending=[False, True]
            )
            df_op = pd.DataFrame(jadwal_op_rows)

            # ============================================================
            # 12. DASHBOARD — RINGKASAN METRIK
            # ============================================================
            st.divider()
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("📦 Total Order", len(job_ids))
            m2.metric("✅ Tepat Waktu", len(job_ids) - pesanan_telat)
            m3.metric("🚨 Terlambat", pesanan_telat, delta_color="inverse")
            m4.metric("📉 Skor Penalti", f"{fcfs_score:,.1f}")

            with st.container(border=True):
                st.markdown(
                    f"""
                    **Mode Penjadwalan:** FCFS  
                    **Urutan yang digunakan:** {urutan_fcfs}  
                    **Jumlah order terpilih:** {len(job_ids)}  
                    **Skor penalti total:** {fcfs_score:,.2f}
                    """
                )
                if use_custom:
                    st.caption("Analisis sensitivitas aktif — kapasitas/resource atau setup time telah diubah dari default.")
                else:
                    st.caption("Menggunakan kapasitas/resource default pabrik.")

            # ============================================================
            # 13. TAB DASHBOARD
            # ============================================================
            tab1, tab2, tab3, tab4 = st.tabs([
                "📊 Gantt Chart FCFS",
                "📑 Laporan Manajemen",
                "👨‍🔧 Lembar Kerja Operator",
                "🔎 Audit & Sanity Check",
            ])

            with tab1:
                st.markdown("**Jadwal Produksi Akhir — FCFS**")
                if not df_gantt.empty:
                    fig1 = px.timeline(
                        df_gantt,
                        x_start="Mulai", x_end="Selesai",
                        y="Stasiun Kerja", color="ID Pesanan",
                        hover_data=["Durasi (Menit)", "Qty"],
                        title=f"Gantt Chart FCFS (Urutan: {urutan_fcfs})"
                    )
                    fig1.update_yaxes(categoryorder="array", categoryarray=STATIONS[::-1])
                    fig1.update_layout(height=500, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig1, use_container_width=True)
                else:
                    st.warning("Tidak ada data jadwal untuk ditampilkan.")

            with tab2:
                st.markdown("**Status Penyelesaian Order per Tenggat Waktu**")
                def color_status(val):
                    return 'background-color:#F87171;color:white' if val == 'Telat' \
                           else 'background-color:#34D399;color:white'
                st.dataframe(
                    df_laporan.style.map(color_status, subset=['Status']),
                    use_container_width=True, height=420,
                )

            with tab3:
                st.markdown("**Instruksi Kerja (Work Order) per Stasiun Kerja**")
                for stasiun in STATIONS:
                    df_st = df_op[df_op['Stasiun Kerja'] == stasiun]
                    if df_st.empty:
                        continue
                    with st.expander(f"📁 {stasiun} — {len(df_st)} order"):
                        st.dataframe(
                            df_st.drop(columns=['Stasiun Kerja']),
                            hide_index=True, use_container_width=True,
                        )

            with tab4:
                st.markdown("### 🔎 Audit Otomatis — Verifikasi Logika Jadwal")

                if sc['err_overlap'] or sc['err_presedens'] or not sc['tabel_minggu'].empty:
                    st.error("🚨 **Sanity Check GAGAL** — ditemukan pelanggaran, lihat detail di bawah.")
                else:
                    st.success("✅ **Sanity Check PASSED** — Jadwal valid, tidak ada pelanggaran logika.")

                with st.expander("📄 Lihat Log Teks Lengkap", expanded=False):
                    st.code(sc['log_text'], language="text")

                st.divider()

                st.markdown("#### [1] Pemeriksaan Overlap Mesin")
                if sc['err_overlap']:
                    st.error("❌ Ditemukan overlap! Lihat log teks di atas untuk detail.")
                else:
                    st.success("✔️ Tidak ada dua order yang menempati mesin yang sama secara bersamaan.")

                st.divider()

                st.markdown(f"#### [2] Verifikasi Presedensi — Contoh Order: `{sc['sample_job_id']}`")
                st.caption("Satu order dipilih secara acak sebagai representasi untuk membuktikan urutan stasiun benar.")

                sr = sc['sample_row']
                rute = sc['sample_rute']

                if sr:
                    col_spec1, col_spec2 = st.columns(2)
                    with col_spec1:
                        st.markdown("**Spesifikasi Order:**")
                        spec_data = {
                            'Atribut': ['ID Pesanan', 'Jenis Produk', 'Qty', 'Due Date',
                                        'Furing', 'Sablon', 'DTF', 'Bordir', 'Pasang Kancing'],
                            'Nilai': [
                                str(sr.get('id pesanan', '-')),
                                str(sr.get('jenis produk', '-')).capitalize(),
                                str(int(sr.get('qty', 0))),
                                pd.Timestamp(sr.get('due date (tanggal)', '')).strftime('%d-%b-%Y')
                                    if sr.get('due date (tanggal)') else '-',
                                '✅ Ya' if sr.get('furing', 0) == 1 else '❌ Tidak',
                                '✅ Ya' if sr.get('sablon', 0) == 1 else '❌ Tidak',
                                '✅ Ya' if sr.get('dtf', 0) == 1 else '❌ Tidak',
                                '✅ Ya' if sr.get('bordir', 0) == 1 else '❌ Tidak',
                                '✅ Ya' if sr.get('pasang kancing', 0) == 1 else '❌ Tidak',
                            ]
                        }
                        st.dataframe(pd.DataFrame(spec_data), hide_index=True, use_container_width=True)

                    with col_spec2:
                        st.markdown("**Routing Aktif (OPC):**")
                        p_sample = P.get(sc['sample_job_id'], {})
                        opc_rows = []
                        for idx_r, m in enumerate(rute):
                            opc_rows.append({
                                'Urutan': idx_r + 1,
                                'Stasiun': m,
                                'Durasi (mnt)': round(p_sample.get(m, 0), 2),
                            })
                        st.dataframe(pd.DataFrame(opc_rows), hide_index=True, use_container_width=True)

                if rute:
                    st.markdown("**Operation Process Chart (OPC) — Flow Stasiun:**")
                    p_sample = P.get(sc['sample_job_id'], {})
                    opc_fig = go.Figure()
                    n = len(rute)
                    xs = list(range(n))
                    durs = [round(p_sample.get(m, 0), 2) for m in rute]

                    for xi, (m, d) in enumerate(zip(rute, durs)):
                        short_m = m.split('. ', 1)[-1].replace('_', ' ')
                        opc_fig.add_trace(go.Scatter(
                            x=[xi], y=[0],
                            mode='markers+text',
                            marker=dict(size=36, color='#1E3A8A', symbol='square'),
                            text=[f"<b>{xi+1}</b>"],
                            textposition='middle center',
                            textfont=dict(color='white', size=13),
                            hovertemplate=f"<b>{m}</b><br>Durasi: {d:.1f} mnt<extra></extra>",
                            showlegend=False,
                        ))
                        opc_fig.add_annotation(
                            x=xi, y=-0.18,
                            text=f"<b>{short_m}</b><br>{d:.1f} mnt",
                            showarrow=False,
                            font=dict(size=10, color='#1E3A8A'),
                            align='center',
                        )
                        if xi < n - 1:
                            opc_fig.add_annotation(
                                ax=xi + 0.08, ay=0, axref='x', ayref='y',
                                x=xi + 0.92, y=0, xref='x', yref='y',
                                showarrow=True, arrowhead=2, arrowsize=1.5,
                                arrowwidth=2, arrowcolor='#64748B',
                            )

                    opc_fig.update_layout(
                        height=200,
                        margin=dict(l=20, r=20, t=20, b=80),
                        xaxis=dict(visible=False, range=[-0.6, n - 0.4]),
                        yaxis=dict(visible=False, range=[-0.5, 0.5]),
                        plot_bgcolor='rgba(0,0,0,0)',
                        paper_bgcolor='rgba(0,0,0,0)',
                    )
                    st.plotly_chart(opc_fig, use_container_width=True)

                st.markdown(f"**Gantt Chart Order `{sc['sample_job_id']}` dalam Jadwal Final:**")
                sched_sample = sc['sample_sched']
                if sched_sample:
                    rows_s = []
                    for t in sched_sample:
                        for blk in pecah_balok_gantt(t['start'], t['dur'], start_date):
                            rows_s.append({
                                'Stasiun': t['m'],
                                'Mulai': blk['start_nyata'],
                                'Selesai': blk['end_nyata'],
                                'Durasi (Menit)': round(blk['durasi_potongan'], 2),
                            })
                    df_sample_gantt = pd.DataFrame(rows_s)
                    fig_sample = px.timeline(
                        df_sample_gantt,
                        x_start="Mulai", x_end="Selesai",
                        y="Stasiun", color="Stasiun",
                        hover_data=["Durasi (Menit)"],
                        title=f"Alur Proses Order {sc['sample_job_id']}",
                        color_discrete_sequence=px.colors.qualitative.Set2,
                    )
                    fig_sample.update_yaxes(categoryorder="array", categoryarray=rute[::-1])
                    fig_sample.update_layout(
                        height=max(250, len(rute) * 45),
                        showlegend=False,
                        plot_bgcolor='rgba(0,0,0,0)',
                        paper_bgcolor='rgba(0,0,0,0)',
                    )
                    st.plotly_chart(fig_sample, use_container_width=True)

                st.divider()

                st.markdown("#### [3] Verifikasi Hari Libur (Minggu)")
                if sc['tabel_minggu'].empty:
                    st.success("✔️ Tidak ada satu pun jadwal yang jatuh di Hari Minggu.")
                    st.dataframe(
                        pd.DataFrame(columns=['ID Pesanan', 'Stasiun', 'Mulai', 'Selesai']),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.caption("↑ Tabel di atas kosong — membuktikan tidak ada aktivitas produksi di Hari Minggu.")
                else:
                    st.error(f"❌ Ditemukan {len(sc['tabel_minggu'])} slot jadwal di Hari Minggu!")
                    st.dataframe(sc['tabel_minggu'], hide_index=True, use_container_width=True)

            # ============================================================
            # 14. DOWNLOAD EXCEL
            # ============================================================
            st.divider()
            st.subheader("📥 Unduh Rekap Excel")

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as writer:
                df_laporan.to_excel(writer, sheet_name='Laporan Manajemen', index=False)
                df_op.to_excel(writer, sheet_name='Jadwal per Stasiun', index=False)
                df_gantt.to_excel(writer, sheet_name='Gantt FCFS', index=False)

            st.download_button(
                "⬇️ Download Laporan .xlsx",
                data=buf.getvalue(),
                file_name=f"Jadwal_FCFS_{datetime.now().strftime('%d%b%Y_%H%M')}.xlsx",
                mime="application/vnd.ms-excel",
                type="secondary",
            )

    except Exception as e:
        st.error(f"🚨 Terjadi kesalahan: {e}")
        import traceback
        st.code(traceback.format_exc(), language="text")
