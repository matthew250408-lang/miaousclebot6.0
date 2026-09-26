"""Miaouscle Bot - Web Dashboard avec Discord OAuth2"""
import os, sys, json, time, requests

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response
from urllib.parse import urlencode
import re
import secrets

from utils.server_config_store import init_schema as _init_server_config_store
_init_server_config_store()

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get('SESSION_SECRET', os.environ.get('FLASK_SECRET_KEY', 'miaouscle-secret-2025'))
app.config['PERMANENT_SESSION_LIFETIME'] = 604800
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False

# Tracking des visites
visits_file = os.path.join(_PROJECT_ROOT, 'web', 'data', 'visits_tracking.json')

def load_visits():
    """Charge les visites depuis le fichier JSON"""
    try:
        if os.path.exists(visits_file):
            with open(visits_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {'total': 0, 'by_page': {}, 'by_device': {'mobile': 0, 'desktop': 0}, 'by_country': {}}

def save_visits(data):
    """Sauvegarde les visites"""
    try:
        os.makedirs(os.path.dirname(visits_file), exist_ok=True)
        with open(visits_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except:
        pass

def detect_device(user_agent):
    """Détecte si c'est mobile ou desktop"""
    mobile_patterns = r'(mobile|android|iphone|ipad|blackberry|windows phone|opera mini)'
    return 'mobile' if re.search(mobile_patterns, user_agent, re.IGNORECASE) else 'desktop'

def get_country_from_ip(ip):
    """Détecte le pays depuis l'IP (simplifié - retourne partie de l'IP)"""
    try:
        # Essayer de utiliser geoip si disponible
        import geoip2.database
        reader = geoip2.database.Reader('GeoLite2-Country.mmdb')
        response = reader.country(ip)
        return response.country.iso_code
    except:
        # Sinon retourner "Unknown"
        return "Unknown"

def track_visit(page):
    """Enregistre une visite"""
    try:
        visits = load_visits()
        user_agent = request.headers.get('User-Agent', 'Unknown')
        device = detect_device(user_agent)
        
        # Incrémenter total
        visits['total'] = visits.get('total', 0) + 1
        
        # Tracker par page
        if page not in visits['by_page']:
            visits['by_page'][page] = 0
        visits['by_page'][page] += 1
        
        # Tracker par device
        visits['by_device'][device] = visits['by_device'].get(device, 0) + 1
        
        # Tracker par pays (simplifié - utiliser l'IP ou X-Forwarded-For)
        ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
        if ip not in visits['by_country']:
            visits['by_country'][ip] = 0
        visits['by_country'][ip] += 1
        
        save_visits(visits)
    except Exception as e:
        print(f"❌ Erreur tracking: {e}")

OWNER_ID = 'BC7317BD000FE468'
OWNER_PASSWORD = os.environ.get('OWNER_PASSWORD', '@user-1234@')
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '')
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID', '1157699815833088001')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET', '')

# Obtenir le domaine depuis les variables d'environnement
REPLIT_DEV_DOMAIN = os.environ.get('REPLIT_DEV_DOMAIN', 'http://localhost:5000')
if not REPLIT_DEV_DOMAIN.startswith('http'):
    REPLIT_DEV_DOMAIN = f"https://{REPLIT_DEV_DOMAIN}"
REDIRECT_URI = f"{REPLIT_DEV_DOMAIN}/callback"

def get_redirect_uri():
    host = request.headers.get('X-Forwarded-Host') or request.host
    proto = request.headers.get('X-Forwarded-Proto') or ('https' if request.is_secure else 'http')
    if host:
        return f"{proto}://{host}/callback"
    return REDIRECT_URI

# Logs de modification
LOGS_CHANNEL_ID = 1440720849375068170  # Canal logs du bot

REPORT_CHANNEL_ID = 1332455504777318561
SUGGESTION_CHANNEL_ID = 1344414298847576104

PENDING_SYNC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pending_role_syncs.json')

def _push_pending_role_sync(discord_user_id: int):
    """Ajoute un user_id dans la queue de sync de rôles Discord.
    Le bot lit ce fichier toutes les 30 secondes et sync immédiatement."""
    try:
        import threading
        _lock = getattr(_push_pending_role_sync, '_lock', None)
        if _lock is None:
            _push_pending_role_sync._lock = threading.Lock()
        with _push_pending_role_sync._lock:
            try:
                with open(PENDING_SYNC_FILE, 'r', encoding='utf-8') as f:
                    pending = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pending = []
            uid = int(discord_user_id)
            if uid not in pending:
                pending.append(uid)
            with open(PENDING_SYNC_FILE, 'w', encoding='utf-8') as f:
                json.dump(pending, f)
    except Exception as e:
        print(f"[sync_queue] erreur push : {e}")


def load_json(f):
    try:
        data_dir = os.environ.get(
            'MAINTENANCE_DATA_DIR',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'),
        )
        abs_path = os.path.join(data_dir, f)
        
        if not os.path.exists(abs_path):
            # Retourner structure par défaut selon le type de fichier
            if 'history' in f or 'queue' in f or 'servers' in f:
                return []
            elif 'maintenance' in f or 'config' in f or 'stats' in f or 'sessions' in f:
                return {}
            return None
        
        with open(abs_path, 'r', encoding='utf-8') as file:
            return json.load(file)
    except:
        if 'history' in f or 'queue' in f or 'servers' in f:
            return []
        elif 'maintenance' in f or 'config' in f or 'stats' in f or 'sessions' in f:
            return {}
        return None

def save_json(f, data):
    try:
        data_dir = os.environ.get(
            'MAINTENANCE_DATA_DIR',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'),
        )
        abs_path = os.path.join(data_dir, f)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        
        with open(abs_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur save_json: {e}")

def get_user_guilds(access_token):
    """Récupère les serveurs Discord où l'utilisateur est owner ou admin.

    Renvoie :
      - une liste de serveurs (peut être vide si aucun serveur admin/owner)
      - `None` si le token Discord est invalide/expiré (l'appelant doit
        signaler à l'utilisateur de se reconnecter).
    """
    if not access_token:
        return None
    try:
        resp = requests.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except Exception as e:
        print(f"⚠️ get_user_guilds: erreur réseau Discord: {e}")
        return None

    if resp.status_code == 401:
        print("⚠️ get_user_guilds: token Discord expiré/invalide (401), reconnexion requise")
        return None
    if resp.status_code == 429:
        print("⚠️ get_user_guilds: rate limit Discord (429)")
        return None
    if resp.status_code != 200:
        print(f"⚠️ get_user_guilds: HTTP {resp.status_code} — {resp.text[:200]}")
        return []

    try:
        guilds = resp.json()
    except Exception as e:
        print(f"⚠️ get_user_guilds: JSON invalide: {e}")
        return []
    if not isinstance(guilds, list):
        return []

    bot_servers = load_json('all_bot_servers.json')
    bot_ids = set()
    if isinstance(bot_servers, list):
        bot_ids = {str(s.get('id', '')) for s in bot_servers}

    eligible = []
    for guild in guilds:
        try:
            permissions = int(guild.get('permissions', 0))
        except (TypeError, ValueError):
            permissions = 0
        is_admin = bool(permissions & 0x8)
        is_owner = bool(guild.get('owner', False))
        has_manage_guild = bool(permissions & 0x20)
        # Owners ont implicitement toutes les perms — Discord devrait
        # toujours renvoyer le bit admin pour eux, mais on vérifie aussi
        # le flag `owner` au cas où, pour ne jamais rater un serveur qu'ils
        # possèdent. MANAGE_GUILD (0x20) est aussi nécessaire pour ajouter un bot.
        if is_admin or is_owner or has_manage_guild:
            guild['in_guild'] = str(guild.get('id', '')) in bot_ids
            guild['is_owner'] = is_owner
            eligible.append(guild)

    eligible.sort(key=lambda g: (not g['in_guild'], g.get('name', '').lower()))
    return eligible

def require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session or not session.get('user', {}).get('id'):
            return redirect(url_for('login'))
        
        # Vérification spéciale pour le panel admin
        if request.path.startswith('/admin-dashboard') or request.path.startswith('/admin') or request.path.startswith('/api/admin/'):
            user = session.get('user', {})
            user_id = str(user.get('id', ''))
            # matix OWNER_ID: 1140296215469240401 OU session temp admin
            is_temp_admin = bool(user.get('temp_session_id') and user.get('permission') == 'admin')
            is_owner = (user_id == '1140296215469240401') or is_temp_admin
            
            # Mise à jour forcée de is_owner dans la session
            if user.get('is_owner') != is_owner:
                user['is_owner'] = is_owner
                session['user'] = user
                
            if not is_owner:
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Forbidden'}), 403
                return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated

def require_owner(f):
    """Vérifie que c'est la vraie session admin (is_owner), pas une session temporaire"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user', {}).get('is_owner'):
            return jsonify({'error': 'Action reserved for admin only (not temporary sessions)'}), 403
        return f(*args, **kwargs)
    return decorated

def require_write_access(f):
    """Vérifie l'accès en écriture (bloque les sessions read_only)"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('user', {}).get('is_read_only'):
            return jsonify({'error': 'Read-only access (temporary session)'}), 403
        if not session.get('user', {}).get('is_owner'):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated

@app.before_request
def track_request():
    """Middleware pour tracker les visites sur les pages principales"""
    path = request.path
    # Tracker certaines pages clés uniquement
    if path in ['/home', '/admin', '/dashboard'] or path.startswith('/user/'):
        page_name = path.split('/')[-1] or 'home'
        track_visit(page_name)

def log_modification(action, details, log_type='config', server_id=None):
    """Enregistre les modifications du site dans les logs Discord avec MEILLEUR formatage"""
    try:
        if not DISCORD_TOKEN:
            return
        
        # 📝 Enregistrer localement TOUJOURS
        logs = load_json('site_logs.json')
        logs[str(int(time.time() * 1000))] = {
            'action': action,
            'details': details,
            'log_type': log_type,
            'timestamp': datetime.now().isoformat()
        }
        save_json('site_logs.json', logs)
        
        # 🔧 Déterminer où envoyer le log Discord
        channel_id = LOGS_CHANNEL_ID  # Par défaut canal global
        
        # Si serveur_id est fourni, utiliser le canal logs du serveur
        if server_id:
            from utils.server_config_store import load_one as _load_server_cfg
            server_config = _load_server_cfg(server_id)
            server_log_channel = server_config.get('log_channel')
            if server_log_channel:
                channel_id = server_log_channel  # Utiliser le canal du serveur si configuré
        
        # 🎨 EMBED AMÉLIORÉ avec couleurs selon l'action
        color = 0x667eea  # Défaut: bleu Discord
        
        if 'ajout' in action.lower() or 'ajouté' in action.lower():
            color = 0x00ff00  # Vert
        elif 'retrait' in action.lower() or 'retiré' in action.lower() or 'suppression' in action.lower():
            color = 0xff0000  # Rouge
        elif 'modification' in action.lower() or 'modifié' in action.lower():
            color = 0xffa500  # Orange
        
        # Envoyer à Discord
        import requests
        embed_json = {
            'embeds': [{
                'title': f'🔧 {action}',
                'description': details,
                'color': color,
                'timestamp': datetime.now().isoformat(),
                'footer': {'text': f'Type: {log_type}'}
            }]
        }
        
        if channel_id:
            requests.post(
                f'https://discord.com/api/v10/channels/{channel_id}/messages',
                json=embed_json,
                headers={'Authorization': f'Bot {DISCORD_TOKEN}'}
            )
    except Exception as e:
        print(f"Erreur log_modification: {e}")

@app.route('/')
def index():
    return redirect(url_for('home'))

@app.route('/home')
def home():
    track_visit('home')
    return render_template('home.html')

@app.route('/status')
def status():
    """Affiche le statut des maintenances actuelles"""
    maint = load_json('maintenance.json')
    
    # 3 états pour le bot
    import subprocess
    bot_running = False
    try:
        # On utilise pgrep -f pour trouver le processus python qui tourne main.py
        result = subprocess.run(['pgrep', '-f', 'python.*main.py'], capture_output=True, text=True, timeout=1)
        bot_running = (result.returncode == 0)
    except:
        bot_running = False

    response = make_response(render_template('status.html', 
        maint=maint,
        bot_running=bot_running
    ))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/login')
@app.route('/login-discord')
def login():
    """Initie le flux OAuth2 Discord"""
    redirect_uri = get_redirect_uri()
    session['oauth_redirect_uri'] = redirect_uri
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'identify guilds'
    }
    auth_url = "https://discord.com/api/v10/oauth2/authorize?" + urlencode(params)
    return redirect(auth_url)

@app.route('/temp-login', methods=['GET', 'POST'])
def temp_login():
    """Connexion via session temporaire (ID + mot de passe fournis par l'admin)"""
    error = None
    if request.method == 'POST':
        input_id = request.form.get('session_id', '').strip().upper()
        input_pwd = request.form.get('password', '').strip()

        sessions = load_json('temp_sessions.json')
        if not isinstance(sessions, list):
            sessions = []

        # Mettre à jour les statuts (pending → active si 2 min écoulées)
        now = datetime.now()
        for s in sessions:
            if s.get('status') == 'pending':
                try:
                    active_at = datetime.fromisoformat(s['active_at'])
                    if now >= active_at:
                        s['status'] = 'active'
                except Exception:
                    pass
        save_json('temp_sessions.json', sessions)

        # Chercher la session correspondante
        matched = next((s for s in sessions
                        if s.get('session_id') == input_id
                        and s.get('password') == input_pwd
                        and s.get('status') == 'active'), None)

        if matched:
            perm = matched.get('permission', 'read_only')
            is_admin_session = (perm == 'admin')
            session.permanent = True
            session['user'] = {
                'id': f"temp_{input_id}",
                'username': f"Session {input_id}",
                'is_owner': is_admin_session,
                'is_read_only': not is_admin_session,
                'avatar': None,
                'access_token': None,
                'temp_session_id': input_id,
                'permission': perm
            }
            if is_admin_session:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))

        elif any(s.get('session_id') == input_id and s.get('password') == input_pwd
                 and s.get('status') == 'pending' for s in sessions):
            error = "Session non encore active — attends encore quelques minutes."
        elif any(s.get('session_id') == input_id and s.get('password') == input_pwd
                 and s.get('status') == 'closed' for s in sessions):
            error = "Cette session a été fermée par l'administrateur."
        else:
            error = "ID ou mot de passe incorrect."

    return render_template('temp_login.html', error=error)

def render_oauth_error(title, message, details=None, status_code=400):
    return render_template(
        'oauth_error.html',
        title=title,
        message=message,
        details=details,
        redirect_uri=get_redirect_uri()
    ), status_code

