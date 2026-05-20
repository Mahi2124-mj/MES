# EMS Deployment Guide

## Development (Windows)

Double-click `start_all.bat` or run separately:

```bat
start_api.bat         # Flask API on :5000
start_frontend.bat    # Vite dev server on :5173
```

---

## Production Deployment

### 1. Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Set the JWT secret (IMPORTANT!)

The default secret in `auth.py` is only for development.  
In production, set an environment variable before starting Flask:

**Windows CMD:**
```bat
set TB_JWT_SECRET=your-long-random-secret-here
python api_server.py
```

**PowerShell:**
```powershell
$env:TB_JWT_SECRET = "your-long-random-secret-here"
python api_server.py
```

> Generate a strong secret with: `python -c "import secrets; print(secrets.token_hex(32))"`

### 3. Run Flask with Waitress (production WSGI)

```bash
pip install waitress
waitress-serve --host=0.0.0.0 --port=5000 api_server:app
```

### 4. Build the React frontend

```bash
cd frontend
npm install
npm run build        # outputs to frontend/dist/
```

Serve `frontend/dist/` with any static file server (nginx, IIS, etc.).

### 5. Serve with Nginx (optional)

```nginx
server {
    listen 80;
    server_name your-machine-ip;

    root /path/to/frontend/dist;
    index index.html;

    # React Router fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy API to Flask
    location /api/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TB_JWT_SECRET` | `tb-ems-default-secret-change-in-prod` | JWT signing key — **change in production!** |
| `VITE_API_BASE_URL` | `/api` | Frontend: Flask API base path |
| `VITE_STREAM_BASE_URL` | `http://localhost:8050` | Frontend: MJPEG stream server |

---

## Secret Key File

Camera credentials are Fernet-encrypted.  
The encryption key is stored at `backend/secret.key` (auto-generated on first run).

> ⚠️ **Do NOT commit `secret.key` to version control.**  
> Back it up securely. Losing it means camera passwords cannot be decrypted.

---

## Port Summary

| Service | Port | Notes |
|---------|------|-------|
| Flask API | 5000 | `api_server.py` |
| React Dev | 5173 | Vite dev server only |
| MJPEG Streams | 8050 | `dashboard_legacy.py` (optional) |
