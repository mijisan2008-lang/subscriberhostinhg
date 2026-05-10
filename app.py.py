import os
import json
import time
import subprocess
import signal
import shutil
import psutil
import zipfile
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'railway_hosting_secret_key_2024'
app.config['BASE_STORAGE'] = os.path.join(os.getcwd(), 'instances')
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

# Create necessary directories
os.makedirs(app.config['BASE_STORAGE'], exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('templates', exist_ok=True)

# Global process tracker
running_processes = {}
process_start_times = {}

# User data storage (JSON based for simplicity)
USERS_FILE = 'users.json'
SERVERS_FILE = 'servers.json'

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def load_servers():
    if os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_servers(servers):
    with open(SERVERS_FILE, 'w') as f:
        json.dump(servers, f, indent=2)

# Initialize default admin user
users = load_users()
if 'admin@railway.com' not in users:
    users['admin@railway.com'] = {
        'password': 'admin123',
        'name': 'Administrator',
        'role': 'admin',
        'server_limit': 100
    }
    save_users(users)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        users_data = load_users()
        
        if email in users_data and users_data[email]['password'] == password:
            session['user'] = email
            session['name'] = users_data[email]['name']
            session['role'] = users_data[email]['role']
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        
        users_data = load_users()
        if email in users_data:
            return render_template('signup.html', error='Email already exists')
        
        users_data[email] = {
            'password': password,
            'name': name,
            'role': 'user',
            'server_limit': 5
        }
        save_users(users_data)
        return redirect(url_for('login'))
    
    return render_template('signup.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=session)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/api/servers')
def api_servers():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    servers_data = load_servers()
    user_servers = servers_data.get(session['user'], [])
    
    for server in user_servers:
        folder = server['folder']
        if folder in running_processes and running_processes[folder].poll() is None:
            server['online'] = True
            if folder in process_start_times:
                uptime = int(time.time() - process_start_times[folder])
                server['uptime'] = uptime
            try:
                proc = running_processes[folder]
                p = psutil.Process(proc.pid)
                server['cpu'] = p.cpu_percent(interval=0.5)
                server['memory'] = p.memory_info().rss / (1024 * 1024)
            except:
                server['cpu'] = 0
                server['memory'] = 0
        else:
            server['online'] = False
            server['uptime'] = 0
            server['cpu'] = 0
            server['memory'] = 0
    
    return jsonify(user_servers)

@app.route('/api/create-server', methods=['POST'])
def create_server():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    name = data.get('name')
    startup_file = data.get('startup', 'main.py')
    
    if not name:
        return jsonify({'error': 'Server name required'}), 400
    
    users_data = load_users()
    user_data = users_data.get(session['user'])
    servers_data = load_servers()
    user_servers = servers_data.get(session['user'], [])
    
    if len(user_servers) >= user_data['server_limit'] and user_data['role'] != 'admin':
        return jsonify({'error': f'Server limit reached. Max: {user_data["server_limit"]}'}), 400
    
    folder = secure_filename(name.lower().replace(' ', '_')) + '_' + str(int(time.time()))
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    os.makedirs(server_path, exist_ok=True)
    
    # Create default files
    with open(os.path.join(server_path, startup_file), 'w') as f:
        f.write('''# Your Python Application
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return {"message": "Your server is running!", "status": "active"}

@app.route('/health')
def health():
    return {"status": "ok"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
''')
    
    server_info = {
        'id': len(user_servers) + 1,
        'name': name,
        'folder': folder,
        'startup': startup_file,
        'created_at': time.time(),
        'online': False
    }
    user_servers.append(server_info)
    servers_data[session['user']] = user_servers
    save_servers(servers_data)
    
    return jsonify({'success': True, 'server': server_info})

# ============= FILE MANAGEMENT SYSTEM =============

@app.route('/api/files/<folder>')
def list_files(folder):
    """List all files in a server directory"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Check if user owns this server
    servers_data = load_servers()
    user_servers = servers_data.get(session['user'], [])
    server = next((s for s in user_servers if s['folder'] == folder), None)
    
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    
    subpath = request.args.get('path', '')
    server_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath)
    
    if not os.path.exists(server_path):
        return jsonify({'files': [], 'current_path': subpath})
    
    files = []
    for item in os.listdir(server_path):
        item_path = os.path.join(server_path, item)
        files.append({
            'name': item,
            'is_dir': os.path.isdir(item_path),
            'size': os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
            'modified': datetime.fromtimestamp(os.path.getmtime(item_path)).strftime('%Y-%m-%d %H:%M:%S')
        })
    
    # Sort: directories first, then files
    files.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    
    return jsonify({
        'files': files,
        'current_path': subpath,
        'folder': folder
    })

@app.route('/api/upload/<folder>', methods=['POST'])
def upload_file(folder):
    """Upload file to server directory"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Verify ownership
    servers_data = load_servers()
    user_servers = servers_data.get(session['user'], [])
    server = next((s for s in user_servers if s['folder'] == folder), None)
    
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    
    subpath = request.form.get('path', '')
    server_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath)
    os.makedirs(server_path, exist_ok=True)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    filename = secure_filename(file.filename)
    file.save(os.path.join(server_path, filename))
    
    return jsonify({'success': True, 'filename': filename})

