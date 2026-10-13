"""Run the app: python -m app"""

import uvicorn

from app.config import get_config
from app.main import create_app


def main() -> None:
    config = get_config()
    uvicorn.run(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
