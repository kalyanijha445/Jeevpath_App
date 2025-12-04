import os
import re
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
import google.generativeai as genai
from PIL import Image, ImageDraw
from werkzeug.utils import secure_filename
from datetime import datetime
from fpdf import FPDF # Ensure 'pip install fpdf' is run

# --- CONFIGURATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'healthcare_secret_key_999' # Strong secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['UPLOAD_FOLDER'] = 'uploads'

# Create uploads folder if not exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- GEMINI API CONFIGURATION ---
genai.configure(api_key="AIzaSyCEr3AD4oaN2yIr_OOtdBm1ue5pFAMKpJg") 
model = genai.GenerativeModel('gemini-2.0-flash')

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(10), default='patient') 
    name = db.Column(db.String(100))
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    
    # Patient Data
    age = db.Column(db.Integer, nullable=True)
    gender = db.Column(db.String(20), nullable=True)
    diet = db.Column(db.String(20), nullable=True) 
    
    # Doctor Data
    department = db.Column(db.String(50), nullable=True)
    experience = db.Column(db.Integer, nullable=True)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    patient_name = db.Column(db.String(100))
    patient_email = db.Column(db.String(100))
    symptoms_desc = db.Column(db.Text) 
    meeting_link = db.Column(db.String(500), nullable=True)
    date_booked = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="Pending")
    patient = db.relationship("User", foreign_keys=[patient_id])
    doctor = db.relationship("User", foreign_keys=[doctor_id])

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=True)
    media_path = db.Column(db.String(300), nullable=True)
    media_type = db.Column(db.String(20), nullable=True) 
    timestamp = db.Column(db.DateTime, default=datetime.now)
    sender = db.relationship("User", foreign_keys=[sender_id])

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    checkup_type = db.Column(db.String(50)) 
    ai_content = db.Column(db.Text)          
    timestamp = db.Column(db.DateTime, default=datetime.now)

class Donor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    blood_group = db.Column(db.String(5))
    contact = db.Column(db.String(20))
    address = db.Column(db.String(200))
    posted_by = db.Column(db.Integer, db.ForeignKey('user.id'))

class HealthVideo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(150))
    description = db.Column(db.Text)
    video_path = db.Column(db.String(300))
    thumbnail_path = db.Column(db.String(300))
    timestamp = db.Column(db.DateTime, default=datetime.now)
    doctor = db.relationship("User", foreign_keys=[doctor_id])

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- PDF HELPERS & DESIGN ---

def create_gradient_header(width, height, start_color, end_color, filename):
    img = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    r1, g1, b1 = start_color; r2, g2, b2 = end_color
    for i in range(height):
        r = int(r1 + (r2 - r1) * i / height)
        g = int(g1 + (g2 - g1) * i / height)
        b = int(b1 + (b2 - b1) * i / height)
        draw.line([(0, i), (width, i)], fill=(r, g, b))
    img.save(filename)
    return filename

def sanitize_text_for_pdf(text):
    if not text: return ""
    # Standardize fancy punctuation to simple ASCII for PDF
    text = text.replace('\u2013', '-').replace('\u2014', '--')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2022', chr(149)) # Use standard PDF bullet char
    # Remove unsupported HTML like bold tags that survived
    text = text.replace('<strong>', '').replace('</strong>', '')
    text = text.replace('<b>', '').replace('</b>', '')
    # Convert remaining Unicode to Latin-1 compatible, replace errors with '?'
    return text.encode('latin-1', 'replace').decode('latin-1')

# --- ROUTES ---

