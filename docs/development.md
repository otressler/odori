# Developer onboarding

This guide covers the features currently implemented in Odori. Planned Azure
Document Intelligence workflows and other future integrations are intentionally
not included.

## Prerequisites

Choose either local Python development or the supplied Docker Compose
development stack.

### Local Python

- Python 3.10 or newer
- Git
- A database supported by `DATABASE_URL` (SQLite is sufficient for local
  development)

### Docker Compose

- Docker Engine or Docker Desktop with Compose
- At least 2 GB of free memory for the web, worker, and PostgreSQL services

## First setup

Copy the example environment file and adjust the local values:

```bash
cp .env.example .env
```

For direct Python development, set at least:

```dotenv
DEBUG=true
SESSION_SECRET=local-development-secret
DATABASE_URL=sqlite:///./odori.db
ALLOWED_HOSTS=localhost,127.0.0.1
SECURE_SSL_REDIRECT=false
SECURE_HSTS_SECONDS=0
SECURE_HSTS_PRELOAD=false
```

Do not commit `.env` or provider credentials.

### Local Python commands

```bash
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_owner --username dev --household "Development"
python manage.py seed_demo
python manage.py runserver
```

The application is available at <http://127.0.0.1:8000/>. The seed command is
optional and creates a broad, repeatable starting point with pantry categories
and states, recipe drafts and approvals, favorites, meal history, a meal plan,
and a shopping list. To remove that data before starting over, run
`python manage.py purge_data`; user accounts are preserved. Run the worker in a
second terminal when using queued features:

```bash
python manage.py worker
```

### Docker Compose commands

The development override builds the application image, starts PostgreSQL, and
exposes the web service on port 8000:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

In another terminal, bootstrap the empty database and optionally seed it:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm odori-web \
  python manage.py bootstrap_owner --username dev --household "Development"
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm odori-web \
  python manage.py seed_demo
```

Open <http://localhost:8000/>. Stop the stack with `docker compose ... down`;
named volumes preserve PostgreSQL data and uploads.

## Google sign-in

1. In Google Cloud Console, configure the OAuth consent screen and create a
   **Web application** OAuth client.
2. Add this exact development redirect URI:
   `http://localhost:8000/accounts/google/login/callback/`
3. Put the credentials in `.env`:

   ```dotenv
   GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
   ```

4. Restart the web service and choose **Mit Google anmelden** on the login
   page.

The first Google sign-in creates the user; complete household setup or use the
normal password account created by `bootstrap_owner`. See
[Google sign-in](google-sign-in.md) for production callback configuration and
troubleshooting.

## Built AI integrations

AI features are optional. The core recipe, pantry, planning, shopping, and
cooking workflows work without them.

### Ingredient embeddings and categorization

Configure an Azure OpenAI embedding deployment:

```dotenv
INGREDIENT_EMBEDDINGS_ENABLED=true
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
```

The application falls back to local text similarity when embeddings are
disabled or unavailable. Use `python scripts/check_embedding_connectivity.py`
to verify the endpoint, deployment, and key.

### Recipe images and pantry icons

The worker generates recipe thumbnails and pantry ingredient icons through the
configured image deployment:

```dotenv
AZURE_OPENAI_IMAGE_DEPLOYMENT=gpt-image-2
AZURE_OPENAI_PANTRY_ICON_DEPLOYMENT=YOUR-ICON-DEPLOYMENT
AZURE_OPENAI_IMAGE_API_VERSION=2025-04-01-preview
```

The endpoint and API key above are shared. Keep the worker running; image
requests are queued and processed asynchronously.

### Recipe URL import

Set a Microsoft Foundry/Azure OpenAI deployment with web search enabled:

```dotenv
AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT=YOUR-IMPORT-DEPLOYMENT
AZURE_OPENAI_RECIPE_IMPORT_TIMEOUT_SECONDS=45
AZURE_OPENAI_RECIPE_IMPORT_MAX_OUTPUT_TOKENS=4000
```

URL imports create reviewable drafts. The worker must be running, and imported
content should be reviewed before approval.

### Generated recipe drafts

Enable this separately with a chat-model deployment:

```dotenv
RECIPE_GENERATION_ENABLED=true
AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT=YOUR-CHAT-DEPLOYMENT
RECIPE_GENERATION_DAILY_LIMIT=3
```

Generation is queued, limited per household, and remains optional. Keep the
feature disabled until the deployment and expected costs are understood.

## Verification and daily workflow

```bash
make lint
make test-container
```

Useful operational checks are `/health/live`, `/health/worker`, and the
household operations page at `/admin/operations`.
