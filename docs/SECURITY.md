# Segurança e Privacidade

Esta versão pública foi preparada para demonstrar arquitetura, regras de negócio e qualidade técnica sem expor dados reais.

## O Que Não Deve Ir Para o Git

- `instance/`
- bancos `.db`, `.sqlite` e derivados WAL/SHM;
- `.env` real;
- backups;
- certificados e chaves locais;
- planilhas de entrada e saída;
- PDFs e CSVs operacionais;
- senhas, tokens, webhooks e credenciais SMTP;
- documentos internos com nomes, processos, IPs ou rotinas privadas.

## Controles No Projeto

- `.gitignore` bloqueia bancos, planilhas, backups, runtime, certificados e segredos.
- `.env.example` mostra configuração sem credenciais reais.
- Chave secreta pode ser gerada localmente em `instance/.secret_key`.
- Senha inicial do admin pode vir de `ADMIN_INITIAL_PASSWORD` ou arquivo local ignorado pelo Git.
- Formulários usam proteção CSRF.
- Senhas de usuário usam hash seguro.
- Alterações relevantes geram histórico auditável.

## Antes De Publicar

Execute uma revisão final:

```powershell
git status --short
git ls-files
rg -n "SECRET_KEY=|DATABASE_URL=|password=|senha=|token=|\\.db|\\.xlsx|\\.csv|\\.pdf"
```

Também é recomendável ativar secret scanning no GitHub após criar o repositório.

## Política de Dados

O repositório público deve conter somente código, migrations, testes e documentação sanitizada. Dados reais pertencem ao ambiente operacional e não fazem parte do controle de versão público.
