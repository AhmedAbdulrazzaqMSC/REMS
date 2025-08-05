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
app = Flask(__name__, static_folder=None)
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/static/*": {"origins": "*"}
})

# ======================
# Configuration
# ======================

# Database Configuration
db_url = os.environ.get('DATABASE_URL', '')
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
    # ... (other columns remain unchanged)

class RepairJob(db.Model):
    __tablename__ = 'repair_jobs'
    id = db.Column(db.Integer, primary_key=True)
    # ... (other columns remain unchanged)

class Alarm(db.Model):
    __tablename__ = 'alarms'
    id = db.Column(db.Integer, primary_key=True)
    # ... (other columns remain unchanged)

# ======================
# Routes
# ======================

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                             'favicon.ico', 
                             mimetype='image/vnd.microsoft.icon')

@app.route('/static/<path:path>')
def serve_static(path):
    response = send_from_directory('.', path)
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

@app.route('/api/submit', methods=['POST'])
@app.route('/submit', methods=['POST'])  # Backwards compatibility
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

        # Process report data
        report = RepairReport(
            container_number=container_nr,
            report_date=datetime.strptime(form_data.get('datum'), '%Y-%m-%d').date(),
            # ... (other fields)
        )
        db.session.add(report)
        
        # Process jobs and alarms
        # ... (your existing logic)

        # Process file uploads
        saved_files = []
        for file_key, file in files.items():
            if file.filename == '':
                continue
                
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
            "message": "Report submitted",
            "report_id": report.id
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Submission failed: {str(e)}")
        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

# ======================
# Helper Functions
# ======================

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

def generate_email_content(form_data, attachments):
    # ... (your existing email template logic)

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
                    img.add_header('Content-Disposition', 'attachment', 
                                 filename=os.path.basename(filepath))
                    msg.attach(img)
                else:
                    part = MIMEApplication(f.read(), 
                                         Name=os.path.basename(filepath))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(filepath)}"'
                    msg.attach(part)
        
        with smtplib.SMTP_SSL(os.getenv('SMTP_SERVER'), int(os.getenv('SMTP_PORT'))) as server:
            server.login(os.getenv('SMTP_USERNAME'), os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)
            
    except Exception as e:
        app.logger.error(f"Email error: {str(e)}")
        raise

# ======================
# Startup
# ======================

@app.before_first_request
def initialize_database():
    try:
        db.create_all()
        db.session.execute("SELECT 1")
        app.logger.info("Database initialized")
    except Exception as e:
        app.logger.critical(f"Database failed: {str(e)}")
        raise

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
