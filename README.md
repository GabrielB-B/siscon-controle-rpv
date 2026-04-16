# SISCON Controle de RPVs

Aplicação web Flask desenvolvida para apoiar um fluxo real de controle financeiro/operacional de RPVs, sigla para Requisições de Pequeno Valor. O sistema organiza requisições individuais, pagamentos agrupados em lotes, pendências documentais, conciliação de importações e conferência REINF.

O projeto nasceu para substituir controles dispersos em planilhas e verificações manuais por um sistema local com rastreabilidade, validação de regras, histórico de alterações e separação segura entre código e dados sensíveis.

Esta versão foi preparada para portfólio público. Ela demonstra arquitetura, regras de negócio e testes automatizados, mas não inclui bancos de dados, planilhas, backups, certificados, arquivos `.env` reais ou documentos internos de operação.

## Contexto

Setores financeiros que lidam com Requisições de Pequeno Valor precisam conferir processos, beneficiários, documentos, valores, imposto retido, status de empenho, ordens bancárias e informações de REINF. Esses pagamentos podem aparecer como requisições individuais ou como grupos/lotes vinculados a uma comunicação interna do setor. Quando esse fluxo depende apenas de planilhas, aumenta o risco de duplicidade, retrabalho, perda de histórico e alterações sem rastreio.

O sistema organiza esse processo em uma aplicação web local, com foco em segurança operacional e produtividade da equipe.

## Impactos do Projeto

- Centralização do acompanhamento de requisições individuais e pagamentos em lote em uma interface única.
- Redução de risco operacional por meio de validação de documentos, valores, duplicidades e status.
- Rastreabilidade de alterações críticas com histórico por usuário, data, hora e campos modificados.
- Maior segurança para dados sensíveis, mantendo banco, planilhas, backups e segredos fora do Git.
- Apoio à tomada de decisão com dashboard, filtros e visões de conferência.
- Padronização de importações com relatórios de pendências antes da entrada definitiva no fluxo.
- Evolução controlada do banco com migrations e testes automatizados.

## Destaques

- Cadastro e acompanhamento de Requisições de Pequeno Valor individuais e em lote.
- Importação assistida de planilhas com validação, pendências e relatórios de saída.
- Regras fiscais para IRRF, casos sem retenção e conciliação de pagamentos.
- Auditoria de alterações com histórico por usuário, data, hora e campos alterados.
- Controle de usuários, perfis, recuperação de senha e troca obrigatória de senha.
- Dashboard operacional com filtros, indicadores e visões de conferência.
- Separação entre código, configuração local e dados sensíveis.
- Suite automatizada com `pytest` cobrindo regras de negócio e fluxos críticos.

## Habilidades Demonstradas

- Desenvolvimento web backend com Flask, Blueprints e SQLAlchemy.
- Modelagem de domínio para fluxo financeiro com processos, pagamentos, impostos e pendências.
- Criação de migrations com Alembic para evolução segura do schema.
- Implementação de regras de negócio com `Decimal` para valores financeiros.
- Importação e conciliação de planilhas com validação antes da persistência.
- Auditoria funcional com snapshots antes/depois de alterações sensíveis.
- Autenticação, perfis, recuperação de senha, CSRF e proteção de sessões.
- Organização de testes automatizados cobrindo comportamento fiscal, operacional e web.
- Scripts PowerShell para execução local em Windows e suporte a HTTPS local.
- Preparação profissional de repositório público sem exposição de dados reais.

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

## Qualidade e Validação

A versão publicada foi validada antes do push:

```text
184 testes automatizados passando
Varredura de arquivos sensíveis no Git
Repositório sem banco, planilhas, certificados, backups ou .env real
Commit publicado com e-mail noreply
```

## Estrutura

```text
app/                         Aplicação Flask
app/models/                  Modelos SQLAlchemy
app/routes/                  Blueprints e rotas web
app/services/                Regras de negócio, importação e auditoria
app/templates/               Templates Jinja2
app/static/                  CSS e JavaScript
migrations/                  Histórico de migrations Alembic
tests/                       Testes automatizados
docs/                        Documentação pública e sanitizada
.env.example                 Exemplo de variáveis locais
.gitignore                   Proteção contra dados sensíveis
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

## Documentação

- [Arquitetura](docs/ARCHITECTURE.md)
- [Funcionalidades](docs/FEATURES.md)
- [Segurança e privacidade](docs/SECURITY.md)
- [Setup local](docs/SETUP.md)
- [Notas de portfólio](docs/PORTFOLIO.md)

## Privacidade

Este repositório foi sanitizado para publicação. Os dados reais devem permanecer fora do Git:

- bancos SQLite;
- planilhas de entrada e saída;
- backups;
- certificados locais;
- arquivos `.env`;
- senhas, tokens e chaves privadas;
- documentos operacionais internos.

## Autor

Gabriel Bomfim Bispo
