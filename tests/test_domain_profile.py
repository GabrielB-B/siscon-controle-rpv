import json
import os
import tempfile
import unittest
from pathlib import Path

from app.utils.domain_profile import (
    clear_domain_profile_cache,
    get_domain_profile,
)


class DomainProfileTestCase(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("DOMAIN_PROFILE_FILE", None)
        clear_domain_profile_cache()

    def test_perfil_padrao_mantem_nomenclaturas_atuais(self):
        profile = get_domain_profile()

        self.assertEqual(profile.situacao_empenho_inicial_nome, "Sem Tratamento")
        self.assertEqual(profile.situacao_imposto_inicial_nome, "Sem Tratamento")
        self.assertEqual(profile.situacao_imposto_sem_irrf_nome, "Sem IRRF")
        self.assertEqual(profile.tipo_rpv_name("rpv_honorarios"), "RPV honorários")

    def test_perfil_json_pode_sobrescrever_nomes_sem_mudar_chaves(self):
        with tempfile.TemporaryDirectory() as tempdir:
            profile_path = Path(tempdir) / "domain_profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "situacoes_empenho": {
                            "sem_tratamento": "Aguardando triagem",
                        },
                        "situacoes_imposto": {
                            "sem_irrf": "Dispensado de IRRF",
                        },
                        "tipos_rpv": {
                            "rpv_honorarios": "Honorarios advocaticios",
                        },
                    }
                ),
                encoding="utf-8",
            )
            os.environ["DOMAIN_PROFILE_FILE"] = str(profile_path)
            clear_domain_profile_cache()

            profile = get_domain_profile()

            self.assertEqual(profile.situacao_empenho_inicial_nome, "Aguardando triagem")
            self.assertEqual(profile.situacao_imposto_sem_irrf_nome, "Dispensado de IRRF")
            self.assertEqual(profile.tipo_rpv_name("rpv_honorarios"), "Honorarios advocaticios")


if __name__ == "__main__":
    unittest.main()
