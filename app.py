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

# Initialize Flask app with enhanced configuration
app = Flask(__name__, static_folder=None)  # Disable default static file handling
CORS(app, resources={
    r"/api/*": {"origins": "*"},  # Allow all origins for API routes
    r"/static/*": {"origins": "*"}  # Allow static files
})

# ======================
# Enhanced Configuration
# ======================

# Database Configuration with connection pooling
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
    'MAX_CONTENT_LENGTH': 16 * 1024 * 1024,  # 16MB
    'UPLOAD_FOLDER': os.path.join(os.getcwd(), 'uploads')
})

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize database with improved settings
db = SQLAlchemy(app)

# ======================
# Database Models (Optimized)
# ======================

class RepairReport(db.Model):
    __tablename__ = 'repair_reports'
    id = db.Column(db.Integer, primary_key=True)
    # [Previous columns remain the same...]
    
    # Indexes for performance
    __table_args__ = (
        db.Index('ix_container_number', 'container_number'),
        db.Index('ix_report_date', 'report_date'),
    )

# [Other model definitions remain the same...]

# ======================
# Application Setup with Error Handling
# ======================

@app.before_first_request
def initialize_database():
    try:
        with app.app_context():
            db.create_all()
            db.session.execute("SELECT 1")  # Test connection
            app.logger.info("Database initialized successfully")
    except Exception as e:
        app.logger.critical(f"Database initialization failed: {str(e)}")
        raise

# ======================
# Enhanced Routes
# ======================

@app.route('/')
def serve_index():
    """Serve frontend entry point with cache control"""
    return send_from_directory('.', 'index.html', cache_timeout=0)

@app.route('/static/<path:path>')
def serve_static(path):
    """Serve static files with proper caching headers"""
    response = send_from_directory('.', path)
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

@app.route('/api/submit', methods=['POST'])
def submit_report():
    """Enhanced submission endpoint with transaction management"""
    try:
        # Validate content type
        if not request.is_json and not request.form:
            return jsonify({"status": "error", "message": "Unsupported content type"}), 415

        with db.session.begin_nested():  # Use nested transaction
            form_data = request.form if request.form else request.get_json()
            files = request.files
            
            # [Previous validation and processing logic...]
            
            # Process data
            report = process_report_data(form_data)
            process_jobs(report, form_data)
            process_alarms(report, form_data)
            
            # Handle file uploads
            saved_files = handle_file_uploads(files)
            
            # Send email (async in production)
            if os.environ.get('FLASK_ENV') != 'testing':
                send_report_email(report, saved_files)
            
            db.session.commit()
            
            return jsonify({
                "status": "success",
                "message": "Report submitted successfully",
                "report_id": report.id
            })

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Report submission failed: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

# ======================
# Refactored Helper Functions
# ======================

def process_report_data(form_data):
    """Create and validate report entity"""
    # [Validation and creation logic...]
    return report

def handle_file_uploads(files):
    """Process uploaded files with cleanup guarantee"""
    saved_files = []
    try:
        # [File processing logic...]
        return saved_files
    except Exception as e:
        # Cleanup any partially saved files
        for file_info in saved_files:
            try:
                os.remove(file_info['path'])
            except:
                pass
        raise

def send_report_email(report, attachments):
    """Send email with error handling"""
    try:
        # [Email sending logic...]
    except Exception as e:
        app.logger.error(f"Email sending failed (report {report.id}): {str(e)}")
        # Continue without failing the request

# ======================
# Production-Ready Entry Point
# ======================

if __name__ == '__main__':
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Start application
    port = int(os.environ.get('PORT', 5000))
    app.run(
        host='0.0.0.0',
        port=port,
        threaded=True,
        debug=os.environ.get('FLASK_ENV') == 'development'
    )
