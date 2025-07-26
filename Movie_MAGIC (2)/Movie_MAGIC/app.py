from flask import Flask, render_template, request, redirect, url_for, session, flash
import hashlib
import sqlite3
import os
import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key'

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
        seats_input = request.form['seats']
        seats = [seat.strip() for seat in seats_input.split(',') if seat.strip()]
        if not seats:
            flash("Please enter valid seats.")
            return redirect(url_for('seating', title=title))

        total_price = movie['price'] * len(seats)

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO bookings (email, movie, seats, total) VALUES (?, ?, ?, ?)",
                      (session['email'], movie['title'], ','.join(seats), total_price))
            conn.commit()

        session.setdefault('bookings', []).append({
            'movie': movie['title'],
            'seats': seats,
            'total': total_price
        })
        session.modified = True

        return redirect(url_for('payment', title=title, seats=','.join(seats), total=total_price))

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
    
    # Get basic booking information
    movie = request.form.get('movie')
    seats = request.form.get('seats')
    total = request.form.get('total')
    payment_method = request.form.get('payment_method')
    
    # Validate payment method
    if not payment_method:
        flash('Payment method was not specified', 'error')
        return redirect(url_for('payment', title=movie, seats=seats, total=total))
    
    # Get payment method specific details
    payment_details = {}
    
    if payment_method == 'UPI':
        payment_details['upi_id'] = request.form.get('upi_id')
    
    elif payment_method == 'Credit Card':
        payment_details['card_number'] = request.form.get('card_number')
        payment_details['expiry_date'] = request.form.get('expiry_date')
        payment_details['name_on_card'] = request.form.get('name_on_card')
        # Don't store CVV for security reasons
    
    elif payment_method == 'Debit Card':
        payment_details['card_number'] = request.form.get('debit_card_number')
        payment_details['expiry_date'] = request.form.get('debit_expiry_date')
        payment_details['name_on_card'] = request.form.get('debit_name_on_card')
        # Don't store CVV for security reasons
    
    elif payment_method == 'Netbanking':
        payment_details['bank_name'] = request.form.get('bank_name')
    
    elif payment_method == 'PayPal':
        payment_details['paypal_email'] = request.form.get('paypal_email')
    
    elif payment_method == 'Google Pay':
        payment_details['phone_number'] = request.form.get('google_pay_number')
    
    # Process the seat information
    seat_list = [s.split(':')[0] if ':' in s else s for s in seats.split(',')]
    
    # Create booking record
    booking = {
        'movie': movie,
        'seats': ', '.join(seat_list),
        'payment_method': payment_method,
        'payment_details': payment_details,  # Store payment details (except sensitive info)
        'total': total,
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Add to session
    session.setdefault('bookings', []).append(booking)
    session.modified = True
    
    # In a real application, you would process the payment with a payment gateway here
    print(f"Processing payment for {movie}, seats: {seats}, total: {total}, method: {payment_method}")
    
    # Show success message
    flash(f"Payment successful! Your booking for {movie} is confirmed.", 'success')
    
    return redirect(url_for('ticket_confirmation', title=movie, seats=seats, total=total))
@app.route('/tickets')
def ticket_confirmation():
    if 'email' not in session:
        return redirect(url_for('login'))

    title = request.args.get('title')
    seats = request.args.get('seats')

    movie = next((m for m in MOVIES if m['title'] == title), None)
    if not movie or not seats:
        flash('Invalid booking details.')
        return redirect(url_for('home'))

    seat_list = seats.split(',')
    return render_template('tickets.html', movie=movie, seats=seat_list)

@app.route('/dashboard')
def dashboard():
    if 'email' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT movie, seats, total FROM bookings WHERE email = ?", (session['email'],))
        rows = c.fetchall()
        bookings = [{'movie': row[0], 'seats': row[1].split(','), 'total': row[2]} for row in rows]

    return render_template('dashboard.html', bookings=bookings)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/services')
def services():
    return render_template('services.html')

if __name__ == '__main__':
    app.run(debug=True)