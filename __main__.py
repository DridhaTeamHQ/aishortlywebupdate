"""Entry point for the autonomous news agent."""

import asyncio
import os

from dotenv import load_dotenv

from core.orchestrator import Orchestrator
from utils.logger import get_logger


load_dotenv()


def main() -> None:
    logger = get_logger("main")

    ai_provider = (os.getenv("AI_PROVIDER", "openai") or "openai").strip().lower()
    model_key_var = "GEMINI_API_KEY" if ai_provider == "gemini" else "OPENAI_API_KEY"
    required_vars = [model_key_var, "CMS_URL", "CMS_EMAIL", "CMS_PASSWORD"]
    missing = [name for name in required_vars if not os.getenv(name)]

    if missing:
        logger.critical(f"Missing environment variables: {', '.join(missing)}")
        return

    try:
        orchestrator = Orchestrator()
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Manually stopped")
    except Exception as exc:
        logger.critical(f"Unhandled exception: {exc}", exc_info=True)


if __name__ == "__main__":
    main()
