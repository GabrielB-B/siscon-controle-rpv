import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.services.operational_health_service import _sqlite_root_shadow_info


class OperationalHealthServiceTestCase(unittest.TestCase):
    def _build_app(self, project_root: Path, instance_path: Path) -> Flask:
        app_root = project_root / "app"
        app_root.mkdir(parents=True, exist_ok=True)
        instance_path.mkdir(parents=True, exist_ok=True)
        return Flask(
            __name__,
            root_path=str(app_root),
            instance_path=str(instance_path),
        )

    def test_shadow_database_info_identifies_root_database_outside_active_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            instance_path = project_root / "instance"
            active_db = instance_path / "controle_rpv.db"
            root_db = project_root / "controle_rpv.db"
            active_db.parent.mkdir(parents=True, exist_ok=True)
            active_db.write_bytes(b"active-db")
            root_db.write_bytes(b"shadow-db")

            app = self._build_app(project_root=project_root, instance_path=instance_path)
            with app.app_context():
                info = _sqlite_root_shadow_info(active_db)

            self.assertIsNotNone(info)
            self.assertTrue(info["root_database_exists"])
            self.assertTrue(info["shadow_database_present"])
            self.assertFalse(info["root_database_is_active"])
            self.assertEqual(info["root_database_path"], str(root_db.resolve()))
            self.assertEqual(info["active_database_path"], str(active_db.resolve()))

    def test_shadow_database_info_marks_root_database_as_active_when_paths_match(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            root_db = project_root / "controle_rpv.db"
            root_db.write_bytes(b"same-db")

            app = self._build_app(project_root=project_root, instance_path=project_root / "instance")
            with app.app_context():
                info = _sqlite_root_shadow_info(root_db)

            self.assertIsNotNone(info)
            self.assertTrue(info["root_database_exists"])
            self.assertFalse(info["shadow_database_present"])
            self.assertTrue(info["root_database_is_active"])

    def test_shadow_database_info_returns_none_without_active_database(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            app = self._build_app(project_root=project_root, instance_path=project_root / "instance")
            with app.app_context():
                info = _sqlite_root_shadow_info(None)

            self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
