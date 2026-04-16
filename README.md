# SISCON Controle De RPVs

Aplicacao web Flask desenvolvida para apoiar um fluxo real de controle financeiro/operacional de RPVs, sigla para Requisicoes de Pequeno Valor. O sistema organiza requisicoes individuais, pagamentos agrupados em lotes, pendencias documentais, conciliacao de importacoes e conferencia REINF.

O projeto nasceu para substituir controles dispersos em planilhas e verificacoes manuais por um sistema local com rastreabilidade, validacao de regras, historico de alteracoes e separacao segura entre codigo e dados sensiveis.

Esta versao foi preparada para portfolio publico. Ela demonstra arquitetura, regras de negocio e testes automatizados, mas nao inclui bancos de dados, planilhas, backups, certificados, arquivos `.env` reais ou documentos internos de operacao.

## Contexto

Setores financeiros que lidam com Requisicoes de Pequeno Valor precisam conferir processos, beneficiarios, documentos, valores, imposto retido, status de empenho, ordens bancarias e informacoes de REINF. Esses pagamentos podem aparecer como requisicoes individuais ou como grupos/lotes vinculados a uma comunicacao interna do setor. Quando esse fluxo depende apenas de planilhas, aumenta o risco de duplicidade, retrabalho, perda de historico e alteracoes sem rastreio.

O sistema organiza esse processo em uma aplicacao web local, com foco em seguranca operacional e produtividade da equipe.

## Impactos Do Projeto

- Centralizacao do acompanhamento de requisicoes individuais e pagamentos em lote em uma interface unica.
- Reducao de risco operacional por meio de validacao de documentos, valores, duplicidades e status.
- Rastreabilidade de alteracoes criticas com historico por usuario, data, hora e campos modificados.
- Maior seguranca para dados sensiveis, mantendo banco, planilhas, backups e segredos fora do Git.
- Apoio a tomada de decisao com dashboard, filtros e visoes de conferencia.
- Padronizacao de importacoes com relatorios de pendencias antes da entrada definitiva no fluxo.
- Evolucao controlada do banco com migrations e testes automatizados.

## Destaques

- Cadastro e acompanhamento de Requisicoes de Pequeno Valor individuais e em lote.
- Importacao assistida de planilhas com validacao, pendencias e relatorios de saida.
- Regras fiscais para IRRF, casos sem retencao e conciliacao de pagamentos.
- Auditoria de alteracoes com historico por usuario, data, hora e campos alterados.
- Controle de usuarios, perfis, recuperacao de senha e troca obrigatoria de senha.
- Dashboard operacional com filtros, indicadores e visoes de conferencia.
- Separacao entre codigo, configuracao local e dados sensiveis.
- Suite automatizada com `pytest` cobrindo regras de negocio e fluxos criticos.

## Habilidades Demonstradas

- Desenvolvimento web backend com Flask, Blueprints e SQLAlchemy.
- Modelagem de dominio para fluxo financeiro com processos, pagamentos, impostos e pendencias.
- Criacao de migrations com Alembic para evolucao segura do schema.
- Implementacao de regras de negocio com `Decimal` para valores financeiros.
- Importacao e conciliacao de planilhas com validacao antes da persistencia.
- Auditoria funcional com snapshots antes/depois de alteracoes sensiveis.
- Autenticacao, perfis, recuperacao de senha, CSRF e protecao de sessoes.
- Organizacao de testes automatizados cobrindo comportamento fiscal, operacional e web.
- Scripts PowerShell para execucao local em Windows e suporte a HTTPS local.
- Preparacao profissional de repositorio publico sem exposicao de dados reais.

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

## Qualidade E Validacao

A versao publicada foi validada antes do push:

```text
184 testes automatizados passando
Varredura de arquivos sensiveis no Git
Repositorio sem banco, planilhas, certificados, backups ou .env real
Commit publicado com e-mail noreply
```

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
