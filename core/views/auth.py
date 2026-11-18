import base64
import io
from datetime import timedelta

import pyotp
import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.forms import CodigoForm, LoginForm
from core.models import PerfilUsuario, TentativaLogin
from core.services.auditoria import ip_de, registrar


def _bloqueado(usuario, ip):
    desde = timezone.now() - timedelta(minutes=settings.LOGIN_BLOQUEIO_MINUTOS)
    falhas_usuario = TentativaLogin.objects.filter(usuario=usuario, sucesso=False, criado_em__gte=desde).count()
    falhas_ip = TentativaLogin.objects.filter(ip=ip, sucesso=False, criado_em__gte=desde).count() if ip else 0
    return falhas_usuario >= settings.LOGIN_MAX_TENTATIVAS or falhas_ip >= settings.LOGIN_MAX_TENTATIVAS * 3


def entrar(request):
    if request.user.is_authenticated:
        return redirect("painel")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.cleaned_data["usuario"].strip()
        ip = ip_de(request)
        if _bloqueado(usuario, ip):
            messages.error(request, f"Muitas tentativas. Aguarde {settings.LOGIN_BLOQUEIO_MINUTOS} minutos.")
            registrar("login.bloqueado", usuario, request=request)
        else:
            user = authenticate(request, username=usuario, password=form.cleaned_data["senha"])
            TentativaLogin.objects.create(usuario=usuario, ip=ip, sucesso=bool(user))
            if user is None:
                messages.error(request, "Usuário ou senha inválidos.")
                registrar("login.falha", usuario, request=request)
            elif PerfilUsuario.de(user).totp_ativo:
                request.session["2fa_user"] = user.pk
                request.session["2fa_next"] = request.GET.get("next", "")
                return redirect("login_2fa")
            else:
                return _concluir_login(request, user, request.GET.get("next"))
    return render(request, "registration/login.html", {"form": form})


def _concluir_login(request, user, proximo=None):
    login(request, user)
    registrar("login.sucesso", user.username, request=request, usuario=user)
    if proximo and proximo.startswith("/") and not proximo.startswith("//"):
        return redirect(proximo)
    return redirect("painel")


def login_2fa(request):
    uid = request.session.get("2fa_user")
    if not uid:
        return redirect("login")
    user = User.objects.get(pk=uid)
    form = CodigoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ip = ip_de(request)
        if _bloqueado(user.username, ip):
            messages.error(request, "Muitas tentativas. Aguarde alguns minutos.")
        elif pyotp.TOTP(PerfilUsuario.de(user).totp_secret).verify(form.cleaned_data["codigo"], valid_window=1):
            proximo = request.session.pop("2fa_next", "")
            request.session.pop("2fa_user", None)
            return _concluir_login(request, user, proximo)
        else:
            TentativaLogin.objects.create(usuario=user.username, ip=ip, sucesso=False)
            messages.error(request, "Código inválido.")
    return render(request, "registration/login_2fa.html", {"form": form})


@require_POST
def sair(request):
    if request.user.is_authenticated:
        registrar("logout", request.user.username, request=request)
    logout(request)
    return redirect("login")


@login_required
def seguranca(request):
    perfil = PerfilUsuario.de(request.user)
    senha_form = PasswordChangeForm(request.user, request.POST if request.POST.get("acao") == "senha" else None)
    for f in senha_form.fields.values():
        f.widget.attrs["class"] = "input"
    codigo_form = CodigoForm(request.POST if request.POST.get("acao") in ("ativar2fa", "desativar2fa") else None)
    qr = None

    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "senha" and senha_form.is_valid():
            user = senha_form.save()
            update_session_auth_hash(request, user)
            registrar("usuario.trocar_senha", user.username, request=request)
            messages.success(request, "Senha alterada.")
            return redirect("seguranca")
        if acao == "ativar2fa" and codigo_form.is_valid():
            if pyotp.TOTP(perfil.totp_secret).verify(codigo_form.cleaned_data["codigo"], valid_window=1):
                perfil.totp_ativo = True
                perfil.save()
                registrar("usuario.2fa_ativado", request.user.username, request=request)
                messages.success(request, "Verificação em duas etapas ativada.")
                return redirect("seguranca")
            messages.error(request, "Código inválido. Tente novamente.")
        if acao == "desativar2fa" and codigo_form.is_valid():
            if pyotp.TOTP(perfil.totp_secret).verify(codigo_form.cleaned_data["codigo"], valid_window=1):
                perfil.totp_ativo = False
                perfil.totp_secret = ""
                perfil.save()
                registrar("usuario.2fa_desativado", request.user.username, request=request)
                messages.success(request, "Verificação em duas etapas desativada.")
                return redirect("seguranca")
            messages.error(request, "Código inválido.")

    if not perfil.totp_ativo:
        if not perfil.totp_secret:
            perfil.totp_secret = pyotp.random_base32()
            perfil.save()
        uri = pyotp.TOTP(perfil.totp_secret).provisioning_uri(name=request.user.username,
                                                              issuer_name="Gestao Suporte")
        img = qrcode.make(uri, box_size=6, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr = base64.b64encode(buf.getvalue()).decode()

    return render(request, "core/seguranca.html", {
        "perfil": perfil, "senha_form": senha_form, "codigo_form": codigo_form, "qr": qr,
        "segredo": perfil.totp_secret if not perfil.totp_ativo else None,
    })
