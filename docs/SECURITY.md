# Seguranca e Privacidade

Este espelho publico foi mantido para demonstrar o produto sem expor dados operacionais reais.

## O que fica fora do Git

- `instance/`
- bancos `.db`, `.sqlite` e derivados
- `.env` real
- backups
- planilhas, PDFs e CSVs operacionais
- certificados, chaves e segredos
- arquivos de runtime com dados reais
- documentos privados, juridicos, negociais ou probatorios

## Controles adotados

- `.gitignore` endurecido para dados sensiveis;
- `.env.example` sem credenciais reais;
- separacao entre repositorio privado de trabalho, runtime operacional e espelho publico;
- scripts preparados para operacao local sem exigir segredos no repo;
- logs, notificacoes e artefatos operacionais mantidos fora do versionamento;
- validacao manual antes de push para evitar vazamento acidental.

## Checklist antes de publicar

```powershell
git status --short
git ls-files
rg -n "SECRET_KEY=|DATABASE_URL=|password=|senha=|token=|\\.db|\\.sqlite|\\.xlsx|\\.csv|\\.pdf"
```

## Regra pratica

O Git publico deve mostrar:

- codigo;
- migrations;
- testes;
- documentacao sanitizada.

O ambiente operacional deve guardar:

- banco;
- segredos;
- backups;
- evidencias reais de uso;
- documentos internos.
