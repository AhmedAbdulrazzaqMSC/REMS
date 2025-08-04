import os
import pandas as pd
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

# Detect environment (Render sets 'RENDER' in env)
IS_RENDER = os.environ.get("RENDER", None) is not None

# Directories and files
BASE_DIR = "/tmp" if IS_RENDER else "."
PHOTO_DIR = os.path.join(BASE_DIR, "photos")
TEMP_FILE = os.path.join(BASE_DIR, "temp_reports.xlsx")
PERM_FILE = os.path.join(BASE_DIR, "master_log.xlsx")

EMAIL_FROM = "emergencyrepairsmpet@gmail.com"
EMAIL_PASS = "gvwe limw yzya oejc"
EMAIL_TO = "A.abdulrazzaq@medrepair.eu"

os.makedirs(PHOTO_DIR, exist_ok=True)

@app.route('/')
def index():
    return render_template('Index.html')


def initialize_temp_file():
    if not os.path.exists(TEMP_FILE):
        df = pd.DataFrame(columns=[
            "containernr", "datum", "naam", "model", "serienr", "warranty_id",
            "garantie", "setpoint", "vents", "hum", "ambient", "supply_voor",
            "supply_na", "return_voor", "return_na", "temp_in_range", "probleem",
            "opmerkingen", "alarms", "job_description", "job_code", "part_number",
            "part_description", "quantity", "old_serial", "new_serial", "labor_hours",
            "damage_type"
        ])
        df.to_excel(TEMP_FILE, index=False)

def move_data_to_master():
    if not os.path.exists(TEMP_FILE):
        print("Temporary file does not exist.")
        return

    temp_df = pd.read_excel(TEMP_FILE)
    if temp_df.empty:
        print("Temporary file is empty. Nothing to move.")
        return

    if os.path.exists(PERM_FILE):
        perm_df = pd.read_excel(PERM_FILE)
        combined_df = pd.concat([perm_df, temp_df], ignore_index=True)
    else:
        combined_df = temp_df

    combined_df.to_excel(PERM_FILE, index=False)
    temp_df.iloc[0:0].to_excel(TEMP_FILE, index=False)
    print("Moved data from temp to master_log.xlsx.")


def send_html_email(meta, jobs, alarms, photo_paths):
    msg = MIMEMultipart()
    msg["Subject"] = f"Herstelmail ({meta['containernr']})"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    meta_html = "".join([f"<p><strong>{k}:</strong> {v}</p>" for k, v in meta.items()])
    alarm_html = "<ul>" + "".join([f"<li>{a}</li>" for a in alarms]) + "</ul>"

    job_rows = "".join([
        f"<tr><td>{j['description']}</td><td>{j['code']}</td><td>{j['part_number']}</td><td>{j['part_description']}</td><td>{j['quantity']}</td><td>{j['old_serial']}</td><td>{j['new_serial']}</td><td>{j['damage_type']}</td></tr>"
        for j in jobs
    ])
    job_table = f"""
        <table border='1' cellpadding='4' cellspacing='0'>
        <tr><th>Description</th><th>Code</th><th>Part Number</th><th>Part Description</th><th>Qty</th><th>Old Serial</th><th>New Serial</th><th>Damage</th></tr>
        {job_rows}
        </table>
    """

    html = f"""
    <html>
    <body>
    <h2>REPAIR REPORT</h2>
    {meta_html}
    <h3>Alarms</h3>
    {alarm_html}
    <h3>Job Tasks</h3>
    {job_table}
    </body></html>
    """
    msg.attach(MIMEText(html, "html"))

    for path in photo_paths:
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(path)}")
            msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.send_message(msg)
        print("Email sent")


scheduler = BackgroundScheduler()
scheduler.add_job(move_data_to_master, 'interval', hours=1)
scheduler.start()

@app.route('/submit', methods=['POST'])
def submit():
    form = request.form
    alarms = request.form.getlist('alarm[]')
    files = request.files

    jobs = []
    job_count = int(form.get("job_count", 0))
    for i in range(job_count):
        jobs.append({
            "description": form.get(f"job[{i}][description]", ""),
            "code": form.get(f"job[{i}][code]", ""),
            "part_number": form.get(f"job[{i}][part_number]", ""),
            "part_description": form.get(f"job[{i}][part_description]", ""),
            "quantity": form.get(f"job[{i}][quantity]", ""),
            "old_serial": form.get(f"job[{i}][old_serial]", ""),
            "new_serial": form.get(f"job[{i}][new_serial]", ""),
            "labor_hours": form.get(f"job[{i}][labor_hours]", ""),
            "damage_type": form.get(f"job[{i}][damage_type]", "")
        })

    meta = {
        "containernr": form.get("containernr", ""),
        "datum": form.get("datum", ""),
        "naam": form.get("naam", ""),
        "model": form.get("model", ""),
        "serienr": form.get("serienr", ""),
        "warranty_id": form.get("warranty_id", ""),
        "garantie": form.get("garantie", ""),
        "setpoint": form.get("setpoint", ""),
        "vents": form.get("vents", ""),
        "hum": form.get("hum", ""),
        "ambient": form.get("ambient", ""),
        "supply_voor": form.get("supply_voor", ""),
        "supply_na": form.get("supply_na", ""),
        "return_voor": form.get("return_voor", ""),
        "return_na": form.get("return_na", ""),
        "temp_in_range": form.get("temp_in_range", ""),
        "probleem": form.get("probleem", ""),
        "opmerkingen": form.get("opmerkingen", "")
    }

    photo_paths = []
    for key in files:
        for f in request.files.getlist(key):
            if f.filename:
                filename = secure_filename(f.filename)
                ext = os.path.splitext(filename)[1]
                save_name = f"{meta['containernr']}_{key}_{len(photo_paths)+1}{ext}"
                save_path = os.path.join(PHOTO_DIR, save_name)
                f.save(save_path)
                photo_paths.append(save_path)

    records = []
    for job in jobs:
        row = {
            **meta,
            "alarms": ", ".join(alarms),
            "job_description": job["description"],
            "job_code": job["code"],
            "part_number": job["part_number"],
            "part_description": job["part_description"],
            "quantity": job["quantity"],
            "old_serial": job["old_serial"],
            "new_serial": job["new_serial"],
            "labor_hours": job["labor_hours"],
            "damage_type": job["damage_type"]
        }
        records.append(row)

    df_new = pd.DataFrame(records)
    existing_df = pd.read_excel(TEMP_FILE)
    df_combined = pd.concat([existing_df, df_new], ignore_index=True)
    df_combined.to_excel(TEMP_FILE, index=False)


    send_html_email(meta, jobs, alarms, photo_paths)

    move_data_to_master()  # ✅ Force master_log update after submission

    return jsonify({"status": "success"})


if __name__ == '__main__':
    initialize_temp_file()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)


