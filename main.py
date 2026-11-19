import calendar
from datetime import datetime, timedelta, timezone
from io import StringIO
import os
import random
import re
from urllib.parse import urlencode
import gspread
import pandas as pd
from playwright.sync_api import sync_playwright
import requests


def jeda_manusia(page, min_detik=1.5, max_detik=3.0):
  waktu = random.uniform(min_detik, max_detik)
  page.wait_for_timeout(int(waktu * 1000))


def gerakan_alami(page):
  page.mouse.move(random.randint(200, 700), random.randint(200, 500))
  page.evaluate("window.scrollBy(0, 250)")
  jeda_manusia(page, 0.5, 1.2)
  page.evaluate("window.scrollBy(0, -150)")


def format_nama_kapal(nama_raw):
  teks = re.sub(r"\s+", " ", str(nama_raw)).strip().upper()
  teks = re.sub(r"^KM\.?\s*", "", teks)
  match = re.match(r"^([A-Z\s\.]+?)[\s/\-]+(\d.*)$", teks)
  if match:
    nama_bagian = match.group(1).strip()
    voyage_bagian = match.group(2).strip()
    return f"KM. {nama_bagian} - {voyage_bagian}"
  return f"KM. {teks}"


def scrape_spil(page):
  print("1. Mengambil data jadwal SPIL...")
  base_url = "https://www.myspil.com/myspilcom/port/select"
  params = urlencode({
      "portfrom": "Manokwari",
      "portto[]": "Surabaya",
      "etd": "",
      "vesselname": "",
      "vesselid": "",
  })
  url = f"{base_url}?{params}"

  for percobaan in range(1, 3):
    try:
      page.goto(url, wait_until="domcontentloaded", timeout=60000)
      jeda_manusia(page, 2.0, 3.5)

      for teks_tombol in ["Agree", "OK"]:
        selector_popup = f"button:text-is('{teks_tombol}'):visible:enabled"
        while page.locator(selector_popup).count() > 0:
          page.locator(selector_popup).first.click(timeout=3000)
          jeda_manusia(page, 1.0, 1.5)

      gerakan_alami(page)
      page.wait_for_selector("text=Vessel Voyage", timeout=30000)
      jeda_manusia(page, 2.0, 3.0)
      html = page.content()

      semua_tabel = pd.read_html(StringIO(html))
      tabel_target = None

      for df in semua_tabel:
        df.columns = [
            col[-1] if isinstance(col, tuple) else str(col).strip()
            for col in df.columns
        ]
        if "Vessel Voyage" in df.columns and "Closing Time" in df.columns:
          tabel_target = df
          break

      if tabel_target is not None and not tabel_target.empty:
        df_hasil = pd.DataFrame()

        nama_bersih = (
            tabel_target["Vessel Voyage"]
            .astype(str)
            .str.extract(r"^(.*?)(?:IMO|$)", expand=False)
            .fillna("")
            .str.strip()
        )
        df_hasil["Nama Kapal"] = nama_bersih.apply(format_nama_kapal)

        tgl_closing = (
            tabel_target["Closing Time"]
            .astype(str)
            .str.extract(r"(\d{2}/\d{2}/\d{4})", expand=False)
        )
        jam_etd = (
            tabel_target["Estimated Time Departure"]
            .astype(str)
            .str.extract(r"(\d{2}:\d{2})", expand=False)
        )

        eta_str_awal = tgl_closing + " " + jam_etd
        eta_dt = pd.to_datetime(
            eta_str_awal, format="%d/%m/%Y %H:%M", errors="coerce"
        ) + pd.Timedelta(days=1)

        etb_dt = eta_dt + pd.Timedelta(hours=1)
        etd_dt = etb_dt + pd.Timedelta(hours=24)

        df_hasil["ETA"] = eta_dt.dt.strftime("%d/%m/%Y %H:%M")
        df_hasil["ETB"] = etb_dt.dt.strftime("%d/%m/%Y %H:%M")
        df_hasil["ETD"] = etd_dt.dt.strftime("%d/%m/%Y %H:%M")

        df_hasil = df_hasil.dropna(subset=["ETA"]).drop_duplicates()
        if not df_hasil.empty:
          print(f"   -> Berhasil mengambil {len(df_hasil)} jadwal SPIL.\n")
          return df_hasil

      print(
          f"   -> Percobaan {percobaan} SPIL belum memuat data, mencoba ulang..."
      )
    except Exception as e:
      print(f"   -> Kendala SPIL (percobaan {percobaan}): {e}")

  return pd.DataFrame(columns=["Nama Kapal", "ETA", "ETB", "ETD"])


