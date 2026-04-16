# Arquitetura

O projeto segue uma arquitetura Flask modular, separando interface web, persistência, regras de negócio e rotinas de importação.

## Camadas

```text
routes       Recebem requests, validam formulários e coordenam respostas
services     Concentram regras de negócio, importação, auditoria e notificações
models       Representam entidades persistidas via SQLAlchemy
templates    Renderizam telas Jinja2
static       Mantém CSS e JavaScript de interação
migrations   Evoluem o schema do banco com Alembic
tests        Protegem regras fiscais, importação, segurança e fluxos web
```

## Pontos Técnicos

- `create_app` centraliza a criação da aplicação.
- Blueprints organizam módulos como autenticação, dashboard, requisições individuais, pagamentos em lote, REINF, usuários e histórico.
- SQLAlchemy modela entidades e relacionamentos.
- Flask-Migrate registra evoluções de schema.
- Serviços encapsulam regras que não pertencem diretamente a uma rota.
- Auditoria registra snapshots antes/depois em alterações relevantes.
- A configuração usa variáveis de ambiente e `.env.example`.

## Fluxo de Dados

1. O operador importa ou cadastra informações operacionais.
2. Serviços validam documentos, valores, status e duplicidades.
3. Registros aprovados são persistidos no banco local configurado.
4. Eventos críticos geram histórico auditável.
5. Dashboards e telas de conferência exibem dados consolidados.

## Banco

A versão pública não inclui banco real. Em ambiente local, o banco padrão é criado em `instance/controle_rpv.db`, que está protegido pelo `.gitignore`.

## Evolução

As migrations permitem recriar a estrutura a partir de zero:

```powershell
$env:FLASK_APP = "run.py"
flask db upgrade heads
```