@app.route('/callback')
def callback():
    """Callback OAuth2 Discord"""
    code = request.args.get('code')
    error = request.args.get('error')
    error_description = request.args.get('error_description')
    
    if error:
        return render_oauth_error(
            "Connexion Discord refusée",
            "Discord a refusé la connexion. Vérifie que l'URL de redirection est bien ajoutée dans le portail Discord du bot.",
            error_description or error
        )
    
    if not code:
        return render_oauth_error(
            "Code Discord manquant",
            "La connexion Discord n'a pas renvoyé de code valide. Réessaie depuis le bouton de connexion."
        )
    
    try:
        redirect_uri = session.get('oauth_redirect_uri') or get_redirect_uri()
        # Échanger le code pour un token
        token_response = requests.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                'client_id': DISCORD_CLIENT_ID,
                'client_secret': DISCORD_CLIENT_SECRET,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
                'scope': 'identify guilds'
            },
            headers={
                'Content-Type': 'application/x-www-form-urlencoded'
            }
        )
        
        if token_response.status_code != 200:
            print(f"❌ OAuth token exchange failed ({token_response.status_code}): {token_response.text[:300]}")
            return render_oauth_error(
                "Erreur OAuth Discord",
                "Discord n'a pas accepté le callback du site. L'URL de redirection ci-dessous doit être autorisée dans le portail Discord.",
                f"HTTP {token_response.status_code}"
            )
        
        token_data = token_response.json()
        access_token = token_data.get('access_token')
        
        if not access_token:
            return render_oauth_error(
                "Token Discord manquant",
                "Discord a répondu sans token de connexion. Réessaie dans quelques secondes."
            )
        
        # Récupérer les infos utilisateur
        user_response = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if user_response.status_code != 200:
            return render_oauth_error(
                "Profil Discord inaccessible",
                "La connexion a été acceptée, mais le profil Discord n'a pas pu être récupéré.",
                f"HTTP {user_response.status_code}"
            )
        
        user_data = user_response.json()
        user_id = str(user_data.get('id'))
        
        # matix OWNER_ID: 1140296215469240401
        is_owner = (user_id == '1140296215469240401')
        
        # Crée la session
        session['user'] = {
            'id': user_id,
            'username': user_data.get('username'),
            'avatar': user_data.get('avatar'),
            'is_owner': is_owner,
            'access_token': access_token
        }
        session.permanent = True
        
        return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"❌ OAuth callback error: {e}")
        return render_oauth_error(
            "Erreur de connexion",
            "Une erreur est arrivée pendant la connexion Discord. Réessaie ou vérifie l'URL de redirection affichée ci-dessous.",
            str(e),
            500
        )

@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    """Page de connexion spécifique pour le panel admin avec sécurité renforcée"""
    error = None
    if request.method == 'POST':
        admin_id = request.form.get('admin_id')
        admin_pwd = request.form.get('admin_pwd')
        
        # matix.yt / matix-off@
        if admin_id == 'matix.yt' and admin_pwd == 'matix-off@':
            session.permanent = True
            session['admin_authenticated'] = True
            # Toujours reconstruire le dict user complètement pour forcer session.modified
            existing = session.get('user') or {}
            session['user'] = {
                'id': '1140296215469240401',
                'username': existing.get('username', 'matix'),
                'is_owner': True,
                'avatar': existing.get('avatar'),
                'access_token': existing.get('access_token')
            }
            session.modified = True
            
            log_modification(
                "Connexion Admin", 
                "L'administrateur matix s'est connecté au panel de contrôle.",
                log_type='security'
            )
            return redirect(url_for('admin_dashboard'))
        else:
            # Petit délai pour éviter le brute force
            time.sleep(1)
            error = "Identifiants administrateur incorrects ou accès refusé."
            
    return render_template('admin_login.html', error=error)

@app.route('/admin-dashboard')
def admin_dashboard():
    # Admin panel TOUJOURS accessible même en maintenance!
    if 'user' not in session or not session.get('user', {}).get('id'):
        # On ne redirige pas vers login normal, mais vers admin-login
        return redirect(url_for('admin_login'))
    
    user = session.get('user', {})
    
    # matix OWNER_ID: 1140296215469240401 OR Password Auth
    user_id = str(user.get('id', ''))
    is_authenticated = session.get('admin_authenticated', False)
    
    # Doit être matix authentifié OU une session temporaire admin
    is_temp_admin = bool(user.get('temp_session_id') and user.get('permission') == 'admin')
    if not is_temp_admin and (user_id != '1140296215469240401' or not is_authenticated):
        return redirect(url_for('admin_login'))
    
    # Afficher une alerte si maintenance programmée (mais on laisse accéder)
    maint = load_json('maintenance.json')
    template = render_template('admin.html', user=user)
    
    # Vérifier maintenance programmée - Afficher une alerte
    if maint.get('scheduled'):
        import datetime
        try:
            scheduled_time = datetime.datetime.fromisoformat(maint.get('scheduled_time', '').replace('Z', '+00:00'))
            now = datetime.datetime.now(datetime.timezone.utc) if maint.get('scheduled_time', '').endswith('Z') else datetime.datetime.now()
            if scheduled_time > now:
                minutes_left = int((scheduled_time - now).total_seconds() / 60)
                # Afficher alerte dans le panel
                alert_html = f"""
                <div style="position: fixed; top: 0; left: 0; right: 0; background: #ff9800; color: white; padding: 1rem; text-align: center; z-index: 10000; font-weight: bold;">
                    ⏰ Maintenance programmée dans {minutes_left} minutes! - {maint.get('message', 'Maintenance prévue')}
                </div>
                <script>document.body.style.marginTop = '60px';</script>
                """
                return alert_html + template
        except:
            pass
    
    return template

@app.route('/docs')
def docs():
    """Docs TOUJOURS accessibles même en maintenance"""
    return render_template('docs.html')

@app.route('/about')
def about():
    """Page À propos"""
    return render_template('about.html')

@app.route('/support')
def support():
    """Support TOUJOURS accessible même en maintenance"""
    return render_template('support.html')

@app.route('/premium')
def premium():
    """Page Premium TOUJOURS accessible même en maintenance"""
    return render_template('premium.html')

@app.route('/logout')
def logout():
    """Déconnexion complète - efface la session Discord et Admin"""
    access_token = session.get('user', {}).get('access_token')
    
    if access_token and DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET:
        try:
            requests.post(
                "https://discord.com/api/v10/oauth2/token/revoke",
                data={
                    'token': access_token,
                    'client_id': DISCORD_CLIENT_ID,
                    'client_secret': DISCORD_CLIENT_SECRET
                }
            )
        except:
            pass
    
    session.clear()
    session['admin_authenticated'] = False # Double sécurité
    response = make_response(redirect(url_for('home')))
    response.delete_cookie('session')
    return response

@app.route('/api/user/premium')
@require_login
def api_user_premium():
    """Retourne le statut Premium des serveurs de l'utilisateur (Premium par serveur uniquement).

    Le bloc `user` est conservé pour compatibilité du front mais ne reflète
    AUCUN tier Premium — le Premium fonctionne uniquement par serveur. Le
    flag `is_owner` indique simplement si l'utilisateur est propriétaire
    du bot (accès aux commandes /premium-admin), sans aucun privilège
    Premium associé.
    """
    try:
        from utils.premium import (
            get_guild_tier,
            get_record,
            is_free_premium_guild,
            TIER_LABELS,
            TIER_FREE,
            OWNER_IDS,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    user = session.get('user', {})
    user_id_str = str(user.get('id', ''))
    if not user_id_str.isdigit():
        return jsonify({'error': 'invalid user'}), 400
    user_id = int(user_id_str)

    is_owner = user_id in OWNER_IDS

    # Récupère les guildes admin de l'utilisateur
    access_token = user.get('access_token')
    guilds_info = {}
    if access_token:
        guilds = get_user_guilds(access_token)
        if guilds is None:
            guilds = []
        for g in guilds:
            try:
                gid = int(g.get('id'))
            except (TypeError, ValueError):
                continue
            tier = get_guild_tier(gid)
            grec = get_record(gid, 'guild')
            guilds_info[str(gid)] = {
                'tier': tier,
                'tier_label': TIER_LABELS.get(tier, tier),
                'is_premium': tier != TIER_FREE,
                'is_free_premium': is_free_premium_guild(gid),
                'expires': grec.get('expires') if grec else None,
                'granted_by': str(grec.get('granted_by', '')) if grec else '',
            }

    response = jsonify({
        'user': {
            'id': str(user_id),
            'tier': TIER_FREE,
            'tier_label': TIER_LABELS.get(TIER_FREE),
            'is_premium': False,
            'is_owner': is_owner,
            'expires': None,
            'since': None,
        },
        'guilds': guilds_info,
    })
    # Empêche le cache navigateur — le statut Premium peut changer à tout
    # moment (grant/revoke via /premium-admin) et doit toujours refléter
    # l'état réel du fichier premium.json.
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/servers/presence')
@require_login
def api_servers_presence():
    """Retourne uniquement les IDs des serveurs où le bot est présent (lecture locale, pas d'appel Discord)"""
    bot_servers = load_json('all_bot_servers.json')
    bot_ids = []
    if isinstance(bot_servers, list):
        bot_ids = [str(s.get('id', '')) for s in bot_servers if s.get('id')]
    return jsonify({'bot_ids': bot_ids})

@app.route('/api/servers')
@require_login
def api_servers():
    """Récupère les serveurs où l'utilisateur est owner ou admin.

    Renvoie 401 si le token Discord est expiré pour que le frontend puisse
    afficher un message « Reconnecte-toi avec Discord » au lieu d'une liste
    vide silencieuse.
    """
    access_token = session.get('user', {}).get('access_token')
    if not access_token:
        return jsonify({'error': 'discord_session_expired',
                        'message': 'Session Discord expirée, reconnecte-toi.'}), 401
    guilds = get_user_guilds(access_token)
    if guilds is None:
        return jsonify({'error': 'discord_session_expired',
                        'message': 'Session Discord expirée, reconnecte-toi.'}), 401
    return jsonify(guilds)

@app.route('/api/server/<gid>/premium-tier')
@require_login
def api_server_premium_tier(gid):
    """Retourne le tier Premium actuel d'un serveur (pour la page d'achat)."""
    if not _check_server_access(gid):
        return jsonify({'error': 'Accès refusé'}), 403
    try:
        from utils.premium import get_guild_tier, TIER_FREE, TIER_PREMIUM, TIER_PREMIUM_PLUS
        tier = get_guild_tier(int(gid))
        return jsonify({
            'tier': tier,
            'is_free': tier == TIER_FREE,
            'is_premium': tier == TIER_PREMIUM,
            'is_premium_plus': tier == TIER_PREMIUM_PLUS,
        })
    except Exception:
        return jsonify({'tier': 'free', 'is_free': True, 'is_premium': False, 'is_premium_plus': False})

@app.route('/api/admin/servers')
@require_login
def api_admin_servers():
    """Retourne TOUS les serveurs depuis all_bot_servers.json"""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    print("✅ API Admin: Retour des 14 serveurs depuis all_bot_servers.json")
    servers = load_json('all_bot_servers.json')
    if not isinstance(servers, list):
        servers = []
    return jsonify(servers)

@app.route('/api/admin/premium-subscriptions')
@require_login
def api_admin_premium_subscriptions():
    """Retourne tous les abonnements Premium actifs et inactifs pour le panel admin."""
    user = session.get('user', {})
    user_id = str(user.get('id', ''))
    is_authenticated = session.get('admin_authenticated', False)
    is_temp_admin = bool(user.get('temp_session_id') and user.get('permission') == 'admin')
    if not is_temp_admin and (user_id != '1140296215469240401' or not is_authenticated):
        return jsonify({'error': 'Forbidden'}), 403

    try:
        premium_file = os.path.join(_PROJECT_ROOT, 'web', 'data', 'premium.json')
        with open(premium_file, 'r', encoding='utf-8') as f:
            premium_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        premium_data = {}

    guilds = premium_data.get('guilds', {})
    from utils.premium import TIER_LABELS, FREE_PREMIUM_GUILDS
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc)
    subscriptions = []

    for guild_id, record in guilds.items():
        tier = record.get('tier', 'free')
        expires_raw = record.get('expires')
        since_raw = record.get('since')
        granted_by = str(record.get('granted_by', ''))
        payment_method = record.get('payment_method') or record.get('payment_provider') or 'Manuel'
        billing_period = record.get('billing_period') or record.get('billing') or '—'
        cancelled = record.get('cancelled', False)

        is_free_guild = int(guild_id) in FREE_PREMIUM_GUILDS if guild_id.isdigit() else False

        status = 'active'
        is_expired = False
        is_late = False

        if is_free_guild:
            status = 'free_gift'
        elif cancelled:
            status = 'cancelled'
        elif expires_raw:
            try:
                exp_dt = datetime.fromisoformat(expires_raw.replace('Z', '+00:00'))
                if exp_dt < now:
                    status = 'expired'
                    is_expired = True
                elif (exp_dt - now).days <= 3:
                    status = 'late'
                    is_late = True
            except (ValueError, AttributeError):
                pass

        subscriptions.append({
            'guild_id': guild_id,
            'user_id': granted_by,
            'tier': tier,
            'tier_label': TIER_LABELS.get(tier, tier),
            'payment_method': payment_method,
            'billing_period': billing_period,
            'since': since_raw,
            'expires': expires_raw,
            'cancelled': cancelled,
            'is_expired': is_expired,
            'is_late': is_late,
            'is_free_gift': is_free_guild,
            'status': status,
        })

    subscriptions.sort(key=lambda x: (0 if x['status'] == 'active' else 1, x['since'] or ''), reverse=False)

    response = jsonify({'subscriptions': subscriptions})
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

def _check_server_access(gid):
    """Vérifie si l'utilisateur connecté a accès à ce serveur (admin ou propriétaire bot)."""
    user = session.get('user', {})
    if user.get('is_owner', False):
        return True
    access_token = user.get('access_token')
    if not access_token:
        return False
    guilds = get_user_guilds(access_token) or []
    return str(gid) in [str(g.get('id')) for g in guilds]

