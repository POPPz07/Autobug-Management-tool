"""Demo server: Flask app simulating a buggy login page for E2E testing."""

from flask import Flask, request

app = Flask(__name__)


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ShopEasy — Login</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Inter', sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }
    .login-wrapper {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 20px;
      width: 100%;
      max-width: 420px;
      padding: 20px;
    }
    .login-card {
      width: 100%;
      background: #fff;
      border-radius: 16px;
      padding: 40px 36px 36px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.15);
    }
    .brand { text-align: center; margin-bottom: 28px; }
    .brand h1 { font-size: 1.6rem; color: #1e293b; font-weight: 700; }
    .brand p { font-size: 0.85rem; color: #64748b; margin-top: 4px; }
    .form-group { margin-bottom: 18px; }
    .form-group label {
      display: block; font-size: 0.82rem; font-weight: 500;
      color: #475569; margin-bottom: 6px;
    }
    .form-group input {
      width: 100%; padding: 12px 14px; border: 1.5px solid #e2e8f0;
      border-radius: 8px; font-size: 0.9rem; font-family: inherit;
      color: #1e293b; background: #f8fafc; outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .form-group input:focus {
      border-color: #6366f1;
      box-shadow: 0 0 0 3px rgba(99,102,241,0.12);
      background: #fff;
    }
    .form-group input::placeholder { color: #94a3b8; }
    .btn-login {
      width: 100%; padding: 13px; border: none; border-radius: 8px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff;
      font-size: 0.92rem; font-weight: 600; font-family: inherit;
      cursor: pointer; transition: opacity 0.2s, transform 0.1s;
      margin-top: 4px;
    }
    .btn-login:hover { opacity: 0.92; }
    .btn-login:active { transform: scale(0.98); }
    .error-box {
      margin-top: 16px; padding: 12px 16px;
      background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px;
      color: #dc2626; font-size: 0.85rem; font-weight: 500;
      display: flex; align-items: center; gap: 8px;
    }
    .error-box::before { content: '⚠️'; }
    .bug-explainer {
      width: 100%;
      background: rgba(255,255,255,0.15);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(255,255,255,0.25);
      border-radius: 12px;
      padding: 20px 24px;
      color: #fff;
    }
    .bug-explainer h3 {
      font-size: 0.9rem; font-weight: 600;
      display: flex; align-items: center; gap: 8px;
      margin-bottom: 10px;
    }
    .bug-explainer p { font-size: 0.8rem; line-height: 1.6; opacity: 0.9; }
    .bug-explainer code {
      background: rgba(0,0,0,0.2); padding: 2px 6px; border-radius: 4px;
      font-size: 0.78rem;
    }
    .bug-tag {
      display: inline-block; padding: 3px 10px; border-radius: 999px;
      background: rgba(239,68,68,0.25); color: #fca5a5; font-size: 0.72rem;
      font-weight: 600; letter-spacing: 0.03em;
    }
  </style>
</head>
<body>
  <div class="login-wrapper">
    <div class="login-card">
      <div class="brand">
        <h1>🛒 ShopEasy</h1>
        <p>Sign in to your account</p>
      </div>
      <form method="post" action="/login">
        <div class="form-group">
          <label for="username">Username</label>
          <input id="username" name="username" type="text" placeholder="Enter your username" autocomplete="off" />
        </div>
        <div class="form-group">
          <label for="password">Password</label>
          <input id="password" name="password" type="password" placeholder="Enter your password" />
        </div>
        <button id="submit" type="submit" class="btn-login">Sign In</button>
        {error_html}
      </form>
    </div>
    <div class="bug-explainer">
      <h3>🐛 Intentional Bug <span class="bug-tag">FOR DEMO</span></h3>
      <p>
        This login page has a <strong>hardcoded authentication bug</strong>.
        The server <code>always returns "Invalid credentials"</code> regardless of what
        username and password you enter — even with correct ones.
        The backend never actually validates the credentials against a database;
        it unconditionally rejects every login attempt. AutoRepro will detect and
        reproduce this bug automatically.
      </p>
    </div>
  </div>
</body>
</html>"""


@app.route("/login")
def login():
    """Render the login form."""
    return LOGIN_PAGE.replace("{error_html}", "")


@app.route("/login", methods=["POST"])
def login_post():
    """Always return 'Invalid credentials' — simulates the bug."""
    error_html = '<div id="error" class="error-box">Invalid credentials</div>'
    return LOGIN_PAGE.replace("{error_html}", error_html)


if __name__ == "__main__":
    app.run(port=8080)
