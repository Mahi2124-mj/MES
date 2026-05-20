import requests

# Test via Vite proxy (port 5575 → 5000)
print("=== Direct backend (5000) ===")
r = requests.post("http://127.0.0.1:5000/api/auth/login", json={"username":"admin","password":"admin123"}, timeout=5)
print(f"status={r.status_code} body={r.text[:150]}")

print("\n=== Via Vite proxy (5575) ===")
try:
    r2 = requests.post("http://127.0.0.1:5575/api/auth/login", json={"username":"admin","password":"admin123"}, timeout=5)
    print(f"status={r2.status_code} body={r2.text[:150]}")
except Exception as e:
    print(f"ERR: {e}")
