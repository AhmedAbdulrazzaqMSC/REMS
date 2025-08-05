import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text  # Required for raw SQL queries
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from datetime import datetime
import logging
from werkzeug.utils import secure_filename

# Initialize Flask app
app = Flask(__name__, static_folder=None)
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/static/*": {"origins": "*"}
})

# ======================
# Configuration
# ======================

# Database Configuration
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    raise RuntimeError("DATABASE_URL environment variable is not set")

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

# ======================
# Database Models
# ======================

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

# ======================
# Initialize Database
# ======================

with app.app_context():
    try:
        db.create_all()
        db.session.execute(text("SELECT 1"))  # Fixed: Using text() wrapper
        app.logger.info("Database initialized successfully")
    except Exception as e:
        app.logger.critical(f"Database initialization failed: {str(e)}")
        raise

# ======================
# Routes
# ======================

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

@app.route('/api/submit', methods=['POST'])
def submit_report():
    try:
        # Validate request
        if not request.is_json and not request.form:
            return jsonify({"status": "error", "message": "Unsupported content type"}), 415

        form_data = request.form if request.form else request.get_json()
        files = request.files
        
        # Validate container number
        container_nr = form_data.get('containernr', '')
        if not (len(container_nr) == 11 and container_nr[:4].isalpha() and container_nr[4:].isdigit()):
            return jsonify({"status": "error", "message": "Invalid container number format"}), 400

        # Create report
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
        
        # Process jobs
        job_count = int(form_data.get('job_count', 0))
        for i in range(job_count):
            job_data = form_data.get(f'job[{i}]')
            if job_data:
                job = RepairJob(
                    report=report,
                    job_code=job_data.get('code'),
                    description=job_data.get('description'),
                    part_number=job_data.get('part_number'),
                    part_description=job_data.get('part_description'),
                    quantity=int(job_data.get('quantity', 1)),
                    damage_type=job_data.get('damage_type'),
                    old_serial=job_data.get('old_serial'),
                    new_serial=job_data.get('new_serial'),
                    labor_hours=float(job_data.get('labor_hours', 0))
                )
                db.session.add(job)
        
        # Process alarms
        for alarm in request.form.getlist('alarm[]'):
            if alarm.strip():
                db.session.add(Alarm(report=report, alarm_code=alarm.strip()))
        
        # Process files
        saved_files = []
        for file_key, file in files.items():
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                saved_files.append(filepath)

        # Send email
        try:
            send_email(
                subject=f"REMS Report - {container_nr}",
                body=generate_email_content(form_data, saved_files),
                attachments=saved_files
            )
        except Exception as e:
            app.logger.error(f"Email failed: {str(e)}")

        db.session.commit()
        return jsonify({
            "status": "success", 
            "message": "Report submitted successfully",
            "report_id": report.id
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Submission failed: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def generate_email_content(form_data, attachments):
    return f"""
    <html>
    <body>
        <h2>REMS Repair Report</h2>
        <p>Container: {form_data.get('containernr')}</p>
        <p>Date: {form_data.get('datum')}</p>
        <p>Technician: {form_data.get('naam')}</p>
        <h3>Problem Description</h3>
        <p>{form_data.get('probleem') or 'N/A'}</p>
        <h3>Resolution</h3>
        <p>{form_data.get('opmerkingen') or 'N/A'}</p>
    </body>
    </html>
    """

def send_email(subject, body, attachments):
    try:
        msg = MIMEMultipart()
        msg['From'] = os.getenv('EMAIL_FROM')
        msg['To'] = os.getenv('EMAIL_TO')
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        for filepath in attachments:
            with open(filepath, 'rb') as f:
                if filepath.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    img = MIMEImage(f.read())
                    img.add_header('Content-Disposition', 'attachment', filename=os.path.basename(filepath))
                    msg.attach(img)
                else:
                    part = MIMEApplication(f.read(), Name=os.path.basename(filepath))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(filepath)}"'
                    msg.attach(part)

        with smtplib.SMTP_SSL(os.getenv('SMTP_SERVER'), int(os.getenv('SMTP_PORT'))) as server:
            server.login(os.getenv('SMTP_USERNAME'), os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)
    except Exception as e:
        app.logger.error(f"Email error: {str(e)}")
        raise

# ======================
# Start Application
# ======================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
