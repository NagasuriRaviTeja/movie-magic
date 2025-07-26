from flask import Flask, render_template, request, redirect, url_for, session, flash
import hashlib
import sqlite3
import boto3
import os
import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key'

# AWS Configuration
AWS_REGION = 'us-east-1'
USERS_TABLE = 'movie ticket_user'
BOOKINGS_TABLE = 'movie ticket_booking'
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:495599749771:movieticket_topic'

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
sns_client = boto3.client('sns', region_name=AWS_REGION)

# Reference DynamoDB tables
users_table = dynamodb.Table(USERS_TABLE)
bookings_table = dynamodb.Table(BOOKINGS_TABLE)

# Dummy movie list
MOVIES = [
    {'title': 'KUBERA', 'price': 350, 'image': 'kubera.jpg'},
    {'title': 'DEVARA', 'price': 300, 'image': 'devara.jpg'},
   
    {'title': 'ANIMAL', 'price': 300, 'image': 'animal.jpg'}
]

DB_NAME = 'database.db'

# Initialize SQLite DB
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        email TEXT PRIMARY KEY,
                        name TEXT,
                        password TEXT
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS bookings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT,
                        movie TEXT,
                        seats TEXT,
                        total INTEGER
                    )''')
        conn.commit()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = hashlib.sha256(request.form['password'].encode()).hexdigest()

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE email = ?", (email,))
            if c.fetchone():
                flash("Email already registered.")
                return redirect(url_for('register'))

            c.execute("INSERT INTO users (email, name, password) VALUES (?, ?, ?)", (email, name, password))
            conn.commit()

        flash("Registration successful! Please login.")
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = hashlib.sha256(request.form['password'].encode()).hexdigest()

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password))
            user = c.fetchone()

        if user:
            session['email'] = email
            session['bookings'] = []
            return redirect(url_for('home'))
        else:
            flash("Invalid credentials")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully')
    return redirect(url_for('index'))

@app.route('/home')
def home():
    if 'email' not in session:
        return redirect(url_for('login'))
    return render_template('home.html', movies=MOVIES)

@app.route('/booking/<title>')
def booking(title):
    if 'email' not in session:
        return redirect(url_for('login'))

    movie = next((m for m in MOVIES if m['title'] == title), None)
    if not movie:
        flash('Movie not found')
        return redirect(url_for('home'))

    return render_template('booking.html', movie=movie)

@app.route('/seating/<title>', methods=['GET', 'POST'])
def seating(title):
    if 'email' not in session:
        return redirect(url_for('login'))

    movie = next((m for m in MOVIES if m['title'] == title), None)
    if not movie:
        flash('Movie not found')
        return redirect(url_for('home'))

    if request.method == 'POST':
        seats_raw = request.form.get('seats')
        if not seats_raw:
            flash('No seats selected.')
            return redirect(url_for('seating', title=title))

        selected_seats = seats_raw.split(',')

        total = 0
        seat_list = []
        prices = []

        for seat in selected_seats:
            if ':' not in seat:
                continue
            seat_name, seat_type = seat.split(':')
            seat_list.append(seat_name)

            if seat_type == 'premium':
                price = 250
            elif seat_type == 'gold':
                price = 170
            else:
                flash(f"Unknown seat type: {seat_type}")
                return redirect(url_for('seating', title=title))

            total += price
            prices.append(price)

        price_per_ticket = prices[0] if prices else 0

        # Save to SQLite
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO bookings (email, movie, seats, total) VALUES (?, ?, ?, ?)",
                      (session['email'], movie['title'], ','.join(seat_list), total))
            conn.commit()

        # Save to DynamoDB
        try:
            bookings_table.put_item(Item={
                'booking_id': f"{session['email']}_{movie['title']}",
                'email': session['email'],
                'movie': movie['title'],
                'seats': ','.join(seat_list),
                'total': total
            })
        except Exception as e:
            print("Error saving to DynamoDB booking table:", e)

        # Send SNS notification
        try:
            message = f"New Booking!\nUser: {session['email']}\nMovie: {movie['title']}\nSeats: {', '.join(seat_list)}\nTotal: ₹{total}"
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=message,
                Subject='New Movie Booking Alert'
            )
        except Exception as e:
            print("Error sending SNS notification:", e)

        return redirect(url_for('payment', title=title, seats=','.join(seat_list), total=total))

    return render_template('seating.html', movie=movie)

@app.route('/payment/<title>', methods=['GET', 'POST'])
def payment(title):
    if 'email' not in session:
        return redirect(url_for('login'))

    seats = request.args.get('seats', '')
    total = request.args.get('total', 0)

    return render_template('payment.html', movie=title, seats=seats, total=total)

@app.route('/process_payment', methods=['POST'])
def process_payment():
    if 'email' not in session:
        return redirect(url_for('login'))
    
    # Get form data
    movie = request.form.get('movie')
    seats = request.form.get('seats')
    total = request.form.get('total')
    payment_method = request.form.get('payment_method')
    
    # Validate payment method
    if not payment_method:
        flash('Payment method was not specified', 'error')
        return redirect(url_for('payment', title=movie, seats=seats, total=total))
    
    # Validate required fields
    if not all([movie, seats, total, payment_method]):
        flash('Missing required payment information. Please try again.', 'error')
        return redirect(url_for('payment', title=movie, seats=seats, total=total))
    
    # Get payment details based on the payment method
    payment_details = {}
    
    try:
        if payment_method == 'UPI':
            upi_id = request.form.get('upi_id')
            if not upi_id or '@' not in upi_id:
                flash('Invalid UPI ID. Please enter a valid UPI ID.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
            payment_details['upi_id'] = upi_id
            
        elif payment_method == 'Credit Card':
            card_number = request.form.get('card_number')
            card_holder = request.form.get('name_on_card')
            expiry_date = request.form.get('expiry_date')
            cvv = request.form.get('cvv')
            
            if not all([card_number, card_holder, expiry_date, cvv]):
                flash('Please fill in all credit card details.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
                
            # Basic validation
            if not (card_number.isdigit() and len(card_number) == 16):
                flash('Invalid card number. Please enter a 16-digit number.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
                
            payment_details['card_number'] = ''.join(['*' * 12, card_number[-4:]])  # Mask card number for security
            payment_details['card_holder'] = card_holder
            payment_details['expiry_date'] = expiry_date
            payment_details['cvv'] = '*'  # Mask CVV for security
            
        elif payment_method == 'Debit Card':
            card_number = request.form.get('debit_card_number')
            card_holder = request.form.get('debit_name_on_card')
            expiry_date = request.form.get('debit_expiry_date')
            cvv = request.form.get('debit_cvv')
            
            if not all([card_number, card_holder, expiry_date, cvv]):
                flash('Please fill in all debit card details.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
                
            # Basic validation
            if not (card_number.isdigit() and len(card_number) == 16):
                flash('Invalid card number. Please enter a 16-digit number.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
                
            payment_details['card_number'] = ''.join(['*' * 12, card_number[-4:]])  # Mask card number for security
            payment_details['card_holder'] = card_holder
            payment_details['expiry_date'] = expiry_date
            payment_details['cvv'] = '*'  # Mask CVV for security
            
        elif payment_method == 'Netbanking':
            bank = request.form.get('bank_name')
            if not bank:
                flash('Please select a bank for netbanking.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
            payment_details['bank'] = bank
            
        elif payment_method == 'PayPal':
            paypal_email = request.form.get('paypal_email')
            if not paypal_email or '@' not in paypal_email:
                flash('Please enter a valid PayPal email address.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
            payment_details['paypal_email'] = paypal_email
            
        elif payment_method == 'Google Pay':
            gpay_number = request.form.get('google_pay_number')
            if not gpay_number or len(gpay_number) < 10:
                flash('Please enter a valid phone number for Google Pay.', 'error')
                return redirect(url_for('payment', title=movie, seats=seats, total=total))
            payment_details['gpay_number'] = gpay_number
            
        else:
            flash('Invalid payment method selected.', 'error')
            return redirect(url_for('payment', title=movie, seats=seats, total=total))
    except Exception as e:
        flash(f'An error occurred while processing your payment: {str(e)}', 'error')
        return redirect(url_for('payment', title=movie, seats=seats, total=total))

    # Process payment (mocked)
    seat_list = [s.split(':')[0] if ':' in s else s for s in seats.split(',')]
    
    # Add timestamp to booking
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    booking = {
        'movie': movie,
        'seats': ', '.join(seat_list),
        'payment_method': payment_method,
        'payment_details': payment_details,
        'total': total,
        'timestamp': timestamp
    }

    session.setdefault('bookings', []).append(booking)
    session.modified = True

    # Send confirmation via SNS
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=f"Booking Confirmed for {movie} - Seats: {seat_list} - Payment Method: {payment_method}",
            Subject="Movie Booking Confirmation"
        )
    except Exception as e:
        print("SNS publish failed:", e)

    print(f"Processing payment for {movie}, seats: {seats}, total: {total}, method: {payment_method}")

    flash('Payment successful! Your tickets are ready.', 'success')

    # Store payment information in session for ticket display
    session['payment_info'] = {
        'method': payment_method,
        'details': payment_details,
        'timestamp': timestamp
    }

    return redirect(url_for('ticket_confirmation', title=movie, seats=seats, total=total))

@app.route('/tickets')
def ticket_confirmation():
    if 'email' not in session:
        return redirect(url_for('login'))

    title = request.args.get('title')
    seats = request.args.get('seats')
    total = request.args.get('total')

    movie = next((m for m in MOVIES if m['title'] == title), None)
    if not movie or not seats:
        flash('Invalid booking details.')
        return redirect(url_for('home'))

    seat_list = seats.split(',')
    
    # Get payment information from session
    payment_info = session.get('payment_info', {
        'method': 'Not specified',
        'details': {},
        'timestamp': 'Not available'
    })
    
    return render_template('tickets.html', movie=movie, seats=seat_list, total=total, 
                           payment_method=payment_info['method'], 
                           payment_timestamp=payment_info['timestamp'])

@app.route('/dashboard')
def dashboard():
    if 'email' not in session:
        return redirect(url_for('login'))

    # Get bookings from session first (these will have payment method and timestamp)
    session_bookings = session.get('bookings', [])
    
    # Then get bookings from database (these might not have payment method)
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT movie, seats, total FROM bookings WHERE email = ?", (session['email'],))
        rows = c.fetchall()
        db_bookings = [{
            'movie': row[0], 
            'seats': row[1].split(','), 
            'total': row[2],
            'payment_method': 'Not recorded',  # Default for old bookings
            'timestamp': 'Not recorded'        # Default for old bookings
        } for row in rows]
    
    # Combine both sources, prioritizing session bookings which have payment info
    # This is a simple approach - in a real app, you'd store payment info in the database
    all_bookings = session_bookings + db_bookings
    
    return render_template('dashboard.html', bookings=all_bookings)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/services')
def services():
    return render_template('services.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
