# Google sign-in integration

Odori uses `django-allauth` as its OAuth client. Google sign-in creates or signs in the Odori user; after the first sign-in, users create a household or join one by invitation link or registration code.

## 1. Create Google OAuth credentials

1. Open Google Cloud Console and select or create a project.
2. Configure the OAuth consent screen under **Google Auth Platform > Branding**.
3. Add your test accounts while the app is in testing mode.
4. Create an **OAuth client ID** of type **Web application**.
5. Add these authorized redirect URIs:

   - Development: `http://localhost:8000/accounts/google/login/callback/`
   - Production: `https://YOUR_ODORI_HOST/accounts/google/login/callback/`

The scheme, hostname, port, and trailing slash must match exactly.

## 2. Configure Odori

Set these environment variables in `.env`, Docker Compose, or your deployment secret store:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
```

For production, also ensure the existing settings are correct:

```dotenv
ALLOWED_HOSTS=odori.example.com
CSRF_TRUSTED_ORIGINS=https://odori.example.com
```

Restart Odori after changing the environment. Do not commit the client secret.

## 3. Apply and verify

```powershell
python manage.py migrate
python manage.py runserver
```

Open `http://localhost:8000/`, choose **Mit Google anmelden**, and complete consent. Google redirects to the callback above, then Odori sends a first-time user to household setup.

For production behind Traefik, confirm that the proxy sends `X-Forwarded-Proto: https`; Odori already trusts that header. If Google reports `redirect_uri_mismatch`, compare the callback URL shown in the error with the URI registered in Google Cloud character for character.
