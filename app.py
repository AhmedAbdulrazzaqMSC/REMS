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

# Initialize Flask app
app = Flask(__name__, static_folder='static')
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
        db.session.execute(text("SELECT 1"))
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
        db.session.flush()  # Get the report ID
        
        # Process jobs
        job_count = int(form_data.get('job_count', 0))
        for i in range(job_count):
            job_data = {
                'code': form_data.get(f'job[{i}][code]'),
                'description': form_data.get(f'job[{i}][description]'),
                'part_number': form_data.get(f'job[{i}][part_number]'),
                'part_description': form_data.get(f'job[{i}][part_description]'),
                'quantity': form_data.get(f'job[{i}][quantity]'),
                'damage_type': form_data.get(f'job[{i}][damage_type]'),
                'old_serial': form_data.get(f'job[{i}][old_serial]'),
                'new_serial': form_data.get(f'job[{i}][new_serial]'),
                'labor_hours': form_data.get(f'job[{i}][labor_hours]')
            }
            job = RepairJob(
                report_id=report.id,
                job_code=job_data['code'],
                description=job_data['description'],
                part_number=job_data['part_number'],
                part_description=job_data['part_description'],
                quantity=int(job_data['quantity'] or 1),
                damage_type=job_data['damage_type'],
                old_serial=job_data['old_serial'],
                new_serial=job_data['new_serial'],
                labor_hours=float(job_data['labor_hours'] or 0)
            )
            db.session.add(job)
        
        # Process alarms
        for alarm in request.form.getlist('alarm[]'):
            if alarm.strip():
                db.session.add(Alarm(
                    report_id=report.id,
                    alarm_code=alarm.strip()
                ))
        
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
                subject=container_nr,
                body=generate_email_content(form_data, request.form.getlist('alarm[]'), job_count),
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

    except ValueError as e:
        db.session.rollback()
        app.logger.error(f"Data validation failed: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Submission failed: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def generate_email_content(form_data, alarms, job_count):
    # Count photos (assuming files are named with 'voor'/'na')
    photos_voor = len([1 for key in form_data.keys() if 'fotos_voor' in key.lower()])
    photos_na = len([1 for key in form_data.keys() if 'fotos_na' in key.lower()])
    
    # Generate jobs HTML
    jobs_html = ""
    for i in range(job_count):
        jobs_html += f"""
        <div style="margin-bottom: 15px; padding: 10px; background: #f5f5f5; border-radius: 5px;">
            <h4 style="margin-top: 0;">Job {i+1}: {form_data.get(f'job[{i}][description]', 'N/A')}</h4>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 5px; width: 150px;"><strong>Code:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][code]', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Part Number:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][part_number]', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Part Description:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][part_description]', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Quantity:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][quantity]', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Damage Type:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][damage_type]', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Old Serial:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][old_serial]', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>New Serial:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][new_serial]', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Labor Hours:</strong></td>
                    <td style="padding: 5px;">{form_data.get(f'job[{i}][labor_hours]', 'N/A')}</td>
                </tr>
            </table>
        </div>
        """
    
    # Generate alarms HTML
    alarms_html = "<ul>" + "".join([f"<li>{alarm}</li>" for alarm in alarms if alarm.strip()]) + "</ul>"
    
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto;">
        <div style="background-color: #0066cc; padding: 20px; color: white;">
            <h1 style="margin: 0;">REMS Repair Report</h1>
        </div>
        
        <div style="padding: 20px;">
            <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 5px;">Container Information</h2>
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 8px; width: 200px;"><strong>Container Number:</strong></td>
                    <td style="padding: 8px;">{form_data.get('containernr', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Date:</strong></td>
                    <td style="padding: 8px;">{form_data.get('datum', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Technician:</strong></td>
                    <td style="padding: 8px;">{form_data.get('naam', 'N/A')}</td>
                </tr>
            </table>
            
            <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 5px;">Unit Details</h2>
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 8px; width: 200px;"><strong>Model:</strong></td>
                    <td style="padding: 8px;">{form_data.get('model', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Serial Number:</strong></td>
                    <td style="padding: 8px;">{form_data.get('serienr', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Warranty ID:</strong></td>
                    <td style="padding: 8px;">{form_data.get('warranty_id', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Warranty Status:</strong></td>
                    <td style="padding: 8px;">{form_data.get('garantie', 'N/A')}</td>
                </tr>
            </table>
            
            <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 5px;">Technical Data</h2>
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 8px; width: 200px;"><strong>Setpoint:</strong></td>
                    <td style="padding: 8px;">{form_data.get('setpoint', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Vents:</strong></td>
                    <td style="padding: 8px;">{form_data.get('vents', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Humidity:</strong></td>
                    <td style="padding: 8px;">{form_data.get('hum', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Ambient Temp:</strong></td>
                    <td style="padding: 8px;">{form_data.get('ambient', 'N/A')}°C</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Supply Temp Before:</strong></td>
                    <td style="padding: 8px;">{form_data.get('supply_voor', 'N/A')}°C</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Supply Temp After:</strong></td>
                    <td style="padding: 8px;">{form_data.get('supply_na', 'N/A')}°C</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Return Temp Before:</strong></td>
                    <td style="padding: 8px;">{form_data.get('return_voor', 'N/A')}°C</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Return Temp After:</strong></td>
                    <td style="padding: 8px;">{form_data.get('return_na', 'N/A')}°C</td>
                </tr>
                <tr>
                    <td style="padding: 8px;"><strong>Temp in Range:</strong></td>
                    <td style="padding: 8px;">{form_data.get('temp_in_range', 'N/A')}</td>
                </tr>
            </table>
            
            <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 5px;">Problem & Resolution</h2>
            <div style="background: #f5f5f5; padding: 15px; margin-bottom: 20px; border-radius: 5px;">
                <h3 style="margin-top: 0;">Problem Description</h3>
                <p>{form_data.get('probleem', 'N/A')}</p>
                
                <h3>Comments/Resolution</h3>
                <p>{form_data.get('opmerkingen', 'N/A')}</p>
            </div>
            
            <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 5px;">Repair Jobs</h2>
            {jobs_html if jobs_html else "<p>No repair jobs recorded</p>"}
            
            <h2 style="color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 5px;">Alarms</h2>
            {alarms_html if alarms else "<p>No alarms recorded</p>"}
            
            
            <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #ddd; color: #666; font-size: 0.9em;">
                <p>This report was automatically generated by the REMS system.</p>
                <p>Report ID: {form_data.get('containernr', '')}-{datetime.now().strftime('%Y%m%d')}</p>
            </div>
        </div>
    </body>
    </html>
    """

def send_email(subject, body, attachments):
    try:
        # Email configuration
        SMTP_SERVER = 'smtp.gmail.com'
        SMTP_PORT = 587
        SMTP_USERNAME = 'emergencyrepairsmpet@gmail.com'
        SMTP_PASSWORD = 'gvwe limw yzya oejc'  # Move to environment variables in production!
        EMAIL_FROM = 'emergencyrepairsmpet@gmail.com'
        
        # Recipients (comma-separated string for headers, list for sending)
        EMAIL_TO = [
            'REEFER.1742@MPET.BE',
            'BE900-BE900-ForemanMedrepairMonitoring@medrepair.eu',
            'BE900-ReeferAdministrationMedrepair@medrepair.eu',
            'fouzi.elyazidi@medrepair.eu',
            'jroets@medrepair.eu',
            'gillis.keustermans@medrepair.eu',
            'a.abdulrazzaq@medrepair.eu'
        ]
        email_to_str = ', '.join(EMAIL_TO)  # For header

        # Prepare email
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = email_to_str  # Comma-separated string for header
        msg['Subject'] = f"Herstelmelding {subject} - {datetime.now().strftime('%d-%m-%Y')}"
        msg.attach(MIMEText(body, 'html'))

        # Attach files (your existing code works perfectly)
        for filepath in attachments:
            try:
                with open(filepath, 'rb') as f:
                    if filepath.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        img = MIMEImage(f.read())
                        img.add_header('Content-Disposition', 'attachment', 
                                     filename=os.path.basename(filepath))
                        msg.attach(img)
                    else:
                        part = MIMEApplication(f.read(), 
                                             Name=os.path.basename(filepath))
                        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(filepath)}"'
                        msg.attach(part)
            except Exception as file_error:
                app.logger.error(f"Failed to attach {filepath}: {str(file_error)}")

        # Send email - CRITICAL FIX: Use sendmail() with recipient list
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())  # Use list of emails here
            app.logger.info(f"Email sent to: {EMAIL_TO}")

    except smtplib.SMTPAuthenticationError:
        app.logger.error("Email failed: SMTP authentication error (check username/password)")
        raise
    except smtplib.SMTPException as e:
        app.logger.error(f"Email failed: SMTP error - {str(e)}")
        raise
    except Exception as e:
        app.logger.error(f"Email failed: {str(e)}", exc_info=True)
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



