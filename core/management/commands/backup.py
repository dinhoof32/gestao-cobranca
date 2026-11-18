"""
Backup completo: banco de dados + arquivos (boletos, comprovantes) em um .tar.gz com data/hora.
Mantém os últimos N dias (BACKUP_RETENCAO_DIAS). Copie a pasta de backups para fora do servidor
(ex.: rclone para Google Drive/S3) — veja o README.
"""
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from core.services.auditoria import registrar


class Command(BaseCommand):
    help = "Gera backup do banco e dos arquivos."

    def handle(self, *args, **opts):
        destino = Path(settings.BACKUP_DIR)
        destino.mkdir(parents=True, exist_ok=True)
        carimbo = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        arquivo = destino / f"backup-{carimbo}.tar.gz"
        db = settings.DATABASES["default"]

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            if db["ENGINE"].endswith("sqlite3"):
                dump = tmp / "db.sqlite3"
                origem = sqlite3.connect(db["NAME"])
                copia = sqlite3.connect(dump)
                with copia:
                    origem.backup(copia)  # cópia consistente mesmo com o sistema em uso
                origem.close()
                copia.close()
            elif "postgresql" in db["ENGINE"]:
                dump = tmp / "db.sql"
                env = {"PGPASSWORD": db.get("PASSWORD") or "", "PATH": "/usr/bin:/bin:/usr/local/bin",
                       "PGSSLMODE": (db.get("OPTIONS") or {}).get("sslmode", "prefer")}
                cmd = ["pg_dump", "--no-owner", "--no-privileges", "-h", db.get("HOST") or "localhost",
                       "-p", str(db.get("PORT") or 5432), "-U", db.get("USER") or "", "-f", str(dump), db["NAME"]]
                try:
                    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
                    ok = r.returncode == 0
                    erro = r.stderr
                except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                    ok, erro = False, str(exc)
                if not ok:
                    # pg_dump ausente ou de versão diferente do servidor: exporta pelo próprio Django
                    self.stderr.write(f"pg_dump indisponível ({erro.strip()[:200]}); usando exportação JSON.")
                    dump = tmp / "db.json"
                    with open(dump, "w", encoding="utf-8") as f:
                        call_command("dumpdata", "--natural-foreign", "--natural-primary",
                                     "--exclude=contenttypes", "--exclude=auth.permission",
                                     "--exclude=sessions", stdout=f)
            else:
                raise CommandError("Banco não suportado para backup automático.")

            with tarfile.open(arquivo, "w:gz") as tar:
                tar.add(dump, arcname=dump.name)
                if Path(settings.MEDIA_ROOT).exists():
                    tar.add(settings.MEDIA_ROOT, arcname="arquivos")

        limite = time.time() - settings.BACKUP_RETENCAO_DIAS * 86400
        removidos = 0
        for antigo in destino.glob("backup-*.tar.gz"):
            if antigo.stat().st_mtime < limite:
                antigo.unlink()
                removidos += 1
        registrar("backup.gerado", arquivo.name, {"tamanho": arquivo.stat().st_size, "removidos": removidos})
        self.stdout.write(self.style.SUCCESS(f"Backup gerado: {arquivo} ({arquivo.stat().st_size // 1024} KB)"))
