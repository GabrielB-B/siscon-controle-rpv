# SISCON Controle de RPVs

Aplicacao web Flask para controle financeiro e operacional de RPVs. O sistema organiza requisicoes individuais, pagamentos em lote, pendencias documentais, conciliacao de importacoes, conferencia REINF e BI operacional.

Esta versao foi preparada para portfolio publico. Ela demonstra arquitetura, regras de negocio, testes e operacao local, mas nao inclui banco real, planilhas, backups, certificados, arquivos `.env` reais ou documentos internos.

## Contexto

O projeto nasceu para substituir controles dispersos em planilhas e verificacoes manuais por um sistema local com rastreabilidade, validacao de regras, historico de alteracoes e separacao segura entre codigo e dados sensiveis.

## Destaques

- Controle de RPVs individuais e pagamentos em lote.
- Importacao assistida com validacao, conciliacao e tratamento de duplicidades.
- REINF mensal e anual com agrupamento por competencia.
- BI operacional com filtros, indicadores e visoes de conferencia.
- Auditoria de alteracoes com snapshots antes/depois.
- Recuperacao de senha, notificacao local e trocas obrigatorias de senha.
- Healthcheck operacional, observabilidade basica e trilha local de suporte.
- Throttling persistente em SQLite para proteger login e recuperacao.
- Separacao segura entre codigo versionado e dados operacionais.

## Funcionalidades de Negocio

- Controle de processo, beneficiario, documento, valor bruto, IRRF, valor liquido e status.
- Controle de C.I., lote e item para pagamentos agrupados.
- Competencia operacional protegida para BI e REINF, priorizando o mes de pagamento quando houver quitacao.
- Pendencias documentais para casos ainda nao prontos para o fluxo financeiro.
- Regras fiscais para IRRF, inclusive cenarios sem retencao.
- Edicao controlada de campos sensiveis com confirmacao explicita.
- Auditoria operacional preventiva e healthchecks locais.

## Habilidades Demonstradas

- Flask com Blueprints e SQLAlchemy.
- Modelagem de dominio financeiro e operacional.
- Migrations Alembic para evolucao segura do schema.
- Regras de negocio com `Decimal`.
- Importacao e conciliacao de planilhas.
- Autenticacao, perfis, CSRF e recuperacao de senha.
- Observabilidade basica, logs locais e controles operacionais.
- Testes automatizados de regras de negocio e fluxos web.
- Scripts PowerShell para execucao local e publicacao segura em Windows.

## Stack

- Python
- Flask
- SQLAlchemy
- Flask-Migrate / Alembic
- SQLite em desenvolvimento local
- Jinja2
- HTML, CSS e JavaScript
- PowerShell
- Pytest

## Qualidade e Validacao

A versao publicada foi validada antes do push:

```text
220 testes automatizados passando
Varredura de arquivos sensiveis no Git
Repositorio sem banco, planilhas, certificados, backups ou .env real
Commit publicado com e-mail noreply
```

## Estrutura

```text
app/                         Aplicacao Flask
app/models/                  Modelos SQLAlchemy
app/routes/                  Blueprints e rotas web
app/services/                Regras de negocio, importacao, auditoria e observabilidade
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
python -m pytest -q tests
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
