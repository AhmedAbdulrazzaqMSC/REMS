import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from datetime import datetime
import logging
from werkzeug.utils import secure_filename

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# ======================
# Configuration
# ======================

# Database Configuration
db_url = os.environ.get('DATABASE_URL', '')
if not db_url:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# Fix for Render's PostgreSQL URL
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File Uploads
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize database
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
    
    jobs = db.relationship('RepairJob', backref='report', cascade='all, delete-orphan')
    alarms = db.relationship('Alarm', backref='report', cascade='all, delete-orphan')

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
# Application Setup
# ======================

@app.before_first_request
def initialize_database():
    try:
        # Create tables if they don't exist
        db.create_all()
        # Test connection
        db.session.execute("SELECT 1")
        app.logger.info("Database connection successful")
    except Exception as e:
        app.logger.error(f"Database initialization failed: {str(e)}")
        raise

# ======================
# Routes
# ======================

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

@app.route('/api/submit', methods=['POST'])
def submit_report():
    try:
        form_data = request.form
        files = request.files
        
        # Validate container number (AABB1234567 format)
        container_nr = form_data.get('containernr', '')
        if not (len(container_nr) == 11 and container_nr[:4].isalpha() and container_nr[4:].isdigit()):
            return jsonify({"status": "error", "message": "Container number must be 4 letters followed by 7 digits"}), 400

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
            prefix = f'job[{i}]'
            job = RepairJob(
                report=report,
                job_code=form_data.get(f'{prefix}[code]'),
                description=form_data.get(f'{prefix}[description]'),
                part_number=form_data.get(f'{prefix}[part_number]'),
                part_description=form_data.get(f'{prefix}[part_description]'),
                quantity=int(form_data.get(f'{prefix}[quantity]', 1)),
                damage_type=form_data.get(f'{prefix}[damage_type]'),
                old_serial=form_data.get(f'{prefix}[old_serial]'),
                new_serial=form_data.get(f'{prefix}[new_serial]'),
                labor_hours=float(form_data.get(f'{prefix}[labor_hours]', 0))
            )
            db.session.add(job)
        
        # Process alarms
        for alarm in request.form.getlist('alarm[]'):
            if alarm.strip():
                db.session.add(Alarm(report=report, alarm_code=alarm.strip()))
        
        # Process file uploads
        saved_files = []
        for file_key, file in files.items():
            if file.filename == '':
                continue
                
            if file and allowed_file(file.filename):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                original_name = secure_filename(file.filename)
                safe_name = f"{timestamp}_{original_name}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
                file.save(file_path)
                saved_files.append({
                    'path': file_path,
                    'original_name': original_name,
                    'type': 'before' if 'voor' in file_key.lower() else 'after'
                })
        
        # Send email
        email_content = generate_email_content(form_data, saved_files)
        send_email(
            subject=f"REMS Report - {container_nr}",
            body=email_content,
            attachments=saved_files
        )
        
        # Cleanup files
        for file_info in saved_files:
            try:
                os.remove(file_info['path'])
            except Exception as e:
                app.logger.error(f"Error deleting file {file_info['path']}: {str(e)}")
        
        db.session.commit()
        return jsonify({
            "status": "success", 
            "message": "Report submitted successfully",
            "report_id": report.id
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Report submission failed: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Failed to submit report: {str(e)}"
        }), 500

# ======================
# Helper Functions
# ======================

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def generate_email_content(form_data, attachments):
    before_photos = len([f for f in attachments if f['type'] == 'before'])
    after_photos = len([f for f in attachments if f['type'] == 'after'])
    
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2>REMS Repair Report</h2>
        <p><strong>Container:</strong> {form_data.get('containernr')}</p>
        <p><strong>Date:</strong> {form_data.get('datum')}</p>
        <p><strong>Technician:</strong> {form_data.get('naam')}</p>
        <p><strong>Photos:</strong> {before_photos} before, {after_photos} after</p>
        
        <h3>Problem Description</h3>
        <p>{form_data.get('probleem') or 'N/A'}</p>
        
        <h3>Resolution</h3>
        <p>{form_data.get('opmerkingen') or 'N/A'}</p>
        
        <p style="color: #666; font-size: 0.9em;">
            This report was automatically generated by the REMS system.
        </p>
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
        
        for file_info in attachments:
            with open(file_info['path'], 'rb') as f:
                if file_info['path'].lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    img = MIMEImage(f.read())
                    img.add_header('Content-Disposition', 'attachment', filename=file_info['original_name'])
                    msg.attach(img)
                else:
                    part = MIMEApplication(f.read(), Name=file_info['original_name'])
                    part['Content-Disposition'] = f'attachment; filename="{file_info["original_name"]}"'
                    msg.attach(part)
        
        with smtplib.SMTP_SSL(os.getenv('SMTP_SERVER'), int(os.getenv('SMTP_PORT'))) as server:
            server.login(os.getenv('SMTP_USERNAME'), os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)
            
    except Exception as e:
        app.logger.error(f"Email sending failed: {str(e)}")
        raise

# ======================
# Entry Point
# ======================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
