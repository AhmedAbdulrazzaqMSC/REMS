import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from datetime import datetime, timedelta
import logging
import io
import re
import zipfile
import urllib.request
import urllib.parse
import http.cookiejar
import xml.etree.ElementTree as ET
from werkzeug.utils import secure_filename

# ===================================
# Initialize Flask App
# ===================================
app = Flask(__name__, static_folder='static')
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/static/*": {"origins": "*"}
})

# ===================================
# Database Configuration
# ===================================
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# Adjust for postgres:// vs postgresql://
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

app.config.update({
    'SQLALCHEMY_DATABASE_URI': db_url,
    'SQLALCHEMY_TRACK_MODIFICATIONS': False,
    'SQLALCHEMY_ENGINE_OPTIONS': {
        'pool_size': 5,
        'pool_recycle': 300,
        'pool_pre_ping': True
    },
    'UPLOAD_FOLDER': os.path.join(os.getcwd(), 'uploads'),
    'MAX_CONTENT_LENGTH': 16 * 1024 * 1024  # 16MB
})

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db = SQLAlchemy(app)

# ===================================
# Database Models
# ===================================
class RepairReport(db.Model):
    __tablename__ = 'repair_reports'
    id = db.Column(db.Integer, primary_key=True)
    container_number = db.Column(db.String(11), nullable=False)
    report_date = db.Column(db.Date, nullable=False)
    technician_name = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100))
    serial_number = db.Column(db.String(100))
    warranty_id = db.Column(db.String(100))
    warranty_status = db.Column(db.String(100))
    setpoint = db.Column(db.Float)
    vents = db.Column(db.String(50))
    humidity = db.Column(db.String(50))
    ambient_temp = db.Column(db.Float)
    supply_temp_before = db.Column(db.Float)
    supply_temp_after = db.Column(db.Float)
    return_temp_before = db.Column(db.Float)
    return_temp_after = db.Column(db.Float)
    temp_in_range = db.Column(db.String(50))
    problem_description = db.Column(db.Text)
    comments = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class RepairJob(db.Model):
    __tablename__ = 'repair_jobs'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('repair_reports.id'), nullable=False)
    job_code = db.Column(db.String(50))
    description = db.Column(db.String(255))
    part_number = db.Column(db.String(100))
    part_description = db.Column(db.String(255))
    quantity = db.Column(db.Integer)
    damage_type = db.Column(db.String(50))
    old_serial = db.Column(db.String(100))
    new_serial = db.Column(db.String(100))
    labor_hours = db.Column(db.Float)

class Alarm(db.Model):
    __tablename__ = 'alarms'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('repair_reports.id'), nullable=False)
    alarm_code = db.Column(db.String(100))

class RepairListItem(db.Model):
    __tablename__ = 'repair_list_items'
    id = db.Column(db.Integer, primary_key=True)
    container_number = db.Column(db.String(11), unique=True, nullable=False, index=True)
    order_temp = db.Column(db.String(50))
    position = db.Column(db.String(100))
    alarms = db.Column(db.Text)
    etd = db.Column(db.String(50))
    vessel = db.Column(db.String(150))
    requested_repair = db.Column(db.Text)
    remarks = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "container_number": self.container_number,
            "order_temp": self.order_temp or "",
            "position": self.position or "",
            "alarms": self.alarms or "",
            "etd": self.etd or "",
            "vessel": self.vessel or "",
            "requested_repair": self.requested_repair or "",
            "remarks": self.remarks or ""
        }

# Initialize DB
with app.app_context():
    try:
        db.create_all()
        db.session.execute(text("SELECT 1"))
        app.logger.info("Database initialized successfully")
    except Exception as e:
        app.logger.critical(f"Database initialization failed: {str(e)}")
        raise

# ===================================
# Routes
# ===================================

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

# --------- LOGIN ROUTE ----------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    # Example ENV: TECHNICIANS="Admin:admin123,Brahim:bm123"
    valid_users = {}
    env_users = os.environ.get("TECHNICIANS", "")
    for pair in env_users.split(","):
        if ":" in pair:
            user, pwd = pair.split(":", 1)
            valid_users[user.strip()] = pwd.strip()

    if username in valid_users and valid_users[username] == password:
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Invalid credentials"}), 401

# --------- REPAIR LIST ----------
@app.route('/api/repair-list', methods=['GET'])
def get_repair_list():
    excel_share_url = os.environ.get('EXCEL_SHARE_URL', '').strip()
    if excel_share_url:
        try:
            items = read_repair_list_from_excel(excel_share_url)
            add_recent_history_counts(items)
            return jsonify({"status": "success", "items": items, "source": "excel"})
        except Exception as exc:
            app.logger.error("Excel repair-list sync failed: %s", exc, exc_info=True)

    # Keep REMS usable if OneDrive is temporarily unavailable or not configured.
    items = RepairListItem.query.order_by(RepairListItem.created_at.asc()).all()
    item_data = [item.to_dict() for item in items]
    add_recent_history_counts(item_data)
    return jsonify({"status": "success", "items": item_data, "source": "database"})

@app.route('/api/report-history/<string:container_nr>', methods=['GET'])
def get_report_history(container_nr):
    container_nr = container_nr.strip().upper()
    cutoff_date = (datetime.utcnow() - timedelta(days=30)).date()
    reports = RepairReport.query.filter(
        RepairReport.container_number == container_nr,
        RepairReport.report_date >= cutoff_date
    ).order_by(RepairReport.report_date.desc(), RepairReport.id.desc()).all()

    history = []
    for report in reports:
        jobs = RepairJob.query.filter_by(report_id=report.id).order_by(RepairJob.id.asc()).all()
        alarms = Alarm.query.filter_by(report_id=report.id).order_by(Alarm.id.asc()).all()
        history.append({
            "id": report.id,
            "report_date": report.report_date.strftime('%d/%m/%Y'),
            "technician_name": report.technician_name or "",
            "problem_description": report.problem_description or "",
            "comments": report.comments or "",
            "alarms": [alarm.alarm_code for alarm in alarms if alarm.alarm_code],
            "jobs": [{
                "job_code": job.job_code or "",
                "description": job.description or "",
                "part_number": job.part_number or "",
                "part_description": job.part_description or "",
                "quantity": job.quantity or 0
            } for job in jobs]
        })
    return jsonify({"status": "success", "container_number": container_nr, "history": history})

@app.route('/api/repair-list', methods=['POST'])
def upsert_repair_list_item():
    """Create/update a work item. The existing email automation can call this endpoint."""
    data = request.get_json(silent=True) or request.form
    container_nr = str(data.get('container_number') or data.get('containernr') or '').strip().upper()
    if not (len(container_nr) == 11 and container_nr[:4].isalpha() and container_nr[4:].isdigit()):
        return jsonify({"status": "error", "message": "Invalid container number format"}), 400

    item = RepairListItem.query.filter_by(container_number=container_nr).first()
    if item is None:
        item = RepairListItem(container_number=container_nr)
        db.session.add(item)

    for field in ('order_temp', 'position', 'alarms', 'etd', 'vessel', 'requested_repair', 'remarks'):
        if field in data:
            setattr(item, field, str(data.get(field) or '').strip())

    db.session.commit()
    return jsonify({"status": "success", "item": item.to_dict()}), 200

@app.route('/api/repair-list/<string:container_nr>', methods=['DELETE'])
def delete_repair_list_item(container_nr):
    item = RepairListItem.query.filter_by(container_number=container_nr.strip().upper()).first()
    if item is None:
        return jsonify({"status": "error", "message": "Container not found"}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({"status": "success"})

# --------- SUBMIT REPORT ----------
@app.route('/api/submit', methods=['POST'])
def submit_report():
    try:
        if not request.is_json and not request.form:
            return jsonify({"status": "error", "message": "Unsupported content type"}), 415

        form_data = request.form if request.form else request.get_json()
        files = request.files

        # Validate container number
        container_nr = form_data.get('containernr', '')
        if not (len(container_nr) == 11 and container_nr[:4].isalpha() and container_nr[4:].isdigit()):
            return jsonify({"status": "error", "message": "Invalid container number format"}), 400

        # Create Repair Report
        report = RepairReport(
            container_number=container_nr,
            report_date=datetime.strptime(form_data.get('datum'), '%Y-%m-%d').date(),
            technician_name=form_data.get('naam'),
            model=form_data.get('model'),
            serial_number=form_data.get('serienr'),
            warranty_id=form_data.get('warranty_id'),
            warranty_status=form_data.get('garantie'),
            setpoint=float(form_data.get('setpoint', 0)),
            vents=form_data.get('vents'),
            humidity=form_data.get('hum'),
            ambient_temp=float(form_data.get('ambient', 0)),
            supply_temp_before=float(form_data.get('supply_voor', 0)),
            supply_temp_after=float(form_data.get('supply_na', 0)),
            return_temp_before=float(form_data.get('return_voor', 0)),
            return_temp_after=float(form_data.get('return_na', 0)),
            temp_in_range=form_data.get('temp_in_range'),
            problem_description=form_data.get('probleem'),
            comments=form_data.get('opmerkingen')
        )
        db.session.add(report)
        db.session.flush()  # Get report ID

        # Jobs
        job_count = int(form_data.get('job_count', 0))
        for i in range(job_count):
            job = RepairJob(
                report_id=report.id,
                job_code=form_data.get(f'job[{i}][code]'),
                description=form_data.get(f'job[{i}][description]'),
                part_number=form_data.get(f'job[{i}][part_number]'),
                part_description=form_data.get(f'job[{i}][part_description]'),
                quantity=int(form_data.get(f'job[{i}][quantity]') or 1),
                damage_type=form_data.get(f'job[{i}][damage_type]'),
                old_serial=form_data.get(f'job[{i}][old_serial]'),
                new_serial=form_data.get(f'job[{i}][new_serial]'),
                labor_hours=float(form_data.get(f'job[{i}][labor_hours]') or 0)
            )
            db.session.add(job)

        # Alarms
        for alarm in request.form.getlist('alarm[]'):
            if alarm.strip():
                db.session.add(Alarm(report_id=report.id, alarm_code=alarm.strip()))

        # Files
        saved_files = []
        for file_key, file in files.items():
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                saved_files.append(filepath)

        # Send Email
        try:
            # Get jobs and alarms for this report
            jobs = RepairJob.query.filter_by(report_id=report.id).all()
            alarms = Alarm.query.filter_by(report_id=report.id).all()
            
            send_email(
                subject=container_nr,
                body="Repair Report submitted", 
                attachments=saved_files,
                report=report,
                jobs=jobs,
                alarms=alarms,
                afmelding=form_data.get("afmelding", "")
            )
        except Exception as e:
            app.logger.error(f"Email failed: {str(e)}")

        # A completed repair disappears from the technician work list.
        # Incomplete reports (Afmelding = Nee) remain available for follow-up.
        if str(form_data.get("afmelding", "")).strip().lower() == "ja":
            RepairListItem.query.filter_by(container_number=container_nr.upper()).delete()

        db.session.commit()
        return jsonify({"status": "success", "message": "Report submitted successfully", "report_id": report.id})

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Submission failed: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# ===================================
# Helpers
# ===================================
def add_recent_history_counts(items):
    container_numbers = {str(item.get('container_number', '')).strip().upper() for item in items}
    container_numbers.discard('')
    if not container_numbers:
        return
    cutoff_date = (datetime.utcnow() - timedelta(days=30)).date()
    reports = RepairReport.query.with_entities(RepairReport.container_number).filter(
        RepairReport.container_number.in_(container_numbers),
        RepairReport.report_date >= cutoff_date
    ).all()
    counts = {}
    for (container_number,) in reports:
        key = (container_number or '').upper()
        counts[key] = counts.get(key, 0) + 1
    for item in items:
        item['history_count'] = counts.get(str(item.get('container_number', '')).upper(), 0)

def excel_column_index(cell_reference):
    letters = re.match(r'[A-Z]+', cell_reference or '')
    if not letters:
        return 0
    result = 0
    for char in letters.group(0):
        result = result * 26 + ord(char) - 64
    return result - 1

def excel_date_display(value):
    try:
        serial = float(value)
        if serial <= 0:
            return str(value or '')
        date_value = datetime(1899, 12, 30) + __import__('datetime').timedelta(days=serial)
        return date_value.strftime('%d/%m')
    except (TypeError, ValueError, OverflowError):
        return str(value or '').strip()

def read_repair_list_from_excel(share_url):
    """Download a view-only OneDrive workbook and read its first worksheet."""
    parsed = urllib.parse.urlsplit(share_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query['download'] = '1'
    download_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment))
    request_obj = urllib.request.Request(download_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
        'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream;q=0.9,*/*;q=0.8'
    })
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    with opener.open(request_obj, timeout=20) as response:
        workbook_bytes = response.read(20 * 1024 * 1024 + 1)
    if len(workbook_bytes) > 20 * 1024 * 1024:
        raise ValueError('Excel file exceeds the 20 MB safety limit')
    if not workbook_bytes.startswith(b'PK'):
        raise ValueError('The sharing link did not return an Excel workbook')

    spreadsheet_ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    relationship_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
    office_rel_ns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

    with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as archive:
        shared_strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared_root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            for item in shared_root.findall(f'{{{spreadsheet_ns}}}si'):
                shared_strings.append(''.join(node.text or '' for node in item.iter(f'{{{spreadsheet_ns}}}t')))

        workbook_root = ET.fromstring(archive.read('xl/workbook.xml'))
        first_sheet = workbook_root.find(f'.//{{{spreadsheet_ns}}}sheet')
        if first_sheet is None:
            return []
        relationship_id = first_sheet.attrib.get(f'{{{office_rel_ns}}}id')
        rels_root = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = None
        for relation in rels_root.findall(f'{{{relationship_ns}}}Relationship'):
            if relation.attrib.get('Id') == relationship_id:
                target = relation.attrib.get('Target')
                break
        if not target:
            raise ValueError('Could not locate the Excel worksheet')
        worksheet_path = target.lstrip('/') if target.startswith('/xl/') else 'xl/' + target.lstrip('/')
        worksheet_path = worksheet_path.replace('xl/xl/', 'xl/')
        sheet_root = ET.fromstring(archive.read(worksheet_path))

        rows = []
        for row in sheet_root.findall(f'.//{{{spreadsheet_ns}}}row'):
            values = {}
            for cell in row.findall(f'{{{spreadsheet_ns}}}c'):
                column = excel_column_index(cell.attrib.get('r', ''))
                cell_type = cell.attrib.get('t')
                value_node = cell.find(f'{{{spreadsheet_ns}}}v')
                if cell_type == 'inlineStr':
                    inline = cell.find(f'{{{spreadsheet_ns}}}is')
                    value = ''.join(node.text or '' for node in inline.iter(f'{{{spreadsheet_ns}}}t')) if inline is not None else ''
                else:
                    value = value_node.text if value_node is not None else ''
                    if cell_type == 's' and value:
                        value = shared_strings[int(value)]
                values[column] = value
            if values:
                rows.append([values.get(i, '') for i in range(max(values) + 1)])

    if not rows:
        return []
    headers = [str(header).strip() for header in rows[0]]
    header_positions = {header.casefold(): index for index, header in enumerate(headers)}

    def get_value(row, header):
        index = header_positions.get(header.casefold())
        return str(row[index]).strip() if index is not None and index < len(row) else ''

    items = []
    for row in rows[1:]:
        container_number = get_value(row, 'Container nummer').upper()
        if not container_number:
            continue
        items.append({
            'container_number': container_number,
            'order_temp': get_value(row, 'Order Temp'),
            'position': get_value(row, 'Positie'),
            'alarms': get_value(row, 'Alarm(en)'),
            'etd': excel_date_display(get_value(row, 'Vertrek')),
            'vessel': get_value(row, 'Vessel'),
            'requested_repair': get_value(row, 'Gevraagd repair'),
            'remarks': get_value(row, 'Opmerkingen')
        })
    return items

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def create_email_body(report, jobs, alarms, afmelding=""):
    """Create HTML email body with report details"""
    if not report:
        return "<p>Repair Report submitted</p>"

    afmelding_value = (afmelding or "").strip()
    if afmelding_value.lower() == "nee":
        afmelding_display = '<span style="color:#d60000;font-weight:bold;font-size:16px;">NEE</span>'
    elif afmelding_value.lower() == "ja":
        afmelding_display = '<span style="color:#168a2e;font-weight:bold;">JA</span>'
    else:
        afmelding_display = "N/A"
    
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            .section {{ margin-bottom: 20px; }}
            .section-title {{ font-weight: bold; font-size: 18px; margin-bottom: 10px; }}
        </style>
    </head>
    <body>
        <h2>Repair Report for Container: {report.container_number}</h2>
        
        <div class="section">
            <div class="section-title">General Information</div>
            <table>
                <tr><th>Container Number</th><td>{report.container_number}</td></tr>
                <tr><th>Date</th><td>{report.report_date}</td></tr>
                <tr><th>Technician</th><td>{report.technician_name}</td></tr>
                <tr><th>Model</th><td>{report.model or 'N/A'}</td></tr>
                <tr><th>Serial Number</th><td>{report.serial_number or 'N/A'}</td></tr>
                <tr><th>Warranty ID</th><td>{report.warranty_id or 'N/A'}</td></tr>
                <tr><th>Warranty Status</th><td>{report.warranty_status or 'N/A'}</td></tr>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">Settings and Readings</div>
            <table>
                <tr><th>Setpoint</th><td>{report.setpoint or 'N/A'} °C</td></tr>
                <tr><th>Vents</th><td>{report.vents or 'N/A'}</td></tr>
                <tr><th>Humidity</th><td>{report.humidity or 'N/A'}</td></tr>
                <tr><th>Ambient</th><td>{report.ambient_temp or 'N/A'} °C</td></tr>
                <tr><th>Supply Temp Before</th><td>{report.supply_temp_before or 'N/A'} °C</td></tr>
                <tr><th>Supply Temp After</th><td>{report.supply_temp_after or 'N/A'} °C</td></tr>
                <tr><th>Return Temp Before</th><td>{report.return_temp_before or 'N/A'} °C</td></tr>
                <tr><th>Return Temp After</th><td>{report.return_temp_after or 'N/A'} °C</td></tr>
                <tr><th>Temperature In Range</th><td>{report.temp_in_range or 'N/A'}</td></tr>
                <tr><th>Afmelding</th><td>{afmelding_display}</td></tr>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">Problem Description</div>
            <p>{report.problem_description or 'N/A'}</p>
        </div>
        
        <div class="section">
            <div class="section-title">Comments</div>
            <p>{report.comments or 'N/A'}</p>
        </div>
    """
    
    # Add jobs section if available
    if jobs:
        html += """
        <div class="section">
            <div class="section-title">Job Tasks</div>
            <table>
                <tr>
                    <th>Job Code</th>
                    <th>Description</th>
                    <th>Part Number</th>
                    <th>Part Description</th>
                    <th>Quantity</th>
                    <th>Damage Type</th>
                    <th>Old Serial</th>
                    <th>New Serial</th>
                    <th>Labor Hours</th>
                </tr>
        """
        
        for job in jobs:
            html += f"""
                <tr>
                    <td>{job.job_code or 'N/A'}</td>
                    <td>{job.description or 'N/A'}</td>
                    <td>{job.part_number or 'N/A'}</td>
                    <td>{job.part_description or 'N/A'}</td>
                    <td>{job.quantity or 'N/A'}</td>
                    <td>{job.damage_type or 'N/A'}</td>
                    <td>{job.old_serial or 'N/A'}</td>
                    <td>{job.new_serial or 'N/A'}</td>
                    <td>{job.labor_hours or 'N/A'}</td>
                </tr>
            """
        
        html += "</table></div>"
    
    # Add alarms section if available
    if alarms:
        html += """
        <div class="section">
            <div class="section-title">Alarms</div>
            <ul>
        """
        
        for alarm in alarms:
            html += f"<li>{alarm.alarm_code or 'N/A'}</li>"
        
        html += "</ul></div>"
    
    html += """
        <div class="section">
            <p>This report was automatically generated by the REMS system.</p>
        </div>
    </body>
    </html>
    """
    
    return html

def send_email(subject, body, attachments, report=None, jobs=None, alarms=None, afmelding=""):
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 587
    SMTP_USERNAME = os.environ.get('EMAIL_USER')
    SMTP_PASSWORD = os.environ.get('EMAIL_PASS')
    EMAIL_FROM = os.environ.get('EMAIL_FROM', SMTP_USERNAME)
    EMAIL_TO = os.environ.get("EMAIL_TO", "").split(",")

    msg = MIMEMultipart()
    msg['From'] = EMAIL_FROM
    msg['To'] = ', '.join(EMAIL_TO)
    msg['Subject'] = f"Herstelmelding {subject} - {datetime.now().strftime('%d-%m-%Y')}"
    
    # Create HTML email body with report data
    html_content = create_email_body(report, jobs, alarms, afmelding)
    msg.attach(MIMEText(html_content, 'html'))

    for filepath in attachments:
        try:
            with open(filepath, 'rb') as f:
                if filepath.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    img = MIMEImage(f.read())
                    img.add_header('Content-Disposition', 'attachment', filename=os.path.basename(filepath))
                    msg.attach(img)
                else:
                    part = MIMEApplication(f.read(), Name=os.path.basename(filepath))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(filepath)}"'
                    msg.attach(part)
        except Exception as e:
            app.logger.error(f"Failed to attach {filepath}: {str(e)}")

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        app.logger.info(f"Email sent to: {EMAIL_TO}")

# ===================================
# Run App
# ===================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

