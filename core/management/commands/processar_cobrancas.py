"""
Executa a régua de cobrança: atualiza vencidas, envia boletos (D-2) e lembretes (D+4),
gera tarefas (boleto faltando, sugestão de bloqueio).

Agende para rodar a cada hora (cron, systemd timer ou o comando `agendador`).
É seguro rodar várias vezes: nenhuma mensagem é enviada em duplicidade.
"""
import fcntl
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from core.services.cobrancas import processar_regua

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Processa a régua de cobrança automática (envio de boletos e lembretes)."

    def add_arguments(self, parser):
        parser.add_argument("--sem-envio", action="store_true", help="Só atualiza status e tarefas.")

    def handle(self, *args, **opts):
        trava = settings.MEDIA_ROOT.parent / ".regua.lock"
        trava.parent.mkdir(parents=True, exist_ok=True)
        with open(trava, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stdout.write("Outra execução em andamento; saindo.")
                return
            res = processar_regua(enviar=not opts["sem_envio"])
        self.stdout.write(self.style.SUCCESS(f"Régua processada: {res}"))
