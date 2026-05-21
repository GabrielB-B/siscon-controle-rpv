import importlib
import os
import unittest

import app.config as config_module


class ConfigLocalTestCase(unittest.TestCase):
    def test_normaliza_url_mysql_legada_para_pymysql(self):
        self.assertEqual(
            config_module._normalize_database_url("mysql://usuario:senha@localhost/controle_rpv"),
            "mysql+pymysql://usuario:senha@localhost/controle_rpv",
        )

    def test_mantem_url_quando_ja_esta_normalizada(self):
        url = "mysql+pymysql://usuario:senha@localhost/controle_rpv?charset=utf8mb4"
        self.assertEqual(config_module._normalize_database_url(url), url)

    def test_normaliza_sqlite_relativo_do_ambiente_para_caminho_absoluto(self):
        original_database_url = os.environ.get("DATABASE_URL")
        try:
            os.environ["DATABASE_URL"] = "sqlite:///instance/controle_rpv.db"
            recarregado = importlib.reload(config_module)
            esperado = f"sqlite:///{(recarregado.BASE_DIR / 'instance' / 'controle_rpv.db').resolve().as_posix()}"

            self.assertEqual(recarregado.Config.SQLALCHEMY_DATABASE_URI, esperado)
        finally:
            if original_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original_database_url
            importlib.reload(config_module)

    def test_usa_sqlite_na_pasta_instance_quando_nao_ha_env(self):
        uri = config_module._default_sqlite_uri()
        self.assertTrue(uri.startswith("sqlite:///"))
        self.assertIn((config_module.BASE_DIR / "instance" / "controle_rpv.db").as_posix(), uri)

    def test_opcoes_de_engine_ativam_pre_ping_no_mysql(self):
        opcoes = config_module._engine_options("mysql+pymysql://usuario:senha@localhost/controle_rpv")
        self.assertTrue(opcoes["pool_pre_ping"])
        self.assertEqual(opcoes["pool_recycle"], 280)

    def test_opcoes_de_engine_ficam_vazias_no_sqlite(self):
        self.assertEqual(config_module._engine_options("sqlite:///instance/controle_rpv.db"), {})

    def test_cookie_name_padrao_da_dev_e_isolado(self):
        self.assertEqual(
            config_module._session_cookie_name_for_base_dir("controle_rpv"),
            "siscon_dev_session",
        )
        esperado_atual = config_module._session_cookie_name_for_base_dir(config_module.BASE_DIR)
        self.assertEqual(config_module._default_session_cookie_name(), esperado_atual)
        self.assertEqual(config_module.Config.REMEMBER_COOKIE_NAME, f"{esperado_atual}_remember")

    def test_cookie_name_runtime_e_isolado(self):
        self.assertEqual(
            config_module._session_cookie_name_for_base_dir("controle_rpv_runtime"),
            "siscon_runtime_session",
        )


if __name__ == "__main__":
    unittest.main()
