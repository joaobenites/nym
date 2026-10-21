"""Uso: python -m app"""
import uvicorn

from app.config import Settings
from app.main import create_app


def main() -> None:
    settings = Settings.load()
    # Escuta só em 127.0.0.1 por padrão. Com NYM_LAN=true escuta na rede local, e outros aparelhos só entram pareados.
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        from app.core.access import lan_addresses
        urls = [f"http://{ip}:{settings.port}" for ip in lan_addresses()] or ["(não achei o IP desta máquina)"]
        print("Nym na rede local. No celular (mesmo Wi-Fi), abra: " + "  ".join(urls))
        print("Para parear: no computador, abra http://127.0.0.1:%s e clique em 'Celular'." % settings.port)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