def scrape_temas(page):
  # Menggunakan UTC+9 (WIT) agar tanggal selalu akurat sesuai waktu Manokwari
  hari_ini = datetime.now(timezone.utc) + timedelta(hours=9)
  satu_bulan_kedepan = hari_ini + timedelta(days=30)

  tgl_mulai = hari_ini.strftime("%Y-%m-%d")
  tgl_akhir = satu_bulan_kedepan.strftime("%Y-%m-%d")

  print(f"2. Mengambil data jadwal TEMAS ({tgl_mulai} s/d {tgl_akhir})...")

  domain = "https://kliktemas.com"
  path = "/schedule"
  params = urlencode(
      {"routes": f"IDMKW~MANOKWARI~IDSUB~SURABAYA~{tgl_mulai}~{tgl_akhir}"},
      safe="~",
  )
  url = f"{domain}{path}?{params}"

  for percobaan in range(1, 3):
    try:
      page.goto(url, wait_until="load", timeout=45000)
      jeda_manusia(page, 3.0, 4.5)
      gerakan_alami(page)

      page.wait_for_selector("text=Est. Departure", timeout=25000)
      jeda_manusia(page, 1.5, 2.5)
      teks_halaman = page.inner_text("body")

      baris_teks = [b.strip() for b in teks_halaman.splitlines() if b.strip()]
      data_jadwal = []

      pola_kapal = re.compile(r"^[A-Za-z0-9\s\.]+\s*-\s*\d+/\d+$")
      pola_tgl = re.compile(r"^\d{2}\s+[A-Za-z]{3}\s+\d{4}$")

      for i, baris in enumerate(baris_teks):
        if pola_kapal.match(baris):
          nama_kapal = format_nama_kapal(baris)
          blok_bawah = baris_teks[i + 1 : i + 12]
          tanggal_kartu = [t for t in blok_bawah if pola_tgl.match(t)]

          etd_asli = next(iter(tanggal_kartu), None)
          if etd_asli:
            tgl_departure_dt = pd.to_datetime(etd_asli, format="%d %b %Y")
            eta_hasil_str = (tgl_departure_dt - timedelta(days=1)).strftime(
                "%d/%m/%Y"
            )
            etd_hasil_str = tgl_departure_dt.strftime("%d/%m/%Y")

            data_jadwal.append({
                "Nama Kapal": nama_kapal,
                "ETA": eta_hasil_str,
                "ETB": eta_hasil_str,
                "ETD": etd_hasil_str,
            })

      if data_jadwal:
        df_hasil = pd.DataFrame(data_jadwal).drop_duplicates()
        print(f"   -> Berhasil mengambil {len(df_hasil)} jadwal TEMAS.\n")
        return df_hasil

      print(
          f"   -> Percobaan {percobaan} TEMAS belum memuat data, mencoba"
          " ulang..."
      )
    except Exception as e:
      print(f"   -> Kendala TEMAS (percobaan {percobaan}): {e}")

  return pd.DataFrame(columns=["Nama Kapal", "ETA", "ETB", "ETD"])


