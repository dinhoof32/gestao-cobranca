from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware


class CsrfPreviewMiddleware(CsrfViewMiddleware):
    """Usado apenas no MODO_PREVIEW (DEBUG): o proxy de pré-visualização muda o endereço de origem.
    O token CSRF continua sendo exigido; só a conferência de origem/referer é relaxada."""

    def _origin_verified(self, request):
        return settings.MODO_PREVIEW or super()._origin_verified(request)

    def _check_referer(self, request):
        if settings.MODO_PREVIEW:
            return
        return super()._check_referer(request)
