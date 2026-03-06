from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import requests
import json
import base64
import os

# Initialize Flask app
app = Flask(__name__)

# Configuration from environment variables (set these in Railway)
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///temp.db')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', '')
ADMIN_KEY = os.environ.get('ADMIN_KEY', 'change-this-admin-key')
CLIENT_KEY = os.environ.get('CLIENT_KEY', 'change-this-client-key')
XOR_KEY = os.environ.get('XOR_KEY', 'X7k9pQ2mR5vL')  # Your XOR key

# Fix for Railway's PostgreSQL URL format
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ============ DATABASE MODELS ============
class User(db.Model):
    """Stores user information and bot status"""
    __tablename__ = 'users'
    
    username = db.Column(db.String(100), primary_key=True)
    run_script = db.Column(db.Boolean, default=False)
    target_user = db.Column(db.String(100), default='none')
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    online = db.Column(db.Boolean, default=False)
    
    def to_dict(self):
        return {
            'username': self.username,
            'run_script': self.run_script,
            'target_user': self.target_user,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'online': self.online
        }

class Brainrot(db.Model):
    """Stores brainrot data for each user"""
    __tablename__ = 'brainrots'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), db.ForeignKey('users.username'), nullable=False)
    plot_id = db.Column(db.String(50))
    plot_owner = db.Column(db.String(100))
    brainrot_name = db.Column(db.String(200))
    generation = db.Column(db.String(50))
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'plotId': self.plot_id,
            'owner': self.plot_owner,
            'brainrots': [{
                'name': self.brainrot_name,
                'gen': self.generation
            }]
        }

# ============ UTILITY FUNCTIONS ============
def send_to_discord(message):
    """Send log messages to Discord webhook"""
    if not DISCORD_WEBHOOK_URL:
        print(f"Discord log (no webhook): {message}")
        return
    
    try:
        payload = {
            "content": message,
            "username": "Bot Logger"
        }
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code != 204:
            print(f"Failed to send to Discord: {response.status_code}")
    except Exception as e:
        print(f"Error sending to Discord: {e}")

def verify_auth(headers, expected_key, key_type="Admin"):
    """Verify authorization headers"""
    auth = headers.get('Authorization', '')
    if auth != expected_key:
        send_to_discord(f"❌ Failed {key_type} auth attempt - invalid key")
        return False
    return True

# ============ API ENDPOINTS ============

@app.route('/', methods=['GET'])
def home():
    """Root endpoint - API status"""
    return jsonify({
        "status": "online",
        "message": "Brainrot Tracker API is running",
        "endpoints": [
            "/users",
            "/users/list",
            "/users/<username>",
            "/users/<username>/brainrots",
            "/admin/<username>"
        ]
    })

@app.route('/users', methods=['GET'])
def get_users():
    """Get list of all users"""
    users = User.query.all()
    return jsonify({
        "users": [user.username for user in users]
    })

@app.route('/users/list', methods=['GET'])
def get_users_list():
    """Get online status for all users"""
    # Update offline status (users not seen in last 5 minutes)
    five_min_ago = datetime.utcnow() - timedelta(minutes=5)
    User.query.filter(User.last_seen < five_min_ago).update({'online': False})
    db.session.commit()
    
    users = User.query.all()
    result = {}
    for user in users:
        result[user.username] = {
            "online": user.online,
            "seconds_ago": int((datetime.utcnow() - user.last_seen).total_seconds()) if user.last_seen else 0
        }
    return jsonify(result)

@app.route('/users/<username>', methods=['GET'])
def get_user(username):
    """Get specific user data"""
    user = User.query.get(username)
    if not user:
        # Auto-create user if they don't exist
        user = User(username=username, run_script=False, target_user='none')
        db.session.add(user)
        db.session.commit()
        send_to_discord(f"🆕 New user registered: {username}")
    
    return jsonify({
        "run_script": user.run_script,
        "target_user": user.target_user
    })

@app.route('/admin/<username>', methods=['POST'])
def admin_update(username):
    """Update bot status (requires ADMIN_KEY)"""
    if not verify_auth(request.headers, ADMIN_KEY, "Admin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    user = User.query.get(username)
    if not user:
        user = User(username=username)
        db.session.add(user)
    
    old_status = user.run_script
    old_target = user.target_user
    
    if 'run_script' in data:
        user.run_script = data['run_script']
    if 'target_user' in data:
        user.target_user = data['target_user']
    
    db.session.commit()
    
    # Log to Discord
    status_change = f"run_script: {old_status} → {user.run_script}"
    target_change = f"target: {old_target} → {user.target_user}"
    send_to_discord(f"⚙️ Admin updated {username}\n{status_change}\n{target_change}")
    
    return jsonify({"message": "User updated successfully"}), 200

@app.route('/users/<username>/brainrots', methods=['GET'])
def get_brainrots(username):
    """Get brainrot data for a user"""
    brainrots = Brainrot.query.filter_by(username=username).all()
    
    # Group by plot
    plots_dict = {}
    for br in brainrots:
        plot_id = br.plot_id or 'unknown'
        if plot_id not in plots_dict:
            plots_dict[plot_id] = {
                'plotId': plot_id,
                'owner': br.plot_owner or 'Unknown',
                'brainrots': []
            }
        plots_dict[plot_id]['brainrots'].append({
            'name': br.brainrot_name,
            'gen': br.generation
        })
    
    result = list(plots_dict.values())
    return jsonify(result)

@app.route('/users/<username>/brainrots', methods=['POST'])
def post_brainrots(username):
    """Receive brainrot scan data (requires CLIENT_KEY)"""
    if not verify_auth(request.headers, CLIENT_KEY, "Client"):
        return jsonify({"error": "Unauthorized"}), 401
    
    # Update user's last seen
    user = User.query.get(username)
    if not user:
        user = User(username=username)
        db.session.add(user)
    
    user.last_seen = datetime.utcnow()
    user.online = True
    db.session.commit()
    
    # Get encrypted data
    encrypted_data = request.get_data(as_text=True)
    
    try:
        # Note: For production, you'd need proper XOR decryption here
        # This is simplified - you'll need to implement the exact XOR logic
        # from your script if the data is XOR-encrypted
        import base64
        decoded_data = base64.b64decode(encrypted_data).decode('utf-8')
        brainrot_data = json.loads(decoded_data)
        
        # Clear old data for this user
        Brainrot.query.filter_by(username=username).delete()
        
        # Save new data
        count = 0
        for plot in brainrot_data:
            for br in plot.get('brainrots', []):
                brainrot = Brainrot(
                    username=username,
                    plot_id=plot.get('plotId'),
                    plot_owner=plot.get('owner'),
                    brainrot_name=br.get('name'),
                    generation=br.get('gen')
                )
                db.session.add(brainrot)
                count += 1
        
        db.session.commit()
        send_to_discord(f"📊 {username} uploaded {count} brainrots")
        
        # Return current status (so bot knows if it should run)
        return jsonify({
            "run_script": user.run_script,
            "target_user": user.target_user
        })
        
    except Exception as e:
        send_to_discord(f"❌ Error processing data from {username}: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Create tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