def scrape_tanto(page):
  print("3. Mengambil data jadwal TANTO...")
  url = "https://www.tantonet.com/schedule.php"

  for percobaan in range(1, 3):
    try:
      page.goto(url, wait_until="domcontentloaded", timeout=45000)
      jeda_manusia(page, 2.0, 3.5)
      gerakan_alami(page)

      page.select_option("select >> nth=0", label="MANOKWARI")
      jeda_manusia(page, 2.0, 3.0)

      kotak_select2 = page.locator(".select2-selection").nth(1)
      if kotak_select2.count() > 0 and kotak_select2.is_visible():
        kotak_select2.click()
        jeda_manusia(page, 0.8, 1.5)
        page.keyboard.type("SURABAYA", delay=random.randint(90, 160))
        jeda_manusia(page, 1.5, 2.5)
        opsi_sby = page.locator(".select2-results__option:has-text('SURABAYA')")
        if opsi_sby.count() > 0:
          opsi_sby.first.click()
        else:
          page.keyboard.press("Enter")
      else:
        page.select_option("select >> nth=1", label="SURABAYA")

      jeda_manusia(page, 1.5, 2.5)
      tombol_search = page.locator(
          "button:has-text('Search'), input[value*='Search']"
      ).first
      tombol_search.click()

      page.wait_for_selector(
          "th:has-text('Vessel'), td:has-text('Vessel')", timeout=25000
      )
      jeda_manusia(page, 2.0, 3.5)
      html = page.content()

      semua_tabel = pd.read_html(StringIO(html))
      tabel_asli = None

      for df in semua_tabel:
        df.columns = [
            col[-1] if isinstance(col, tuple) else str(col).strip()
            for col in df.columns
        ]
        if "Vessel" in df.columns and "Closing" in df.columns:
          tabel_asli = df
          break

      if tabel_asli is not None and not tabel_asli.empty:
        df_hasil = pd.DataFrame()
        df_hasil["Nama Kapal"] = tabel_asli["Vessel"].apply(format_nama_kapal)

        tanggal_closing = pd.to_datetime(tabel_asli["Closing"], errors="coerce")
        tanggal_eta = tanggal_closing + pd.Timedelta(days=1)
        eta_formatted = tanggal_eta.dt.strftime("%d/%m/%Y")

        tanggal_etd = pd.to_datetime(tabel_asli["ETD"], errors="coerce")
        etd_formatted = tanggal_etd.dt.strftime("%d/%m/%Y").fillna(
            tabel_asli["ETD"].astype(str).str.strip()
        )

        df_hasil["ETA"] = eta_formatted
        df_hasil["ETB"] = eta_formatted
        df_hasil["ETD"] = etd_formatted

        df_hasil = df_hasil.dropna(subset=["ETA"]).drop_duplicates()
        if not df_hasil.empty:
          print(f"   -> Berhasil mengambil {len(df_hasil)} jadwal TANTO.\n")
          return df_hasil

      print(
          f"   -> Percobaan {percobaan} TANTO belum memuat data, mencoba"
          " ulang..."
      )
    except Exception as e:
      print(f"   -> Kendala TANTO (percobaan {percobaan}): {e}")

  return pd.DataFrame(columns=["Nama Kapal", "ETA", "ETB", "ETD"])


