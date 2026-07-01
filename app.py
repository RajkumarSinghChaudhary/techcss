import os
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import razorpay

# Establish absolute base directory pathing for SQLite persistence stability on cloud platforms
basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-change-this-in-production'
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'uploads')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Database Models ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    whatsapp = db.Column(db.String(20), nullable=False)
    license_no = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    bookings = db.relationship('Booking', backref='customer', lazy=True)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    whatsapp = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    license_no = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=False)
    scheduled_time = db.Column(db.String(50), nullable=False)
    screenshot_path = db.Column(db.String(200))
    payment_status = db.Column(db.String(20), default="Pending")
    razorpay_order_id = db.Column(db.String(100))
    remote_link = db.Column(db.String(200))        # Auto-generated Customer Join Link
    admin_remote_link = db.Column(db.String(200))  # Auto-generated Technician Console Link

# --- Third-Party Credentials Setup ---
RAZORPAY_KEY_ID = "rzp_test_T7dA8Pf6P3SNpQ"
RAZORPAY_KEY_SECRET = "60tDt8E9BYnpBby5DqUG6Daj"
client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# 🔑 Zoho API Configuration (Updated with your live tokens)
ZOHO_CLIENT_ID = "1000.LKNIHCDN6REVVR1NVFP2P463IRWW1O"
ZOHO_CLIENT_SECRET = "26a9c6022c79bb493b2b5eb10199d28a31392a3c04"
ZOHO_REFRESH_TOKEN = "1000.16d094a6501b51282399761df1a9405d.a165a740053e0ad20e84991515204544" 

# --- Helper: Dynamic Zoho Access Token Generator ---
def get_zoho_access_token():
    """Exchanges your long-lived Refresh Token for a temporary live Access Token via Zoho India."""
    url = "https://accounts.zoho.in/oauth/v2/token"  # 🌍 Kept as .in
    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }
    try:
        response = requests.post(url, params=params).json()
        return response.get("access_token")
    except Exception as e:
        print(f"Error fetching Zoho Access Token: {e}")
        return None

# --- Authentication Routes ---

@app.route('/register', methods=['GET', 'POST'], strict_slashes=False)
def register():
    next_page = request.args.get('next') or request.form.get('next')
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        whatsapp = request.form.get('whatsapp')
        license_no = request.form.get('license_no')
        
        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            flash('Email already registered!', 'error')
            return redirect(url_for('register', next=next_page))
            
        hashed_password = generate_password_hash(password, method='scrypt')
        new_user = User(email=email, password=hashed_password, name=name, whatsapp=whatsapp, license_no=license_no)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Account created successfully! Please log in.', 'success')
        if next_page:
            return redirect(url_for('login', next=next_page))
        return redirect(url_for('login'))
    return render_template('register.html', next=next_page)

@app.route('/login', methods=['GET', 'POST'], strict_slashes=False)
def login():
    next_page = request.args.get('next') or request.form.get('next')
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid login credentials!', 'error')
    return render_template('login.html', next=next_page)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Booking & Core Application Routes ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        user_bookings = Booking.query.filter_by(user_id=current_user.id).all()
        return render_template('index.html', name=current_user.email, bookings=user_bookings)

    return render_template('index.html', name=None, bookings=[])

@app.route('/book', methods=['POST'], strict_slashes=False)
@login_required
def book():
    scheduled_time_str = request.form.get('scheduled_time') # Format coming in: 'YYYY-MM-DDTHH:MM'
    
    # Parse the user's chosen time string into a Python datetime object
    chosen_time = datetime.strptime(scheduled_time_str, '%Y-%m-%dT%H:%M')
    
    # Calculate what 30 minutes from right now looks like
    minimum_allowed_time = datetime.now() + timedelta(minutes=29)
    
    # Backend Guard Gate Check
    if chosen_time < minimum_allowed_time:
        flash('Booking Error: Support slots must be scheduled at least 30 minutes in advance!', 'error')
        return redirect(url_for('index'))
    
    description = request.form.get('description')
    scheduled_time = request.form.get('scheduled_time')
    
    file = request.files.get('screenshot')
    screenshot_path = None
    if file:
        filename = f"{current_user.whatsapp}_{file.filename}"
        screenshot_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    order_data = {'amount': 50000, 'currency': 'INR', 'payment_capture': 1}
    try:
        order = client.order.create(data=order_data)
    except requests.exceptions.RequestException:
        flash('Unable to connect to the payment gateway. Please try again in a moment.', 'error')
        return redirect(url_for('index'))

    if file:
        try:
            file.save(screenshot_path)
        except Exception:
            screenshot_path = None

    new_booking = Booking(
        user_id=current_user.id,
        name=current_user.name,
        whatsapp=current_user.whatsapp,
        email=current_user.email,
        license_no=current_user.license_no,
        description=description,
        scheduled_time=scheduled_time,
        screenshot_path=screenshot_path,
        razorpay_order_id=order['id']
    )
    db.session.add(new_booking)
    db.session.commit()

    flash('Ticket created successfully! Please complete payment from your dashboard.', 'success')
    return redirect(url_for('index'))

@app.route('/checkout/<int:booking_id>')
@login_required
def checkout(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first_or_404()
    if booking.payment_status == "Paid":
        flash('This ticket is already paid!', 'info')
        return redirect(url_for('index'))
        
    order = {'id': booking.razorpay_order_id, 'amount': 50000}
    return render_template('payment.html', order=order, key_id=RAZORPAY_KEY_ID, booking_id=booking.id)

# --- 🚀 AUTOMATED ROUTE: GENERATES ZOHO SESSIONS UPON SUCCESSFUL PAYMENT ---
@app.route('/payment-success', methods=['POST'])
@login_required
def payment_success():
    data = request.json
    booking_id = data.get('booking_id')
    booking = Booking.query.get(booking_id)
    
    if booking and booking.user_id == current_user.id:
        booking.payment_status = "Paid"
        
        # 1. Fetch a fresh live Access Token dynamically via our Refresh Token
        zoho_token = get_zoho_access_token()
        
        if zoho_token:
            # 2. Call Zoho Assist India API to provision a new remote support session
            zoho_api_url = "https://assist.zoho.in/api/v2/session"  # 🌍 Kept as .in
            headers = {
                "Authorization": f"Zoho-oauthtoken {zoho_token}",
                "Content-Type": "application/json"
            }
            payload = {
                "customer_email": booking.email,
                "type": "rs"  # 'rs' represents Remote Support Session
            }
            
            try:
                zoho_response = requests.post(zoho_api_url, headers=headers, json=payload).json()
                
                # 3. Safely extract customer and technician control URLs from Zoho response
                representation = zoho_response.get("representation", {})
                
                booking.remote_link = representation.get("customer_url")
                booking.admin_remote_link = representation.get("technician_url")
                
            except Exception as e:
                print(f"Failed to communicate with Zoho Assist API endpoint: {e}")
                
        db.session.commit()
        return jsonify({"status": "success", "redirect": url_for('success', booking_id=booking.id)})
    
    return jsonify({"status": "failed"}), 400

@app.route('/success/<int:booking_id>')
@login_required
def success(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first_or_404()
    return render_template('success.html', booking=booking)

# --- Admin Panel Routes ---

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash('Access Denied: Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    all_bookings = Booking.query.order_by(Booking.id.desc()).all()
    return render_template('admin.html', bookings=all_bookings)

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Ensure database context initialization handles app-level scopes reliably on runtime boot
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
