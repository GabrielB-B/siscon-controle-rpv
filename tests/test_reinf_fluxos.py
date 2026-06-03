from tests.rpv_test_case_base import *
from tests.rpv_test_case_base import _query_dativos_reinf, _query_rpvs_reinf


class ReinfFluxosTestCase(BaseRPVSemIRRFTestCase):
    __test__ = True

    def test_reinf_preserva_retorno_ao_abrir_rpv(self):
        registro = self._criar_rpv(
            nome_beneficiario="Retorno REINF",
            documento_original="39053344705",
            numero_processo="PROC-RETORNO-REINF",
            valor_irrf=Decimal("320.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 15),
        )

        resposta = self.client.get(
            "/reinf/?competencia=2026-03&reinf_status=todos&q=Retorno&responsavel=todos&ordenar=imposto&direcao=desc"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn(f"/rpvs/{registro.id}/editar?retorno=", html)
        href_inicio = html.index(f'/rpvs/{registro.id}/editar?retorno=')
        href_fim = html.index('"', href_inicio)
        abrir_href = html[href_inicio:href_fim]
        retorno = parse_qs(urlsplit(abrir_href).query).get("retorno", [""])[0]

        self.assertTrue(retorno.startswith("/reinf/?competencia=2026-03"))
        self.assertIn("q=Retorno", retorno)
        self.assertIn("responsavel=todos", retorno)
        self.assertIn("ordenar=imposto", retorno)
        self.assertIn("direcao=desc", retorno)

    def test_reinf_atualizacao_status_preserva_retorno_explicito(self):
        registro = self._criar_rpv(
            nome_beneficiario="Status REINF Retorno",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        retorno = "/reinf/?competencia=2026-03&reinf_status=todos&q=Retorno&responsavel=todos"

        resposta = self.client.post(
            "/reinf/atualizar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "reinf_status": "Concluído",
                "retorno": retorno,
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["Location"], retorno)

    def test_reinf_atualizacao_status_ignora_retorno_externo(self):
        registro = self._criar_rpv(
            nome_beneficiario="Status REINF Externo",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )

        resposta = self.client.post(
            "/reinf/atualizar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "reinf_status": "Concluído",
                "retorno": "https://exemplo.invalid/reinf",
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["Location"], "/reinf/")

    def test_reinf_precarrega_situacoes_para_filtro_de_cancelamento(self):
        registro = self._criar_rpv(
            nome_beneficiario="REINF Eager RPV",
            valor_irrf=Decimal("210.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 12),
        )
        _, item = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-REINF-EAGER",
            nome_beneficiario="REINF Eager Dativo",
            cpf_original="10987654321",
            numero_processo="PROC-REINF-EAGER",
            valor_bruto=Decimal("5300.00"),
            valor_irrf=Decimal("530.00"),
        )
        item.data_pagamento = date(2026, 3, 14)
        db.session.commit()

        registro_reinf = _query_rpvs_reinf(
            competencia="2026-03",
            filtro_responsavel="todos",
            filtro_busca="",
            ano=None,
        ).filter(RegistroRPV.id == registro.id).one()
        item_reinf = _query_dativos_reinf(
            competencia="2026-03",
            filtro_responsavel="todos",
            filtro_busca="",
            ano=None,
        ).filter(DativoItem.id == item.id).one()

        self.assertNotIn("situacao_empenho", sa_inspect(registro_reinf).unloaded)
        self.assertNotIn("situacao_rpv", sa_inspect(item_reinf).unloaded)

    def test_reinf_ignora_registro_sem_data_pagamento(self):
        self._criar_rpv(
            nome_beneficiario="Sem Data REINF",
            valor_irrf=Decimal("500.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=None,
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("Sem Data REINF", html)

    def test_reinf_bloqueia_competencia_posterior_com_pendencia_anterior(self):
        self._criar_rpv(
            nome_beneficiario="REINF Marco Pendente",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        self._criar_rpv(
            nome_beneficiario="REINF Abril Pendente",
            valor_irrf=Decimal("510.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 4, 3),
        )

        resposta = self.client.get("/reinf/?competencia=2026-04")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('value="2026-03"', html)
        self.assertIn("fechamento do REINF permanece em", html)
        self.assertIn("REINF Marco Pendente", html)
        self.assertNotIn("REINF Abril Pendente", html)

    def test_reinf_cancelado_nao_bloqueia_competencia_posterior(self):
        registro_marco = self._criar_rpv(
            nome_beneficiario="REINF Marco Cancelado",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro_marco.reinf_status = "Cancelado"
        db.session.commit()

        self._criar_rpv(
            nome_beneficiario="REINF Abril Livre",
            valor_irrf=Decimal("510.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 4, 3),
        )

        resposta = self.client.get("/reinf/?competencia=2026-04&reinf_status=todos")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn('value="2026-04"', html)
        self.assertNotIn("fechamento do REINF permanece em", html)
        self.assertIn("REINF Abril Livre", html)

    def test_reinf_exporta_csv_com_documento_e_resumo(self):
        self._criar_rpv(
            nome_beneficiario="Exporta REINF",
            valor_irrf=Decimal("350.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 16),
        )

        resposta = self.client.get("/reinf/exportar.csv?competencia=2026-03&reinf_status=todos")
        conteudo = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Exporta REINF", conteudo)
        self.assertIn("12345678", conteudo)
        self.assertIn("IRRF", conteudo)

    def test_reinf_exibe_beneficiario_com_documento_limpo_e_sem_cards_analiticos(self):
        registro = self._criar_rpv(
            nome_beneficiario="Beneficiario Limpo",
            valor_irrf=Decimal("420.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 16),
            documento_original="123.456.789-01",
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Filtros de pesquisa", html)
        self.assertIn("Beneficiario Limpo", html)
        self.assertIn("12345678901", html)
        self.assertIn("Conferencia mensal", html)
        self.assertIn("Conferencia anual", html)
        self.assertNotIn("Menu da area REINF", html)
        self.assertNotIn("Pagos com IRRF", html)
        self.assertNotIn("Pendentes de envio", html)
        self.assertNotIn(f"Processo {registro.processo.numero_processo}", html)

    def test_reinf_operacional_abre_em_ordem_alfabetica_por_beneficiario(self):
        self._criar_rpv(
            nome_beneficiario="Zeta REINF Padrao",
            documento_original="33333333333",
            valor_irrf=Decimal("330.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 5),
        )
        self._criar_rpv(
            nome_beneficiario="Alfa REINF Padrao",
            documento_original="11111111111",
            valor_irrf=Decimal("110.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 19),
        )
        self._criar_rpv(
            nome_beneficiario="Alfa REINF Padrao",
            documento_original="22222222222",
            valor_irrf=Decimal("220.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 7),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03&reinf_status=todos")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Ordenação:</strong> Beneficiário", html)
        self.assertIn("Direção:</strong> Crescente", html)
        primeira_alfa = html.index("11111111111")
        segunda_alfa = html.index("22222222222")
        zeta = html.index("Zeta REINF Padrao")
        self.assertLess(primeira_alfa, segunda_alfa)
        self.assertLess(segunda_alfa, zeta)

    def test_reinf_conferencia_mensal_exibe_tabela_limpa_por_beneficiario(self):
        self._criar_rpv(
            nome_beneficiario="Conferencia Mensal",
            documento_original="12345678901",
            valor_bruto=Decimal("3000.00"),
            valor_irrf=Decimal("300.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 10),
        )
        _, item_irrf = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-CONF-MENSAL",
            nome_beneficiario="Conferencia Mensal",
            cpf_original="12345678901",
            numero_processo="PROC-CONF-MENSAL-2",
            valor_bruto=Decimal("2200.00"),
            valor_irrf=Decimal("220.00"),
        )
        item_irrf.data_pagamento = date(2026, 3, 21)
        self._criar_rpv(
            nome_beneficiario="Conferencia Mensal CPF Menor",
            documento_original="02345678901",
            valor_bruto=Decimal("1000.00"),
            valor_irrf=Decimal("100.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 12),
        )

        self._criar_dativo_sem_irrf(
            processo_edoc="CI-CONF-MENSAL-LIVRE",
            itens=[
                {
                    "nome_beneficiario": "Nao Deve Entrar",
                    "cpf_original": "99988877766",
                    "numero_processo": "PROC-CONF-MENSAL-LIVRE",
                    "valor_bruto": Decimal("900.00"),
                    "data_pagamento": date(2026, 3, 25),
                }
            ],
        )
        db.session.commit()

        resposta = self.client.get("/reinf/?visao=conferencia_mensal&competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Voltar para REINF mensal", html)
        self.assertIn("Ir para anual", html)
        self.assertIn("Conferencia mensal por beneficiario", html)
        self.assertIn("Valor base", html)
        self.assertIn("IRRF retido", html)
        self.assertLess(
            html.index("023.456.789-01"),
            html.index("123.456.789-01"),
        )
        self.assertIn("Conferencia Mensal", html)
        self.assertIn("023.456.789-01", html)
        self.assertIn("123.456.789-01", html)
        self.assertIn("março/2026", html)
        self.assertIn("5.200,00", html)
        self.assertIn("520,00", html)
        self.assertIn("Nenhum processo conferido", html)
        self.assertIn("0/2", html)
        self.assertIn("Por conferir", html)
        self.assertIn("Marcar", html)
        self.assertIn("Ver processos", html)
        self.assertIn("Valor bruto", html)
        self.assertIn("Liquido", html)
        self.assertIn("data-reinf-progress-summary", html)
        self.assertIn("data-reinf-review-toggle", html)
        self.assertIn("este processo", html)
        self.assertIn("/reinf/marcar-conferencia-processo", html)
        self.assertIn("PROC-CONF-MENSAL-2", html)
        self.assertNotIn("Abrir", html)
        self.assertNotIn("Nao Deve Entrar", html)
        self.assertNotIn("Atualizar selecionados", html)
        self.assertNotIn("Status REINF", html)

    def test_reinf_conferencia_mensal_persiste_processo_conferido_no_historico(self):
        registro = self._criar_rpv(
            nome_beneficiario="Conferencia Persistida",
            documento_original="12345678901",
            valor_bruto=Decimal("3000.00"),
            valor_irrf=Decimal("300.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 10),
        )
        _, item_irrf = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-CONF-PERSISTIDA",
            nome_beneficiario="Conferencia Persistida",
            cpf_original="12345678901",
            numero_processo="PROC-CONF-PERSISTIDA-2",
            valor_bruto=Decimal("2200.00"),
            valor_irrf=Decimal("220.00"),
        )
        item_irrf.data_pagamento = date(2026, 3, 21)
        db.session.commit()

        resposta = self.client.post(
            "/reinf/marcar-conferencia-processo",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "revisado": "1",
                "retorno": "/reinf/?visao=conferencia_mensal&competencia=2026-03",
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "fetch",
            },
            follow_redirects=False,
        )
        payload = resposta.get_json()

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["created"])
        self.assertTrue(payload["reviewed"])
        self.assertIn("Conferido por", payload["reviewed_meta"])

        eventos = (
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="registro_rpv",
                entidade_id=registro.id,
                acao="Conferencia REINF mensal",
            )
            .order_by(HistoricoAlteracao.id.asc())
            .all()
        )
        self.assertEqual(len(eventos), 1)

        resposta_repetida = self.client.post(
            "/reinf/marcar-conferencia-processo",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "revisado": "1",
                "retorno": "/reinf/?visao=conferencia_mensal&competencia=2026-03",
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "fetch",
            },
            follow_redirects=False,
        )
        payload_repetido = resposta_repetida.get_json()

        self.assertEqual(resposta_repetida.status_code, 200)
        self.assertTrue(payload_repetido["ok"])
        self.assertFalse(payload_repetido["created"])
        self.assertTrue(payload_repetido["reviewed"])
        self.assertEqual(
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="registro_rpv",
                entidade_id=registro.id,
                acao="Conferencia REINF mensal",
            ).count(),
            1,
        )

        resposta_html = self.client.get("/reinf/?visao=conferencia_mensal&competencia=2026-03")
        html = resposta_html.get_data(as_text=True)

        self.assertEqual(resposta_html.status_code, 200)
        self.assertIn("1 de 2 processo(s) conferido(s)", html)
        self.assertIn("1/2", html)
        self.assertIn("processos conferidos", html)
        self.assertIn("Conferido por", html)

        resposta_desmarcar = self.client.post(
            "/reinf/marcar-conferencia-processo",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
                "revisado": "0",
                "retorno": "/reinf/?visao=conferencia_mensal&competencia=2026-03",
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "fetch",
            },
            follow_redirects=False,
        )
        payload_desmarcado = resposta_desmarcar.get_json()

        self.assertEqual(resposta_desmarcar.status_code, 200)
        self.assertTrue(payload_desmarcado["ok"])
        self.assertTrue(payload_desmarcado["created"])
        self.assertFalse(payload_desmarcado["reviewed"])
        self.assertEqual(
            HistoricoAlteracao.query.filter_by(
                entidade_tipo="registro_rpv",
                entidade_id=registro.id,
                acao="Conferencia REINF mensal",
            ).count(),
            2,
        )

        resposta_html_desmarcada = self.client.get("/reinf/?visao=conferencia_mensal&competencia=2026-03")
        html_desmarcado = resposta_html_desmarcada.get_data(as_text=True)

        self.assertEqual(resposta_html_desmarcada.status_code, 200)
        self.assertIn("Nenhum processo conferido", html_desmarcado)
        self.assertIn("0/2", html_desmarcado)
        self.assertIn("Por conferir", html_desmarcado)
        self.assertNotIn("Conferido por", html_desmarcado)

    def test_reinf_conferencia_anual_exibe_total_do_ano_e_detalhamento_por_exercicio(self):
        self._criar_rpv(
            nome_beneficiario="Conferencia Anual",
            documento_original="12345678901",
            valor_bruto=Decimal("3100.00"),
            valor_irrf=Decimal("310.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 2, 12),
        )
        _, item_irrf = self._criar_item_dativo_com_irrf(
            processo_edoc="CI-CONF-ANUAL",
            nome_beneficiario="Conferencia Anual",
            cpf_original="12345678901",
            numero_processo="PROC-CONF-ANUAL-2",
            valor_bruto=Decimal("2400.00"),
            valor_irrf=Decimal("240.00"),
        )
        item_irrf.data_pagamento = date(2026, 4, 5)

        self._criar_dativo_sem_irrf(
            processo_edoc="CI-CONF-ANUAL-LIVRE",
            itens=[
                {
                    "nome_beneficiario": "Fora da Anual",
                    "cpf_original": "99988877766",
                    "numero_processo": "PROC-CONF-ANUAL-LIVRE",
                    "valor_bruto": Decimal("1500.00"),
                    "data_pagamento": date(2026, 6, 8),
                }
            ],
        )
        db.session.commit()

        resposta = self.client.get("/reinf/?visao=conferencia_anual&ano=2026")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Conferencia anual por beneficiario", html)
        self.assertIn("Valor total do ano", html)
        self.assertIn("IRRF retido no ano", html)
        self.assertIn("Conferencia Anual", html)
        self.assertIn("123.456.789-01", html)
        self.assertIn("5.500,00", html)
        self.assertIn("550,00", html)
        self.assertIn("Voltar para REINF mensal", html)
        self.assertIn("Ir para mensal", html)
        self.assertIn("Ver exercicios", html)
        self.assertIn("fevereiro/2026", html)
        self.assertIn("abril/2026", html)
        self.assertIn("Valor bruto", html)
        self.assertIn("3.100,00", html)
        self.assertIn("310,00", html)
        self.assertIn("2.790,00", html)
        self.assertIn("2.400,00", html)
        self.assertIn("240,00", html)
        self.assertIn("2.160,00", html)
        self.assertNotIn("PROC-CONF-ANUAL-2", html)
        self.assertNotIn("CI-CONF-ANUAL", html)
        self.assertNotIn("Abrir", html)
        self.assertNotIn("Fora da Anual", html)
        self.assertNotIn("Atualizar selecionados", html)
        self.assertNotIn("Status REINF", html)

    def test_reinf_atualiza_status_em_lote(self):
        registro = self._criar_rpv(
            nome_beneficiario="Lote REINF",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )

        resposta = self.client.post(
            "/reinf/atualizar-status-lote",
            data={
                "reinf_status_lote": "Concluído",
                "selecionados": [f"rpv:{registro.id}"],
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status_legivel, "Concluído")

    def test_reinf_nao_sobrescreve_status_individual_sem_status_no_post(self):
        registro = self._criar_rpv(
            nome_beneficiario="REINF Sem Status Individual",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro.reinf_status = "Concluído"
        db.session.commit()

        resposta = self.client.post(
            "/reinf/atualizar-status",
            data={
                "origem": "rpv",
                "registro_id": str(registro.id),
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status, "Concluído")

    def test_reinf_nao_sobrescreve_status_lote_sem_status_no_post(self):
        registro = self._criar_rpv(
            nome_beneficiario="REINF Sem Status Lote",
            valor_irrf=Decimal("410.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        registro.reinf_status = "Cancelado"
        db.session.commit()

        resposta = self.client.post(
            "/reinf/atualizar-status-lote",
            data={
                "selecionados": [f"rpv:{registro.id}"],
            },
            follow_redirects=False,
        )

        self.assertEqual(resposta.status_code, 302)
        registro_atualizado = db.session.get(RegistroRPV, registro.id)
        self.assertEqual(registro_atualizado.reinf_status, "Cancelado")

    def test_reinf_aplica_ordenacao_e_paginacao(self):
        self._criar_rpv(
            nome_beneficiario="REINF Menor",
            valor_irrf=Decimal("100.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 18),
        )
        self._criar_rpv(
            nome_beneficiario="REINF Maior",
            valor_irrf=Decimal("450.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 19),
        )

        resposta = self.client.get(
            "/reinf/?competencia=2026-03&reinf_status=todos&ordenar=imposto&direcao=desc&por_pagina=1&pagina=1"
        )
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("REINF Maior", html)
        self.assertNotIn("REINF Menor", html)
        self.assertIn("Página 1 de 2", html)

    def test_reinf_nao_exibe_opcao_meus_no_filtro_de_responsavel(self):
        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn('option value="meus"', html)

    def test_reinf_exibe_colunas_fixas_na_grade(self):
        self._criar_rpv(
            nome_beneficiario="REINF Sticky",
            valor_irrf=Decimal("220.00"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 20),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("sticky-left-1", html)
        self.assertIn("sticky-right-1", html)

    def test_reinf_operacional_exibe_bloco_financeiro_com_bruto_e_irrf(self):
        self._criar_rpv(
            nome_beneficiario="REINF Financeiro",
            valor_bruto=Decimal("4321.98"),
            valor_irrf=Decimal("321.98"),
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 22),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03&reinf_status=todos&q=Financeiro")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("reinf-finance-cell", html)
        self.assertIn("Financeiro", html)
        self.assertIn("Bruto", html)
        self.assertIn("4.321,98", html)
        self.assertIn("IRRF", html)
        self.assertIn("321,98", html)

    def test_reinf_exclui_rpv_marcado_sem_irrf(self):
        self._criar_rpv(
            nome_beneficiario="Oculto Reinf",
            valor_irrf=None,
            sem_irrf=True,
            data_pagamento=date(2026, 3, 15),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("Oculto Reinf", html)

    def test_reinf_exibe_rpv_pendente_quando_irrf_ainda_nao_foi_informado(self):
        self._criar_rpv(
            nome_beneficiario="Pendente Reinf",
            valor_irrf=None,
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_pendente_id,
            data_pagamento=date(2026, 3, 15),
        )

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Pendente Reinf", html)
        self.assertIn("IRRF PENDENTE", html)

    def test_reinf_preserva_compatibilidade_com_registro_legado_sem_irrf(self):
        registro = self._criar_rpv(
            nome_beneficiario="Legado Sem IRRF",
            valor_irrf=None,
            sem_irrf=False,
            situacao_imposto_id=self.situacao_imposto_sem_irrf_id,
            data_pagamento=date(2026, 3, 15),
        )

        self.assertTrue(registro.sem_irrf_efetivo)

        resposta = self.client.get("/reinf/?competencia=2026-03")
        html = resposta.get_data(as_text=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("Legado Sem IRRF", html)