@app.route('/')
def root():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form.get('role')
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and user.password == password:
            if user.role != role:
                flash(f"Role mismatch. You are registered as a {user.role}.")
                return redirect(url_for('login'))
            login_user(user)
            if user.role == 'doctor':
                return redirect(url_for('doctor_dashboard'))
            return redirect(url_for('user_dashboard'))
        else:
            flash('Invalid credentials')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        role = request.form.get('role')
        name = request.form.get('name')
        email = request.form.get('email')
        pwd = request.form.get('password')
        pwd2 = request.form.get('confirm_password')
        if pwd != pwd2:
            flash("Passwords do not match")
            return redirect(url_for('signup'))
        if User.query.filter_by(email=email).first():
            flash("Email already exists")
            return redirect(url_for('signup'))
        new_user = User(name=name, email=email, password=pwd, role=role)
        if role == 'patient':
            new_user.age = request.form.get('age')
            new_user.gender = request.form.get('gender')
            new_user.diet = request.form.get('diet')
        else:
            new_user.department = request.form.get('department')
            new_user.experience = request.form.get('experience')
        db.session.add(new_user)
        db.session.commit()
        flash("Registration Successful! Please Login.")
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- MAIN DASHBOARDS ---

@app.route('/user_dashboard')
@login_required
def user_dashboard():
    if current_user.role != 'patient': return redirect(url_for('doctor_dashboard'))
    return render_template('user_dashboard.html', user=current_user)

@app.route('/doctor_dashboard')
@login_required
def doctor_dashboard():
    if current_user.role != 'doctor': return redirect(url_for('user_dashboard'))
    my_appointments = Appointment.query.filter_by(doctor_id=current_user.id).order_by(Appointment.date_booked.desc()).all()
    return render_template('doctor_dashboard.html', user=current_user, appointments=my_appointments)

# --- CORE AI LOGIC (UPDATED FOR DETAILED RESULTS) ---

