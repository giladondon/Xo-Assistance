import os

from telegram_bot import build_application


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    webhook_path = os.environ.get("TELEGRAM_WEBHOOK_PATH", "telegram-webhook").strip()

    if not external_url:
        raise RuntimeError("RENDER_EXTERNAL_URL environment variable is required")

    webhook_path = webhook_path.lstrip("/") or "telegram-webhook"
    webhook_url = f"{external_url.rstrip('/')}/{webhook_path}"

    application = build_application()

    print(
        f"🌐 Starting webhook listener on port {port} "
        f"with external URL {webhook_url}"
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
    )


if __name__ == "__main__":
    main()
