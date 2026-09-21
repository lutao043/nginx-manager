"""备份与回滚回归。

其中 test_failed_save_keeps_backup_and_rolls_back 是本工具最坏失败模式的门禁用例
（ROADMAP G1 明确要求）：保存一份校验失败的配置后，备份必须仍然存在，且能回滚回原内容。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import INVALID_DIRECTIVE, VALID_CONF, ServerTestCase, requires_posix  # noqa: E402


class BackupTest(ServerTestCase):
    def setUp(self):
        self.fixture.write_conf("nginx.conf", VALID_CONF)

    def test_backups_endpoint_shape(self):
        st, body = self.fixture.get("/api/backups")
        self.assertEqual(st, 200)
        self.assertIn("backups", body)
        self.assertIsInstance(body["backups"], list)
        self.assertIn("retention", body)

    @requires_posix
    def test_failed_save_keeps_backup_and_rolls_back(self):
        """保存 → 校验失败（409 saved:true）→ 备份仍在且内容为原配置 → 一键回滚恢复原内容。"""
        original = self.fixture.read_conf()
        broken = VALID_CONF.replace("worker_processes  1;",
                                    "worker_processes  1;\n%s;" % INVALID_DIRECTIVE)

        # 1) 保存并备份：校验失败，但内容已落盘
        st, body = self.fixture.put("/api/config/file",
                                    {"path": "nginx.conf", "content": broken, "doBackup": True})
        self.assertEqual(st, 409)
        self.assertTrue(body.get("saved"))
        backup_id = body.get("backupId")
        self.assertTrue(backup_id, "409 响应必须带 backupId，前端才能给出回滚入口")
        self.assertTrue(body.get("backedUp"))
        self.assertEqual(self.fixture.read_conf(), broken)

        # 2) 备份里必须是「保存前」的原内容，且备份目录真实存在
        self.assertIn(backup_id, self.fixture.backup_ids())
        backup_file = os.path.join(self.fixture.backup_dir(backup_id), "nginx.conf")
        self.assertTrue(os.path.isfile(backup_file))
        with open(backup_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original)

        # 3) 备份接口能列出这份备份
        st, listed = self.fixture.get("/api/backups")
        self.assertEqual(st, 200)
        ids = [b["id"] for b in listed["backups"]]
        self.assertIn(backup_id, ids)

        # 4) 一键回滚：磁盘内容回到原配置，且回滚后校验通过
        st, restored = self.fixture.post("/api/backups/restore", {"id": backup_id})
        self.assertEqual(st, 200)
        self.assertTrue(restored.get("ok"))
        self.assertIn("nginx.conf", restored.get("restored", []))
        self.assertTrue(restored["test"]["ok"])
        self.assertEqual(self.fixture.read_conf(), original)

    @requires_posix
    def test_restore_of_invalid_backup_keeps_current_content(self):
        """备份本身非法时：回滚被拒绝（409），且当前文件内容原样保留。"""
        # 先制造一份非法配置并保存（带备份，备份里是合法内容 → 见上一个用例）
        broken = VALID_CONF + "\n%s;\n" % INVALID_DIRECTIVE
        self.fixture.write_conf("nginx.conf", broken)
        st, body = self.fixture.put("/api/config/file",
                                    {"path": "nginx.conf", "content": broken, "doBackup": True})
        self.assertEqual(st, 409)
        backup_id = body["backupId"]          # 备份内容 = broken（保存前就是坏的）

        before = self.fixture.read_conf()
        st, resp = self.fixture.post("/api/backups/restore", {"id": backup_id})
        self.assertEqual(st, 409)
        self.assertIn("校验失败", resp.get("error", ""))
        self.assertEqual(self.fixture.read_conf(), before)   # 未留下半成品

    def test_restore_rejects_illegal_id(self):
        st, _ = self.fixture.post("/api/backups/restore", {"id": "../../etc"})
        self.assertEqual(st, 400)

    def test_restore_unknown_backup_404(self):
        st, _ = self.fixture.post("/api/backups/restore", {"id": "19700101_000000"})
        self.assertEqual(st, 404)

    def test_delete_backup(self):
        self.fixture.put("/api/config/file",
                         {"path": "nginx.conf", "content": VALID_CONF, "doBackup": True})
        ids = self.fixture.backup_ids()
        self.assertTrue(ids)
        st, body = self.fixture.delete("/api/backups", {"id": ids[-1]})
        self.assertEqual(st, 200)
        self.assertNotIn(ids[-1], self.fixture.backup_ids())
        self.assertIn("backups", body)

    def test_delete_requires_csrf(self):
        st, _ = self.fixture.delete("/api/backups", {"id": "19700101_000000"}, csrf=False)
        self.assertEqual(st, 403)


if __name__ == "__main__":
    unittest.main()
