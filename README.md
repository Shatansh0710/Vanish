# Vanish: self-destructing secret notes, in Docker on Amazon EC2

Vanish lets you share a password or private message as a link that **deletes itself** after a set
number of views or a time limit, whichever comes first.

- **Encrypted in the browser** (AES-256-GCM). The key is only in the part of the link after `#`,
  which browsers never send to servers, so the server stores text it can't read.
- **Optional passphrase** encrypts the note a second time.
- **Atomic view counting:** one SQLite `UPDATE … RETURNING` statement, so two people can't both get the last view.
- **Automatic cleanup:** expired notes are deleted every minute.
- **Deployment:** one EC2 instance running two Docker containers via Docker Compose.

## Architecture

```
                 Amazon EC2 instance (t3.micro, Ubuntu 24.04, ap-south-1)
              ┌─────────────────────────────────────────────────────────────┐
 Browser ──►  │ Security group: 22 (SSH, your IP)  80, 443 (anyone)          │
 (encrypts,   │                                                             │
  decrypts)   │  ┌──────────── Docker Compose ─────────────────────────────┐ │
   HTTPS ───────►│ caddy  :80 :443  free Let's Encrypt cert for the IP   │ │
              │  │    │ reverse_proxy                                     │ │
              │  │    ▼                                                   │ │
              │  │ app    :8000  Flask + Gunicorn (Python 3.13)           │ │
              │  │    │                                                   │ │
              │  │    ▼                                                   │ │
              │  │ volume "notes" → /data/vanish.db (SQLite)              │ │
              │  └────────────────────────────────────────────────────────┘ │
              │  EBS gp3 8 GB root volume holds Docker images and volumes    │
              └─────────────────────────────────────────────────────────────┘
```

| Piece | What it does |
|---|---|
| `app` container | Serves the pages and the JSON API; stores encrypted notes in SQLite |
| `caddy` container | Public HTTPS on ports 80/443; gets and renews a free certificate; forwards to `app` |
| EC2 instance | Runs Docker. User data installs Docker on first boot |
| Security group | Firewall: SSH only from your IP, web from anywhere |
| EBS volume | Disk for the OS, Docker images and the notes database |

### API

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/health` | Liveness check (used by the Docker health check) |
| `POST` | `/api/notes` | Create: `ciphertext`, `iv`, `ttlSeconds` (300, 3600, 86400, 604800), `maxViews` (1–10), `protected` |
| `GET` | `/api/notes/{id}` | Views left and expiry; doesn't use a view |
| `POST` | `/api/notes/{id}/open` | Use one view, return the ciphertext (`410` once gone) |
| `DELETE` | `/api/notes/{id}` | Destroy early; header `x-delete-token` |

## Cost

A new AWS account on the **Free plan** gets $100 of credit; `t3.micro` is Free Tier eligible and is
paid from that credit. Roughly: instance $0.0112/hour (~$8.20/month in Mumbai) + public IPv4 address
~$3.60/month + 8 GB disk ~$0.75/month ≈ **$12–13/month of credit, ₹0 out of pocket**. Stop the
instance when you're not using it to save credit (disk and IP charges are small).
On an older account with no Free Tier left, the same setup bills about that amount to your card.

## Repository layout

```
vanish/
├── app/
│   ├── main.py            # Flask app: API, pages, SQLite, cleanup thread
│   ├── pages/             # index.html, view.html, about.html, 404.html
│   └── static/            # app.js, view.js, crypto.js, style.css, vendor/qrcode.js
├── Dockerfile             # app image
├── compose.yaml           # app + caddy
├── Caddyfile              # HTTPS + reverse proxy
├── requirements.txt
├── scripts/
│   └── build-for-ec2.sh   # run on a Mac/laptop: builds linux/amd64 images, saves them, zips the project
├── deploy/
│   ├── user-data.sh       # paste into EC2 "User data": installs Docker on first boot
│   └── deploy.sh          # run on the instance: finds its IP, loads (or builds) images, starts
├── dist/                  # created by build-for-ec2.sh: vanish-images.tar.gz (not in Git)
├── slides/                # LaTeX Beamer deck (vanish-slides.tex), PDFs, screenshots
├── docs/                  # the end-to-end guide as a PDF
└── tests/test_app.py      # 16 pytest tests
```

## 1. Run it on your laptop

**With Docker Desktop** (recommended):

```bash
docker compose up app                 # builds vanish-app:1.0 the first time
# or, with the ready-built images:  docker load -i dist/vanish-images.tar.gz && docker compose up app
# open http://localhost:8000   (Ctrl+C to stop)
```

**Without Docker** (Python 3.11+):

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q                            # 16 passed
python app/main.py                   # open http://localhost:8000
```

## 2. Build the images on one computer, deploy from another

The images are built once (for example on a Mac), carried as one zip, and loaded on EC2.
`docs/Vanish_EC2_Docker_Guide.pdf` has every step in detail.

**On the build computer (Mac/Linux with Docker Desktop running):**

