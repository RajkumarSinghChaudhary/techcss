import os
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import razorpay

# Cloudinary & Firebase SDK Imports
import cloudinary
import cloudinary.uploader
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-change-this-in-production'

# --- ☁️ Cloudinary Configuration ---
# Configuration       
cloudinary.config( 
    cloud_name = "hc1ekqow", 
    api_key = "162334798191534", 
    api_secret = "<your_api_secret>", # Click 'View API Keys' above to copy your API secret
    secure=True
)

# --- 🔥 Firebase Firestore Setup ---
# Place your 'serviceAccountKey.json' file in your project root directory.
# In Production on Render, it is safer to read these values from environment variables.
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# --- Flask-Login Setup with User Class Wrapper ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, user_id, data):
        self.id = user_id
        self.name = data.get('name')
        self.whatsapp = data.get('whatsapp')
        self.license_no = data.get('license_no')
        self.email = data.get('email')
        self.password = data.get('password')
        self.is_admin = data.get('is_admin', False)

@login_manager.user_loader
def load_user(user_id):
    user_ref = db.collection('users').document(str(user_id)).get()
    if user_ref.exists:
        return User(user_ref.id, user_ref.to_dict())
    return None

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
    url = "https://accounts.zoho.in/oauth/v2/token"
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
        
        # Query Firebase Firestore to see if user exists
        users_ref = db.collection('users').where('email', '==', email).limit(1).get()
        if len(users_ref) > 0:
            flash('Email already registered!', 'error')
            return redirect(url_for('register', next=next_page))
            
        hashed_password = generate_password_hash(password, method='scrypt')
        
        # Auto-generate auto-incrementing-like string IDs or native Firestore IDs
        new_user_ref = db.collection('users').document() 
        new_user_ref.set({
            "name": name,
            "whatsapp": whatsapp,
            "license_no": license_no,
            "email": email,
            "password": hashed_password,
            "is_admin": False
        })
        
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
        
        users_ref = db.collection('users').where('email', '==', email).limit(1).get()
        if len(users_ref) > 0:
            user_doc = users_ref[0]
            user_data = user_doc.to_dict()
            if check_password_hash(user_data.get('password'), password):
                user_obj = User(user_doc.id, user_data)
                login_user(user_obj)
                return redirect(next_page or url_for('index'))
        
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
        # Fetch only bookings belonging to the current logged in user
        bookings_snapshots = db.collection('bookings').where('user_id', '==', current_user.id).get()
        user_bookings = []
        for doc in bookings_snapshots:
            b_data = doc.to_dict()
            b_data['id'] = doc.id  # Append document id so frontend templates render links perfectly
            user_bookings.append(b_data)
        return render_template('index.html', name=current_user.email, bookings=user_bookings)

    return render_template('index.html', name=None, bookings=[])

@app.route('/book', methods=['POST'], strict_slashes=False)
@login_required
def book():
    scheduled_time_str = request.form.get('scheduled_time')
    chosen_time = datetime.strptime(scheduled_time_str, '%Y-%m-%dT%H:%M')
    minimum_allowed_time = datetime.now() + timedelta(minutes=29)
    
    if chosen_time < minimum_allowed_time:
        flash('Booking Error: Support slots must be scheduled at least 30 minutes in advance!', 'error')
        return redirect(url_for('index'))
    
    description = request.form.get('description')
    scheduled_time = request.form.get('scheduled_time')
    
    file = request.files.get('screenshot')
    screenshot_url = None
    
    # Direct Cloudinary Streaming - Wires directly to external server without saving local files
    if file and file.filename != '':
        try:
            upload_result = cloudinary.uploader.upload(file, folder="tickets/")
            screenshot_url = upload_result.get("secure_url")
        except Exception as e:
            print(f"Cloudinary upload failed: {e}")
            screenshot_url = None

    order_data = {'amount': 50000, 'currency': 'INR', 'payment_capture': 1}
    try:
        order = client.order.create(data=order_data)
    except requests.exceptions.RequestException:
        flash('Unable to connect to the payment gateway. Please try again in a moment.', 'error')
        return redirect(url_for('index'))

    # Save tracking payload details into Cloud Firestore
    booking_data = {
        "user_id": current_user.id,
        "name": current_user.name,
        "whatsapp": current_user.whatsapp,
        "email": current_user.email,
        "license_no": current_user.license_no,
        "description": description,
        "scheduled_time": scheduled_time,
        "screenshot_path": screenshot_url, # Key name remains identical to keep HTML templates working
        "payment_status": "Pending",
        "razorpay_order_id": order['id'],
        "remote_link": None,
        "admin_remote_link": None,
        "created_at": firestore.SERVER_TIMESTAMP
    }
    db.collection('bookings').add(booking_data)

    flash('Ticket created successfully! Please complete payment from your dashboard.', 'success')
    return redirect(url_for('index'))

