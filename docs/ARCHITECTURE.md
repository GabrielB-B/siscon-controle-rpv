# Arquitetura

O SISCON e um monolito modular em Flask, com foco em rastreabilidade, seguranca operacional e evolucao incremental sem reescritas grandes.

## Camadas principais

```text
routes       entrada HTTP e orquestracao de fluxo
services     regra de negocio, datasets, exportacoes e contexto
models       entidades SQLAlchemy e integridade de dominio
templates    interface Jinja2
static       CSS e JavaScript
utils        regras compartilhadas e funcoes de suporte
migrations   evolucao de schema com Alembic
tests        regressao funcional e regras de negocio
```

## Evolucao estrutural demonstrada

### 1. BI mais modular

O modulo de BI deixou de concentrar tudo em uma unica rota e passou a usar servicos dedicados para:

- filtros;
- dataset;
- contexto;
- metricas operacionais;
- exportacao;
- beneficiarios;
- projecoes.

### 2. Modulos operacionais mais finos

As rotas de `dativos`, `REINF` e `RPVs` passaram por extracao progressiva para servicos de:

- filtros;
- queries e datasets;
- montagem de contexto;
- exportacoes especificas.

### 3. Suite em divisao progressiva

A base de testes deixou de depender somente de um arquivo gigante e passou a separar infraestrutura comum e dominios especificos, reduzindo custo de manutencao.

## Dominio representado

- `RPVs normais`
- `RPVs dativos`
- `Cotas mensais`
- `REINF`
- `BI operacional`
- `Usuarios e seguranca`
- `Auditoria e observabilidade`

## Persistencia

- SQLAlchemy como ORM
- Alembic para migrations
- SQLite para ambiente local e runtime assistida desta fase

O repositorio publico nao inclui banco real. Em ambiente local, o banco padrao e criado em `instance/controle_rpv.db`, que fica fora do versionamento.

## Principios arquiteturais

- preservar comportamento antes de refatorar;
- mover implementacao interna sem quebrar rota, tela ou contrato;
- manter `dev`, `runtime` e espelho publico separados;
- tratar dados reais como responsabilidade do ambiente, nao do Git;
- documentar checkpoints e validacao a cada frente relevante.
