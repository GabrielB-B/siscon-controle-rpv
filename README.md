# SISCON Controle de RPVs

Aplicacao web Flask para controle operacional e financeiro de RPVs, dativos, cotas mensais, REINF e BI operacional.

Esta pasta e o espelho publico do sistema, preparada para portfolio tecnico. O objetivo aqui e demonstrar arquitetura, qualidade de codigo, organizacao de produto e disciplina operacional sem expor banco real, planilhas, backups, certificados, segredos ou documentos internos.

## O que este repositorio demonstra

- Monolito modular em Flask com crescimento controlado.
- Dominio real de RPVs, dativos, cotas, REINF e BI.
- Separacao entre `routes`, `services`, `models`, `templates` e `tests`.
- Evolucao de schema via Alembic.
- Regras financeiras e fiscais com `Decimal`.
- Auditoria de alteracoes sensiveis.
- Healthcheck operacional e observabilidade local.
- Scripts Windows para execucao, backup e publicacao segura.
- Suite automatizada com `268` testes passando na sincronizacao desta copia.

## Principais modulos

- `RPVs normais`: cadastro, lista, filtros, pendencias e cruzamentos.
- `RPVs dativos`: C.I., lotes, itens, conciliacao e revisao operacional.
- `Cotas`: saldo mensal por ficha, consumo, transferencia e historico.
- `REINF`: recortes mensal/anual, conferencia fiscal e exportacao.
- `BI`: leitura executiva, series operacionais, beneficiarios e filtros.
- `Usuarios e seguranca`: login, troca de senha, recuperacao e perfis.

## Estado arquitetural atual

O projeto evoluiu para um patamar mais profissional sem reescrita grande. As melhorias mais relevantes desta fase publica foram:

- modularizacao do `BI` em servicos dedicados;
- modularizacao de `dativos`, `REINF` e lista principal de `RPVs`;
- separacao progressiva da suite por dominio;
- endurecimento da camada operacional para crescimento futuro;
- reforco da governanca entre `dev`, `runtime` e espelho publico.

## Qualidade e validacao

Na sincronizacao desta copia publica, a validacao considerada foi:

```text
268 testes automatizados passando
python -m compileall app tests -q sem erro
varredura de seguranca sem banco real, instance, .env real, backups ou docs privados rastreados
```

## Estrutura

```text
app/                   aplicacao Flask
migrations/            historico Alembic
tests/                 testes automatizados
docs/                  documentacao publica do portfolio
run.py                 entrada Flask
serve_local.py         servidor HTTP local
serve_https_local.py   servidor HTTPS local
```

## Como rodar localmente

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

Endereco padrao:

```text
http://127.0.0.1:8080/login
```

Para HTTPS local:

```powershell
.\iniciar_servidor_https_local.ps1 -Port 8445 -ForceCert
```

## Testes

```powershell
python -m pytest -q tests
```

## Documentacao publica

- [Indice da documentacao](docs/README.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Funcionalidades](docs/FEATURES.md)
- [Seguranca](docs/SECURITY.md)
- [Setup local](docs/SETUP.md)
- [Notas de portfolio](docs/PORTFOLIO.md)

## Higiene de publicacao

Este repositorio deve permanecer sem:

- bancos SQLite reais;
- pasta `instance/`;
- backups;
- planilhas e PDFs operacionais;
- `.env` real;
- certificados e chaves locais;
- material juridico, negocial ou privado.

## Autor

Gabriel Bomfim Bispo
