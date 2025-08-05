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

app = Flask(__name__, static_folder=None)
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/static/*": {"origins": "*"}
})

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
    'MAX_CONTENT_LENGTH': 16 * 1024 * 1024
})

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db = SQLAlchemy(app)

# Models
class RepairReport(db.Model):
    __tablename__ = 'repair_reports'
    id = db.Column(db.Integer, primary_key=True)
    # ... (other columns remain the same)
    jobs = db.relationship('RepairJob', backref='report', cascade='all, delete-orphan')
    alarms = db.relationship('Alarm', backref='report', cascade='all, delete-orphan')

class RepairJob(db.Model):
    __tablename__ = 'repair_jobs'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('repair_reports.id'), nullable=False)
    # ... (other columns remain the same)

class Alarm(db.Model):
    __tablename__ = 'alarms'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('repair_reports.id'), nullable=False)
    alarm_code = db.Column(db.String(100))

# Initialize Database
with app.app_context():
    try:
        db.create_all()
        db.session.execute(text("SELECT 1"))
        app.logger.info("Database initialized successfully")
    except Exception as e:
        app.logger.critical(f"Database initialization failed: {str(e)}")
        raise

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
            # ... (other fields)
        )
        db.session.add(report)
        
        # Process jobs
        job_count = int(form_data.get('job_count', 0))
        for i in range(job_count):
            job_data = form_data.get(f'job[{i}]')
            if job_data:
                job = RepairJob(
                    report_id=report.id,
                    job_code=job_data.get('code'),
                    # ... (other fields)
                )
                db.session.add(job)
        
        # Process alarms - FIXED
        for alarm in request.form.getlist('alarm[]'):
            if alarm.strip():
                alarm_entry = Alarm(
                    report_id=report.id,
                    alarm_code=alarm.strip()
                )
                db.session.add(alarm_entry)
        
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

# ... (rest of helper functions remain the same)

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
