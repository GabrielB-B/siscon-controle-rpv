# Arquitetura

O projeto segue uma arquitetura Flask modular, separando interface web, persistencia, regras de negocio e rotinas de importacao.

## Camadas

```text
routes       Recebem requests, validam formularios e coordenam respostas
services     Concentraram regras de negocio, importacao, auditoria e notificacoes
models       Representam entidades persistidas via SQLAlchemy
templates    Renderizam telas Jinja2
static       Mantem CSS e JavaScript de interacao
migrations   Evoluem o schema do banco com Alembic
tests        Protegem regras fiscais, importacao, seguranca e fluxos web
```

## Pontos Tecnicos

- `create_app` centraliza a criacao da aplicacao.
- Blueprints organizam modulos como autenticacao, dashboard, RPVs, dativos, REINF, usuarios e historico.
- SQLAlchemy modela entidades e relacionamentos.
- Flask-Migrate registra evolucoes de schema.
- Servicos encapsulam regras que nao pertencem diretamente a uma rota.
- Auditoria registra snapshots antes/depois em alteracoes relevantes.
- A configuracao usa variaveis de ambiente e `.env.example`.

## Fluxo De Dados

1. O operador importa ou cadastra informacoes operacionais.
2. Servicos validam documentos, valores, status e duplicidades.
3. Registros aprovados sao persistidos no banco local configurado.
4. Eventos criticos geram historico auditavel.
5. Dashboards e telas de conferencia exibem dados consolidados.

## Banco

A versao publica nao inclui banco real. Em ambiente local, o banco padrao e criado em `instance/controle_rpv.db`, que esta protegido pelo `.gitignore`.

## Evolucao

As migrations permitem recriar a estrutura a partir de zero:

```powershell
$env:FLASK_APP = "run.py"
flask db upgrade heads
```
