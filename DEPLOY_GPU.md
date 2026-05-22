# Deploy AI Paper System (GPU/Remote AI Service)

## Security Notice
- On **May 22, 2026**, exposed credentials were removed from this repository.
- Use your own credentials and rotate old keys/passwords before production deployment.

## Deployment Model
- Backend and frontend run on your app server.
- Neo4j runs on Aura (`neo4j+s://...`).
- LLM/OCR/Embedding can run on remote GPU services via HTTP endpoints.

## 1) Install dependencies
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm
```

## 2) Clone source
```bash
git clone <repo-url> ai-paper-system
cd ai-paper-system
```

## 3) Configure environment
```bash
cp deploy.env.example backend/.env
nano backend/.env
```

Required variables:
- `DATABASE_URL`
- `SECRET_KEY`
- `INTERNAL_API_TOKEN`
- Neo4j user graph: `NEO4J_URI`, `NEO4J_USER` or `NEO4J_USERNAME`, `NEO4J_PASSWORD`
- Neo4j recommendation graph: `NEO4J_REC_URI`, `NEO4J_REC_USER` or `NEO4J_REC_USERNAME`, `NEO4J_REC_PASSWORD`

## 4) Run backend
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 5) Run frontend
```bash
cd frontend
npm install
npm run build
npm run dev -- --host 0.0.0.0 --port 5173
```

## 6) Verify
- Backend docs: `http://<server>:8000/docs`
- Frontend: `http://<server>:5173`

## 7) Production recommendations
- Run backend/frontend with `systemd` or `pm2`.
- Put Nginx/Caddy in front with HTTPS.
- Restrict private AI service endpoints by firewall/VPN.