def simpan_ke_google_sheets(daftar_pelayaran):
  print("4. Mengirim data & mengatur tampilan ke Google Sheets...")
  file_kunci = "credentials.json"
  spreadsheet_id = "1Kff0jGlzrMSYE8tt-q5V14QajC2fH---20su3l8S4tc"

  warna_hijau_tua = {"red": 0.15, "green": 0.45, "blue": 0.25}
  warna_hijau_muda = {"red": 0.88, "green": 0.95, "blue": 0.90}
  warna_abu_tua = {"red": 0.35, "green": 0.38, "blue": 0.42}
  warna_abu_muda = {"red": 0.93, "green": 0.94, "blue": 0.95}
  teks_putih = {"red": 1.0, "green": 1.0, "blue": 1.0}
  teks_hitam = {"red": 0.1, "green": 0.1, "blue": 0.1}

  try:
    gc = gspread.service_account(filename=file_kunci)
    sh = gc.open_by_key(spreadsheet_id)
    worksheet = sh.sheet1
    sheet_id = worksheet.id

    baris_untuk_sheets = []
    info_blok_tabel = []
    baris_saat_ini = 1

    for idx, (judul, df) in enumerate(daftar_pelayaran):
      baris_judul = baris_saat_ini
      baris_untuk_sheets.append([judul])
      baris_saat_ini += 1

      baris_header = baris_saat_ini
      baris_untuk_sheets.append(df.columns.tolist())
      baris_saat_ini += 1

      baris_awal_data = baris_saat_ini
      if not df.empty:
        baris_untuk_sheets.extend(df.values.tolist())
        baris_saat_ini += len(df)
        ada_data = True
      else:
        baris_untuk_sheets.append(["Data jadwal kosong"])
        baris_saat_ini += 1
        ada_data = False
      baris_akhir_data = baris_saat_ini - 1

      info_blok_tabel.append({
          "judul": judul,
          "baris_judul": baris_judul,
          "baris_header": baris_header,
          "baris_awal_data": baris_awal_data,
          "baris_akhir_data": baris_akhir_data,
          "ada_data": ada_data,
      })

      if idx < len(daftar_pelayaran) - 1:
        baris_untuk_sheets.append([])
        baris_untuk_sheets.append([])
        baris_saat_ini += 2

    worksheet.clear()
    sh.batch_update({
        "requests": [{
            "updateCells": {
                "range": {"sheetId": sheet_id},
                "fields": "userEnteredFormat",
            }
        }]
    })

    worksheet.update(range_name="A1", values=baris_untuk_sheets)

    requests_styling = []
    border_hitam = {
        "style": "SOLID",
        "width": 1,
        "color": {"red": 0.2, "green": 0.2, "blue": 0.2},
    }

    for blok in info_blok_tabel:
      is_spil = blok["judul"] == "SPIL"
      bg_judul = warna_hijau_tua if is_spil else warna_abu_tua
      bg_header = warna_hijau_muda if is_spil else warna_abu_muda

      requests_styling.append({
          "repeatCell": {
              "range": {
                  "sheetId": sheet_id,
                  "startRowIndex": blok["baris_judul"] - 1,
                  "endRowIndex": blok["baris_judul"],
                  "startColumnIndex": 0,
                  "endColumnIndex": 4,
              },
              "cell": {
                  "userEnteredFormat": {
                      "backgroundColor": bg_judul,
                      "textFormat": {
                          "foregroundColor": teks_putih,
                          "bold": True,
                          "fontSize": 11,
                      },
                      "horizontalAlignment": "LEFT",
                      "verticalAlignment": "MIDDLE",
                  }
              },
              "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
          }
      })

      requests_styling.append({
          "repeatCell": {
              "range": {
                  "sheetId": sheet_id,
                  "startRowIndex": blok["baris_header"] - 1,
                  "endRowIndex": blok["baris_header"],
                  "startColumnIndex": 0,
                  "endColumnIndex": 4,
              },
              "cell": {
                  "userEnteredFormat": {
                      "backgroundColor": bg_header,
                      "textFormat": {
                          "foregroundColor": teks_hitam,
                          "bold": True,
                          "fontSize": 10,
                      },
                      "horizontalAlignment": "CENTER",
                      "verticalAlignment": "MIDDLE",
                  }
              },
              "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
          }
      })

      # Membuat kolom Nama Kapal (kolom A / index 0) menjadi huruf tebal jika ada data
      if blok["ada_data"]:
        requests_styling.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": blok["baris_awal_data"] - 1,
                    "endRowIndex": blok["baris_akhir_data"],
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "bold": True,
                        },
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(textFormat.bold,verticalAlignment)",
            }
        })

      requests_styling.append({
          "repeatCell": {
              "range": {
                  "sheetId": sheet_id,
                  "startRowIndex": blok["baris_awal_data"] - 1,
                  "endRowIndex": blok["baris_akhir_data"],
                  "startColumnIndex": 1,
                  "endColumnIndex": 4,
              },
              "cell": {
                  "userEnteredFormat": {
                      "horizontalAlignment": "CENTER",
                      "verticalAlignment": "MIDDLE",
                  }
              },
              "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment)",
          }
      })

      requests_styling.append({
          "updateBorders": {
              "range": {
                  "sheetId": sheet_id,
                  "startRowIndex": blok["baris_judul"] - 1,
                  "endRowIndex": blok["baris_akhir_data"],
                  "startColumnIndex": 0,
                  "endColumnIndex": 4,
              },
              "top": border_hitam,
              "bottom": border_hitam,
              "left": border_hitam,
              "right": border_hitam,
              "innerHorizontal": border_hitam,
              "innerVertical": border_hitam,
          }
      })

    requests_styling.append({
        "autoResizeDimensions": {
            "dimensions": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": 0,
                "endIndex": 4,
            }
        }
    })

    sh.batch_update({"requests": requests_styling})
    print(
        "   -> Sukses! Data & desain tabel berhasil diterapkan di Google"
        " Sheets.\n"
    )
  except FileNotFoundError:
    print("   -> Gagal: File kunci credentials.json belum ditemukan.\n")
  except Exception as e:
    print("   -> Gagal mengirim ke Google Sheets:", e, "\n")


