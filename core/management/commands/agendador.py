"""
Agendador simples embutido (alternativa ao cron, útil em Docker):
- régua de cobrança a cada 30 minutos;
- backup diário às 02h.
Uso: python manage.py agendador
"""
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Executa a régua periodicamente e o backup diário."

    def add_arguments(self, parser):
        parser.add_argument("--intervalo", type=int, default=30, help="Minutos entre execuções da régua.")

    def handle(self, *args, **opts):
        ultimo_backup = None
        self.stdout.write("Agendador iniciado.")
        while True:
            try:
                call_command("processar_cobrancas")
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"Erro na régua: {exc}")
            agora = timezone.localtime()
            if agora.hour == 2 and ultimo_backup != agora.date():
                try:
                    call_command("backup")
                    ultimo_backup = agora.date()
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(f"Erro no backup: {exc}")
            time.sleep(opts["intervalo"] * 60)