@app.route('/api/server/<gid>/config', methods=['GET'])
@require_login
def api_server_config(gid):
    """Récupère la config d'un serveur (admin uniquement) + statut Premium du serveur."""
    if not _check_server_access(gid):
        return jsonify({'error': 'Accès refusé — permissions insuffisantes'}), 403
    from utils.server_config_store import load_one as _load_server_cfg
    server_config = _load_server_cfg(gid)
    if not isinstance(server_config, dict):
        server_config = {}

    # Joint le statut Premium du serveur (modèle Premium par serveur
    # uniquement, depuis v13.0.0) pour permettre au front de verrouiller/
    # déverrouiller les champs Premium. Les OWNER_IDS du bot ne bypassent
    # PAS le gating Premium — seul le tier réel du serveur compte.
    premium_info = {'tier': 'free', 'is_premium': False, 'is_premium_plus': False}
    try:
        from utils.premium import (
            get_guild_tier,
            is_free_premium_guild,
            TIER_FREE, TIER_PREMIUM, TIER_PREMIUM_PLUS, TIER_LABELS,
        )
        gid_int = int(gid)
        guild_tier = get_guild_tier(gid_int)
        premium_info = {
            'tier': guild_tier,
            'tier_label': TIER_LABELS.get(guild_tier, guild_tier),
            'is_premium': guild_tier != TIER_FREE,
            'is_premium_plus': guild_tier == TIER_PREMIUM_PLUS,
            'is_free_premium': is_free_premium_guild(gid_int),
            # Source toujours 'guild' (Premium par serveur uniquement). 'none' si Free.
            'source': 'guild' if guild_tier != TIER_FREE else 'none',
            'guild_tier': guild_tier,
        }
    except Exception as e:
        print(f"[premium_info] Erreur calcul tier: {e}")

    response = jsonify({**server_config, '_premium': premium_info})
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/server/<gid>/config', methods=['POST'])
@require_login
def api_server_config_save(gid):
    """Sauvegarde la config d'un serveur (filtre les champs Premium si serveur non Premium)."""
    user = session.get('user', {})

    if user.get('is_read_only'):
        return jsonify({'error': 'Read-only session cannot save configurations'}), 403

    if not _check_server_access(gid):
        return jsonify({'error': 'Accès refusé — permissions insuffisantes'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Retire la clé miroir _premium éventuellement renvoyée par le front
    data.pop('_premium', None)

    # Filtrage Premium côté serveur (le serveur est l'autorité, pas le front)
    rejected_premium_keys: list = []
    user_id_str = str(user.get('id', ''))
    user_id_int = int(user_id_str) if user_id_str.isdigit() else 0
    try:
        from utils.premium import filter_premium_config
        data, rejected_premium_keys = filter_premium_config(data, int(gid), user_id_int)
    except Exception:
        pass
    
    from utils.server_config_store import load_one as _load_server_cfg, save_one as _save_server_cfg
    existing = _load_server_cfg(gid)
    if not isinstance(existing, dict):
        existing = {}

    # 🛡 Normalisation/validation des sections sensibles avant écriture.
    # Évite qu'une valeur vide ou non typée corrompe le JSON et fasse crasher
    # le bot (ex : int(None) dans on_member_join du cog anti_raid).
    if 'anti_raid' in data and isinstance(data['anti_raid'], dict):
        ar = data['anti_raid']
        ALLOWED_AR_ACTIONS = {'lockdown', 'kick', 'ban'}
        def _clamp_int(v, lo, hi, default):
            try:
                n = int(v)
            except (TypeError, ValueError):
                return default
            return max(lo, min(hi, n))
        clean_ar = {}
        if 'enabled' in ar:
            clean_ar['enabled'] = bool(ar.get('enabled'))
        if 'threshold' in ar:
            clean_ar['threshold'] = _clamp_int(ar.get('threshold'), 2, 100, 5)
        if 'window' in ar:
            clean_ar['window'] = _clamp_int(ar.get('window'), 5, 600, 10)
        if 'min_account_age_days' in ar:
            clean_ar['min_account_age_days'] = _clamp_int(ar.get('min_account_age_days'), 0, 365, 7)
        if 'action' in ar:
            act = str(ar.get('action') or '').lower()
            clean_ar['action'] = act if act in ALLOWED_AR_ACTIONS else 'lockdown'
        if 'log_channel_id' in ar:
            v = ar.get('log_channel_id')
            try:
                clean_ar['log_channel_id'] = int(v) if v not in (None, '', 0) else None
            except (TypeError, ValueError):
                clean_ar['log_channel_id'] = None
        data['anti_raid'] = clean_ar

    if data.get('reset'):
        new_cfg = {}
    else:
        new_cfg = dict(existing)
        # Fusionner les objets imbriqués pour éviter d'écraser les sections non envoyées
        for key, value in data.items():
            if isinstance(value, dict) and key in new_cfg and isinstance(new_cfg.get(key), dict):
                merged = dict(new_cfg[key])
                merged.update(value)
                new_cfg[key] = merged
            else:
                new_cfg[key] = value

    try:
        _save_server_cfg(gid, new_cfg)
    except Exception as save_err:
        # En cas de panne PostgreSQL, on remonte une vraie erreur 500 au front
        # plutôt qu'un faux succès. Le dashboard affiche depuis la v13.1.3 le
        # message exact de la réponse JSON dans une alerte.
        from utils.server_config_store import ServerConfigStoreError
        if isinstance(save_err, ServerConfigStoreError):
            return jsonify({'error': f'Sauvegarde impossible : {save_err}'}), 503
        print(f"[api_server_config_save] save inattendu KO: {save_err}")
        return jsonify({'error': f'Sauvegarde échouée : {save_err}'}), 500
    
    # 🔄 Synchroniser avec le bot (vider le cache du bot)
    try:
        import requests
        # On notifie le bot via un log ou un signal si possible
        # Ici on force le rafraîchissement des logs pour que l'utilisateur voit le changement
        log_modification(
            "Mise à jour Configuration", 
            f"La configuration du serveur {gid} a été modifiée via le Dashboard.",
            log_type='config',
            server_id=gid
        )
    except:
        pass
        
    return jsonify({'success': True, 'rejected_premium_keys': rejected_premium_keys})

@app.route('/api/server/<gid>/logs/search')
@require_login
def api_server_logs_search(gid):
    """
    Recherche dans les mod-logs persistés (Premium uniquement).
    Filtres : q (texte libre), type (mod|automod|all), action,
              user_id, since (ISO 8601), until (ISO 8601), limit (max 500).
    """
    if not _check_server_access(gid):
        return jsonify({'error': 'Accès refusé — permissions insuffisantes'}), 403

    # Gating Premium
    try:
        from utils.premium import get_guild_tier, TIER_FREE
        if get_guild_tier(int(gid)) == TIER_FREE:
            return jsonify({'error': 'premium_required',
                            'message': 'Cette fonctionnalité requiert Premium.'}), 402
    except Exception:
        return jsonify({'error': 'premium_check_failed'}), 500

    # Lecture du fichier de logs
    path = os.path.join(_PROJECT_ROOT, 'web', 'data', 'mod_logs', f'{gid}.json')
    logs = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if isinstance(payload, dict) and isinstance(payload.get('logs'), list):
            logs = payload['logs']
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        logs = []

    # Filtres
    q = (request.args.get('q') or '').strip().lower()
    log_type = (request.args.get('type') or 'all').strip().lower()
    action = (request.args.get('action') or '').strip().lower()
    user_id = (request.args.get('user_id') or '').strip()
    since = (request.args.get('since') or '').strip()
    until = (request.args.get('until') or '').strip()
    try:
        limit = max(1, min(500, int(request.args.get('limit', 100))))
    except (TypeError, ValueError):
        limit = 100

    def _match(entry: dict) -> bool:
        if log_type in ('mod', 'automod') and entry.get('kind') != log_type:
            return False
        if action and (entry.get('action') or '').lower() != action:
            return False
        if user_id and str(entry.get('user_id') or '') != user_id:
            return False
        if since and (entry.get('ts') or '') < since:
            return False
        if until and (entry.get('ts') or '') > until:
            return False
        if q:
            haystack = ' '.join(str(v) for v in (
                entry.get('reason'),
                entry.get('user_name'),
                entry.get('moderator_name'),
                entry.get('action'),
                entry.get('content'),
                entry.get('channel_name'),
                entry.get('sanction_id'),
            ) if v).lower()
            if q not in haystack:
                return False
        return True

    # Du plus récent au plus ancien
    matches = [e for e in reversed(logs) if _match(e)][:limit]
    return jsonify({
        'total': len(matches),
        'limit': limit,
        'logs': matches,
    })


@app.route('/api/server/<gid>/stats/advanced')
@require_login
def api_server_stats_advanced(gid):
    """
    Stats avancées du serveur (Premium uniquement). Lecture seule.
    Retour : history (jusqu'à 30 jours), totals (cumuls 30j), tickets_30d,
    member_count, sanctions_30d_by_action.
    """
    if not _check_server_access(gid):
        return jsonify({'error': 'Accès refusé — permissions insuffisantes'}), 403

    try:
        from utils.premium import get_guild_tier, TIER_FREE
        if get_guild_tier(int(gid)) == TIER_FREE:
            return jsonify({'error': 'premium_required',
                            'message': 'Cette fonctionnalité requiert Premium.'}), 402
    except Exception:
        return jsonify({'error': 'premium_check_failed'}), 500

    # 1) Historique journalier
    try:
        from utils.stats_daily import read_history
        history = read_history(gid, days=30)
    except Exception:
        history = []

    # 2) Sanctions 30j par action (depuis mod_logs)
    sanctions_by_action: dict = {}
    sanctions_30d_total = 0
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff_iso = (_dt.now(_tz.utc) - _td(days=30)).isoformat()
        path = os.path.join(_PROJECT_ROOT, 'web', 'data', 'mod_logs', f'{gid}.json')
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        for entry in (payload.get('logs') or []):
            ts = entry.get('ts') or ''
            if ts < cutoff_iso:
                continue
            action = (entry.get('action') or 'autre').lower()
            sanctions_by_action[action] = sanctions_by_action.get(action, 0) + 1
            sanctions_30d_total += 1
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # 3) Tickets ouverts/fermés 30j
    tickets_open = 0
    tickets_30d = 0
    try:
        from datetime import datetime as _dt2, timedelta as _td2, timezone as _tz2
        tcutoff = (_dt2.now(_tz2.utc) - _td2(days=30)).isoformat()
        with open(os.path.join(_PROJECT_ROOT, 'web', 'data', 'tickets.json'),
                  'r', encoding='utf-8') as f:
            tdata = json.load(f) or {}
        gcfg = tdata.get(str(gid), {}) or {}
        for t in (gcfg.get('tickets') or {}).values():
            opened = t.get('opened_at') or ''
            if opened >= tcutoff:
                tickets_30d += 1
            if t.get('status') == 'open':
                tickets_open += 1
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # 4) Member count (depuis dernière entrée history) + total messages 30j
    member_count = history[-1].get('members', 0) if history else 0
    messages_30d = sum(int(e.get('messages', 0) or 0) for e in history)

    return jsonify({
        'history': history,
        'member_count': member_count,
        'messages_30d': messages_30d,
        'sanctions_30d': sanctions_30d_total,
        'sanctions_by_action': sanctions_by_action,
        'tickets_open': tickets_open,
        'tickets_30d': tickets_30d,
    })


@app.route('/api/server/<gid>/channels')
@require_login
def api_server_channels(gid):
    """Récupère les canaux d'un serveur (admin uniquement)"""
    if not _check_server_access(gid):
        return jsonify({'error': 'Accès refusé'}), 403
    if not DISCORD_TOKEN:
        return jsonify({'channels': []}), 400
    
    try:
        url = f"https://discord.com/api/v10/guilds/{gid}/channels"
        headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
        resp = requests.get(url, headers=headers)
        
        if resp.status_code == 200:
            channels = resp.json()
            return jsonify({'channels': channels})
        return jsonify({'channels': []}), 400
    except:
        return jsonify({'channels': []}), 400

@app.route('/api/server/<gid>/roles')
@require_login
def api_server_roles(gid):
    """Récupère les rôles d'un serveur (admin uniquement)"""
    if not _check_server_access(gid):
        return jsonify({'error': 'Accès refusé'}), 403
    if not DISCORD_TOKEN:
        return jsonify({'roles': []}), 400
    
    try:
        url = f"https://discord.com/api/v10/guilds/{gid}/roles"
        headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
        resp = requests.get(url, headers=headers)
        
        if resp.status_code == 200:
            roles = resp.json()
            return jsonify({'roles': roles})
        return jsonify({'roles': []}), 400
    except:
        return jsonify({'roles': []}), 400

@app.route('/api/submit-feedback', methods=['POST'])
def submit_feedback():
    """Reçoit un rapport ou une suggestion depuis le web"""
    try:
        data = request.get_json()
        feedback_type = data.get('type', 'report')  # 'report' ou 'suggestion'
        user_name = data.get('user_name') or data.get('discord') or 'Utilisateur Web'
        user_avatar = data.get('user_avatar', 'https://cdn.discordapp.com/embed/avatars/0.png')
        message = data.get('message') or data.get('description') or 'Pas de message'
        category = data.get('category', 'Général')
        
        if not DISCORD_TOKEN:
            return jsonify({'error': 'Bot not configured'}), 400
            
        channel_id = REPORT_CHANNEL_ID if feedback_type == 'report' else SUGGESTION_CHANNEL_ID
        
        # Embed stylisé
        color = 0xff0000 if feedback_type == 'report' else 0x7c3aed
        title = "🚨 Nouveau Rapport (Web)" if feedback_type == 'report' else "💡 Nouvelle Suggestion (Web)"
        
        embed = {
            'title': title,
            'color': color,
            'fields': [
                {'name': '👤 Auteur', 'value': f"**{user_name}**", 'inline': True},
                {'name': '📂 Catégorie', 'value': f"`{category}`", 'inline': True},
                {'name': '🌐 Source', 'value': '`Site Web`', 'inline': True},
                {'name': '💬 Message', 'value': message, 'inline': False}
            ],
            'thumbnail': {'url': user_avatar},
            'footer': {'text': 'Miaouscle Bot - Système de Feedback', 'icon_url': user_avatar},
            'timestamp': datetime.now().isoformat()
        }
        
        print(f"📡 Sending feedback to channel {channel_id} (type: {feedback_type})")
            
        resp = requests.post(
            f'https://discord.com/api/v10/channels/{channel_id}/messages',
            json={'embeds': [embed]},
            headers={'Authorization': f'Bot {DISCORD_TOKEN}'}
        )
        
        if resp.status_code in [200, 201]:
            print(f"✅ Feedback sent successfully")
            return jsonify({'success': True})
        else:
            print(f"❌ Discord API Error ({resp.status_code}): {resp.text}")
            return jsonify({'error': f"Discord API Error: {resp.status_code}"}), 500
            
    except Exception as e:
        print(f"❌ Submit feedback error: {e}")
        return jsonify({'error': str(e)}), 500
        if feedback_type == 'suggestion':
            channel_id = SUGGESTION_CHANNEL_ID
            title = "💡 Nouvelle Suggestion"
            color = 0x7c3aed  # Violet Premium
        else:
            channel_id = REPORT_CHANNEL_ID
            title = "🚨 Nouveau Signalement"
            color = 0xef4444  # Rouge
        
        embed = {
            'title': title,
            'description': f"```\n{message}\n```",
            'color': color,
            'author': {
                'name': f"Envoyé par {user_name}",
                'icon_url': user_avatar
            },
            'fields': [
                {'name': '👤 Utilisateur', 'value': f"`{user_name}`", 'inline': True},
                {'name': '📋 Catégorie', 'value': f"`{category}`", 'inline': True},
                {'name': '🌐 Source', 'value': "`Dashboard Web`", 'inline': True}
            ],
            'footer': {
                'text': f"Miaouscle Bot v{BOT_VERSION} • {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            },
            'timestamp': datetime.now().isoformat()
        }
        
        # S'assurer que les channel IDs sont des entiers
        try:
            target_channel = int(channel_id)
        except:
            target_channel = channel_id
            
        print(f"📡 Sending feedback to channel {target_channel} (type: {feedback_type})")
            
        resp = requests.post(
            f'https://discord.com/api/v10/channels/{target_channel}/messages',
            json={'embeds': [embed]},
            headers={'Authorization': f'Bot {DISCORD_TOKEN}'}
        )
        
        if resp.status_code in [200, 201]:
            print(f"✅ Feedback sent successfully")
            return jsonify({'success': True})
        else:
            print(f"❌ Discord API Error ({resp.status_code}): {resp.text}")
            return jsonify({'error': f"Discord API Error: {resp.status_code}"}), 500
            
    except Exception as e:
        print(f"❌ Submit feedback error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/maintenance')
def maintenance_page():
    """Affiche la page de maintenance - RENDU CÔTÉ SERVEUR pour éviter le clignotement"""
    import subprocess
    from flask import make_response
    
    # Vérifier si le bot Discord fonctionne réellement
    bot_online = True
    try:
        result = subprocess.run(['pgrep', '-f', 'python.*main.py'], capture_output=True, text=True, timeout=2)
        bot_online = result.returncode == 0
    except:
        bot_online = True
    
    # Le site est online
    site_online = True
    
    # Vérifier la maintenance
    maint = load_json('maintenance.json')
    maintenance_active = maint.get('active', False) if isinstance(maint, dict) else False
    maintenance_type = maint.get('type', 'both') if isinstance(maint, dict) else 'both'
    message = maint.get('message', '') if isinstance(maint, dict) else ''
    
    # Passer les données au template
    response = make_response(render_template('maintenance_page.html', 
                         bot_online=bot_online,
                         site_online=site_online,
                         maintenance_active=maintenance_active,
                         maintenance_type=maintenance_type,
                         message=message))
    
    # Pas de cache agressif - le contenu est généré côté serveur dynamiquement
    response.headers['Cache-Control'] = 'public, max-age=0'
    
    return response

@app.route('/api/v2/status', methods=['GET'])
def api_v2_status():
    """Route PUBLIQUE: Retourne le statut du bot et du site + maintenances programmées"""
    import subprocess
    
    # Lire maintenance.json (structure: {bot: {...}, site: {...}})
    maint_file = load_json('maintenance.json')
    if not isinstance(maint_file, dict):
        maint_file = {}
    
    bot_maint = maint_file.get('bot', {})
    site_maint = maint_file.get('site', {})
    
    bot_maintenance_active = bot_maint.get('active', False)
    site_maintenance_active = site_maint.get('active', False)
    
    # Vérifier si le bot Discord est running
    bot_running = False
    try:
        # On utilise pgrep -f pour trouver le processus python qui tourne main.py
        result = subprocess.run(['pgrep', '-f', 'python.*main.py'], capture_output=True, text=True, timeout=1)
        bot_running = (result.returncode == 0)
    except:
        bot_running = False
    
    # Le bot est online (opérationnel) s'il tourne ET n'est pas en maintenance
    bot_online = bot_running and not bot_maintenance_active
    
    # Le site est online s'il n'est pas en maintenance
    site_online = not site_maintenance_active
    
    # Déterminer le type de maintenance pour le frontend
    maintenance_type = 'none'
    if bot_maintenance_active and site_maintenance_active:
        maintenance_type = 'both'
    elif bot_maintenance_active:
        maintenance_type = 'bot'
    elif site_maintenance_active:
        maintenance_type = 'site'
    
    return jsonify({
        'maintenance_active': bot_maintenance_active or site_maintenance_active,
        'maintenance_type': maintenance_type,
        'bot_running': bot_running,
        'bot_online': bot_online,
        'site_online': site_online,
        'bot_data': bot_maint,
        'site_data': site_maint,
        'last_update': datetime.now().strftime('%H:%M:%S')
    })

@app.route('/api/maintenance-status', methods=['GET'])
def api_maintenance_status():
    """Route PUBLIQUE: Vérifie l'état de la maintenance.

    Le fichier utilise désormais des sections ``bot`` et ``site``. Garder
    une lecture de l'ancien format ici faisait répondre ``active: false``
    alors que le site était réellement en maintenance.
    """
    current = load_json('maintenance.json')
    if not isinstance(current, dict):
        return jsonify({'active': False, 'type': 'none'})

    if isinstance(current.get('bot'), dict) or isinstance(current.get('site'), dict):
        bot_maint = current.get('bot', {})
        site_maint = current.get('site', {})
        bot_active = bool(bot_maint.get('active', False))
        site_active = bool(site_maint.get('active', False))
        if bot_active and site_active:
            maint_type = 'both'
        elif bot_active:
            maint_type = 'bot'
        elif site_active:
            maint_type = 'site'
        else:
            maint_type = 'none'
        active_section = site_maint if site_active else bot_maint
        return jsonify({
            'active': bot_active or site_active,
            'type': maint_type,
            'message': active_section.get('message', ''),
            'bot_data': bot_maint,
            'site_data': site_maint,
        })

    # Compatibilité avec les anciens fichiers plats.
    return jsonify({
        'active': current.get('active', False),
        'type': current.get('type', 'both'),
        'message': current.get('message', '')
    })

@app.route('/api/admin/maintenance', methods=['GET', 'POST'])
@require_login
def api_admin_maintenance():
    """ENDPOINT SIMPLE: Gère la maintenance"""
    if request.method == 'POST' and session.get('user', {}).get('is_read_only'):
        return jsonify({'error': 'Read-only access (temporary session)'}), 403
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    import datetime
    
    if request.method == 'POST':
        data = request.get_json()
        
        # Charger l'historique
        history = load_json('maintenance_history.json')
        if not isinstance(history, list):
            history = []
        
        if data.get('active'):
            # ✅ ACTIVÉE IMMÉDIATEMENT
            maint_type = data.get('maint_type', 'both')  # 'site', 'bot', ou 'both'
            now = datetime.datetime.utcnow()
            # Si end_time non spécifié, mettre 1 heure par défaut
            end_time = data.get('end_time')
            if not end_time:
                end_time = (now + datetime.timedelta(hours=1)).isoformat() + 'Z'
            
            maint = {
                'type': 'active',
                'message': data.get('message', '🔧 Maintenance'),
                'start_time': now.isoformat() + 'Z',
                'end_time': end_time,
                'status': 'active',
                'maint_type': maint_type
            }
            # Sauvegarder dans NOUVELLE structure (préserver le bot)
            current = load_json('maintenance.json') or {'bot': {}, 'site': {}}
            if maint_type in ['site', 'both']:
                current['site'] = {
                    'active': True,
                    'message': data.get('message', '🔧 Maintenance'),
                    'start_time': maint['start_time'],
                    'end_time': maint['end_time'],
                    'show_reason': True
                }
            if maint_type in ['bot', 'both']:
                current['bot'] = {
                    'active': True,
                    'message': data.get('message', '🔧 Maintenance'),
                    'start_time': maint['start_time'],
                    'end_time': maint['end_time'],
                    'show_reason': True
                }
            save_json('maintenance.json', current)
            history.append(maint)
            
        elif data.get('scheduled'):
            # ✅ PROGRAMMÉE
            maint = {
                'type': 'scheduled',
                'message': data.get('message', '🔧 Maintenance programmée'),
                'scheduled_time': data.get('scheduled_time'),
                'end_time': data.get('end_time'),
                'status': 'scheduled'
            }
            print(f"📅 Adding scheduled: {maint}")
            history.append(maint)
            print(f"📅 History now has {len(history)} items: {history}")
            
        else:
            # ❌ DÉSACTIVÉE
            save_json('maintenance.json', {
                'bot': {'active': False, 'message': '', 'start_time': None, 'end_time': None, 'show_reason': True},
                'site': {'active': False, 'message': '', 'start_time': None, 'end_time': None, 'show_reason': True}
            })
        
        save_json('maintenance_history.json', history)
        print(f"✅ MAINTENANCE: {len(history)} items saved!")
        print(f"📊 Verification - reading back: {load_json('maintenance_history.json')}")
        return jsonify({'success': True})
    
    # GET - Retourner l'état actuel
    current = load_json('maintenance.json')
    return jsonify(current if isinstance(current, dict) else {'active': False})

@app.route('/api/admin/maintenance-timeline')
@require_login
def api_maintenance_timeline():
    """Retourne la timeline de maintenance - SIMPLE"""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    history = load_json('maintenance_history.json')
    if not isinstance(history, list):
        history = []
    
    # Séparer par type, en incluant l'index réel dans la liste complète
    active = []
    scheduled = []
    completed = []
    for idx, m in enumerate(history):
        item = dict(m)
        item['_history_idx'] = idx
        if m.get('type') == 'active' and m.get('status') == 'active':
            active.append(item)
        elif m.get('type') == 'scheduled' and m.get('status') == 'scheduled':
            scheduled.append(item)
        elif m.get('status') == 'completed':
            completed.append(item)
    timeline = {'active': active, 'scheduled': scheduled, 'completed': completed}
    
    print(f"📅 Timeline: {len(timeline['active'])} active, {len(timeline['scheduled'])} scheduled, {len(timeline['completed'])} completed")
    return jsonify(timeline)

@app.route('/api/admin/maintenance/mark-complete', methods=['POST'])
@require_login
def mark_maintenance_complete():
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    data = request.get_json()
    action_type = data.get('type')
    index = data.get('index')
    
    history = load_json('maintenance_history.json')
    if isinstance(history, list) and isinstance(index, int) and 0 <= index < len(history):
        import datetime
        item = history[index]
        if action_type == 'scheduled':
            # Lancer une maintenance programmée → la rendre active
            now = datetime.datetime.utcnow().isoformat() + 'Z'
            item['status'] = 'active'
            item['type'] = 'active'
            item['start_time'] = now
            # Mettre à jour maintenance.json
            current = load_json('maintenance.json') or {'bot': {}, 'site': {}}
            current['site'] = {
                'active': True,
                'message': item.get('message', '🔧 Maintenance'),
                'start_time': now,
                'end_time': item.get('end_time'),
                'show_reason': True
            }
            save_json('maintenance.json', current)
        else:
            # Terminer une maintenance active → la marquer complète
            item['status'] = 'completed'
            item['end_actual'] = datetime.datetime.now().isoformat()
            # Désactiver dans maintenance.json
            current = load_json('maintenance.json') or {'bot': {}, 'site': {}}
            current['site'] = {'active': False, 'message': '', 'start_time': None, 'end_time': None, 'show_reason': True}
            save_json('maintenance.json', current)
        save_json('maintenance_history.json', history)
        return jsonify({'success': True})
    
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/admin/maintenance/delete', methods=['POST'])
@require_login
def delete_maintenance():
    if session.get('user', {}).get('is_read_only'):
        return jsonify({'error': 'Read-only access (temporary session)'}), 403
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    data = request.get_json()
    index = data.get('index')
    
    history = load_json('maintenance_history.json')
    if isinstance(history, list) and isinstance(index, int) and 0 <= index < len(history):
        history.pop(index)
        save_json('maintenance_history.json', history)
        return jsonify({'success': True})
    
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/admin/maintenance/update', methods=['POST'])
@require_login
def update_maintenance():
    if session.get('user', {}).get('is_read_only'):
        return jsonify({'error': 'Read-only access (temporary session)'}), 403
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    data = request.get_json()
    index = data.get('index')
    new_datetime = data.get('datetime')
    new_message = data.get('message')
    
    history = load_json('maintenance_history.json')
    if isinstance(history, list) and isinstance(index, int) and 0 <= index < len(history):
        if new_datetime:
            history[index]['scheduled_time'] = new_datetime
            history[index]['end_time'] = new_datetime
        if new_message:
            history[index]['message'] = new_message
        save_json('maintenance_history.json', history)
        print(f"✏️ MODIFICATION: Maintenance #{index} modifiée")
        return jsonify({'success': True})
    
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/admin/broadcast', methods=['POST', 'GET'])
@require_login
def api_admin_broadcast():
    """Broadcast à tous les serveurs"""
    if request.method == 'POST' and session.get('user', {}).get('is_read_only'):
        return jsonify({'error': 'Read-only access (temporary session)'}), 403
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    if request.method == 'POST':
        data = request.get_json()
        title = data.get('title')
        message = data.get('message')
        
        # Lire les serveurs et envoyer le message à chacun
        servers = load_json('all_bot_servers.json')
        if not isinstance(servers, list):
            return jsonify({'error': 'No servers'}), 400
        
        queue_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'data', 'broadcast_queue.json')
        try:
            with open(queue_path, 'r', encoding='utf-8') as f:
                queue = json.load(f)
        except Exception:
            queue = []
        if not isinstance(queue, list):
            queue = []
        nb_servers = len(servers) if isinstance(servers, list) else 0
        queue.append({
            'title': title or 'Broadcast',
            'message': message,
            'status': 'pending',
            'created_at': datetime.now().isoformat()
        })
        os.makedirs(os.path.dirname(queue_path), exist_ok=True)
        with open(queue_path, 'w', encoding='utf-8') as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True, 'queued': True, 'servers_targeted': nb_servers})
    
    return jsonify({'broadcasts': []})

@app.route('/api/admin/leave-server/<gid>', methods=['POST'])
@require_login
def api_admin_leave_server(gid):
    """Quitter un serveur"""
    if session.get('user', {}).get('is_read_only'):
        return jsonify({'error': 'Read-only access (temporary session)'}), 403
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    if not DISCORD_TOKEN:
        return jsonify({'error': 'Bot not configured'}), 400
    
    try:
        requests.delete(
            f'https://discord.com/api/v10/users/@me/guilds/{gid}',
            headers={'Authorization': f'Bot {DISCORD_TOKEN}'}
        )
        return jsonify({'success': True})
    except:
        return jsonify({'error': 'Failed to leave'}), 500

@app.route('/api/admin/stats')
@require_login
def api_admin_stats():
    """Stats des serveurs"""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    servers = load_json('all_bot_servers.json')
    if not isinstance(servers, list):
        servers = []
    
    return jsonify({
        'total_servers': len(servers),
        'total_members': sum(s.get('members', 0) for s in servers),
        'servers': servers
    })

@app.route('/dashboard')
@app.route('/user/server/<gid>')
@require_login
def dashboard(gid=None):
    """Affiche le dashboard utilisateur (URL directe ou avec serveur pré-sélectionné)"""
    maint = load_json('maintenance.json')
    if maint.get('site', {}).get('active'):
        return redirect(url_for('home'))

    user = session.get('user', {})
    response = make_response(render_template('dashboard.html', user=user))
    # Pas de cache pour que les MAJ de l'UI (sections Premium, etc.) soient
    # immédiatement visibles après un redéploiement, sans hard-refresh.
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

def _parse_changelog_entries(max_versions: int = 3, max_items: int = 20,
                              user_facing_only: bool = True):
    """Parse CHANGELOG.md → liste de dicts orientés utilisateurs.

    - Garde seulement les changements visibles pour les utilisateurs (BOT, SITE, WEB, FEATURE).
    - Skippe les FIX/BACKEND (jargon technique sans intérêt pour les utilisateurs).
    - Titre court d'une ligne (≤ 90 chars), pas de description longue.
    - Sous-titre court de 1 phrase (≤ 110 chars) si une explication simple existe.
    """
    import re
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    changelog_path = os.path.join(project_root, 'CHANGELOG.md')
    if not os.path.exists(changelog_path):
        return None, []
    with open(changelog_path, 'r', encoding='utf-8') as f:
        content = f.read()

    CATEGORY_META = {
        'BOT':     {'icon': '🤖', 'label': 'Bot',      'color': '#5865f2'},
        'SITE':    {'icon': '🌐', 'label': 'Site',     'color': '#10b981'},
        'WEB':     {'icon': '🌐', 'label': 'Site',     'color': '#10b981'},
        'FEATURE': {'icon': '✨', 'label': 'Nouveau',  'color': '#a855f7'},
        'DEFAULT': {'icon': '📝', 'label': 'Maj',      'color': '#667eea'},
    }
    USER_FACING = {'BOT', 'SITE', 'WEB', 'FEATURE'}
    TAG_RE = re.compile(r'^(?:\*\*)?\[(\w+)\](?:\*\*)?\s*[—–-]?\s*')
    BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
    CODE_RE = re.compile(r'`([^`]+)`')

    def clean_md(s: str) -> str:
        s = BOLD_RE.sub(r'\1', s)
        s = CODE_RE.sub(r'\1', s)
        return s.strip()

    def short_sentence(text: str, max_len: int) -> str:
        """Retourne une seule phrase courte, sans jargon."""
        text = clean_md(text).strip().lstrip('—–-:.,').strip()
        if not text:
            return ''
        # Couper à la première phrase
        for sep in ['. ', '! ', '? ']:
            idx = text.find(sep)
            if 0 < idx <= max_len:
                text = text[:idx].strip()
                break
        if len(text) > max_len:
            text = text[:max_len - 1].rstrip() + '…'
        return text

    lines = content.split('\n')
    entries = []
    versions_found = 0
    current_version = ''
    current_date = '—'
    latest_version = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('## Version'):
            if versions_found >= max_versions:
                break
            parts = stripped.split(' - ', 1)
            v = parts[0].split('Version ')[1].strip()
            current_version = v if v.startswith('v') else 'v' + v
            current_date = parts[1].strip() if len(parts) > 1 else '—'
            if latest_version is None:
                latest_version = current_version
            versions_found += 1
        elif stripped.startswith('- ') and versions_found > 0:
            raw = stripped[2:].strip()
            tag = 'DEFAULT'
            tm = TAG_RE.match(raw)
            if tm:
                tag = tm.group(1).upper()
                raw = raw[tm.end():].strip()

            # Filtre : on n'expose que ce qui concerne directement l'utilisateur
            if user_facing_only and tag not in USER_FACING:
                continue

            meta = CATEGORY_META.get(tag, CATEGORY_META['DEFAULT'])

            # Titre court : premier **gras** sinon première phrase, max 90 chars
            title_text = ''
            rest = raw
            bm = BOLD_RE.match(raw)
            if bm:
                title_text = bm.group(1).rstrip('. ').strip()
                rest = raw[bm.end():].strip()
            else:
                idx = raw.find('.')
                if 0 < idx <= 120:
                    title_text = raw[:idx].strip()
                    rest = raw[idx + 1:].strip()
                else:
                    title_text = raw[:90].strip()
                    rest = ''
            title_text = clean_md(title_text)
            if len(title_text) > 90:
                title_text = title_text[:87].rstrip() + '…'

            # Sous-titre : une seule phrase, jusqu'à 240 chars (pour rester
            # lisible mais sans tronquer brutalement les explications utiles).
            subtitle = short_sentence(rest, 240) if rest else ''
            # Texte complet (pour tooltip / aria-label côté front), sans markdown
            full_text = clean_md(raw)

            if not title_text:
                continue

            entries.append({
                'version':        current_version,
                'date':           current_date,
                'category':       meta['label'],
                'category_color': meta['color'],
                'icon':           meta['icon'],
                'title':          title_text,
                'description':    subtitle,
                'full_text':      full_text,
            })
            if len(entries) >= max_items:
                break

    # Fallback : si le filtre user-facing a tout filtré (ex : 3 dernières
    # versions ne contiennent que des [FIX]/[BACKEND]), on relâche la
    # contrainte plutôt que d'afficher une section vide côté visiteur.
    if user_facing_only and not entries:
        return _parse_changelog_entries(
            max_versions=max_versions,
            max_items=max_items,
            user_facing_only=False,
        )

    return latest_version or 'v0.0.0', entries


@app.route('/api/latest-updates')
def api_latest_updates():
    """Récupère les dernières mises à jour pour le dashboard via CHANGELOG.md"""
    try:
        latest_version, entries = _parse_changelog_entries(max_versions=3, max_items=20)
        if latest_version is None:
            return jsonify({'version': 'v0.0.0', 'updates': []})
        # Format compatible avec l'ancien front (title/description) + nouveaux champs
        updates = []
        for e in entries:
            updates.append({
                'icon':           e['icon'],
                'title':          e['title'],
                'description':    e['description'],
                'full_text':      e.get('full_text', ''),
                'date':           e['date'],
                'source':         e['version'],
                'type':           e['category'],
                'category':       e['category'],
                'category_color': e['category_color'],
                'is_admin':       False,
            })
        return jsonify({'version': latest_version, 'updates': updates})
    except Exception as e:
        print(f"Erreur API updates: {e}")
        return jsonify({'version': 'v0.0.0', 'updates': []})

def _read_changelog_raw():
    """Lit CHANGELOG.md brut et retourne path + contenu + sections parsées (admin)."""
    import re as _re
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(project_root, 'CHANGELOG.md')
    if not os.path.exists(path):
        return path, '', []
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    sections = []
    current = None
    entry_idx = 0
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('## Version'):
            if current is not None:
                sections.append(current)
            parts = stripped.split(' - ', 1)
            v = parts[0].replace('## Version', '').strip()
            date = parts[1].strip() if len(parts) > 1 else '—'
            current = {'version': v, 'date': date, 'header': stripped, 'entries': []}
            entry_idx = 0
        elif stripped.startswith('- ') and current is not None:
            raw = stripped[2:].strip()
            tag = 'DEFAULT'
            tm = _re.match(r'^(?:\*\*)?\[(\w+)\](?:\*\*)?\s*[—–-]?\s*', raw)
            if tm:
                tag = tm.group(1).upper()
                raw_after = raw[tm.end():].strip()
            else:
                raw_after = raw
            bm = _re.match(r'\*\*(.+?)\*\*', raw_after)
            title = bm.group(1) if bm else raw_after.split('.')[0]
            current['entries'].append({
                'index': entry_idx,
                'tag': tag,
                'line': stripped,
                'raw': raw_after,
                'title': title,
            })
            entry_idx += 1
    if current is not None:
        sections.append(current)
    return path, content, sections


@app.route('/api/admin/changelog-editor', methods=['GET'])
@require_login
def api_admin_changelog_get():
    """Retourne toutes les sections du CHANGELOG.md pour l'éditeur admin."""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    try:
        _, _, sections = _read_changelog_raw()
        return jsonify({'sections': sections})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/changelog-editor/entry', methods=['POST'])
@require_login
def api_admin_changelog_add_entry():
    """Ajoute une entrée dans le CHANGELOG.md."""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    try:
        data = request.get_json(silent=True) or {}
        tag = (data.get('tag') or 'BOT').upper()
        title = (data.get('title') or '').strip()
        desc = (data.get('desc') or '').strip()
        if not title:
            return jsonify({'error': 'Titre requis'}), 400
        entry_line = f'- [{tag}] **{title}**'
        if desc:
            entry_line += f'. {desc}'
        if not entry_line.endswith('.'):
            entry_line += '.'
        path, content, sections = _read_changelog_raw()
        if not sections:
            today = datetime.now().strftime('%B %Y')
            content = f'## Version v13.9.0 - {today}\n\n{entry_line}\n'
        else:
            first_header = sections[0]['header']
            pos = content.find(first_header) + len(first_header)
            content = content[:pos] + '\n' + entry_line + content[pos:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/changelog-editor/entry', methods=['DELETE'])
@require_login
def api_admin_changelog_delete_entry():
    """Supprime une entrée du CHANGELOG.md."""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    try:
        data = request.get_json(silent=True) or {}
        version = data.get('version', '')
        index = int(data.get('index', -1))
        path, content, sections = _read_changelog_raw()
        target = next((s for s in sections if s['version'] == version), None)
        if not target or index < 0 or index >= len(target['entries']):
            return jsonify({'error': 'Entrée introuvable'}), 404
        target_line = target['entries'][index]['line']
        lines = content.split('\n')
        new_lines = []
        removed = False
        in_version = False
        for line in lines:
            stripped = line.strip()
            if stripped == target['header']:
                in_version = True
            elif stripped.startswith('## Version') and stripped != target['header']:
                in_version = False
            if in_version and not removed and stripped == target_line:
                removed = True
                continue
            new_lines.append(line)
        if not removed:
            return jsonify({'error': 'Ligne introuvable dans le fichier'}), 404
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/changelog-editor/version', methods=['POST'])
@require_login
def api_admin_changelog_add_version():
    """Crée un nouveau header de version dans le CHANGELOG.md."""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    try:
        data = request.get_json(silent=True) or {}
        version = (data.get('version') or '').strip()
        date = (data.get('date') or '').strip()
        if not version:
            return jsonify({'error': 'Version requise'}), 400
        if not version.startswith('v'):
            version = 'v' + version
        if not date:
            date = datetime.now().strftime('%B %Y')
        new_header = f'## Version {version} - {date}\n\n'
        path, content, _ = _read_changelog_raw()
        content = new_header + content
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/maintenance-history')
def api_maintenance_history():
    """Route PUBLIQUE: Retourne l'historique des maintenances pour la page status"""
    history = load_json('maintenance_history.json')
    if not isinstance(history, list):
        history = []
    
    formatted_history = []
    for item in history[:20]:
        status = item.get('status', 'ended')
        if status == 'scheduled':
            timestamp = item.get('scheduled_time', item.get('start_time', ''))
        elif status == 'active':
            timestamp = item.get('start_time', item.get('scheduled_time', ''))
        else:
            timestamp = item.get('end_actual', item.get('end_time', item.get('start_time', '')))
        formatted_history.append({
            'type': item.get('maintenance_type', item.get('type', 'maintenance')),
            'message': item.get('message', ''),
            'status': status,
            'timestamp': timestamp,
            'start_time': item.get('start_time', ''),
            'end_time': item.get('end_time', ''),
            'scheduled_time': item.get('scheduled_time', '')
        })
    
    return jsonify({'history': formatted_history})

@app.route('/api/scheduled-maintenance')
def api_scheduled_maintenance():
    """Route PUBLIQUE: Retourne les maintenances programmées pour la page home"""
    history = load_json('maintenance_history.json')
    if not isinstance(history, list):
        history = []
    
    scheduled = []
    for item in history:
        if item.get('status') != 'scheduled':
            continue
        maintenance = dict(item)
        if not maintenance.get('start_time') and maintenance.get('scheduled_time'):
            maintenance['start_time'] = maintenance.get('scheduled_time')
        scheduled.append(maintenance)
    return jsonify({'scheduled': scheduled})

@app.route('/api/server/<gid>/bot-status')
@require_login
def api_server_bot_status(gid):
    """Vérifie si le bot est sur le serveur"""
    if not DISCORD_TOKEN:
        return jsonify({'bot_present': False}), 400
    
    try:
        url = f"https://discord.com/api/v10/guilds/{gid}"
        headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
        resp = requests.get(url, headers=headers)
        
        if resp.status_code == 200:
            return jsonify({'bot_present': True})
        elif resp.status_code == 403 or resp.status_code == 401:
            return jsonify({'bot_present': False})
        return jsonify({'bot_present': False})
    except:
        return jsonify({'bot_present': False})

@app.route('/api/status', methods=['GET'])
def api_status():
    """Route PUBLIQUE: Retourne le statut du bot et du site + maintenances programmées"""
    import subprocess
    
    # Lire maintenance.json (structure: {bot: {...}, site: {...}})
    maint_file = load_json('maintenance.json')
    if not isinstance(maint_file, dict):
        maint_file = {}
    
    bot_maint = maint_file.get('bot', {})
    site_maint = maint_file.get('site', {})
    
    bot_maintenance_active = bot_maint.get('active', False)
    site_maintenance_active = site_maint.get('active', False)
    
    # Vérifier si le bot Discord est running
    bot_running = False
    try:
        # On utilise pgrep -f pour trouver le processus python qui tourne main.py
        result = subprocess.run(['pgrep', '-f', 'python.*main.py'], capture_output=True, text=True, timeout=1)
        bot_running = (result.returncode == 0)
    except:
        bot_running = False
    
    # Le bot est online (opérationnel) s'il tourne ET n'est pas en maintenance
    bot_online = bot_running and not bot_maintenance_active
    
    # Le site est online s'il n'est pas en maintenance
    site_online = not site_maintenance_active
    
    # Déterminer le type de maintenance pour le frontend
    maintenance_type = 'none'
    if bot_maintenance_active and site_maintenance_active:
        maintenance_type = 'both'
    elif bot_maintenance_active:
        maintenance_type = 'bot'
    elif site_maintenance_active:
        maintenance_type = 'site'
    
    return jsonify({
        'maintenance_active': bot_maintenance_active or site_maintenance_active,
        'maintenance_type': maintenance_type,
        'bot_running': bot_running,
        'bot_online': bot_online,
        'site_online': site_online,
        'bot_data': bot_maint,
        'site_data': site_maint,
        'last_update': datetime.now().strftime('%H:%M:%S')
    })

@app.route('/api/status_v2', methods=['GET'])
def api_status_v2():
    """Route PUBLIQUE: Version étendue du statut"""
    return api_status()

@app.route('/api/admin/visit-stats')
@require_login
def api_visit_stats():
    """Retourne les statistiques de visites du site"""
    visits = load_visits()
    
    # Trier les pages par nombre de visites
    top_pages = sorted(visits.get('by_page', {}).items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Trier les IPs/pays par nombre de visites
    top_countries = sorted(visits.get('by_country', {}).items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Calculer pourcentages devices
    total = visits.get('total', 1)
    devices = visits.get('by_device', {})
    mobile_pct = round((devices.get('mobile', 0) / total) * 100, 1) if total > 0 else 0
    desktop_pct = round((devices.get('desktop', 0) / total) * 100, 1) if total > 0 else 0
    
    return jsonify({
        'total_visits': visits.get('total', 0),
        'mobile_visits': devices.get('mobile', 0),
        'desktop_visits': devices.get('desktop', 0),
        'mobile_percentage': mobile_pct,
        'desktop_percentage': desktop_pct,
        'top_pages': [{'page': p[0], 'visits': p[1]} for p in top_pages],
        'top_countries': [{'ip': c[0], 'visits': c[1]} for c in top_countries]
    })

@app.route('/api/admin/temp-session', methods=['POST'])
@require_login
def create_temp_session():
    """Crée une session temporaire avec niveau de permission choisi"""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json(silent=True) or {}
    permission = data.get('permission', 'read_only')  # 'read_only' ou 'admin'
    if permission not in ('read_only', 'admin'):
        permission = 'read_only'

    session_id = secrets.token_hex(6).upper()
    password = secrets.token_urlsafe(10)

    temp_session = {
        'session_id': session_id,
        'password': password,
        'permission': permission,
        'created_at': datetime.now().isoformat(),
        'active_at': (datetime.now() + timedelta(minutes=2)).isoformat(),
        'status': 'pending',
        'closed_at': None
    }

    sessions = load_json('temp_sessions.json')
    if not isinstance(sessions, list):
        sessions = []
    sessions.append(temp_session)
    save_json('temp_sessions.json', sessions)

    print(f"🔐 Session temporaire PROGRAMMÉE: {session_id} ({permission}, active dans 2 min)")
    return jsonify({'session_id': session_id, 'password': password, 'permission': permission, 'active_in': 120})

@app.route('/api/admin/temp-sessions')
@require_login
def get_temp_sessions():
    """Retourne les sessions temporaires (pending ou active)"""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    sessions = load_json('temp_sessions.json')
    if not isinstance(sessions, list):
        return jsonify({'sessions': []})
    
    # Mettre à jour les statuts des sessions
    now = datetime.now()
    for s in sessions:
        if s.get('status') == 'pending':
            active_at = datetime.fromisoformat(s.get('active_at', ''))
            if now >= active_at:
                s['status'] = 'active'
    
    # Retourner seulement les non fermées
    active = [s for s in sessions if s.get('status') in ['pending', 'active']]
    save_json('temp_sessions.json', sessions)
    
    return jsonify({'sessions': active})

@app.route('/api/admin/temp-session/<session_id>', methods=['DELETE'])
@require_login
def delete_temp_session(session_id):
    """Ferme une session temporaire (désactive l'ID/MDP)"""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    
    sessions = load_json('temp_sessions.json')
    if isinstance(sessions, list):
        for s in sessions:
            if s.get('session_id') == session_id:
                s['status'] = 'closed'
                s['closed_at'] = datetime.now().isoformat()
        save_json('temp_sessions.json', sessions)
    
    return jsonify({'success': True})

def check_scheduled_maintenance():
    """Vérifie et lance les maintenances programmées automatiquement.

    Le dashboard est servi par plusieurs workers Gunicorn. Chaque worker
    peut importer ce module, mais un seul doit posséder la boucle de
    maintenance : le verrou de fichier sert d'élection de leader et se
    libère automatiquement si le worker leader disparaît.
    """
    import datetime
    import fcntl
    import threading
    import time

    if getattr(check_scheduled_maintenance, '_started', False):
        return
    check_scheduled_maintenance._started = True
    
    def maintenance_check_loop():
        data_dir = os.environ.get(
            'MAINTENANCE_DATA_DIR',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'),
        )
        lock_path = os.path.join(data_dir, '.maintenance-checker.lock')
        lock_file = None

        # Un thread est créé dans chaque worker, mais un seul peut acquérir
        # ce verrou. Si le leader s'arrête, le verrou est libéré et un autre
        # worker reprend automatiquement la surveillance.
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            lock_file = open(lock_path, 'a+', encoding='utf-8')
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(2)
            print("✅ Vérificateur de maintenance programmée démarré (worker leader)")
        except Exception as e:
            print(f"❌ Impossible de prendre le verrou de maintenance : {e}")
            if lock_file:
                lock_file.close()
            return

        def parse_datetime(value):
            if not value:
                return None
            parsed = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            # Les dates sans timezone historiques sont considérées UTC.
            # Toutes les comparaisons restent aware et en UTC pour éviter
            # les décalages liés au fuseau du serveur.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)

        def utc_iso(value):
            return value.astimezone(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')

        def stop_site_maintenance(now, history):
            current = load_json('maintenance.json') or {'bot': {}, 'site': {}}
            site = current.get('site', {}) if isinstance(current, dict) else {}
            if not site.get('active') or not site.get('end_time'):
                return False
            end_dt = parse_datetime(site.get('end_time'))
            if not end_dt or now < end_dt:
                return False
            print("⏹️ AUTO-STOP: Maintenance site terminée automatiquement!")
            current['site'] = {'active': False, 'message': '', 'start_time': None, 'end_time': None, 'show_reason': True}
            save_json('maintenance.json', current)
            for maint in history:
                if maint.get('type') == 'active' and maint.get('status') == 'active':
                    maint_end = parse_datetime(maint.get('end_time'))
                    if maint_end and now >= maint_end:
                        maint['status'] = 'completed'
                        maint['end_actual'] = utc_iso(now)
            save_json('maintenance_history.json', history)
            return True

        while True:
            try:
                history = load_json('maintenance_history.json')
                if not isinstance(history, list):
                    time.sleep(_maintenance_check_interval())
                    continue
                
                now = datetime.datetime.now(datetime.timezone.utc)
                stop_site_maintenance(now, history)
                
                # Vérifier aussi les maintenances ACTIVES avec end_time dépassé
                for maint in history:
                    if maint.get('type') == 'active' and maint.get('status') == 'active':
                        end_time_str = maint.get('end_time')
                        if end_time_str:
                            try:
                                end_dt = parse_datetime(end_time_str)
                                if now >= end_dt:
                                    print(f"⏹️ AUTO-STOP: Maintenance terminée automatiquement!")
                                    maint['status'] = 'completed'
                                    maint['end_actual'] = utc_iso(now)
                                    # Sauvegarder la history avec end_actual
                                    save_json('maintenance_history.json', history)
                                    # Désactiver la maintenance (site uniquement - preservé bot)
                                    current = load_json('maintenance.json') or {'bot': {}, 'site': {}}
                                    current['site'] = {'active': False, 'message': '', 'start_time': None, 'end_time': None, 'show_reason': True}
                                    save_json('maintenance.json', current)
                            except:
                                pass
                
                # Chercher une maintenance programmée prête à lancer
                for maint in history:
                    if maint.get('type') == 'scheduled' and maint.get('status') == 'scheduled':
                        scheduled_time = maint.get('scheduled_time', '')
                        if not scheduled_time:
                            continue
                        
                        try:
                            scheduled_dt = parse_datetime(scheduled_time)
                            
                            # Si l'heure est atteinte, la lancer automatiquement
                            if now >= scheduled_dt:
                                # Vérifier que end_time n'est pas déjà dans le passé
                                # (évite d'activer puis stopper instantanément)
                                end_time = maint.get('end_time')
                                if end_time:
                                    end_dt = parse_datetime(end_time)
                                    if end_dt and now >= end_dt:
                                        print(f"⏭️ SKIP: Maintenance expirée ignorée (end_time déjà passé: {end_time})")
                                        maint['status'] = 'completed'
                                        maint['type'] = 'active'
                                        maint['start_time'] = scheduled_dt.isoformat() + 'Z'
                                        maint['end_actual'] = now.isoformat() + 'Z'
                                        save_json('maintenance_history.json', history)
                                        continue

                                print(f"🚀 LANCEMENT AUTO: Maintenance programmée lancée!")
                                maint['status'] = 'active'
                                maint['type'] = 'active'
                                maint['start_time'] = utc_iso(now)
                                
                                # Utiliser end_time si fourni, sinon 1 heure par défaut
                                if not end_time:
                                    end_time = utc_iso(now + datetime.timedelta(hours=1))
                                    maint['end_time'] = end_time
                                
                                # Sauvegarder dans maintenance.json (NOUVELLE structure)
                                current = load_json('maintenance.json') or {'bot': {}, 'site': {}}
                                current['site'] = {
                                    'active': True,
                                    'message': maint.get('message', '🔧 Maintenance'),
                                    'start_time': utc_iso(now),
                                    'end_time': end_time,
                                    'show_reason': True
                                }
                                save_json('maintenance.json', current)
                                save_json('maintenance_history.json', history)
                                print(f"✅ Maintenance activée automatiquement! Fin prévue: {end_time}")
                        except Exception as e:
                            print(f"❌ Erreur parsing date: {e}")
                
                time.sleep(_maintenance_check_interval())
            except Exception as e:
                print(f"❌ Erreur check maintenance: {e}")
                time.sleep(_maintenance_check_interval())

        if lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    thread = threading.Thread(target=maintenance_check_loop, daemon=True)
    thread.start()
    return thread


def _maintenance_check_interval():
    """Retourne l'intervalle de sondage, avec une valeur sûre par défaut."""
    try:
        return max(float(os.environ.get('MAINTENANCE_CHECK_INTERVAL', '1')), 0.01)
    except (TypeError, ValueError):
        return 1.0


# Gunicorn charge `app:app` et n'exécute jamais le bloc `__main__`.
# Démarrer ici rend la planification active en production ; la fonction est
# idempotente dans chaque worker et le verrou ci-dessus évite les doublons.
if os.environ.get('DISABLE_MAINTENANCE_CHECKER') != '1':
    check_scheduled_maintenance()

# ============================================================================
# 💳 PAIEMENTS — infrastructure défensive (Stripe + PayPal)
# ----------------------------------------------------------------------------
# Toutes les routes ci-dessous sont volontairement BLOQUÉES tant que les clés
# d'environnement (STRIPE_SECRET_KEY, PAYPAL_CLIENT_ID, ...) ne sont pas
# définies. Les boutons « Bientôt disponible » du front restent en place ;
# cette section ne fait que poser la plomberie pour le jour J.
# ============================================================================

from utils import payments as _payments


@app.route('/api/payment/status')
def payment_status():
    """État public des fournisseurs de paiement disponibles.

    Sûr à exposer : ne contient AUCUN secret, juste la clé publishable Stripe
    (qui par définition est publique) et les drapeaux d'activation.
    """
    try:
        return jsonify(_payments.get_payment_status())
    except Exception as e:
        print(f"[payments] status error: {e}")
        return jsonify({"enabled": False, "providers": {"stripe": False, "paypal": False}}), 200


@app.route('/api/premium-launch')
def api_premium_launch():
    """Configuration publique de l'ouverture officielle du Premium.

    Lit web/data/premium_launch.json et renvoie les infos pour le compte à
    rebours affiché sur /home et /premium. Endpoint public — aucun secret.
    """
    cfg_path = os.path.join(os.path.dirname(__file__), 'data', 'premium_launch.json')
    default_cfg = {
        "active": False,
        "launch_at_iso": None,
        "title_pre_launch": "🎉 Le Premium ouvre dans quelque instant...",
        "subtitle_pre_launch": "",
        "title_live": "🚀 Le Premium est ouvert !",
        "subtitle_live": "",
        "cta_text": "Découvrir",
        "cta_url": "/premium",
    }
    try:
        if not os.path.exists(cfg_path):
            return jsonify(default_cfg)
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        # Filtre pour ne renvoyer que les champs publics (on retire _doc).
        return jsonify({
            "active": bool(cfg.get("active", False)),
            "launch_at_iso": cfg.get("launch_at_iso"),
            "title_pre_launch": cfg.get("title_pre_launch", default_cfg["title_pre_launch"]),
            "subtitle_pre_launch": cfg.get("subtitle_pre_launch", ""),
            "title_live": cfg.get("title_live", default_cfg["title_live"]),
            "subtitle_live": cfg.get("subtitle_live", ""),
            "cta_text": cfg.get("cta_text", "Découvrir"),
            "cta_url": cfg.get("cta_url", "/premium"),
        })
    except Exception as e:
        print(f"[premium-launch] config error: {e}")
        return jsonify(default_cfg)


@app.route('/maintenance-annonce')
def maintenance_announce():
    """Page publique d'annonce de maintenance."""
    ann_path = os.path.join(os.path.dirname(__file__), 'data', 'maintenance_announce.json')
    try:
        with open(ann_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {'active': False}
    return render_template('maintenance_announce.html', data=data)


@app.route('/api/admin/maintenance-announce', methods=['GET'])
@require_login
def api_admin_maintenance_announce_get():
    """Retourne les données de l'annonce maintenance pour l'admin."""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    ann_path = os.path.join(os.path.dirname(__file__), 'data', 'maintenance_announce.json')
    try:
        with open(ann_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception:
        return jsonify({
            'active': False, 'title': '', 'intro': '',
            'start_date': '', 'sections': [], 'warning': '', 'footer': ''
        })


@app.route('/api/admin/maintenance-announce', methods=['POST'])
@require_login
def api_admin_maintenance_announce_save():
    """Sauvegarde les données de l'annonce maintenance."""
    if not session.get('user', {}).get('is_owner'):
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json(silent=True) or {}
    ann_path = os.path.join(os.path.dirname(__file__), 'data', 'maintenance_announce.json')
    try:
        payload = {
            'active':     bool(data.get('active', False)),
            'title':      str(data.get('title', '')).strip(),
            'intro':      str(data.get('intro', '')).strip(),
            'start_date': str(data.get('start_date', '')).strip(),
            'sections':   data.get('sections', []),
            'warning':    str(data.get('warning', '')).strip(),
            'footer':     str(data.get('footer', '')).strip(),
        }
        # Valider les sections
        clean_sections = []
        for s in payload['sections']:
            if isinstance(s, dict) and s.get('title'):
                items = [str(i).strip() for i in s.get('items', []) if str(i).strip()]
                clean_sections.append({'title': str(s['title']).strip(), 'items': items})
        payload['sections'] = clean_sections
        with open(ann_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _is_payment_admin():
    """Les réglages de paiement sont réservés au propriétaire authentifié."""
    user = session.get('user') or {}
    return bool(
        user.get('is_owner')
        or (
            str(user.get('id', '')) == '1140296215469240401'
            and session.get('admin_authenticated', False)
        )
    )


@app.route('/api/admin/payment-settings', methods=['GET', 'POST'])
@require_login
def api_admin_payment_settings():
    """Lit ou modifie les fournisseurs et plans autorisés à la vente."""
    if not _is_payment_admin():
        return jsonify({'error': 'Forbidden'}), 403

    if request.method == 'GET':
        return jsonify(_payments.get_payment_status())

    data = request.get_json(silent=True) or {}
    providers = data.get('providers') if isinstance(data.get('providers'), dict) else {}
    plans = data.get('plans') if isinstance(data.get('plans'), dict) else {}
    try:
        current = _payments.get_payment_settings()
        providers = {**current['providers'], **providers}
        plans = {**current['plans'], **plans}
        settings = _payments.save_payment_settings(providers, plans)
        return jsonify({'success': True, 'settings': settings})
    except OSError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/promo', methods=['GET', 'POST', 'DELETE'])
@require_login
def api_admin_promo():
    """Gère la promotion visible sur la page Premium."""
    if not _is_payment_admin():
        return jsonify({'error': 'Forbidden'}), 403

    from datetime import datetime, timezone
    from utils.promo import clear_promo, get_promo, set_promo

    if request.method == 'GET':
        return jsonify(get_promo() or {'active': False, 'items': []})
    if request.method == 'DELETE':
        clear_promo()
        return jsonify({'success': True, 'active': False})

    data = request.get_json(silent=True) or {}
    plan = str(data.get('plan', '')).strip()
    billing = str(data.get('billing', '')).strip()
    label = str(data.get('label', '')).strip() or '🏷️ Promotion en cours'
    end_time_raw = str(data.get('end_time', '')).strip()

    if plan not in ('all', _payments.PLAN_PREMIUM, _payments.PLAN_PREMIUM_PLUS):
        return jsonify({'error': 'Plan de promotion invalide'}), 400
    if billing not in ('all', _payments.BILLING_MONTHLY, _payments.BILLING_YEARLY):
        return jsonify({'error': 'Période de promotion invalide'}), 400
    try:
        promo_cents = int(round(float(str(data.get('promo_price', '')).replace(',', '.')) * 100))
    except (TypeError, ValueError):
        return jsonify({'error': 'Prix promotionnel invalide'}), 400
    if promo_cents <= 0:
        return jsonify({'error': 'Le prix promotionnel doit être supérieur à 0'}), 400

    try:
        end_time = datetime.fromisoformat(end_time_raw.replace('Z', '+00:00'))
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        end_time = end_time.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return jsonify({'error': 'Date de fin invalide'}), 400
    if end_time <= datetime.now(timezone.utc):
        return jsonify({'error': 'La date de fin doit être dans le futur'}), 400

    plans = (
        [_payments.PLAN_PREMIUM, _payments.PLAN_PREMIUM_PLUS]
        if plan == 'all' else [plan]
    )
    billings = (
        [_payments.BILLING_MONTHLY, _payments.BILLING_YEARLY]
        if billing == 'all' else [billing]
    )
    items = []
    for selected_plan in plans:
        for selected_billing in billings:
            regular = _payments.get_catalog_entry(selected_plan, selected_billing)
            if regular is None:
                return jsonify({'error': 'Combinaison plan/période invalide'}), 400
            items.append({
                'plan': selected_plan,
                'billing': selected_billing,
                'promo_cents': promo_cents,
            })

    set_promo(label=label, end_time=end_time, items=items)
    return jsonify({'success': True, 'promo': get_promo()})


@app.route('/api/promo')
def api_promo():
    """Retourne la promotion tarifaire active (ou inactive) — endpoint public."""
    try:
        from utils.promo import get_promo
        return jsonify(get_promo() or {"active": False})
    except Exception as e:
        print(f"[api/promo] error: {e}")
        return jsonify({"active": False})


def _is_before_launch():
    """Retourne True si la date d'ouverture Premium n'est pas encore atteinte."""
    try:
        launch_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'premium_launch.json')
        with open(launch_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if not cfg.get('active'):
            return False
        launch_at = cfg.get('launch_at_iso')
        if not launch_at:
            return False
        from datetime import datetime, timezone
        launch_dt = datetime.fromisoformat(launch_at)
        return datetime.now(timezone.utc) < launch_dt.astimezone(timezone.utc)
    except Exception:
        return False


def _checkout_validate(data):
    """Validation stricte du payload de checkout.

    Renvoie (ok, plan, billing, guild_id, amount_cents, error_response).
    """
    if not isinstance(data, dict):
        return False, None, None, None, None, (jsonify({"error": "Payload invalide"}), 400)

    plan = data.get('plan')
    billing = data.get('billing')
    guild_id = data.get('guild_id')

    if plan not in (_payments.PLAN_PREMIUM, _payments.PLAN_PREMIUM_PLUS):
        return False, None, None, None, None, (jsonify({"error": "Plan inconnu"}), 400)
    if not _payments.is_plan_enabled(plan):
        return False, None, None, None, None, (
            jsonify({"error": "Ce plan est temporairement indisponible.", "code": "PLAN_DISABLED"}),
            403,
        )
    if billing not in (_payments.BILLING_MONTHLY, _payments.BILLING_YEARLY):
        return False, None, None, None, None, (jsonify({"error": "Période de facturation inconnue"}), 400)
    if not guild_id or not str(guild_id).isdigit():
        return False, None, None, None, None, (jsonify({"error": "ID de serveur invalide"}), 400)

    entry = _payments.get_catalog_entry(plan, billing)
    if entry is None:
        return False, None, None, None, None, (jsonify({"error": "Tarif introuvable"}), 400)
    amount_cents, _label = entry
    return True, plan, billing, str(guild_id), amount_cents, None


def _require_logged_user():
    """Renvoie l'user_id si connecté, sinon (None, response 401)."""
    user = session.get('user') or {}
    user_id = user.get('id')
    if not user_id:
        return None, (jsonify({"error": "Connexion Discord requise"}), 401)
    return str(user_id), None


@app.route('/api/checkout/stripe', methods=['POST'])
def checkout_stripe():
    """Crée une session Stripe Checkout — BLOQUÉ tant que non configuré.

    Garde-fou principal : `assert_payments_enabled()` renvoie 503 si les clés
    Stripe ne sont pas dans l'environnement. Donc IMPOSSIBLE de débiter
    quelqu'un par accident tant que tu n'as pas mis les vraies clés.
    """
    ok, msg = _payments.assert_payments_enabled()
    if not ok or not _payments.is_provider_enabled("stripe"):
        return jsonify({"error": msg or "Stripe non configuré", "code": "PAYMENTS_DISABLED"}), 503

    if _is_before_launch():
        return jsonify({"error": "Le Premium n'est pas encore ouvert. Revenez le 06 juin 2026 à 20h (heure de Paris) !", "code": "NOT_LAUNCHED"}), 403

    user_id, err = _require_logged_user()
    if err:
        return err

    valid, plan, billing, guild_id, amount_cents, err = _checkout_validate(request.get_json(silent=True))
    if not valid:
        return err

    # Sécurité : seul un admin/owner du serveur Discord ciblé peut acheter
    # un Premium pour ce serveur. Sans ce contrôle, n'importe quel user
    # connecté pourrait payer pour activer Premium sur n'importe quel
    # guild_id (par ex. exfiltrer du Premium vers un serveur tiers).
    if not _check_server_access(guild_id):
        return jsonify({
            "error": "Tu dois être administrateur du serveur Discord ciblé pour souscrire à Premium pour ce serveur.",
            "code": "FORBIDDEN_GUILD",
        }), 403

    # Vérification du tier actuel du serveur pour éviter un double-achat
    # et autoriser l'upgrade Premium → Premium+
    try:
        from utils.premium import get_guild_tier, TIER_FREE, TIER_PREMIUM, TIER_PREMIUM_PLUS
        current_guild_tier = get_guild_tier(int(guild_id))
        if current_guild_tier != TIER_FREE:
            if current_guild_tier == plan:
                return jsonify({
                    "error": "Ce serveur a déjà cet abonnement actif.",
                    "code": "ALREADY_SUBSCRIBED",
                }), 409
            if current_guild_tier == TIER_PREMIUM_PLUS:
                return jsonify({
                    "error": "Ce serveur est déjà au niveau Premium+, le tier le plus élevé.",
                    "code": "ALREADY_MAX_TIER",
                }), 409
    except Exception:
        pass

    order = _payments.create_pending_order(
        user_id=user_id, guild_id=guild_id, plan=plan,
        billing=billing, provider='stripe', amount_cents=amount_cents,
    )

    try:
        import stripe
        stripe.api_key = os.environ['STRIPE_SECRET_KEY']
        success_url = request.host_url.rstrip('/') + '/premium?success=1&order=' + order['id']
        cancel_url = request.host_url.rstrip('/') + '/premium?canceled=1'

        _entry = _payments.get_catalog_entry(plan, billing)
        label = _entry[1] if _entry else plan
        stripe_session = stripe.checkout.Session.create(
            mode='subscription',
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': _payments.CURRENCY,
                    'product_data': {'name': f'Miaouscle {label}'},
                    'unit_amount': amount_cents,
                    'recurring': {
                        'interval': 'month' if billing == _payments.BILLING_MONTHLY else 'year'
                    },
                },
                'quantity': 1,
            }],
            client_reference_id=order['id'],
            metadata={
                'order_id': order['id'],
                'user_id': user_id,
                'guild_id': guild_id,
                'plan': plan,
                'billing': billing,
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
        _payments.update_order(order['id'], {'provider_session_id': stripe_session.id})
        return jsonify({"checkout_url": stripe_session.url, "order_id": order['id']})
    except Exception as e:
        _payments.update_order(order['id'], {'status': 'error', 'metadata': {'error': str(e)}})
        print(f"[stripe] checkout error: {e}")
        return jsonify({"error": "Erreur création de la session de paiement"}), 500


@app.route('/api/checkout/paypal', methods=['POST'])
def checkout_paypal():
    """Crée une souscription PayPal et renvoie l'URL d'approbation.

    Étapes:
      1. Validation stricte du payload (plan/billing/guild_id) — montant
         imposé côté serveur depuis le catalogue.
      2. Création d'une commande interne en statut `pending`.
      3. `paypal_ensure_plan` crée (ou récupère depuis cache) le couple
         product+plan PayPal correspondant.
      4. `paypal_create_subscription` crée la souscription et renvoie
         l'URL d'approbation où l'utilisateur doit être redirigé.
    """
    ok, msg = _payments.assert_payments_enabled()
    if not ok or not _payments.is_provider_enabled("paypal"):
        return jsonify({"error": msg or "PayPal non configuré", "code": "PAYMENTS_DISABLED"}), 503

    if _is_before_launch():
        return jsonify({"error": "Le Premium n'est pas encore ouvert. Revenez le 20 juin 2026 à 20h (heure de Paris) !", "code": "NOT_LAUNCHED"}), 403

    user_id, err = _require_logged_user()
    if err:
        return err

    valid, plan, billing, guild_id, amount_cents, err = _checkout_validate(request.get_json(silent=True))
    if not valid:
        return err

    # Sécurité : même garde-fou que pour Stripe — l'utilisateur doit
    # administrer le serveur ciblé pour pouvoir acheter Premium dessus.
    if not _check_server_access(guild_id):
        return jsonify({
            "error": "Tu dois être administrateur du serveur Discord ciblé pour souscrire à Premium pour ce serveur.",
            "code": "FORBIDDEN_GUILD",
        }), 403

    # Vérification du tier actuel du serveur pour éviter un double-achat
    # et autoriser l'upgrade Premium → Premium+
    try:
        from utils.premium import get_guild_tier, TIER_FREE, TIER_PREMIUM, TIER_PREMIUM_PLUS
        current_guild_tier = get_guild_tier(int(guild_id))
        if current_guild_tier != TIER_FREE:
            if current_guild_tier == plan:
                return jsonify({
                    "error": "Ce serveur a déjà cet abonnement actif.",
                    "code": "ALREADY_SUBSCRIBED",
                }), 409
            if current_guild_tier == TIER_PREMIUM_PLUS:
                return jsonify({
                    "error": "Ce serveur est déjà au niveau Premium+, le tier le plus élevé.",
                    "code": "ALREADY_MAX_TIER",
                }), 409
    except Exception:
        pass

    order = _payments.create_pending_order(
        user_id=user_id, guild_id=guild_id, plan=plan,
        billing=billing, provider='paypal', amount_cents=amount_cents,
    )

    plan_id = _payments.paypal_ensure_plan(plan, billing)
    if not plan_id:
        _payments.update_order(order['id'], {'status': 'error', 'metadata': {'error': 'PayPal plan creation failed'}})
        return jsonify({"error": "Impossible de préparer l'offre PayPal pour le moment."}), 502

    return_url = request.host_url.rstrip('/') + '/premium?success=1&provider=paypal&order=' + order['id']
    cancel_url = request.host_url.rstrip('/') + '/premium?canceled=1&provider=paypal'

    sub = _payments.paypal_create_subscription(
        plan_id=plan_id,
        return_url=return_url,
        cancel_url=cancel_url,
        custom_id=order['id'],
    )
    if not sub or not sub.get('approval_url'):
        _payments.update_order(order['id'], {'status': 'error', 'metadata': {'error': 'PayPal subscription create failed'}})
        return jsonify({"error": "Création de la souscription PayPal échouée."}), 502

    _payments.update_order(order['id'], {
        'provider_session_id': sub.get('id'),
        'metadata': {'paypal_subscription_id': sub.get('id'), 'plan_id': plan_id},
    })
    return jsonify({"checkout_url": sub['approval_url'], "order_id": order['id']})


@app.route('/api/subscription/cancel', methods=['POST'])
@require_login
def api_cancel_subscription():
    """Annule l'abonnement actif d'un serveur depuis le dashboard utilisateur.

    Seul l'acheteur d'origine (ou un owner du bot) peut annuler.
    L'annulation est transmise à Stripe/PayPal ; le webhook déclenchera ensuite
    la révocation du Premium automatiquement. En cas d'échec du webhook, le
    statut de l'ordre est quand même mis à 'canceled' côté local.
    """
    from utils.premium import OWNER_IDS as _OWNER_IDS
    user_id = str(session['user']['id'])

    data = request.get_json(silent=True) or {}
    guild_id = str(data.get('guild_id', '')).strip()
    if not guild_id:
        return jsonify({'error': 'guild_id manquant'}), 400

    order = _payments.find_active_order_for_guild(guild_id)
    if not order:
        return jsonify({'error': 'Aucun abonnement actif trouvé pour ce serveur. '
                                 'Si tu as souscrit via PayPal ou Stripe directement, '
                                 'gère l\'annulation depuis leur interface.'}), 404

    order_user_id = str(order.get('user_id', ''))
    try:
        uid_int = int(user_id)
    except ValueError:
        uid_int = 0
    if user_id != order_user_id and uid_int not in _OWNER_IDS:
        return jsonify({'error': "Tu n'es pas l'acheteur de cet abonnement. "
                                 "Seul l'acheteur peut annuler depuis le dashboard."}), 403

    sub_id = order.get('provider_payment_id')
    provider = order.get('provider', '')

    if provider == 'stripe':
        ok, msg = _payments.cancel_stripe_subscription(sub_id)
    elif provider == 'paypal':
        ok, msg = _payments.cancel_paypal_subscription(sub_id)
    else:
        return jsonify({'error': f'Fournisseur de paiement inconnu: {provider}'}), 400

    if not ok:
        return jsonify({'error': msg}), 500

    _payments.update_order(order['id'], {'status': 'canceled'})
    print(f"[cancel] ✅ Abonnement {provider} annulé pour guild {guild_id} par user {user_id}")
    return jsonify({'success': True, 'message': msg})


# ---------------------------------------------------------------------------
# Premium+ multi-server slots API
# ---------------------------------------------------------------------------

@app.route('/api/subscription/slots', methods=['GET'])
@require_login
def api_get_plus_slots():
    """Retourne les slots Premium+ de l'utilisateur connecté."""
    user_id = str(session['user']['id'])
    order = _payments.get_plus_order_for_user(user_id)
    if not order:
        return jsonify({'has_plus_subscription': False, 'guild_ids': [], 'max_slots': 3, 'order_id': None})
    order_id = order['id']
    slot_data = _payments.get_slots_for_order(order_id) or {}
    guild_ids = slot_data.get('guild_ids', [str(order.get('guild_id', ''))])
    max_slots = slot_data.get('max_slots', _payments.MAX_PLUS_SLOTS)
    return jsonify({
        'has_plus_subscription': True,
        'order_id': order_id,
        'max_slots': max_slots,
        'guild_ids': guild_ids,
        'available_slots': max(0, max_slots - len(guild_ids)),
    })


@app.route('/api/subscription/slots/add', methods=['POST'])
@require_login
def api_add_plus_slot():
    """Ajoute un serveur aux slots Premium+ de l'utilisateur."""
    from utils.premium import grant_premium, TIER_PREMIUM_PLUS
    user_id = str(session['user']['id'])
    data = request.get_json(silent=True) or {}
    guild_id = str(data.get('guild_id', '')).strip()
    if not guild_id:
        return jsonify({'error': 'guild_id manquant'}), 400

    order = _payments.get_plus_order_for_user(user_id)
    if not order:
        return jsonify({'error': 'Aucun abonnement Premium+ actif trouvé'}), 404

    order_id = order['id']
    slot_data = _payments.get_slots_for_order(order_id)
    if not slot_data:
        _payments.init_plus_slots(order_id, user_id, str(order.get('guild_id', guild_id)))
        slot_data = _payments.get_slots_for_order(order_id) or {}

    ok, msg = _payments.add_guild_to_plus_slots(order_id, guild_id)
    if not ok:
        return jsonify({'error': msg}), 400

    try:
        buyer_id = int(user_id)
    except ValueError:
        buyer_id = None

    try:
        grant_premium(
            target_id=int(guild_id),
            target_type='guild',
            tier=TIER_PREMIUM_PLUS,
            expires=None,
            granted_by=buyer_id,
        )
        print(f"[slots] ✅ Premium+ activé pour serveur {guild_id} via slot (ordre {order_id})")
    except Exception as e:
        print(f"[slots] grant_premium failed for {guild_id}: {e}")

    return jsonify({'success': True, 'message': msg})


@app.route('/api/subscription/slots/remove', methods=['POST'])
@require_login
def api_remove_plus_slot():
    """Retire un serveur des slots Premium+ et révoque son accès."""
    from utils.premium import revoke_premium
    user_id = str(session['user']['id'])
    data = request.get_json(silent=True) or {}
    guild_id = str(data.get('guild_id', '')).strip()
    if not guild_id:
        return jsonify({'error': 'guild_id manquant'}), 400

    order = _payments.get_plus_order_for_user(user_id)
    if not order:
        return jsonify({'error': 'Aucun abonnement Premium+ actif trouvé'}), 404

    order_id = order['id']
    ok, msg = _payments.remove_guild_from_plus_slots(order_id, guild_id)
    if not ok:
        return jsonify({'error': msg}), 400

    try:
        revoke_premium(target_id=int(guild_id), target_type='guild')
        print(f"[slots] ⛔ Premium+ révoqué pour serveur {guild_id} via slot (ordre {order_id})")
    except Exception as e:
        print(f"[slots] revoke_premium failed for {guild_id}: {e}")

    return jsonify({'success': True, 'message': msg})


def _activate_premium_from_order(order_id):
    """Active le tier Premium correspondant pour le serveur d'une commande.

    Appelé par les webhooks Stripe et PayPal quand un paiement est confirmé.
    Aucune date d'expiration n'est posée puisque l'abonnement est récurrent —
    la révocation se fera via l'événement `subscription.deleted`/`cancelled`.
    """
    if not order_id:
        return False
    orders = _payments.load_orders()
    order = next((o for o in orders if o.get('id') == order_id), None)
    if not order:
        print(f"[payments] activation: ordre introuvable {order_id}")
        return False
    try:
        from utils.premium import grant_premium, TIER_PREMIUM, TIER_PREMIUM_PLUS
    except Exception as e:
        print(f"[payments] import premium failed: {e}")
        return False
    plan = order.get('plan')
    tier = TIER_PREMIUM_PLUS if plan == _payments.PLAN_PREMIUM_PLUS else TIER_PREMIUM
    try:
        guild_id = int(order.get('guild_id'))
    except (TypeError, ValueError):
        print(f"[payments] activation: guild_id invalide {order.get('guild_id')}")
        return False
    buyer_discord_id = None
    raw_uid = order.get('user_id')
    if raw_uid:
        try:
            buyer_discord_id = int(raw_uid)
        except (TypeError, ValueError):
            pass
    try:
        grant_premium(
            target_id=guild_id,
            target_type='guild',
            tier=tier,
            expires=None,
            granted_by=buyer_discord_id,
        )
        print(f"[payments] ✅ Premium {tier} activé pour serveur {guild_id} (ordre {order_id}, acheteur={buyer_discord_id})")
        if plan == _payments.PLAN_PREMIUM_PLUS and buyer_discord_id:
            _payments.init_plus_slots(order_id, str(buyer_discord_id), str(guild_id))
        if buyer_discord_id:
            _push_pending_role_sync(buyer_discord_id)
        return True
    except Exception as e:
        print(f"[payments] grant_premium failed: {e}")
        return False


def _revoke_premium_from_order(order_id):
    """Révoque le Premium du serveur lié à une commande (annulation d'abonnement).

    Pour Premium+, révoque TOUS les serveurs du slot (jusqu'à 3).
    Pour Premium, révoque uniquement le serveur de la commande.
    """
    if not order_id:
        return False
    orders = _payments.load_orders()
    order = next((o for o in orders if o.get('id') == order_id), None)
    if not order:
        return False
    try:
        from utils.premium import revoke_premium
        plan = order.get('plan', '')
        if plan == _payments.PLAN_PREMIUM_PLUS:
            guild_ids = _payments.get_all_plus_guild_ids(order_id)
            if not guild_ids:
                guild_ids = [str(order.get('guild_id', ''))]
            for gid in guild_ids:
                try:
                    revoke_premium(target_id=int(gid), target_type='guild')
                    print(f"[payments] ⛔ Premium+ révoqué pour serveur {gid} (ordre {order_id})")
                except Exception as e2:
                    print(f"[payments] revoke_premium failed for {gid}: {e2}")
            slots = _payments.load_slots()
            if order_id in slots:
                slots[order_id]['guild_ids'] = []
                _payments.save_slots(slots)
        else:
            guild_id = int(order.get('guild_id'))
            revoke_premium(target_id=guild_id, target_type='guild')
            print(f"[payments] ⛔ Premium révoqué pour serveur {guild_id} (ordre {order_id})")
        return True
    except Exception as e:
        print(f"[payments] revoke_premium failed: {e}")
        return False


@app.route('/webhook/stripe', methods=['POST'])
def webhook_stripe():
    """Webhook Stripe. Vérifie la signature, active/révoque le Premium.

    Événements traités :
    - `checkout.session.completed` → marque l'ordre `paid` ET active le Premium
      du serveur ciblé via `grant_premium`.
    - `customer.subscription.deleted` → révoque le Premium du serveur lié.
    - `invoice.payment_failed` / `checkout.session.expired` → marque l'ordre
      en `failed` (sans révoquer immédiatement le Premium, Stripe gère les
      retries automatiquement).
    """
    if not _payments.is_stripe_configured():
        return jsonify({"received": True, "ignored": True}), 200

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    if not webhook_secret:
        return jsonify({"error": "Webhook secret manquant"}), 503

    try:
        import stripe
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        print(f"[stripe] webhook signature invalide: {e}")
        return jsonify({"error": "Signature invalide"}), 400

    try:
        # NOTE : `event` est un objet `stripe.Event` (StripeObject) qui ne
        # supporte PAS la méthode `.get()` (qui collisionne avec son
        # __getattr__). On accède aux champs comme un dict en `event['x']`
        # avec un dict() de conversion pour pouvoir appeler `.get()` dessus.
        ev_dict = event.to_dict() if hasattr(event, 'to_dict') else dict(event)
        etype = ev_dict.get('type', '')
        obj = ((ev_dict.get('data') or {}).get('object') or {})
        if etype == 'checkout.session.completed':
            order_id = (obj.get('metadata') or {}).get('order_id') or obj.get('client_reference_id')
            if order_id:
                # Pour les souscriptions, on stocke le subscription_id en
                # `provider_payment_id` afin que `customer.subscription.deleted`
                # puisse retrouver l'ordre. Pour les paiements one-shot
                # (sans subscription), on tombe sur le payment_intent.
                _payments.update_order(order_id, {
                    'status': 'paid',
                    'provider_payment_id': obj.get('subscription') or obj.get('payment_intent'),
                    'metadata': {
                        'stripe_event': etype,
                        'subscription_id': obj.get('subscription'),
                        'payment_intent_id': obj.get('payment_intent'),
                    },
                })
                _activate_premium_from_order(order_id)
        elif etype == 'customer.subscription.deleted':
            # Retrouve l'ordre via subscription_id stocké dans provider_payment_id
            sub_id = obj.get('id')
            if sub_id:
                for o in _payments.load_orders():
                    if o.get('provider') == 'stripe' and o.get('provider_payment_id') == sub_id:
                        _payments.update_order(o['id'], {'status': 'canceled', 'metadata': {'stripe_event': etype}})
                        _revoke_premium_from_order(o['id'])
                        break
        elif etype in ('invoice.payment_failed', 'checkout.session.expired'):
            order_id = (obj.get('metadata') or {}).get('order_id')
            if order_id:
                _payments.update_order(order_id, {'status': 'failed', 'metadata': {'stripe_event': etype}})
    except Exception as e:
        import traceback
        print(f"[stripe] webhook handler error: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        # On renvoie 200 pour éviter que Stripe ne renvoie en boucle ;
        # l'incident est loggé pour investigation manuelle.

    return jsonify({"received": True}), 200


@app.route('/webhook/paypal', methods=['POST'])
def webhook_paypal():
    """Webhook PayPal. Vérifie la signature via API officielle puis active/révoque.

    Si `PAYPAL_WEBHOOK_ID` n'est pas défini en env, AUCUN effet de bord n'est
    appliqué (sécurité par défaut) — il faut créer le webhook dans le dashboard
    PayPal et poser son ID dans l'environnement pour que les paiements PayPal
    activent automatiquement le Premium.

    Événements traités :
    - `BILLING.SUBSCRIPTION.ACTIVATED` → active le Premium
    - `BILLING.SUBSCRIPTION.CANCELLED` / `BILLING.SUBSCRIPTION.SUSPENDED` /
      `BILLING.SUBSCRIPTION.EXPIRED` → révoque
    """
    if not _payments.is_paypal_configured():
        return jsonify({"received": True, "ignored": True}), 200

    body_str = request.get_data(as_text=True) or ""
    # Headers normalisés (Flask renvoie la casse exacte, mais PayPal peut
    # varier — utils.paypal_verify_webhook gère les deux conventions).
    headers_dict = {k: v for k, v in request.headers.items()}

    verified = _payments.paypal_verify_webhook(headers_dict, body_str)
    if not verified:
        # Pas de PAYPAL_WEBHOOK_ID OU signature invalide : on log et on ne fait
        # AUCUN effet de bord. On renvoie 200 pour ne pas faire boucler PayPal.
        if not os.environ.get("PAYPAL_WEBHOOK_ID"):
            print("[paypal] webhook reçu mais PAYPAL_WEBHOOK_ID non configuré — ignoré (configure le dans le dashboard PayPal pour activer)")
        else:
            print("[paypal] webhook signature invalide — ignoré")
        return jsonify({"received": True, "verified": False}), 200

    try:
        event = json.loads(body_str) if body_str else {}
    except Exception:
        return jsonify({"received": True, "ignored": True}), 200

    etype = event.get('event_type', '')
    resource = event.get('resource', {}) or {}
    custom_id = resource.get('custom_id') or (resource.get('subscriber') or {}).get('payer_id')
    # `custom_id` contient notre order_id interne (posé lors de paypal_create_subscription)

    try:
        if etype == 'BILLING.SUBSCRIPTION.ACTIVATED':
            if custom_id:
                _payments.update_order(custom_id, {
                    'status': 'paid',
                    'provider_payment_id': resource.get('id'),
                    'metadata': {'paypal_event': etype},
                })
                _activate_premium_from_order(custom_id)
        elif etype in ('BILLING.SUBSCRIPTION.CANCELLED', 'BILLING.SUBSCRIPTION.SUSPENDED', 'BILLING.SUBSCRIPTION.EXPIRED'):
            sub_id = resource.get('id')
            target_order = None
            if custom_id:
                target_order = custom_id
            elif sub_id:
                for o in _payments.load_orders():
                    if o.get('provider') == 'paypal' and o.get('provider_payment_id') == sub_id:
                        target_order = o['id']
                        break
            if target_order:
                _payments.update_order(target_order, {'status': 'canceled', 'metadata': {'paypal_event': etype}})
                _revoke_premium_from_order(target_order)
    except Exception as e:
        print(f"[paypal] webhook handler error: {e}")

    return jsonify({"received": True, "verified": True}), 200


@app.route('/api/me/orders')
def my_orders():
    """Historique des commandes de l'utilisateur connecté."""
    user_id, err = _require_logged_user()
    if err:
        return err
    return jsonify({"orders": _payments.get_user_orders(user_id)})


# ============================================================================
# Fin section paiements
# ============================================================================


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'✅ Dashboard démarré sur http://0.0.0.0:{port}')
    check_scheduled_maintenance()
    app.run(host='0.0.0.0', port=port, debug=False)
