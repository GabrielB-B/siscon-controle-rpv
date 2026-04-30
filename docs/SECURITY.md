# Seguranca e Privacidade

Esta versao publica foi preparada para demonstrar arquitetura, regras de negocio e qualidade tecnica sem expor dados reais.

## O Que Nao Deve Ir Para o Git

- `instance/`
- bancos `.db`, `.sqlite` e derivados WAL/SHM
- `.env` real
- backups
- certificados e chaves locais
- planilhas de entrada e saida
- PDFs e CSVs operacionais
- senhas, tokens, webhooks e credenciais SMTP
- documentos internos com nomes, processos, IPs ou rotinas privadas

## Controles No Projeto

- `.gitignore` bloqueia bancos, planilhas, backups, runtime, certificados e segredos.
- `.env.example` mostra configuracao sem credenciais reais.
- Chave secreta pode ser gerada localmente em `instance/.secret_key`.
- Senha inicial do admin pode vir de `ADMIN_INITIAL_PASSWORD` ou arquivo local ignorado pelo Git.
- Formularios usam protecao CSRF.
- Senhas de usuario usam hash seguro.
- Login e recuperacao de senha possuem throttling configuravel com armazenamento local.
- Notificacoes de recuperacao podem ficar em `instance/notifications` no modo local, sem sair para o Git.
- Logs e artefatos operacionais permanecem em `instance/` e fora do versionamento.
- Alteracoes relevantes geram historico auditavel.

## Antes De Publicar

Execute uma revisao final:

```powershell
git status --short
git ls-files
rg -n "SECRET_KEY=|DATABASE_URL=|password=|senha=|token=|\\.db|\\.xlsx|\\.csv|\\.pdf"
```

Tambem e recomendavel ativar secret scanning no GitHub apos criar o repositorio.

## Politica de Dados

O repositorio publico deve conter somente codigo, migrations, testes e documentacao sanitizada. Dados reais pertencem ao ambiente operacional e nao fazem parte do controle de versao publico.
