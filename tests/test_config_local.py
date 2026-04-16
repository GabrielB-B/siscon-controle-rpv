import unittest

from app.config import BASE_DIR, _default_sqlite_uri, _engine_options, _normalize_database_url


class ConfigLocalTestCase(unittest.TestCase):
    def test_normaliza_url_mysql_legada_para_pymysql(self):
        self.assertEqual(
            _normalize_database_url("mysql://usuario:senha@localhost/controle_rpv"),
            "mysql+pymysql://usuario:senha@localhost/controle_rpv",
        )

    def test_mantem_url_quando_ja_esta_normalizada(self):
        url = "mysql+pymysql://usuario:senha@localhost/controle_rpv?charset=utf8mb4"
        self.assertEqual(_normalize_database_url(url), url)

    def test_usa_sqlite_na_pasta_instance_quando_nao_ha_env(self):
        uri = _default_sqlite_uri()
        self.assertTrue(uri.startswith("sqlite:///"))
        self.assertIn((BASE_DIR / "instance" / "controle_rpv.db").as_posix(), uri)

    def test_opcoes_de_engine_ativam_pre_ping_no_mysql(self):
        opcoes = _engine_options("mysql+pymysql://usuario:senha@localhost/controle_rpv")
        self.assertTrue(opcoes["pool_pre_ping"])
        self.assertEqual(opcoes["pool_recycle"], 280)

    def test_opcoes_de_engine_ficam_vazias_no_sqlite(self):
        self.assertEqual(_engine_options("sqlite:///instance/controle_rpv.db"), {})


if __name__ == "__main__":
    unittest.main()
