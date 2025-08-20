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
from datetime import datetime
import logging
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
            send_email(
                subject=container_nr,
                body="Repair Report submitted", 
                attachments=saved_files
            )
        except Exception as e:
            app.logger.error(f"Email failed: {str(e)}")

        db.session.commit()
        return jsonify({"status": "success", "message": "Report submitted successfully", "report_id": report.id})

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Submission failed: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# ===================================
# Helpers
# ===================================
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def send_email(subject, body, attachments):
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 587
    SMTP_USERNAME = os.environ.get('SMTP_USER')
    SMTP_PASSWORD = os.environ.get('SMTP_PASS')
    EMAIL_FROM = os.environ.get('EMAIL_FROM', SMTP_USERNAME)
    EMAIL_TO = os.environ.get("EMAIL_TO", "").split(",")

    msg = MIMEMultipart()
    msg['From'] = EMAIL_FROM
    msg['To'] = ', '.join(EMAIL_TO)
    msg['Subject'] = f"Herstelmelding {subject} - {datetime.now().strftime('%d-%m-%Y')}"
    msg.attach(MIMEText(body, 'html'))

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