```bash
bash scripts/build-for-ec2.sh
# -> dist/vanish-images.tar.gz  (app + caddy images, linux/amd64)
# -> ../vanish-transfer.zip     (whole project incl. dist/) - copy this to the work laptop
```

It always builds for `linux/amd64`, because a t3.micro is x86_64 and Apple Silicon Macs build
arm64 by default (which would fail on EC2 with `exec format error`).

**On AWS (from the work laptop):**

1. **Launch an instance** (EC2 console, region *Asia Pacific (Mumbai)*):
   - Name `vanish`, AMI **Ubuntu Server 24.04 LTS** (64-bit x86), instance type **t3.micro**.
   - Key pair: *Create new key pair* → `vanish-key`, RSA, `.pem`.
   - Network settings: *Allow SSH traffic from* **My IP**; tick **Allow HTTPS** and **Allow HTTP** from the internet.
   - Storage: 8 GiB gp3 (default).
   - Advanced details → **User data**: paste the whole of `deploy/user-data.sh`.
   - **Launch**, wait for *3/3 checks passed*, copy the **Public IPv4 address**.
2. **Upload the zip** (from the folder holding `vanish-transfer.zip` and `vanish-key.pem`):
   ```bash
   scp -i vanish-key.pem vanish-transfer.zip ubuntu@<PUBLIC-IP>:~
   ```
3. **Log in and deploy:**
   ```bash
   ssh -i vanish-key.pem ubuntu@<PUBLIC-IP>
   cat ~/READY.txt                      # "Docker is ready" (if missing, wait a minute)
   unzip -q vanish-transfer.zip && cd vanish
   bash deploy/deploy.sh                # loads dist/vanish-images.tar.gz, starts both containers
   ```
4. Open **https://\<PUBLIC-IP\>** (the certificate can take up to a minute the first time).

No `dist/` folder? `deploy.sh` builds the image on the instance instead (2–4 minutes).
Force that with `bash deploy/deploy.sh --build`.

## 3. Day-to-day commands (on the instance, inside `~/vanish`)

| Task | Command |
|---|---|
| See containers | `docker compose ps` |
| Logs | `docker compose logs -f app` / `docker compose logs -f caddy` |
| Rebuild after editing code on the instance | `bash deploy/deploy.sh --build` |
| Use a newly built zip | upload it, then `unzip -oq vanish-transfer.zip && cd vanish && bash deploy/deploy.sh` |
| After stop/start (new IP) | `bash deploy/deploy.sh` |
| Restart | `docker compose restart` |
| Stop the app | `docker compose down` (notes are kept in the volume) |
| Open the database | `docker compose exec app python -c "import sqlite3;print(sqlite3.connect('/data/vanish.db').execute('select id,view_count,max_views,expires_at from notes').fetchall())"` |

## 4. Tear down

EC2 console → Instances → select `vanish` → **Instance state → Terminate**. This deletes the
instance, its disk and all notes. Then delete the `vanish-key` key pair and the `launch-wizard-*`
security group if you no longer need them.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ssh: Permission denied (publickey)` | Use `ubuntu@` as the user and the right `.pem`. On macOS/Linux run `chmod 400 vanish-key.pem`. |
| `WARNING: UNPROTECTED PRIVATE KEY FILE` on Windows | In PowerShell: `icacls vanish-key.pem /inheritance:r /grant:r "${env:USERNAME}:R"` |
| SSH times out | Your IP changed: edit the security group's SSH rule to *My IP* again. |
| `docker: command not found` | User data is still running. Wait, then check `cat ~/READY.txt`. |
| `permission denied ... docker.sock` | Log out and back in (`exit`, then `ssh` again). `deploy.sh` already falls back to `sudo`. |
| Browser shows a certificate error | Wait a minute and reload. Check `docker compose logs caddy`. Port 80 must be open to the internet for Let's Encrypt to verify the IP. |
| Certificate still fails | Use a domain instead: point a DNS A record (any registrar, or a free DuckDNS name) at the IP, then `bash deploy/deploy.sh your.domain`. |
| "Encryption needs a secure connection" | You opened `http://`. Use `https://`. |
| Site stopped working after Stop/Start | The public IP changed. Run `bash deploy/deploy.sh` and use the new IP. |
| `The image is built for arm64 but this instance is amd64` / `exec format error` | Rebuild with `bash scripts/build-for-ec2.sh`, or run `bash deploy/deploy.sh --build`. |

## Security notes

- The server stores only ciphertext plus timestamps and view counts.
- AES-GCM is authenticated; tampered data fails to decrypt. Passphrases use PBKDF2-SHA256 (310,000 rounds).
- The app container runs as a non-root user; port 8000 is bound to `127.0.0.1` only, so the internet reaches the app only through Caddy.
- Strict Content-Security-Policy, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, HSTS.
- Delete tokens are stored as SHA-256 hashes. Note creation is limited to 20 per minute per IP.
- Limitation: anyone with the full link can read the note. Use a passphrase for sensitive notes.