@app.route('/api/upload-zip/<folder>', methods=['POST'])
def upload_zip(folder):
    """Upload and extract ZIP file"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    servers_data = load_servers()
    user_servers = servers_data.get(session['user'], [])
    server = next((s for s in user_servers if s['folder'] == folder), None)
    
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    
    subpath = request.form.get('path', '')
    server_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath)
    os.makedirs(server_path, exist_ok=True)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    filename = secure_filename(file.filename)
    zip_path = os.path.join(server_path, filename)
    file.save(zip_path)
    
    # Extract ZIP
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(server_path)
        os.remove(zip_path)  # Remove ZIP after extraction
        return jsonify({'success': True, 'extracted': True})
    except Exception as e:
        return jsonify({'error': f'Failed to extract ZIP: {str(e)}'}), 400

@app.route('/api/create-folder/<folder>', methods=['POST'])
def create_folder(folder):
    """Create a new folder in server directory"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    subpath = data.get('path', '')
    folder_name = data.get('name', '')
    
    if not folder_name:
        return jsonify({'error': 'Folder name required'}), 400
    
    server_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, secure_filename(folder_name))
    os.makedirs(server_path, exist_ok=True)
    
    return jsonify({'success': True})

@app.route('/api/create-file/<folder>', methods=['POST'])
def create_file(folder):
    """Create a new file in server directory"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    subpath = data.get('path', '')
    filename = data.get('name', '')
    content = data.get('content', '')
    
    if not filename:
        return jsonify({'error': 'File name required'}), 400
    
    file_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, secure_filename(filename))
    with open(file_path, 'w') as f:
        f.write(content)
    
    return jsonify({'success': True})

@app.route('/api/get-file/<folder>', methods=['GET'])
def get_file_content(folder):
    """Get file content for editing"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    subpath = request.args.get('path', '')
    filename = request.args.get('file', '')
    
    file_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, filename)
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'content': content, 'filename': filename})
    except Exception as e:
        return jsonify({'error': f'Failed to read file: {str(e)}'}), 400

@app.route('/api/save-file/<folder>', methods=['POST'])
def save_file_content(folder):
    """Save file content after editing"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    subpath = data.get('path', '')
    filename = data.get('file', '')
    content = data.get('content', '')
    
    file_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, filename)
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'Failed to save: {str(e)}'}), 400

@app.route('/api/delete-file/<folder>', methods=['POST'])
def delete_file(folder):
    """Delete a file or folder"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    subpath = data.get('path', '')
    name = data.get('name', '')
    
    item_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, name)
    
    if not os.path.exists(item_path):
        return jsonify({'error': 'Item not found'}), 404
    
    try:
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'Failed to delete: {str(e)}'}), 400

