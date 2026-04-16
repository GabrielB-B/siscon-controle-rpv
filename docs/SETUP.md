# Setup Local

## Requisitos

- Python 3.11 ou superior
- PowerShell no Windows
- Git

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Banco Local

```powershell
$env:FLASK_APP = "run.py"
flask db upgrade heads
flask seed-data
```

O banco será criado em `instance/controle_rpv.db`. Essa pasta é ignorada pelo Git.

## Servidor HTTP

```powershell
python serve_local.py
```

Endereço padrão:

```text
http://127.0.0.1:8080/login
```

## Servidor HTTPS Local

```powershell
.\iniciar_servidor_https_local.ps1 -Port 8445 -ForceCert
```

O certificado gerado fica em `instance/certs/`, também ignorado pelo Git.

## Testes

```powershell
pytest
```

## Variáveis Principais

```text
FLASK_APP=run.py
SECRET_KEY=defina-uma-chave-local
DATABASE_URL=sqlite:///instance/controle_rpv.db
APP_HOST=127.0.0.1
APP_PORT=8080
SESSION_COOKIE_SECURE=0
```
