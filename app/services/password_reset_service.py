import hashlib
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, or_

from app.extensions import db
from app.models import PasswordResetToken, User
from app.utils.account_security import normalizar_email
from app.utils.datetime_utils import utc_now_naive
from app.utils.normalizers import normalizar_telefone


class PasswordResetService:
    CODE_LENGTH = 6

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()

    @classmethod
    def _normalize_code(cls, raw_code: str | None) -> str:
        return "".join(ch for ch in str(raw_code or "") if ch.isdigit())

    @classmethod
    def _hash_code(cls, challenge_token: str, raw_code: str | None) -> str:
        normalized_code = cls._normalize_code(raw_code)
        return cls._hash_token(f"{str(challenge_token or '').strip()}:{normalized_code}")

    @staticmethod
    def _mask_email(email: str | None) -> str:
        email = normalizar_email(email)
        if "@" not in email:
            return "Email cadastrado"

        local, domain = email.split("@", 1)
        local_masked = f"{local[:2]}***" if local else "***"
        return f"{local_masked}@{domain}"

    @staticmethod
    def _mask_phone(phone: str | None) -> str:
        digits = normalizar_telefone(phone)
        if len(digits) < 4:
            return "Telefone cadastrado"
        return f"(**) *****-{digits[-4:]}"

    @classmethod
    def perform_dummy_work(cls):
        cls._hash_token(secrets.token_urlsafe(16))

    @staticmethod
    def token_ttl_minutes() -> int:
        try:
            ttl_minutes = int(
                current_app.config.get(
                    "PASSWORD_RESET_CODE_TTL_MINUTES",
                    current_app.config.get("PASSWORD_RESET_TOKEN_TTL_MINUTES", 10),
                )
                or 10
            )
        except (TypeError, ValueError):
            ttl_minutes = 10
        return max(ttl_minutes, 5)

    @staticmethod
    def code_max_attempts() -> int:
        try:
            max_attempts = int(current_app.config.get("PASSWORD_RESET_CODE_MAX_ATTEMPTS", 5) or 5)
        except (TypeError, ValueError):
            max_attempts = 5
        return max(max_attempts, 3)

    @classmethod
    def _ttl_delta(cls, ttl_minutes: int | None = None) -> timedelta:
        minutos = int(ttl_minutes or 0)
        if minutos <= 0:
            minutos = cls.token_ttl_minutes()
        return timedelta(minutes=minutos)

    @classmethod
    def buscar_usuario_por_identificador(cls, identificador: str | None) -> User | None:
        texto = str(identificador or "").strip()
        if not texto:
            return None

        email = normalizar_email(texto)
        telefone = normalizar_telefone(texto)
        login = texto.lower()

        filtros = [func.lower(User.login) == login]
        if email:
            filtros.append(func.lower(User.email) == email)
        if telefone:
            filtros.append(User.telefone == telefone)

        return User.query.filter(User.ativo.is_(True)).filter(or_(*filtros)).first()

    @classmethod
    def buscar_usuario_por_login(cls, login: str | None) -> User | None:
        texto = str(login or "").strip().lower()
        if not texto:
            return None
        return User.query.filter(func.lower(User.login) == texto, User.ativo.is_(True)).first()

    @classmethod
    def canais_recuperacao_disponiveis(cls, usuario: User) -> list[dict]:
        canais = []

        email = normalizar_email(usuario.email)
        if email:
            canais.append(
                {
                    "channel": "email",
                    "title": "Email cadastrado",
                    "masked_destination": cls._mask_email(email),
                }
            )

        telefone = normalizar_telefone(usuario.telefone)
        if telefone:
            canais.append(
                {
                    "channel": "sms",
                    "title": "SMS cadastrado",
                    "masked_destination": cls._mask_phone(telefone),
                }
            )

        return canais

    @classmethod
    def resolver_canal_recuperacao(
        cls,
        usuario: User,
        preferred_channel: str | None = None,
    ) -> tuple[str, str, str] | None:
        canal_preferido = str(preferred_channel or "").strip().lower()

        if canal_preferido == "email":
            email = normalizar_email(usuario.email)
            if email:
                return "email", email, cls._mask_email(email)
            return None

        if canal_preferido == "sms":
            telefone = normalizar_telefone(usuario.telefone)
            if telefone:
                return "sms", telefone, cls._mask_phone(telefone)
            return None

        for canal in cls.canais_recuperacao_disponiveis(usuario):
            channel = canal["channel"]
            if channel == "email":
                return channel, normalizar_email(usuario.email), canal["masked_destination"]
            if channel == "sms":
                return channel, normalizar_telefone(usuario.telefone), canal["masked_destination"]

        return None

    @classmethod
    def invalidar_tokens_ativos(cls, usuario_id: int):
        agora = utc_now_naive()
        tokens = PasswordResetToken.query.filter_by(
            user_id=usuario_id,
            utilizado_em=None,
        ).all()
        for token in tokens:
            token.utilizado_em = agora

    @classmethod
    def criar_solicitacao(
        cls,
        *,
        usuario: User,
        request_ip: str | None,
        ttl_minutes: int | None = None,
        preferred_channel: str | None = None,
    ) -> tuple[PasswordResetToken, str, str, str]:
        canal_info = cls.resolver_canal_recuperacao(
            usuario,
            preferred_channel=preferred_channel,
        )
        if not canal_info:
            raise ValueError("Usuario sem contato valido para o canal de recuperacao solicitado.")

        canal, destino, destino_mascarado = canal_info
        challenge_token = secrets.token_urlsafe(16)
        raw_code = f"{secrets.randbelow(10 ** cls.CODE_LENGTH):0{cls.CODE_LENGTH}d}"
        cls.invalidar_tokens_ativos(usuario.id)

        solicitacao = PasswordResetToken(
            user_id=usuario.id,
            token_hash=cls._hash_code(challenge_token, raw_code),
            canal=canal,
            destino_mascarado=destino_mascarado,
            solicitado_ip=request_ip,
            expira_em=utc_now_naive() + cls._ttl_delta(ttl_minutes or cls.token_ttl_minutes()),
            verificado_em=None,
            tentativas_codigo=0,
        )
        db.session.add(solicitacao)
        return solicitacao, raw_code, destino, challenge_token

    @classmethod
    def obter_solicitacao_valida_por_id(cls, solicitacao_id: int | None) -> PasswordResetToken | None:
        try:
            solicitacao_id_int = int(solicitacao_id or 0)
        except (TypeError, ValueError):
            return None

        solicitacao = PasswordResetToken.query.filter_by(
            id=solicitacao_id_int,
            utilizado_em=None,
        ).first()
        if not solicitacao or solicitacao.expirado:
            return None
        return solicitacao

    @classmethod
    def segundos_restantes(cls, solicitacao: PasswordResetToken | None) -> int:
        if not solicitacao:
            return cls.token_ttl_minutes() * 60
        delta = solicitacao.expira_em - utc_now_naive()
        return max(int(delta.total_seconds()), 0)

    @classmethod
    def _registrar_codigo_invalido(
        cls,
        solicitacao: PasswordResetToken,
    ) -> tuple[None, str, str]:
        solicitacao.tentativas_codigo = int(solicitacao.tentativas_codigo or 0) + 1
        restantes = max(cls.code_max_attempts() - solicitacao.tentativas_codigo, 0)

        if restantes <= 0:
            solicitacao.utilizado_em = utc_now_naive()
            return (
                None,
                "O codigo excedeu o limite de tentativas. Solicite um novo para continuar.",
                "restart",
            )

        return (
            None,
            f"Codigo invalido. Restam {restantes} tentativa(s) antes de bloquear a solicitacao.",
            "retry",
        )

    @classmethod
    def verificar_codigo(
        cls,
        *,
        solicitacao_id: int | None,
        challenge_token: str | None,
        raw_code: str | None,
    ) -> tuple[PasswordResetToken | None, str | None, str]:
        solicitacao = cls.obter_solicitacao_valida_por_id(solicitacao_id)
        if not solicitacao:
            return None, "O codigo expirou ou nao esta mais disponivel. Solicite um novo.", "restart"

        if solicitacao.codigo_verificado:
            return solicitacao, None, "verified"

        if not challenge_token or len(cls._normalize_code(raw_code)) != cls.CODE_LENGTH:
            return cls._registrar_codigo_invalido(solicitacao)

        code_hash = cls._hash_code(challenge_token, raw_code)
        if not secrets.compare_digest(solicitacao.token_hash, code_hash):
            return cls._registrar_codigo_invalido(solicitacao)

        solicitacao.verificado_em = utc_now_naive()
        return solicitacao, None, "verified"

    @classmethod
    def redefinir_senha(cls, *, solicitacao_id: int | None, nova_senha: str, password_validator):
        solicitacao = cls.obter_solicitacao_valida_por_id(solicitacao_id)
        if not solicitacao or not solicitacao.codigo_verificado:
            raise ValueError("A confirmacao do codigo e invalida ou expirou. Solicite um novo codigo.")

        usuario = solicitacao.usuario
        password_validator(nova_senha, usuario.login)
        usuario.set_password(nova_senha)

        agora = utc_now_naive()
        cls.invalidar_tokens_ativos(usuario.id)
        solicitacao.utilizado_em = agora
        return usuario, solicitacao