@app.route('/analyze_report', methods=['POST'])
@login_required
def analyze_report():
    
    # 1. HANDLE MULTIPLE FILES
    uploaded_files = request.files.getlist('report_gallery')
    camera_file = request.files.get('report_camera')
    
    image_objects = []

    # Process Gallery Files
    if uploaded_files and uploaded_files[0].filename != '':
        for file in uploaded_files:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            image_objects.append(Image.open(filepath))
            
    # Process Camera File
    if camera_file and camera_file.filename != '':
        filename = secure_filename(camera_file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        camera_file.save(filepath)
        image_objects.append(Image.open(filepath))
        
    if not image_objects:
        flash("Please upload at least one image (X-ray, Report, Symptom, etc).")
        return redirect(url_for('user_dashboard'))

    # Gather Data
    checkup_type = request.form.get('checkup_type') or "General Checkup"
    name = current_user.name 
    age = request.form.get('age')
    gender = request.form.get('gender')
    diet = request.form.get('diet')
    symptoms = request.form.get('description')
    selected_language = request.form.get('language') or 'en'
    
    height = request.form.get('height') or "N/A"
    weight = request.form.get('weight') or "N/A"

    specific_notes = ""
    if checkup_type == "General Checkup":
        bp = request.form.get('bp')
        sugar = request.form.get('sugar')
        thyroid = request.form.get('thyroid')
        temp = request.form.get('temp')
        heart_rate = request.form.get('heart_rate')
        specific_notes = f"BP: {bp}, Sugar: {sugar}, Thyroid: {thyroid}, Body Temp: {temp} F, Heart Rate: {heart_rate} bpm."
    elif checkup_type == "Dengue Fever":
        fever_days = request.form.get('fever_days')
        platelets = request.form.get('platelets')
        pain_level = request.form.get('pain_level')
        specific_notes = f"Fever: {fever_days} days. Platelets: {platelets}. Body Pain: {pain_level}."
    elif checkup_type == "Malaria":
        shivering = request.form.get('shivering')
        fever_pattern = request.form.get('fever_pattern')
        travel_hist = request.form.get('travel_hist')
        specific_notes = f"Shivering: {shivering}. Pattern: {fever_pattern}. Travel: {travel_hist}."
    elif checkup_type == "Typhoid":
        stomach_pain = request.form.get('stomach_pain')
        appetite = request.form.get('appetite')
        specific_notes = f"Stomach Pain: {stomach_pain}. Appetite: {appetite}."

    # DETAILED MEDICAL PROMPT
    prompt = f"""
    Act as a highly experienced Senior General Physician (MD), Radiologist, and Nutritionist. 
    Analyze the uploaded medical images (X-rays, blood reports, visible symptoms, etc.) combined with patient data.
    
    LANGUAGE MODE: Output exactly in language code: '{selected_language}'.
    PATIENT: {name}, {age}yrs, {gender}. 
    VITALS: {specific_notes}. Height:{height}, Weight:{weight}.
    SYMPTOMS: {symptoms}.
    DIET TYPE: {diet}.

    TASK:
    1. READ VALUES: Extract numbers, ranges, and visual anomalies from the images meticulously.
    2. ANALYZE: Correlate vitals, image findings, and symptoms to form a hypothesis.
    3. ADVISE: Provide Indian-context medical advice, medicines (Safe OTC), and specific diet changes.

    FORMAT YOUR RESPONSE STRICTLY USING THE FOLLOWING HTML TAGS ONLY (Do not use Markdown like ** or ##).
    The PDF generator depends on these specific tags to function.

    <h3>1. DETAILED CLINICAL OBSERVATION</h3>
    <p>Describe exactly what is visible in the images and abnormal in vitals.</p>
    <ul>
      <li>Point 1: Detailed observation from image/data.</li>
      <li>Point 2: Correlation with reported symptoms.</li>
    </ul>

    <h3>2. MEDICAL DIAGNOSIS & EXPLANATION</h3>
    <p>Explain the potential condition clearly. Be reassuring but realistic.</p>

    <h3>3. TREATMENT & MEDICATION (INDIAN CONTEXT)</h3>
    <p>Step-by-step path to recovery.</p>
    <ul>
       <li><strong>Medicines:</strong> Suggest OTC options (like Dolo-650, Cetrizine, ORS, etc.) with dosage hints if safe.</li>
       <li><strong>Home Remedy:</strong> Effective Indian household remedies.</li>
       <li><strong>Alert:</strong> When to see a doctor immediately.</li>
    </ul>

    <h3>4. PRECISE DIET PLAN ({diet})</h3>
    <p>Foods to eat and foods to strictly avoid for this specific condition.</p>
    <ul>
       <li><strong>Eat:</strong> Specific ingredients tailored to the disease (e.g. Papaya leaf for Dengue).</li>
       <li><strong>Avoid:</strong> Specific triggers.</li>
    </ul>
    """

    try:
        inputs = [prompt]
        inputs.extend(image_objects)
        
        response = model.generate_content(inputs)
        result = response.text
        # Cleanup
        result = result.replace("```html", "").replace("```", "").replace("*", "")
        
        new_report = Report(
            user_id=current_user.id,
            checkup_type=checkup_type,
            ai_content=result,
            timestamp=datetime.now()
        )
        db.session.add(new_report)
        db.session.commit()
        
    except Exception as e:
        result = f"<h3>Error in Analysis</h3><p>Could not process images. Error: {str(e)}</p>"
        new_report = Report(id=0)

    return render_template('user_dashboard.html', 
                           result=result, 
                           user=current_user, 
                           selected_lang=selected_language, 
                           report_id=new_report.id if hasattr(new_report, 'id') else 0)

# --- ADVANCED PDF GENERATOR (JEEVPATH THEME) ---

@app.route('/download_pdf/<int:report_id>')
@login_required
def download_pdf(report_id):
    report = Report.query.get_or_404(report_id)
    if report.user_id != current_user.id: return "Unauthorized", 403

    # -- THEME SETTINGS (JeevPath Emerald) --
    COLOR_BG_HEADER = (236, 253, 245) # #ECFDF5
    COLOR_ACCENT = (16, 185, 129)     # #10B981
    COLOR_TEXT_MAIN = (55, 65, 81)    # #374151
    
    class PDF(FPDF):
        def footer(self):
            self.set_y(-15)
            self.set_font('Arial', 'I', 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 10, f'JeevPath AI Diagnostics | Report #{report_id} | Page {self.page_no()}', 0, 0, 'C')

    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # 1. GENERATE HEADER IMAGE (Gradient Green)
    header_path = os.path.join(app.config["UPLOAD_FOLDER"], "jp_gradient_head.png")
    if not os.path.exists(header_path):
        create_gradient_header(210, 30, (255,255,255), COLOR_BG_HEADER, header_path)
    
    # Draw Header
    pdf.image(header_path, 0, 0, 210, 30)
    
    pdf.set_y(8)
    pdf.set_font("Arial", 'B', 18)
    pdf.set_text_color(*COLOR_ACCENT)
    pdf.cell(10) # Indent
    pdf.cell(0, 10, "JEEVPATH LABS", ln=True)
    pdf.set_font("Arial", 'I', 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(10)
    pdf.cell(0, 5, "Advanced AI Medical Assessment Report", ln=True)
    
    pdf.ln(12)

    # 2. PATIENT DETAILS GRID
    pdf.set_fill_color(250, 250, 250)
    pdf.rect(10, pdf.get_y(), 190, 30, 'F')
    
    base_y = pdf.get_y() + 5
    
    def print_field(lbl, val, x, y):
        pdf.set_xy(x, y)
        pdf.set_font("Arial", 'B', 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(30, 5, lbl)
        pdf.set_font("Arial", '', 10)
        pdf.set_text_color(*COLOR_TEXT_MAIN)
        pdf.cell(0, 5, sanitize_text_for_pdf(str(val)))

    print_field("Patient:", current_user.name, 15, base_y)
    print_field("Age/Sex:", f"{current_user.age} / {current_user.gender}", 15, base_y+8)
    print_field("ID:", f"JP-{current_user.id}", 15, base_y+16)
    
    print_field("Date:", report.timestamp.strftime('%d %B %Y'), 110, base_y)
    print_field("Category:", report.checkup_type, 110, base_y+8)
   

    pdf.set_y(base_y + 35)

    # 3. ROBUST CONTENT PARSING
    # We treat the entire HTML content as a stream and break it by tags to handle formatting correctly.
    
    raw_content = report.ai_content
    # Normalize newline chars
    raw_content = raw_content.replace('\n', ' ')
    
    # Find all headings (h3) and content chunks
    # Regex Breakdown: Match <h3>(Title)</h3> then capture everything until next <h3> or end of string
    sections = re.findall(r'<h3>(.*?)</h3>(.*?)(?=<h3>|$)', raw_content, re.IGNORECASE | re.DOTALL)
    
    if not sections:
        # Fallback if AI didn't format strict H3s (e.g. failed instruction)
        # Just dump sanitized text wrapped.
        pdf.set_font("Arial", '', 11)
        pdf.multi_cell(0, 6, sanitize_text_for_pdf(re.sub('<[^<]+?>', '', raw_content)))
    else:
        for title, body in sections:
            # SECTION TITLE (Green Chip)
            title = sanitize_text_for_pdf(title.strip())
            
            # Check page break
            if pdf.get_y() > 250: pdf.add_page()
            
            pdf.ln(5)
            pdf.set_fill_color(*COLOR_ACCENT) 
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Arial", 'B', 11)
            # Dynamic width based on title
            w = pdf.get_string_width(title) + 10
            pdf.cell(w, 8, title, 0, 1, 'C', fill=True)
            pdf.ln(3)
            
            # SECTION BODY parsing
            # Split body into Paragraphs <p> and Lists <ul>
            body_parts = re.split(r'(<ul>.*?</ul>|<p>.*?</p>)', body, flags=re.IGNORECASE | re.DOTALL)
            
            for part in body_parts:
                part = part.strip()
                if not part: continue
                
                # Check page break inside content
                if pdf.get_y() > 260: pdf.add_page()

                # Paragraphs
                if part.startswith('<p'):
                    text = re.sub(r'<[^<]+?>', '', part).strip() # Remove tags
                    text = sanitize_text_for_pdf(text)
                    pdf.set_font("Arial", '', 10)
                    pdf.set_text_color(*COLOR_TEXT_MAIN)
                    pdf.multi_cell(0, 6, text)
                    pdf.ln(2)
                
                # Unordered Lists
                elif part.startswith('<ul'):
                    # Find all <li> items
                    list_items = re.findall(r'<li>(.*?)</li>', part, re.IGNORECASE | re.DOTALL)
                    for item in list_items:
                        item_text = re.sub(r'<[^<]+?>', '', item).strip() # Clean inner tags like <strong>
                        item_text = sanitize_text_for_pdf(item_text)
                        
                        pdf.set_x(15) # Indent for bullet
                        pdf.set_text_color(*COLOR_TEXT_MAIN)
                        pdf.set_font("Arial", '', 10)
                        
                        # Draw Bullet
                        pdf.cell(5, 6, chr(149), 0, 0)
                        pdf.multi_cell(0, 6, item_text)
                    pdf.ln(2)

    # 4. MEDICAL DISCLAIMER FOOTER (On every last page)
    pdf.ln(10)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Arial", 'I', 8)
    pdf.set_text_color(128, 128, 128)
    disclaimer = "DISCLAIMER: This report is generated by Artificial Intelligence (AI) and does not constitute a definitive medical diagnosis. Values extracted from images may contain errors. Please verify this report with a certified clinical physician."
    pdf.multi_cell(0, 4, disclaimer, 0, 'C')

    filename = f"JeevPath_Analysis_{report_id}.pdf"
    
    # Return as Stream
    from flask import Response
    return Response(pdf.output(dest='S').encode('latin-1'), mimetype='application/pdf', headers={'Content-Disposition': f'attachment;filename={filename}'})

# --- REPORT HISTORY & VIEW ---

@app.route('/reports')
@login_required
def reports():
    user_reports = Report.query.filter_by(user_id=current_user.id).order_by(Report.timestamp.desc()).all()
    # FIX: Pass the current_user object to the template context
    return render_template('reports.html', reports=user_reports, user=current_user)

@app.route('/view_report/<int:report_id>')
@login_required
def view_report(report_id):
    report = Report.query.get_or_404(report_id)
    if report.user_id != current_user.id:
        return "Unauthorized Access", 403
    return render_template('user_dashboard.html', result=report.ai_content, user=current_user)

# --- CONSULT & CHAT ROUTES ---

@app.route('/consult')
@login_required
def consult():
    doctors = User.query.filter_by(role='doctor').all()
    my_appts = Appointment.query.filter_by(patient_id=current_user.id).order_by(Appointment.date_booked.desc()).all()
    return render_template('consult.html', doctors=doctors, my_appts=my_appts, user=current_user)

@app.route('/book_appointment', methods=['POST'])
@login_required
def book_appointment():
    doctor_id = request.form.get('doctor_id')
    symptoms = request.form.get('symptoms')
    if not doctor_id: return redirect(url_for('consult'))

    appt = Appointment(patient_id=current_user.id, patient_name=current_user.name, patient_email=current_user.email,
                       doctor_id=doctor_id, symptoms_desc=symptoms, status="Pending")
    db.session.add(appt)
    db.session.commit()
    flash("Request Sent!")
    return redirect(url_for('consult'))

@app.route('/update_status/<int:appt_id>/<string:new_status>')
@login_required
def update_status(appt_id, new_status):
    appt = Appointment.query.get(appt_id)
    if appt and appt.doctor_id == current_user.id:
        appt.status = new_status
        db.session.commit()
    return redirect(url_for('doctor_dashboard'))

@app.route('/start_meeting', methods=['POST'])
@login_required
def start_meeting():
    appt_id = request.form.get('appt_id')
    meeting_link = request.form.get('meeting_link')
    appt = Appointment.query.get(appt_id)
    if appt and appt.doctor_id == current_user.id:
        appt.meeting_link = meeting_link
        appt.status = "Confirmed"
        db.session.commit()
    return redirect(url_for('doctor_dashboard'))

# --- REAL-TIME CHAT SYSTEM ---

@app.route('/chat')
@login_required
def chat():
    contacts = []
    if current_user.role == 'patient':
        users = User.query.filter_by(role='doctor').all()
        for u in users:
            contacts.append({'id': u.id, 'name': 'Dr. ' + u.name, 'subtitle': u.department})
    else:
        users = User.query.filter_by(role='patient').all()
        for u in users:
            contacts.append({'id': u.id, 'name': u.name, 'subtitle': 'Patient'})
    return render_template('chat.html', user=current_user, contacts=contacts)

@app.route('/api/get_messages/<int:contact_id>')
@login_required
def get_messages(contact_id):
    messages = Message.query.filter(
        ((Message.sender_id == current_user.id) & (Message.receiver_id == contact_id)) |
        ((Message.sender_id == contact_id) & (Message.receiver_id == current_user.id))
    ).order_by(Message.timestamp.asc()).all()

    msg_list = []
    for m in messages:
        msg_list.append({
            'sender_id': m.sender_id,
            'content': m.content,
            'media_path': m.media_path,
            'media_type': m.media_type,
            'time': m.timestamp.strftime('%d-%b %I:%M %p')
        })
    return jsonify(msg_list)

@app.route('/api/send_message', methods=['POST'])
@login_required
def send_message():
    receiver_id = request.form.get('receiver_id')
    content = request.form.get('content')
    file = request.files.get('file')

    media_path = None
    media_type = None

    if file:
        filename = secure_filename(file.filename)
        timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{timestamp_str}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        media_path = filename 
        
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in ['jpg', 'jpeg', 'png', 'gif']: media_type = 'image'
        elif ext in ['mp4', 'mov', 'avi']: media_type = 'video'
        else: media_type = 'file'

    new_msg = Message(
        sender_id=current_user.id, receiver_id=receiver_id,
        content=content if content else "",
        media_path=media_path, media_type=media_type,
        timestamp=datetime.now()
    )
    db.session.add(new_msg)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- BLOOD BANK ROUTES ---

@app.route('/blood_bank')
@login_required
def blood_bank():
    donors = Donor.query.all()
    return render_template('blood_bank.html', donors=donors, user=current_user)

@app.route('/register_donor', methods=['POST'])
@login_required
def register_donor():
    name = request.form.get('name')
    blood_group = request.form.get('blood_group')
    contact = request.form.get('contact')
    address = request.form.get('address')
    
    new_donor = Donor(
        name=name,
        blood_group=blood_group,
        contact=contact,
        address=address,
        posted_by=current_user.id
    )
    db.session.add(new_donor)
    db.session.commit()
    
    flash("Registered as Donor Successfully!")
    return redirect(url_for('blood_bank'))

# --- VIDEO ROUTES ---

@app.route('/upload_video', methods=['POST'])
@login_required
def upload_video():
    if current_user.role != 'doctor':
        return redirect(url_for('user_dashboard'))
    
    title = request.form.get('title')
    desc = request.form.get('description')
    
    video_file = request.files.get('video_file')
    thumb_file = request.files.get('thumb_file')
    
    if video_file and thumb_file:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        v_name = secure_filename(video_file.filename)
        v_final_name = f"VID_{ts}_{v_name}"
        video_file.save(os.path.join(app.config['UPLOAD_FOLDER'], v_final_name))
        
        t_name = secure_filename(thumb_file.filename)
        t_final_name = f"THUMB_{ts}_{t_name}"
        thumb_file.save(os.path.join(app.config['UPLOAD_FOLDER'], t_final_name))
        
        new_video = HealthVideo(
            doctor_id=current_user.id,
            title=title,
            description=desc,
            video_path=v_final_name,
            thumbnail_path=t_final_name,
            timestamp=datetime.now()
        )
        db.session.add(new_video)
        db.session.commit()
        flash("Video Uploaded Successfully!")
        
    return redirect(url_for('doctor_dashboard'))

@app.route('/videos')
@login_required
def videos():
    all_videos = HealthVideo.query.order_by(HealthVideo.timestamp.desc()).all()
    return render_template('videos.html', videos=all_videos, user=current_user)

# --- STARTUP FIX FOR RENDER ---
# This ensures tables are created even when started via Gunicorn
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