@app.route('/api/rename-file/<folder>', methods=['POST'])
def rename_file(folder):
    """Rename a file or folder"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    subpath = data.get('path', '')
    old_name = data.get('old_name', '')
    new_name = data.get('new_name', '')
    
    old_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, old_name)
    new_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, secure_filename(new_name))
    
    if not os.path.exists(old_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        os.rename(old_path, new_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'Failed to rename: {str(e)}'}), 400

@app.route('/api/download-file/<folder>')
def download_file(folder):
    """Download a file"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    subpath = request.args.get('path', '')
    filename = request.args.get('file', '')
    
    file_path = os.path.join(app.config['BASE_STORAGE'], folder, subpath, filename)
    
    if not os.path.exists(file_path):
        return "File not found", 404
    
    return send_file(file_path, as_attachment=True, download_name=filename)

# ============= SERVER MANAGEMENT =============

@app.route('/api/start-server/<folder>', methods=['POST'])
def start_server(folder):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    servers_data = load_servers()
    user_servers = servers_data.get(session['user'], [])
    server = next((s for s in user_servers if s['folder'] == folder), None)
    
    if not server:
        return jsonify({'error': 'Server not found'}), 404
    
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    startup_file = server['startup']
    log_file = os.path.join(server_path, 'console.log')
    
    if folder in running_processes and running_processes[folder].poll() is None:
        return jsonify({'error': 'Server already running'}), 400
    
    try:
        with open(log_file, 'a') as log:
            log.write(f'\n[{datetime.now()}] Starting server...\n')
            proc = subprocess.Popen(
                ['python3', startup_file],
                cwd=server_path,
                stdout=log,
                stderr=log,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )
            running_processes[folder] = proc
            process_start_times[folder] = time.time()
        
        return jsonify({'success': True, 'message': 'Server started'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop-server/<folder>', methods=['POST'])
def stop_server(folder):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if folder in running_processes:
        try:
            proc = running_processes[folder]
            if hasattr(os, 'killpg'):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=5)
            del running_processes[folder]
            if folder in process_start_times:
                del process_start_times[folder]
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'success': True, 'message': 'Server stopped'})

@app.route('/api/restart-server/<folder>', methods=['POST'])
def restart_server(folder):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Stop first
    if folder in running_processes:
        try:
            proc = running_processes[folder]
            if hasattr(os, 'killpg'):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=5)
        except:
            pass
    
    # Then start
    return start_server(folder)

@app.route('/api/delete-server/<folder>', methods=['DELETE'])
def delete_server(folder):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if folder in running_processes:
        try:
            proc = running_processes[folder]
            if hasattr(os, 'killpg'):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
            del running_processes[folder]
        except:
            pass
    
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    if os.path.exists(server_path):
        shutil.rmtree(server_path)
    
    servers_data = load_servers()
    user_servers = servers_data.get(session['user'], [])
    user_servers = [s for s in user_servers if s['folder'] != folder]
    servers_data[session['user']] = user_servers
    save_servers(servers_data)
    
    return jsonify({'success': True, 'message': 'Server deleted'})

@app.route('/api/server-logs/<folder>')
def server_logs(folder):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    server_path = os.path.join(app.config['BASE_STORAGE'], folder)
    log_file = os.path.join(server_path, 'console.log')
    
    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            logs = f.read()
            return jsonify({'logs': logs[-5000:]})
    
    return jsonify({'logs': 'No logs available yet'})

@app.route('/api/system-stats')
def system_stats():
    return jsonify({
        'cpu': psutil.cpu_percent(interval=1),
        'memory': psutil.virtual_memory().percent,
        'disk': psutil.disk_usage('/').percent
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)