# Controle RPV

Aplicacao web Flask para controle operacional de RPVs normais, dativos, pendencias documentais, conciliacao de importacoes e conferencia REINF.

Esta versao foi preparada para portfolio publico. Ela nao inclui bancos de dados, planilhas, backups, certificados, arquivos `.env` reais ou documentos internos de operacao.

## Destaques

- Cadastro e acompanhamento de RPVs normais e dativos.
- Importacao assistida de planilhas com validacao, pendencias e relatorios de saida.
- Regras fiscais para IRRF, casos sem retencao e conciliacao de pagamentos.
- Auditoria de alteracoes com historico por usuario, data, hora e campos alterados.
- Controle de usuarios, perfis, recuperacao de senha e troca obrigatoria de senha.
- Dashboard operacional com filtros, indicadores e visoes de conferencia.
- Separacao entre codigo, configuracao local e dados sensiveis.
- Suite automatizada com `pytest` cobrindo regras de negocio e fluxos criticos.

## Stack

- Python
- Flask
- SQLAlchemy
- Flask-Migrate / Alembic
- SQLite em desenvolvimento local
- Jinja2
- HTML, CSS e JavaScript
- PowerShell para scripts locais no Windows
- Pytest

## Estrutura

```text
app/                         Aplicacao Flask
app/models/                  Modelos SQLAlchemy
app/routes/                  Blueprints e rotas web
app/services/                Regras de negocio, importacao e auditoria
app/templates/               Templates Jinja2
app/static/                  CSS e JavaScript
migrations/                  Historico de migrations Alembic
tests/                       Testes automatizados
docs/                        Documentacao publica e sanitizada
.env.example                 Exemplo de variaveis locais
.gitignore                   Protecao contra dados sensiveis
```

## Como Rodar Localmente

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
$env:FLASK_APP = "run.py"
flask db upgrade heads
flask seed-data
python serve_local.py
```

Depois acesse:

```text
http://127.0.0.1:8080/login
```

Para testar HTTPS local no Windows:

```powershell
.\iniciar_servidor_https_local.ps1 -Port 8445 -ForceCert
```

## Testes

```powershell
pytest
```

## Documentacao

- [Arquitetura](docs/ARCHITECTURE.md)
- [Funcionalidades](docs/FEATURES.md)
- [Seguranca e privacidade](docs/SECURITY.md)
- [Setup local](docs/SETUP.md)
- [Notas de portfolio](docs/PORTFOLIO.md)

## Privacidade

Este repositorio foi sanitizado para publicacao. Os dados reais devem permanecer fora do Git:

- bancos SQLite;
- planilhas de entrada e saida;
- backups;
- certificados locais;
- arquivos `.env`;
- senhas, tokens e chaves privadas;
- documentos operacionais internos.

## Autor

Gabriel Bomfim Bispo
