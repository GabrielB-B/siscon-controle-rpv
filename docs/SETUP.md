# Setup Local

## Requisitos

- Python 3.11 ou superior
- PowerShell no Windows
- Git

## Instalacao

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Banco local descartavel

```powershell
$env:FLASK_APP = "run.py"
flask db upgrade heads
flask seed-data
```

O banco local sera criado em `instance/controle_rpv.db`. Essa pasta continua fora do Git.

## Subindo localmente

HTTP:

```powershell
python serve_local.py
```

HTTPS local:

```powershell
.\iniciar_servidor_https_local.ps1 -Port 8445 -ForceCert
```

## Testes

```powershell
python -m pytest -q tests
python -m compileall app tests -q
```

## Variaveis principais

```text
FLASK_APP=run.py
SECRET_KEY=defina-uma-chave-local
DATABASE_URL=sqlite:///instance/controle_rpv.db
APP_HOST=127.0.0.1
APP_PORT=8080
SESSION_COOKIE_SECURE=0
NOTIFICATION_DELIVERY_MODE=file
```
