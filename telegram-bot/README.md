# Telegram Bot Deployment Notes

## OAuth Redirect Configuration

Set the `GOOGLE_REDIRECT_URI` environment variable to point at the bot's OAuth
callback endpoint. The new built-in web server exposes the callback at
`/oauth/callback`, so the value should look like:

```
https://<your-domain-or-ip>/oauth/callback
```

Make sure the same URL is also registered in the Google Cloud Console for your
OAuth client. Without this value the authorization flow will redirect to a 404
page and the bot will never receive the authorization code.

## Local Web Server

The bot now serves the OAuth callback itself using `aiohttp`. You can customize
its bind address with the optional environment variables `BOT_WEB_HOST` and
`BOT_WEB_PORT` (defaults are `0.0.0.0` and `8080`).
