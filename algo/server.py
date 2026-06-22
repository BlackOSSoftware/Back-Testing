from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import date, timedelta
from functools import wraps
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import MetaTrader5 as mt5
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "algo"

from .algo_control import create_algo_blueprint
from .app_paths import INSTANCE_DIR, RESOURCE_DIR, prepare_runtime
from .market_data import delta_symbols, normalize_source


prepare_runtime()
app = Flask(__name__, static_folder=None, template_folder=str(RESOURCE_DIR), instance_path=str(INSTANCE_DIR))
Path(app.instance_path).mkdir(parents=True, exist_ok=True)

AUTH_FILE = Path(app.instance_path) / "auth.json"
SECRET_FILE = Path(app.instance_path) / "session_secret"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD_HASH = "scrypt:32768:8:1$QISOB6xfCKIB9Tef$1c28c2344d09c3fd43b055bc5d8699ce98e315cfa296c74193cd6ffe647c0d8a64bbef97ca68fbd6eb5f9b05e2927be83bd6f5b84dbb44cbc6242444e83a7a6d"
LOGIN_WINDOW_SECONDS = 300.0
LOGIN_MAX_ATTEMPTS = 5
FALLBACK_MT5_SYMBOLS = ["BTCUSD#", "BTCUSD", "ETHUSD", "XAUUSD", "XAGUSD", "US30", "NAS100", "SPX500"]
_login_failures: dict[str, list[float]] = {}


def load_secret_key() -> str:
    configured = os.getenv("ALGO_SECRET_KEY")
    if configured:
        return configured
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(secret, encoding="utf-8")
    try:
        SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return secret


app.config.update(
    SECRET_KEY=load_secret_key(),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.getenv("ALGO_HTTPS", "").lower() in {"1", "true", "yes"},
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)


def provision_default_account() -> None:
    if AUTH_FILE.exists():
        return
    payload = {
        "username": DEFAULT_USERNAME,
        "password_hash": DEFAULT_PASSWORD_HASH,
        "created_on": date.today().isoformat(),
    }
    temporary = AUTH_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(AUTH_FILE)
    try:
        AUTH_FILE.chmod(0o600)
    except OSError:
        pass


def load_account() -> dict:
    if not AUTH_FILE.exists():
        provision_default_account()
    data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    if not data.get("username") or not data.get("password_hash"):
        raise ValueError("Authentication configuration is invalid.")
    return data


provision_default_account()


@app.before_request
def cors_preflight():
    if request.method == "OPTIONS":
        return ("", 204)


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def csrf_is_valid() -> bool:
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    return bool(supplied and secrets.compare_digest(supplied, session.get("csrf_token", "")))


def login_key() -> str:
    return request.remote_addr or "local"


def login_is_limited(key: str) -> bool:
    cutoff = monotonic() - LOGIN_WINDOW_SECONDS
    failures = [attempt for attempt in _login_failures.get(key, []) if attempt > cutoff]
    _login_failures[key] = failures
    return len(failures) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(key: str) -> None:
    _login_failures.setdefault(key, []).append(monotonic())


def safe_redirect_target(target: str | None) -> str:
    if not target:
        return url_for("algo_control.page")
    parsed = urlsplit(target)
    if parsed.netloc or parsed.scheme or not target.startswith("/") or target.startswith("//"):
        return url_for("algo_control.page")
    return target


def is_authenticated() -> bool:
    return bool(session.get("username"))


def auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_authenticated():
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("login", next=request.full_path.rstrip("?")))

    return wrapped


app.register_blueprint(create_algo_blueprint(auth_required, csrf_is_valid))


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_authenticated():
        return redirect(url_for("algo_control.page"))
    error = None
    target = request.form.get("next") or request.args.get("next", "")
    if request.method == "POST":
        key = login_key()
        if not csrf_is_valid():
            error = "This form expired. Please try again."
        elif login_is_limited(key):
            error = "Too many failed attempts. Wait five minutes before trying again."
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            try:
                account = load_account()
            except (OSError, ValueError, json.JSONDecodeError):
                return render_template("login.html", csrf_token=csrf_token(), error="Account configuration could not be read.", next=target), 500
            valid_username = secrets.compare_digest(username, account["username"])
            valid_password = check_password_hash(account["password_hash"], password)
            if valid_username and valid_password:
                _login_failures.pop(key, None)
                session.clear()
                session.permanent = True
                session["username"] = account["username"]
                csrf_token()
                return redirect(safe_redirect_target(target))
            record_login_failure(key)
            error = "Invalid user ID or password."
    return render_template("login.html", csrf_token=csrf_token(), error=error, next=target)


@app.post("/logout")
@auth_required
def logout():
    if not csrf_is_valid():
        return jsonify({"error": "Invalid security token."}), 400
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@auth_required
def index():
    return redirect(url_for("algo_control.page"))


@app.get("/styles.css")
def public_styles():
    return send_from_directory(RESOURCE_DIR, "styles.css")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "server": "mt5-algo", "version": 1})


@app.get("/api/session")
@auth_required
def current_session():
    return jsonify({"username": session["username"], "csrf_token": csrf_token()})


@app.get("/api/symbols")
@auth_required
def symbols():
    try:
        source = normalize_source(request.args.get("source", "MT5"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if source == "DELTA":
        try:
            return jsonify(delta_symbols())
        except Exception as exc:
            return jsonify({"error": f"Delta symbol list failed: {exc}"}), 400

    if not mt5.initialize():
        return jsonify(FALLBACK_MT5_SYMBOLS)

    try:
        all_symbols = mt5.symbols_get()
    finally:
        mt5.shutdown()

    if not all_symbols:
        return jsonify(FALLBACK_MT5_SYMBOLS)

    priority = ("BTC", "ETH", "XRP", "SOL", "DOGE", "BNB", "ADA", "XAU", "XAG", "US30", "NAS", "SPX")
    names = sorted({symbol.name for symbol in all_symbols} | set(FALLBACK_MT5_SYMBOLS))
    names.sort(key=lambda name: (not any(term in name.upper() for term in priority), name.upper()))
    return jsonify(names)


@app.post("/api/profile/password")
@auth_required
def update_password():
    if not csrf_is_valid():
        return jsonify({"error": "Invalid security token."}), 400
    try:
        data = request.get_json(force=True)
        current_password = str(data.get("current_password", ""))
        new_password = str(data.get("new_password", ""))
        confirm_password = str(data.get("confirm_password", ""))
        account = load_account()
        if not check_password_hash(account["password_hash"], current_password):
            raise ValueError("Current password is incorrect.")
        if len(new_password) < 8:
            raise ValueError("New password must be at least 8 characters.")
        if new_password != confirm_password:
            raise ValueError("New passwords do not match.")
        if check_password_hash(account["password_hash"], new_password):
            raise ValueError("New password must be different from current password.")
        account["password_hash"] = generate_password_hash(new_password, method="scrypt")
        account["password_updated_on"] = date.today().isoformat()
        temporary = AUTH_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(account, indent=2), encoding="utf-8")
        temporary.replace(AUTH_FILE)
        return jsonify({"message": "Password updated successfully."})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found."}), 404
    return redirect(url_for("algo_control.page"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
