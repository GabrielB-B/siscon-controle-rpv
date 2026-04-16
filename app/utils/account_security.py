def normalizar_email(email: str | None) -> str:
    return str(email or "").strip().lower()


def validar_senha(senha: str, login: str):
    senha = str(senha or "")

    if len(senha) < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres.")

    if senha.strip().lower() == str(login or "").strip().lower():
        raise ValueError("A senha nao pode ser igual ao login.")

    if not any(char.isalpha() for char in senha) or not any(char.isdigit() for char in senha):
        raise ValueError("A senha precisa ter letras e numeros.")