@app.route('/checkout/<string:booking_id>')
@login_required
def checkout(booking_id):
    booking_ref = db.collection('bookings').document(booking_id).get()
    if not booking_ref.exists:
        return "Not Found", 404
        
    booking_data = booking_ref.to_dict()
    if booking_data.get('user_id') != current_user.id:
        return "Unauthorized", 401
        
    if booking_data.get('payment_status') == "Paid":
        flash('This ticket is already paid!', 'info')
        return redirect(url_for('index'))
        
    order = {'id': booking_data.get('razorpay_order_id'), 'amount': 50000}
    return render_template('payment.html', order=order, key_id=RAZORPAY_KEY_ID, booking_id=booking_ref.id)

# --- AUTOMATED ROUTE: GENERATES ZOHO SESSIONS UPON SUCCESSFUL PAYMENT ---
@app.route('/payment-success', methods=['POST'])
@login_required
def payment_success():
    data = request.json
    booking_id = data.get('booking_id')
    
    booking_ref = db.collection('bookings').document(str(booking_id))
    booking_snap = booking_ref.get()
    
    if booking_snap.exists:
        booking_data = booking_snap.to_dict()
        if booking_data.get('user_id') == current_user.id:
            updated_payload = {"payment_status": "Paid"}
            
            zoho_token = get_zoho_access_token()
            if zoho_token:
                zoho_api_url = "https://assist.zoho.in/api/v2/session"
                headers = {
                    "Authorization": f"Zoho-oauthtoken {zoho_token}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "customer_email": booking_data.get('email'),
                    "type": "rs"
                }
                
                try:
                    zoho_response = requests.post(zoho_api_url, headers=headers, json=payload).json()
                    representation = zoho_response.get("representation", {})
                    
                    updated_payload["remote_link"] = representation.get("customer_url")
                    updated_payload["admin_remote_link"] = representation.get("technician_url")
                    
                except Exception as e:
                    print(f"Failed to communicate with Zoho Assist API endpoint: {e}")
                    
            booking_ref.update(updated_payload)
            return jsonify({"status": "success", "redirect": url_for('success', booking_id=booking_ref.id)})
    
    return jsonify({"status": "failed"}), 400

@app.route('/success/<string:booking_id>')
@login_required
def success(booking_id):
    booking_ref = db.collection('bookings').document(booking_id).get()
    if not booking_ref.exists:
        return "Not Found", 404
    booking_data = booking_ref.to_dict()
    booking_data['id'] = booking_ref.id
    return render_template('success.html', booking=booking_data)

# --- Admin Panel Routes ---

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash('Access Denied: Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    # Fetch all tickets sorted by creation date descending
    all_bookings_snap = db.collection('bookings').order_by('created_at', direction=firestore.Query.DESCENDING).get()
    all_bookings = []
    for doc in all_bookings_snap:
        b_data = doc.to_dict()
        b_data['id'] = doc.id
        all_bookings.append(b_data)
        
    return render_template('admin.html', bookings=all_bookings)

# Deprecated route as assets are now directly sourced from Cloudinary's global secure URLs
@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return "Moved to Cloudinary", 410

# --- 🚀 NEW: ASYNC PRE-LOGIN SCREENSHOT UPLOADER ---
@app.route('/upload-temp-screenshot', methods=['POST'])
def upload_temp_screenshot():
    file = request.files.get('file')
    if file:
        try:
            upload_result = cloudinary.uploader.upload(file, folder="tickets/")
            return jsonify({"secure_url": upload_result.get("secure_url")})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "No file supplied"}), 400


# --- 🚀 NEW: AUTO-PILOT BOOKING ROUTE FOR POST-LOGIN PROCESSING ---
@app.route('/book-async', methods=['POST'])
@login_required
def book_async():
    data = request.json or {}
    description = data.get('description')
    scheduled_time = data.get('scheduled_time')
    screenshot_url = data.get('screenshot_url') # Directly receives the pre-saved Cloudinary URL

    # Safety Guard Gate Check (Mirroring standard /book constraint criteria)
    chosen_time = datetime.strptime(scheduled_time, '%Y-%m-%dT%H:%M')
    if chosen_time < (datetime.now() + timedelta(minutes=29)):
        return jsonify({"status": "error", "message": "Support slots must be scheduled 30 minutes in advance"}), 400

    order_data = {'amount': 50000, 'currency': 'INR', 'payment_capture': 1}
    try:
        order = client.order.create(data=order_data)
    except Exception:
        return jsonify({"status": "error", "message": "Payment gateway communication error"}), 500

    booking_data = {
        "user_id": current_user.id,
        "name": current_user.name,
        "whatsapp": current_user.whatsapp,
        "email": current_user.email,
        "license_no": current_user.license_no,
        "description": description,
        "scheduled_time": scheduled_time,
        "screenshot_path": screenshot_url, # Saves instantly with zero data data drop-off
        "payment_status": "Pending",
        "razorpay_order_id": order['id'],
        "remote_link": None,
        "admin_remote_link": None,
        "created_at": firestore.SERVER_TIMESTAMP
    }
    db.collection('bookings').add(booking_data)
    
    flash('Ticket created automatically from your saved slot! Please complete your payment.', 'success')
    return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(debug=True)