def kirim_notifikasi_wa_fonnte(daftar_pelayaran):
  print("5. Mengirim ringkasan jadwal ke WhatsApp...")
  token = os.environ.get("FONNTE_TOKEN")
  target = os.environ.get("WA_TARGET")

  if not token:
    print("   -> Gagal: FONNTE_TOKEN belum diatur di GitHub Secrets.\n")
    return

  waktu_wit = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime(
      "%d/%m/%Y %H:%M WIT"
  )

  pesan = f"*UPDATE JADWAL KAPAL (MKW - SUB)*\n_{waktu_wit}_\n\n"

  for judul, df in daftar_pelayaran:
    pesan += f"*{judul}*\n"
    if not df.empty:
      for idx, (_, row) in enumerate(df.iterrows(), start=1):
        pesan += f"  {idx}. *{row['Nama Kapal']}*\n"
        pesan += f"     • ETA: {row['ETA']}\n"
        pesan += f"     • ETB: {row['ETB']}\n"
        pesan += f"     • ETD: {row['ETD']}\n"
    else:
      pesan += "  • _Data jadwal kosong_\n"
    pesan += "\n"

  pesan = pesan.rstrip()

  url = "https://api.fonnte.com/send"
  headers = {"Authorization": token}
  payload = {
      "target": target,
      "message": pesan,
      "delay": "2-5",  # Opsional: jeda acak 2-5 detik antar grup agar aman
  }

  try:
    response = requests.post(url, headers=headers, data=payload, timeout=15)
    print(f"   -> Respon Server Fonnte: {response.text}\n")
  except Exception as e:
    print(f"   -> Error koneksi WhatsApp: {e}\n")


def main():
  print("=== MEMULAI PROSES SCRAPING JADWAL PELAYARAN ===\n")

  with sync_playwright() as p:
    argumen_stealth = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]

    browser = p.chromium.launch(headless=True, args=argumen_stealth)

    context = browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="id-ID",
        timezone_id="Asia/Jayapura",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )

    context.add_init_script("""
      Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
      });
    """)

    page = context.new_page()

    df_spil = scrape_spil(page)
    jeda_manusia(page, 2.0, 3.5)

    df_temas = scrape_temas(page)
    jeda_manusia(page, 2.0, 3.5)

    df_tanto = scrape_tanto(page)

    browser.close()

  daftar_pelayaran = [
      ("SPIL", df_spil),
      ("TANTO", df_tanto),
      ("TEMAS", df_temas),
  ]

  for judul, df in daftar_pelayaran:
    print("==========================================================")
    print(judul)
    print("==========================================================")
    if not df.empty:
      print(df.to_string(index=False))
    else:
      print("Data jadwal kosong")
    print()

  simpan_ke_google_sheets(daftar_pelayaran)
  kirim_notifikasi_wa_fonnte(daftar_pelayaran)


if __name__ == "__main__":
  main()
